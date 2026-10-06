"""Platform-aware locations for OpenApply data.

The root defaults to ``~/.openapply`` and can be moved with ``OPENAPPLY_HOME``
(used by tests and by users who want the data elsewhere).
"""

from __future__ import annotations

import os
from pathlib import Path

HOME_ENV_VAR = "OPENAPPLY_HOME"


def app_dir() -> Path:
    override = os.environ.get(HOME_ENV_VAR)
    return Path(override).expanduser() if override else Path.home() / ".openapply"


def config_path() -> Path:
    return app_dir() / "config.toml"


def profile_path() -> Path:
    return app_dir() / "profile.json"


def database_path() -> Path:
    return app_dir() / "openapply.db"


def resumes_dir() -> Path:
    return app_dir() / "resumes"
