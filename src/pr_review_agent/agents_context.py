"""Loaders for the rules/lessons/criteria files, and the system prompt they build.

Content from `rules-file` and `lessons-file` is injected into the prompt
close to verbatim, under generic headings — this module never distills a
specific repository's rules into named categories, since that structure
doesn't generalize across arbitrary consumer repos (see plan.md's "What We're
NOT Doing").
"""

import logging
import re
from pathlib import Path

from pr_review_agent.models import Criterion

logger = logging.getLogger(__name__)

_CRITERION_HEADING = re.compile(r"^##[ \t]+(?P<name>.+?)[ \t]*$", re.MULTILINE)

_ROLE_STATEMENT = (
    "You are an automated code reviewer for a GitHub pull request. Review "
    "ONLY the diff you are given below. Never attempt to fix code, and never "
    "modify, create, or delete any file — your output is review feedback, "
    "not a patch."
)

_TOOL_USAGE_GUIDANCE = (
    "You have no Read, Grep, or Glob tools — there is no way to browse the "
    "repository. Review the diff below exactly as given; judge each change "
    "from the diff context alone rather than waiting to confirm it against "
    "the wider codebase."
)

_SUBMIT_FINDING_CONTRACT = (
    "For every issue you find, call `submit_finding` once. Do not batch "
    "multiple issues into one call. Every diff line below is pre-annotated "
    "as `<old_line> <new_line> <marker><content>` — a `.` means that side has "
    "no line there (e.g. an added line has no old_line). Set `line` to the "
    "exact number shown in that annotation: the new_line column when "
    '`side="RIGHT"`, the old_line column when `side="LEFT"`. Never count '
    "lines yourself — a miscounted line number makes GitHub reject the "
    "comment outright. When the issue relates to something stated in "
    '"Repository Rules" or "Additional Lessons / Pitfalls", cite it in '
    "`rule_reference`; otherwise leave `rule_reference` unset."
)


class InvalidReviewCriteriaError(ValueError):
    """Raised when a criteria file contains no `##` sections at all."""


def _read_required_file(path: Path, what: str) -> str:
    """Read a file that must exist, with a clearer error if it doesn't.

    Args:
        path: Path to the file.
        what: Short human-readable label for the file, used in the error
            message, e.g. "rules file".

    Returns:
        str: The file's contents.

    Raises:
        FileNotFoundError: If `path` does not exist.
    """
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"{what.capitalize()} not found: {path}") from exc


def load_rules_file(path: Path) -> str | None:
    """Load the consumer's optional repository-rules file.

    A soft dependency: not every repo has an `AGENTS.md` (or whatever the
    consumer points `--rules-file` at), so a missing file is not an error —
    the review proceeds without a "Repository Rules" section, scored only
    against the loaded criteria.

    Args:
        path: Path to the rules file (e.g. an `AGENTS.md`).

    Returns:
        str | None: The file's contents, or None if `path` doesn't exist.
    """
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("Rules file not found, continuing without it: %s", path)
        return None


def load_lessons_file(path: Path | None) -> str | None:
    """Load the consumer's optional lessons/pitfalls file.

    A soft dependency: unlike the rules file, a missing or unset lessons file
    is not an error — the review simply proceeds without that section.

    Args:
        path: Path to the lessons file, or None if the consumer didn't supply
            one.

    Returns:
        str | None: The file's contents, or None if `path` is None or the
        file doesn't exist.
    """
    if path is None:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("Lessons file not found, continuing without it: %s", path)
        return None


def load_review_criteria(path: Path) -> list[Criterion]:
    """Load and parse the consumer's review criteria.

    Parses each `##` heading in `path` as one criterion: the heading text is
    the name, and the text up to the next `##` heading (or end of file) is
    the description. The number of criteria is each repo's own call — a
    small, focused stack has different review axes than a large polyglot
    one — so nothing here enforces a fixed count; only requiring at least one
    still verifies the discovery session described in plan.md's
    "Prerequisite" actually happened, for whichever repo is consuming the
    action.

    Args:
        path: Path to the criteria markdown file.

    Returns:
        list[Criterion]: The criteria, in file order.

    Raises:
        FileNotFoundError: If `path` does not exist.
        InvalidReviewCriteriaError: If the file contains no `##` headings.
    """
    return parse_review_criteria(_read_required_file(path, "criteria file"), str(path))


def parse_review_criteria(text: str, source: str) -> list[Criterion]:
    """Parse review criteria out of already-loaded markdown text.

    Split from `load_review_criteria` because the criteria file is not always
    read from disk: `cli.py` fetches it from the pull request's *base* ref, so
    that a PR cannot rewrite the axes it is about to be scored against (see
    `github_diff.get_file_at_ref`). Only the reading differs; the parsing rule
    is identical either way.

    Args:
        text: The criteria file's markdown content.
        source: Where `text` came from, used only in the error message —
            a path, or something like `"AGENTS.md at base ref main"`.

    Returns:
        list[Criterion]: The criteria, in file order.

    Raises:
        InvalidReviewCriteriaError: If `text` contains no `##` headings.
    """
    headings = list(_CRITERION_HEADING.finditer(text))
    criteria = []
    for index, heading in enumerate(headings):
        start = heading.end()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        criteria.append(
            Criterion(
                name=heading.group("name").strip(),
                description=text[start:end].strip(),
            )
        )

    if not criteria:
        raise InvalidReviewCriteriaError(
            f"{source} must contain at least one `##` criterion heading, found none."
        )
    return criteria


def _criteria_section(criteria: list[Criterion]) -> str:
    """Render the "Review Criteria" prompt section.

    Args:
        criteria: The loaded review criteria, in the order they should be
            scored.

    Returns:
        str: The rendered section, one `###` subsection per criterion.
    """
    lines = [
        "## Review Criteria",
        "",
        (
            "These are the axes `submit_review_verdict` must score, one "
            "`CriterionScore` per criterion below, matched by exact name:"
        ),
        "",
    ]
    for criterion in criteria:
        lines.append(f"### {criterion.name}")
        lines.append(criterion.description)
        lines.append("")
    return "\n".join(lines).rstrip()


def _submit_verdict_contract(criteria: list[Criterion]) -> str:
    """Render the `submit_review_verdict` tool-usage contract.

    Args:
        criteria: The loaded review criteria, whose exact names the verdict
            must match.

    Returns:
        str: The contract text, naming every criterion the verdict must
        cover.
    """
    names = ", ".join(f'"{criterion.name}"' for criterion in criteria)
    return (
        "After all `submit_finding` calls are done, call "
        "`submit_review_verdict` exactly once. It must include one "
        "`CriterionScore` per review criterion listed above, using its exact "
        f"name ({names}), plus one `overall_verdict` for the review as a "
        "whole."
    )


def build_system_prompt(
    rules_content: str | None,
    lessons_content: str | None,
    criteria: list[Criterion],
) -> str:
    """Compose the agent's system prompt from the consumer's loaded inputs.

    Section order: role statement, review criteria, optional repository
    rules, optional additional lessons, tool-usage guidance, then the
    `submit_finding` and `submit_review_verdict` tool contracts.

    Args:
        rules_content: The consumer's rules-file content, injected verbatim,
            or None to omit the section entirely (not every repo has one).
        lessons_content: The consumer's lessons-file content, injected
            verbatim, or None to omit the section entirely.
        criteria: The loaded review criteria (however many the consumer's
            criteria file defines).

    Returns:
        str: The complete system prompt.
    """
    sections = [_ROLE_STATEMENT, _criteria_section(criteria)]
    if rules_content:
        sections.append(f"## Repository Rules\n\n{rules_content}")
    if lessons_content:
        sections.append(f"## Additional Lessons / Pitfalls\n\n{lessons_content}")
    sections.append(_TOOL_USAGE_GUIDANCE)
    sections.append(_SUBMIT_FINDING_CONTRACT)
    sections.append(_submit_verdict_contract(criteria))
    return "\n\n".join(sections)
