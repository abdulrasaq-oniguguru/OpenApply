from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openapply.candidate.models import CandidateProfile, Eligibility, Identity
from openapply.candidate.storage import ProfileStorage
from openapply.cli.app import app
from openapply.config.settings import load_settings
from openapply.providers.models import ProviderStatus

runner = CliRunner()

READY = [
    ProviderStatus(name="codex", display_name="Codex", installed=True, available=True, detail="ok"),
    ProviderStatus(
        name="ollama", display_name="Ollama", installed=True, available=True, detail="ok"
    ),
    ProviderStatus(
        name="gemini",
        display_name="Gemini",
        installed=True,
        available=False,
        supports_generation=False,
    ),
]


@pytest.fixture(autouse=True)
def fake_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("openapply.cli.commands.setup._detect_providers", lambda: READY)


def _answers(*lines: str) -> str:
    return "\n".join(lines) + "\n"


# Prompt order: name, email, preferred, phone, city, country, timezone | linkedin, github,
# portfolio | skills, summary | roles, industries, employment, remote, locations, salary,
# [currency] | authorized, sponsorship, relocate | resume | default provider
def _full_input(resume: str = "") -> str:
    return _answers(
        "Ada Lovelace", "ada@example.com", "Ada", "+234 800", "Abuja", "Nigeria", "Africa/Lagos",
        "https://linkedin.com/in/ada", "https://github.com/ada", "",
        "Python, Django, python, SQL", "Backend engineer",
        "Backend Engineer, Python Developer", "", "full-time", "remote", "Remote, Lagos",
        "60000", "USD",
        "Nigeria", "unknown", "no",
        resume,
        "ollama",
    )  # fmt: skip


def test_setup_creates_profile(tmp_path: Path, isolated_home: Path) -> None:
    resume = tmp_path / "cv.pdf"
    resume.write_bytes(b"%PDF-1.4 x")
    result = runner.invoke(app, ["setup"], input=_full_input(str(resume)))
    assert result.exit_code == 0, result.output

    profile = ProfileStorage().load()
    assert profile is not None
    assert profile.identity.full_name == "Ada Lovelace"
    assert profile.identity.first_name == "Ada"
    assert profile.links.github == "https://github.com/ada"
    assert profile.links.portfolio is None
    assert profile.skills == ["Python", "Django", "SQL"]  # de-duplicated, order kept
    assert profile.preferences.roles == ["Backend Engineer", "Python Developer"]
    assert profile.preferences.salary_minimum == 60000
    assert profile.preferences.salary_currency == "USD"
    assert profile.preferences.remote_preference is not None
    assert profile.eligibility.requires_sponsorship is None  # 'unknown' preserved, not guessed
    assert profile.eligibility.willing_to_relocate is False
    assert profile.resume is not None
    assert (isolated_home / "resumes" / profile.resume.filename).is_file()
    assert load_settings().default_provider == "ollama"
    assert "requires_sponsorship" in result.output  # reported as unconfirmed


def test_setup_reprompts_on_bad_input_and_missing_required(isolated_home: Path) -> None:
    answers = _answers(
        "", "Ada Lovelace",  # empty name rejected, then accepted
        "not-an-email", "ada@example.com",  # bad email rejected, then accepted
        *([""] * 6),  # preferred, phone, city, country, timezone, linkedin
        *([""] * 4),  # github, portfolio, skills, summary
        *([""] * 3),  # roles, industries, employment
        "sometimes", "hybrid",  # bad remote choice, then valid
        "", "abc", "",  # locations, bad salary, then none
        "", "maybe", "no", "",  # authorized, bad tri-state, no, relocate unchanged
        "",  # resume
        "",  # provider: accept default
    )  # fmt: skip
    result = runner.invoke(app, ["setup"], input=answers)
    assert result.exit_code == 0, result.output
    assert "is required" in result.output
    assert "not a valid email" in result.output
    assert "Choose one of" in result.output
    assert "whole number" in result.output
    assert "yes, no or unknown" in result.output
    profile = ProfileStorage().load()
    assert profile is not None
    assert profile.identity.email == "ada@example.com"
    assert profile.eligibility.requires_sponsorship is False


def test_setup_keeps_existing_values_and_dash_clears(isolated_home: Path) -> None:
    ProfileStorage().save(
        CandidateProfile(
            identity=Identity(full_name="Ada Lovelace", email="ada@example.com", city="Abuja"),
            skills=["Python"],
            eligibility=Eligibility(requires_sponsorship=True),
        )
    )
    # Enter everywhere (keep) except: city cleared with '-'.
    answers = _answers(
        "", "", "", "", "-", *([""] * 40)
    )  # fmt: skip
    result = runner.invoke(app, ["setup"], input=answers)
    assert result.exit_code == 0, result.output
    profile = ProfileStorage().load()
    assert profile is not None
    assert profile.identity.full_name == "Ada Lovelace"
    assert profile.identity.city is None
    assert profile.skills == ["Python"]
    assert profile.eligibility.requires_sponsorship is True


def test_setup_bad_resume_then_skip(tmp_path: Path, isolated_home: Path) -> None:
    bad = tmp_path / "cv.txt"
    bad.write_text("x")
    answers = _answers(
        "Ada Lovelace", "ada@example.com", *([""] * 5), *([""] * 3), *([""] * 2),
        *([""] * 7), *([""] * 3),
        str(bad), "",  # rejected, then skipped
        "",
    )  # fmt: skip
    result = runner.invoke(app, ["setup"], input=answers)
    assert result.exit_code == 0, result.output
    assert ".pdf or .docx" in result.output
    profile = ProfileStorage().load()
    assert profile is not None and profile.resume is None


def test_setup_refuses_to_overwrite_corrupt_profile(isolated_home: Path) -> None:
    isolated_home.mkdir(parents=True)
    corrupt = isolated_home / "profile.json"
    corrupt.write_text("{broken", encoding="utf-8")
    result = runner.invoke(app, ["setup"])
    assert result.exit_code == 2
    assert corrupt.read_text(encoding="utf-8") == "{broken"  # untouched


# --- profile show / edit -------------------------------------------------------


def _saved() -> CandidateProfile:
    profile = CandidateProfile(
        identity=Identity(full_name="Ada Lovelace", email="ada@example.com"),
        skills=["Python"],
    )
    ProfileStorage().save(profile)
    return profile


def test_profile_show_without_profile() -> None:
    result = runner.invoke(app, ["profile", "show"])
    assert result.exit_code == 1
    assert "openapply setup" in result.output


def test_profile_show_marks_unknown_eligibility() -> None:
    _saved()
    result = runner.invoke(app, ["profile", "show"])
    assert result.exit_code == 0
    assert "Ada Lovelace" in result.output
    assert "UNKNOWN" in result.output


def test_profile_show_json() -> None:
    _saved()
    result = runner.invoke(app, ["profile", "show", "--json"])
    data = json.loads(result.output)
    assert data["identity"]["email"] == "ada@example.com"
    assert data["eligibility"]["requires_sponsorship"] is None


def test_profile_edit_applies_valid_change(monkeypatch: pytest.MonkeyPatch) -> None:
    _saved()

    def fake_edit(text: str, **_: object) -> str:
        data = json.loads(text)
        data["skills"].append("Rust")
        return json.dumps(data)

    monkeypatch.setattr("click.edit", fake_edit)
    result = runner.invoke(app, ["profile", "edit"])
    assert result.exit_code == 0, result.output
    profile = ProfileStorage().load()
    assert profile is not None and profile.skills == ["Python", "Rust"]


def test_profile_edit_invalid_then_retry_keeps_user_text(monkeypatch: pytest.MonkeyPatch) -> None:
    _saved()
    seen: list[str] = []

    def fake_edit(text: str, **_: object) -> str:
        seen.append(text)
        data = json.loads(text)
        data["identity"]["email"] = "broken" if len(seen) == 1 else "ada@new.example.com"
        return json.dumps(data)

    monkeypatch.setattr("click.edit", fake_edit)
    result = runner.invoke(app, ["profile", "edit"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "identity.email" in result.output
    assert '"broken"' in seen[1]  # second editor session starts from the user's edit
    profile = ProfileStorage().load()
    assert profile is not None and profile.identity.email == "ada@new.example.com"


def test_profile_edit_invalid_and_declined_keeps_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = _saved()
    monkeypatch.setattr("click.edit", lambda text, **_: '{"identity": {"email": "bad"}}')
    result = runner.invoke(app, ["profile", "edit"], input="n\n")
    assert result.exit_code == 1
    assert ProfileStorage().load() == original


def test_profile_edit_no_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    _saved()
    monkeypatch.setattr("click.edit", lambda text, **_: text)
    result = runner.invoke(app, ["profile", "edit"])
    assert result.exit_code == 0
    assert "No changes" in result.output


def test_profile_edit_rejects_malformed_resume_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = _saved()

    def fake_edit(text: str, **_: object) -> str:
        data = json.loads(text)
        data["resume"] = {"filename": "../../secret.pdf", "original_name": "x", "sha256": "0"}
        return json.dumps(data)

    monkeypatch.setattr("click.edit", fake_edit)
    result = runner.invoke(app, ["profile", "edit"], input="n\n")
    assert result.exit_code == 1
    assert "resume" in result.output
    assert ProfileStorage().load() == original


def test_profile_edit_cannot_swap_in_a_well_formed_resume_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _saved()

    def fake_edit(text: str, **_: object) -> str:
        data = json.loads(text)
        data["resume"] = {
            "filename": "other-aaaaaaaa.pdf",
            "original_name": "x",
            "sha256": "a" * 64,
        }
        return json.dumps(data)

    monkeypatch.setattr("click.edit", fake_edit)
    result = runner.invoke(app, ["profile", "edit"])
    assert result.exit_code == 0
    profile = ProfileStorage().load()
    assert profile is not None and profile.resume is None


def test_profile_edit_cannot_bump_schema_version(monkeypatch: pytest.MonkeyPatch) -> None:
    original = _saved()

    def fake_edit(text: str, **_: object) -> str:
        data = json.loads(text)
        data["schema_version"] = 2
        return json.dumps(data)

    monkeypatch.setattr("click.edit", fake_edit)
    result = runner.invoke(app, ["profile", "edit"], input="n\n")
    assert result.exit_code == 1
    assert "schema_version" in result.output
    assert ProfileStorage().load() == original  # still loadable; user not locked out


def test_show_and_doctor_flag_a_modified_resume(
    tmp_path: Path, isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openapply.candidate.service import CandidateService

    async def detect(self: object) -> list[ProviderStatus]:
        return READY

    monkeypatch.setattr("openapply.providers.registry.ProviderRegistry.detect", detect)
    pdf = tmp_path / "cv.pdf"
    pdf.write_bytes(b"%PDF original")
    service = CandidateService()
    profile = service.attach_resume(_saved(), pdf)
    service.save(profile)
    assert profile.resume is not None
    (isolated_home / "resumes" / profile.resume.filename).write_bytes(b"tampered")

    assert "modified since added" in runner.invoke(app, ["profile", "show"]).output
    assert "changed since it was added" in runner.invoke(app, ["doctor"]).output


def test_doctor_profile_line(monkeypatch: pytest.MonkeyPatch) -> None:
    async def detect(self: object) -> list[ProviderStatus]:
        return READY

    monkeypatch.setattr("openapply.providers.registry.ProviderRegistry.detect", detect)
    assert "not configured" in runner.invoke(app, ["doctor"]).output
    _saved()
    output = runner.invoke(app, ["doctor"]).output
    assert "configured" in output and "unconfirmed" in output
