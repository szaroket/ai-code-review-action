# Repository Conventions

`ai-code-review-action` — a reusable composite GitHub Action (`src/pr_review_agent/`)
that runs a Claude Agent SDK review loop over a GitHub PR's diff. Python 3.13,
managed with `uv`.

## Standalone constraint

This repo must never depend on another repository — not in source code, not
in tests or fixtures, not in verification/CI steps. Other repos *consume*
this action via `uses: szaroket/ai-code-review-action@ref`; none participate
in building or validating it.

## Tooling gates (all must pass)

- `uv run ruff check .` and `uv run ruff format --check .`
- `uv run pyright` (`basic` mode, `include = ["src"]`, zero errors)
- `uv run pytest -v`
- `uv run pre-commit run --all-files`

## Code conventions

- Google-style docstrings (`Args`/`Returns`/`Raises`) on every function,
  enforced by ruff's `D` rules (`pydocstyle convention = "google"`); only
  `__init__`/dunder methods and `tests/*` are waived.
- Closed value sets are `StrEnum`, never `Literal` or a plain `enum.Enum` —
  `StrEnum` members serialize to their wire string directly, which several
  modules (`models.py`, `output.py`, `github_publish.py`) depend on silently.
- No `print()` in `src/` — use `logging.getLogger(__name__)`. Logs go to
  stderr only; stdout is reserved for review output.
- Never log or write an unredacted secret/token; route anything that might
  contain one through `logging_config.redact`.
- `cli.py`'s exit-code contract (0/2/3/4/5/6, documented in its module
  docstring) is a public contract for consuming workflows — preserve it
  deliberately, don't drift it incidentally.
- GitHub access goes through `githubkit`, not a `gh` CLI subprocess. Local
  VCS state (`find_repo_root`, `origin` remote) is the one thing that still
  uses a `git` subprocess.
- Rules/lessons/criteria file content is injected into the system prompt
  close to verbatim — no hardcoded per-stack categories baked into code.

## Review-relevant background

See `context/foundation/review-criteria.md` for what this repo's own PR
review scores against, and `context/changes/ai-code-review/plan.md` for the
full design history and rationale behind the decisions above.
