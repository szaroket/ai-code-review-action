# Follow-ups from the Phase 0-3 implementation review

Source: `context/changes/ai-code-review/reviews/impl-review-phases-0-3.md`
Triaged: 2026-07-30

## Open

### F5 (partial) — no tests on the redaction filter

Skipped during triage. Tracked as unchecked Progress item **3.3** in `plan.md`.

`_RedactionFilter` / `redact()` is the repo's only security control and has
zero test coverage — a regex typo silently disables it. The F1 fix rewrote
that code and added a `github_pat_` pattern and a `_RedactingFormatter`
traceback path, so there is now *more* untested surface than when the finding
was written.

Wanted: `tests/test_logging_config.py` covering redaction of each token
format (`Bearer`, `ghp_`, `github_pat_`, `sk-ant-`), the `formatException`
traceback path, and that `configure_logging()` is safe to call twice.

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
