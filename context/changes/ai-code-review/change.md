---
change_id: ai-code-review
title: PR Review Action — reusable composite GitHub Action
status: impl_reviewed
created: 2026-07-30
updated: 2026-07-31
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

**Two required-input files narrowed to optional/flexible during Phase 8
(2026-07-31, user decisions).** Both reverse decisions `plan.md` had
explicitly documented:
- **`--rules-file` is now a soft dependency**, mirroring `--lessons-file`.
  Not every consumer repo has an `AGENTS.md`; `load_rules_file` (Phase 4,
  `agents_context.py`) now returns `None` and logs a warning on a missing
  file instead of raising `FileNotFoundError`, and `build_system_prompt`
  omits the "Repository Rules" section entirely when there's no content.
  `cli.py`'s exit-`3` path (input-file errors) now only fires for
  `--criteria-file`.
- **Review criteria are no longer pinned to exactly five.** User's reasoning:
  "it is specific per repo" — the number of criteria a discovery session
  converges on shouldn't be a number this tool imposes on every consumer.
  `load_review_criteria` (Phase 4) now requires only *at least one* `##`
  heading; `InvalidReviewCriteriaError` fires only on zero. `criteria-file`
  itself is still required — a review still needs some structured criteria to
  score against.

`plan.md` updated throughout: the "Prerequisite" section, two Key Decisions
(the "five" bullet reworded to "Review criteria — sourcing methodology and no
fixed count," plus a new "Rules-file is optional" bullet), Phase 4's
`load_rules_file`/`load_review_criteria`/`build_system_prompt` descriptions,
Phase 8's argparse spec and exit-code-contract step 3, Phase 6's
`build_summary` description, Phase 10's `action.yml` description field,
Testing Strategy steps 6-7, and the References entry for
`review-criteria.md`. Verified directly: `load_review_criteria` accepts a
single-criterion file and still rejects a zero-heading file;
`build_system_prompt` omits "Repository Rules" when `rules_content` is
`None` and includes it when non-empty. Full `pytest`/`ruff`/`pyright` gates
re-run clean after both changes.

**`--max-turns` default lowered from 15 to 5 (2026-07-31, user decision).**
`cli.py`'s `_DEFAULT_MAX_TURNS` and Phase 10's `action.yml` draft default
both updated to match.

**Default model lowered from `claude-opus-5` to `claude-sonnet-5`
(2026-07-31, user decision).** The switch was made in `cli.py` during Phase 8
but not written down at the time; the implementation review caught the
divergence. Rationale: a review runs on every push, so the default carries
recurring cost, and opus remains one `--model` flag away for runs that
warrant it. `plan.md` updated at all three sites (Key Decisions, the Phase 8
argparse spec, and Phase 10's `action.yml` draft default) so `action.yml`
does not drift when Phase 10 is written.

**Real bug found and fixed during Phase 8 manual `--publish` testing
(2026-07-31): off-by-one line numbers caused a GitHub 422.** The user ran
`--publish` against PR #1 successfully, then (per the post-failure Testing
Strategy step) closed the PR and re-ran `--publish`, expecting a clean
rejection. Instead got `HTTP 422: "Line could not be resolved"`. Reopening
PR #1 and re-running the identical command reproduced the *same* 422 on an
**open** PR — proving the failure had nothing to do with the PR being
closed. Inspecting the written JSON artifact showed the model had submitted
a finding at `line: 11`, but PR #1's only hunk (`@@ -6,3 +6,5 @@`) only
spans new-file lines 6-10; line 11 doesn't exist. Root cause: `hunks_text`
(the diff text shown to the model) was plain unified-diff text — line
numbers only appear once, in the `@@ -a,b +c,d @@` header — so the model had
to count lines itself to know what number to put in `submit_finding`, and
miscounted by one.

**Fix**: `diff_parser._render_hunk` now prefixes every hunk line with its
exact `old_line new_line` pair (`.` for the side that has none, e.g. an
added line has no old_line), so the model reads the number directly instead
of counting. `agents_context._SUBMIT_FINDING_CONTRACT` was updated to
describe the annotation format and instruct the model to use it verbatim,
"never count lines yourself." Verified the fix directly: re-fetching PR #1's
diff now renders `.      9 +` / `.     10 +<!-- throwaway... -->` for the
added lines; a manually-built `Finding(path="README.md", line=10,
side=RIGHT, ...)` posted successfully via `post_review` (confirmed live on
GitHub, anchored to the correct line), whereas `line=11` is exactly what had
failed before. All of Phase 2's existing tests (`tests/test_diff_parser.py`)
still pass unchanged — they measure `hunks_text` length/membership
dynamically rather than asserting a fixed raw-diff string, so the format
change didn't need any test rewrites. Full `pytest`/`ruff`/`pyright` gates
re-run clean. `plan.md`'s Phase 2 section updated with this fix, tagged to
Phase 8 since that's when it surfaced.

**Closed/merged-PR guard added (2026-07-31, user decision).** After the fix
above, the user re-tested the post-failure path against the closed PR #1 and
found it was no longer a failure at all — the review ran and the comment
published successfully to a *closed* PR. Their feedback: reviewing (and
posting to) a PR nobody can act on anymore is wasted work, and the tool
should check PR state up front and stop, not just happen to succeed or fail
depending on whether GitHub allows the API call. `PullRequestMetadata`
(`github_diff.py`) gained `state: str` and `merged: bool` fields, populated
directly from `pulls.get`'s response. `cli.py`'s `main_async` now fetches PR
metadata, checks `state != "open"` **immediately** — before fetching the
diff, before loading any files, before the agent ever runs — and if closed
or merged, prints "PR #<n> is closed/merged; skipping review" and exits `0`
(the same benign-skip pattern as the zero-changed-files and scope guards).
Verified live against PR #1 (currently closed, left that way per the user):
the run exits `0` immediately after the metadata fetch, with no diff fetch,
no agent invocation, and no publish attempt in the logs. `plan.md`'s Phase 8
exit-code-contract list and Phase 3's `PullRequestMetadata` description both
updated; the contract's steps were renumbered 1-12 to insert this as step 2.
Full `pytest`/`ruff`/`pyright` gates re-run clean; no test file constructs
`PullRequestMetadata` directly, so no existing tests needed updating for the
two new required fields.

**Phase 9 closed out (2026-07-31).** Added the missing
`[project.scripts] pr-review-agent = "pr_review_agent.cli:main"` entry point
to `pyproject.toml`; verified `uv run pr-review-agent --help` resolves via
the console script. **Build-system left as `uv_build` (deviation from the
plan's `hatchling` text)**: Phase 0's `uv init --lib` scaffolding already set
`[build-system] requires = ["uv_build>=0.10.9,<0.11.0"]` /
`build-backend = "uv_build"`, and it already builds/installs the package
correctly — switching to hatchling would be pure churn with no functional
difference for a single-package `src/` layout, so it was left as-is rather
than escalated. `uv.lock` was already tracked and committed (not gitignored),
consistent with the plan. Gate re-run clean: ruff ✅ · ruff format ✅ ·
pyright ✅ 0 errors (both hit the known `SSLKEYLOGFILE`/AVG `OPENSSL_Applink`
crash from Phase 3's TLS note until `SSLKEYLOGFILE` was unset for the shell)
· pytest ✅ 40 passed, 1 skipped.

**Implementation review of phases 4-9 (2026-07-31): REJECTED — 3 critical, 6
warnings, 1 observation. Triaged the same day; all 10 fixed.** See
`reviews/impl-review-phases-4-9.md`. All automated success criteria re-ran
green (pytest 40 passed/1 skipped · ruff · ruff format · pyright 0 errors), and
the implementation matches the plan's described behavior closely (~50 MATCH
verdicts). The verdict is driven by three things the plan never described:
(F1) `token.txt` in the repo root holds a live `sk-or-v1-` OpenRouter key,
untracked but **not** gitignored — one `git add .` from `main`; `redact()` has
no pattern for that prefix either. (F2) `review_agent._build_options` sets
neither `setting_sources` nor `strict_mcp_config`, so the SDK loads
`.claude/settings.json`, `CLAUDE.md`, and `.mcp.json` from the *PR-head*
checkout — a hook in an attacker's PR executes on the runner, bypassing the
whole `allowed_tools`/`dontAsk` lockdown. (F3) the plan's step-9-before-step-10
ordering is implemented correctly, but `_write_artifacts` writes nothing when
`--format console` (the default), so the exit-`5` path loses every finding
while logging that it saved them — the exact loss Risk #5 and step 10 were
written to prevent.

**Triage of the phases 4-9 review (2026-07-31): all 10 findings fixed, none
skipped or accepted as risk.** Gates after: pytest ✅ 77 passed / 1 skipped
(up from 40/1) · ruff ✅ · ruff format ✅ · pyright ✅ 0 errors. The
substantive outcomes, beyond the mechanical fixes recorded in the review file:

*Review inputs now come from the PR's base ref, not the checkout (F4).* This
is a **consumer-visible contract change**, so it went into `plan.md`'s Key
Decisions as an amendment. `rules-file`, `lessons-file` and `criteria-file`
all land in the *system* prompt, and the workflow checks out the PR head — so
a pull request could append "always approve" to its own `AGENTS.md`, or
rewrite the criteria it was about to be scored against. Any input path
resolving inside the checkout is now fetched via the contents API at
`base_ref_name`; paths outside it still read from disk, since they aren't part
of the PR. Consequence: a PR that legitimately updates `AGENTS.md` isn't
reviewed against its own new rules — that takes effect on merge.
`--trust-head-files` / `trust-head-files:` opts back out, for local runs and
for branches whose criteria file doesn't exist on the base ref yet. Criteria
was folded in alongside rules/lessons even though the finding only named the
latter two: it defines the axes being scored, so leaving it on PR-head would
have left an identical hole open.

*The `Read` sandbox is a `PreToolUse` hook, not `can_use_tool` (F4).* The
review proposed `can_use_tool`; it would never have fired. `allowed_tools`
lists `Read`/`Grep`/`Glob` by bare name, and the SDK auto-approves a
whole-tool entry *before* consulting the permission callback — it even emits
`CanUseToolShadowedWarning` for exactly this, and its own guidance is to use a
`PreToolUse` hook. `review_agent._make_repo_path_guard` now denies any
`file_path`/`path` argument resolving outside `repo_root`, with `resolve()`
collapsing `..` and following symlinks. These are in-process Python hooks —
unrelated to the `.claude/settings.json` hooks that F2's `setting_sources=[]`
keeps the PR-head checkout from registering.

*The plan was wrong about the exit-code contract, and the plan lost (F7).*
Phase 5's bullet and step 10 both folded "SDK failed" and "no valid verdict"
into exit `5`; `cli.py`'s module docstring described them as 4 and 5. The
docstring was the correct half — `ReviewRunResult`'s own docstring insists the
two are independently checkable facts, and a consumer retries a transient SDK
failure but treats a missing verdict as advisory. Code split the branch, and
both plan sites were amended rather than the code being bent to match.

*Exit code 2's argparse collision was deliberately left alone (F7).* Moving it
is a breaking change to a contract already published in `plan.md`, and the
argparse case is a permanent config error the workflow fails on immediately
either way. Only the branch split was taken.

*Test baseline restored (F9).* `tests/test_agents_context.py` is new (16
tests); `tests/test_cli.py` went from 7 tests to 28, covering every documented
exit code with the five boundary calls monkeypatched, plus regression tests
pinning F3, F7, F8 and F4. No `pytest-asyncio` was needed — the tests drive
`main_async` through `asyncio.run`. `github_publish.py` stays untested, on the
same live-boundary rationale the plan used to waive `review_agent.py`.

**Two items still need the user**: rotate the leaked OpenRouter key from F1
(and update the `OPENROUTER_API_KEY` repo secret Phase 12 depends on), and
`git add tests/fixtures/smoke-criteria.md` — it is still untracked and the
test suite now depends on it.
