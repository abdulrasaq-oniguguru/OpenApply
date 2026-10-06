# Contributing to OpenApply

Thanks for helping. OpenApply fills in job applications on a person's behalf, so the bar for
changes that touch what it sends, answers or clicks is deliberately high.

## Setup

```bash
git clone https://github.com/abdulrasaq-oniguguru/OpenApply.git
cd OpenApply
uv sync
```

Before opening a pull request, all of these must pass:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
```

Tests never need a paid AI provider or an API key: providers are mocked. Browser tests run
against local HTML fixtures in `tests/fixtures/` and use whichever Chrome, Edge or Playwright
Chromium is installed (they skip if none is).

## Ground rules

- **Never guess a sensitive answer.** Visa sponsorship, work authorization, relocation and
  similar questions are answered only from an explicit profile answer. Demographic, criminal
  history, clearance and consent questions are never answered or ticked. Missing data is
  "ask the user", never a default.
- **Web pages and AI replies are untrusted.** Anything from a page goes through the delimited
  prompt helpers in `openapply/prompts/common.py`, is validated before use, and is escaped
  before it is printed (`openapply/cli/render.py`).
- **Nothing is submitted without the user.** Do not add a path that submits, ticks a consent, or
  sends data to another site without the confirmation flow in `openapply/cli/review.py`.
- **Credentials are never read.** Providers are driven only through their own CLIs or local API.
- **A security fix needs a test that fails without it.** The existing tests were written that
  way; please keep it up. Fixtures for hostile pages live in `tests/fixtures/`.

## Style

Python 3.12, type hints everywhere (`mypy --strict`), Ruff for formatting and linting, small
modules, dependencies injected so providers and browsers can be faked in tests.

## Reporting security problems

Please do not open a public issue. See [SECURITY.md](SECURITY.md).
