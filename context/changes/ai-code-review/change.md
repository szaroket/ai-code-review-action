---
change_id: ai-code-review
title: PR Review Action — reusable composite GitHub Action
status: implementing
created: 2026-07-30
updated: 2026-07-30
archived_at: null
---

## Notes

Originated as a single-repo tool inside another repo
(`tools/pr-review-agent/`), reviewing that repo's own `AGENTS.md`. Moved to
this dedicated repo (`ai-code-review-action`) and generalized into a
reusable composite GitHub Action — consumer repos supply their own rules
file, criteria file, and optional lessons file as inputs instead of the
tool hardcoding one repo's paths and conventions. See `plan.md` for the
full design.

**Standalone constraint (2026-07-30, user directive during plan triage):**
this repo must not depend on any other repository — including in its tests
and verification steps. Other repos consume the action; none participate in
building or validating it. This is what drove F3's fix away from a
second-repo checkout toward the self-contained nested-checkout
`path-resolution-test` job, and what removed the cross-repo smoke test from
the Testing Strategy.

**Dependency sweep (2026-07-30, follow-up to the constraint above):** audited
`plan.md` for every reference to the origin repo. Nine were live couplings
and were removed — `gh` availability was re-verified in *this* repo
(`gh 2.93.0`, authenticated as `szaroket`) instead of being inherited from
research done elsewhere; the origin repo's own criteria-file discovery
session and workflow-wiring paragraphs were deleted from the Prerequisite
and Follow-up sections; three design justifications that leaned on the
origin repo's conventions (`.claude/` ignore rule, `upload-artifact` pattern,
CI shape) were re-grounded in this repo's own reasoning; the Phase 2 binary
fixture is now explicitly hand-authored here. The remaining mentions are
history of how the design got here, plus the `.gitignore` cleanup
provenance, and are labelled as such. The constraint is now stated in the
plan's Overview so implementers see it, and restated as a "What We're NOT
Doing" bullet.

Plan review triaged 2026-07-30: all 9 findings fixed. See
`reviews/plan-review.md`.
