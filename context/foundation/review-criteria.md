# Review Criteria

## Exit-Code / Error-Handling Contract Fidelity
`cli.py` documents an explicit exit-code contract (0 clean success/benign
skip, 2 GitHub fetch failure, 3 required-input-file error, 4 SDK failure, 5
run completed without a valid verdict, 6 publish failure). Changes that touch
`main_async`, `github_diff.py`, `github_publish.py`, `agents_context.py`, or
`review_agent.py` must preserve this mapping: no new failure mode should be
silently swallowed, misclassified under an unrelated exit code, or allowed to
raise an unhandled exception instead of returning a documented code. A change
that widens or narrows which exceptions map to which exit code must update the
module docstring in the same PR.

## No Cross-Repo Coupling
This repo must never depend on another repository — not in source code, not
in tests or fixtures, not in verification/CI steps. Other repos consume this
action; none participate in building or validating it. Flag anything that
reads a path, URL, or fixture assumed to exist in a different repository, or
any test/workflow step that requires a second checkout.

## Type Safety / Pyright Cleanliness
New and changed code is fully typed and passes `pyright` in `basic` mode with
zero errors and no `# type: ignore` / `# pyright: ignore` suppressions unless
the PR explains why the suppression is unavoidable (e.g. a third-party
stub gap). Closed value sets use `StrEnum`, not `Literal` or a plain
`enum.Enum` — this is load-bearing for JSON serialization elsewhere in the
codebase (see `models.py`), so a regression here can fail silently.

## Docstring Completeness (Google Style)
Every function (excluding `__init__`/dunder methods, which `ruff`'s `D107`/
`D105` waiver already covers, and test functions, which `tests/*`'s
per-file-ignore covers) has a Google-style docstring with `Args`, `Returns`,
and `Raises` sections matching its actual signature and behavior — not a
restatement of the function name. A docstring that omits a parameter, claims
a return type the code doesn't produce, or lists a `Raises` entry the code no
longer raises is a defect, not just a style nit.

## Security / Untrusted-Input Handling
This tool posts real comments to GitHub PRs and feeds PR-diff content,
PR-supplied file contents, and (for in-checkout paths) base-ref file contents
into an LLM's system/user prompt. Flag anything that:
- Treats content sourced from a PR's own head checkout (diff text, changed
  files, or a rules/criteria/lessons path resolving inside the checkout) as
  trusted instruction rather than untrusted PR-author-controlled input —
  the base-ref-sourcing design in `cli.py`'s `_load_review_input` exists
  specifically to prevent a PR from rewriting the rules or criteria it is
  scored against.
- Logs or writes to disk an unredacted secret, token, or API key (see
  `logging_config.redact`) — including in exception messages, tracebacks, or
  artifact files under `--out-dir`.
- Grants the agent (`Read`/`Grep`/`Glob` via `ClaudeAgentOptions`) a path
  outside the intended `repo_root`, or widens `allowed_tools` beyond the
  read-only + `submit_finding`/`submit_review_verdict` set without an
  explicit justification.
