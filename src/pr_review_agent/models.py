"""Data models shared across the PR review pipeline."""

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    side: Literal["LEFT", "RIGHT"]
    severity: Literal["blocker", "warning", "nit"]
    comment: str
    rule_reference: str | None


@dataclass(frozen=True)
class Criterion:
    name: str
    description: str


@dataclass(frozen=True)
class CriterionScore:
    name: str
    score: Literal["pass", "fail", "not_applicable"]
    rationale: str


@dataclass(frozen=True)
class ReviewVerdict:
    criteria: list[CriterionScore]
    overall_verdict: Literal["APPROVE", "REQUEST_CHANGES", "COMMENT"]


@dataclass
class ReviewOutput:
    pr_number: int
    event: Literal["COMMENT", "REQUEST_CHANGES", "APPROVE"]
    summary_body: str
    comments: list[Finding] = field(default_factory=list)
    verdict: ReviewVerdict | None = None
