"""Subprocess wrapper around the `gh` CLI: PR metadata, PR diffs, repo-root resolution."""

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


class GhCommandError(RuntimeError):
    """Raised when a `gh` CLI invocation fails."""


@dataclass(frozen=True)
class PullRequestMetadata:
    number: int
    title: str
    url: str
    base_ref_name: str
    head_ref_name: str
    files: list[str]


def _run_gh(args: list[str]) -> str:
    if shutil.which("gh") is None:
        logger.error("`gh` CLI not found on PATH")
        raise GhCommandError(
            "`gh` CLI not found on PATH. Install it from https://cli.github.com/ "
            "and ensure it is available to this job."
        )

    logger.debug("Running: gh %s", " ".join(args))
    try:
        result = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        logger.error("Failed to invoke `gh`: %s", exc)
        raise GhCommandError(f"Failed to invoke `gh`: {exc}") from exc

    if result.returncode != 0:
        stderr = result.stderr.strip()
        lowered = stderr.lower()
        if any(
            marker in lowered
            for marker in ("auth login", "not logged into", "authentication")
        ):
            logger.error("`gh` is not authenticated: %s", stderr)
            raise GhCommandError(
                "`gh` is not authenticated. Run `gh auth login`, or set `GH_TOKEN`. "
                f"Original error: {stderr}"
            )
        if any(
            marker in lowered
            for marker in (
                "could not resolve to a pullrequest",
                "no pull requests found",
            )
        ):
            logger.error("Pull request not found: %s", stderr)
            raise GhCommandError(f"Pull request not found. Original error: {stderr}")
        logger.error("`gh %s` failed: %s", " ".join(args), stderr)
        raise GhCommandError(f"`gh {' '.join(args)}` failed: {stderr}")

    logger.debug("gh command succeeded (%d bytes stdout)", len(result.stdout))
    return result.stdout


def get_pr_metadata(pr_number: int, repo: str | None = None) -> PullRequestMetadata:
    """Fetch a pull request's metadata via `gh pr view`.

    Args:
        pr_number: The pull request number.
        repo: Optional `OWNER/REPO` to target. When omitted, `gh` targets the
            repo detected from the current directory.

    Returns:
        PullRequestMetadata: The parsed pull request metadata.

    Raises:
        GhCommandError: If `gh` is missing, unauthenticated, the PR doesn't
            exist, or the invocation otherwise fails.
    """
    args = [
        "pr",
        "view",
        str(pr_number),
        "--json",
        "number,title,url,baseRefName,headRefName,files",
    ]
    if repo:
        args.extend(["-R", repo])

    raw = _run_gh(args)
    data = json.loads(raw)
    logger.info(
        "Fetched metadata for PR #%d: %d changed file(s)", pr_number, len(data["files"])
    )
    return PullRequestMetadata(
        number=data["number"],
        title=data["title"],
        url=data["url"],
        base_ref_name=data["baseRefName"],
        head_ref_name=data["headRefName"],
        files=[f["path"] for f in data["files"]],
    )


def get_pr_diff(
    pr_number: int, repo: str | None = None, exclude_globs: list[str] | None = None
) -> str:
    """Fetch a pull request's unified diff via `gh pr diff --patch`.

    Args:
        pr_number: The pull request number.
        repo: Optional `OWNER/REPO` to target. When omitted, `gh` targets the
            repo detected from the current directory.
        exclude_globs: Optional glob patterns to exclude from the diff, each
            passed as a separate `-e` flag to `gh pr diff`.

    Returns:
        str: The raw unified diff text.

    Raises:
        GhCommandError: If `gh` is missing, unauthenticated, the PR doesn't
            exist, or the invocation otherwise fails.
    """
    args = ["pr", "diff", str(pr_number), "--patch"]
    if repo:
        args.extend(["-R", repo])
    for glob in exclude_globs or []:
        args.extend(["-e", glob])

    diff_text = _run_gh(args)
    logger.info("Fetched diff for PR #%d (%d bytes)", pr_number, len(diff_text))
    return diff_text


def find_repo_root() -> Path:
    """Resolve the local git checkout root, degrading to the cwd if unavailable.

    Used as the `cwd` for agent repo-exploration tools and for resolving
    relative file inputs against the caller's checkout. Never raises: outside
    a git checkout (or without `git` on `PATH`), a warning is logged to
    stderr and `Path.cwd()` is returned instead, since a diff-only review can
    still proceed without local repo exploration.

    Returns:
        Path: The checkout root from `git rev-parse --show-toplevel`, or
        `Path.cwd()` on any failure to determine it.
    """
    if shutil.which("git") is None:
        logger.warning(
            "`git` not found on PATH; falling back to the current directory "
            "for repo exploration."
        )
        return Path.cwd()

    logger.debug("Running: git rev-parse --show-toplevel (cwd=%s)", Path.cwd())
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        logger.warning(
            "Failed to invoke `git` (%s); falling back to the current directory "
            "for repo exploration.",
            exc,
        )
        return Path.cwd()

    if result.returncode != 0:
        logger.warning(
            "Current directory is not a git checkout; falling back to the "
            "current directory for repo exploration."
        )
        return Path.cwd()

    repo_root = Path(result.stdout.strip())
    logger.debug("Resolved repo root: %s", repo_root)
    return repo_root
