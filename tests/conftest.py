from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from openapply.config.paths import HOME_ENV_VAR


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test gets its own OpenApply data dir; the real one is never touched."""
    home = tmp_path / "openapply-home"
    monkeypatch.setenv(HOME_ENV_VAR, str(home))
    return home


@pytest.fixture(autouse=True)
def reset_logging() -> Iterator[None]:
    yield
    logger = logging.getLogger("openapply")
    logger.handlers.clear()
    logger.propagate = True
    logger.setLevel(logging.NOTSET)
