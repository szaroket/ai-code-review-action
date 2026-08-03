# ai-code-review-action

A reusable, generic GitHub Action that runs a Claude Agent SDK agentic loop
reviewing a GitHub PR's diff against whatever repository rules and review
criteria the consuming workflow points it at.

Nothing about the review is baked in. You supply the criteria file; the action
supplies the diff, the agent loop, the deduplication and capping, the artifacts,
and (optionally) the inline PR comments.

## Prerequisite: a criteria file

**`criteria-file` is required and there is no default.** It is a markdown file
whose `##` headings are the criteria the PR is scored against — at least one, or
the run fails with exit `3`.

```markdown
# Review Criteria

## Correctness
Does the change do what it claims, without introducing obvious bugs?

## Test Coverage
Are meaningful tests included or updated for the behavior that changed?
```

Heading text is the criterion name; the prose under it is the guidance the model
gets. `tests/fixtures/smoke-criteria.md` in this repo is a working example.

### Where the criteria, rules and lessons files are read from

By default these three files are read **from the pull request's base ref**, not
from the checkout — a PR must not be able to rewrite the rules and criteria it
is about to be judged by. Practical consequence: **a criteria file added by the
PR under review will be reported as missing.** Merge it to the base branch
first.

`trust-head-files: "true"` reads them from the working checkout instead. Use it
for local runs and for branches you control. **Do not use it on pull requests
from people you don't trust** — it hands the PR author control of the system
prompt.

## Usage

```yaml
name: AI PR Review

on:
  pull_request:

permissions:
  contents: read
  pull-requests: write   # only needed for publish: "true"

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: szaroket/ai-code-review-action@main   # pin to a SHA in real use
        with:
          pr-number: ${{ github.event.pull_request.number }}
          criteria-file: .github/review-criteria.md
          rules-file: AGENTS.md
          scope-dirs: "src,tests"
          exclude: "**/*.lock,**/migrations/**"
          anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
          github-token: ${{ github.token }}
          publish: "true"
      - name: Upload review output
        if: always()
        uses: actions/upload-artifact@v7
        with:
          name: review-output
          path: review-output/
```

`actions/checkout` is required: the action resolves `criteria-file`,
`rules-file` and `lessons-file` relative to the checkout. The agent reviews
the diff only — it has no Read/Grep/Glob access to browse the repository;
an earlier version offered it, but a run given the tools reliably spent its
entire turn budget exploring and never submitted a finding.

## Inputs

| Input | Required | Default | Notes |
|---|---|---|---|
| `pr-number` | yes | — | The PR to review. |
| `criteria-file` | yes | — | See [Prerequisite](#prerequisite-a-criteria-file). |
| `github-token` | yes | — | Needs `pull-requests: read`, or `write` for `publish`. |
| `repo` | no | `GITHUB_REPOSITORY`, then the checkout's `origin` | `OWNER/REPO`. |
| `rules-file` | no | `AGENTS.md` | Injected into the prompt verbatim. Skipped if absent. |
| `lessons-file` | no | — | Extra lessons/pitfalls file, same trust rules. |
| `scope-dirs` | no | — | **No default.** Omit to review every changed file. |
| `exclude` | no | — | **No default.** Comma-separated globs. |
| `model` | no | `claude-sonnet-5` | |
| `max-turns` | no | `5` | Agent turn budget. |
| `max-findings` | no | `30` | Applied after deduplication. |
| `format` | no | `console` | `console`, `json`, `markdown`, or `all`. |
| `publish` | no | `false` | See [Publishing](#publishing). |
| `trust-head-files` | no | `false` | Unsafe for untrusted PRs. |
| `out-dir` | no | `./review-output` | Artifacts land here. |
| `anthropic-api-key` | no | — | See [Anthropic auth](#anthropic-auth). |
| `anthropic-base-url` | no | — | See [Anthropic auth](#anthropic-auth). |
| `anthropic-auth-token` | no | — | See [Anthropic auth](#anthropic-auth). |

### No default scope

There is no built-in `scope-dirs` or `exclude` list — earlier versions carried
one tuned for a single FastAPI/React repository, and it was wrong everywhere
else. Every consumer sets its own, or reviews the whole diff.

## Anthropic auth

Pick **one** of these two, never both:

- **Direct Anthropic** — set `anthropic-api-key`.
- **An Anthropic-API-compatible gateway** — set `anthropic-base-url` *and*
  `anthropic-auth-token` together. OpenRouter is the worked example:

  ```yaml
  anthropic-base-url: "https://openrouter.ai/api"
  anthropic-auth-token: ${{ secrets.OPENROUTER_API_KEY }}
  ```

> [!WARNING]
> **Do not set `anthropic-api-key` alongside the gateway pair.** A non-empty
> `ANTHROPIC_API_KEY` takes precedence over `ANTHROPIC_AUTH_TOKEN`, so the
> gateway is **silently bypassed** — no error, no warning, and the traffic (and
> the bill) goes straight to Anthropic instead. Nothing in this action can
> detect that for you; the variables are read by the Claude Code CLI itself.

Always pass these through `secrets`. Never commit a key.

## Publishing

`publish: "true"` posts a real GitHub review with inline comments on the PR.

> [!WARNING]
> Published comments are visible to everyone who can see the pull request, and
> the action posts them under the identity of whatever `github-token` you give
> it. Run without `publish` first and read the artifacts before turning it on
> for a busy repository.

Requirements and behavior:

- `permissions: pull-requests: write` on the job.
- Comment lines must exist in the diff — GitHub rejects the whole review
  otherwise.
- A review that produced no verdict is **never** published, regardless of this
  input.
- With the default `GITHUB_TOKEN`, an `APPROVE` event is downgraded to a plain
  `COMMENT`: GitHub does not let the Actions token approve pull requests.

### Fork pull requests

`pull_request` runs from a fork get a **read-only** token and no access to
repository secrets. Such runs cannot publish, and will not have an Anthropic
key. This is a GitHub restriction inherited by every consumer of this action;
it is documented rather than worked around with a PAT, because the workarounds
(`pull_request_target`) run untrusted code with a writable token.

For fork coverage, run the review on a trusted trigger and read the artifacts.

## Output

Artifacts are written to `out-dir` before the verdict is evaluated and before
publishing, so a failed publish never costs you the review:

- `pr-<n>-<timestamp>.json` — the exact review payload plus a local-only
  `criteria` array. Written on every failure path regardless of `format`.
- `pr-<n>-<timestamp>.md` — the same review as markdown (`format: markdown` or
  `all`).

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Clean success, including "nothing to review" and closed/merged PRs. |
| `2` | GitHub fetch failure. |
| `3` | The criteria file is missing or invalid, or a review-input file could not be read. |
| `4` | The Claude Agent SDK itself failed. |
| `5` | The run completed without a valid verdict. |
| `6` | The review completed but publishing it failed — artifacts are still on disk. |

## Local development

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run pyright
```

Running the CLI by hand against a real PR:

```bash
export ANTHROPIC_API_KEY=...   # never commit this
export GH_TOKEN=...
uv run pr-review-agent \
  --pr 42 \
  --repo owner/name \
  --criteria-file .github/review-criteria.md \
  --format all
```

Add `--publish` only when you mean it. `--verbose` logs assistant activity at
DEBUG level. Tokens are redacted from logs, but treat the artifacts as
sensitive anyway.
