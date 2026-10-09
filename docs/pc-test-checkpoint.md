# Other-PC test checkpoint

Updated: 2026-10-09

This is the first checkpoint intended for cloning onto another computer. It tests the reviewed
application loop in installed Google Chrome without contacting or applying to a real employer.

## What this checkpoint proves

The personal desk can now take one discovered opportunity through:

`prepare locally -> review/edit -> authorize an exact revision -> queue once -> submit -> report`

Preparation scans the form but does not enter candidate data. Submission requires two explicit
web confirmations, is tied to one immutable revision and destination, and is rejected if the
form shape changes. Dispatch intent is persisted before the click; after a crash OpenApply marks
the result unknown instead of submitting again.

This checkpoint supports the existing single-page form engine. It does not yet claim reliable
Greenhouse, Lever or Ashby multi-step flows, saved account sessions, CAPTCHA handling, employer
receipt confirmation or automatic submission policies.

## Clone and install on Windows

Install Git, [uv](https://docs.astral.sh/uv/) and Google Chrome first. Then use PowerShell:

```powershell
git clone https://github.com/abdulrasaq-oniguguru/OpenApply.git
cd OpenApply
uv sync --extra web
uv run openapply doctor
```

The reviewed-browser checkpoint is completely local and safe to run:

```powershell
uv run pytest -q tests/web/test_reviewed_flow_browser.py -s
```

Expected result:

```text
1 passed
```

That test launches installed Google Chrome headlessly, clicks the real OpenApply dashboard,
starts the real worker twice, and submits exactly once to a temporary local HTML form. It does
not use a provider, external job site or personal profile.

For a broader check:

```powershell
uv run pytest -q
uv run ruff format --check .
uv run ruff check .
uv run mypy
```

## Start a fresh personal desk

Personal data is deliberately not stored in Git, so set up the profile separately on the new PC:

```powershell
uv run openapply setup
uv run openapply providers list
```

If needed, select an already-authenticated provider:

```powershell
uv run openapply providers set-default codex
```

Run these in separate PowerShell windows:

```powershell
# Window 1
uv run openapply agent serve

# Window 2
uv run openapply worker run
```

Open `http://127.0.0.1:8080` in Chrome. Queue a career page or a single job URL. When analysis
finishes, use **Prepare**, open **Review**, resolve every required field, save a revision, and
inspect the destination. Authorization lasts 30 minutes. The final button queues only that exact
revision; you can revoke it before the worker starts dispatch.

For the first manual test, use a form you own or a deliberately disposable test application.
Do not use automatic or bulk submissions; those modes do not exist in this checkpoint.

## What to record if something fails

Capture these without including personal answers, CV contents, cookies or tokens:

- Windows version and `uv run openapply doctor` output.
- The failed command and error text.
- Whether Chrome opened and whether the worker terminal changed task state.
- The application/task ID shown by the desk.
- A screenshot with personal fields redacted.

Do not share `~/.openapply/openapply.db`, `profile.json`, stored resumes, provider transcripts or
Telegram tokens.
