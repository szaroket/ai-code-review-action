# PR Review Action — Implementation Plan

## Overview

Build a reusable, generic **GitHub Action** (`ai-code-review-action`, this
repo) that runs a Claude Agent SDK agentic loop reviewing a GitHub PR's diff
against *whatever repository rules and review criteria the consuming
workflow points it at* — not hardcoded to any one target repo. Locally the
underlying CLI defaults to dry-run (console/JSON/markdown output, nothing
posted anywhere); a consuming workflow that passes `publish: true` gets real
inline PR comments posted via the GitHub reviews API.

This plan originated as a single-repo tool (`tools/pr-review-agent/` inside
another repo, reviewing that repo's own `AGENTS.md`). It was generalized and
moved here once we decided the tool should be consumable by
*any* repo's workflow via `uses: szaroket/ai-code-review-action@<ref>` (or a
subdirectory reference), without requiring the calling workflow to check out
two repos — a composite action's steps already run inside the *caller's*
already-checked-out workspace, so file inputs (rules file, criteria file,
lessons file) are just paths the caller supplies, resolved against its own
checkout.

Guiding principle, unchanged from the original brief: as much as possible
stays deterministic Python (diff fetching, diff parsing, prompt
construction, output writing, comment publishing); the agent's only job is
the actual review judgment, plus optional read-only repo exploration
(Read/Grep/Glob) for context around changed lines.

**Standalone constraint (user directive — binding on every phase):** this
repo must not depend on any other repository — not in its code, not in its
tests, not in its fixtures, not in its verification steps. Other repos
*consume* the action; none participate in building or validating it. Where
this plan names the repo it originated in, that is **history of how the
design got here**, never a dependency: no phase, test, or success criterion
below requires that repo to exist.

**Scope restriction is now fully generic and optional:** the original plan
hardcoded a `frontend`/`backend` scope filter specific to the single repo it
was born in. That's gone — `scope-dirs` is now an optional input with **no
default**; if omitted, every changed file in the diff is in scope. A consumer
that wants directory-scoped review passes e.g. `scope-dirs: frontend,backend`
itself.

## Current State Analysis

**This repo's actual current state** (verified directly, not assumed):

- Already `git init`'d, with `origin` already configured and tracked:
  `https://github.com/szaroket/ai-code-review-action.git`, branch `main`,
  one commit ("Initial commit"). No further remote setup needed.
- `.claude/` (the 10xDevs AI Toolkit — skills, commands, prompts) is present
  on disk, **untracked**, and already covered by the ignore rule at
  `.gitignore:243` (verified: `git ls-tree -r --name-only HEAD` lists
  `.gitignore` only; `git ls-files .claude` returns nothing). Nothing to do
  about it.
- `.gitignore` exists (comprehensive Python template) but is **mid-edit,
  uncommitted**, and was evidently copy-pasted from `10xDevs-Project`: it
  still carries that repo's frontend-specific cruft (`node_modules/`,
  `dist-ssr/`, `playwright-report/`, `test-results/`, `.playwright/`,
  `frontend/e2e/.auth/`, an `!frontend/src/lib/` un-ignore exception) that
  doesn't apply here — this is a Python-only repo. It also already adds
  `.claude/` to the ignore list, which is correct and already consistent
  with reality (see the `.claude/` bullet above). Phase 0's only job here is
  trimming the frontend-specific lines.
- No `README.md`, `pyproject.toml`, `.python-version`,
  `.pre-commit-config.yaml`, `src/`, or `.github/workflows/` yet — this is a
  genuinely fresh repo for all of that.
- `gh` CLI is installed and authenticated in this environment (`gh 2.93.0`,
  logged in as `szaroket` with `repo` scope). **No longer a runtime
  dependency** since the transport moved to `githubkit` — but still the
  easiest way to mint a local token: `export GH_TOKEN=$(gh auth token)`.
- Prior research: `context/changes/ai-code-review/first-research.md`
  (moved into this repo along with this plan) verified the relevant Claude
  Agent SDK facts against official docs — `query()`/`ClaudeSDKClient`,
  `ClaudeAgentOptions` fields, `permission_mode="dontAsk"` +
  `allowed_tools`/`disallowed_tools` for a read-only lockdown (never
  `bypassPermissions`), the `@tool`/`create_sdk_mcp_server` pattern, and the
  GitHub reviews POST payload shape (`{path, line, side, body}`) the
  `Finding` model is built to match. These facts are about the SDK and the
  GitHub API only — no repo-specific content — so they carry over unchanged.
- Confirmed behavior of GitHub's patch output (observed live during earlier
  research): a PR with binary assets emits `GIT binary patch` blocks, not the
  friendlier `Binary files ... differ` summary — `diff_parser.py` must handle
  this defensively (Phase 2). This is a property of the patch format itself,
  so it held when the transport moved from `gh pr diff --patch` to the
  `application/vnd.github.diff` media type. Phase 2's fixture reproducing it
  is **hand-authored in this repo**, never copied from another repository's
  history.
- **What did *not* carry over, because it was repo-specific content baked
  into code:** the earlier plan's `build_review_checklist` distilled the
  origin repo's `AGENTS.md` into hardcoded named categories ("Backend
  Layer Boundaries", "Frontend Structure Rules", referencing FastAPI/React
  specifics and lesson IDs L-002–L-006 verbatim). That can't live in a
  generic action's code — see the reworked Phase 4 and the "No repo-specific
  hardcoded rule categories" entry in "What We're NOT Doing."

## Prerequisite: Review Criteria Discovery Session

**Still a manual step, but now per-consumer, not a one-time thing done here.**
Any repo that wants to use this action — including this repo itself, for
its own dogfood workflow (Phase 12) — must first run a **separate, fresh
conversation** (not a planning session) about what good code review and PR
acceptance look like *for that repo's stack*, converging on a small, focused
set of **concrete criteria**. *(Originally scoped to exactly five; narrowed
during Phase 8 implementation, user decision 2026-07-31 — see the "Review
criteria — no fixed count" Key Decision below. How many criteria make sense
is each repo's own call, not a number this tool should impose.)* AI can help
surface ecosystem-typical practices, but the requirements predate the tool;
the user's judgment is the essential input, the AI's job is to help
structure it.

Output: a markdown file (path is the caller's choice, passed as the
`criteria-file` input) shaped like:

```markdown
# Review Criteria

## <Criterion 1 name>
<1-3 sentences: what this checks and why it matters for this repo>

## <Criterion 2 name>
...

(one `##` section per criterion; however many the discovery session converges on)
```

`load_review_criteria` (Phase 4) parses `##` headings as names and raises a
clear error only if there are none at all — this is what enforces the
discovery session actually happened, for whichever repo is consuming the
action, without dictating how many criteria that session must produce.

**For this repo's own dogfooding** (Phase 12), we run this session ourselves
and commit our own `context/foundation/review-criteria.md`. That path is this
repo's own convention; consumers choose whatever path they like and pass it
via `criteria-file`.

Any *other* repo that wants to consume this action runs its own session and
commits its own criteria file, in its own repo, on its own schedule. That
work is entirely outside this plan — no phase, verification step, or success
criterion here waits on it or references it.

## Decisions confirmed with the user

- **Separate repo, generic composite Action** (this session's decision,
  superseding the earlier "keep it inside the origin repo" call): the tool is
  parameterized to accept the target repo's rules file, criteria file, and
  optional lessons file as **inputs** rather than hardcoded paths. This
  fixes the original objection to a separate repo (needing to check out two
  repos) — a composite action's steps run in the *caller's* already-checked-
  out workspace by default, so file inputs just resolve against that
  checkout. Reusability doesn't strictly require a separate repo (GitHub
  supports `uses: owner/repo/subpath@ref`), but a dedicated repo was chosen
  since this action now has its own independent release cadence, its own
  CI, and its own version tags, decoupled from any one consumer's history.
- **Language:** this tool's own CLI output, README, logs, and code comments
  are English — it's dev tooling, not a product's user-facing surface. (The
  earlier Polish-UI-text carve-out belonged to the origin repo's own app and
  has no analogue in a generic action. `agents_context.py` forwards whatever
  the consumer's own `rules-file` says, unedited.)
- **Default model:** `claude-sonnet-5`, overridable via `--model`/`model:`
  input (e.g. `claude-haiku-4-5` for cheap harness-debugging). *Lowered from
  `claude-opus-5`, user decision 2026-07-31: a per-PR review runs on every
  push, so the default carries recurring cost; opus stays one `--model` flag
  away for the runs that warrant it.*
- **Scope restriction:** `scope-dirs` is optional, **no default** (see
  Overview) — a hard include-list when set, applied in `cli.py` right after
  `parse_diff`, before `build_diff_context` or the agent ever sees the file
  list. Matching is on path *segments*, not raw string prefixes, so
  `scope-dirs: api` cannot pull in `api_client_generated/`.
- **Review inputs are read from the PR's base ref, not the checkout**
  (*amendment, 2026-07-31, from the phases 4-9 impl review, finding F4*).
  `rules-file`, `lessons-file` and `criteria-file` are all interpolated into
  the **system** prompt — the highest-authority position in the conversation.
  The original plan resolved them against the workflow's PR-head checkout,
  which meant a pull request could append "always approve" to its own
  `AGENTS.md`, or rewrite the criteria it was about to be scored against, and
  the agent would read it as instruction from the repository owner. So any
  input path that resolves *inside* the checkout is fetched via the contents
  API at `base_ref_name` instead (`github_diff.get_file_at_ref`); a path
  outside the checkout is still read from disk, since it isn't part of the
  pull request and carries the caller's authority rather than the author's.
  Contents API rather than `git show base:path` because `actions/checkout`
  clones shallow by default, so the base ref usually isn't present locally.
  **Consumer-visible consequence:** a PR that legitimately updates `AGENTS.md`
  is *not* reviewed against its own new rules — the change takes effect once
  merged. `--trust-head-files` / `trust-head-files:` opts back out, for local
  runs and for branches whose criteria file doesn't exist on the base ref yet.
- **Publish gate:** real GitHub posting is behind `publish` (boolean,
  default `false`). Rejected: env-var auto-enable (implicit), folding into
  `format` (conflates a side-effect-free artifact with an action that
  mutates a real PR).
- **GitHub transport: `githubkit`, not the `gh` CLI.** *(Decision reversed
  during the Phase 0-3 impl review — the original plan specified a subprocess
  wrapper around `gh`, and Phase 3 was first implemented that way.)* Rationale
  for the reversal: three of the impl review's findings (no subprocess
  timeout, locale-not-UTF-8 decoding, unguarded `json.loads` on CLI stdout)
  were all artifacts of the subprocess boundary rather than of the problem
  being solved. `githubkit` is generated from GitHub's OpenAPI spec and is
  fully typed, so it pays for itself under Phase 9's `pyright` gate; it
  carries its own HTTP timeout; and it removes the "is `gh` installed and
  authenticated" failure class entirely.
  - The **diff** is fetched as the raw patch via
    `GET /repos/{owner}/{repo}/pulls/{n}` with
    `Accept: application/vnd.github.diff` (githubkit's public
    `GitHub.request`, not a private requester). Deliberately **not** built
    from `pulls.list_files`, whose per-file `patch` field GitHub omits for
    large files and caps at 3000 entries — silently, exactly where a review
    matters most.
  - **Path exclusion moves client-side.** The REST API has no equivalent of
    `gh pr diff -e <glob>`, so `diff_parser.exclude_paths(files, globs)`
    filters after parsing. `get_pr_diff` no longer takes `exclude_globs`.
  - **Cost accepted:** local runs now need `GH_TOKEN`/`GITHUB_TOKEN` in the
    environment (`export GH_TOKEN=$(gh auth token)`), where the `gh` CLI
    previously reused an interactive `gh auth login` session.
  - `git` **stays** on subprocess for `find_repo_root` and `origin`-remote
    detection — that is local VCS state, not a GitHub API concern.
- **Post method:** `pulls.create_review` with
  `{event, body, comments: [{path, line, side, body}]}` — gives real per-line
  inline comments anchored to the diff (the whole point). Rejected:
  `gh pr review --body <summary>` (loses inline anchoring), and a
  summary-only comment.
- **Auth tokens are consumer-supplied inputs**, not hardcoded: `github-token`
  input, mapped to `GH_TOKEN` for the underlying SDK/githubkit calls. A
  consuming workflow typically passes `github-token: ${{ github.token }}`
  (its own default `GITHUB_TOKEN`, `permissions: pull-requests: write`).
  Known limitation, inherited by every consumer: a `pull_request` event
  from a **fork** gets a read-only default token, so `publish: true` will
  fail there regardless of which repo is consuming the action.
- **Anthropic auth supports a direct key *or* an Anthropic-API-compatible
  gateway — OpenRouter, concretely.** *(Decision made 2026-07-31, mid-Phase-5,
  at the user's request: their only funded billing account is OpenRouter, not
  the Anthropic Console, and they want that to be the action's supported path
  going forward, not just a local dev-time workaround.)* Three inputs instead
  of one required key: `anthropic-api-key` (optional now, was required),
  `anthropic-base-url`, `anthropic-auth-token` — mapped to `ANTHROPIC_API_KEY`
  / `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` respectively. This is
  **zero-code-change** in `review_agent.py`: verified directly against the
  installed `claude-agent-sdk` (0.2.128) source
  (`_internal/transport/subprocess_cli.py`) that the bundled Claude Code CLI
  subprocess is launched with `inherited_env = os.environ` merged under
  `ClaudeAgentOptions.env` — so whichever of these three vars the *process*
  running `pr-review-agent` sees, the CLI subprocess sees too, with no SDK
  option carrying an API key or base URL directly. The three-env-var pattern
  (`ANTHROPIC_BASE_URL=https://openrouter.ai/api`,
  `ANTHROPIC_AUTH_TOKEN=<key>`, `ANTHROPIC_API_KEY=""`) is OpenRouter's own
  documented Claude-Code-compatibility path, not a hack.
  A consumer that wants direct Anthropic billing still passes only
  `anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}`, unchanged from
  before. This repo's own dogfood workflow (Phase 12) uses the OpenRouter
  path with a repo secret named `OPENROUTER_API_KEY`. Trade-off accepted:
  `anthropic-api-key` moving from `required: true` to optional means a
  consumer who sets none of the three inputs no longer gets an immediate
  GitHub Actions input-validation error — the failure surfaces slightly
  later, as an auth error from the Claude Code CLI subprocess itself once the
  agent step runs.
- **Post-failure handling:** local artifacts are written *before* attempting
  the post; a failed post exits a distinct code (`6`) rather than silently
  succeeding. A consuming workflow uploads `review-output/` via
  `actions/upload-artifact@v4` with `if: always()` so findings survive a
  red job — the standard `upload-artifact` pattern for preserving output
  from a failing job.
- **Review criteria — sourcing methodology and no fixed count.** Sourced via
  a per-consumer discovery session (see "Prerequisite" above), not
  mechanically distilled by this tool's code from any one repo's docs.
  `criteria-file` is a **required input with no default** — every repo
  names/locates this file differently. *(Narrowed during Phase 8
  implementation, user decision 2026-07-31: the file originally had to
  contain exactly five `##` criteria; that count is now the consumer's own
  choice — `load_review_criteria` only requires at least one. Reasoning: "it
  is specific per repo," and a fixed number one repo happened to converge on
  has no reason to generalize to every consumer's stack. `criteria-file`
  itself stays required — a review still needs *some* structured criteria to
  score against, just not exactly five of them.)*
- **Criterion score scale:** `pass` / `fail` / `not_applicable` per
  criterion, plus one `overall_verdict` (`APPROVE`/`REQUEST_CHANGES`/
  `COMMENT`, matching `ReviewOutput.event`'s casing directly). Rejected: a
  `partial` state (too fuzzy for a downstream gate) and a 1-5 numeric scale
  (no clear use this iteration, harder to score consistently run-to-run).
- **Verdict vs. exit code:** exit code stays scoped to "did the tool
  complete its job" — it does not fail based on the verdict's *content*
  (`REQUEST_CHANGES` is not itself a failure). It does fail (exit `5`) if
  the agent never produces a *valid* verdict at all — a run-completeness
  failure, same category as never calling `submit_finding`.
- **Rules-file / lessons-file content is injected verbatim, not distilled
  into hardcoded categories.** The earlier single-repo design parsed that
  one repo's `AGENTS.md` into named categories ("Backend Layer Boundaries",
  etc.) baked into this tool's code. That's repo-specific and can't survive
  genericization — see Phase 4 and "What We're NOT Doing."
- **Rules-file is optional, not a hard requirement.** *(Narrowed during
  Phase 8 implementation, user decision 2026-07-31, superseding Phase 4's
  original "hard requirement" framing.)* Not every consumer repo has an
  `AGENTS.md` (or whatever `--rules-file` points at). `load_rules_file` now
  behaves like `load_lessons_file` — missing is a warning, not a
  `FileNotFoundError`, and `build_system_prompt` omits the "Repository
  Rules" section entirely when there's no content. `criteria-file` remains
  the one file whose absence is fatal (exit `3`); a review can proceed with
  nothing but the loaded criteria and the diff itself.

## What We're NOT Doing

- No repo-specific hardcoded rule categories (e.g. "Backend Layer
  Boundaries") baked into this action's code — `rules-file`/`lessons-file`
  content is injected into the system prompt as provided, under generic
  headings. Distilling a specific repo's rules into named categories is
  either done in that repo's own docs, or left to the model's judgment when
  reading the raw text.
- **No work in, and no dependency on, any other repository** — no config
  backfills elsewhere, no consumer workflow edits, no fixtures or test data
  sourced from another repo, no verification step that needs a second
  checkout. Every phase, test, and success criterion below is satisfiable
  with this repo alone. (This repo's own `.pre-commit-config.yaml`, Phase 0,
  is written fresh with no scoping gap to begin with.)
- No unit tests with mocked SDK responses for `review_agent.py` this
  iteration (Phase 5) — verification is a real end-to-end run instead.
- No support for fork PRs on any consumer — inherited token limitation, not
  fixed with a PAT this iteration.
- No dedup/update of a prior review on re-run — a new commit posts a *new*
  review rather than editing the previous one.
- No branch-protection / required-check wiring on any consumer — this
  action's job is advisory by default; making it a required check is each
  consumer's own repo-settings decision.
- No automatic consistency check between per-finding `severity` and the
  agent's own `overall_verdict` — trusted to the model's own internal
  consistency this iteration.
- No enforcement/gating on the verdict's content — see "Verdict vs. exit
  code" above.
- No LICENSE file chosen this iteration, and no GitHub Marketplace listing
  — this action is consumed via `uses: szaroket/ai-code-review-action@ref`
  by repos this user controls, not published for public discovery yet. Pick
  a license before any public Marketplace listing.
- No **release tagging** as part of this plan's execution — cutting a `@v1`
  (or any tag consumers can reference) is an explicit, deliberate step the
  user takes when ready, not automated by any phase here.
  *(Narrowed during the Phase 0-3 impl review, F10: this originally read "no
  `git push`/tagging", which contradicted Phase 3's own verification step —
  verifying against a real PR in this repo is impossible without pushing a
  branch. The implementer followed the phase; the guardrail was wrong.)*
  **Verification pushes are expected**: feature branches and throwaway PRs
  opened to exercise a phase are part of the work. What stays off-limits is
  anything a consumer could pin to — tags, releases, and Marketplace
  listings.
- No consumer-side workflow wiring as part of this plan — adopting the
  action is a downstream step each consuming repo takes in its *own*
  repository, after this one is tagged.

## Proposed Structure

```
ai-code-review-action/              (this repo's root — the action itself)
  .gitignore                        # exists, needs trimming (Phase 0)
  .python-version                   # "3.13"
  README.md
  pyproject.toml
  uv.lock                           # committed, NOT ignored (Phase 9)
  .pre-commit-config.yaml           # whole-repo scope, no gap this time
  action.yml                        # composite action definition (Phase 10)
  src/pr_review_agent/
    __init__.py
    py.typed                        # PEP 561 marker — this package ships types
    cli.py                          # argparse, orchestration, exit codes
    logging_config.py               # stderr logging, UTC, secret redaction (Phase 3)
    github_diff.py                  # githubkit client: PR metadata + raw diff
    diff_parser.py                  # unified diff -> changed-lines model
    agents_context.py               # loads rules/lessons/criteria files, builds system prompt
    review_agent.py                 # ClaudeAgentOptions, @tool, query() loop
    models.py                       # Finding / ReviewOutput / Criterion / ReviewVerdict
    output.py                       # console + JSON + markdown writers
    github_publish.py               # real PR-review posting via pulls.create_review
  tests/
    test_diff_parser.py
    test_logging_config.py          # redaction filter + traceback path (Phase 3)
    test_github_diff.py             # mocked subprocess: argv, error branches (Phase 3)
    test_cli.py                     # scope-filter unit tests
    test_output.py
    fixtures/sample.diff
  .github/workflows/
    ci.yml                          # this repo's own lint/typecheck/test (Phase 11)
    self-test.yml                   # dogfood: use the action on this repo's own PRs (Phase 12)
  context/
    foundation/
      README.md
      review-criteria.md            # this repo's own review criteria (Phase 12 prerequisite)
    changes/ai-code-review/
      change.md, first-research.md, plan.md   # this plan, moved here
    archive/README.md
  .claude/                          # present on disk, untracked (gitignored) — 10xDevs AI Toolkit
```

Own `pyproject.toml` (uv, Python 3.13). `action.yml` at the repo root is
what makes `uses: szaroket/ai-code-review-action@<ref>` resolvable from any
other repo's workflow.

## Implementation Approach

Build bottom-up: repo/environment setup first (this is a fresh repo), then
shared models, then the correctness-critical diff parser, then the
plain-code GitHub fetch layer, then the now-generic prompt-building layer,
then the agent loop, then output formatting, then the publish layer, then
the CLI wiring it together, then the packaging layer (`pyproject.toml`,
`action.yml`), then this repo's own CI, then a dogfood workflow that
exercises the action against this repo's own PRs — which is also the
simplest, single-repo way to produce the course assignment's three required
pieces of evidence (visible pipeline job, live logs, real PR comment)
without involving any second repository.

## Phase 0: Repo Environment Setup

This is the one phase that's about the repo, not the tool.

1. **`.gitignore` cleanup:** remove the frontend-specific lines carried over
   from the file this one was copy-pasted from (`node_modules/`,
   `dist-ssr/`, `*.local`, `playwright-report/`, `test-results/`,
   `.playwright/`, `frontend/e2e/.auth/`, the `!frontend/src/lib/`
   exception) — none of it applies to a Python-only repo. Keep the generic
   Python template, the editor-directories block, and the `.claude/` rule
   (the local AI toolkit is machine-local tooling, not part of this action's
   source — verified already untracked here).
2. `.python-version` — `3.13`.
3. `pyproject.toml` skeleton via `uv init --lib` (or hand-authored to match
   Phase 9's final shape) — enough for `uv sync` to succeed with an empty
   `src/pr_review_agent/__init__.py`.
4. `.pre-commit-config.yaml` — ruff (lint + format) over the whole repo
   (`src/`, `tests/`), plus generic whitespace/EOF/YAML/TOML hooks. No
   scoping gap this time — everything here is this repo's own code.
5. `README.md` stub — filled in properly once Phase 8/10 exist (usage,
   inputs, setup).
6. Confirm: `uv sync` succeeds; `uv run python -c "import pr_review_agent"`
   works; `pre-commit run --all-files` passes on the (currently empty)
   skeleton.
7. **Not doing in this phase:** no `git add`/`commit`, no remote
   push — those are the user's explicit calls once the skeleton is real
   (see "What We're NOT Doing").

## Phase 1: models.py

Everything else depends on this.

**Closed value sets are `StrEnum`, not `Literal`.** *(The plan originally
specified `Literal[...]`; commit `4972799` refactored to enums and the impl
review confirmed the change is correct and better.)* Four enums —
`DiffSide` (`LEFT`/`RIGHT`), `Severity` (`blocker`/`warning`/`nit`),
`CriterionResult` (`pass`/`fail`/`not_applicable`), `ReviewEvent`
(`APPROVE`/`REQUEST_CHANGES`/`COMMENT`). Each member's value is the exact
wire string, character-for-character.

**`StrEnum` specifically, and that is load-bearing.** Because `StrEnum`
members *are* `str`, `json.dumps(asdict(finding))` emits `"RIGHT"`, not
`"DiffSide.RIGHT"` — so Phase 6's `write_json` and Phase 7's POST payload
need no `.value` calls anywhere. Downgrading these to a plain `enum.Enum`
would break both phases **silently**, with no type error. Phase 1's tests
pin this by serializing, not by comment.

Enums also give Phase 5's `@tool` handler free validation with a usable
message (`DiffSide("bogus")` raises `ValueError`), and a single shared
`ReviewEvent` structurally guarantees that `ReviewVerdict.overall_verdict`
and `ReviewOutput.event` cannot drift apart — which two separate `Literal`s
could not.

Frozen `Finding` (`path`, `line`, `side: DiffSide`, `severity: Severity`,
`comment`, `rule_reference: str | None`).

Frozen `Criterion` (`name`, `description`) — one per entry loaded from the
consumer's `criteria-file`; frozen `CriterionScore` (`name`,
`score: CriterionResult`, `rationale`); frozen `ReviewVerdict`
(`criteria: list[CriterionScore]`, `overall_verdict: ReviewEvent`).

Mutable `ReviewOutput` (`pr_number`, `event: ReviewEvent`, `summary_body`,
`comments: list[Finding]`, `verdict: ReviewVerdict | None` — `None` on the
exit-`5` path, where Phase 8 builds and writes `ReviewOutput` *before* the
verdict check so the agent's findings survive; `event` then falls back to
`ReviewEvent.COMMENT` per Phase 6).

No pydantic — small, no network/DB boundary needing coercion. No validation
here; validation happens once at the `@tool` handler boundary in Phase 5.

Verify: `uv run python -c "from pr_review_agent.models import Finding, ReviewOutput, Criterion, CriterionScore, ReviewVerdict"` works.

## Phase 2: diff_parser.py + tests

Correctness-critical; build and test in isolation. Adds `unidiff`
dependency.

- `ChangedFile` dataclass: `path`, `is_added`, `is_removed`, `is_renamed`,
  `source_path`, `hunks_text`, `added_line_numbers` (→ `side="RIGHT"`),
  `removed_line_numbers` (→ `side="LEFT"`).
- `parse_diff(diff_text) -> list[ChangedFile]` via `unidiff.PatchSet`.
- `build_diff_context(files, max_chars) -> (text, was_truncated)`: truncate
  by dropping whole low-priority files, never mid-file; always keep the
  full file list.
- **Binary-diff handling:** `GIT binary patch` blocks (confirmed live
  format from GitHub's raw patch output) must not crash the parse. Catch the
  binary case per-file and emit a `ChangedFile` with empty line-number
  lists and `hunks_text = "[binary file, diff not shown]"`.
- **Line-annotated `hunks_text`** *(fixed during Phase 8 manual testing,
  2026-07-31 — see change.md for the full incident)*. Plain `str(hunk)`
  (raw unified-diff text) carries line numbers only in the `@@ -a,b +c,d @@`
  header, forcing the reviewing model to count lines itself — and it
  miscounted by one on a real `--publish` run, submitting a finding for a
  line that didn't exist in the diff at all, which GitHub's reviews API
  rejected with a 422 ("Line could not be resolved") regardless of whether
  the PR was open or closed. `_render_hunk` now prefixes every hunk line
  with its exact `old_line new_line` pair (`.` when a side has no line
  there), so the model reads `line` directly instead of counting;
  `agents_context._SUBMIT_FINDING_CONTRACT` was updated to describe the
  annotation and tell the model to use it verbatim.

**Test plan:** `tests/fixtures/sample.diff` covers 5 cases in one diff: a
modified file (added+removed lines in one hunk), an added file, a renamed
file (with a content tweak), a removed file, and a binary file change (the
real `GIT binary patch` form). Tests: one-changed-file-per-entry count;
correct line numbers on the modified file (hand-computed); added file has
no removed lines; removed file has no added lines; renamed file captures
`source_path`; binary file doesn't crash and yields empty line lists;
`build_diff_context` under-budget returns full text; over-budget drops
whole files (not partial) and still lists all filenames.

Verify: `uv run pytest tests/test_diff_parser.py -v`.

## Phase 3: github_diff.py + logging_config.py

**`logging_config.py` ships alongside** (added during implementation, recorded
here after the fact — see the Phase 0-3 impl review). It exists because every
module from Phase 3 onward needs a log channel that cannot pollute stdout, and
because token redaction has to be in place *before* the first subprocess error
message is rendered. Contract: a single `StreamHandler` on
`ext://sys.stderr`; UTC timestamps; a module-level `redact(text)` shared by
the log filter, the formatters' `formatException`, and `github_diff.py`'s
error paths; `configure_logging(verbose: bool)` — the exact hook Phase 5's
`--verbose` flag calls — and **nothing configured at import time**. Every
module uses `logging.getLogger(__name__)`; no `print()` anywhere in `src/`.
Log format is deliberately not configurable: `--format {console,json,markdown}`
in Phase 8 governs *review output*, not logs.

`py.typed` is added in the same phase so consumers and `pyright` see this
package as typed.

Plain code, no agent. `GitHubApiError(RuntimeError)`; `PullRequestMetadata`
dataclass (adds `state`/`merged` fields — *added during Phase 8 manual
testing, user decision 2026-07-31*, so `cli.py` can skip a closed/merged PR
before fetching its diff); `resolve_repo(repo=None) -> tuple[str, str]`;
`get_pr_metadata(pr_number, repo=None)` via `pulls.get` + paginated
`pulls.list_files`; `get_pr_diff(pr_number, repo=None)` via the raw-patch
media type (see "GitHub transport" under Key Decisions).

**Repository resolution** replaces `gh`'s `-R/--repo`: explicit argument →
`GITHUB_REPOSITORY` (always set by Actions) → the `origin` remote of the
local checkout. A malformed explicit slug is rejected with
`GitHubApiError`; a malformed `GITHUB_REPOSITORY` falls through to
autodetection rather than being fatal. The slug regex also serves as the
input validation the impl review flagged as cheap insurance against a
hostile `repo` input redirecting the review at a different repository.

**Error mapping** replaces the CLI's stderr sniffing with HTTP status:
401/403 → token rejected (with the permissions hint), 404 → PR not found,
other non-2xx → status + redacted body, `RequestTimeout` → timeout message,
`RequestError` → network error. All are `GitHubApiError`, which Phase 8 maps
to exit `2`.

**TLS**: the client is built with `truststore` so it trusts the **OS**
certificate store rather than `certifi`. Without this, corporate proxies and
antivirus TLS interception break the tool on developer machines — verified
here: AVG Antivirus on the dev machine both MITMs the connection (certifi
rejects its root) and exports `SSLKEYLOGFILE` pointing at a device path,
which crashes CPython's OpenSSL on Windows (`no OPENSSL_Applink`).
`_build_ssl_context` therefore also suppresses `SSLKEYLOGFILE` while
building the context — independently correct, since a process holding GitHub
tokens and an Anthropic API key must not write TLS session keys to disk.
**This applies to the Anthropic SDK in Phase 5 too**, which uses `httpx` over
the same stdlib `ssl`.

**`find_repo_root() -> Path` lives here** (this module owns the local-VCS
subprocess role). Implementation: `git rev-parse --show-toplevel` through a
shared `_git_output` helper — explicit `timeout`, `encoding="utf-8"`, and
`errors="replace"`, returning `None` on every failure mode rather than
raising. It is load-bearing in three places — Phase 5's
`ClaudeAgentOptions(cwd=...)`, Phase 5's repo-mismatch guard, and Phase 10's
`--project`-not-`--directory` invariant, where every relative path
(`--rules-file`, `--criteria-file`, `--lessons-file`) must resolve against
the caller's checkout.

Failure behavior: if `git` is absent or the cwd isn't a git checkout, log a
warning to stderr and **fall back to `Path.cwd()`** rather than raising —
the tool can still review a diff fetched over the API without a local
checkout, it just loses repo exploration (the mismatch guard above will
disable Read/Grep/Glob anyway, since it fails in the same conditions).
No new exit code; this is a degradation, not a hard failure.

Verify: manually against a real PR in **this** repo (open a throwaway PR
during Phase 3 if none exists yet). No other repository is involved in any
verification step of this plan; this action is standalone, and other repos
consume it rather than participate in its tests.

## Phase 4: agents_context.py

`load_rules_file(path) -> str | None` reads the file at the given path,
returns `None` if missing, logging a warning (soft dependency — *narrowed
during Phase 8 implementation, user decision 2026-07-31, from the originally
planned hard requirement*: not every consumer repo has an `AGENTS.md`);
`load_lessons_file(path) -> str | None` reads the given path, returns `None`
if the path wasn't provided or the file is missing (soft dependency, always
was). `load_review_criteria(path) -> list[Criterion]` parses `##` headings
from the given path as described in "Prerequisite" above; raises
`FileNotFoundError` if missing, `InvalidReviewCriteriaError(ValueError)` if
there are zero `##` headings (*narrowed the same day from "exactly five" —
see the "Review criteria — no fixed count" Key Decision*).

**No hardcoded per-stack categories this time** (the key change from the
original single-repo design): `rules_content` and
`lessons_content` are injected into the prompt close to verbatim, under
generic headings — no attempt to parse them into named categories like
"Backend Layer Boundaries," since that structure doesn't generalize across
arbitrary consumer repos.

`build_system_prompt(rules_content, lessons_content, criteria:
list[Criterion])` composes, in order: role statement (review ONLY the
diff, never fix code, never modify files) → a **"Review Criteria"** section
listing the loaded `criteria` verbatim (name + description each), with an
instruction that these are the axis `submit_review_verdict` must score, by
exact name → (if present) a **"Repository Rules"** section with
`rules_content` verbatim → (if present) an **"Additional Lessons /
Pitfalls"** section with `lessons_content` verbatim → tool-usage guidance
(Read/Grep/Glob only for
context strictly around changed lines) → `submit_finding` contract (one
call per issue, cite something from "Repository Rules"/"Additional Lessons"
in `rule_reference` when possible) → `submit_review_verdict` contract: call
exactly once, after all `submit_finding` calls, with one `CriterionScore`
per loaded criterion (exact name match) plus one `overall_verdict`.

Verify: `uv run python -c "from pr_review_agent.agents_context import build_system_prompt, load_review_criteria, load_rules_file, load_lessons_file"` works.

## Phase 5: review_agent.py (the actual agent loop)

Adds `claude-agent-sdk` dependency.

- `findings: list[Finding]` collected via closure inside a
  `@tool("submit_finding", ...)` handler — validates `side`/`severity`,
  appends, returns a short confirmation.
- `verdict: ReviewVerdict | None` (starts `None`) collected via closure
  inside a second `@tool("submit_review_verdict", ...)` handler — validates
  the submitted criteria names are an exact set-match against the loaded
  `criteria`, and every score is valid; on success stores the
  `ReviewVerdict`, on failure returns an explanatory error so the model can
  retry.
- `create_sdk_mcp_server(name="reviewer", version="0.1.0",
  tools=[submit_finding, submit_review_verdict])`.
- `ClaudeAgentOptions(system_prompt=..., cwd=repo_root, allowed_tools=
  ["Read","Grep","Glob","mcp__reviewer__submit_finding",
  "mcp__reviewer__submit_review_verdict"], disallowed_tools=["Bash","Write",
  "Edit","NotebookEdit","WebSearch","Task"], permission_mode="dontAsk",
  mcp_servers={"reviewer": server}, model=..., max_turns=...)`. `cwd` is the
  *consumer's* checkout root (see Phase 10's critical `--project` vs
  `--directory` note), never this action's own source location. Never
  `bypassPermissions` (it ignores `allowed_tools`).
- **Repo-mismatch guard.** `cwd` is the local checkout, but the diff comes
  from whatever `--repo` names. In the supported consumption path these are
  the same thing — the caller runs `actions/checkout` on its own repo and
  reviews its own PR. When they diverge, Read/Grep/Glob would silently
  browse a *different codebase* than the one under review, either finding
  nothing or (worse) citing same-named files with different contents in
  `rule_reference`. So `cli.py` compares the resolved `--repo` against the
  local checkout's `origin` remote (reusing Phase 3's `_git_output`, run in
  `repo_root`); **on mismatch it drops `Read`/`Grep`/`Glob` from
  `allowed_tools` for that run** — degrading to a diff-only review — and
  emits a loud stderr warning naming both repos and stating that repo
  exploration is disabled. An undeterminable origin (no remote, not a
  checkout) is treated as a mismatch: exploration off, warning emitted.
- `async for message in query(...)`: log assistant text at `--verbose` to
  stderr (keep stdout clean); capture the final `ResultMessage`'s status.
- **Before writing this phase**, run `uv run python -c "import
  claude_agent_sdk; help(claude_agent_sdk)"` to lock down the exact `@tool`
  input-schema shape and message-type names.
- Empty findings + `status == "success"` + a **valid** `verdict` collected
  is the only valid clean result. Anything else is a run-completeness
  failure — never a judgment about the verdict's content. *(Amended
  2026-07-31, impl-review finding F7: the two failure kinds were originally
  folded into one exit code, which made them indistinguishable to a
  consuming workflow. They are now reported separately, matching
  `ReviewRunResult`'s own claim that `sdk_success` and `verdict` are
  independently checkable facts.)* A non-success SDK status → exit `4`, the
  same code as an SDK exception, because both are transient and retryable
  (overloaded API, `error_max_turns`). Success with `verdict` still `None`
  — never called, or every attempt failed validation → exit `5`, which a
  consumer reads as advisory.
- No unit tests for this module this iteration (depends on a live SDK/API
  connection) — verification deferred to the end-to-end runs in "Testing
  Strategy / Verification."

## Phase 6: output.py

`deduplicate_findings` (exact `(path, line, comment)` match,
first-occurrence order), `cap_findings(findings, max_findings) -> (kept,
was_capped)`, `build_summary(findings, verdict)` — deterministic summary
built entirely in code: renders the `CriterionScore`s first (e.g.
`"✅ <name>: pass — <rationale>"`), then the findings-count breakdown.
`build_review_output(pr_number, findings, verdict)` — `event` comes
directly from `verdict.overall_verdict` (no severity-rollup heuristic; the
model's own structured verdict is authoritative). When `verdict is None`
(the exit-`5` path in Phase 8, where artifacts are written *before* the
verdict check so findings aren't lost), `event` falls back to `"COMMENT"`
and `build_summary` renders the `"=== INCOMPLETE — NO VERDICT PRODUCED ==="`
banner in place of the criteria breakdown. This fallback exists solely to
make a doomed run's findings persistable — it is never published.

`print_console` (grouped by file, clear `"=== DRY RUN — NOT PUBLISHED TO
GITHUB ==="` banner, criteria breakdown before findings), `write_json`
(`review-output/pr-<n>-<timestamp>.json` — includes a `criteria` array for
local inspection *in addition to* the exact future POST-payload shape
`{"event", "body", "comments": [{"path","line","side","body"}, ...]}`; the
criteria breakdown also lives inside `body` as rendered markdown for the
real POST — see Phase 7), `write_markdown`. All file writes explicit
`encoding="utf-8"`. Default cap 30; when capped, output says so explicitly.

Worth adding light `tests/test_output.py` for the pure functions (dedup,
cap, summary, verdict→event passthrough).

## Phase 7: github_publish.py (real PR-comment posting)

Plain code, no agent — mirrors `github_diff.py`'s githubkit-client style and
reuses its `resolve_repo`, `_client`, and error-mapping helpers.
`GitHubPublishError(RuntimeError)` (distinct from `GitHubApiError`).
`post_review(pr_number, review_output, repo=None) -> None`: serializes to
**exactly** `{"event", "body", "comments": [...]}` — strips the local-only
`criteria` key that `write_json` includes, since GitHub's reviews endpoint
has no schema slot for it, and relies on `body` already containing the
rendered criteria section (built once in `build_summary`, reused here) —
and posts it via `pulls.create_review(owner, repo, pr_number, data=...)`.
Raise `GitHubPublishError` with the mapped HTTP status and redacted response
body on failure; a 403 here almost always means the fork-token limitation
documented under Key Decisions, so say so in the message.

Verify: manually, by publishing a real review to a scratch/test PR (see
Testing Strategy) — no live-API mocking this iteration.

**Actions-token approval restriction found during Phase 12 dogfood testing
(2026-07-31, on PR #10).** GitHub structurally refuses to let the default
Actions `GITHUB_TOKEN` post a review with `event: APPROVE` — a 422 with
`"GitHub Actions is not permitted to approve pull requests"`, independent of
the `pull-requests: write` permission being granted. This isn't a bug in this
tool's token handling (unlike the fork-PR 403 case); it's a hard GitHub
restriction on the Actions token specifically. `post_review` now detects this
exact 422 message and retries once with `event` downgraded to `COMMENT`
rather than losing the findings and summary entirely — the local artifact and
`review_output.event` still record the model's real `APPROVE` verdict
unchanged; only the outgoing payload is downgraded. A consumer using a PAT
instead of the default token would not hit this at all.

## Phase 8: cli.py

`argparse`: `--pr` (required int), `--repo` (now routinely needed, not
optional-in-practice), `--rules-file` (default `"AGENTS.md"` — a common
convention, but overridable since not every repo uses that name; **optional
file** — missing is a warning, not an error, since not every repo has one),
`--criteria-file` (**required, no default**), `--lessons-file` (optional,
no default), `--scope-dirs` (repeatable, **no default** — omitted means "no
filtering"), `--model` (default `claude-sonnet-5` — *lowered from
`claude-opus-5`, user decision 2026-07-31*), `--max-turns` (default
5 — *lowered from 15, user decision 2026-07-31*), `--max-findings` (default
30), `--exclude` (repeatable, default
`[]` — the earlier repo-specific defaults like
`backend/migrations/*` don't generalize; a consumer sets its own), `--out-
dir` (default `./review-output`), `--format {console,json,markdown,all}`
(default `console`), `--publish` (boolean, default `False`),
`--trust-head-files` (boolean, default `False` — *added 2026-07-31 with the
base-ref sourcing amendment in Key Decisions*), `--verbose`.

**`--scope-dirs` / `--exclude` accept both forms**: repeatable (`--scope-dirs
frontend --scope-dirs backend`) *and* comma-separated within one value
(`--scope-dirs "frontend,backend"`), with the two composable. Implement by
splitting each occurrence on `,` and stripping whitespace, then extending a
single accumulated list. This is required, not a convenience: `action.yml`'s
`scope-dirs`/`exclude` inputs are comma-scalar strings, so a repeatable-only
flag would make them unusable through the action.

**Scope filter (`filter_in_scope_files`)**: a file is in scope when its
current path (post-rename) *equals* one of `scope_dirs` or begins with one
followed by `/`; empty `scope_dirs` means everything is in scope. Unit tested
in `tests/test_cli.py`, including the colliding-prefix case. (*Tightened
2026-07-31 from the original bare `startswith`, impl-review finding F8: a
plain prefix match put `api_client_generated/bundle.min.js` in scope for
`--scope-dirs api`, and one generated file can consume the whole
diff-context budget.*)

`main_async` orchestration, exit-code contract:

1. Fetch PR metadata; `GitHubApiError` → stderr message, exit `2`.
2. **Closed/merged-PR guard** *(added during Phase 8 manual testing, user
   decision 2026-07-31 — see change.md)*: if `pr_metadata.state != "open"`,
   print "PR #<n> is closed/merged; skipping review," exit `0` — **before**
   the diff is even fetched, let alone the agent invoked. Nobody can act on
   review feedback for a PR that's already closed or merged; posting one is
   wasted API/token cost at best and a stray comment on a dead PR at worst.
   Checked first, ahead of every other guard, since it's the cheapest to
   evaluate (needs only the metadata call already made in step 1).
3. Fetch the diff; `GitHubApiError` → stderr message, exit `2`.
4. **Zero-changed-files guard**: no changed files at all → print "nothing
   to review", exit `0` without invoking the agent.
5. Load `--rules-file`, `--lessons-file` (if given), `--criteria-file`.
   Only `--criteria-file` is a hard requirement: its `FileNotFoundError` or
   `InvalidReviewCriteriaError` → stderr message naming which file/problem,
   exit `3`. A missing/unset rules-file or lessons-file is not an error —
   both are soft dependencies (*rules-file narrowed from a hard requirement
   during Phase 8 implementation, user decision 2026-07-31*); the
   corresponding prompt section is simply omitted.
6. Parse diff into `ChangedFile`s, apply `filter_in_scope_files` (no-op if
   `scope-dirs` wasn't set). **Scope guard**: if `scope-dirs` was set and
   `in_scope` ends up empty, print "N files out of scope," exit `0` without
   invoking the agent.
7. Build diff context from `in_scope`, build the system prompt from the
   three loaded inputs.
8. Run the agent; SDK exception (e.g. missing `ANTHROPIC_API_KEY`) → stderr
   message, exit `4`.
9. Dedup + cap findings, build `ReviewOutput` from `(findings, verdict)`,
   write requested format(s) to disk. **This happens before the verdict
   check, not after** — same principle as the "Post-failure handling"
   decision and Risk #8: artifacts land before anything that can fail, so
   findings survive a red job.
10. Non-success run → exit `4` when the SDK reported failure, exit `5` when
    the verdict is missing or invalid (see Phase 5's amendment), *with
    artifacts already written from step 9*. **Step 9 honours `--format`, and
    `console` is the default, so on this path the JSON artifact is written
    unconditionally regardless of `--format`** — otherwise the default
    invocation persists nothing and the findings are lost outright, which is
    the exact loss this step exists to prevent. (*Added 2026-07-31,
    impl-review finding F3.*) Risk #5 makes this the
    most likely failure mode, and an agent that submitted twenty good
    findings but never landed a valid `submit_review_verdict` (hit
    `max_turns`, or every attempt failed Phase 5's exact-name-match
    validation) must not lose them. On this path `ReviewOutput.verdict` is
    `None`, `event` falls back to `"COMMENT"` (see Phase 6), and the console
    and markdown output carry an explicit `"=== INCOMPLETE — NO VERDICT
    PRODUCED ==="` banner. **Never publish on this path**, regardless of
    `--publish`.
11. If `--publish`: call `github_publish.post_review(...)`. `GitHubPublishError`
    → stderr message noting local artifacts are saved, exit `6`. On success,
    console confirms "Posted N inline comments to PR #<n>" (no dry-run
    banner).
12. Else: print the existing dry-run console/JSON/markdown output, exit `0`.

README covers: setup (`uv sync`, `ANTHROPIC_API_KEY`, never commit it), the
`criteria-file` prerequisite (link to "Prerequisite" section), usage
example with all the now-required inputs spelled out, `--publish` and its
warning, the fork-PR limitation, and that there is no default `scope-dirs`
or `exclude` list anymore — every consumer sets its own. **Also covers the
Anthropic auth choice**: either `anthropic-api-key` alone, or
`anthropic-base-url` + `anthropic-auth-token` together for an
Anthropic-API-compatible gateway (OpenRouter is the worked example, since
that's this repo's own dogfood setup in Phase 12) — with an explicit warning
against setting both, since a non-empty `ANTHROPIC_API_KEY` wins over
`ANTHROPIC_AUTH_TOKEN` and silently defeats gateway routing.

## Phase 9: final pyproject.toml

`requires-python = ">=3.13"`; deps `claude-agent-sdk`, `unidiff`,
`githubkit`, `truststore` (let `uv add` resolve real current versions); `[project.scripts] pr-review-agent =
"pr_review_agent.cli:main"`; `[build-system]` hatchling +
`[tool.hatch.build.targets.wheel] packages = ["src/pr_review_agent"]`.

**Tooling config was pulled forward into Phase 0-3** during the impl review
(F9), because "the gates exist" and "the gates pass" are different claims and
only the second is worth anything: `ruff check` and `pyright` were not
installed, so neither had ever run against this code. Current state, all
green: dev group is `pytest`, `ruff`, `pyright`; ruff `extend-select = ["D"]`
with **`ignore = ["D107", "D105"]`, not `["D1"]`** — the originally planned
blanket `D1` would have disabled precisely the missing-docstring rules that
enforce this project's own docstring rule; pydocstyle
`convention = "google"`; `per-file-ignores` waives `D100`/`D103` for
`tests/*`; pyright `include = ["src"]`, `typeCheckingMode = "basic"`.

Three things worth knowing for anyone re-running these:
- **Ruff's `D1xx` does not cover underscore-prefixed functions.** Private
  helpers need their docstrings written by hand; the linter will never flag
  a missing one.
- `unidiff` must be imported from `unidiff.constants` / `unidiff.patch`, not
  the package root — the root re-exports without declaring public, which
  pyright rejects as `reportPrivateImportUsage`.
- `pyright` bootstraps Node via `nodeenv`, so it needs working TLS; if it
  dies with `no OPENSSL_Applink`, see the `SSLKEYLOGFILE` note in Phase 3.

**Commit `uv.lock`.** `.gitignore` leaves it commented out (i.e. not
ignored), which is correct and must stay that way. For a published action
the lock file is what makes `uv sync --project "${{ github.action_path }}"`
reproducible at a given tag — without it, every consumer's run at `@v1`
re-resolves `claude-agent-sdk`, `unidiff`, and `githubkit` at install time,
so an upstream breaking release silently breaks every consumer of an
already-tagged, never-touched action. `githubkit` pins a dated schema package
(`githubkit-schemas-*`), which makes the lock file more load-bearing, not
less.

## Phase 10: action.yml

```yaml
name: "AI PR Code Review"
description: "Reviews a PR's diff against caller-supplied rules and review criteria, using Claude."
inputs:
  pr-number: { required: true }
  repo: { required: false }
  rules-file: { required: false, default: "AGENTS.md" }
  criteria-file: { required: true }
  lessons-file: { required: false }
  scope-dirs: { required: false }
  exclude: { required: false }
  model: { required: false, default: "claude-sonnet-5" }
  max-turns: { required: false, default: "5" }
  max-findings: { required: false, default: "30" }
  format: { required: false, default: "console" }
  publish: { required: false, default: "false" }
  trust-head-files: { required: false, default: "false" }
  out-dir: { required: false, default: "./review-output" }
  anthropic-api-key: { required: false, default: "" }
  anthropic-base-url: { required: false, default: "" }
  anthropic-auth-token: { required: false, default: "" }
  github-token: { required: true }
runs:
  using: "composite"
  steps:
    - uses: astral-sh/setup-uv@<pinned-sha>
    - name: Install pr-review-agent
      shell: bash
      run: uv sync --project "${{ github.action_path }}"
    - name: Run review
      shell: bash
      env:
        ANTHROPIC_API_KEY: ${{ inputs.anthropic-api-key }}
        ANTHROPIC_BASE_URL: ${{ inputs.anthropic-base-url }}
        ANTHROPIC_AUTH_TOKEN: ${{ inputs.anthropic-auth-token }}
        GH_TOKEN: ${{ inputs.github-token }}
      run: |
        uv run --project "${{ github.action_path }}" pr-review-agent \
          --pr "${{ inputs.pr-number }}" \
          ${{ inputs.repo != '' && format('--repo "{0}"', inputs.repo) || '' }} \
          --rules-file "${{ inputs.rules-file }}" \
          --criteria-file "${{ inputs.criteria-file }}" \
          ${{ inputs.lessons-file != '' && format('--lessons-file "{0}"', inputs.lessons-file) || '' }} \
          ${{ inputs.scope-dirs != '' && format('--scope-dirs "{0}"', inputs.scope-dirs) || '' }} \
          ${{ inputs.exclude != '' && format('--exclude "{0}"', inputs.exclude) || '' }} \
          --max-turns "${{ inputs.max-turns }}" \
          --max-findings "${{ inputs.max-findings }}" \
          --out-dir "${{ inputs.out-dir }}" \
          --model "${{ inputs.model }}" --format "${{ inputs.format }}" \
          ${{ inputs.publish == 'true' && '--publish' || '' }} \
          ${{ inputs.trust-head-files == 'true' && '--trust-head-files' || '' }} --verbose
```

**Every declared input is forwarded.** The three with defaults
(`max-turns`, `max-findings`, `out-dir`) go unconditionally; the four
genuinely optional ones (`repo`, `lessons-file`, `scope-dirs`, `exclude`)
use the empty-string-fallback expression form. Getting this wrong is not a
cosmetic bug: `scope-dirs` is the headline generalization of this whole
plan, and a consumer that sets it while the run step drops it would get
every changed file reviewed with no error — and a consumer overriding
`out-dir` would upload an empty artifact directory in its `if: always()`
step.

**The three Anthropic auth inputs go straight into `env:`, unconditionally,
with empty-string defaults — deliberately not the `format(...)`-fallback
form the optional CLI-flag inputs use.** These three were never going to
become CLI flags (the SDK reads them from the process environment, not
`cli.py`'s argparse — see the "Anthropic auth" Key Decision), so there is no
`--flag` to conditionally omit; an empty string env var is simply absent to
the CLI subprocess. A consumer sets exactly one pair: `anthropic-api-key`
alone (direct Anthropic billing), or `anthropic-base-url` +
`anthropic-auth-token` together (OpenRouter or another Anthropic-API-
compatible gateway) — mixing both leaves `ANTHROPIC_API_KEY` non-empty,
which the Claude Code CLI checks before `ANTHROPIC_AUTH_TOKEN`, silently
defeating the gateway routing. Worth a explicit README callout, not just an
implicit convention.

**Critical detail** (see also Risks/Mitigations): use `uv run --project
"${{ github.action_path }}"`, **never** `--directory`. `--project` points
uv at this action's own `pyproject.toml`/venv without changing the
subprocess's working directory; `--directory` would `cd` into the action's
own source *before* running, which would silently break every relative
path this tool resolves (`--rules-file`, `--criteria-file`,
`find_repo_root()`'s `git rev-parse --show-toplevel`) — they all need to
resolve against the **caller's** checkout (the default composite-action
working directory, `$GITHUB_WORKSPACE`), not this action's own source
location.

**No `actions/setup-python` step.** `uv sync` provisions its own interpreter
from `.python-version` (3.13) and `requires-python = ">=3.13"`. Adding
`setup-python` would cost every consumer ~10s per job and introduce a second
source of truth for the Python version, free to drift from `.python-version`.

Not added: repo/PR checkout — a composite action doesn't check out its own
repo into the caller's workspace; the *caller's* workflow is expected to
have already run `actions/checkout` before this action's step (documented
in the README's usage example).

## Phase 11: .github/workflows/ci.yml

This repo's own lint/typecheck/test pipeline, scoped to the whole repo (no
scoping gap, see Phase 0). Triggers on `push`/`pull_request`
to `main`. Jobs: `uv sync`, `uv run ruff check . && uv run ruff format
--check .`, `uv run pyright`, `uv run pytest -v`.

## Phase 12: .github/workflows/self-test.yml (dogfood)

Before this phase: run the discovery session (see "Prerequisite") for
*this* repo, committing `context/foundation/review-criteria.md`; also
write a minimal `rules-file` for this repo (can be as simple as a short
`AGENTS.md` describing this repo's own conventions — reuses Phase 0/9's
tooling choices as content).

Workflow: triggers on `pull_request` to this repo, `permissions: contents:
read, pull-requests: write`, uses **this repo's own action via a local
path** (`uses: ./` — valid for an action referencing itself from within its
own repo) with `rules-file: AGENTS.md`, `criteria-file:
context/foundation/review-criteria.md`, `anthropic-base-url:
"https://openrouter.ai/api"`, `anthropic-auth-token: ${{
secrets.OPENROUTER_API_KEY }}` (a **repo secret the user must add manually**
in this repo's GitHub settings — not something set via CLI here; see the
"Anthropic auth" Key Decision for why this repo's own dogfood routes through
OpenRouter rather than a direct Anthropic key), `github-token: ${{
github.token }}`, `publish: true`. Uploads `review-output/` via
`actions/upload-artifact@v4` with `if: always()`.

This is the **simplest path to the course assignment's three deliverables**
(visible pipeline job, live logs, real PR comment) — it's entirely
self-contained in this one repo and doesn't require touching any other repo.

**Second job in the same workflow: `path-resolution-test`** — this is what
actually gates Phase 10, and it exists because the `dogfood` job above
*cannot* gate it. With `uses: ./`, `github.action_path` == `$GITHUB_WORKSPACE`,
so `--project` and `--directory` are behaviorally identical and the top risk
(Risk #1) goes unexercised. Breaking that degeneracy without a second repo:

```yaml
  path-resolution-test:
    runs-on: ubuntu-latest
    steps:
      # The "caller" checkout, at the workspace root.
      - uses: actions/checkout@v4
      # The action's own source, deliberately NOT at the workspace root.
      - uses: actions/checkout@v4
        with: { path: _action }
      # A criteria file that exists ONLY at the workspace root, never
      # under _action/ — this is the discriminator.
      - shell: bash
        run: |
          mkdir -p _fixture
          cp context/foundation/review-criteria.md _fixture/criteria.md
      - uses: ./_action
        with:
          pr-number: ${{ github.event.pull_request.number }}
          criteria-file: _fixture/criteria.md
          rules-file: AGENTS.md
          model: claude-haiku-4-5
          anthropic-base-url: "https://openrouter.ai/api"
          anthropic-auth-token: ${{ secrets.OPENROUTER_API_KEY }}
          github-token: ${{ github.token }}
          trust-head-files: "true"
          # publish deliberately omitted — dry run only
```

Because `_fixture/criteria.md` is untracked and generated after checkout, it
exists at `$GITHUB_WORKSPACE` but **not** under `$GITHUB_WORKSPACE/_action`.
If `action.yml` ever regresses to `--directory`, uv `cd`s into the action's
own source, the criteria file isn't found, and the job fails loudly with
exit `3`. Under the correct `--project`, it resolves and the review runs.
Uses only this repo — no second repo, no extra token, `claude-haiku-4-5` to
keep it cheap.

**`trust-head-files: "true"` added during live verification (2026-07-31, on
PR #10)** — without it, this job failed with "Criteria file not found," not
because path resolution was broken, but because the base-ref-sourcing default
(added later, during the phases 4-9 review, Key Decisions' "Review inputs are
read from the PR's base ref" amendment) fetches any in-checkout path from the
PR's *base* ref via the contents API instead of reading local disk.
`_fixture/criteria.md` is generated by this very job, after checkout — it can
never exist at *any* git ref, so it was unreachable by design, independent of
`--project` vs `--directory` working correctly. This job's whole point is
local path resolution, not the base-ref security property (that's the
`dogfood` job's concern, which uses genuinely tracked files), so trusting the
checkout here is correct, not a workaround.

## Critical Implementation Details / Risks and Mitigations

1. **`uv run --project` vs `--directory`** — using `--directory` in Phase
   10's composite action would silently make every consumer's review
   target *this action's own source* instead of their repo. Mitigated by
   using `--project` exclusively, called out explicitly in Phase 10, and
   **mechanically gated** by Phase 12's `path-resolution-test` job, which is
   built specifically so the two flags produce different outcomes (the
   `dogfood` job's `uses: ./` cannot tell them apart).
2. **Binary files in the diff** — real `GIT binary patch` blocks confirmed
   via the live API. Mitigated in Phase 2 with a dedicated fixture case
   and defensive per-file exception handling.
3. **Missing/insufficient token, or wrong `--repo`** on some future
   consumer's runner — Phase 3 differentiates these by HTTP status with
   distinct actionable messages (401/403 vs 404), and rejects a malformed
   `--repo` slug up front. `--repo` is load-bearing on every invocation
   (this tool never assumes "the current repo").
3b. **TLS interception on developer machines** — corporate proxies and
   antivirus MITM break `certifi`-based verification, and an antivirus-set
   `SSLKEYLOGFILE` can crash CPython's OpenSSL on Windows outright. Phase 3's
   `_build_ssl_context` mitigates both (OS trust store via `truststore`,
   `SSLKEYLOGFILE` suppressed). Applies equally to the Anthropic SDK in
   Phase 5.
4. **Zero-changed-files PR** — Phase 8 short-circuits before invoking the
   agent.
5. **Agent never calls `submit_finding` or `submit_review_verdict`**
   (confused / hit `max_turns`) vs. genuinely finding nothing — Phase 5/8's
   exit-code contract (0 = clean success incl. valid verdict, 5 =
   non-success) makes this distinguishable. Crucially, exit `5` still
   writes artifacts first (Phase 8 step 7), so findings produced before the
   verdict failed are preserved rather than discarded.
6. **Token cost during development** — use `--model claude-haiku-4-5` for
   early harness smoke tests before full default-model passes.
7. **`criteria-file` missing, or the discovery session hasn't happened
   yet** — Phase 4's `load_review_criteria` fails loudly (exit `3`) rather
   than inventing a default criteria set.
8. **Real GitHub post fails after findings were already produced**
   (permissions, rate limit, PR closed mid-run) — Phase 8 writes local
   artifacts *before* attempting the post; a consumer's workflow uploads
   them via `if: always()` regardless.
9. **Fork PRs get a read-only token** — inherited by every consumer;
   documented, not worked around with a PAT.
10. **Genericization dropped the origin repo's hardcoded rule categories**
    — a deliberate simplification (see Current State Analysis), not a
    regression: those categories only ever made sense for one specific
    FastAPI/React stack.
11. **Two Anthropic auth env vars set at once** (`ANTHROPIC_API_KEY`
    non-empty *and* `anthropic-base-url`/`anthropic-auth-token` set) — the
    Claude Code CLI checks `ANTHROPIC_API_KEY` first, so the gateway is
    silently bypassed rather than erroring. Mitigated by an explicit README
    warning (Phase 8) rather than code-level validation, since `cli.py` never
    touches these vars itself — they reach the CLI subprocess purely via
    inherited process environment (see the "Anthropic auth" Key Decision).

## Testing Strategy / Verification

1. `uv sync`; `uv run pytest -v` — all `diff_parser.py`, `test_cli.py`,
   `output.py` tests pass.
2. `uv run ruff check . && uv run ruff format --check . && uv run pyright`.
3. Export either `ANTHROPIC_API_KEY`, or `ANTHROPIC_BASE_URL` +
   `ANTHROPIC_AUTH_TOKEN` for an Anthropic-API-compatible gateway (this
   developer's own runs use OpenRouter: `ANTHROPIC_BASE_URL=
   https://openrouter.ai/api`, `ANTHROPIC_AUTH_TOKEN=<OpenRouter key>`,
   `ANTHROPIC_API_KEY=""` — never commit any of these).
4. **Recommended evidence path for the course assignment — entirely within
   this repo**: complete this repo's own discovery session (Phase 12
   prerequisite), write its `review-criteria.md` and a minimal `AGENTS.md`,
   implement Phase 12's `self-test.yml`, open a real PR against this repo,
   and confirm in the GitHub Actions tab: (a) the job appears and runs —
   "pipeline view with at least one visible job"; (b) the job's log shows
   `--verbose` output live during the review step — "logs from the
   pipeline while the code-review step executes"; (c) the PR shows the
   agent's real inline comments — "PR comment from the agent." This
   satisfies all three required deliverables without touching any other
   repo.
5. **Path-resolution test (mandatory — this is Phase 10's gate).** Run the
   `path-resolution-test` job from Phase 12's `self-test.yml` on a real PR
   to this repo. It exercises `action.yml` itself (not the CLI directly)
   with the action source checked out at `_action/` and the criteria file
   existing only at the workspace root. Pass condition: the review runs and
   resolves `_fixture/criteria.md`. Fail condition (i.e. a `--directory`
   regression): exit `3`, criteria file not found. This is the only check
   that can distinguish `--project` from `--directory`; the `dogfood` job's
   `uses: ./` cannot.
6. Confirm the criteria-file guard: point at a file with zero `##` headings
   → exit `3` with a specific error.
7. Confirm `--publish` end-to-end against a real scratch PR: inline
   comments anchored to correct lines (spot-check against `gh pr diff
   --patch`), local artifacts still written, summary body includes all
   loaded criteria with ratings and rationale.
8. Confirm the post-failure path (e.g. an unauthenticated/insufficiently
   scoped `GH_TOKEN`) → exit `6`, artifacts preserved. *(Originally also
   listed "a closed PR" as a trigger for this path; superseded 2026-07-31 —
   the closed/merged-PR guard now intercepts that case earlier and exits `0`
   before publishing is ever attempted, see step 8b and change.md.)*
8b. Confirm the closed/merged-PR guard (added 2026-07-31): running against a
    closed PR prints "PR #<n> is closed/merged; skipping review" and exits
    `0`, with no diff fetch, no agent invocation, and no publish attempt in
    the logs.
9. Confirm exit codes: a bogus `--pr` → exit `2` with a clear `gh` error.

## Follow-up / Out of Scope for This Plan

- **Consumer adoption — happens in the consuming repo, never here**: once
  this action is pushed and tagged (e.g. `v1`), a consuming repo adds a
  workflow step calling `uses: szaroket/ai-code-review-action@v1` with its
  own `rules-file`, `criteria-file`, and (optionally) `scope-dirs`. Each
  consumer first runs its own discovery session and commits its own criteria
  file. Nothing in this plan blocks on, is verified by, or must be updated
  because of that work.
- **Push `origin`/tag a release** — explicit user action, not automated
  here (see "What We're NOT Doing").
- **LICENSE + Marketplace listing** — only relevant if/when this becomes
  publicly discoverable, not needed for private cross-repo use via `uses:
  owner/repo@ref`.

## References

- `context/changes/ai-code-review/first-research.md` — source design doc
  (Claude Agent SDK facts), moved here unchanged
- `src/pr_review_agent/diff_parser.py` — correctness-critical, gets real
  tests
- `src/pr_review_agent/agents_context.py` — generic loaders + system prompt
- `src/pr_review_agent/review_agent.py` — the agent loop, both tools
- `src/pr_review_agent/github_publish.py` — real PR-comment posting, the
  untrusted external-API boundary
- `src/pr_review_agent/cli.py` — orchestration + exit-code contract
- `action.yml` — the reusable entrypoint; the `--project`-not-`--directory`
  detail lives here
- `pyproject.toml` — dependency/tooling setup
- `.github/workflows/self-test.yml` — the course assignment's evidence path
- `context/foundation/review-criteria.md` — this repo's own review criteria
  (prerequisite for Phase 12)

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 0: Repo Environment Setup

#### Automated

- [x] 0.1 `uv sync` succeeds — 0ae9e32
- [x] 0.2 `uv run python -c "import pr_review_agent"` works — 0ae9e32
- [x] 0.3 `pre-commit run --all-files` passes on the skeleton — 0ae9e32

#### Manual

- [x] 0.4 `.gitignore` trimmed of frontend-specific lines — 0ae9e32

### Phase 1: models.py

#### Automated

- [x] 1.1 `uv run python -c "from pr_review_agent.models import Finding, ReviewOutput, Criterion, CriterionScore, ReviewVerdict"` works — 1271954
- [x] 1.2 Skipped by user decision (2026-07-30): `tests/test_models.py` removed, no dedicated tests for `models.py` — supersedes the phase text's StrEnum-pinning rationale — 07f9a59

### Phase 2: diff_parser.py + tests

#### Automated

- [x] 2.1 `uv run pytest tests/test_diff_parser.py -v` passes — 6e76704

### Phase 3: github_diff.py + logging_config.py

#### Automated

- [x] 3.2 `uv run pytest tests/test_github_diff.py -v` passes (`resolve_repo` precedence + slug validation)
- [x] 3.3 Skipped by user decision (2026-07-30): no dedicated tests for `logging_config.py`, superseding impl-review F5's "wanted" note and `follow-ups/review-fixes.md`'s open item — 07f9a59

#### Manual

- [x] 3.1 Verified manually against a real PR in this repo (throwaway PR if none exists yet); `find_repo_root()` returns the checkout root and degrades to `cwd` with a warning outside a git checkout — b7b0c3f
- [x] 3.4 Re-verified end-to-end after the `gh` → `githubkit` transport switch: `resolve_repo`, `get_pr_metadata`, `get_pr_diff`, `parse_diff`, `exclude_paths`, `build_diff_context`, `find_repo_root` all exercised live against PR #1

### Phase 4: agents_context.py

#### Automated

- [x] 4.1 `uv run python -c "from pr_review_agent.agents_context import build_system_prompt, load_review_criteria, load_rules_file, load_lessons_file"` works — 78074c5

#### Manual

- [x] 4.2 Criteria-file guard smoke test per Testing Strategy step 6 (malformed file → exit `3`) — 78074c5

### Phase 5: review_agent.py (the actual agent loop)

#### Manual

- [x] 5.1 Exercised via the Testing Strategy end-to-end run (no dedicated unit tests this iteration) — ae2a448
- [x] 5.2 `submit_review_verdict` produces a valid verdict on a real run; a mismatched-criteria call is rejected with a retryable error — ae2a448

### Phase 6: output.py

#### Automated

- [x] 6.1 `uv run pytest tests/test_output.py -v` passes — a5b59bb

### Phase 7: github_publish.py (real PR-comment posting)

#### Manual

- [x] 7.1 Exercised via Testing Strategy step 7 (real `--publish` run against a scratch PR) — e5a49e9

### Phase 8: cli.py

#### Automated

- [x] 8.1 `uv run pytest tests/test_cli.py -v` passes (scope filter unit tests) — 55246f3

#### Manual

- [x] 8.2 Exit-code contract smoke test per Testing Strategy step 9 — 55246f3
- [x] 8.3 `--publish` and post-failure smoke tests per Testing Strategy steps 7-8 — 55246f3
- [x] 8.4 Closed/merged-PR guard confirmed live per Testing Strategy step 8b (exit `0`, no diff fetch, no agent run, no publish attempt) — 55246f3

### Phase 9: final pyproject.toml

#### Automated

- [x] 9.1 `uv run ruff check . && uv run ruff format --check . && uv run pyright` passes — 09dccb3

### Phase 10: action.yml

#### Manual

- [ ] 10.1 `path-resolution-test` job (Phase 12 `self-test.yml`) passes per Testing Strategy step 5, confirming `--project` (not `--directory`) resolves paths against the caller's checkout with `action_path` != workspace

### Phase 11: .github/workflows/ci.yml

#### Automated

- [ ] 11.1 CI job passes on a real PR to this repo (lint, typecheck, test)

### Phase 12: .github/workflows/self-test.yml (dogfood)

#### Manual

- [x] 12.1 This repo's own discovery session completed, `review-criteria.md` and a minimal `AGENTS.md` committed
- [ ] 12.2 Assignment-evidence smoke test per Testing Strategy step 4 (job visible in Actions tab, `--verbose` logs visible during the step, real inline comment visible on this repo's own PR)
