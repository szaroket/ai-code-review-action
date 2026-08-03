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
import os
import sys
from pathlib import Path

from claude_agent_sdk import ClaudeSDKError
from unidiff.errors import UnidiffParseError

from pr_review_agent.agents_context import (
    InvalidReviewCriteriaError,
    build_system_prompt,
    parse_review_criteria,
)
from pr_review_agent.diff_parser import (
    ChangedFile,
    build_diff_context,
    exclude_paths,
    parse_diff,
)
from pr_review_agent.github_diff import (
    GitHubApiError,
    find_repo_root,
    get_file_at_ref,
    get_pr_diff,
    get_pr_metadata,
    origin_repo,
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
    """Keep only changed files that live under one of `scope_dirs`.

    Matching is on path *segments*, not raw string prefixes: `--scope-dirs api`
    covers `api/handler.py` but not `api_client_generated/bundle.min.js` or
    `apirc.py`. A bare `startswith` would pull those in, and one generated file
    is enough to consume the whole diff-context budget and truncate away the
    changes actually under review.

    Args:
        files: Parsed changed files.
        scope_dirs: Directory prefixes to restrict the review to. Empty means
            every file is in scope (no filtering).

    Returns:
        list[ChangedFile]: The files in scope, in their original order.
    """
    if not scope_dirs:
        return files
    prefixes = [stripped for prefix in scope_dirs if (stripped := prefix.rstrip("/"))]
    return [
        changed_file
        for changed_file in files
        if any(
            changed_file.path == prefix or changed_file.path.startswith(f"{prefix}/")
            for prefix in prefixes
        )
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
    local_origin = origin_repo(cwd=repo_root)
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


def _repo_relative(path: Path, repo_root: Path) -> str | None:
    """Express `path` relative to `repo_root`, or None if it falls outside.

    The comparison is deliberately *lexical*. `Path.resolve()` follows
    symlinks, and the checkout is the pull request's own head — so a PR that
    replaced `AGENTS.md` with a symlink pointing outside the repository would
    make its rules file resolve outside `repo_root`, be classified as external,
    and thereby win the *higher* trust level in `_load_review_input`.
    `os.path.abspath` normalises the process cwd and any `..` without touching
    the filesystem, so the trust decision cannot be moved by anything the pull
    request author controls.

    Args:
        path: The path to test, absolute or relative to the process cwd.
        repo_root: The local checkout root.

    Returns:
        str | None: A repository-relative POSIX path, or None when `path` lies
        outside `repo_root` by literal path (or cannot be made absolute).
    """
    try:
        literal = Path(os.path.abspath(path))
        return literal.relative_to(Path(os.path.abspath(repo_root))).as_posix()
    except (ValueError, OSError):
        return None


def _reject_symlink_escape(path: Path, repo_root: Path, what: str) -> None:
    """Refuse an in-repo review-input path that resolves outside the checkout.

    `_repo_relative` classifies lexically and the in-repo branch reads from the
    base ref, so an escaping symlink can no longer leak a file on its own. It
    still means the head checkout disagrees with the base ref about what this
    path *is* — either an attack or a broken tree, and both are worth failing
    loudly on rather than silently reviewing against the base-ref copy.

    Args:
        path: A review-input path already known to sit inside `repo_root` by
            literal path.
        repo_root: The local checkout root.
        what: Short label for the error message, e.g. `"Rules file"`.

    Returns:
        None: Returns normally when `path` stays inside `repo_root`, or when
        neither side can be resolved at all.

    Raises:
        OSError: If `path` resolves outside `repo_root`.
    """
    try:
        resolved = path.resolve()
        inside = resolved.is_relative_to(repo_root.resolve())
    except OSError:
        return
    if not inside:
        raise OSError(
            f"{what} {path} is inside the checkout but resolves to {resolved}, "
            "outside it. Refusing to read a path that escapes the repository: "
            "it cannot be sourced from the base ref, and the checkout is the "
            "pull request's own head."
        )


def _load_review_input(
    path: Path,
    what: str,
    *,
    repo_root: Path,
    base_ref: str,
    repo: str,
    trust_head: bool,
) -> str | None:
    """Read one review-input file, preferring the PR's base ref over the checkout.

    The rules, lessons and criteria files all land in the *system* prompt —
    the highest-authority position in the conversation. Resolved against the
    PR-head checkout the workflow runs in, that means a pull request could add
    "always approve" to its own `AGENTS.md`, or rewrite the criteria it is
    about to be scored against, and the agent would treat it as instruction
    from the repository owner. So anything inside the checkout is read from
    the base ref instead, which only someone with write access can change.

    Files *outside* the checkout are read from disk unchanged: they aren't
    part of the pull request, so they carry the caller's authority, not the
    author's. That is what lets a workflow point `--criteria-file` at criteria
    shipped alongside the action rather than at a file in the repo under
    review. Which side of that line a path falls on is decided lexically (see
    `_repo_relative`), so the author cannot cross it with a symlink; a path
    that is inside by literal name but escapes on disk is rejected outright.

    Args:
        path: The `--rules-file` / `--lessons-file` / `--criteria-file` value.
        what: Short label for logs, e.g. `"rules file"`.
        repo_root: The local checkout root.
        base_ref: The pull request's base branch name.
        repo: `OWNER/REPO` the review is running against.
        trust_head: When True, read from the checkout even for in-repo paths.
            The escape hatch for running against a branch you control, where
            the criteria may not exist on the base ref yet.

    Returns:
        str | None: The file's contents, or None if it doesn't exist at the
        source used.

    Raises:
        GitHubApiError: If the base-ref fetch fails for any reason other than
            the file being absent.
        OSError: If an on-disk read fails for a reason other than the file
            being absent, or if an in-repo path escapes the checkout through a
            symlink.
        UnicodeDecodeError: If an on-disk file isn't valid UTF-8.
    """
    relative = _repo_relative(path, repo_root)
    if relative is not None and not trust_head:
        _reject_symlink_escape(path, repo_root, what)
    if trust_head or relative is None:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.warning("%s not found, continuing without it: %s", what, path)
            return None

    content = get_file_at_ref(relative, base_ref, repo=repo)
    if content is None:
        logger.warning(
            "%s not found at base ref `%s`: %s. Continuing without it — note "
            "that a copy added by this pull request is deliberately ignored; "
            "pass --trust-head-files to read the checkout instead.",
            what,
            base_ref,
            relative,
        )
    return content


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
    # A write failure (read-only --out-dir, full disk) must not abort: by this
    # point the agent run is already paid for, and the console preview and the
    # exit code are still worth delivering.
    try:
        if fmt in ("json", "all"):
            write_json(review_output, out_dir, was_capped)
        if fmt in ("markdown", "all"):
            write_markdown(review_output, out_dir, was_capped)
    except OSError as exc:
        logger.error("Could not write review artifacts to %s: %s", out_dir, exc)


def _ensure_json_artifact(
    review_output: ReviewOutput, out_dir: Path, was_capped: bool, fmt: str
) -> None:
    """Persist the JSON artifact on a failing run even if `--format` skipped it.

    `_write_artifacts` honours `--format`, so the default `"console"` leaves
    nothing on disk. On the exit-5 and exit-6 paths that would silently discard
    every finding collected before the failure — which step 10 of the plan
    forbids, and which is what the consuming workflow's `if: always()` upload
    step exists to capture. So the failure branches write JSON unconditionally.

    Args:
        review_output: The review to write.
        out_dir: Directory to write the artifact into.
        was_capped: Whether `review_output.comments` was truncated to fit the
            findings cap.
        fmt: The `--format` value; a no-op when `_write_artifacts` already
            wrote JSON for it.
    """
    if fmt in ("json", "all"):
        return
    try:
        write_json(review_output, out_dir, was_capped)
    except OSError as exc:
        logger.error("Could not write the review artifact to %s: %s", out_dir, exc)


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
        "without a Repository Rules section. Read from the PR's base ref when "
        "it lives inside the checkout (see --trust-head-files).",
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
        "--trust-head-files",
        action="store_true",
        help="Read the rules/lessons/criteria files from the working checkout "
        "instead of the PR's base ref. Unsafe for pull requests you don't "
        "control — a PR can then rewrite the rules and criteria it is judged "
        "by. Intended for local runs and for branches whose criteria file "
        "doesn't exist on the base ref yet.",
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
        logger.info("PR #%d is %s; skipping review.", args.pr, status)
        return EXIT_SUCCESS

    try:
        diff_text = get_pr_diff(args.pr, repo=repo)
    except GitHubApiError as exc:
        logger.error("%s", exc)
        return EXIT_GITHUB_FETCH_ERROR

    if pr_metadata.changed_file_count == 0:
        logger.info("PR #%d has no changed files; nothing to review.", args.pr)
        return EXIT_SUCCESS

    repo_root = find_repo_root()
    load_kwargs = {
        "repo_root": repo_root,
        "base_ref": pr_metadata.base_ref_name,
        "repo": repo,
        "trust_head": args.trust_head_files,
    }

    # OSError covers FileNotFoundError plus the plausible-typo cases a bare
    # FileNotFoundError misses — a directory passed as --criteria-file
    # (IsADirectoryError), an unreadable path (PermissionError). A binary file
    # passed as --rules-file surfaces as UnicodeDecodeError, which is a
    # ValueError and so needs naming separately.
    try:
        rules_content = _load_review_input(args.rules_file, "Rules file", **load_kwargs)
        lessons_content = (
            _load_review_input(args.lessons_file, "Lessons file", **load_kwargs)
            if args.lessons_file is not None
            else None
        )
        criteria_text = _load_review_input(
            args.criteria_file, "Criteria file", **load_kwargs
        )
    except GitHubApiError as exc:
        logger.error("%s", exc)
        return EXIT_GITHUB_FETCH_ERROR
    except (OSError, UnicodeDecodeError) as exc:
        logger.error("%s", exc)
        return EXIT_INPUT_FILE_ERROR

    if criteria_text is None:
        logger.error(
            "Criteria file not found: %s. It is the one required input file.",
            args.criteria_file,
        )
        return EXIT_INPUT_FILE_ERROR

    try:
        criteria = parse_review_criteria(criteria_text, str(args.criteria_file))
    except InvalidReviewCriteriaError as exc:
        logger.error("%s", exc)
        return EXIT_INPUT_FILE_ERROR

    try:
        parsed_diff = parse_diff(diff_text)
    except UnidiffParseError as exc:
        logger.error("Could not parse the diff returned for PR #%s: %s", args.pr, exc)
        return EXIT_GITHUB_FETCH_ERROR

    changed_files = exclude_paths(parsed_diff, exclude_globs)
    in_scope = filter_in_scope_files(changed_files, scope_dirs)
    if scope_dirs and not in_scope:
        logger.info(
            "All %d changed file(s) are out of scope (%s); nothing to review.",
            len(changed_files),
            ", ".join(scope_dirs),
        )
        return EXIT_SUCCESS

    diff_context, was_truncated = build_diff_context(in_scope, _MAX_DIFF_CONTEXT_CHARS)
    system_prompt = build_system_prompt(rules_content, lessons_content, criteria)
    allow_repo_exploration = _allow_repo_exploration((owner, name), repo_root)

    try:
        result = await run_review(
            pr_metadata=pr_metadata,
            diff_context=diff_context,
            was_truncated=was_truncated,
            system_prompt=system_prompt,
            criteria=criteria,
            changed_files=in_scope,
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

    # Reported separately, not collapsed: a consuming workflow retries an SDK
    # failure (transient — overloaded API, turn budget) but treats a missing
    # verdict as advisory. Folding both into 5 makes the first unrecoverable.
    if not result.sdk_success:
        failure = ("the Claude Agent SDK reported a failed run", EXIT_AGENT_ERROR)
    elif result.verdict is None:
        failure = ("the run completed without a valid verdict", EXIT_INCOMPLETE_RUN)
    else:
        failure = None

    if failure is not None:
        reason, exit_code = failure
        _ensure_json_artifact(review_output, args.out_dir, was_capped, args.format)
        logger.error(
            "Review run did not complete cleanly (%s); the %d finding(s) "
            "collected before the failure were written to %s.",
            reason,
            len(review_output.comments),
            args.out_dir,
        )
        # Deliberately never published, `--publish` or not: a PR comment
        # should only ever be one of two shapes — a clean pass (verdict +
        # per-criterion scores, even with zero findings) or one with findings
        # to fix. A crashed or incomplete run is neither, and posting an
        # "INCOMPLETE — NO VERDICT PRODUCED" comment reads ambiguously next
        # to a genuine clean pass. The failing exit code is the signal here;
        # local artifacts (written above) carry the detail for debugging.
        if args.format in ("console", "all"):
            print_console(review_output, was_capped)
        return exit_code

    if args.publish:
        try:
            post_review(args.pr, review_output, repo=repo)
        except GitHubPublishError as exc:
            _ensure_json_artifact(review_output, args.out_dir, was_capped, args.format)
            logger.error("%s Local artifacts were saved to %s.", exc, args.out_dir)
            return EXIT_PUBLISH_ERROR
        logger.info(
            "Posted %d inline comment(s) to PR #%d.",
            len(review_output.comments),
            args.pr,
        )
        if args.format in ("console", "all"):
            print_console(review_output, was_capped, published=True)
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
