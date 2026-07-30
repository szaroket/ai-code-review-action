"""GitHub REST access via githubkit: PR metadata, PR diffs, repo-root resolution."""

import logging
import os
import re
import shutil
import ssl
import subprocess
from dataclasses import dataclass
from pathlib import Path

import truststore
from githubkit import GitHub
from githubkit.exception import RequestError, RequestFailed, RequestTimeout

from pr_review_agent.logging_config import redact

logger = logging.getLogger(__name__)

_TOKEN_ENV_VARS = ("GH_TOKEN", "GITHUB_TOKEN")
_DIFF_MEDIA_TYPE = "application/vnd.github.diff"
_HTTP_TIMEOUT_SECONDS = 60.0
_GIT_TIMEOUT_SECONDS = 30
_REPO_SLUG = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
_REMOTE_URL = re.compile(
    r"(?:github\.com[:/])(?P<owner>[A-Za-z0-9._-]+)/(?P<repo>[A-Za-z0-9._-]+?)(?:\.git)?/?$"
)


class GitHubApiError(RuntimeError):
    """Raised when a GitHub REST call fails or cannot be attempted."""


@dataclass(frozen=True)
class PullRequestMetadata:
    """A pull request's identifying fields and its changed-file paths."""

    number: int
    title: str
    url: str
    base_ref_name: str
    head_ref_name: str
    files: list[str]


def _build_ssl_context() -> ssl.SSLContext:
    """Build an SSL context that trusts the OS certificate store.

    Uses `truststore` rather than `certifi` so corporate proxies and
    antivirus TLS interception — whose roots are installed in the OS store,
    never in `certifi` — do not break the tool on developer machines. CI
    runners are unaffected either way.

    `SSLKEYLOGFILE` is suppressed while the context is created: this process
    handles GitHub tokens and API keys, so writing TLS session keys to disk is
    an unacceptable leak vector. On Windows it also crashes CPython outright
    when the variable points at a device path, as some antivirus products set.

    Returns:
        ssl.SSLContext: A client context backed by the OS trust store.
    """
    keylog = os.environ.pop("SSLKEYLOGFILE", None)
    try:
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    finally:
        if keylog is not None:
            os.environ["SSLKEYLOGFILE"] = keylog


def _resolve_token() -> str:
    """Read the GitHub token from the environment.

    Returns:
        str: The token from `GH_TOKEN`, else `GITHUB_TOKEN`.

    Raises:
        GitHubApiError: If neither variable is set to a non-empty value.
    """
    for name in _TOKEN_ENV_VARS:
        token = os.environ.get(name, "").strip()
        if token:
            logger.debug("Using GitHub token from %s", name)
            return token
    raise GitHubApiError(
        "No GitHub token found. Set `GH_TOKEN` or `GITHUB_TOKEN` "
        "(in GitHub Actions, pass `github-token: ${{ github.token }}`; "
        "locally, `export GH_TOKEN=$(gh auth token)`)."
    )


def _client() -> GitHub:
    """Construct an authenticated githubkit client.

    Returns:
        GitHub: A client with an explicit timeout and OS-trust-store TLS.

    Raises:
        GitHubApiError: If no token is available.
    """
    return GitHub(
        _resolve_token(),
        timeout=_HTTP_TIMEOUT_SECONDS,
        ssl_verify=_build_ssl_context(),
        user_agent="pr-review-agent",
    )


def _raise_for_github_error(exc: Exception, context: str) -> GitHubApiError:
    """Translate a githubkit exception into a `GitHubApiError` with guidance.

    Args:
        exc: The githubkit exception that was raised.
        context: Short description of the attempted call, e.g. "PR #12".

    Returns:
        GitHubApiError: The error to raise, with secrets redacted.
    """
    if isinstance(exc, RequestFailed):
        status = exc.response.status_code
        if status in (401, 403):
            return GitHubApiError(
                f"GitHub rejected the token while fetching {context} "
                f"(HTTP {status}). Check that the token is valid and has "
                "`pull-requests: read` (and `write` to post reviews)."
            )
        if status == 404:
            return GitHubApiError(
                f"{context} not found (HTTP 404). Check the PR number and "
                "that the token can see this repository."
            )
        return GitHubApiError(
            f"GitHub returned HTTP {status} while fetching {context}: "
            f"{redact(exc.response.text[:500])}"
        )
    if isinstance(exc, RequestTimeout):
        return GitHubApiError(
            f"Timed out after {_HTTP_TIMEOUT_SECONDS:.0f}s while fetching {context}."
        )
    return GitHubApiError(f"Network error while fetching {context}: {redact(str(exc))}")


def resolve_repo(repo: str | None = None) -> tuple[str, str]:
    """Determine the `owner, name` pair to operate on.

    Resolution order: the explicit `repo` argument, then `GITHUB_REPOSITORY`
    (always set by GitHub Actions), then the `origin` remote of the local
    checkout.

    Args:
        repo: Optional `OWNER/REPO` slug.

    Returns:
        tuple[str, str]: The owner and repository name.

    Raises:
        GitHubApiError: If `repo` is malformed, or none of the sources yield a
            repository.
    """
    if repo:
        if not _REPO_SLUG.match(repo):
            raise GitHubApiError(
                f"Invalid repository {repo!r}; expected the form `OWNER/REPO`."
            )
        owner, name = repo.split("/", 1)
        return owner, name

    env_repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if _REPO_SLUG.match(env_repo):
        owner, name = env_repo.split("/", 1)
        logger.debug("Resolved repo %s/%s from GITHUB_REPOSITORY", owner, name)
        return owner, name

    remote = _git_output(["remote", "get-url", "origin"])
    match = _REMOTE_URL.search(remote) if remote else None
    if match:
        owner, name = match.group("owner"), match.group("repo")
        logger.debug("Resolved repo %s/%s from the `origin` remote", owner, name)
        return owner, name

    raise GitHubApiError(
        "Could not determine which repository to review. Pass `--repo OWNER/REPO`, "
        "or run inside a checkout whose `origin` remote points at GitHub."
    )


def get_pr_metadata(pr_number: int, repo: str | None = None) -> PullRequestMetadata:
    """Fetch a pull request's metadata and changed-file paths.

    Args:
        pr_number: The pull request number.
        repo: Optional `OWNER/REPO` to target. When omitted, the repository is
            resolved from `GITHUB_REPOSITORY` or the local `origin` remote.

    Returns:
        PullRequestMetadata: The parsed pull request metadata.

    Raises:
        GitHubApiError: If no token is available, the repository cannot be
            resolved, or the API call fails.
    """
    owner, name = resolve_repo(repo)
    github = _client()
    context = f"PR #{pr_number} in {owner}/{name}"

    try:
        pull_request = github.rest.pulls.get(owner, name, pr_number).parsed_data
        files = [
            entry.filename
            for entry in github.rest.paginate(
                github.rest.pulls.list_files,
                owner=owner,
                repo=name,
                pull_number=pr_number,
            )
        ]
    except (RequestFailed, RequestTimeout, RequestError) as exc:
        raise _raise_for_github_error(exc, context) from exc

    logger.info(
        "Fetched metadata for PR #%d: %d changed file(s)", pr_number, len(files)
    )
    return PullRequestMetadata(
        number=pull_request.number,
        title=pull_request.title,
        url=pull_request.html_url,
        base_ref_name=pull_request.base.ref,
        head_ref_name=pull_request.head.ref,
        files=files,
    )


def get_pr_diff(pr_number: int, repo: str | None = None) -> str:
    """Fetch a pull request's unified diff via the `vnd.github.diff` media type.

    This returns the same complete patch `git diff` would produce. It is
    deliberately not built from the per-file `list_files` payload, whose
    `patch` field GitHub omits for large files and caps at 3000 entries.

    Path exclusion is applied after parsing — see
    `diff_parser.exclude_paths` — because the REST API has no server-side
    exclude parameter.

    Args:
        pr_number: The pull request number.
        repo: Optional `OWNER/REPO` to target. When omitted, the repository is
            resolved from `GITHUB_REPOSITORY` or the local `origin` remote.

    Returns:
        str: The raw unified diff text.

    Raises:
        GitHubApiError: If no token is available, the repository cannot be
            resolved, or the API call fails.
    """
    owner, name = resolve_repo(repo)
    github = _client()
    context = f"the diff for PR #{pr_number} in {owner}/{name}"

    try:
        response = github.request(
            "GET",
            f"/repos/{owner}/{name}/pulls/{pr_number}",
            headers={"Accept": _DIFF_MEDIA_TYPE},
        )
    except (RequestFailed, RequestTimeout, RequestError) as exc:
        raise _raise_for_github_error(exc, context) from exc

    diff_text = response.text
    logger.info("Fetched diff for PR #%d (%d bytes)", pr_number, len(diff_text))
    return diff_text


def _git_output(args: list[str]) -> str | None:
    """Run a read-only `git` command, returning stripped stdout or None.

    Never raises: every failure mode (missing `git`, OS error, timeout,
    non-zero exit) is logged and reported as None, because both callers
    degrade rather than fail.

    Args:
        args: Arguments to pass to `git`, excluding the program name.

    Returns:
        str | None: Stripped stdout on success, otherwise None.
    """
    if shutil.which("git") is None:
        logger.warning("`git` not found on PATH")
        return None

    logger.debug("Running: git %s (cwd=%s)", " ".join(args), Path.cwd())
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Failed to invoke `git %s`: %s", " ".join(args), exc)
        return None

    if result.returncode != 0:
        logger.debug("`git %s` exited %d", " ".join(args), result.returncode)
        return None

    return result.stdout.strip()


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
    toplevel = _git_output(["rev-parse", "--show-toplevel"])
    if not toplevel:
        logger.warning(
            "Could not determine the git checkout root; falling back to the "
            "current directory for repo exploration."
        )
        return Path.cwd()

    repo_root = Path(toplevel)
    logger.debug("Resolved repo root: %s", repo_root)
    return repo_root
