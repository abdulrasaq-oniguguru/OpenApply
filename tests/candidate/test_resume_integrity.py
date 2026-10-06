"""Resume storage: permissions, reference validation, and content integrity."""

from __future__ import annotations

import hashlib
import os
import stat
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from openapply.candidate import service as service_module
from openapply.candidate.models import CandidateProfile, Identity
from openapply.candidate.service import CandidateService, ResumeStatus

SHA_A = "a" * 64


def _profile() -> CandidateProfile:
    return CandidateProfile(identity=Identity(full_name="Ada Lovelace", email="ada@example.com"))


def _pdf(tmp_path: Path, content: bytes = b"%PDF-1.4 x") -> Path:
    path = tmp_path / "cv.pdf"
    path.write_bytes(content)
    return path


@pytest.mark.parametrize(
    "ref",
    [
        {"filename": "../../secret.pdf", "original_name": "x", "sha256": SHA_A},  # traversal
        {"filename": "cv-aaaaaaaa.pdf", "original_name": "x", "sha256": "0"},  # short hash
        {"filename": "cv-bbbbbbbb.pdf", "original_name": "x", "sha256": SHA_A},  # name != hash
        {"filename": "cv-aaaaaaaa.exe", "original_name": "x", "sha256": SHA_A},  # bad suffix
        {"filename": "cv.pdf", "original_name": "x", "sha256": SHA_A},  # not the convention
        {"filename": "cv-aaaaaaaa.pdf", "original_name": "x", "sha256": "A" * 64},  # uppercase
    ],
)
def test_forged_resume_reference_is_rejected_by_the_model(ref: dict[str, str]) -> None:
    data = _profile().model_dump()
    data["resume"] = ref
    with pytest.raises(ValidationError):
        CandidateProfile.model_validate(data)


def test_modified_stored_resume_is_detected(tmp_path: Path, isolated_home: Path) -> None:
    service = CandidateService()
    profile = service.attach_resume(_profile(), _pdf(tmp_path))
    assert profile.resume is not None
    assert service.resume_status(profile) is ResumeStatus.OK
    assert service.resume_file(profile) is not None

    stored = isolated_home / "resumes" / profile.resume.filename
    stored.write_bytes(b"tampered")
    assert service.resume_status(profile) is ResumeStatus.MODIFIED
    assert service.resume_file(profile) is None

    stored.unlink()
    assert service.resume_status(profile) is ResumeStatus.MISSING
    assert service.resume_file(profile) is None
    assert service.resume_status(_profile()) is ResumeStatus.NONE


def test_well_formed_reference_to_another_file_is_not_trusted(isolated_home: Path) -> None:
    (isolated_home / "resumes").mkdir(parents=True)
    (isolated_home / "resumes" / "other-aaaaaaaa.pdf").write_bytes(b"someone elses file")
    data = _profile().model_dump()
    data["resume"] = {"filename": "other-aaaaaaaa.pdf", "original_name": "x", "sha256": SHA_A}
    profile = CandidateProfile.model_validate(data)
    assert CandidateService().resume_status(profile) is ResumeStatus.MODIFIED  # hash mismatch
    assert CandidateService().resume_file(profile) is None


def test_stored_bytes_are_the_bytes_that_were_hashed(
    tmp_path: Path, isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The source changes right after it is read; the stored copy must still match its hash."""
    source = _pdf(tmp_path, b"version-one")
    real_read = service_module._read_bounded
    calls = {"n": 0}

    def racing_read(path: Path) -> bytes:
        data = real_read(path)
        if calls["n"] == 0:
            calls["n"] += 1
            path.write_bytes(b"version-two-written-mid-attach")  # editor/download replaces it
        return data

    monkeypatch.setattr(service_module, "_read_bounded", racing_read)
    service = CandidateService()
    profile = service.attach_resume(_profile(), source)
    assert profile.resume is not None
    stored = isolated_home / "resumes" / profile.resume.filename
    assert stored.read_bytes() == b"version-one"
    assert hashlib.sha256(stored.read_bytes()).hexdigest() == profile.resume.sha256
    assert service.resume_status(profile) is ResumeStatus.OK


def test_resume_dir_and_file_are_restricted_via_chmod(
    tmp_path: Path, isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Runs on every platform: asserts the intended modes are requested."""
    calls: list[tuple[str, int]] = []
    real_chmod = os.chmod

    def spy(path: str | Path, mode: int, **kwargs: object) -> None:
        calls.append((str(path), mode))
        real_chmod(path, mode)

    monkeypatch.setattr(os, "chmod", spy)
    profile = CandidateService().attach_resume(_profile(), _pdf(tmp_path))
    assert profile.resume is not None
    assert (str(isolated_home / "resumes"), 0o700) in calls
    assert any(mode == 0o600 for _, mode in calls)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_resume_permissions_on_posix(tmp_path: Path, isolated_home: Path) -> None:
    resumes = isolated_home / "resumes"
    resumes.mkdir(parents=True)
    resumes.chmod(0o755)  # a pre-existing, too-open directory gets tightened
    old_umask = os.umask(0o022)
    try:
        profile = CandidateService().attach_resume(_profile(), _pdf(tmp_path))
    finally:
        os.umask(old_umask)
    assert profile.resume is not None
    assert stat.S_IMODE(resumes.stat().st_mode) == 0o700
    assert stat.S_IMODE((resumes / profile.resume.filename).stat().st_mode) == 0o600


def test_schema_version_is_pinned_to_the_current_version() -> None:
    assert CandidateProfile().schema_version == 1
    for bad in (0, 2, 999):
        with pytest.raises(ValidationError):
            CandidateProfile.model_validate({"schema_version": bad})
