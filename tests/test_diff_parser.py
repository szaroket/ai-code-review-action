from pathlib import Path

import pytest

from pr_review_agent.diff_parser import (
    ChangedFile,
    build_diff_context,
    exclude_paths,
    parse_diff,
)

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


def test_removed_file_hunks_text_is_placeholder(
    sample_files: list[ChangedFile],
) -> None:
    """A deleted file's body is never rendered — nothing left to review."""
    removed = _by_path(sample_files, "to_remove.py")
    assert (
        removed.hunks_text == "[file deleted, contents not shown — nothing to review]"
    )


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


def _stub_file(path: str, hunk_size: int) -> ChangedFile:
    return ChangedFile(
        path=path,
        is_added=False,
        is_removed=False,
        is_renamed=False,
        source_path=None,
        hunks_text="x" * hunk_size,
    )


def test_build_diff_context_stops_at_first_over_budget_file() -> None:
    """A file that busts the budget stops packing — it does not skip ahead."""
    files = [_stub_file("big.py", 500), _stub_file("small.py", 10)]
    file_list_len = len("Changed files:\nbig.py\nsmall.py\n\n")

    text, was_truncated = build_diff_context(files, max_chars=file_list_len + 200)

    assert was_truncated
    # Neither block fits/is packed: big.py busts the budget and small.py, though
    # it would fit in the leftover space, is lower priority and must not jump it.
    assert files[0].hunks_text not in text
    assert files[1].hunks_text not in text


def test_build_diff_context_never_exceeds_max_chars_on_huge_file_list() -> None:
    """The changed-file list is trimmed too, so max_chars is a real ceiling."""
    files = [_stub_file(f"src/module_{i:03d}.py", 10) for i in range(200)]

    text, was_truncated = build_diff_context(files, max_chars=300)

    assert was_truncated
    assert len(text) <= 300
    assert "more file(s)" in text


def test_exclude_paths_without_globs_keeps_everything(
    sample_files: list[ChangedFile],
) -> None:
    assert exclude_paths(sample_files, None) == sample_files
    assert exclude_paths(sample_files, []) == sample_files


def test_exclude_paths_filters_by_glob(sample_files: list[ChangedFile]) -> None:
    kept = exclude_paths(sample_files, ["*.png"])
    assert "image.png" not in {f.path for f in kept}
    assert len(kept) == len(sample_files) - 1


def test_exclude_paths_matches_across_directory_separators() -> None:
    """`*` crosses `/`, so `docs/*` excludes nested paths too."""
    files = [
        _stub_file("docs/guide/intro.md", 5),
        _stub_file("src/main.py", 5),
    ]
    kept = exclude_paths(files, ["docs/*"])
    assert [f.path for f in kept] == ["src/main.py"]


def test_build_diff_context_renders_rename_direction() -> None:
    """The header must show old -> new, not new -> old."""
    renamed = ChangedFile(
        path="new_name.py",
        is_added=False,
        is_removed=False,
        is_renamed=True,
        source_path="old_name.py",
        hunks_text="@@ -1 +1 @@\n",
    )

    text, _ = build_diff_context([renamed], max_chars=100_000)

    assert "File: old_name.py -> new_name.py (renamed)" in text
