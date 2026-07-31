import json
from pathlib import Path

from pr_review_agent.models import (
    CriterionResult,
    CriterionScore,
    DiffSide,
    Finding,
    ReviewEvent,
    ReviewVerdict,
    Severity,
)
from pr_review_agent.output import (
    build_review_output,
    build_review_payload,
    build_summary,
    cap_findings,
    deduplicate_findings,
    write_json,
    write_markdown,
)


def _finding(path: str = "a.py", line: int = 1, comment: str = "issue") -> Finding:
    return Finding(
        path=path,
        line=line,
        side=DiffSide.RIGHT,
        severity=Severity.WARNING,
        comment=comment,
        rule_reference=None,
    )


def _verdict(overall: ReviewEvent = ReviewEvent.APPROVE) -> ReviewVerdict:
    return ReviewVerdict(
        criteria=[
            CriterionScore(
                name="Naming", score=CriterionResult.PASS, rationale="Consistent."
            ),
            CriterionScore(
                name="Tests", score=CriterionResult.FAIL, rationale="Missing."
            ),
        ],
        overall_verdict=overall,
    )


def test_deduplicate_findings_drops_exact_repeats_keeping_first() -> None:
    findings = [
        _finding("a.py", 1, "dup"),
        _finding("a.py", 1, "dup"),
        _finding("a.py", 2, "different line"),
        _finding("b.py", 1, "dup"),
    ]
    deduped = deduplicate_findings(findings)
    assert deduped == [findings[0], findings[2], findings[3]]


def test_cap_findings_under_limit_is_a_no_op() -> None:
    findings = [_finding(line=i) for i in range(3)]
    kept, was_capped = cap_findings(findings, max_findings=5)
    assert kept == findings
    assert not was_capped


def test_cap_findings_over_limit_truncates() -> None:
    findings = [_finding(line=i) for i in range(5)]
    kept, was_capped = cap_findings(findings, max_findings=3)
    assert kept == findings[:3]
    assert was_capped


def test_build_summary_renders_criteria_before_findings_breakdown() -> None:
    summary = build_summary([_finding()], _verdict())
    criteria_index = summary.index("Naming")
    breakdown_index = summary.index("Findings:")
    assert criteria_index < breakdown_index
    assert "✅ Naming: pass" in summary
    assert "❌ Tests: fail" in summary
    assert "1 warning (1 total)" in summary


def test_build_summary_with_no_verdict_renders_incomplete_banner() -> None:
    summary = build_summary([_finding()], None)
    assert "=== INCOMPLETE — NO VERDICT PRODUCED ===" in summary
    assert "Naming" not in summary


def test_build_summary_with_no_findings_says_so() -> None:
    summary = build_summary([], _verdict())
    assert "No findings." in summary


def test_build_review_output_event_passes_through_from_verdict() -> None:
    output = build_review_output(7, [], _verdict(ReviewEvent.REQUEST_CHANGES))
    assert output.event == ReviewEvent.REQUEST_CHANGES
    assert output.verdict is not None


def test_build_review_output_falls_back_to_comment_without_verdict() -> None:
    output = build_review_output(7, [_finding()], None)
    assert output.event == ReviewEvent.COMMENT
    assert output.verdict is None
    assert output.comments == [_finding()]


def test_build_review_payload_matches_reviews_api_comment_shape() -> None:
    finding = Finding(
        path="a.py",
        line=5,
        side=DiffSide.LEFT,
        severity=Severity.BLOCKER,
        comment="Broken.",
        rule_reference="Rule 1",
    )
    output = build_review_output(1, [finding], _verdict())
    payload = build_review_payload(output)

    assert set(payload) == {"event", "body", "comments"}
    assert payload["event"] == "APPROVE"
    [comment] = payload["comments"]
    assert set(comment) == {"path", "line", "side", "body"}
    assert comment["path"] == "a.py"
    assert comment["line"] == 5
    assert comment["side"] == "LEFT"
    assert "Broken." in comment["body"]
    assert "Rule 1" in comment["body"]


def test_write_json_includes_criteria_alongside_the_post_payload_shape(
    tmp_path: Path,
) -> None:
    output = build_review_output(3, [_finding()], _verdict())
    path = write_json(output, tmp_path, was_capped=True)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["event"] == "APPROVE"
    assert data["was_capped"] is True
    assert len(data["criteria"]) == 2
    assert data["criteria"][0] == {
        "name": "Naming",
        "score": "pass",
        "rationale": "Consistent.",
    }


def test_write_markdown_lists_findings_grouped_by_file(tmp_path: Path) -> None:
    findings = [_finding("a.py", 1, "first"), _finding("b.py", 2, "second")]
    output = build_review_output(3, findings, _verdict())
    path = write_markdown(output, tmp_path, was_capped=False)

    text = path.read_text(encoding="utf-8")
    assert "### a.py" in text
    assert "### b.py" in text
    assert "first" in text
    assert "second" in text
