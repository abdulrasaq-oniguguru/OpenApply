"""Eligibility signals in a posting, checked against what the profile *explicitly* says.

The rule for sensitive facts is "never guess": a posting that says it will not sponsor
visas is a *blocker* only if the profile explicitly says sponsorship is needed. If the
profile answer is unknown (``None``) the result is "needs your confirmation", not a
guess in either direction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from openapply.candidate.models import CandidateProfile
from openapply.jobs.models import JobPosting
from openapply.security.text import strip_control_chars

_NO_SPONSORSHIP = tuple(
    re.compile(p, re.I)
    for p in (
        r"\b(?:do(?:es)?\s+not|don'?t|doesn'?t|will\s+not|won'?t|cannot|can'?t|unable\s+to|"
        r"not\s+able\s+to|not\s+(?:currently\s+)?(?:offer|provid|sponsor|support)\w*)\s+"
        r"(?:\w+\s+){0,3}sponsor",
        r"\bno\s+(?:\w+\s+){0,2}sponsorship\b",
        r"\bsponsorship\s+(?:is\s+)?(?:not\s+(?:available|offered|provided)|unavailable)\b",
        r"\bwithout\s+(?:\w+\s+){0,2}sponsorship\b",
        r"\bmust\s+not\s+require\s+(?:\w+\s+){0,2}sponsorship\b",
        r"\bnot\s+eligible\s+for\s+(?:\w+\s+){0,2}sponsorship\b",
    )
)
_AUTHORIZED_IN = re.compile(
    r"(?i:(?:must|need\s+to|required\s+to|should)\s+(?:be\s+)?(?:legally\s+)?"
    r"(?:authori[sz]ed|eligible|permitted)\s+to\s+work\s+(?:legally\s+)?in|"
    r"right\s+to\s+work\s+in)\s+(?i:the\s+)?"
    r"([A-Z][\w.&' -]{1,40}?)"
    r"(?=[.,;:()\n]|\s+(?i:without|and|or|at|on|for|with|to)\b|$)"
)
_CLEARANCE = re.compile(
    r"\b(?:security\s+clearance|secret\s+clearance|top[- ]secret|ts/sci|"
    r"clearance\s+(?:is\s+)?required)\b",
    re.I,
)
_CITIZENSHIP = re.compile(
    r"\b(?:u\.?s\.?\s+citizen(?:s|ship)?|citizenship\s+(?:is\s+)?required|citizens?\s+only|"
    r"must\s+be\s+a\s+(?:\w+\s+){0,2}citizen)\b",
    re.I,
)

_COUNTRY_ALIASES = {
    "us": "united states",
    "u.s": "united states",
    "u.s.a": "united states",
    "usa": "united states",
    "america": "united states",
    "uk": "united kingdom",
    "u.k": "united kingdom",
    "britain": "united kingdom",
    "great britain": "united kingdom",
}


def canonical_country(name: str) -> str:
    cleaned = re.sub(r"^the\s+", "", name.strip().casefold()).strip(" .")
    return _COUNTRY_ALIASES.get(cleaned, cleaned)


@dataclass
class EligibilityFindings:
    blockers: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)
    needs_confirmation: list[str] = field(default_factory=list)

    def confirm(self, question: str) -> None:
        if question not in self.needs_confirmation:
            self.needs_confirmation.append(question)


def posting_text(job: JobPosting) -> str:
    parts = [job.raw_text or "", job.description or "", *job.requirements, *job.responsibilities]
    return "\n".join(p for p in parts if p)


def structured_text(job: JobPosting) -> str:
    """The AI-extracted posting fields, as opposed to the raw page text."""
    parts = [job.description or "", *job.requirements, *job.preferred_requirements]
    return "\n".join(p for p in [*parts, *job.responsibilities] if p)


def _quote(patterns: tuple[re.Pattern[str], ...], text: str) -> str | None:
    """The sentence containing the first match, so the user can check the evidence."""
    for pattern in patterns:
        if match := pattern.search(text):
            stops = (".", "!", "?", chr(10))
            left = max(text.rfind(c, 0, match.start()) for c in stops)
            rights = [i for c in stops if (i := text.find(c, match.end())) != -1]
            right = min(rights) if rights else len(text)
            sentence = " ".join(strip_control_chars(text[left + 1 : right + 1]).split())
            return sentence[:200]
    return None


def check_eligibility(profile: CandidateProfile, job: JobPosting) -> EligibilityFindings:
    """Compare posting signals with the profile's *explicit* answers.

    Page text cannot be authenticated, so a statement found only in the raw page text never
    forces a verdict. A blocker needs the statement in the extracted posting as well; a
    page-text-only statement is a concern. Either way the matching sentence is quoted so the
    user can check it. Nothing here ever takes an action.
    """
    text = posting_text(job)
    eligibility = profile.eligibility
    findings = EligibilityFindings()

    extracted_quote = _quote(_NO_SPONSORSHIP, structured_text(job))
    page_quote = _quote(_NO_SPONSORSHIP, job.raw_text or "")
    if extracted_quote or page_quote:
        if eligibility.requires_sponsorship is True:
            if extracted_quote:
                findings.blockers.append(
                    f'The posting says "{extracted_quote}", and your profile says you need '
                    "sponsorship."
                )
            else:
                findings.concerns.append(
                    f'The page text says "{page_quote}", but this is not in the extracted '
                    "posting. Your profile says you need sponsorship, so check the posting "
                    "yourself before relying on it."
                )
        elif eligibility.requires_sponsorship is None:
            findings.confirm(
                "Will you require visa sponsorship? (the posting says none is offered)"
            )

    countries = [m.group(1).strip() for m in _AUTHORIZED_IN.finditer(text)]
    for country in dict.fromkeys(countries):
        if eligibility.authorized_countries:
            wanted = canonical_country(country)
            if not any(canonical_country(c) == wanted for c in eligibility.authorized_countries):
                findings.concerns.append(
                    f"The posting requires authorization to work in {country}; your profile "
                    f"lists: {', '.join(eligibility.authorized_countries)}."
                )
        else:
            findings.confirm(f"Are you authorized to work in {country}? (the posting requires it)")

    if _CLEARANCE.search(text):
        findings.confirm("Do you hold the security clearance the posting mentions?")
    if _CITIZENSHIP.search(text):
        findings.confirm("Do you meet the citizenship requirement the posting mentions?")
    return findings
