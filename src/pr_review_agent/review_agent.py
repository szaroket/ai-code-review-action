"""The agent loop: builds tools/options, runs the review, collects the result.

Deterministic code owns everything except the review judgment itself: the
system prompt (`agents_context.py`), the diff context (`diff_parser.py`), and
the tool contracts below are all plain Python. The model's only job is to
call `submit_finding` for each issue it spots and `submit_review_verdict`
exactly once at the end.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    create_sdk_mcp_server,
    query,
    tool,
)

from pr_review_agent.github_diff import PullRequestMetadata
from pr_review_agent.models import (
    Criterion,
    CriterionResult,
    CriterionScore,
    DiffSide,
    Finding,
    ReviewEvent,
    ReviewVerdict,
    Severity,
)

logger = logging.getLogger(__name__)

_EXPLORATION_TOOLS = ["Read", "Grep", "Glob"]
_MCP_TOOLS = ["mcp__reviewer__submit_finding", "mcp__reviewer__submit_review_verdict"]
_DISALLOWED_TOOLS = ["Bash", "Write", "Edit", "NotebookEdit", "WebSearch", "Task"]

_SUBMIT_FINDING_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "The file path the finding applies to, exactly as it appears in the diff.",
        },
        "line": {
            "type": "integer",
            "description": "The line number in `side`'s version of the file.",
        },
        "side": {
            "type": "string",
            "enum": [member.value for member in DiffSide],
            "description": "Which side of the diff `line` refers to.",
        },
        "severity": {
            "type": "string",
            "enum": [member.value for member in Severity],
        },
        "comment": {"type": "string", "description": "The review comment body."},
        "rule_reference": {
            "type": ["string", "null"],
            "description": (
                'A citation into "Repository Rules" or "Additional Lessons / '
                'Pitfalls" if this finding relates to one; omit or pass null '
                "otherwise."
            ),
        },
    },
    "required": ["path", "line", "side", "severity", "comment"],
}

_SUBMIT_VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "criteria": {
            "type": "array",
            "description": "One score per loaded review criterion, matched by exact name.",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "score": {
                        "type": "string",
                        "enum": [member.value for member in CriterionResult],
                    },
                    "rationale": {"type": "string"},
                },
                "required": ["name", "score", "rationale"],
            },
        },
        "overall_verdict": {
            "type": "string",
            "enum": [member.value for member in ReviewEvent],
        },
    },
    "required": ["criteria", "overall_verdict"],
}


class _CriterionScoreArgs(TypedDict):
    """Shape of one entry in `submit_review_verdict`'s `criteria` argument."""

    name: str
    score: str
    rationale: str


@dataclass
class _Collector:
    """Mutable state the two tool handlers close over during one review run."""

    findings: list[Finding] = field(default_factory=list)
    verdict: ReviewVerdict | None = None


@dataclass
class ReviewRunResult:
    """Everything the agent loop collected from one `run_review` call.

    `sdk_success` and `verdict` are reported separately, not collapsed into
    one flag, because Phase 8's exit-code contract treats "SDK reported
    success" and "a valid verdict was collected" as independently checkable
    facts — a run can end in SDK success with `verdict` still None (the model
    never called `submit_review_verdict`, or every attempt failed
    validation).
    """

    findings: list[Finding]
    verdict: ReviewVerdict | None
    sdk_success: bool


def _make_submit_finding_tool(collector: _Collector) -> Any:
    """Build the `submit_finding` tool bound to `collector`.

    Args:
        collector: Mutable state to append validated findings to.

    Returns:
        Any: The `SdkMcpTool` returned by the `@tool` decorator.
    """

    @tool(
        "submit_finding",
        "Submit one review finding anchored to a specific line of the diff.",
        _SUBMIT_FINDING_SCHEMA,
    )
    async def submit_finding(args: dict[str, Any]) -> dict[str, Any]:
        """Validate and record one finding, or return a retryable error.

        Args:
            args: The tool call arguments, matching `_SUBMIT_FINDING_SCHEMA`.

        Returns:
            dict[str, Any]: An MCP tool-result payload; `is_error` is set when
            `side` or `severity` fails validation.
        """
        try:
            side = DiffSide(args["side"])
            severity = Severity(args["severity"])
            line = int(args["line"])
        except (KeyError, ValueError, TypeError) as exc:
            return {
                "content": [{"type": "text", "text": f"Invalid finding: {exc}"}],
                "is_error": True,
            }

        collector.findings.append(
            Finding(
                path=args["path"],
                line=line,
                side=side,
                severity=severity,
                comment=args["comment"],
                rule_reference=args.get("rule_reference"),
            )
        )
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Recorded finding #{len(collector.findings)}.",
                }
            ]
        }

    return submit_finding


def _make_submit_verdict_tool(collector: _Collector, criteria: list[Criterion]) -> Any:
    """Build the `submit_review_verdict` tool bound to `collector` and `criteria`.

    Args:
        collector: Mutable state to store the validated verdict in.
        criteria: The loaded review criteria; the submitted criteria names
            must exactly match this set.

    Returns:
        Any: The `SdkMcpTool` returned by the `@tool` decorator.
    """
    expected_names = {criterion.name for criterion in criteria}

    @tool(
        "submit_review_verdict",
        "Submit the final review verdict: one score per review criterion, "
        "plus one overall verdict for the review as a whole. Call this "
        "exactly once, after all submit_finding calls.",
        _SUBMIT_VERDICT_SCHEMA,
    )
    async def submit_review_verdict(args: dict[str, Any]) -> dict[str, Any]:
        """Validate the submitted verdict and store it, or return a retryable error.

        Args:
            args: The tool call arguments, matching `_SUBMIT_VERDICT_SCHEMA`.

        Returns:
            dict[str, Any]: An MCP tool-result payload; `is_error` is set when
            the criteria names don't exactly match `criteria`, or any score /
            `overall_verdict` value is invalid.
        """
        submitted: list[_CriterionScoreArgs] = args.get("criteria", [])
        submitted_names = {entry.get("name") for entry in submitted}
        if submitted_names != expected_names:
            missing = expected_names - submitted_names
            extra = submitted_names - expected_names
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "criteria names must exactly match the loaded review "
                            f"criteria. missing={sorted(missing)} extra={sorted(extra)}"
                        ),
                    }
                ],
                "is_error": True,
            }

        by_name = {entry["name"]: entry for entry in submitted}
        try:
            scores = [
                CriterionScore(
                    name=criterion.name,
                    score=CriterionResult(by_name[criterion.name]["score"]),
                    rationale=by_name[criterion.name]["rationale"],
                )
                for criterion in criteria
            ]
            overall_verdict = ReviewEvent(args["overall_verdict"])
        except (KeyError, ValueError, TypeError) as exc:
            return {
                "content": [{"type": "text", "text": f"Invalid verdict: {exc}"}],
                "is_error": True,
            }

        collector.verdict = ReviewVerdict(
            criteria=scores, overall_verdict=overall_verdict
        )
        return {"content": [{"type": "text", "text": "Verdict recorded."}]}

    return submit_review_verdict


def _build_options(
    system_prompt: str,
    repo_root: Path,
    model: str,
    max_turns: int,
    allow_repo_exploration: bool,
    collector: _Collector,
    criteria: list[Criterion],
) -> ClaudeAgentOptions:
    """Assemble the `ClaudeAgentOptions` for one review run.

    Args:
        system_prompt: The composed system prompt (role, criteria, rules,
            lessons, tool contracts).
        repo_root: The consumer's checkout root, used as `cwd` so Read/Grep/
            Glob (when allowed) browse the repository under review rather
            than this action's own source.
        model: The model to run the review with.
        max_turns: The agent turn budget.
        allow_repo_exploration: When False, Read/Grep/Glob are dropped from
            `allowed_tools` (the repo-mismatch guard degrades to a diff-only
            review rather than browsing the wrong codebase).
        collector: Mutable state the two tools append/store into.
        criteria: The loaded review criteria, for the verdict tool's
            exact-name validation.

    Returns:
        ClaudeAgentOptions: Options with `permission_mode="dontAsk"` and an
        explicit `disallowed_tools` list; never `bypassPermissions`, which
        would ignore `allowed_tools` entirely.
    """
    server = create_sdk_mcp_server(
        name="reviewer",
        version="0.1.0",
        tools=[
            _make_submit_finding_tool(collector),
            _make_submit_verdict_tool(collector, criteria),
        ],
    )
    allowed_tools = list(_MCP_TOOLS)
    if allow_repo_exploration:
        allowed_tools = [*_EXPLORATION_TOOLS, *allowed_tools]
    else:
        logger.warning(
            "Repo exploration disabled for this run (Read/Grep/Glob withheld); "
            "reviewing the diff only."
        )

    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        cwd=str(repo_root),
        allowed_tools=allowed_tools,
        disallowed_tools=_DISALLOWED_TOOLS,
        permission_mode="dontAsk",
        mcp_servers={"reviewer": server},
        model=model,
        max_turns=max_turns,
    )


def _build_user_prompt(
    pr_metadata: PullRequestMetadata, diff_context: str, was_truncated: bool
) -> str:
    """Render the initial user message: PR identity plus the diff to review.

    Args:
        pr_metadata: The pull request's identifying fields.
        diff_context: The rendered diff text from `diff_parser.build_diff_context`.
        was_truncated: Whether `diff_context` had files or paths dropped to
            fit the character budget.

    Returns:
        str: The complete user prompt for `query()`.
    """
    lines = [
        f"Pull request #{pr_metadata.number}: {pr_metadata.title}",
        f"URL: {pr_metadata.url}",
        f"Branch: {pr_metadata.head_ref_name} -> {pr_metadata.base_ref_name}",
    ]
    if was_truncated:
        lines.append(
            "NOTE: the diff below was truncated to fit the context budget; "
            "some files or the full file list may be incomplete."
        )
    lines.append("")
    lines.append(diff_context)
    lines.append(
        "Review the diff above. Call `submit_finding` for each issue, then "
        "call `submit_review_verdict` exactly once."
    )
    return "\n".join(lines)


async def run_review(
    *,
    pr_metadata: PullRequestMetadata,
    diff_context: str,
    was_truncated: bool,
    system_prompt: str,
    criteria: list[Criterion],
    repo_root: Path,
    model: str,
    max_turns: int,
    allow_repo_exploration: bool = True,
) -> ReviewRunResult:
    """Run the agentic review loop once and collect its findings and verdict.

    Args:
        pr_metadata: The pull request's identifying fields.
        diff_context: The rendered diff text to review.
        was_truncated: Whether `diff_context` was truncated to fit budget.
        system_prompt: The composed system prompt.
        criteria: The loaded review criteria.
        repo_root: The consumer's checkout root, used as `cwd`.
        model: The model to run the review with.
        max_turns: The agent turn budget.
        allow_repo_exploration: Whether Read/Grep/Glob are allowed this run.

    Returns:
        ReviewRunResult: The collected findings, verdict (if any), and
        whether the SDK itself reported a successful run.
    """
    collector = _Collector()
    options = _build_options(
        system_prompt=system_prompt,
        repo_root=repo_root,
        model=model,
        max_turns=max_turns,
        allow_repo_exploration=allow_repo_exploration,
        collector=collector,
        criteria=criteria,
    )
    prompt = _build_user_prompt(pr_metadata, diff_context, was_truncated)

    sdk_success = False
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    logger.debug("Assistant: %s", block.text)
        elif isinstance(message, ResultMessage):
            sdk_success = message.subtype == "success" and not message.is_error
            logger.info(
                "Review run finished: subtype=%s is_error=%s num_turns=%d",
                message.subtype,
                message.is_error,
                message.num_turns,
            )

    return ReviewRunResult(
        findings=collector.findings,
        verdict=collector.verdict,
        sdk_success=sdk_success,
    )
