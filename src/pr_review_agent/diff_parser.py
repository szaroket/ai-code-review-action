"""Unified-diff parsing: turn a raw unified patch into changed-lines models."""

import logging
from dataclasses import dataclass, field
from fnmatch import fnmatch

# Imported from their defining modules rather than the `unidiff` package root:
# the root re-exports these without declaring them public, which pyright flags
# as reportPrivateImportUsage.
from unidiff.constants import RE_PATCH_FILE_PREFIX
from unidiff.patch import Hunk, PatchedFile, PatchSet

logger = logging.getLogger(__name__)

_BINARY_PLACEHOLDER = "[binary file, diff not shown]"
_FILE_LIST_HEADER = "Changed files:\n"
_FILE_LIST_TAIL = "... and {count} more file(s)\n"


@dataclass(frozen=True)
class ChangedFile:
    """One file touched by a diff, with its hunks and changed line numbers.

    `hunks_text` renders each line pre-annotated with its exact old/new line
    number (see `_render_hunk`) so a reviewing model can read a line number
    directly instead of counting from the `@@ -a,b +c,d @@` header.
    """

    path: str
    is_added: bool
    is_removed: bool
    is_renamed: bool
    source_path: str | None
    hunks_text: str
    added_line_numbers: list[int] = field(default_factory=list)
    removed_line_numbers: list[int] = field(default_factory=list)


def _strip_vcs_prefix(filepath: str) -> str:
    """Strip the leading `a/` or `b/` a unified diff puts on each path.

    Args:
        filepath: A path as it appears in a diff header.

    Returns:
        str: The path without its VCS prefix, unchanged if it had none.
    """
    if RE_PATCH_FILE_PREFIX.match(filepath):
        return filepath[2:]
    return filepath


def _render_hunk(hunk: Hunk) -> str:
    """Render one hunk with its exact old/new line number on every line.

    Plain `str(hunk)` (the unidiff default) reproduces the raw unified-diff
    text, which carries line numbers only in the `@@ -a,b +c,d @@` header —
    a reviewing model has to count from there to know which line a change is
    on, and an off-by-one miscount there is exactly what caused a real
    GitHub 422 ("Line could not be resolved"): the model submitted a finding
    for a line that didn't exist in the diff at all. Annotating every line
    with its resolved number removes the counting step entirely.

    Args:
        hunk: One hunk from a `unidiff` patched file.

    Returns:
        str: The `@@ ... @@` header, then one `old new marker content` line
        per line in the hunk. `.` marks the side that has no line there
        (e.g. an added line has no old-file line number).
    """
    header = (
        f"@@ -{hunk.source_start},{hunk.source_length} "
        f"+{hunk.target_start},{hunk.target_length} @@"
    )
    lines = [header]
    for line in hunk:
        old = str(line.source_line_no) if line.source_line_no is not None else "."
        new = str(line.target_line_no) if line.target_line_no is not None else "."
        lines.append(f"{old:>6} {new:>6} {line.line_type}{line.value.rstrip(chr(10))}")
    return "\n".join(lines) + "\n"


def _changed_file_from_patched_file(patched_file: PatchedFile) -> ChangedFile:
    """Convert one `unidiff` patched file into a `ChangedFile`.

    Binary files short-circuit to a placeholder with empty line lists, since
    there are no text hunks to attribute line numbers to.

    Args:
        patched_file: A single file's patch, as parsed by `unidiff`.

    Returns:
        ChangedFile: The parsed representation of that file's changes.
    """
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
        hunk_texts.append(_render_hunk(hunk))
        for line in hunk:
            # `unidiff` types both line numbers as Optional[int]: the target
            # number is None on removed lines and vice versa. Narrow rather
            # than cast — a None slipping into these lists would anchor a
            # review comment at the wrong place.
            if line.is_added and (target_line_no := line.target_line_no) is not None:
                added_line_numbers.append(target_line_no)
            elif (
                line.is_removed and (source_line_no := line.source_line_no) is not None
            ):
                removed_line_numbers.append(source_line_no)

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
    """Parse a unified diff (as returned by the GitHub diff media type).

    Binary file changes (`GIT binary patch` blocks) are represented with empty
    line-number lists and a placeholder `hunks_text` rather than raising.

    Args:
        diff_text: Raw unified diff text, e.g. from `github_diff.get_pr_diff`.

    Returns:
        list[ChangedFile]: One entry per file touched by the diff, in the
        order they appear in `diff_text`.

    Raises:
        unidiff.errors.UnidiffParseError: If `diff_text` is not a well-formed
        unified diff.
    """
    patch_set = PatchSet(diff_text)
    changed_files = [
        _changed_file_from_patched_file(patched_file) for patched_file in patch_set
    ]
    logger.info("Parsed %d changed file(s) from diff", len(changed_files))
    return changed_files


def exclude_paths(
    files: list[ChangedFile], exclude_globs: list[str] | None
) -> list[ChangedFile]:
    """Drop changed files whose path matches any of `exclude_globs`.

    Applied after parsing because the GitHub REST API has no server-side
    exclude parameter. Matching uses `fnmatch`, so `*` also crosses `/` —
    `*.lock` and `docs/*` both behave as a reviewer would expect, but these
    are not gitignore-grade patterns.

    Args:
        files: Parsed changed files.
        exclude_globs: Glob patterns to exclude; None or empty keeps all files.

    Returns:
        list[ChangedFile]: The files whose paths matched no pattern, in the
        original order.
    """
    if not exclude_globs:
        return files

    kept = [
        changed_file
        for changed_file in files
        if not any(fnmatch(changed_file.path, glob) for glob in exclude_globs)
    ]
    if len(kept) < len(files):
        logger.info(
            "Excluded %d of %d changed file(s) matching %s",
            len(files) - len(kept),
            len(files),
            exclude_globs,
        )
    return kept


def _file_header(changed_file: ChangedFile) -> str:
    """Render the one-line header that labels a file's block in the prompt.

    The rename case renders `source -> path`; getting that direction wrong
    would tell the model a rename went the opposite way.

    Args:
        changed_file: The file to label.

    Returns:
        str: A header line such as `File: old.py -> new.py (renamed)`.
    """
    if changed_file.is_renamed:
        return f"File: {changed_file.source_path} -> {changed_file.path} (renamed)"
    if changed_file.is_added:
        return f"File: {changed_file.path} (added)"
    if changed_file.is_removed:
        return f"File: {changed_file.path} (removed)"
    return f"File: {changed_file.path}"


def _file_block(changed_file: ChangedFile) -> str:
    """Render a file's full prompt block: its header plus its hunks.

    Args:
        changed_file: The file to render.

    Returns:
        str: The header line followed by the file's hunk text.
    """
    return f"{_file_header(changed_file)}\n{changed_file.hunks_text}\n"


def _build_file_list(files: list[ChangedFile], max_chars: int) -> tuple[str, bool]:
    """Render the changed-file list, trimming paths if it alone busts the budget.

    Args:
        files: Changed files, in priority order (highest first).
        max_chars: Maximum length of the returned text.

    Returns:
        tuple[str, bool]: The rendered list (always ending in a blank line),
        and whether any paths had to be omitted.
    """
    full_text = _FILE_LIST_HEADER + "".join(f"{f.path}\n" for f in files) + "\n"
    if len(full_text) <= max_chars:
        return full_text, False

    kept_paths: list[str] = []
    running_len = len(_FILE_LIST_HEADER) + len("\n")
    for index, changed_file in enumerate(files):
        tail = _FILE_LIST_TAIL.format(count=len(files) - index)
        if running_len + len(changed_file.path) + 1 + len(tail) > max_chars:
            break
        kept_paths.append(changed_file.path)
        running_len += len(changed_file.path) + 1

    logger.warning(
        "Changed-file list truncated: listed %d of %d path(s) under max_chars=%d",
        len(kept_paths),
        len(files),
        max_chars,
    )
    tail = _FILE_LIST_TAIL.format(count=len(files) - len(kept_paths))
    return (
        _FILE_LIST_HEADER + "".join(f"{p}\n" for p in kept_paths) + tail + "\n",
        True,
    )


def build_diff_context(files: list[ChangedFile], max_chars: int) -> tuple[str, bool]:
    """Render `files` into prompt-ready text, truncating whole files if needed.

    When the rendered hunks would exceed `max_chars`, whole files are dropped
    (in the given, assumed-priority order) starting from the first file that
    would push the total over budget — a kept file's hunks are never truncated
    mid-way, and no lower-priority file is packed into the leftover space. The
    changed-file list is rendered in full whenever it fits; if it alone busts
    the budget it is trimmed to an "... and N more files" tail, so the return
    value stays bounded by `max_chars`.

    Args:
        files: Changed files to render, in priority order (highest first).
        max_chars: Maximum length of the returned text.

    Returns:
        tuple[str, bool]: The rendered text, and whether any file list entries
        or file hunks had to be dropped to fit `max_chars`.
    """
    file_list_text, was_truncated = _build_file_list(files, max_chars)

    kept_blocks: list[str] = []
    running_len = len(file_list_text)
    for changed_file in files:
        block = _file_block(changed_file)
        if running_len + len(block) > max_chars:
            was_truncated = True
            logger.debug(
                "Dropping %s and all lower-priority files from diff context "
                "to stay under max_chars=%d",
                changed_file.path,
                max_chars,
            )
            break
        kept_blocks.append(block)
        running_len += len(block)

    if len(kept_blocks) < len(files):
        logger.warning(
            "Diff context truncated: kept %d of %d file(s) under max_chars=%d",
            len(kept_blocks),
            len(files),
            max_chars,
        )

    return file_list_text + "".join(kept_blocks), was_truncated
