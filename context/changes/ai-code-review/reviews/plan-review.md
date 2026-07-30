<!-- PLAN-REVIEW-REPORT -->
# Plan Review: PR Review Action — Implementation Plan

- **Plan**: `context/changes/ai-code-review/plan.md`
- **Mode**: Deep
- **Date**: 2026-07-30
- **Verdict**: REVISE → **SOUND** after triage (all 9 findings fixed 2026-07-30)
- **Findings**: 3 critical, 4 warnings, 2 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | FAIL |
| Lean Execution | WARNING |
| Architectural Fitness | PASS |
| Blind Spots | WARNING |
| Plan Completeness | WARNING |

## Grounding

`.gitignore` ✓ (frontend-specific lines confirmed at `:220-229` and `:247`; `.claude/` rule at `:243`) · SDK facts ✓ (traced to `first-research.md:39-59` — `permission_mode="dontAsk"`, `allowed_tools`/`disallowed_tools`, `create_sdk_mcp_server`, reviews POST payload) · repo state ✗ — `.claude/` claimed committed, actually untracked (F1) · all other paths are new files in a fresh repo (expected) · no `plan-brief.md`, no `context/foundation/lessons.md`, no `docs/reference/contract-surfaces.md` (those checks skipped).

## Findings

### F1 — Current State Analysis is wrong about `.claude/`; Phase 0 step 2 will error

- **Severity**: ❌ CRITICAL
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Blind Spots
- **Location**: Current State Analysis (`plan.md:43-57`) · Phase 0 step 2 · Progress 0.4
- **Detail**: The plan states `.claude/` is "already present and **already committed** in that initial commit", that this is "out of sync with the ignore rule", and Phase 0 step 2 prescribes `git rm --cached -r .claude`. Verified against the repo: `git ls-tree -r --name-only HEAD` → `.gitignore` only (1 file total); `git ls-files .claude` → 0 files; `git check-ignore -v .claude/` → `.gitignore:243` ✓. `.claude/` was never committed and is already correctly ignored. The "out of sync" premise is false, and `git rm --cached -r .claude` will fail with `fatal: pathspec '.claude' did not match any files` — the literal first command of the first phase.
- **Fix**: Delete Phase 0 step 2 and Progress row 0.4's untrack clause; correct the Current State Analysis bullet to "`.claude/` is present on disk, untracked, and already covered by `.gitignore:243` — nothing to do." The `.gitignore` trim (step 1) is unaffected and still correct.
- **Decision**: FIXED — Phase 0 step 2 and Progress 0.4 untrack clause removed; Current State Analysis corrected.

### F2 — action.yml declares six inputs it never forwards to the CLI

- **Severity**: ❌ CRITICAL
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: End-State Alignment
- **Location**: Phase 10 — action.yml (`plan.md:565-608`)
- **Detail**: The `inputs:` block declares `repo`, `scope-dirs`, `exclude`, `max-turns`, `max-findings`, `out-dir`. The `Run review` step passes only `--pr`, `--rules-file`, `--criteria-file`, `--lessons-file`, `--model`, `--format`, `--publish`, `--verbose`. All six are silently dropped. `scope-dirs` is not incidental — it is the headline generalization of this entire plan (Overview `:30-34`), and the Follow-up section commits `10xDevs-Project` to consuming the action with `scope-dirs: frontend,backend`. As written, that consumer would set the input, see no error, and get every changed file reviewed. `out-dir` matters too: Phase 12 uploads `review-output/` as the assignment's evidence artifact, and a consumer overriding `out-dir` would upload an empty directory.
- **Fix**: Extend the run step with conditional forwarding for all six, matching the existing `lessons-file` pattern — e.g. `${{ inputs.repo != '' && format('--repo "{0}"', inputs.repo) || '' }}` and `${{ inputs.scope-dirs != '' && format('--scope-dirs "{0}"', inputs.scope-dirs) || '' }}` — plus unconditional `--max-turns`/`--max-findings`/`--out-dir` (they have defaults). Note `--scope-dirs`/`--exclude` are repeatable in Phase 8 but comma-scalar as action inputs; Phase 8 must accept a comma-separated value, or action.yml must split. Pin that down in Phase 8's argparse spec.
  - Strength: Closes the gap where the plan's stated primary feature is unreachable through the action, which is the only supported consumption path.
  - Tradeoff: The run block gets noticeably harder to read; six more expression-interpolated shell fragments to get quoting right.
  - Confidence: HIGH — verified by direct comparison of the inputs block against the run block; no ambiguity.
  - Blind spot: Whether GitHub expression interpolation inside a multi-line `run:` handles empty-string fallbacks cleanly for all six hasn't been exercised — worth one throwaway workflow run.
- **Decision**: FIXED — all six inputs forwarded in action.yml; Phase 8 now specifies comma-separated `--scope-dirs`/`--exclude`.

### F3 — Phase 10's only verification cannot verify Phase 10

- **Severity**: ❌ CRITICAL
- **Impact**: 🔬 HIGH — architectural stakes; think carefully before deciding
- **Dimension**: End-State Alignment
- **Location**: Progress 10.1 · Testing Strategy step 5 · Phase 12 · Risk #1
- **Detail**: Risk #1 is the plan's own top-listed risk: `--project` vs `--directory` in action.yml, which if wrong "would silently make every consumer's review target *this action's own source* instead of their repo." Three things are meant to cover it, and none do:
  1. Progress 10.1 says the cross-repo smoke test "confirms `--project` (not `--directory`) resolves paths against the caller's checkout." But Testing Strategy step 5 invokes the CLI directly — `uv run pr-review-agent --pr 53 --repo szaroket/10xDevs-Project ...` — from this repo's root. `action.yml` is never executed, so neither flag is ever exercised. The verification and the thing verified are disjoint.
  2. Testing Strategy step 5 is labeled "(optional, validates genuine reusability)" while Progress 10.1 treats it as Phase 10's mandatory gate. Direct contradiction.
  3. Phase 12's dogfood uses `uses: ./`, which makes `github.action_path` == `$GITHUB_WORKSPACE`. When the action's project dir and the caller's checkout are the same directory, `--project` and `--directory` are behaviorally identical — the self-test is blind to this exact failure by construction, despite the plan claiming it "doubles as the action's own integration test before tagging a release."

  Net: the plan can reach "all phases green" with its highest-severity risk entirely unexercised, and only discover it when the first real external consumer wires it up.
- **Fix A ⭐ Recommended**: Add a real cross-repo *action* test, not a CLI test. Make Phase 12 (or a new Phase 13) include a workflow job that checks out a second repo into the workspace and invokes the action against it with an explicit `repo:` input — i.e. a case where `action_path` ≠ the reviewed checkout. Retarget Progress 10.1 at that job. Resolve the optional/mandatory contradiction by making Testing Strategy step 5 non-optional and rewriting it to run the action, not the CLI.
  - Strength: Actually exercises the failure mode; also happens to be the only end-to-end proof of the reusability claim the whole repo-split decision rests on.
  - Tradeoff: Needs a second repo (or a scratch fixture repo) plus a token with read access to it; more CI surface to maintain.
  - Confidence: HIGH — the degeneracy of `uses: ./` making `action_path` equal the workspace is a direct consequence of how composite actions resolve, not a guess.
  - Blind spot: Whether a scratch fixture repo is enough, or whether it needs a PR with a real diff to be meaningful — likely the latter, which adds setup cost.
- **Fix B**: Assert the invariant in code instead of in CI. Have `cli.py` log (at `--verbose`) and assert that `find_repo_root()` != the installed package's location, failing with a clear message if they match unexpectedly. Keep Progress 10.1 as a cheap CLI-level check and drop the claim that it validates `action.yml`.
  - Strength: No second repo needed; the guard protects every consumer at runtime rather than only at our release time.
  - Tradeoff: Doesn't prove `action.yml` is correct — only that a wrong cwd is loud instead of silent. The misconfiguration still ships.
  - Confidence: MEDIUM — the assertion is easy, but picking a condition that doesn't false-positive under `uses: ./` (where the two paths legitimately coincide) needs care.
  - Blind spot: Interaction with `uses: ./` self-reference is exactly the case that makes the assertion ambiguous.
- **Decision**: FIXED (reformulated for standalone) — original Fix A required a second repo, ruled out by the user's standalone constraint. Replaced with a nested-checkout `path-resolution-test` job in Phase 12 (checkout at root + `path: _action`, `uses: ./_action`, root-only fixture criteria file), which breaks the `action_path == workspace` degeneracy using only this repo. Progress 10.1 retargeted; Testing Strategy step 5 rewritten as mandatory and action-level; Risk #1 notes the mechanical gate.

### F4 — Exit 5 discards findings the agent already produced

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Phase 8 — cli.py, exit-code contract steps 7-8 (`plan.md:540-542`)
- **Detail**: Step 7 exits `5` on "missing/invalid verdict"; step 8 is what writes artifacts to disk. So a run where the agent submitted twenty good findings but never landed a valid `submit_review_verdict` (hit `max_turns`, or every attempt failed the exact-name-match validation in Phase 5) exits with nothing written — the findings are lost. This contradicts the principle the plan states twice elsewhere: the "Post-failure handling" decision (`:176-180`) and Risk #8 (`:684-686`) both commit to writing local artifacts *before* anything that can fail, so findings survive a red job. Exit 5 is precisely such a failure, and it's the one the plan calls most likely (Risk #5).
- **Fix**: Move artifact writing ahead of the verdict check. On a missing verdict, write findings with `verdict: null` and a clear "INCOMPLETE — no verdict produced" banner in the console/markdown output, then exit 5. This also needs `ReviewOutput.event` to tolerate a null verdict; Phase 1 already allows `verdict: ReviewVerdict | None` but Phase 6's `build_review_output` derives `event` from `verdict.overall_verdict`, so give it a `"COMMENT"` fallback used only on this path. Never publish on this path.
- **Decision**: FIXED — artifact writing moved ahead of the verdict check (Phase 8 steps 7/8 swapped); null verdict + INCOMPLETE banner; `"COMMENT"` event fallback in Phase 6; never publishes on this path. Phase 1 and Risk #5 updated to match.

### F5 — Agent explores the wrong repo when `--repo` ≠ local checkout

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Phase 5 (`cwd=repo_root`) · Phase 3 (`--repo`) · Testing Strategy step 5
- **Detail**: Phase 5 sets `ClaudeAgentOptions(cwd=repo_root, allowed_tools=["Read","Grep","Glob",...])` so the agent can explore "context strictly around changed lines." `repo_root` is the local checkout. But the diff comes from whatever `--repo` names, and Phase 3 explicitly says `--repo` is "now *always* meaningfully used, since every consuming repo is a different `--repo` (no more 'usually the same repo' assumption)." When the two diverge, Read/Grep/Glob silently browse a *different codebase* than the one under review. The agent will either find nothing (paths don't exist) or — worse — find same-named files with different contents and cite them in `rule_reference`. Testing Strategy step 5 does exactly this: reviews a `10xDevs-Project` PR while `cwd` is this action's repo. Its stated success condition ("confirm it correctly resolves paths against the *other* repo") is not achievable for the exploration tools. Nothing in the plan detects or warns about the mismatch.
- **Fix**: In `cli.py`, compare `--repo` against the local checkout's origin (`gh repo view --json nameWithOwner`). On mismatch, either drop Read/Grep/Glob from `allowed_tools` for that run (diff-only review) or emit a loud stderr warning that repo exploration is disabled. Update Testing Strategy step 5's expected outcome to match — it becomes a diff-only smoke test, which is still a valid check of the fetch/parse/prompt path.
- **Decision**: FIXED — repo-mismatch guard added to Phase 5 (`gh repo view` vs `--repo`; on mismatch drop Read/Grep/Glob and warn loudly). Cross-repo Testing Strategy step 5 deleted; Phase 3 verification retargeted at this repo.

### F6 — Progress↔Phase headers violate the mechanical contract

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase headers (`plan.md:294-634`) vs Progress (`plan.md:765-853`)
- **Detail**: `references/progress-format.md` requires "One `### Phase N: <name>` per phase, in order, matching the `## Phase N:` headers earlier in the plan", and `/10x-implement` counts phases "from `## Phase N:` headers". Actual state: body is `### Phase 0 — Repo Environment Setup` (H3, em dash) while Progress is `### Phase 0: Repo Environment Setup` (H3, colon). `grep -c "## Phase [0-9]*:"` returns 13, and all 13 matches are inside the Progress section — the body headers match nothing. Phase counting happens to land on the right number by reading Progress twice rather than by reading the body once. Names also diverge (Phase 1 body backticks `models.py`; Phase 12 body appends "+ this repo's own criteria file", Progress doesn't). Separately, Progress is specified to sit after a `## References` heading; the plan has no such heading (`## Critical Files` is the nearest).
- **Fix**: Promote body phase headers to `## Phase N: <name>` (H2, colon) with names byte-identical to their Progress counterparts, and rename `## Critical Files` → `## References` (or add `## References` above `## Progress`).
- **Decision**: FIXED — all 13 body headers promoted to `## Phase N: <name>`, byte-identical to Progress; `## Critical Files` renamed `## References`. Verified: 13/13 match, 0 stray checkboxes before Progress, References precedes Progress.

### F7 — `find_repo_root()` is load-bearing but unowned by any phase

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 10 critical note (`:616`) · Phase 5 (`cwd=repo_root`)
- **Detail**: `find_repo_root()` appears only inside Phase 10's prose, as one of the relative-path resolvers the `--project` choice protects, and is implied by Phase 5's `cwd=repo_root`. No phase says which module defines it, that it shells out to `git rev-parse --show-toplevel`, or what happens when that fails (not a git checkout, or `git` absent). Given F5, this function is also where the repo-mismatch guard would naturally live.
- **Fix**: Assign it explicitly — add `find_repo_root() -> Path` to Phase 3's `github_diff.py` (it's already the subprocess-wrapper module), specify the `git rev-parse --show-toplevel` implementation, and define the fallback (cwd) plus which exit code a hard failure maps to.
- **Decision**: FIXED — `find_repo_root() -> Path` assigned to Phase 3's `github_diff.py` with `git rev-parse --show-toplevel` implementation, `Path.cwd()` fallback with stderr warning, and no new exit code (degradation, not hard failure). Progress 3.1 updated.

### F8 — `actions/setup-python@v6` is redundant alongside setup-uv

- **Severity**: 💬 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Lean Execution
- **Location**: Phase 10 — action.yml steps
- **Detail**: `uv sync` provisions its own interpreter from `.python-version` (3.13, Phase 0) and `requires-python = ">=3.13"` (Phase 9). The `setup-python` step adds ~10s to every consumer's job and introduces a second source of truth for the Python version that can drift from `.python-version`.
- **Fix**: Drop the `actions/setup-python@v6` step; let `astral-sh/setup-uv` + `uv sync` own the interpreter.
- **Decision**: FIXED — `actions/setup-python@v6` step dropped from action.yml, with a note on why.

### F9 — `uv.lock` isn't in the structure and no phase commits it

- **Severity**: 💬 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Proposed Structure (`:241-275`) · Phase 0 step 4 · Phase 9
- **Detail**: The tree lists `pyproject.toml` but not `uv.lock`, and no phase says to commit it. `.gitignore:98-101` leaves `uv.lock` commented out (i.e. not ignored), so it *can* be committed — but for a published action the lock is what makes `uv sync --project` reproducible at a given tag. Without it, every consumer's run at `@v1` re-resolves `claude-agent-sdk` and `unidiff`, so an upstream breaking release breaks all consumers of an already-tagged action.
- **Fix**: Add `uv.lock` to Proposed Structure and to Phase 9's deliverables, with an explicit note that it is committed (not ignored).
- **Decision**: FIXED — `uv.lock` added to Proposed Structure and Phase 9, explicitly noted as committed, not ignored.

## Reviewer's note

This is a well-researched plan — the SDK facts trace cleanly to `first-research.md`, the genericization reasoning is sound, and the exit-code contract is unusually well thought through. The problems cluster in one place: the `action.yml`/verification boundary (F2, F3), where the reusability promise the whole repo-split rests on is neither wired through nor testable. F1 is a five-minute correction but sits in the very first step.
