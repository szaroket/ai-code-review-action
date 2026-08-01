"""Real PR-review posting via the GitHub reviews API.

Plain code, no agent involvement. Mirrors `github_diff.py`'s githubkit-client
style — same repo resolution, same client construction, same TLS/timeout
handling — since both modules talk to the same GitHub REST API; only the
error-mapping detail differs, since a failed *post* has a different likely
cause (token write-scope, fork PRs) than a failed *fetch*.
"""

import logging
from typing import Any, cast

from githubkit import GitHub
from githubkit.exception import RequestError, RequestFailed, RequestTimeout
from githubkit_schemas.latest.types import (
    ReposOwnerRepoPullsPullNumberReviewsPostBodyType,
)

from pr_review_agent.github_diff import (
    HTTP_TIMEOUT_SECONDS,
    GitHubApiError,
    build_client,
    rate_limit_reason,
    resolve_repo,
)
from pr_review_agent.logging_config import redact
from pr_review_agent.models import ReviewEvent, ReviewOutput
from pr_review_agent.output import build_review_payload

logger = logging.getLogger(__name__)

# GitHub's own restriction, not this tool's: the default Actions GITHUB_TOKEN
# is structurally barred from approving a pull request, even with `pull-
# requests: write`. It surfaces as a 422 rather than a 403, so it can't be
# told apart from a genuinely malformed payload by status code alone.
_APPROVAL_NOT_PERMITTED_MARKER = "not permitted to approve pull requests"


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
        if status in (403, 429):
            limited = rate_limit_reason(exc.response)
            if limited is not None:
                return GitHubPublishError(
                    f"GitHub rate-limited posting the review for {context} "
                    f"(HTTP {status}): {limited}. This is not a permissions "
                    "problem — the review is saved in the local artifacts; "
                    "wait and re-run."
                )
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
            f"{context}: {redact(exc.response.text)[:500]}"
        )
    if isinstance(exc, RequestTimeout):
        return GitHubPublishError(
            f"Timed out after {HTTP_TIMEOUT_SECONDS:.0f}s while posting the "
            f"review for {context}."
        )
    return GitHubPublishError(
        f"Network error while posting the review for {context}: {redact(str(exc))}"
    )


def _is_approval_not_permitted(exc: Exception) -> bool:
    """Detect GitHub's "Actions can't approve PRs" rejection specifically.

    Args:
        exc: The exception raised by `create_review`.

    Returns:
        bool: True if `exc` is the 422 GitHub returns for exactly this
        restriction, not some other unprocessable-payload cause.
    """
    return (
        isinstance(exc, RequestFailed)
        and exc.response.status_code == 422
        and _APPROVAL_NOT_PERMITTED_MARKER in exc.response.text
    )


def _post(
    github: GitHub, owner: str, name: str, pr_number: int, payload: dict[str, Any]
) -> None:
    """Call `create_review` with an already-built payload.

    Args:
        github: The githubkit client.
        owner: Repository owner.
        name: Repository name.
        pr_number: The pull request number to review.
        payload: `{"event", "body", "comments"}`, per `build_review_payload`.

    Returns:
        None: The created review is not read back; callers only need to know
        whether the call raised.

    Raises:
        RequestFailed: If GitHub answers with an error status.
        RequestTimeout: If the call exceeds `HTTP_TIMEOUT_SECONDS`.
        RequestError: For any other transport-level failure.
    """
    github.rest.pulls.create_review(
        owner,
        name,
        pr_number,
        data=cast(ReposOwnerRepoPullsPullNumberReviewsPostBodyType, payload),
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

    If the model's verdict was `APPROVE` and GitHub rejects the post because
    the default Actions token is barred from approving pull requests, this
    retries once as a `COMMENT` review instead of failing outright — the
    inline findings and summary are the valuable part of the post, and losing
    them entirely over a token restriction this tool can't do anything about
    would be worse than posting without the formal approval state. The
    verdict recorded in local artifacts (`review_output.event`) is untouched;
    only the outgoing payload is downgraded.

    Args:
        pr_number: The pull request number to review.
        review_output: The review to post. Callers must never pass a review
            whose `verdict` is None (the exit-5 incomplete-run path); that
            check belongs to `cli.py`, not here.
        repo: Optional `OWNER/REPO` to target. When omitted, the repository is
            resolved from `GITHUB_REPOSITORY` or the local `origin` remote.

    Raises:
        GitHubPublishError: If no token is available, the repository cannot
            be resolved, or the API call fails (including the retried post).
    """
    try:
        owner, name = resolve_repo(repo)
        github = build_client()
    except GitHubApiError as exc:
        raise GitHubPublishError(str(exc)) from exc

    context = f"PR #{pr_number} in {owner}/{name}"
    payload = build_review_payload(review_output)
    posted_event = payload["event"]

    try:
        _post(github, owner, name, pr_number, payload)
    except (RequestFailed, RequestTimeout, RequestError) as exc:
        # The event check is not redundant with the marker: it is what makes
        # the docstring's promise ("if the model's verdict was APPROVE") true
        # in code, so a future marker match on a non-APPROVE payload can't
        # silently repost something GitHub already rejected on other grounds.
        if posted_event != ReviewEvent.APPROVE or not _is_approval_not_permitted(exc):
            raise _raise_for_publish_error(exc, context) from exc

        logger.warning(
            "GitHub Actions' default token cannot approve pull requests; "
            "posting %s as a COMMENT review instead of APPROVE.",
            context,
        )
        posted_event = "COMMENT"
        retry_payload = {**payload, "event": posted_event}
        try:
            _post(github, owner, name, pr_number, retry_payload)
        except (RequestFailed, RequestTimeout, RequestError) as retry_exc:
            raise _raise_for_publish_error(retry_exc, context) from retry_exc

    logger.info(
        "Posted review to PR #%d: event=%s, %d inline comment(s)",
        pr_number,
        posted_event,
        len(review_output.comments),
    )
