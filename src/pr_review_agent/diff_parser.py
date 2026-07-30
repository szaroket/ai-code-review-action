"""Unified-diff parsing: turn `gh pr diff --patch` output into changed-lines models."""

import logging
from dataclasses import dataclass, field

import unidiff
from unidiff.patch import PatchedFile

logger = logging.getLogger(__name__)

_BINARY_PLACEHOLDER = "[binary file, diff not shown]"


@dataclass(frozen=True)
class ChangedFile:
    path: str
    is_added: bool
    is_removed: bool
    is_renamed: bool
    source_path: str | None
    hunks_text: str
    added_line_numbers: list[int] = field(default_factory=list)
    removed_line_numbers: list[int] = field(default_factory=list)


def _strip_vcs_prefix(filepath: str) -> str:
    if unidiff.patch.RE_PATCH_FILE_PREFIX.match(filepath):
        return filepath[2:]
    return filepath


def _changed_file_from_patched_file(patched_file: PatchedFile) -> ChangedFile:
    source_path = (
        _strip_vcs_prefix(patched_file.source_file) if patched_file.is_rename else None
    )

    if patched_file.is_binary_file:
        logger.debug("Binary file diff detected: %s", patched_file.path)
        return ChangedFile(
            path=patched_file.path,
            is_added=patched_file.is_added_file,
            is_removed=patched_file.is_removed_file,
            is_renamed=patched_file.is_rename,
            source_path=source_path,
            hunks_text=_BINARY_PLACEHOLDER,
        )

    added_line_numbers: list[int] = []
    removed_line_numbers: list[int] = []
    hunk_texts: list[str] = []
    for hunk in patched_file:
        hunk_texts.append(str(hunk))
        for line in hunk:
            if line.is_added:
                added_line_numbers.append(line.target_line_no)
            elif line.is_removed:
                removed_line_numbers.append(line.source_line_no)

    return ChangedFile(
        path=patched_file.path,
        is_added=patched_file.is_added_file,
        is_removed=patched_file.is_removed_file,
        is_renamed=patched_file.is_rename,
        source_path=source_path,
        hunks_text="".join(hunk_texts),
        added_line_numbers=added_line_numbers,
        removed_line_numbers=removed_line_numbers,
    )


def parse_diff(diff_text: str) -> list[ChangedFile]:
    """Parse a unified diff (as produced by `gh pr diff --patch`) into `ChangedFile`s.

    Binary file changes (`GIT binary patch` blocks) are represented with empty
    line-number lists and a placeholder `hunks_text` rather than raising.

    Args:
        diff_text: Raw unified diff text, e.g. the output of `gh pr diff --patch`.

    Returns:
        list[ChangedFile]: One entry per file touched by the diff, in the
        order they appear in `diff_text`.

    Raises:
        unidiff.errors.UnidiffParseError: If `diff_text` is not a well-formed
        unified diff.
    """
    patch_set = unidiff.PatchSet(diff_text)
    changed_files = [
        _changed_file_from_patched_file(patched_file) for patched_file in patch_set
    ]
    logger.info("Parsed %d changed file(s) from diff", len(changed_files))
    return changed_files


def _file_header(changed_file: ChangedFile) -> str:
    if changed_file.is_renamed:
        return f"File: {changed_file.source_path} -> {changed_file.path} (renamed)"
    if changed_file.is_added:
        return f"File: {changed_file.path} (added)"
    if changed_file.is_removed:
        return f"File: {changed_file.path} (removed)"
    return f"File: {changed_file.path}"


def _file_block(changed_file: ChangedFile) -> str:
    return f"{_file_header(changed_file)}\n{changed_file.hunks_text}\n"


def build_diff_context(files: list[ChangedFile], max_chars: int) -> tuple[str, bool]:
    """Render `files` into prompt-ready text, truncating whole files if needed.

    The changed-file list is always rendered in full, regardless of budget.
    When the rendered hunks would exceed `max_chars`, whole files are dropped
    (in the given, assumed-priority order) starting from the first file that
    would push the total over budget — a kept file's hunks are never
    truncated mid-way.

    Args:
        files: Changed files to render, in priority order (highest first).
        max_chars: Maximum length of the returned text.

    Returns:
        tuple[str, bool]: The rendered text, and whether one or more files'
        hunks had to be dropped to fit `max_chars`.
    """
    file_list_text = "Changed files:\n" + "\n".join(f.path for f in files) + "\n\n"

    kept_blocks: list[str] = []
    running_len = len(file_list_text)
    was_truncated = False
    for changed_file in files:
        block = _file_block(changed_file)
        if running_len + len(block) > max_chars:
            was_truncated = True
            logger.debug(
                "Dropping %s from diff context to stay under max_chars=%d",
                changed_file.path,
                max_chars,
            )
            continue
        kept_blocks.append(block)
        running_len += len(block)

    if was_truncated:
        logger.warning(
            "Diff context truncated: kept %d of %d file(s) under max_chars=%d",
            len(kept_blocks),
            len(files),
            max_chars,
        )

    return file_list_text + "".join(kept_blocks), was_truncated
