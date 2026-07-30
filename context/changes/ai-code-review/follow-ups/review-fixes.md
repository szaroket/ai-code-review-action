# Follow-ups from the Phase 0-3 implementation review

Source: `context/changes/ai-code-review/reviews/impl-review-phases-0-3.md`
Triaged: 2026-07-30

## Open

### Pre-commit corrupts diff fixtures (noted in the review, not a finding)

`.pre-commit-config.yaml`'s `trailing-whitespace` hook runs on
`tests/fixtures/sample.diff`. In a real unified diff an empty context line is
`" "` (single space); the hook has already stripped one to `""`. `unidiff`
tolerates it and tests pass, but the fixture is no longer byte-faithful to
real patch output — which is its stated purpose — and any future fixture with
blank context lines will be silently corrupted on commit.

Fix: add `exclude: ^tests/fixtures/` to the whitespace/EOF hooks.

## Closed during triage

F1, F2, F3, F4, F6, F7, F8, F9, F10 — see the review file for per-finding
decisions. F6 and F7 were resolved at the root by the `gh` CLI → `githubkit`
transport switch rather than by the patches originally proposed.

F5 (partial, no tests on the redaction filter) — closed 2026-07-30 by user
decision: no dedicated `tests/test_logging_config.py`. `tests/test_models.py`
(the StrEnum-pinning tests from Phase 1) was removed on the same decision.
Plan Progress 3.3 and 1.2 updated to reflect the skip.
