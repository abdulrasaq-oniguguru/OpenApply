from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def fake_browser_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """`doctor` must never launch a real Playwright driver during unit tests."""
    monkeypatch.setattr(
        "openapply.cli.commands.doctor._browser_status", lambda: (True, "Chromium installed")
    )
