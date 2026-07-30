"""Data models shared across the PR review pipeline."""

from dataclasses import dataclass, field
from enum import StrEnum


class DiffSide(StrEnum):
    """Which side of the diff a comment anchors to, in GitHub's casing."""

    LEFT = "LEFT"
    RIGHT = "RIGHT"


class Severity(StrEnum):
    """How serious a finding is; the wire value is lowercase."""

    BLOCKER = "blocker"
    WARNING = "warning"
    NIT = "nit"


class CriterionResult(StrEnum):
    """Outcome of scoring the diff against one review criterion."""

    PASS = "pass"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"


class ReviewEvent(StrEnum):
    """GitHub reviews-API `event` value; shared by the verdict and the output.

    Deliberately one enum rather than two: it is what structurally prevents
    `ReviewVerdict.overall_verdict` and `ReviewOutput.event` from drifting.
    """

    APPROVE = "APPROVE"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    COMMENT = "COMMENT"


@dataclass(frozen=True)
class Finding:
    """One inline review comment, shaped to the reviews-API comment schema."""

    path: str
    line: int
    side: DiffSide
    severity: Severity
    comment: str
    rule_reference: str | None


@dataclass(frozen=True)
class Criterion:
    """One review criterion loaded from the consumer's criteria file."""

    name: str
    description: str


@dataclass(frozen=True)
class CriterionScore:
    """The agent's scoring of a single criterion, with its reasoning."""

    name: str
    score: CriterionResult
    rationale: str


@dataclass(frozen=True)
class ReviewVerdict:
    """Per-criterion scores plus the overall review event they add up to."""

    criteria: list[CriterionScore]
    overall_verdict: ReviewEvent


@dataclass
class ReviewOutput:
    """The complete review: summary, inline comments, and optional verdict.

    `verdict` is None on the exit-5 path, where Phase 8 writes the output
    before the verdict check so the agent's findings survive.
    """

    pr_number: int
    event: ReviewEvent
    summary_body: str
    comments: list[Finding] = field(default_factory=list)
    verdict: ReviewVerdict | None = None
