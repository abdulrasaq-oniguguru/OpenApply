"""Candidate profile use-cases: load/save, resume attachment, completeness checks."""

from __future__ import annotations

import hashlib
import re
from enum import StrEnum
from pathlib import Path

from openapply.candidate.models import CandidateProfile, ResumeRef
from openapply.candidate.parser import SUPPORTED_SUFFIXES
from openapply.candidate.storage import ProfileStorage
from openapply.config.atomic import atomic_write_bytes, ensure_private_dir
from openapply.config.paths import resumes_dir

MAX_RESUME_BYTES = 10 * 1024 * 1024


class ResumeError(Exception):
    """The resume file cannot be used."""


class ResumeStatus(StrEnum):
    NONE = "none"
    OK = "ok"
    MISSING = "missing"
    MODIFIED = "modified"


def _read_bounded(path: Path) -> bytes:
    """Read at most MAX_RESUME_BYTES + 1 bytes: enough to detect oversize, never unbounded."""
    with path.open("rb") as handle:
        return handle.read(MAX_RESUME_BYTES + 1)


class CandidateService:
    def __init__(self, storage: ProfileStorage | None = None, resumes: Path | None = None) -> None:
        self.storage = storage or ProfileStorage()
        self._resumes = resumes

    @property
    def resumes_path(self) -> Path:
        return self._resumes or resumes_dir()

    def load(self) -> CandidateProfile | None:
        return self.storage.load()

    def load_or_new(self) -> CandidateProfile:
        return self.storage.load() or CandidateProfile()

    def save(self, profile: CandidateProfile) -> Path:
        return self.storage.save(profile)

    def attach_resume(self, profile: CandidateProfile, source: Path) -> CandidateProfile:
        """Copy ``source`` into the data directory and return the updated profile.

        The file is read once; the hash, the filename and the stored bytes all come from
        that single read, so they cannot disagree even if the source changes meanwhile.
        """
        source = source.expanduser()
        if source.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise ResumeError("Resume must be a .pdf or .docx file")
        if not source.is_file():
            raise ResumeError(f"Resume file not found: {source}")
        data = _read_bounded(source)
        if not data:
            raise ResumeError("Resume file is empty")
        if len(data) > MAX_RESUME_BYTES:
            raise ResumeError(f"Resume is larger than {MAX_RESUME_BYTES // (1024 * 1024)} MB")

        digest = hashlib.sha256(data).hexdigest()
        safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", source.stem).strip("._") or "resume"
        filename = f"{safe_stem}-{digest[:8]}{source.suffix.lower()}"
        ensure_private_dir(self.resumes_path)
        atomic_write_bytes(self.resumes_path / filename, data, private=True)
        ref = ResumeRef(filename=filename, original_name=source.name, sha256=digest)
        return profile.model_copy(update={"resume": ref})

    def resume_status(self, profile: CandidateProfile) -> ResumeStatus:
        """Check the stored resume against the hash recorded when it was attached."""
        ref = profile.resume
        if ref is None:
            return ResumeStatus.NONE
        path = self.resumes_path / Path(ref.filename).name  # defence in depth; ref is validated
        if not path.is_file():
            return ResumeStatus.MISSING
        digest = hashlib.sha256(_read_bounded(path)).hexdigest()
        return ResumeStatus.OK if digest == ref.sha256 else ResumeStatus.MODIFIED

    def resume_file(self, profile: CandidateProfile) -> Path | None:
        """Path of the stored resume, only if present *and* unmodified."""
        if profile.resume is None or self.resume_status(profile) is not ResumeStatus.OK:
            return None
        return self.resumes_path / Path(profile.resume.filename).name

    @staticmethod
    def missing_required(profile: CandidateProfile) -> list[str]:
        missing = []
        if not profile.identity.full_name:
            missing.append("full name")
        if not profile.identity.email:
            missing.append("email")
        return missing

    @staticmethod
    def unknown_eligibility(profile: CandidateProfile) -> list[str]:
        """Sensitive answers that must be confirmed by the user when asked."""
        unknown = []
        if profile.eligibility.requires_sponsorship is None:
            unknown.append("requires_sponsorship")
        if profile.eligibility.willing_to_relocate is None:
            unknown.append("willing_to_relocate")
        if not profile.eligibility.authorized_countries:
            unknown.append("authorized_countries")
        return unknown
