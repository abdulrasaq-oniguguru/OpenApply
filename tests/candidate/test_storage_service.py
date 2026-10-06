from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

from openapply.candidate.models import CandidateProfile, Eligibility, Identity
from openapply.candidate.service import CandidateService, ResumeError
from openapply.candidate.storage import ProfileError, ProfileStorage


def _profile() -> CandidateProfile:
    return CandidateProfile(
        identity=Identity(full_name="Ada Lovelace", email="ada@example.com"),
        skills=["Python"],
    )


def test_load_missing_returns_none(isolated_home: Path) -> None:
    storage = ProfileStorage()
    assert storage.exists() is False
    assert storage.load() is None


def test_save_and_load_round_trip_under_env_home(isolated_home: Path) -> None:
    storage = ProfileStorage()
    path = storage.save(_profile())
    assert path == isolated_home / "profile.json"
    assert storage.load() == _profile()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_profile_file_is_owner_only(isolated_home: Path) -> None:
    path = ProfileStorage().save(_profile())
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_corrupt_json_gives_profile_error(isolated_home: Path) -> None:
    isolated_home.mkdir(parents=True)
    (isolated_home / "profile.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ProfileError, match="Cannot read"):
        ProfileStorage().load()


def test_schema_violation_gives_profile_error(isolated_home: Path) -> None:
    isolated_home.mkdir(parents=True)
    (isolated_home / "profile.json").write_text(
        json.dumps({"identity": {"email": "bogus"}}), encoding="utf-8"
    )
    with pytest.raises(ProfileError, match="Invalid profile"):
        ProfileStorage().load()


def test_newer_schema_version_is_refused(isolated_home: Path) -> None:
    isolated_home.mkdir(parents=True)
    (isolated_home / "profile.json").write_text(
        json.dumps({"schema_version": 999, "future_field": 1}), encoding="utf-8"
    )
    with pytest.raises(ProfileError, match="Upgrade"):
        ProfileStorage().load()


def test_save_leaves_no_temp_files(isolated_home: Path) -> None:
    ProfileStorage().save(_profile())
    ProfileStorage().save(_profile())
    assert [p.name for p in isolated_home.iterdir()] == ["profile.json"]


# --- service -----------------------------------------------------------------


def _resume(
    tmp_path: Path, name: str = "My CV (final).pdf", content: bytes = b"%PDF-1.4 x"
) -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


def test_attach_resume_copies_into_data_dir(tmp_path: Path, isolated_home: Path) -> None:
    service = CandidateService()
    source = _resume(tmp_path)
    profile = service.attach_resume(_profile(), source)

    assert profile.resume is not None
    assert profile.resume.original_name == "My CV (final).pdf"
    stored = service.resume_file(profile)
    assert stored is not None
    assert stored.parent == isolated_home / "resumes"
    assert stored.read_bytes() == source.read_bytes()
    assert " " not in stored.name and "(" not in stored.name  # sanitized
    source.unlink()
    assert service.resume_file(profile) is not None  # independent of the original


def test_same_resume_attached_twice_is_idempotent(tmp_path: Path, isolated_home: Path) -> None:
    service = CandidateService()
    source = _resume(tmp_path)
    first = service.attach_resume(_profile(), source)
    second = service.attach_resume(first, source)
    assert first.resume == second.resume
    assert len(list((isolated_home / "resumes").iterdir())) == 1


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("cv.txt", b"x", ".pdf or .docx"),
        ("cv.pdf", b"", "empty"),
    ],
)
def test_attach_resume_rejections(tmp_path: Path, name: str, content: bytes, message: str) -> None:
    with pytest.raises(ResumeError, match=message):
        CandidateService().attach_resume(_profile(), _resume(tmp_path, name, content))


def test_attach_missing_and_oversized_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = CandidateService()
    with pytest.raises(ResumeError, match="not found"):
        service.attach_resume(_profile(), tmp_path / "ghost.pdf")
    monkeypatch.setattr("openapply.candidate.service.MAX_RESUME_BYTES", 5)
    with pytest.raises(ResumeError, match="larger"):
        service.attach_resume(_profile(), _resume(tmp_path, content=b"123456789"))


def test_completeness_helpers() -> None:
    assert CandidateService.missing_required(CandidateProfile()) == ["full name", "email"]
    assert CandidateService.missing_required(_profile()) == []
    assert set(CandidateService.unknown_eligibility(_profile())) == {
        "requires_sponsorship",
        "willing_to_relocate",
        "authorized_countries",
    }
    known = _profile().model_copy(
        update={
            "eligibility": Eligibility(
                authorized_countries=["NG"], requires_sponsorship=False, willing_to_relocate=True
            )
        }
    )
    assert CandidateService.unknown_eligibility(known) == []
