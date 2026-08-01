"""Tests for the publish boundary, focused on the APPROVE→COMMENT downgrade."""

from typing import Any

import httpx
import pytest
from githubkit.exception import RequestFailed

from pr_review_agent import github_publish
from pr_review_agent.github_publish import GitHubPublishError, post_review
from pr_review_agent.models import (
    DiffSide,
    Finding,
    ReviewEvent,
    ReviewOutput,
    Severity,
)

_APPROVAL_422 = (
    '{"message":"Unprocessable Entity","errors":["GitHub Actions is not '
    'permitted to approve pull requests."]}'
)


class _FakeResponse:
    """Just enough of a githubkit `Response` to build a `RequestFailed`."""

    def __init__(self, status_code: int, text: str) -> None:
        self.raw_request = httpx.Request("POST", "https://api.github.com/test")
        self.raw_response = httpx.Response(
            status_code, text=text, request=self.raw_request
        )
        self.status_code = status_code
        self.headers = self.raw_response.headers
        self.text = text


def _failed(status_code: int, text: str) -> RequestFailed:
    return RequestFailed(_FakeResponse(status_code, text))  # pyright: ignore[reportArgumentType]


def _review(event: ReviewEvent = ReviewEvent.APPROVE) -> ReviewOutput:
    return ReviewOutput(
        pr_number=42,
        event=event,
        summary_body="Looks good.",
        comments=[
            Finding(
                path="api/handler.py",
                line=1,
                side=DiffSide.RIGHT,
                severity=Severity.NIT,
                comment="Consider a clearer name.",
                rule_reference=None,
            )
        ],
    )


@pytest.fixture(autouse=True)
def stub_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the repo/token/HTTP boundaries out of these assertions."""
    monkeypatch.setattr(github_publish, "resolve_repo", lambda repo: ("owner", "name"))
    monkeypatch.setattr(github_publish, "build_client", lambda: object())


def _record_posts(
    monkeypatch: pytest.MonkeyPatch, *errors: Exception | None
) -> list[dict[str, Any]]:
    """Stub `_post` to record each payload and raise the matching error, if any."""
    seen: list[dict[str, Any]] = []
    remaining = list(errors)

    def _post(
        github: object, owner: str, name: str, pr_number: int, payload: dict[str, Any]
    ) -> None:
        seen.append(payload)
        error = remaining.pop(0) if remaining else None
        if error is not None:
            raise error

    monkeypatch.setattr(github_publish, "_post", _post)
    return seen


def test_successful_post_happens_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _record_posts(monkeypatch)
    post_review(42, _review(), repo="owner/name")
    assert len(seen) == 1
    assert seen[0]["event"] == "APPROVE"


def test_approval_restriction_reposts_once_as_comment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The findings are the valuable part; losing them over a token restriction is worse."""
    seen = _record_posts(monkeypatch, _failed(422, _APPROVAL_422), None)
    post_review(42, _review(), repo="owner/name")

    assert [payload["event"] for payload in seen] == ["APPROVE", "COMMENT"]
    # Everything except the event is carried over untouched.
    assert seen[1]["body"] == seen[0]["body"]
    assert seen[1]["comments"] == seen[0]["comments"]


def test_a_failing_retry_is_not_retried_again(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _record_posts(monkeypatch, _failed(422, _APPROVAL_422), _failed(500, "boom"))
    with pytest.raises(GitHubPublishError):
        post_review(42, _review(), repo="owner/name")
    assert len(seen) == 2


def test_any_other_422_raises_without_a_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """A malformed payload must not be silently reposted as a COMMENT."""
    seen = _record_posts(
        monkeypatch, _failed(422, '{"message":"Line could not be resolved"}')
    )
    with pytest.raises(GitHubPublishError) as excinfo:
        post_review(42, _review(), repo="owner/name")

    assert len(seen) == 1
    assert "422" in str(excinfo.value)


def test_403_raises_without_a_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _record_posts(monkeypatch, _failed(403, "Forbidden"))
    with pytest.raises(GitHubPublishError) as excinfo:
        post_review(42, _review(), repo="owner/name")

    assert len(seen) == 1
    assert "pull-requests: write" in str(excinfo.value)
