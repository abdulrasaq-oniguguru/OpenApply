"""Test builders for profiles and postings."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from openapply.candidate.models import (
    CandidateProfile,
    Eligibility,
    Experience,
    Identity,
    Preferences,
    RemotePreference,
)
from openapply.jobs.models import EmploymentType, JobPosting, RemoteStatus, SalaryPeriod

TODAY = date(2026, 6, 1)


def make_profile(
    *,
    skills: list[str] | None = None,
    experience: list[Experience] | None = None,
    city: str | None = "Abuja",
    country: str | None = "Nigeria",
    preferences: Preferences | None = None,
    eligibility: Eligibility | None = None,
    summary: str | None = None,
) -> CandidateProfile:
    return CandidateProfile(
        identity=Identity(
            full_name="Ada Lovelace", email="ada@example.com", city=city, country=country
        ),
        summary=summary,
        skills=["Python", "Django", "PostgreSQL", "REST APIs"] if skills is None else skills,
        experience=(
            [
                Experience(
                    company="Acme", title="Backend Developer", start=date(2022, 1, 1), current=True
                )
            ]
            if experience is None
            else experience
        ),
        preferences=preferences
        or Preferences(
            roles=["Backend Engineer"],
            remote_preference=RemotePreference.REMOTE,
            employment_types=["full-time"],
            salary_minimum=50_000,
            salary_currency="USD",
        ),
        eligibility=eligibility
        or Eligibility(
            authorized_countries=["Nigeria"], requires_sponsorship=False, willing_to_relocate=False
        ),
    )


def make_job(**overrides: Any) -> JobPosting:
    base: dict[str, Any] = {
        "id": "job-1",
        "source_url": "https://careers.example.com/jobs/1",
        "title": "Backend Engineer",
        "company": "Example Corp",
        "location": "Remote",
        "employment_type": EmploymentType.FULL_TIME,
        "remote_status": RemoteStatus.REMOTE,
        "description": "Build and run payments APIs.",
        "responsibilities": ["Build APIs"],
        "requirements": [
            "3-5 years of professional Python experience",
            "Experience with Django and PostgreSQL",
            "Comfortable designing and documenting REST APIs",
        ],
        "preferred_requirements": ["Kubernetes", "Go"],
        "salary_min": 60_000,
        "salary_max": 80_000,
        "currency": "USD",
        "salary_period": SalaryPeriod.YEAR,
        "extracted_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    base.update(overrides)
    return JobPosting(**base)
