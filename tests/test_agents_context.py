from pathlib import Path

import pytest

from pr_review_agent.agents_context import (
    InvalidReviewCriteriaError,
    build_system_prompt,
    load_lessons_file,
    load_review_criteria,
    load_rules_file,
    parse_review_criteria,
)
from pr_review_agent.models import Criterion

_FIXTURE = Path(__file__).parent / "fixtures" / "smoke-criteria.md"


def test_parses_one_criterion_per_h2_heading() -> None:
    criteria = parse_review_criteria(
        "## Correctness\nDoes it work?\n\n## Clarity\nIs it readable?\n", "inline"
    )
    assert [c.name for c in criteria] == ["Correctness", "Clarity"]
    assert criteria[0].description == "Does it work?"


def test_criteria_keep_file_order() -> None:
    criteria = load_review_criteria(_FIXTURE)
    assert [c.name for c in criteria] == [
        "Correctness",
        "Clarity",
        "Test Coverage",
        "Scope Discipline",
        "Documentation",
    ]


def test_h1_and_h3_headings_are_not_criteria() -> None:
    """Only `##` delimits a criterion; the fixture's `# Review Criteria` is prose."""
    criteria = parse_review_criteria(
        "# Review Criteria\n\n## Correctness\nBody.\n\n### Sub\nMore body.\n", "inline"
    )
    assert len(criteria) == 1
    assert "### Sub" in criteria[0].description


def test_description_runs_to_the_next_heading_or_eof() -> None:
    criteria = parse_review_criteria("## A\nfirst\n\n## B\nlast\n", "inline")
    assert criteria[0].description == "first"
    assert criteria[1].description == "last"


@pytest.mark.parametrize(
    "text",
    ["", "# Only an h1\n\nSome prose.\n", "Just prose, no headings at all.\n"],
)
def test_criteria_file_without_h2_headings_is_rejected(text: str) -> None:
    with pytest.raises(InvalidReviewCriteriaError, match="at least one"):
        parse_review_criteria(text, "criteria.md")


def test_criteria_error_names_its_source() -> None:
    """The source label is what tells a consumer which input file to fix."""
    with pytest.raises(InvalidReviewCriteriaError, match="AGENTS.md at base ref main"):
        parse_review_criteria("prose", "AGENTS.md at base ref main")


def test_missing_criteria_file_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Criteria file not found"):
        load_review_criteria(tmp_path / "absent.md")


def test_missing_rules_file_is_tolerated(tmp_path: Path) -> None:
    """The rules file is a soft dependency — not every repo has an AGENTS.md."""
    assert load_rules_file(tmp_path / "absent.md") is None


def test_missing_lessons_file_is_tolerated(tmp_path: Path) -> None:
    assert load_lessons_file(tmp_path / "absent.md") is None


def test_unset_lessons_file_is_tolerated() -> None:
    assert load_lessons_file(None) is None


def _criteria() -> list[Criterion]:
    return [Criterion(name="Correctness", description="Does it work?")]


def test_system_prompt_omits_optional_sections_when_absent() -> None:
    prompt = build_system_prompt(None, None, _criteria())
    assert "## Repository Rules" not in prompt
    assert "## Additional Lessons / Pitfalls" not in prompt
    assert "## Review Criteria" in prompt


def test_system_prompt_orders_sections_as_documented() -> None:
    prompt = build_system_prompt("RULES_BODY", "LESSONS_BODY", _criteria())
    order = [
        prompt.index("automated code reviewer"),
        prompt.index("## Review Criteria"),
        prompt.index("## Repository Rules"),
        prompt.index("## Additional Lessons / Pitfalls"),
        prompt.index("You may use the Read, Grep, and Glob tools"),
        prompt.index("call `submit_finding` once"),
        prompt.index("After all `submit_finding` calls are done"),
    ]
    assert order == sorted(order)


def test_system_prompt_injects_rules_verbatim() -> None:
    """Rules are forwarded unedited — this module never distills them."""
    body = "- Use snake_case.\n- Never call `eval`.\n"
    assert body in build_system_prompt(body, None, _criteria())


def test_verdict_contract_names_every_criterion() -> None:
    criteria = load_review_criteria(_FIXTURE)
    prompt = build_system_prompt(None, None, criteria)
    for criterion in criteria:
        assert f'"{criterion.name}"' in prompt
