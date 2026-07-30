"""Data models shared across the PR review pipeline."""

from dataclasses import dataclass, field
from enum import StrEnum


class DiffSide(StrEnum):
    LEFT = "LEFT"
    RIGHT = "RIGHT"


class Severity(StrEnum):
    BLOCKER = "blocker"
    WARNING = "warning"
    NIT = "nit"


class CriterionResult(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"


class ReviewEvent(StrEnum):
    APPROVE = "APPROVE"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    COMMENT = "COMMENT"


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    side: DiffSide
    severity: Severity
    comment: str
    rule_reference: str | None


@dataclass(frozen=True)
class Criterion:
    name: str
    description: str


@dataclass(frozen=True)
class CriterionScore:
    name: str
    score: CriterionResult
    rationale: str


@dataclass(frozen=True)
class ReviewVerdict:
    criteria: list[CriterionScore]
    overall_verdict: ReviewEvent


@dataclass
class ReviewOutput:
    pr_number: int
    event: ReviewEvent
    summary_body: str
    comments: list[Finding] = field(default_factory=list)
    verdict: ReviewVerdict | None = None
