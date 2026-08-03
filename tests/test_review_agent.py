"""Unit tests for the pure, SDK-free parts of the agent loop."""

import asyncio
from pathlib import Path
from typing import Any

import pytest

from pr_review_agent import review_agent
from pr_review_agent.diff_parser import ChangedFile
from pr_review_agent.github_diff import PullRequestMetadata
from pr_review_agent.models import DiffSide, Finding, Severity
from pr_review_agent.review_agent import (
    _anchor_error,
    _build_options,
    _Collector,
    _make_repo_path_guard,
    run_review,
)


def _changed_file(
    path: str,
    *,
    added: list[int] | None = None,
    removed: list[int] | None = None,
) -> ChangedFile:
    return ChangedFile(
        path=path,
        is_added=False,
        is_removed=False,
        is_renamed=False,
        source_path=None,
        hunks_text="",
        added_line_numbers=added or [],
        removed_line_numbers=removed or [],
    )


def _finding(
    path: str = "api/handler.py",
    line: int = 12,
    side: DiffSide = DiffSide.RIGHT,
) -> Finding:
    return Finding(
        path=path,
        line=line,
        side=side,
        severity=Severity.WARNING,
        comment="something",
        rule_reference=None,
    )


_FILES = {
    "api/handler.py": _changed_file(
        "api/handler.py", added=[12, 13, 40], removed=[7, 8]
    )
}


def test_line_in_the_diff_is_accepted() -> None:
    assert _anchor_error(_finding(line=40), _FILES) is None


def test_left_side_is_checked_against_removed_lines() -> None:
    assert _anchor_error(_finding(line=7, side=DiffSide.LEFT), _FILES) is None
    # 12 is a valid RIGHT line, but not a valid LEFT one.
    assert _anchor_error(_finding(line=12, side=DiffSide.LEFT), _FILES) is not None


def test_hallucinated_line_is_rejected_with_the_valid_lines() -> None:
    """The error has to be actionable: the model needs to know what to use instead."""
    error = _anchor_error(_finding(line=87), _FILES)
    assert error is not None
    assert "87" in error
    assert "api/handler.py" in error
    assert "12, 13, 40" in error


def test_unknown_path_is_rejected_and_lists_the_reviewable_paths() -> None:
    error = _anchor_error(_finding(path="not/in/diff.py"), _FILES)
    assert error is not None
    assert "not/in/diff.py" in error
    assert "api/handler.py" in error


def test_file_with_no_lines_on_that_side_is_rejected() -> None:
    """A binary file or a pure rename has no anchorable line at all."""
    files = {"logo.png": _changed_file("logo.png")}
    error = _anchor_error(_finding(path="logo.png", line=1), files)
    assert error is not None
    assert "no RIGHT-side lines" in error


def test_long_line_lists_are_summarised_not_dumped() -> None:
    """A 500-line file must not spend the turn budget on a rejection message."""
    files = {"big.py": _changed_file("big.py", added=list(range(1, 501)))}
    error = _anchor_error(_finding(path="big.py", line=9000), files)
    assert error is not None
    assert "(500 in total)" in error
    assert "500," not in error


def _guard_decision(repo_root: Path, tool_name: str, **tool_input: Any) -> str | None:
    """Run the PreToolUse guard once and return its decision, or None to allow."""
    guard = _make_repo_path_guard(repo_root)
    result = asyncio.run(
        guard(
            {  # pyright: ignore[reportArgumentType] - partial PreToolUse payload
                "hook_event_name": "PreToolUse",
                "tool_name": tool_name,
                "tool_input": tool_input,
            },
            None,
            None,  # pyright: ignore[reportArgumentType] - context is unused
        )
    )
    specific = result.get("hookSpecificOutput")
    if specific is None:
        return None
    return specific["permissionDecision"]


@pytest.mark.parametrize(
    "tool_input",
    [
        {"file_path": "/etc/passwd"},
        {"file_path": "../../../etc/passwd"},
        {"path": "../.."},
    ],
)
def test_guard_denies_out_of_repo_path_arguments(
    tmp_path: Path, tool_input: dict[str, str]
) -> None:
    assert _guard_decision(tmp_path, "Read", **tool_input) == "deny"


def test_guard_allows_an_in_repo_path(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    assert _guard_decision(tmp_path, "Read", file_path="src") is None


@pytest.mark.parametrize(
    "pattern",
    [
        "/home/runner/**/*",
        "../../../**/*.pem",
        "/**/*.pem",  # absolute with nothing left to anchor: fail closed
    ],
)
def test_guard_denies_globs_that_escape_the_checkout(
    tmp_path: Path, pattern: str
) -> None:
    """Glob needs no `path` argument, so `pattern` is the only thing to check."""
    assert _guard_decision(tmp_path, "Glob", pattern=pattern) == "deny"


@pytest.mark.parametrize("pattern", ["**/*.py", "src/**/*.py", "*.md"])
def test_guard_allows_in_repo_globs(tmp_path: Path, pattern: str) -> None:
    assert _guard_decision(tmp_path, "Glob", pattern=pattern) is None


def test_guard_does_not_treat_a_grep_regex_as_a_path(tmp_path: Path) -> None:
    """Grep's `pattern` matches contents; reading it as a path would deny valid calls."""
    assert _guard_decision(tmp_path, "Grep", pattern="/etc/passwd|../../secret") is None


def _options(tmp_path: Path, *, allow_repo_exploration: bool = True) -> Any:
    return _build_options(
        system_prompt="review this",
        repo_root=tmp_path,
        model="claude-sonnet-5",
        max_turns=5,
        allow_repo_exploration=allow_repo_exploration,
        collector=_Collector(),
        criteria=[],
        changed_files=[],
    )


def test_options_do_not_load_consumer_settings(tmp_path: Path) -> None:
    """The checkout is the PR's own head: its settings/CLAUDE.md/MCP must not load.

    This is a two-kwarg pair that reads like a default, so a version bump could
    plausibly drop it — hence a test rather than a comment.
    """
    options = _options(tmp_path)
    assert options.setting_sources == []
    assert options.strict_mcp_config is True


def test_options_never_bypass_permissions(tmp_path: Path) -> None:
    """`bypassPermissions` would ignore `allowed_tools` entirely."""
    assert _options(tmp_path).permission_mode == "dontAsk"


@pytest.mark.parametrize("tool", ["Bash", "Write", "Edit", "WebFetch", "WebSearch"])
def test_mutation_and_egress_tools_are_disallowed(tmp_path: Path, tool: str) -> None:
    """WebFetch is the exfiltration path the PreToolUse guard cannot see."""
    options = _options(tmp_path)
    assert tool in options.disallowed_tools
    assert tool not in options.allowed_tools


def test_exploration_tools_are_dropped_on_a_repo_mismatch(tmp_path: Path) -> None:
    options = _options(tmp_path, allow_repo_exploration=False)
    for tool in ("Read", "Grep", "Glob"):
        assert tool not in options.allowed_tools


def _pr_metadata() -> PullRequestMetadata:
    return PullRequestMetadata(
        number=1,
        title="Add a handler",
        url="https://github.com/owner/name/pull/1",
        base_ref_name="main",
        head_ref_name="feature",
        changed_file_count=1,
        state="open",
        merged=False,
    )


def test_run_review_survives_a_mid_run_sdk_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A max-turns-style in-run failure must not crash the whole review.

    The SDK's own control loop raises a bare `Exception` for this case
    (`_internal/query.py`'s `receive_messages`, not a `ClaudeSDKError`
    subclass), so `run_review` is the only place that can turn it into a
    reportable result instead of an unhandled traceback.
    """

    async def _boom(*, prompt: str, options: object) -> Any:
        if False:  # pragma: no cover - makes this an async generator
            yield
        raise RuntimeError("Reached maximum number of turns (5)")

    monkeypatch.setattr(review_agent, "query", _boom)

    result = asyncio.run(
        run_review(
            pr_metadata=_pr_metadata(),
            diff_context="diff --git a/x b/x\n",
            was_truncated=False,
            system_prompt="be a reviewer",
            criteria=[],
            changed_files=[],
            repo_root=tmp_path,
            model="claude-sonnet-5",
            max_turns=5,
            allow_repo_exploration=False,
        )
    )

    assert result.sdk_success is False
    assert result.verdict is None
    assert result.findings == []
