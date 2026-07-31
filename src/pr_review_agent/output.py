"""Turning collected findings and a verdict into review output.

Everything here is deterministic: the model's job ends at `submit_finding`
and `submit_review_verdict` (`review_agent.py`); this module owns rendering
those into a console preview, a JSON artifact, and a markdown artifact, plus
the exact GitHub reviews-API payload shape that `github_publish.py` posts.
"""

import json
import logging
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pr_review_agent.logging_config import redact
from pr_review_agent.models import (
    CriterionResult,
    Finding,
    ReviewEvent,
    ReviewOutput,
    ReviewVerdict,
    Severity,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_FINDINGS = 30

_DRY_RUN_BANNER = "=== DRY RUN — NOT PUBLISHED TO GITHUB ==="
_INCOMPLETE_BANNER = "=== INCOMPLETE — NO VERDICT PRODUCED ==="

_CRITERION_ICONS = {
    CriterionResult.PASS: "✅",
    CriterionResult.FAIL: "❌",
    CriterionResult.NOT_APPLICABLE: "➖",
}


def deduplicate_findings(findings: list[Finding]) -> list[Finding]:
    """Drop findings that exactly repeat an earlier `(path, line, comment)`.

    Args:
        findings: Findings as collected from the agent run, in submission
            order.

    Returns:
        list[Finding]: The findings with exact repeats removed, keeping the
        first occurrence of each `(path, line, comment)` key.
    """
    seen: set[tuple[str, int, str]] = set()
    deduped: list[Finding] = []
    for finding in findings:
        key = (finding.path, finding.line, finding.comment)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(finding)

    if len(deduped) < len(findings):
        logger.info(
            "Deduplicated %d finding(s) down to %d", len(findings), len(deduped)
        )
    return deduped


def cap_findings(
    findings: list[Finding], max_findings: int = DEFAULT_MAX_FINDINGS
) -> tuple[list[Finding], bool]:
    """Truncate `findings` to at most `max_findings`.

    Args:
        findings: Findings to cap, in submission order.
        max_findings: The maximum number of findings to keep.

    Returns:
        tuple[list[Finding], bool]: The kept findings (first `max_findings`
        of them), and whether any were dropped.
    """
    if len(findings) <= max_findings:
        return findings, False

    logger.warning(
        "Capping findings: %d found, keeping the first %d", len(findings), max_findings
    )
    return findings[:max_findings], True


def _findings_breakdown(findings: list[Finding]) -> str:
    """Render the one-line findings-count-by-severity summary.

    Args:
        findings: The findings to summarize.

    Returns:
        str: e.g. `"Findings: 2 blocker, 1 nit (3 total)"`, or a no-findings
        message when `findings` is empty.
    """
    if not findings:
        return "No findings."

    counts = Counter(finding.severity for finding in findings)
    parts = [
        f"{counts[severity]} {severity}" for severity in Severity if counts[severity]
    ]
    return f"Findings: {', '.join(parts)} ({len(findings)} total)"


def build_summary(findings: list[Finding], verdict: ReviewVerdict | None) -> str:
    """Render the deterministic review summary body.

    Renders the per-criterion scores first, then the findings-count
    breakdown. When `verdict` is None, an incomplete-run banner replaces the
    criteria breakdown — this happens on the exit-5 path in `cli.py`, where
    output is still written so a doomed run's findings aren't lost.

    Args:
        findings: The findings to summarize (already deduplicated/capped).
        verdict: The collected review verdict, or None if the run never
            produced one.

    Returns:
        str: The rendered summary, used as both the console preview and the
        GitHub review's `body`.
    """
    lines: list[str] = []
    if verdict is None:
        lines.append(_INCOMPLETE_BANNER)
        lines.append("")
    else:
        lines.append("## Review Criteria")
        lines.append("")
        for score in verdict.criteria:
            icon = _CRITERION_ICONS[score.score]
            lines.append(f"{icon} {score.name}: {score.score} — {score.rationale}")
        lines.append("")

    lines.append(_findings_breakdown(findings))
    return "\n".join(lines)


def _redact_findings(findings: list[Finding]) -> list[Finding]:
    """Scrub secrets out of the model-authored fields of each finding.

    Args:
        findings: The findings to scrub.

    Returns:
        list[Finding]: Copies with `comment` and `rule_reference` redacted.
        `path`, `line`, `side` and `severity` are structural and left alone.
    """
    return [
        replace(
            finding,
            comment=redact(finding.comment),
            rule_reference=(
                redact(finding.rule_reference)
                if finding.rule_reference is not None
                else None
            ),
        )
        for finding in findings
    ]


def _redact_verdict(verdict: ReviewVerdict | None) -> ReviewVerdict | None:
    """Scrub secrets out of the model-authored rationale of each criterion score.

    Args:
        verdict: The collected review verdict, or None.

    Returns:
        ReviewVerdict | None: A copy with every `rationale` redacted, or None.
        `name` is validated against the loaded criteria and `score` is an enum,
        so neither can carry model-authored text.
    """
    if verdict is None:
        return None
    return replace(
        verdict,
        criteria=[
            replace(score, rationale=redact(score.rationale))
            for score in verdict.criteria
        ],
    )


def build_review_output(
    pr_number: int, findings: list[Finding], verdict: ReviewVerdict | None
) -> ReviewOutput:
    """Assemble the complete `ReviewOutput` from a run's collected results.

    `event` comes directly from `verdict.overall_verdict` — there is no
    severity-rollup heuristic, since the model's own structured verdict is
    authoritative. When `verdict` is None, `event` falls back to
    `ReviewEvent.COMMENT`; this output must never be published in that case
    (enforced by `cli.py`, not here).

    This is also the redaction boundary. Every model-authored string is
    scrubbed here, once, rather than at each renderer: the diff and the PR
    metadata are attacker-controlled, so a prompt-injected agent could be
    steered into reading a secret and emitting it as a finding. Downstream
    sinks — the console preview, both artifacts, and the comment posted to a
    public PR — all read from the returned `ReviewOutput`, so redacting at
    construction covers them by design and cannot be forgotten by a future
    renderer.

    Args:
        pr_number: The pull request number the review is for.
        findings: The findings to include (already deduplicated/capped).
        verdict: The collected review verdict, or None if the run never
            produced one.

    Returns:
        ReviewOutput: The complete review, ready to print or write to disk,
        with all model-authored text redacted.
    """
    safe_findings = _redact_findings(findings)
    safe_verdict = _redact_verdict(verdict)
    event = verdict.overall_verdict if verdict is not None else ReviewEvent.COMMENT
    return ReviewOutput(
        pr_number=pr_number,
        event=event,
        summary_body=build_summary(safe_findings, safe_verdict),
        comments=safe_findings,
        verdict=safe_verdict,
    )


def _render_finding_body(finding: Finding) -> str:
    """Render one finding's severity, comment, and rule citation as GitHub markdown.

    Shared by `write_json` and `github_publish.post_review` (via
    `build_review_payload`), so the artifact and the real posted comment
    never drift apart.

    Args:
        finding: The finding to render.

    Returns:
        str: The markdown body for this finding's inline comment.
    """
    body = f"**[{finding.severity}]** {finding.comment}"
    if finding.rule_reference:
        body += f"\n\n_Rule: {finding.rule_reference}_"
    return body


def _comment_payload(finding: Finding) -> dict[str, Any]:
    """Render one finding as a GitHub reviews-API comment entry.

    Args:
        finding: The finding to render.

    Returns:
        dict[str, Any]: `{"path", "line", "side", "body"}`, matching the
        reviews-API comment schema exactly.
    """
    return {
        "path": finding.path,
        "line": finding.line,
        "side": finding.side,
        "body": _render_finding_body(finding),
    }


def build_review_payload(review_output: ReviewOutput) -> dict[str, Any]:
    """Render the exact GitHub reviews-API `pulls.create_review` payload shape.

    Args:
        review_output: The review to render.

    Returns:
        dict[str, Any]: `{"event", "body", "comments": [...]}` — no other
        keys. `write_json` adds a local-only `criteria` key on top of this;
        `github_publish.post_review` posts this shape unmodified.
    """
    return {
        "event": review_output.event,
        "body": review_output.summary_body,
        "comments": [_comment_payload(finding) for finding in review_output.comments],
    }


def _criteria_payload(verdict: ReviewVerdict | None) -> list[dict[str, str]]:
    """Render the local-only `criteria` array for the JSON artifact.

    Args:
        verdict: The collected review verdict, or None if the run never
            produced one.

    Returns:
        list[dict[str, str]]: One `{"name", "score", "rationale"}` entry per
        criterion, or an empty list if there is no verdict.
    """
    if verdict is None:
        return []
    return [
        {"name": score.name, "score": score.score, "rationale": score.rationale}
        for score in verdict.criteria
    ]


def _group_by_file(findings: list[Finding]) -> list[tuple[str, list[Finding]]]:
    """Group findings by path, preserving first-occurrence file order.

    Args:
        findings: The findings to group.

    Returns:
        list[tuple[str, list[Finding]]]: `(path, findings)` pairs, in the
        order each path first appeared.
    """
    grouped: dict[str, list[Finding]] = {}
    for finding in findings:
        grouped.setdefault(finding.path, []).append(finding)
    return list(grouped.items())


def print_console(review_output: ReviewOutput, was_capped: bool) -> None:
    """Print the dry-run console preview of a review to stdout.

    Args:
        review_output: The review to render.
        was_capped: Whether `comments` was truncated to fit the findings cap.
    """
    print(_DRY_RUN_BANNER)
    print()
    print(review_output.summary_body)
    if was_capped:
        print()
        print("NOTE: findings were capped; some findings are not shown above.")

    if not review_output.comments:
        return

    print()
    print("Findings by file:")
    for path, file_findings in _group_by_file(review_output.comments):
        print()
        print(f"{path}:")
        for finding in file_findings:
            reference = f" [{finding.rule_reference}]" if finding.rule_reference else ""
            print(
                f"  L{finding.line} ({finding.side}, {finding.severity}){reference}: "
                f"{finding.comment}"
            )


def _timestamp() -> str:
    """Render the current UTC time for use in an output filename.

    Returns:
        str: A sortable, filesystem-safe timestamp, e.g. `20260731T120000Z`.
    """
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def write_json(review_output: ReviewOutput, out_dir: Path, was_capped: bool) -> Path:
    """Write the review as a JSON artifact under `out_dir`.

    The payload includes the exact future POST-payload shape
    (`event`/`body`/`comments`) plus a local-only `criteria` array for
    inspection — `github_publish.post_review` strips `criteria` before
    posting, since the reviews API has no schema slot for it.

    Args:
        review_output: The review to write.
        out_dir: Directory to write into; created if it doesn't exist.
        was_capped: Whether `comments` was truncated to fit the findings cap.

    Returns:
        Path: The path the artifact was written to.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"pr-{review_output.pr_number}-{_timestamp()}.json"

    payload = build_review_payload(review_output)
    payload["criteria"] = _criteria_payload(review_output.verdict)
    payload["was_capped"] = was_capped

    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("Wrote JSON review artifact: %s", path)
    return path


def write_markdown(
    review_output: ReviewOutput, out_dir: Path, was_capped: bool
) -> Path:
    """Write the review as a markdown artifact under `out_dir`.

    Args:
        review_output: The review to write.
        out_dir: Directory to write into; created if it doesn't exist.
        was_capped: Whether `comments` was truncated to fit the findings cap.

    Returns:
        Path: The path the artifact was written to.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"pr-{review_output.pr_number}-{_timestamp()}.md"

    lines = [
        f"# Review for PR #{review_output.pr_number}",
        "",
        review_output.summary_body,
    ]
    if was_capped:
        lines += ["", "**Findings were capped; some findings are not listed below.**"]

    if review_output.comments:
        lines += ["", "## Findings", ""]
        for file_path, file_findings in _group_by_file(review_output.comments):
            lines.append(f"### {file_path}")
            for finding in file_findings:
                lines.append(
                    f"- **L{finding.line}** ({finding.side}, {finding.severity}): "
                    f"{finding.comment}"
                )
                if finding.rule_reference:
                    lines.append(f"  - _Rule: {finding.rule_reference}_")
            lines.append("")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    logger.info("Wrote markdown review artifact: %s", path)
    return path
