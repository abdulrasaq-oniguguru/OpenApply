from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import cast

from docx import Document

from openapply.candidate.models import CandidateProfile, Eligibility, Identity, Links, Preferences
from openapply.candidate.parser import LocalResumeTextExtractor, profile_hints
from openapply.candidate.resume_import import ResumeProfileDraft, merge_resume_draft
from openapply.candidate.service import CandidateService


def _docx(path: Path, lines: list[str]) -> Path:
    document = Document()
    for line in lines:
        document.add_paragraph(line)
    document.save(str(path))
    return path


def test_docx_is_extracted_locally_and_yields_conservative_hints(tmp_path: Path) -> None:
    path = _docx(
        tmp_path / "Ada CV.docx",
        [
            "Ada Lovelace",
            "ada@example.com | +44 20 7946 0958 | github.com/ada",
            "SUMMARY",
            "Built reliable analytical systems.",
            "SKILLS",
            "Python, SQL | Technical writing",
            "EXPERIENCE",
            "This is not deterministically structured.",
        ],
    )

    text = LocalResumeTextExtractor().extract_text(path)
    hints = profile_hints(text)

    identity = cast(Identity, hints["identity"])
    links = cast(Links, hints["links"])
    assert identity.full_name == "Ada Lovelace"
    assert identity.email == "ada@example.com"
    assert links.github == "https://github.com/ada"
    assert hints["summary"] == "Built reliable analytical systems."
    assert hints["skills"] == ["Python", "SQL", "Technical writing"]
    assert "This is not deterministically structured" in text


def test_resume_merge_fills_blanks_but_preserves_user_answers(
    tmp_path: Path, isolated_home: Path
) -> None:
    current = CandidateProfile(
        identity=Identity(full_name="User Chosen Name"),
        preferences=Preferences(roles=["Backend Engineer"]),
        eligibility=Eligibility(requires_sponsorship=False),
    )
    attached = CandidateService().attach_resume_data(current, "resume.pdf", b"%PDF-1.4 x")
    assert attached.resume is not None
    draft = ResumeProfileDraft(
        identity=Identity(full_name="Resume Name", email="resume@example.com"),
        skills=["Python"],
    )

    merged = merge_resume_draft(attached, draft, attached.resume)

    assert merged.identity.full_name == "User Chosen Name"
    assert merged.identity.email == "resume@example.com"
    assert merged.skills == ["Python"]
    assert merged.preferences.roles == ["Backend Engineer"]
    assert merged.eligibility.requires_sponsorship is False
    assert merged.eligibility.authorized_countries == []


def test_standard_work_and_education_sections_are_structured_without_ai() -> None:
    hints = profile_hints(
        "\n".join(
            [
                "Ada Lovelace",
                "ada@example.com",
                "EXPERIENCE",
                "Software Engineer",
                "January 2020 - Present",
                "Analytical Engines Ltd | London",
                "Built dependable computing systems.",
                "EDUCATION",
                "B.Sc. Mathematics",
                "University of London",
                "2013 - 2017",
            ]
        )
    )

    experience = hints["experience"]
    education = hints["education"]
    assert isinstance(experience, list) and len(experience) == 1
    assert isinstance(education, list) and len(education) == 1
    assert experience[0].company == "Analytical Engines Ltd"
    assert experience[0].start == date(2020, 1, 1)
    assert experience[0].current is True
    assert education[0].institution == "University of London"
    assert education[0].end == date(2017, 1, 1)
