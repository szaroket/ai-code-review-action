<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: PR Review Action

- **Plan**: `context/changes/ai-code-review/plan.md`
- **Scope**: Phases 0-3 of 12
- **Date**: 2026-07-30
- **Verdict**: NEEDS ATTENTION → **TRIAGED 2026-07-30** (9 fixed, 1 skipped)
- **Findings**: 0 critical, 8 warnings, 2 observations

## Triage outcome (2026-07-30)

| Finding | Decision |
|---|---|
| F1 secret redaction bypasses | FIXED |
| F2 locale decoding | FIXED |
| F3 `build_diff_context` contract | FIXED (Fix A) |
| F4 unplanned `logging_config.py` | FIXED (Fix A) |
| F5 missing tests | **SKIPPED** — part (3) landed via F3; rest queued in `follow-ups/review-fixes.md` |
| F6 no subprocess timeout | FIXED at root — transport switched to `githubkit` |
| F7 unguarded `json.loads` | FIXED at root — same switch |
| F8 `StrEnum` unpinned | FIXED |
| F9 quality gates absent | FIXED |
| F10 plan self-contradiction | FIXED |

**Major change triggered during triage:** the user questioned whether the
`gh` CLI subprocess was needed at all. Since F2, F6, and F7 were all
artifacts of the subprocess boundary, the GitHub transport was switched to
**`githubkit`** (typed, OpenAPI-generated) — reversing a decision the plan
had documented. `plan.md` was updated throughout: Key Decisions, Phase 3,
Phase 5's repo-mismatch guard, Phase 7, Phase 8's exit codes, Phase 9's
dependency list, the risk register, and Proposed Structure.

**Environment discovery:** AVG Antivirus MITMs TLS on the dev machine *and*
exports `SSLKEYLOGFILE` pointing at a device path, which crashes CPython's
OpenSSL on Windows (`no OPENSSL_Applink`) — it broke `uv add`, `httpx`,
stdlib `urllib`, and `pyright`'s nodeenv bootstrap alike. Mitigated in
`_build_ssl_context` (OS trust store via `truststore`, `SSLKEYLOGFILE`
suppressed). **This applies to the Anthropic SDK in Phase 5 too.**

Final gate state: `ruff check` ✅ · `ruff format --check` ✅ ·
`pyright` ✅ 0 errors (first run ever) · `pytest` ✅ 26 passed ·
`pre-commit` ✅ 7 hooks.

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | WARNING |
| Scope Discipline | WARNING |
| Safety & Quality | WARNING |
| Architecture | PASS |
| Pattern Consistency | WARNING |
| Success Criteria | PASS |

## Success criteria — all verified by execution

| Check | Result |
|-------|--------|
| 0.1 `uv sync` | PASS |
| 0.2 `uv run python -c "import pr_review_agent"` | PASS |
| 0.3 `pre-commit run --all-files` | PASS (7 hooks) |
| 0.4 `.gitignore` trimmed of frontend-specific lines | PASS — zero hits for `node_modules\|dist-ssr\|*.local\|playwright\|test-results\|frontend`; `# uv.lock` correctly left commented |
| 1.1 models import | PASS |
| 2.1 `uv run pytest tests/test_diff_parser.py -v` | PASS (8 passed) |
| 3.1 manual verification against a real PR | PASS — PR #1 exists on this repo, opened 2026-07-30 from `phase3-github-diff-verification-test`. Not rubber-stamped. |

Note: `uv run ruff check` and `uv run pyright` both fail with "program not
found" (dev group holds only pytest). Lint runs only via the pre-commit hook.
See F9.

## Findings

### F1 — Secret redaction bypassed on exception and traceback paths

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: `src/pr_review_agent/logging_config.py:29-34,94-95`; `src/pr_review_agent/github_diff.py:69`
- **Detail**: `_RedactionFilter` scrubs only `record.getMessage()`. Two channels route around it: (1) `_JsonFormatter` emits `payload["exc_info"] = self.formatException(...)` separately, and the console formatter's `super().format()` appends the traceback the same way — neither is scrubbed; (2) `GhCommandError(f"\`gh {args}\` failed: {stderr}")` carries raw `gh` stderr into an exception message, which never passes through a logging filter at all — Phase 8's CLI will print these. Also `_GH_TOKEN` (line 13) matches `gh[pousr]_` but not `github_pat_...`, the fine-grained PAT format GitHub now steers users toward and a plausible `github-token` input value. This lands in public GitHub Actions logs since `action.yml` runs with `--verbose`. No current call site logs `exc_info`, so it is latent, not live.
- **Fix**: Extract the three substitutions into a module-level `redact(text: str) -> str` in `logging_config.py`; call it from `_RedactionFilter`, from `_JsonFormatter`'s exc_info branch, and on `stderr` at each `raise GhCommandError` site. Add `github_pat_[A-Za-z0-9_]{20,}` to the pattern set.
  - Strength: Closes all three paths with one shared helper, and makes the control testable in isolation (see F5).
  - Tradeoff: Touches two modules; ~15 lines.
  - Confidence: HIGH — verified all three bypass paths by reading the formatter and the raise sites directly.
  - Blind spot: Pattern matching always trails new token formats; scrubbing the literal env-var values at `configure_logging()` time would be strictly safer.
- **Decision**: FIXED — added module-level `redact()` + `_RedactingFormatter` base (overrides `formatException`, inherited by both formatters, covering the console traceback path too); `github_pat_` pattern added; `_run_gh` redacts `stderr` before it reaches any `GhCommandError`.

### F2 — subprocess calls decode with the locale encoding, not UTF-8

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: `src/pr_review_agent/github_diff.py:37-42,167-173`
- **Detail**: Both `subprocess.run` calls pass `text=True` with no `encoding=`. Python 3.13 then decodes with `locale.getencoding()` — cp1250 on this Windows dev machine. `gh pr diff --patch` emits UTF-8, and cp1250 maps nearly every byte, so non-ASCII in a diff (identifiers, comments, filenames) is silently mojibake'd rather than raising. Linux CI is usually UTF-8, making this a broken-locally / works-on-CI divergence that is hard to notice — and the agent would review corrupted text while the line-number math still "succeeds". The plan mandates explicit `encoding="utf-8"` on file writes (Phase 6); the same reasoning applies to reading `gh` output.
- **Fix**: Pass `encoding="utf-8", errors="replace"` to both `subprocess.run` calls.
  - Strength: Two-line change; makes local and CI behavior identical and matches the plan's stated encoding discipline.
  - Tradeoff: None meaningful — `errors="replace"` trades a crash on genuinely invalid bytes for a visible replacement char.
  - Confidence: HIGH — Python 3.13 `text=True` semantics are documented and this repo is developed on Windows / run on Linux.
  - Blind spot: None significant.
- **Decision**: FIXED — `encoding="utf-8", errors="replace"` added to both `subprocess.run` calls.

### F3 — build_diff_context drops the highest-priority file, and max_chars is not a ceiling

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Plan Adherence
- **Location**: `src/pr_review_agent/diff_parser.py:127,132-141`
- **Detail**: Two contract violations in one function, both verified by running it against `tests/fixtures/sample.diff`.
  (a) Line 141 uses `continue`, so after skipping one over-budget file it keeps packing smaller later files. The docstring (lines 114-117) promises dropping "starting from the first file that would push the total over budget" — that describes `break`. The plan says "dropping whole low-priority files". Measured at `max_chars=180` with priority order `[modified.py, new_file.py, new_name.py, to_remove.py, image.png]`: it dropped `modified.py` (highest priority) and kept `new_file.py`. At a tighter budget the sole survivor was `image.png` — the binary placeholder, the least informative block in the diff.
  (b) `file_list_text` (line 127) is emitted before any budgeting. Measured: `build_diff_context(files, max_chars=10)` returns 75 characters. On a PR touching thousands of files the return can exceed the caller's budget without bound — exactly the prompt overflow `max_chars` exists to prevent.
  The "never truncate mid-file" half of the requirement does hold. `test_build_diff_context_over_budget_drops_whole_files` sets the budget to exactly file-list + first block, the one value where greedy and prefix truncation coincide — which is why the suite is blind to (a).
- **Fix A ⭐ Recommended**: Change `continue` → `break` to match the docstring and the plan, and truncate the file list itself with an "... and N more files" tail once it alone exceeds `max_chars`. Add a test with a big-file-then-small-file ordering that discriminates the two behaviors.
  - Strength: Restores both stated contracts; the discriminating test pins the choice so a future refactor can't silently flip it back.
  - Tradeoff: Slightly less budget utilization — one oversized file now stops the packing entirely.
  - Confidence: HIGH — reproduced both behaviors by execution, not inspection.
  - Blind spot: Callers may prefer max utilization; no caller exists yet (Phase 8 builds it), so the cost is unmeasured.
- **Fix B**: Keep greedy best-fit and rewrite the docstring to describe it honestly, plus rename `max_chars` to reflect that it budgets hunks only.
  - Strength: Preserves more context per prompt-token budget.
  - Tradeoff: Accepts that the most important file in a PR — usually the biggest — is the one most likely to be dropped.
  - Confidence: MEDIUM — defensible, but contradicts the plan's own priority-ordering language.
  - Blind spot: Haven't modeled real PR size distributions.
- **Decision**: FIXED via Fix A — `continue` → `break`; new `_build_file_list` trims the path list to an `... and N more file(s)` tail so `max_chars` is a real ceiling; 3 new tests (stop-at-first-over-budget, ceiling-on-huge-file-list, and the F5 rename-direction assertion). 11 tests pass.

### F4 — logging_config.py and py.typed appear nowhere in the plan

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Scope Discipline
- **Location**: `src/pr_review_agent/logging_config.py` (164 lines); `src/pr_review_agent/py.typed`
- **Detail**: The word "logging" appears nowhere in the 988-line plan. This module is absent from "Proposed Structure", from Phase 3's deliverables, and from every Progress checkbox — 3.1 covers only `find_repo_root` and the `gh` wrappers, yet 164 lines of logging infrastructure landed under commit `b7b0c3f`.
  It is not *incompatible* with the plan — the only handler streams to `ext://sys.stderr` (line 122), stdout stays clean, and `configure_logging(verbose=...)` → DEBUG (line 161) is exactly the hook Phase 5's "--verbose to stderr" needs. Nothing configures logging at import time. That is good design.
  Three things still warrant a deliberate decision: (1) `json_format` (line 141) has no plan slot — Phase 8's `--format {console,json,markdown,all}` governs review output, not log format; nothing ever sets it, so it is unreachable speculative surface. (2) `configure_logging` hand-copies three dict levels (154-161) but leaves `filters`/`formatters` aliased to the module-level `LOGGING_CONFIG`; `dictConfig` writes converted objects back in place, so the first call mutates the shared dict and a second call operates on converted state. (3) Commit `b7b0c3f`'s own message says it was "modeled on 10xDevs-Project's logging_config.py minus correlation ID" — not a runtime dependency, so not a violation of the standalone constraint's letter, but an unplanned module sourced from the exact repo the plan spends three paragraphs disentangling from.
- **Fix A ⭐ Recommended**: Keep the module, amend the plan's "Proposed Structure" and Phase 3 to name it, drop the unused `json_format` branch, and replace the hand-copy with `copy.deepcopy(LOGGING_CONFIG)`.
  - Strength: The module is genuinely well-built and Phase 5 needs a `--verbose` stderr channel anyway; updating the plan keeps it usable as ground truth for the remaining 9 phases.
  - Tradeoff: Plan becomes a moving target; sets a precedent for post-hoc scope documentation.
  - Confidence: HIGH — read the module in full; it does what Phase 5 will need and nothing harmful.
  - Blind spot: Whether the origin-repo provenance matters beyond the letter of the constraint is the user's call, not verifiable here.
- **Fix B**: Trim to a ~30-line minimal logger setup (stderr handler + redaction + verbose level), dropping the JSON formatter, the color formatter, and the dictConfig machinery.
  - Strength: Removes all four sub-issues at once and cuts the origin-repo lineage to nothing recognizable.
  - Tradeoff: Loses working, tested-by-use code for a stylistic win.
  - Confidence: MEDIUM — the color formatter's TTY check is genuinely useful for local dev.
  - Blind spot: Haven't checked whether structured JSON logs are wanted for a later observability step.
- **Decision**: FIXED via Fix A — `_JsonFormatter` and the `json_format` parameter removed; `configure_logging` now uses `copy.deepcopy(LOGGING_CONFIG)` (repeated calls verified); plan amended — `logging_config.py` + `py.typed` added to Proposed Structure, Phase 3 retitled "github_diff.py + logging_config.py" with a contract paragraph, and new Progress item 3.2 for the Phase 3 tests.

### F5 — Zero tests on the redaction filter and on github_diff.py; _file_header's rename direction is unasserted

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: `tests/test_diff_parser.py` (the repo's only test file)
- **Detail**: The existing 8 tests are good — line numbers are hand-computed and independently confirmed against the fixture (`modified.py` added `[2,3]` / removed `[2]`; `new_file.py` `[1,2,3]`; `to_remove.py` removed `[1,2]`; `new_name.py` added `[2]` / removed `[2]`). Nothing is tautological, and despite its name the binary test asserts the placeholder string, so it genuinely discriminates the binary branch. The gaps: (1) `_RedactionFilter` is the repo's only security control and has zero coverage — a regex typo silently disables it, and F1 makes this urgent; (2) `github_diff.py` has zero tests — the three-way error classification, argv construction (`-R`, repeated `-e`), and the `find_repo_root` degradation path are all easily testable with a mocked `subprocess.run`; (3) `_file_header` is completely unverified — both `build_diff_context` tests assert only `f.path in text` and `f.hunks_text in text`, so swapping `source_path` and `path` on line 98 would fail no test, and that arrow is what tells the model which way a rename went.
- **Fix**: Add `tests/test_logging_config.py` (redaction of each token format, including the exception path from F1), `tests/test_github_diff.py` (mocked subprocess: argv assembly, three error branches, `find_repo_root` fallback), and a `_file_header` assertion on the `"old -> new (renamed)"` string.
  - Strength: Covers the one security control and the one subprocess boundary, both of which Phase 5 and 8 build directly on top of.
  - Tradeoff: ~100 lines of test code before Phase 4 starts.
  - Confidence: HIGH — verified each gap by reading the assertions.
  - Blind spot: None significant.
- **Decision**: SKIPPED — part (3), the `_file_header` rename-direction assertion, landed anyway under F3. Parts (1) and (2) remain owed; tracked as unchecked Progress item 3.2 in `plan.md` and queued in `follow-ups/review-fixes.md`.

### F6 — No timeout on either subprocess call

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: `src/pr_review_agent/github_diff.py:37-42,167-173`
- **Detail**: Neither `subprocess.run` passes `timeout=`. A `gh` call stalled on a network read hangs until the GitHub Actions job-level timeout (6h default) burns — on every consumer's runner, since this ships as a reusable action.
- **Fix**: Add `timeout=120` (gh) / `timeout=30` (git) and catch `subprocess.TimeoutExpired` — note it is NOT an `OSError` subclass, so the existing `except OSError` will not catch it.
- **Decision**: FIXED (root cause) — the `gh` subprocess call no longer exists; the GitHub transport moved to `githubkit`, whose client carries `timeout=60`. The surviving `git` subprocess (now the shared `_git_output` helper) has `timeout=30` and catches `subprocess.TimeoutExpired` alongside `OSError`, exactly as the finding noted was required.

### F7 — Unguarded json.loads and dict indexing in get_pr_metadata

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: `src/pr_review_agent/github_diff.py:101,103,111`
- **Detail**: `json.loads(raw)` and `data["files"]` / `f["path"]` are unprotected, while the docstring declares `Raises: GhCommandError` only. A `gh` deprecation notice on stdout or an unexpected shape crashes with a raw traceback instead of the module's own error type — defeating Phase 8's exit-`2` mapping for exactly this class of failure.
- **Fix**: Wrap in `try/except (json.JSONDecodeError, KeyError, TypeError)` and re-raise as `GhCommandError` with the offending output.
- **Decision**: FIXED (root cause) — there is no `json.loads` of CLI stdout any more. githubkit returns validated pydantic models (`pull_request.number`, `.base.ref`, …) instead of raw dict indexing, and every failure surfaces as `RequestFailed`/`RequestTimeout`/`RequestError`, mapped to `GitHubApiError` by HTTP status. Phase 8's exit-`2` contract is preserved (the exception type is renamed in `plan.md`).

### F8 — Literal→Enum refactor is undocumented, and its correctness rests on StrEnum with nothing pinning it

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence
- **Location**: `src/pr_review_agent/models.py:7-27` (commit `4972799`)
- **Detail**: Phase 1 specifies `Literal[...]` on five fields; commit `4972799` replaced them with `StrEnum`s. The refactor is benign and arguably better — verified by execution: `json.dumps(asdict(finding))` emits plain `"RIGHT"` / `"blocker"`, not `"DiffSide.RIGHT"`, so Phase 6's `write_json` and Phase 7's POST payload will be correct with no `.value` calls; `DiffSide("bogus")` gives Phase 5's `@tool` handler free validation-with-message; all wire strings match character-for-character (`LEFT`/`RIGHT`, `APPROVE`/`REQUEST_CHANGES`/`COMMENT`, `blocker`/`warning`/`nit`, `pass`/`fail`/`not_applicable`); and a shared `ReviewEvent` structurally guarantees the plan's "same casing, no translation needed" instead of relying on two Literals staying in sync. The risk is that this correctness rests *entirely* on `StrEnum` — a later "cleanup" to `enum.Enum` breaks Phase 6 and Phase 7 silently, with no type error, and no test would catch it.
- **Fix**: Update Phase 1's text to describe the Enums, and add a test asserting `json.dumps(asdict(Finding(...)))` contains `"side": "RIGHT"` — that pins `StrEnum` by behavior, not comment.
- **Decision**: FIXED — Phase 1 rewritten around the four `StrEnum`s with an explicit "this is load-bearing" paragraph; new `tests/test_models.py` (4 tests) pins serialization, unknown-value rejection, and the shared `ReviewEvent`; new Progress item 1.2. Confirmed the pin discriminates: a plain `enum.Enum` serializes to `"DiffSide.RIGHT"`.

### F9 — Quality gates are absent today and pre-set to miss docstrings and break on already-committed code

- **Severity**: 💡 OBSERVATION
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Pattern Consistency
- **Location**: `pyproject.toml`; `src/pr_review_agent/diff_parser.py:55,57`
- **Detail**: Three converging issues, none a Phase 0-3 violation on its own (Phase 9 owns ruff/pyright config) but together meaning the project's stated rules are currently unenforced and one is already broken. (1) `uv run ruff check` and `uv run pyright` both fail with "program not found" — the dev group holds only pytest; lint runs only via the pre-commit hook (which passes), and pyright has never run against this code. (2) Five private functions have no Google-style docstring, against the standing project rule: `_strip_vcs_prefix` (`diff_parser.py:26`), `_changed_file_from_patched_file` (`:32`), `_file_header` (`:96`), `_file_block` (`:106`), and `_run_gh` (`github_diff.py:27`) — the last being the most complex function in the repo and the sole source of the `GhCommandError` contract three public docstrings advertise; Phase 9's planned `ignore = ["D1"]` disables exactly the missing-docstring rules, so ruff will never catch these. (3) `diff_parser.py:55,57` append `line.target_line_no` / `line.source_line_no` (typed `Optional[int]` by unidiff, which ships `py.typed` — confirmed) into `list[int]`; under Phase 9's planned `typeCheckingMode = "basic"` this is a `reportArgumentType` error, so gate 9.1 will fail on Phase 2 code the moment it is first run.
- **Fix**: Add ruff+pyright to the dev group now rather than at Phase 9, narrow `ignore = ["D1"]` to just `["D107","D105"]` so the docstring rule is actually enforced, write the five missing docstrings, and narrow the two unidiff line numbers with a walrus guard.
- **Decision**: FIXED — `ruff` + `pyright` added to the dev group; `[tool.ruff.lint]` / `[tool.pyright]` config written with `ignore = ["D107","D105"]`; 11 missing class docstrings and 4 private-function docstrings written; the two `Optional[int]` line numbers narrowed with walrus guards. All four gates now pass: `ruff check`, `ruff format --check`, `pyright` (0 errors — first run ever), `pytest` (26 passed). Two things learned and recorded in Phase 9: ruff's `D1xx` never covers underscore-prefixed functions, and `unidiff` must be imported from `unidiff.constants`/`unidiff.patch` to satisfy `reportPrivateImportUsage`.

### F10 — Plan self-contradicts on git push

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Scope Discipline
- **Location**: `plan.md` "What We're NOT Doing" vs. Phase 3 verification
- **Detail**: `git status -sb` shows `main...origin/main` in sync and `origin/phase3-github-diff-verification-test` exists — work has been pushed. "What We're NOT Doing" says "No `git push`/tagging as part of this plan's execution." But Phase 3's verification step *requires* "a real PR in this repo (open a throwaway PR during Phase 3 if none exists yet)", which cannot happen without a push. The implementer followed the phase; the guardrail is what's wrong. No tags exist, so the release half of the rule is intact.
- **Fix**: Narrow the guardrail to "no tagging / no release push" and note that verification pushes are expected.
- **Decision**: FIXED — guardrail narrowed to "no release tagging"; verification pushes (feature branches, throwaway PRs) are now explicitly expected, with tags/releases/Marketplace listings still off-limits.

## Not findings — verified clean

- **No command injection.** Both `subprocess.run` call sites use argument lists; no `shell=True` anywhere in the tree; `pr_number` is `str()`-coerced from an `int` parameter. Residual (low): a malicious `repo` input could redirect the review at a different repository — a `^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$` validation is cheap insurance.
- **No live secret leak in the logging path.** Nothing dumps `os.environ`; `github_diff.py:35` logs argv only, `:71` logs a byte count only, never stdout. `configure_logging(verbose=True)` raises only the `pr_review_agent` logger to DEBUG and leaves root at WARNING, so a future SDK's DEBUG request logging stays suppressed. Good design.
- **No import-time logging configuration** — `dictConfig` is called only inside `configure_logging()`.
- **Consistent logging across all modules** — every module uses `logging.getLogger(__name__)`; not one `print()` or `sys.stderr.write()` in `src/`.
- **`.gitignore`, `.python-version`, `.pre-commit-config.yaml`, `README.md` stub** all match Phase 0 exactly. `pyproject.toml` uses `uv_build` rather than Phase 9's hatchling, which Phase 0's own "via `uv init --lib`" wording sanctions.
- **`sample.diff` is hand-authored here** — fake blob hashes (`1111111`, `7777777`) confirm no other repo's history was involved, satisfying the standalone constraint. All five declared cases present, including a real `GIT binary patch` block.
- **Phase 3's `gh` flags verified against the installed `gh 2.93.0`** — `-e/--exclude` and `-R` are both real flags on `gh pr diff`.
- **`find_repo_root` degradation is exactly as planned** — never raises; warns and returns `Path.cwd()` on missing `git`, `OSError`, and non-zero exit.

## Minor note not worth a finding

`.pre-commit-config.yaml`'s `trailing-whitespace` hook runs on `tests/fixtures/sample.diff`. In a real unified diff an empty context line is `" "` (single space); the hook has already stripped one to `""` (line 10). `unidiff` tolerates it and all tests pass, but the fixture is no longer byte-faithful to real `gh pr diff --patch` output, which is its stated purpose — and any future fixture with blank context lines will be silently corrupted on commit. Consider `exclude: ^tests/fixtures/` on the whitespace/EOF hooks.
