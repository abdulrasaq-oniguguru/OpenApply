"""Turn extracted resume text into a reviewable candidate-profile preview."""

from __future__ import annotations

from pydantic import Field

from openapply.candidate.models import (
    CandidateProfile,
    Certification,
    Education,
    Experience,
    Identity,
    Links,
    Project,
    ResumeRef,
    Text,
    _Model,
)
from openapply.candidate.parser import profile_hints
from openapply.prompts.resume_extraction import build_resume_extraction_prompt
from openapply.providers.base import AgentProvider
from openapply.providers.structured import generate_structured


class ResumeProfileDraft(_Model):
    """Career facts a provider may extract; sensitive answers are deliberately absent."""

    identity: Identity = Field(default_factory=Identity)
    links: Links = Field(default_factory=Links)
    summary: Text | None = None
    skills: list[Text] = Field(default_factory=list)
    experience: list[Experience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)


def local_draft(text: str) -> ResumeProfileDraft:
    return ResumeProfileDraft.model_validate(profile_hints(text))


async def provider_draft(provider: AgentProvider, text: str) -> ResumeProfileDraft:
    result = await generate_structured(
        provider,
        build_resume_extraction_prompt(text),
        ResumeProfileDraft,
        timeout=180,
    )
    return result.value


def _value(current: object, extracted: object) -> object:
    if current is None or current == "" or current == []:
        return extracted
    return current


def merge_resume_draft(
    current: CandidateProfile, draft: ResumeProfileDraft, resume: ResumeRef
) -> CandidateProfile:
    """Fill empty profile fields while preserving every value the user already supplied."""
    identity = current.identity.model_copy(
        update={
            name: _value(getattr(current.identity, name), getattr(draft.identity, name))
            for name in Identity.model_fields
        }
    )
    links = current.links.model_copy(
        update={
            name: _value(getattr(current.links, name), getattr(draft.links, name))
            for name in Links.model_fields
        }
    )
    return current.model_copy(
        update={
            "identity": identity,
            "links": links,
            "summary": _value(current.summary, draft.summary),
            "skills": _value(current.skills, draft.skills),
            "experience": _value(current.experience, draft.experience),
            "education": _value(current.education, draft.education),
            "certifications": _value(current.certifications, draft.certifications),
            "projects": _value(current.projects, draft.projects),
            "resume": resume,
        }
    )
