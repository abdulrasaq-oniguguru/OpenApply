"""Local persistence for the candidate profile (``profile.json``)."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from openapply.candidate.models import SCHEMA_VERSION, CandidateProfile
from openapply.config.atomic import atomic_write_text
from openapply.config.paths import profile_path


class ProfileError(Exception):
    """The profile file exists but cannot be used."""


class ProfileStorage:
    """JSON-file store. The path is resolved lazily so ``OPENAPPLY_HOME`` is honoured."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path or profile_path()

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> CandidateProfile | None:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProfileError(f"Cannot read profile {self.path}: {exc}") from exc
        version = data.get("schema_version", SCHEMA_VERSION) if isinstance(data, dict) else None
        if isinstance(version, int) and version > SCHEMA_VERSION:
            raise ProfileError(
                f"Profile {self.path} uses schema v{version}; this OpenApply understands "
                f"v{SCHEMA_VERSION}. Upgrade OpenApply."
            )
        try:
            return CandidateProfile.model_validate(data)
        except ValidationError as exc:
            raise ProfileError(f"Invalid profile {self.path}: {exc}") from exc

    def save(self, profile: CandidateProfile) -> Path:
        text = profile.model_dump_json(indent=2, exclude_none=False) + "\n"
        # Personal data: owner-only where the OS supports it (a no-op on Windows).
        return atomic_write_text(self.path, text, private=True)
