"""Real PR-review posting via the GitHub reviews API.

Plain code, no agent involvement. Mirrors `github_diff.py`'s githubkit-client
style — same repo resolution, same client construction, same TLS/timeout
handling — since both modules talk to the same GitHub REST API; only the
error-mapping detail differs, since a failed *post* has a different likely
cause (token write-scope, fork PRs) than a failed *fetch*.
"""

import logging
from typing import cast

from githubkit.exception import RequestError, RequestFailed, RequestTimeout
from githubkit_schemas.latest.types import (
    ReposOwnerRepoPullsPullNumberReviewsPostBodyType,
)

from pr_review_agent.github_diff import (
    _HTTP_TIMEOUT_SECONDS,
    GitHubApiError,
    _client,
    resolve_repo,
)
from pr_review_agent.logging_config import redact
from pr_review_agent.models import ReviewOutput
from pr_review_agent.output import build_review_payload

logger = logging.getLogger(__name__)


class GitHubPublishError(RuntimeError):
    """Raised when posting a review to GitHub fails.

    Distinct from `GitHubApiError` (fetch-side failures in `github_diff.py`)
    so `cli.py` can map fetch failures and publish failures to different
    exit codes.
    """


def _raise_for_publish_error(exc: Exception, context: str) -> GitHubPublishError:
    """Translate a githubkit exception into a `GitHubPublishError` with guidance.

    Args:
        exc: The githubkit exception that was raised.
        context: Short description of the attempted post, e.g. "PR #12 in
            owner/repo".

    Returns:
        GitHubPublishError: The error to raise, with secrets redacted.
    """
    if isinstance(exc, RequestFailed):
        status = exc.response.status_code
        if status == 403:
            return GitHubPublishError(
                f"GitHub rejected posting the review for {context} (HTTP 403). "
                "This almost always means the token lacks `pull-requests: "
                "write`, or the PR is from a fork — a `pull_request` event "
                "from a fork gets a read-only default token regardless of "
                "which repo is consuming this action."
            )
        if status == 401:
            return GitHubPublishError(
                f"GitHub rejected the token while posting the review for "
                f"{context} (HTTP 401). Check that the token is valid."
            )
        if status == 404:
            return GitHubPublishError(
                f"{context} not found (HTTP 404) while posting the review. "
                "Check the PR number and that the token can see this "
                "repository."
            )
        return GitHubPublishError(
            f"GitHub returned HTTP {status} while posting the review for "
            f"{context}: {redact(exc.response.text[:500])}"
        )
    if isinstance(exc, RequestTimeout):
        return GitHubPublishError(
            f"Timed out after {_HTTP_TIMEOUT_SECONDS:.0f}s while posting the "
            f"review for {context}."
        )
    return GitHubPublishError(
        f"Network error while posting the review for {context}: {redact(str(exc))}"
    )


def post_review(
    pr_number: int, review_output: ReviewOutput, repo: str | None = None
) -> None:
    """Post a review to GitHub as real, per-line inline comments.

    Serializes `review_output` to exactly `{"event", "body", "comments"}` via
    `output.build_review_payload` — which already omits the local-only
    `criteria` key that `write_json` adds, since the reviews endpoint has no
    schema slot for it — and relies on `body` already containing the rendered
    criteria section built once in `output.build_summary`.

    Args:
        pr_number: The pull request number to review.
        review_output: The review to post. Callers must never pass a review
            whose `verdict` is None (the exit-5 incomplete-run path); that
            check belongs to `cli.py`, not here.
        repo: Optional `OWNER/REPO` to target. When omitted, the repository is
            resolved from `GITHUB_REPOSITORY` or the local `origin` remote.

    Raises:
        GitHubPublishError: If no token is available, the repository cannot
            be resolved, or the API call fails.
    """
    try:
        owner, name = resolve_repo(repo)
        github = _client()
    except GitHubApiError as exc:
        raise GitHubPublishError(str(exc)) from exc

    context = f"PR #{pr_number} in {owner}/{name}"
    payload = build_review_payload(review_output)

    try:
        github.rest.pulls.create_review(
            owner,
            name,
            pr_number,
            data=cast(ReposOwnerRepoPullsPullNumberReviewsPostBodyType, payload),
        )
    except (RequestFailed, RequestTimeout, RequestError) as exc:
        raise _raise_for_publish_error(exc, context) from exc

    logger.info(
        "Posted review to PR #%d: event=%s, %d inline comment(s)",
        pr_number,
        review_output.event,
        len(review_output.comments),
    )
