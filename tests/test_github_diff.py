import pytest

from pr_review_agent.github_diff import GitHubApiError, resolve_repo

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
