"""Deterministic answers: fill what the profile *explicitly* knows, and nothing else.

The rule that shapes this module: **absence of data is never an answer**.

* eligibility questions (visa sponsorship, work authorization, relocation) are answered only
  from an explicit profile answer, and only when the question is clear and unambiguous
* a "no" is never inferred from the lack of a "yes" (a country missing from your list of
  authorized countries does not mean you are unauthorized)
* demographic, criminal-history, clearance, government-restriction and legal-acknowledgement
  questions are never answered or ticked
* free-text questions are not answered here at all (the generation module handles them)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from openapply.applications.models import (
    AnswerStatus,
    ApplicationField,
    FieldAnswer,
    FieldOption,
    FieldType,
    Intent,
    Sensitivity,
)
from openapply.applications.questions import normalize
from openapply.candidate.models import CandidateProfile, Experience
from openapply.candidate.service import ResumeStatus
from openapply.jobs.eligibility import canonical_country

HIGH_CONFIDENCE = 0.9  # eligibility answers need an unambiguous, clearly matched question

_YES = frozenset({"yes", "y", "true"})
_NO = frozenset({"no", "n", "false"})
_WITHOUT = re.compile(
    r"\b(without|not require|no need|do not require|don t require|don t need|"
    r"able to work without|not need)\b"
)
_NEGATED_AUTH = re.compile(
    r"\b(not|without|lack|lacking|unauthori[sz]ed)\b.*\bauthori[sz]|\bunauthori[sz]ed\b"
)
_COUNTRY_SYNONYMS = {
    "united states": {"united states", "united states of america", "usa", "us", "u s", "america"},
    "united kingdom": {"united kingdom", "uk", "u k", "great britain", "britain", "england"},
}

_NEEDS_USER_REASON = {
    Intent.DEMOGRAPHIC: "demographic questions are always yours to answer",
    Intent.CRIMINAL: "criminal-history questions are always yours to answer",
    Intent.CLEARANCE: "security-clearance questions are always yours to answer",
    Intent.GOVERNMENT_RESTRICTION: "government and legal-restriction questions are yours to answer",
    Intent.LEGAL_ACK: "legal acknowledgements and consents are never ticked for you",
}


@dataclass(frozen=True)
class AnswerContext:
    profile: CandidateProfile
    resume_path: Path | None = None
    resume_status: ResumeStatus = ResumeStatus.NONE


def _answer(
    field: ApplicationField,
    status: AnswerStatus,
    *,
    value: str | None = None,
    source: str = "",
    reason: str = "",
) -> FieldAnswer:
    return FieldAnswer(field_id=field.id, status=status, value=value, source=source, reason=reason)


def _needs_user(field: ApplicationField, reason: str) -> FieldAnswer:
    return _answer(field, AnswerStatus.NEEDS_USER, reason=reason)


def _missing(field: ApplicationField, reason: str) -> FieldAnswer:
    return _answer(field, AnswerStatus.MISSING, reason=reason)


# --- options ------------------------------------------------------------------------


def _expand_country(name: str) -> set[str]:
    canon = canonical_country(name)
    return _COUNTRY_SYNONYMS.get(canon, {canon})


def _option_for_text(options: list[FieldOption], wanted: str) -> FieldOption | None:
    """The single option matching ``wanted``; None when there is no match or it is unclear."""
    target = normalize(wanted)
    if not target or not options:
        return None
    exact = [o for o in options if normalize(o.label) == target or normalize(o.value) == target]
    if len(exact) == 1:
        return exact[0]
    synonyms = _expand_country(wanted)
    by_synonym = [
        o for o in options if normalize(o.label) in synonyms or normalize(o.value) in synonyms
    ]
    if len(by_synonym) == 1:
        return by_synonym[0]
    contained = [
        o
        for o in options
        if len(normalize(o.label)) > 2
        and (target in normalize(o.label).split() or normalize(o.label) in target.split())
    ]
    return contained[0] if len(contained) == 1 else None


def _yes_no_option(options: list[FieldOption], *, want_yes: bool) -> FieldOption | None:
    """The unambiguous yes (or no) option, or None."""
    hits = []
    for option in options:
        words = normalize(option.label).split() or normalize(option.value).split()
        if not words:
            continue
        first = words[0]
        if (first in _YES and want_yes) or (first in _NO and not want_yes):
            hits.append(option)
    return hits[0] if len(hits) == 1 else None


def _names_authorized_country(field: ApplicationField, countries: list[str]) -> str | None:
    text = normalize(f"{field.label} {field.hint or ''}")
    for country in countries:
        for alias in _expand_country(country):
            if alias and re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text):
                return country
    return None


# --- identity ----------------------------------------------------------------------


def _current_experience(profile: CandidateProfile) -> Experience | None:
    if not profile.experience:
        return None
    # A current role wins; otherwise the one that ended last (then the one that started last).
    return max(
        profile.experience,
        key=lambda e: (e.current, e.end or date.min, e.start or date.min),
    )


def _profile_value(intent: Intent, profile: CandidateProfile) -> tuple[str | None, str]:
    """(value, description of where it came from). The value is None when not on file."""
    identity = profile.identity
    experience = _current_experience(profile)
    table: dict[Intent, tuple[str | None, str]] = {
        Intent.FIRST_NAME: (identity.first_name or None, "profile: name"),
        Intent.LAST_NAME: (identity.last_name or None, "profile: name"),
        Intent.FULL_NAME: (identity.full_name or None, "profile: name"),
        Intent.PREFERRED_NAME: (identity.preferred_name, "profile: preferred name"),
        Intent.EMAIL: (identity.email or None, "profile: email"),
        Intent.PHONE: (identity.phone, "profile: phone"),
        Intent.CITY: (identity.city, "profile: city"),
        Intent.COUNTRY: (identity.country, "profile: country"),
        Intent.LOCATION: (
            ", ".join(p for p in (identity.city, identity.country) if p) or None,
            "profile: location",
        ),
        Intent.LINKEDIN: (profile.links.linkedin, "profile: LinkedIn"),
        Intent.GITHUB: (profile.links.github, "profile: GitHub"),
        Intent.PORTFOLIO: (profile.links.portfolio, "profile: portfolio"),
        Intent.CURRENT_COMPANY: (
            experience.company if experience else None,
            "profile: latest role",
        ),
        Intent.CURRENT_TITLE: (experience.title if experience else None, "profile: latest role"),
    }
    return table.get(intent, (None, ""))


_MISSING_FROM_PROFILE = {
    Intent.ADDRESS: "your street address is not stored in the profile",
    Intent.AVAILABILITY: "your availability is not stored in the profile",
    Intent.REFERRAL_SOURCE: "how you heard about the job cannot be known",
}


def _fill_identity(field: ApplicationField, context: AnswerContext) -> FieldAnswer:
    value, source = _profile_value(field.intent, context.profile)
    if not value:
        return _missing(field, "not in your profile")
    return _place_value(field, value, source)


def _place_value(field: ApplicationField, value: str, source: str) -> FieldAnswer:
    """Put ``value`` into a field, mapping it onto an option for selects and radios."""
    if field.type in {FieldType.SELECT, FieldType.RADIO}:
        option = _option_for_text(field.options, value)
        if option is None:
            return _missing(field, f"no option matches '{value}'")
        return _answer(field, AnswerStatus.FILLED, value=option.value, source=source)
    if field.type in {FieldType.CHECKBOX, FieldType.FILE}:
        return _needs_user(field, "this field type cannot be filled from a text value")
    if field.max_length is not None and len(value) > field.max_length:
        return _needs_user(
            field, f"'{value[:20]}...' is longer than the {field.max_length}-character limit"
        )
    return _answer(field, AnswerStatus.FILLED, value=value, source=source)


# --- documents ---------------------------------------------------------------------


def _accepts(field: ApplicationField, path: Path) -> bool:
    if not field.accept:
        return True
    suffix = path.suffix.lower()
    mime = {".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats"}.get(suffix, "")
    for token in (t.strip().lower() for t in field.accept.split(",") if t.strip()):
        if (
            token == suffix
            or (mime and token.startswith(mime))
            or token in {"*/*", "application/*"}
        ):
            return True
    return False


def _fill_resume(field: ApplicationField, context: AnswerContext) -> FieldAnswer:
    if field.type is not FieldType.FILE:
        return _needs_user(field, "paste your resume text yourself")
    if context.resume_status is ResumeStatus.MODIFIED:
        return _needs_user(field, "the stored resume changed since it was added; re-run `setup`")
    if context.resume_path is None:
        return _needs_user(field, "no resume on file; run `openapply setup`")
    if not _accepts(field, context.resume_path):
        return _needs_user(
            field,
            f"the form accepts {field.accept} but your resume is {context.resume_path.suffix}",
        )
    return _answer(
        field, AnswerStatus.FILLED, value=str(context.resume_path), source="profile: stored resume"
    )


# --- eligibility (HIGH): explicit answers only ----------------------------------------


def _fill_yes_no(field: ApplicationField, *, want_yes: bool, source: str) -> FieldAnswer:
    if field.type not in {FieldType.RADIO, FieldType.SELECT}:
        return _needs_user(field, "this eligibility question is not a clear yes/no choice")
    option = _yes_no_option(field.options, want_yes=want_yes)
    if option is None:
        return _needs_user(field, "could not tell which option means yes/no; choose it yourself")
    return _answer(field, AnswerStatus.FILLED, value=option.value, source=source)


def _eligibility(field: ApplicationField, context: AnswerContext) -> FieldAnswer:
    if field.confidence < HIGH_CONFIDENCE:
        return _needs_user(field, "this is a combined or unclear eligibility question")
    eligibility = context.profile.eligibility
    text = normalize(f"{field.label} {field.hint or ''}")

    if field.intent is Intent.SPONSORSHIP:
        if eligibility.requires_sponsorship is None:
            return _needs_user(field, "your profile has no answer about visa sponsorship")
        inverted = bool(_WITHOUT.search(text))
        want_yes = (
            (not eligibility.requires_sponsorship) if inverted else eligibility.requires_sponsorship
        )
        return _fill_yes_no(field, want_yes=want_yes, source="profile: sponsorship (explicit)")

    if field.intent is Intent.RELOCATION:
        if eligibility.willing_to_relocate is None:
            return _needs_user(field, "your profile has no answer about relocation")
        return _fill_yes_no(
            field, want_yes=eligibility.willing_to_relocate, source="profile: relocation (explicit)"
        )

    # Work authorization: only ever an affirmative answer, and only for a country the
    # profile explicitly lists and the question explicitly names.
    if _NEGATED_AUTH.search(text):
        return _needs_user(field, "this authorization question is phrased negatively")
    country = _names_authorized_country(field, eligibility.authorized_countries)
    if country is None:
        return _needs_user(
            field,
            "work authorization is only answered when the question names a country your "
            "profile explicitly lists as authorized",
        )
    return _fill_yes_no(field, want_yes=True, source=f"profile: authorized in {country} (explicit)")


# --- salary --------------------------------------------------------------------------


def _salary(field: ApplicationField, context: AnswerContext) -> FieldAnswer:
    prefs = context.profile.preferences
    if not prefs.salary_minimum:
        return _needs_user(field, "your profile has no salary preference; declare it yourself")
    if field.type not in {FieldType.TEXT, FieldType.TEXTAREA}:
        return _needs_user(field, "salary is not a free-text field here; choose it yourself")
    amount = str(prefs.salary_minimum)
    value = amount if field.numeric else " ".join(p for p in (amount, prefs.salary_currency) if p)
    return _place_value(field, value, "profile: salary preference (minimum)")


# --- entry point -----------------------------------------------------------------------


def answer_field(field: ApplicationField, context: AnswerContext) -> FieldAnswer | None:
    """The deterministic answer for ``field``; ``None`` if it needs a written (generated) one."""
    intent = field.intent

    if field.sensitivity is Sensitivity.REQUIRES_USER:
        reason = _NEEDS_USER_REASON.get(intent, "only you can answer this")
        if field.type is FieldType.CHECKBOX and field.current_value == "true":
            reason += " (the page pre-ticked it; check that is what you want)"
        return _needs_user(field, reason)

    if field.sensitivity is Sensitivity.HIGH:
        return _eligibility(field, context)

    if intent is Intent.RESUME:
        return _fill_resume(field, context)
    if intent is Intent.COVER_LETTER:
        if field.type is FieldType.TEXTAREA:
            return None  # written by the generation module
        return _needs_user(field, "upload a cover letter yourself; OpenApply does not create files")
    if intent is Intent.OPEN_ENDED:
        return None  # written by the generation module (it checks the field's limits)
    if intent is Intent.SALARY:
        return _salary(field, context)
    if intent in _MISSING_FROM_PROFILE:
        return _missing(field, _MISSING_FROM_PROFILE[intent])
    if intent is Intent.UNKNOWN:
        if field.current_value:
            return _answer(
                field, AnswerStatus.PREFILLED, value=field.current_value, source="page default"
            )
        if field.required:
            return _needs_user(field, "could not tell what this field asks for")
        return _answer(
            field, AnswerStatus.SKIPPED, reason="optional field that could not be identified"
        )
    return _fill_identity(field, context)
