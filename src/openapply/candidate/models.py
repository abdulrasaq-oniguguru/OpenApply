"""Candidate profile schema.

Sensitive eligibility fields are ``bool | None``. ``None`` means "unknown: do
not guess, ask the user", and is a first-class state, not a missing value.
"""

from __future__ import annotations

import re
from datetime import date
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

SCHEMA_VERSION = 1

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

Text = Annotated[str, StringConstraints(strip_whitespace=True)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class RemotePreference(StrEnum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"
    ANY = "any"


class Identity(_Model):
    full_name: Text = ""
    preferred_name: Text | None = None
    email: Text = ""
    phone: Text | None = None
    city: Text | None = None
    country: Text | None = None
    timezone: Text | None = None

    @field_validator("email")
    @classmethod
    def _check_email(cls, value: str) -> str:
        if value and not _EMAIL_RE.match(value):
            raise ValueError(f"'{value}' is not a valid email address")
        return value

    @property
    def first_name(self) -> str:
        parts = self.full_name.split()
        return parts[0] if parts else ""

    @property
    def last_name(self) -> str:
        parts = self.full_name.split()
        return " ".join(parts[1:])


class Links(_Model):
    linkedin: Text | None = None
    github: Text | None = None
    portfolio: Text | None = None


class Experience(_Model):
    company: Text
    title: Text
    location: Text | None = None
    start: date | None = None
    end: date | None = None  # None with ``current`` True means ongoing
    current: bool = False
    summary: Text | None = None
    highlights: list[Text] = Field(default_factory=list)
    skills: list[Text] = Field(default_factory=list)


class Education(_Model):
    institution: Text
    degree: Text | None = None
    field_of_study: Text | None = None
    start: date | None = None
    end: date | None = None


class Certification(_Model):
    name: Text
    issuer: Text | None = None
    issued: date | None = None


class Project(_Model):
    name: Text
    description: Text | None = None
    url: Text | None = None
    skills: list[Text] = Field(default_factory=list)


class Preferences(_Model):
    roles: list[Text] = Field(default_factory=list)
    industries: list[Text] = Field(default_factory=list)
    remote_preference: RemotePreference | None = None
    locations: list[Text] = Field(default_factory=list)
    salary_minimum: Annotated[int, Field(ge=0)] | None = None
    salary_currency: Text | None = None
    employment_types: list[Text] = Field(default_factory=list)


class Eligibility(_Model):
    authorized_countries: list[Text] = Field(default_factory=list)
    requires_sponsorship: bool | None = None
    willing_to_relocate: bool | None = None


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_STORED_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+-(?P<short>[0-9a-f]{8})\.(?:pdf|docx)$")


class ResumeRef(_Model):
    """A resume copied into the OpenApply data directory.

    ``filename`` must follow the generated convention ``<stem>-<first 8 of sha256>.<ext>``
    so a hand-edited reference can't point at some other file.
    """

    filename: Text
    original_name: Text
    sha256: Text

    @model_validator(mode="after")
    def _check_convention(self) -> ResumeRef:
        if not _SHA256_RE.match(self.sha256):
            raise ValueError("resume sha256 must be 64 lowercase hex characters")
        match = _STORED_NAME_RE.match(self.filename)
        if match is None or match.group("short") != self.sha256[:8]:
            raise ValueError("resume filename does not match its sha256")
        return self


class CandidateProfile(_Model):
    # Only the current version is writable; newer files are refused at load time.
    schema_version: Literal[1] = 1
    identity: Identity = Field(default_factory=Identity)
    links: Links = Field(default_factory=Links)
    summary: Text | None = None
    skills: list[Text] = Field(default_factory=list)
    experience: list[Experience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    preferences: Preferences = Field(default_factory=Preferences)
    eligibility: Eligibility = Field(default_factory=Eligibility)
    resume: ResumeRef | None = None
