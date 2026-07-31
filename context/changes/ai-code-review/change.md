---
change_id: ai-code-review
title: PR Review Action — reusable composite GitHub Action
status: impl_reviewed
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

Implementation review of phases 0-3 (2026-07-30): NEEDS ATTENTION — 0
critical, 8 warnings, 2 observations. **Triaged the same day: 9 fixed, 1
skipped** (F5's redaction tests, queued in `follow-ups/review-fixes.md`). See
`reviews/impl-review-phases-0-3.md`.

**GitHub transport switched from the `gh` CLI to `githubkit` (2026-07-30,
user decision during impl-review triage).** The user questioned whether the
subprocess wrapper was needed at all; since three findings (no timeout,
locale-not-UTF-8 decoding, unguarded `json.loads` on CLI stdout) were all
artifacts of the subprocess boundary rather than of the problem, the
transport moved to the typed, OpenAPI-generated `githubkit`. This reversed a
decision `plan.md` had explicitly documented and rejected the alternative
for, so the plan was updated throughout — Key Decisions, Phase 3 (rewritten),
Phase 5's repo-mismatch guard, Phase 7, Phase 8's exit codes, Phase 9's
dependencies, the risk register, and Proposed Structure. The raw diff is
fetched via `Accept: application/vnd.github.diff` (deliberately not
`pulls.list_files`, whose per-file `patch` GitHub omits for large files);
path exclusion moved client-side to `diff_parser.exclude_paths`; `git` stays
on subprocess for local VCS state only.

**Local TLS is hostile on this dev machine (2026-07-30).** AVG Antivirus
MITMs TLS *and* exports `SSLKEYLOGFILE` pointing at a device path, which
crashes CPython's OpenSSL on Windows (`no OPENSSL_Applink`) — it broke
`uv add`, `httpx`, stdlib `urllib`, and `pyright`'s nodeenv bootstrap.
Mitigated in `github_diff._build_ssl_context` (OS trust store via
`truststore`; `SSLKEYLOGFILE` suppressed while building the context, which is
independently correct for a process holding tokens). **Expect the same for
the Anthropic SDK in Phase 5** — it uses `httpx` over the same stdlib `ssl`.

Quality gates were pulled forward from Phase 9 (F9): `ruff` and `pyright` are
now installed and configured, and pyright has run for the first time.
Gate state at end of triage: ruff ✅ · ruff format ✅ · pyright ✅ 0 errors ·
pytest ✅ 26 passed · pre-commit ✅ 7 hooks.

**Phase 3 closed out (2026-07-30, user decision).** F5's queued redaction
tests were dropped: no `tests/test_logging_config.py` was written, and
`tests/test_models.py` (Phase 1's StrEnum-pinning tests) was removed too.
Progress 1.2 and 3.3 marked done-by-decision — 07f9a59. All Phase 1-3
Progress items are now checked; remaining gate state: ruff ✅ · ruff format ✅
· pyright ✅ 0 errors · pytest ✅ 22 passed, 1 skipped.

**Anthropic auth widened to support an OpenRouter-style gateway (2026-07-31,
user decision during Phase 5).** The user's only funded billing account is
OpenRouter, not the Anthropic Console, and asked for this to be the action's
supported path going forward — not just a local workaround for this one
smoke test. `anthropic-api-key` moved from a required `action.yml` input to
optional, alongside two new optional inputs, `anthropic-base-url` and
`anthropic-auth-token`, mapped to `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN`.
Verified directly against the installed `claude-agent-sdk` (0.2.128) source
that this needs **zero changes to `review_agent.py`**:
`_internal/transport/subprocess_cli.py` launches the bundled Claude Code CLI
with `inherited_env = os.environ` merged under `ClaudeAgentOptions.env`, so
whichever of the three env vars the `pr-review-agent` process itself sees,
the CLI subprocess sees too. Phase 12's dogfood workflow now routes through
OpenRouter (`anthropic-base-url: https://openrouter.ai/api`,
`anthropic-auth-token: ${{ secrets.OPENROUTER_API_KEY }}`) instead of a
direct `ANTHROPIC_API_KEY` secret. `plan.md` updated: the "Auth tokens"
Key Decision (split into a GitHub-token bullet and a new "Anthropic auth"
bullet), Phase 10's `action.yml` draft and its input-forwarding commentary,
Phase 12's `self-test.yml` and `path-resolution-test` job, the README bullet
under Phase 8, Testing Strategy step 3, and a new Risk #11 (setting both
`ANTHROPIC_API_KEY` and the gateway vars silently defeats the gateway, since
the CLI checks `ANTHROPIC_API_KEY` first — mitigated by a README warning,
not code, since `cli.py` never touches these vars itself). A consumer that
wants direct Anthropic billing is unaffected: `anthropic-api-key` alone still
works exactly as before.
