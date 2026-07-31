"""CLI orchestration: argument parsing, the review pipeline, and exit codes.

Deterministic glue only — this module fetches the diff, loads the consumer's
rules/lessons/criteria files, filters to the requested scope, runs the agent
loop, writes artifacts, and (optionally) publishes a real GitHub review. The
exit-code contract is: 0 clean success (including "nothing to review"), 2
GitHub fetch failure, 3 the criteria file is missing or invalid (the only
required input file — the rules and lessons files are optional), 4 the Claude
Agent SDK itself failed, 5 the run completed without a valid verdict, 6
publishing a completed review failed.
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from claude_agent_sdk import ClaudeSDKError

from pr_review_agent.agents_context import (
    InvalidReviewCriteriaError,
    build_system_prompt,
    load_lessons_file,
    load_review_criteria,
    load_rules_file,
)
from pr_review_agent.diff_parser import (
    ChangedFile,
    build_diff_context,
    exclude_paths,
    parse_diff,
)
from pr_review_agent.github_diff import (
    GitHubApiError,
    _origin_repo,
    find_repo_root,
    get_pr_diff,
    get_pr_metadata,
    resolve_repo,
)
from pr_review_agent.github_publish import GitHubPublishError, post_review
from pr_review_agent.logging_config import configure_logging
from pr_review_agent.models import ReviewOutput
from pr_review_agent.output import (
    DEFAULT_MAX_FINDINGS,
    build_review_output,
    cap_findings,
    deduplicate_findings,
    print_console,
    write_json,
    write_markdown,
)
from pr_review_agent.review_agent import run_review

logger = logging.getLogger(__name__)

EXIT_SUCCESS = 0
EXIT_GITHUB_FETCH_ERROR = 2
EXIT_INPUT_FILE_ERROR = 3
EXIT_AGENT_ERROR = 4
EXIT_INCOMPLETE_RUN = 5
EXIT_PUBLISH_ERROR = 6

_DEFAULT_MODEL = "claude-sonnet-5"
_DEFAULT_MAX_TURNS = 5
_DEFAULT_OUT_DIR = Path("./review-output")
_FORMAT_CHOICES = ("console", "json", "markdown", "all")

# Character budget for the rendered diff handed to the model. Not a CLI flag
# (Phase 8 exposes no such input) — generous enough that only unusually large
# PRs ever hit `build_diff_context`'s truncation path.
_MAX_DIFF_CONTEXT_CHARS = 100_000


def _parse_comma_list(raw_values: list[str] | None) -> list[str]:
    """Flatten repeated and/or comma-separated `--scope-dirs`/`--exclude` values.

    Both forms are supported and composable: `--scope-dirs a --scope-dirs b`
    and `--scope-dirs "a,b"` produce the same result, since `action.yml`'s
    inputs are comma-scalar strings and a repeatable-only flag would make them
    unusable through the action.

    Args:
        raw_values: Accumulated `action="append"` values, or None if the flag
            was never passed.

    Returns:
        list[str]: Every non-empty, stripped entry across all occurrences, in
        order.
    """
    if not raw_values:
        return []
    flattened: list[str] = []
    for raw in raw_values:
        flattened.extend(part.strip() for part in raw.split(",") if part.strip())
    return flattened


def filter_in_scope_files(
    files: list[ChangedFile], scope_dirs: list[str]
) -> list[ChangedFile]:
    """Keep only changed files whose current path starts with one of `scope_dirs`.

    Args:
        files: Parsed changed files.
        scope_dirs: Directory prefixes to restrict the review to. Empty means
            every file is in scope (no filtering).

    Returns:
        list[ChangedFile]: The files in scope, in their original order.
    """
    if not scope_dirs:
        return files
    return [
        changed_file
        for changed_file in files
        if any(changed_file.path.startswith(prefix) for prefix in scope_dirs)
    ]


def _allow_repo_exploration(resolved_repo: tuple[str, str], repo_root: Path) -> bool:
    """Decide whether Read/Grep/Glob should be enabled for this run.

    The diff is fetched for `resolved_repo`, but `repo_root` is whatever local
    checkout the process happens to be running in. When the two diverge — or
    the local `origin` can't be determined at all — repo-exploration tools
    would silently browse a different codebase than the one under review, so
    exploration is disabled and a loud warning is logged instead.

    Args:
        resolved_repo: The `(owner, name)` the diff/metadata were fetched for.
        repo_root: The local checkout root that would be used as `cwd`.

    Returns:
        bool: True when the local checkout's `origin` remote matches
        `resolved_repo`.
    """
    local_origin = _origin_repo(cwd=repo_root)
    if local_origin == resolved_repo:
        return True

    local_label = (
        f"{local_origin[0]}/{local_origin[1]}" if local_origin else "undeterminable"
    )
    logger.warning(
        "Repo exploration disabled: reviewing %s/%s but the local checkout's "
        "origin is %s. Read/Grep/Glob will not be available for this run.",
        resolved_repo[0],
        resolved_repo[1],
        local_label,
    )
    return False


def _write_artifacts(
    review_output: ReviewOutput, out_dir: Path, was_capped: bool, fmt: str
) -> None:
    """Write disk artifacts for whichever formats `--format` selected.

    Called before the verdict check so a doomed run's findings are still
    persisted to `out_dir` for a consuming workflow's `if: always()` upload
    step.

    Args:
        review_output: The review to write.
        out_dir: Directory to write artifacts into.
        was_capped: Whether `review_output.comments` was truncated to fit the
            findings cap.
        fmt: The `--format` value; only `"json"`, `"markdown"`, and `"all"`
            trigger a disk write here.
    """
    if fmt in ("json", "all"):
        write_json(review_output, out_dir, was_capped)
    if fmt in ("markdown", "all"):
        write_markdown(review_output, out_dir, was_capped)


def build_parser() -> argparse.ArgumentParser:
    """Build the `pr-review-agent` argument parser.

    Returns:
        argparse.ArgumentParser: The configured parser.
    """
    parser = argparse.ArgumentParser(
        prog="pr-review-agent",
        description=(
            "Review a GitHub PR's diff against caller-supplied rules and review "
            "criteria, using a Claude agent."
        ),
    )
    parser.add_argument("--pr", type=int, required=True, help="Pull request number.")
    parser.add_argument(
        "--repo",
        default=None,
        help="OWNER/REPO to review. Defaults to GITHUB_REPOSITORY, then the "
        "local checkout's `origin` remote.",
    )
    parser.add_argument(
        "--rules-file",
        type=Path,
        default=Path("AGENTS.md"),
        help="Path to the repository rules file, injected into the prompt "
        "verbatim. Optional: if the file doesn't exist, the review proceeds "
        "without a Repository Rules section.",
    )
    parser.add_argument(
        "--criteria-file",
        type=Path,
        required=True,
        help="Path to a markdown file with at least one `##` review criterion.",
    )
    parser.add_argument(
        "--lessons-file",
        type=Path,
        default=None,
        help="Optional path to an additional lessons/pitfalls file.",
    )
    parser.add_argument(
        "--scope-dirs",
        action="append",
        default=None,
        help="Restrict the review to files under these directory prefixes. "
        "Repeatable and/or comma-separated. Omit to review every changed file.",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=None,
        help="Glob pattern(s) of paths to exclude from the review. Repeatable "
        "and/or comma-separated.",
    )
    parser.add_argument(
        "--model", default=_DEFAULT_MODEL, help="Model to run the review with."
    )
    parser.add_argument(
        "--max-turns", type=int, default=_DEFAULT_MAX_TURNS, help="Agent turn budget."
    )
    parser.add_argument(
        "--max-findings",
        type=int,
        default=DEFAULT_MAX_FINDINGS,
        help="Maximum number of findings to keep, after deduplication.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=_DEFAULT_OUT_DIR,
        help="Directory to write review artifacts into.",
    )
    parser.add_argument(
        "--format",
        choices=_FORMAT_CHOICES,
        default="console",
        help="Which output form(s) to produce.",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Post the review to GitHub as real inline PR comments.",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Log assistant activity at DEBUG level."
    )
    return parser


async def main_async(args: argparse.Namespace) -> int:
    """Run the full review pipeline for one invocation.

    Args:
        args: Parsed CLI arguments from `build_parser`.

    Returns:
        int: The process exit code (see the module docstring for the contract).
    """
    scope_dirs = _parse_comma_list(args.scope_dirs)
    exclude_globs = _parse_comma_list(args.exclude)

    try:
        owner, name = resolve_repo(args.repo)
    except GitHubApiError as exc:
        logger.error("%s", exc)
        return EXIT_GITHUB_FETCH_ERROR
    repo = f"{owner}/{name}"

    try:
        pr_metadata = get_pr_metadata(args.pr, repo=repo)
    except GitHubApiError as exc:
        logger.error("%s", exc)
        return EXIT_GITHUB_FETCH_ERROR

    if pr_metadata.state != "open":
        status = "merged" if pr_metadata.merged else pr_metadata.state
        print(f"PR #{args.pr} is {status}; skipping review.")
        return EXIT_SUCCESS

    try:
        diff_text = get_pr_diff(args.pr, repo=repo)
    except GitHubApiError as exc:
        logger.error("%s", exc)
        return EXIT_GITHUB_FETCH_ERROR

    if not pr_metadata.files:
        print(f"PR #{args.pr} has no changed files; nothing to review.")
        return EXIT_SUCCESS

    try:
        rules_content = load_rules_file(args.rules_file)
        lessons_content = load_lessons_file(args.lessons_file)
        criteria = load_review_criteria(args.criteria_file)
    except (FileNotFoundError, InvalidReviewCriteriaError) as exc:
        logger.error("%s", exc)
        return EXIT_INPUT_FILE_ERROR

    changed_files = exclude_paths(parse_diff(diff_text), exclude_globs)
    in_scope = filter_in_scope_files(changed_files, scope_dirs)
    if scope_dirs and not in_scope:
        print(
            f"All {len(changed_files)} changed file(s) are out of scope "
            f"({', '.join(scope_dirs)}); nothing to review."
        )
        return EXIT_SUCCESS

    diff_context, was_truncated = build_diff_context(in_scope, _MAX_DIFF_CONTEXT_CHARS)
    system_prompt = build_system_prompt(rules_content, lessons_content, criteria)
    repo_root = find_repo_root()
    allow_repo_exploration = _allow_repo_exploration((owner, name), repo_root)

    try:
        result = await run_review(
            pr_metadata=pr_metadata,
            diff_context=diff_context,
            was_truncated=was_truncated,
            system_prompt=system_prompt,
            criteria=criteria,
            repo_root=repo_root,
            model=args.model,
            max_turns=args.max_turns,
            allow_repo_exploration=allow_repo_exploration,
        )
    except ClaudeSDKError as exc:
        logger.error("%s", exc)
        return EXIT_AGENT_ERROR

    kept_findings, was_capped = cap_findings(
        deduplicate_findings(result.findings), max_findings=args.max_findings
    )
    review_output = build_review_output(args.pr, kept_findings, result.verdict)
    _write_artifacts(review_output, args.out_dir, was_capped, args.format)

    if not result.sdk_success or result.verdict is None:
        logger.error(
            "Review run did not complete cleanly (sdk_success=%s, verdict=%s); "
            "any findings collected before the failure were still written to %s.",
            result.sdk_success,
            "present" if result.verdict is not None else "missing",
            args.out_dir,
        )
        return EXIT_INCOMPLETE_RUN

    if args.publish:
        try:
            post_review(args.pr, review_output, repo=repo)
        except GitHubPublishError as exc:
            logger.error("%s Local artifacts were saved to %s.", exc, args.out_dir)
            return EXIT_PUBLISH_ERROR
        print(
            f"Posted {len(review_output.comments)} inline comment(s) to PR #{args.pr}."
        )
        return EXIT_SUCCESS

    if args.format in ("console", "all"):
        print_console(review_output, was_capped)
    return EXIT_SUCCESS


def _ensure_utf8_streams() -> None:
    """Reconfigure stdout/stderr to UTF-8, replacing what can't be encoded.

    Review output carries unicode (criterion icons, PR titles, agent prose)
    that a non-UTF-8 console — e.g. Windows' legacy per-locale codepage —
    cannot encode, crashing the process mid-`print`. GitHub Actions runners
    are UTF-8 already, so this only changes behavior on affected local setups.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    """CLI entry point: parse arguments, configure logging, run, and exit."""
    _ensure_utf8_streams()
    args = build_parser().parse_args()
    configure_logging(args.verbose)
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
