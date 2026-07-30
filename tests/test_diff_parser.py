from pathlib import Path

import pytest

from pr_review_agent.diff_parser import ChangedFile, build_diff_context, parse_diff

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample.diff"


@pytest.fixture
def sample_diff_text() -> str:
    return FIXTURE_PATH.read_text(encoding="utf-8")


@pytest.fixture
def sample_files(sample_diff_text: str) -> list[ChangedFile]:
    return parse_diff(sample_diff_text)


def _by_path(files: list[ChangedFile], path: str) -> ChangedFile:
    return next(f for f in files if f.path == path)


def test_one_changed_file_per_entry(sample_files: list[ChangedFile]) -> None:
    assert len(sample_files) == 5
    assert {f.path for f in sample_files} == {
        "modified.py",
        "new_file.py",
        "new_name.py",
        "to_remove.py",
        "image.png",
    }


def test_modified_file_line_numbers(sample_files: list[ChangedFile]) -> None:
    modified = _by_path(sample_files, "modified.py")
    assert modified.added_line_numbers == [2, 3]
    assert modified.removed_line_numbers == [2]
    assert not modified.is_added
    assert not modified.is_removed
    assert not modified.is_renamed


def test_added_file_has_no_removed_lines(sample_files: list[ChangedFile]) -> None:
    added = _by_path(sample_files, "new_file.py")
    assert added.is_added
    assert added.removed_line_numbers == []
    assert added.added_line_numbers == [1, 2, 3]


def test_removed_file_has_no_added_lines(sample_files: list[ChangedFile]) -> None:
    removed = _by_path(sample_files, "to_remove.py")
    assert removed.is_removed
    assert removed.added_line_numbers == []
    assert removed.removed_line_numbers == [1, 2]


def test_renamed_file_captures_source_path(sample_files: list[ChangedFile]) -> None:
    renamed = _by_path(sample_files, "new_name.py")
    assert renamed.is_renamed
    assert renamed.source_path == "old_name.py"
    assert renamed.added_line_numbers == [2]
    assert renamed.removed_line_numbers == [2]


def test_binary_file_does_not_crash_and_has_empty_line_lists(
    sample_files: list[ChangedFile],
) -> None:
    binary = _by_path(sample_files, "image.png")
    assert binary.added_line_numbers == []
    assert binary.removed_line_numbers == []
    assert binary.hunks_text == "[binary file, diff not shown]"


def test_build_diff_context_under_budget_returns_full_text(
    sample_files: list[ChangedFile],
) -> None:
    text, was_truncated = build_diff_context(sample_files, max_chars=100_000)
    assert not was_truncated
    for f in sample_files:
        assert f.path in text
        assert f.hunks_text in text


def test_build_diff_context_over_budget_drops_whole_files(
    sample_files: list[ChangedFile],
) -> None:
    file_list_len = len(
        "Changed files:\n" + "\n".join(f.path for f in sample_files) + "\n\n"
    )
    # Budget for the file-list header plus exactly the first file's block.
    first_block_len = len(
        f"File: {sample_files[0].path}\n{sample_files[0].hunks_text}\n"
    )
    max_chars = file_list_len + first_block_len

    text, was_truncated = build_diff_context(sample_files, max_chars=max_chars)

    assert was_truncated
    # The full file list is always present, even for dropped files.
    for f in sample_files:
        assert f.path in text.split("\n\n", 1)[0]
    # The first file's hunk text is kept in full (no mid-file truncation).
    assert sample_files[0].hunks_text in text
    # A later file's hunk text was dropped wholesale.
    assert sample_files[-1].hunks_text not in text
