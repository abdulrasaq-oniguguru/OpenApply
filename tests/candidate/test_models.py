from __future__ import annotations

import pytest
from pydantic import ValidationError

from openapply.candidate.models import CandidateProfile, Eligibility, Identity, RemotePreference


def test_sensitive_eligibility_defaults_to_unknown() -> None:
    eligibility = Eligibility()
    assert eligibility.requires_sponsorship is None
    assert eligibility.willing_to_relocate is None
    assert CandidateProfile().eligibility.requires_sponsorship is None


def test_unknown_stays_unknown_through_json_round_trip() -> None:
    profile = CandidateProfile(eligibility=Eligibility(requires_sponsorship=False))
    loaded = CandidateProfile.model_validate_json(profile.model_dump_json())
    assert loaded.eligibility.requires_sponsorship is False
    assert loaded.eligibility.willing_to_relocate is None


@pytest.mark.parametrize("bad", ["nope", "a@b", "a b@c.com", "@c.com"])
def test_invalid_email_rejected(bad: str) -> None:
    with pytest.raises(ValidationError):
        Identity(email=bad)


def test_valid_email_and_empty_email_allowed() -> None:
    assert Identity(email="a.b@example.co.uk").email == "a.b@example.co.uk"
    assert Identity().email == ""


@pytest.mark.parametrize(
    ("full", "first", "last"),
    [
        ("Ada Lovelace", "Ada", "Lovelace"),
        ("  Ada   King Lovelace ", "Ada", "King Lovelace"),
        ("Cher", "Cher", ""),
        ("", "", ""),
    ],
)
def test_first_and_last_name_derivation(full: str, first: str, last: str) -> None:
    identity = Identity(full_name=full)
    assert (identity.first_name, identity.last_name) == (first, last)


def test_whitespace_is_stripped_and_unknown_fields_rejected() -> None:
    assert Identity(full_name="  Ada  ").full_name == "Ada"
    with pytest.raises(ValidationError):
        CandidateProfile.model_validate({"identity": {"ssn": "123"}})


def test_remote_preference_and_negative_salary_validated() -> None:
    with pytest.raises(ValidationError):
        CandidateProfile.model_validate({"preferences": {"remote_preference": "sometimes"}})
    with pytest.raises(ValidationError):
        CandidateProfile.model_validate({"preferences": {"salary_minimum": -1}})
    ok = CandidateProfile.model_validate({"preferences": {"remote_preference": "hybrid"}})
    assert ok.preferences.remote_preference is RemotePreference.HYBRID
