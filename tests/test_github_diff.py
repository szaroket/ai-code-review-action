import httpx
import pytest
from githubkit.exception import RequestFailed

from pr_review_agent.github_diff import (
    GitHubApiError,
    _raise_for_github_error,
    resolve_repo,
)
from pr_review_agent.github_publish import _raise_for_publish_error

_ENV_VARS = ("GITHUB_REPOSITORY", "GH_TOKEN", "GITHUB_TOKEN")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the ambient CI/developer environment out of these assertions."""
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_explicit_repo_wins_over_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "someone/else")
    assert resolve_repo("owner/name") == ("owner", "name")


def test_repo_falls_back_to_github_repository_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "octocat/hello-world")
    assert resolve_repo() == ("octocat", "hello-world")


@pytest.mark.parametrize(
    "bad",
    ["no-slash", "too/many/slashes", "owner/", "/name", "own er/name", ""],
)
def test_malformed_repo_slug_is_rejected(bad: str) -> None:
    """A malformed slug must not silently fall through to autodetection."""
    if bad == "":
        pytest.skip("empty string is falsy and means 'autodetect', not 'invalid'")
    with pytest.raises(GitHubApiError, match="OWNER/REPO"):
        resolve_repo(bad)


def test_malformed_env_repo_does_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """A junk GITHUB_REPOSITORY is ignored, not fatal — autodetection continues."""
    monkeypatch.setenv("GITHUB_REPOSITORY", "junk-without-slash")
    # Falls through to the git remote, which exists in this checkout.
    owner, name = resolve_repo()
    assert owner and name


class _FakeResponse:
    """Just enough of a githubkit `Response` for the error-mapping helpers.

    `RequestFailed.__init__` reads `raw_request`/`raw_response` to build the
    underlying `httpx.HTTPStatusError`; the mapping code itself reads
    `status_code`, `headers` and `text`. Real `httpx` objects back the headers
    so the case-insensitive lookup under test is the real one.
    """

    def __init__(self, status_code: int, headers: dict[str, str]) -> None:
        self.raw_request = httpx.Request("GET", "https://api.github.com/test")
        self.raw_response = httpx.Response(
            status_code, headers=headers, text="{}", request=self.raw_request
        )
        self.status_code = status_code
        self.headers = self.raw_response.headers
        self.text = self.raw_response.text


def _failed(status_code: int, headers: dict[str, str]) -> RequestFailed:
    return RequestFailed(_FakeResponse(status_code, headers))  # pyright: ignore[reportArgumentType]


def test_secondary_rate_limit_403_is_not_reported_as_a_permissions_problem() -> None:
    """GitHub returns 403 for its secondary rate limit — the same status as a bad scope."""
    error = _raise_for_github_error(_failed(403, {"retry-after": "60"}), "PR #1")
    assert "rate-limited" in str(error)
    assert "60s" in str(error)
    assert "pull-requests" not in str(error)


def test_exhausted_primary_quota_is_recognised() -> None:
    error = _raise_for_github_error(
        _failed(403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1700000000"}),
        "PR #1",
    )
    assert "quota is exhausted" in str(error)
    assert "1700000000" in str(error)


def test_plain_403_still_points_at_the_token_scope() -> None:
    error = _raise_for_github_error(
        _failed(403, {"x-ratelimit-remaining": "42"}), "PR #1"
    )
    assert "pull-requests: read" in str(error)
    assert "rate-limited" not in str(error)


def test_publish_403_rate_limit_mentions_the_saved_artifacts() -> None:
    error = _raise_for_publish_error(_failed(403, {"retry-after": "30"}), "PR #1")
    assert "rate-limited" in str(error)
    assert "local artifacts" in str(error)
    assert "fork" not in str(error)
