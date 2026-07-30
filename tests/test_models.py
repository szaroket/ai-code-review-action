import json
from dataclasses import asdict

from pr_review_agent.models import (
    CriterionResult,
    CriterionScore,
    DiffSide,
    Finding,
    ReviewEvent,
    ReviewOutput,
    ReviewVerdict,
    Severity,
)


def test_finding_serializes_to_bare_wire_strings() -> None:
    """StrEnum is load-bearing: plain `enum.Enum` would emit "DiffSide.RIGHT".

    Phase 6's `write_json` and Phase 7's reviews POST both rely on this, and
    a downgrade to `enum.Enum` would break them with no type error.
    """
    finding = Finding(
        path="src/app.py",
        line=42,
        side=DiffSide.RIGHT,
        severity=Severity.BLOCKER,
        comment="boom",
        rule_reference=None,
    )

    payload = json.loads(json.dumps(asdict(finding)))

    assert payload["side"] == "RIGHT"
    assert payload["severity"] == "blocker"


def test_review_output_serializes_to_bare_wire_strings() -> None:
    output = ReviewOutput(
        pr_number=1,
        event=ReviewEvent.REQUEST_CHANGES,
        summary_body="summary",
        verdict=ReviewVerdict(
            criteria=[
                CriterionScore(
                    name="Typing", score=CriterionResult.NOT_APPLICABLE, rationale="n/a"
                )
            ],
            overall_verdict=ReviewEvent.REQUEST_CHANGES,
        ),
    )

    payload = json.loads(json.dumps(asdict(output)))

    assert payload["event"] == "REQUEST_CHANGES"
    assert payload["verdict"]["overall_verdict"] == "REQUEST_CHANGES"
    assert payload["verdict"]["criteria"][0]["score"] == "not_applicable"


def test_enums_reject_unknown_values() -> None:
    """Phase 5's @tool handler gets validation-with-message for free."""
    for enum_cls in (DiffSide, Severity, CriterionResult, ReviewEvent):
        try:
            enum_cls("bogus")
        except ValueError:
            continue
        raise AssertionError(f"{enum_cls.__name__} accepted an unknown value")


def test_verdict_and_output_share_one_event_enum() -> None:
    """A single ReviewEvent is what stops the two fields drifting apart."""
    verdict_field = ReviewVerdict.__annotations__["overall_verdict"]
    output_field = ReviewOutput.__annotations__["event"]
    assert verdict_field is ReviewEvent or verdict_field == "ReviewEvent"
    assert output_field is ReviewEvent or output_field == "ReviewEvent"
