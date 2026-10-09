"""Deterministic checks for mandatory qualifications in regulated roles.

The general skill scorer cannot infer licences or professional degrees from ordinary
technology keywords.  These checks deliberately cover only high-confidence role and
credential phrases.  Missing evidence means "not found in this profile", never a claim
that the person does not hold the qualification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from openapply.candidate.models import CandidateProfile
from openapply.jobs.models import JobPosting


@dataclass(frozen=True)
class _CredentialRule:
    label: str
    role: re.Pattern[str]
    evidence: re.Pattern[str]
    requirement: re.Pattern[str] | None = None


def _pattern(value: str) -> re.Pattern[str]:
    return re.compile(value, re.IGNORECASE)


_RULES = (
    _CredentialRule(
        "oncology specialist qualification",
        _pattern(r"\boncologist\b"),
        _pattern(r"\boncolog(?:ist|y|ical)\b"),
    ),
    _CredentialRule(
        "medical degree or physician licence",
        _pattern(
            r"\b(?:general\s+)?physician\b|\bmedical\s+doctor\b|\boncologist\b|"
            r"\bsurgeon\b|\bpsychiatrist\b|\bcardiologist\b|\bpediatrician\b|"
            r"\bdermatologist\b|\bgyn(?:a)?ecologist\b"
        ),
        _pattern(
            r"\bmbbs\b|(?<!\w)m\.?d\.?(?!\w)|doctor of medicine|bachelor of medicine|"
            r"medicine and surgery|medical licen[cs]e|\bphysician\b|\bmedical doctor\b|"
            r"\boncolog(?:ist|y|ical)\b|\bsurgeon\b|\bpsychiatrist\b|\bcardiologist\b|"
            r"\bpediatrician\b|\bdermatologist\b|\bgyn(?:a)?ecologist\b"
        ),
        _pattern(r"medical licen[cs]e|licensed (?:medical )?(?:doctor|physician)"),
    ),
    _CredentialRule(
        "nursing qualification or registration",
        _pattern(r"\bregistered nurse\b|\bnurse practitioner\b|\bclinical nurse\b"),
        _pattern(r"\bnursing\b|\bregistered nurse\b|\bnurse practitioner\b|nursing licen[cs]e"),
        _pattern(r"nursing licen[cs]e|registered nurse|\bRN\b"),
    ),
    _CredentialRule(
        "dental degree or licence",
        _pattern(r"\bdentist\b|\bdental surgeon\b"),
        _pattern(r"\bdentist\b|\bdentistry\b|\bbds\b|\bdds\b|dental licen[cs]e"),
        _pattern(r"dental licen[cs]e|licensed dentist|\bbds\b|\bdds\b"),
    ),
    _CredentialRule(
        "pharmacy degree or registration",
        _pattern(r"\bpharmacist\b"),
        _pattern(r"\bpharmacist\b|\bpharmacy\b|\bpharm\.?d\b|pharmacy licen[cs]e"),
        _pattern(r"pharmacy licen[cs]e|registered pharmacist|\bpharm\.?d\b"),
    ),
    _CredentialRule(
        "legal qualification or bar admission",
        _pattern(r"\battorney\b|\blawyer\b|\bsolicitor\b|\bbarrister\b"),
        _pattern(
            r"\battorney\b|\blawyer\b|\bsolicitor\b|\bbarrister\b|bar admission|"
            r"called to the bar|\bll\.?b\b|\bjuris doctor\b|(?<!\w)j\.?d\.?(?!\w)"
        ),
        _pattern(r"bar admission|licensed (?:attorney|lawyer)|called to the bar"),
    ),
    _CredentialRule(
        "required accounting certification",
        _pattern(r"\bcertified public accountant\b|\bchartered accountant\b"),
        _pattern(r"\bcpa\b|\bacca\b|\baca\b|certified public accountant|chartered accountant"),
        _pattern(r"\bcpa\b|\bacca\b|\baca\b|certified public accountant|chartered accountant"),
    ),
)

_DEGREE_RULES = (
    (
        "required doctorate",
        _pattern(r"(?<!\w)ph\.?d\.?(?!\w)|\bdoctorate\b|\bdoctoral degree\b"),
        _pattern(r"(?<!\w)ph\.?d\.?(?!\w)|\bdoctorate\b|\bdoctoral degree\b"),
    ),
    (
        "required master's degree",
        _pattern(r"\bmaster(?:['\u2019]?s)? degree\b|\bm\.?sc\b|\bmba\b"),
        _pattern(r"\bmaster(?:['\u2019]?s)?\b|\bm\.?sc\b|\bmba\b"),
    ),
    (
        "required bachelor's degree",
        _pattern(r"\bbachelor(?:['\u2019]?s)? degree\b|\bb\.?sc\b|\bb\.?eng\b"),
        _pattern(r"\bbachelor(?:['\u2019]?s)?\b|\bb\.?sc\b|\bb\.?eng\b"),
    ),
)


@dataclass
class CredentialFindings:
    matched: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)


def _candidate_text(profile: CandidateProfile) -> str:
    parts = [profile.summary or "", *profile.skills]
    for experience in profile.experience:
        parts.extend(
            [
                experience.title,
                experience.summary or "",
                *experience.highlights,
                *experience.skills,
            ]
        )
    for education in profile.education:
        parts.extend(
            [education.degree or "", education.field_of_study or "", education.institution]
        )
    for certification in profile.certifications:
        parts.extend([certification.name, certification.issuer or ""])
    return "\n".join(part for part in parts if part)


def check_credentials(profile: CandidateProfile, job: JobPosting) -> CredentialFindings:
    """Return mandatory credentials found or absent with high confidence."""
    title = job.title
    requirements = "\n".join(job.requirements)
    evidence = _candidate_text(profile)
    findings = CredentialFindings()

    for rule in _RULES:
        required = bool(rule.role.search(title)) or bool(
            rule.requirement and rule.requirement.search(requirements)
        )
        if not required:
            continue
        if rule.evidence.search(evidence):
            findings.matched.append(rule.label)
        else:
            findings.blockers.append(
                f"Mandatory credential not found in your profile: {rule.label}."
            )

    for label, requirement, candidate_evidence in _DEGREE_RULES:
        matching_lines = [line for line in job.requirements if requirement.search(line)]
        if not matching_lines:
            continue
        if any(re.search(r"\bor equivalent\b", line, re.IGNORECASE) for line in matching_lines):
            continue
        if candidate_evidence.search(evidence):
            findings.matched.append(label)
        else:
            findings.blockers.append(f"Mandatory credential not found in your profile: {label}.")

    findings.matched = list(dict.fromkeys(findings.matched))
    findings.blockers = list(dict.fromkeys(findings.blockers))
    return findings
