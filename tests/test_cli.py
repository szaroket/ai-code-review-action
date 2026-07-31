from pr_review_agent.cli import _parse_comma_list, filter_in_scope_files
from pr_review_agent.diff_parser import ChangedFile


def _changed_file(path: str) -> ChangedFile:
    return ChangedFile(
        path=path,
        is_added=False,
        is_removed=False,
        is_renamed=False,
        source_path=None,
        hunks_text="",
    )


def test_filter_in_scope_files_with_no_scope_dirs_keeps_everything() -> None:
    files = [_changed_file("frontend/a.py"), _changed_file("backend/b.py")]
    assert filter_in_scope_files(files, []) == files


def test_filter_in_scope_files_keeps_only_matching_prefixes() -> None:
    files = [
        _changed_file("frontend/a.py"),
        _changed_file("backend/b.py"),
        _changed_file("docs/readme.md"),
    ]
    in_scope = filter_in_scope_files(files, ["frontend", "backend"])
    assert [f.path for f in in_scope] == ["frontend/a.py", "backend/b.py"]


def test_filter_in_scope_files_empty_when_nothing_matches() -> None:
    files = [_changed_file("docs/readme.md")]
    assert filter_in_scope_files(files, ["frontend"]) == []


def test_filter_in_scope_files_preserves_original_order() -> None:
    files = [_changed_file("backend/b.py"), _changed_file("frontend/a.py")]
    in_scope = filter_in_scope_files(files, ["frontend", "backend"])
    assert [f.path for f in in_scope] == ["backend/b.py", "frontend/a.py"]


def test_parse_comma_list_none_is_empty() -> None:
    assert _parse_comma_list(None) == []


def test_parse_comma_list_splits_each_occurrence_on_comma() -> None:
    assert _parse_comma_list(["frontend,backend", " docs "]) == [
        "frontend",
        "backend",
        "docs",
    ]


def test_parse_comma_list_drops_empty_entries() -> None:
    assert _parse_comma_list(["frontend,,backend,"]) == ["frontend", "backend"]
