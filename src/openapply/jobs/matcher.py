"""Deterministic candidate/job matching.

Pure functions, no I/O and no AI: the same profile and posting always give the same result.
Each factor returns a score (or ``None`` if the signal is unknown) *and its reasons*, so the
result can always explain itself. Unknown signals are left out of the overall score and
reduce ``confidence``; they are never silently counted as zero or as a match.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from openapply.candidate.models import CandidateProfile, RemotePreference
from openapply.jobs.credentials import check_credentials
from openapply.jobs.eligibility import check_eligibility
from openapply.jobs.experience import candidate_years, required_years
from openapply.jobs.match_models import (
    RECOMMENDATION_ORDER,
    FactorResult,
    JobMatch,
    Recommendation,
)
from openapply.jobs.models import EmploymentType, JobPosting, RemoteStatus, SalaryPeriod
from openapply.jobs.skills import SkillIndex, canonicalize

WEIGHTS = {"skills": 0.45, "experience": 0.20, "location": 0.15, "preferences": 0.20}

STRONG_THRESHOLD = 75
POSSIBLE_THRESHOLD = 55
WEAK_THRESHOLD = 35
BLOCKED_SCORE_CAP = 25
UNRELATED_ROLE_SCORE_CAP = 45

# How much each kind of job skill matters, and how much credit each kind of evidence earns.
_SKILL_WEIGHT = {"required": 1.0, "context": 0.4, "preferred": 0.3}
_LISTED_CREDIT = 1.0
_MENTIONED_CREDIT = 0.6  # appears in experience/project text but is not listed as a skill

_ANNUALIZE = {
    SalaryPeriod.YEAR: 1,
    SalaryPeriod.MONTH: 12,
    SalaryPeriod.WEEK: 52,
    SalaryPeriod.DAY: 260,
    SalaryPeriod.HOUR: 2080,
}
_GENERIC_LOCATION_WORDS = re.compile(
    r"\b(?:remote|anywhere|worldwide|global(?:ly)?|work\s+from\s+home|wfh|fully|100%|hybrid)\b",
    re.I,
)
_ROLE_NOISE = frozenset(
    {"a", "an", "the", "of", "and", "senior", "junior", "lead", "staff", "principal", "sr", "jr"}
)


@dataclass
class _Notes:
    """Findings collected while scoring, in the order they were found."""

    strengths: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)
    confirm: list[str] = field(default_factory=list)
    role_mismatch: bool = False


def _pct(value: float) -> int:
    return max(0, min(100, round(value * 100)))


def _mentions(text: str, term: str) -> bool:
    return bool(term.strip()) and bool(
        re.search(rf"(?<!\w){re.escape(term.strip())}(?!\w)", text, re.IGNORECASE)
    )


# --- skills ---------------------------------------------------------------------


@dataclass
class _SkillsResult:
    factor: FactorResult
    matched: list[str]
    missing_required: list[str]
    missing_preferred: list[str]
    required_total: int
    required_covered: int


def _candidate_skills(profile: CandidateProfile) -> tuple[set[str], str]:
    """(skills the candidate lists, free text that may mention more)."""
    listed = {canonicalize(s) for s in profile.skills}
    for entry in profile.experience:
        listed.update(canonicalize(s) for s in entry.skills)
    for project in profile.projects:
        listed.update(canonicalize(s) for s in project.skills)
    text_parts = [profile.summary or ""]
    for entry in profile.experience:
        text_parts += [entry.title, entry.summary or "", *entry.highlights]
    for project in profile.projects:
        text_parts += [project.name, project.description or ""]
    text_parts += [c.name for c in profile.certifications]
    return {s for s in listed if s}, "\n".join(text_parts)


def _skills(profile: CandidateProfile, job: JobPosting, notes: _Notes) -> _SkillsResult:
    listed, free_text = _candidate_skills(profile)
    index = SkillIndex(extra_skills=listed)
    mentioned = set(index.find(free_text)) - listed

    required = index.find("\n".join([job.title, *job.requirements]))
    preferred = [t for t in index.find("\n".join(job.preferred_requirements)) if t not in required]
    context_text = "\n".join([*job.responsibilities, job.description or ""])
    seen = {*required, *preferred}
    context = [t for t in index.find(context_text) if t not in seen]

    def credit(skill: str) -> float:
        if skill in listed:
            return _LISTED_CREDIT
        return _MENTIONED_CREDIT if skill in mentioned else 0.0

    weighted = [(t, "required") for t in required]
    weighted += [(t, "context") for t in context] + [(t, "preferred") for t in preferred]
    matched = [t for t, _ in weighted if credit(t) > 0]
    missing_required = [t for t in required if credit(t) == 0]
    missing_preferred = [t for t in preferred if credit(t) == 0]
    required_covered = sum(1 for t in required if credit(t) > 0)

    reasons: list[str] = []
    if not weighted:
        reasons.append(
            "No specific skills from the built-in vocabulary were found in the posting, "
            "so skills could not be scored."
        )
        return _SkillsResult(
            FactorResult(name="skills", score=None, weight=WEIGHTS["skills"], reasons=reasons),
            [], [], [], 0, 0,
        )  # fmt: skip

    total = sum(_SKILL_WEIGHT[kind] for _, kind in weighted)
    earned = sum(_SKILL_WEIGHT[kind] * credit(t) for t, kind in weighted)
    score = _pct(earned / total)

    if required:
        reasons.append(f"Covers {required_covered} of {len(required)} required skills.")
    for skill in required:
        if skill in listed:
            notes.strengths.append(f"{skill} (required)")
        elif skill in mentioned:
            notes.gaps.append(
                f"{skill}: only mentioned in your experience text, not listed as a skill"
            )
        else:
            notes.gaps.append(f"Missing required skill: {skill}")
    for skill in preferred:
        if credit(skill) > 0:
            notes.strengths.append(f"{skill} (preferred)")
    if missing_preferred:
        reasons.append(f"Preferred but not in your profile: {', '.join(missing_preferred)}.")
    context_missing = [t for t in context if credit(t) == 0]
    if context_missing:
        reasons.append(f"Mentioned in the role, not in your profile: {', '.join(context_missing)}.")
        notes.gaps.append(f"Mentioned in the role: {', '.join(context_missing)}")
    if mentioned & set(required + preferred + context):
        reasons.append("Some skills are credited at 60% because they appear only in free text.")
    return _SkillsResult(
        FactorResult(name="skills", score=score, weight=WEIGHTS["skills"], reasons=reasons),
        matched, missing_required, missing_preferred, len(required), required_covered,
    )  # fmt: skip


# --- experience -----------------------------------------------------------------


def _experience(
    profile: CandidateProfile, job: JobPosting, today: date, notes: _Notes
) -> FactorResult:
    weight = WEIGHTS["experience"]
    needed = required_years(job.requirements) or required_years(
        line for line in (job.description or "").splitlines() if "experience" in line.casefold()
    )
    if needed is None:
        return FactorResult(
            name="experience", score=None, weight=weight,
            reasons=["The posting does not state years of experience."],
        )  # fmt: skip
    minimum, maximum = needed
    have = candidate_years(profile, today)
    if have is None:
        notes.concerns.append(
            "Your profile has no dated work history, so experience can't be compared."
        )
        return FactorResult(
            name="experience", score=None, weight=weight,
            reasons=[
                f"The posting asks for about {minimum}+ years; your profile has no dated roles."
            ],
        )  # fmt: skip
    wanted = f"{minimum}-{maximum}" if maximum else f"{minimum}+"
    if minimum <= 0 or have >= minimum:
        reasons = [f"You have about {have:g} years; the posting asks for {wanted}."]
        notes.strengths.append(f"Experience: ~{have:g} years vs {wanted} asked")
        if maximum and have > maximum * 1.5:
            notes.concerns.append("You may be more senior than this role expects.")
            reasons.append("Well above the stated range, so the role may be too junior.")
            return FactorResult(name="experience", score=90, weight=weight, reasons=reasons)
        return FactorResult(name="experience", score=100, weight=weight, reasons=reasons)
    notes.gaps.append(f"Experience: ~{have:g} years vs {wanted} asked")
    return FactorResult(
        name="experience", score=_pct(have / minimum), weight=weight,
        reasons=[f"You have about {have:g} years; the posting asks for {wanted}."],
    )  # fmt: skip


# --- location -------------------------------------------------------------------


def _candidate_places(profile: CandidateProfile) -> list[str]:
    identity = profile.identity
    places = [identity.city or "", identity.country or "", *profile.preferences.locations]
    places += profile.eligibility.authorized_countries
    return [p for p in places if p.strip()]


def _location(profile: CandidateProfile, job: JobPosting, notes: _Notes) -> FactorResult:
    weight = WEIGHTS["location"]
    where = job.location or ""
    remote_pref = profile.preferences.remote_preference
    places = _candidate_places(profile)
    local = next((p for p in places if _mentions(where, p)), None)
    status = job.remote_status

    def result(score: int | None, *reasons: str) -> FactorResult:
        return FactorResult(name="location", score=score, weight=weight, reasons=list(reasons))

    if status is RemoteStatus.UNKNOWN and not where:
        return result(None, "The posting does not state a location or remote policy.")

    if status is RemoteStatus.REMOTE:
        score = 100
        reasons = ["The role is remote."]
        if remote_pref is RemotePreference.ONSITE:
            score = 50
            notes.concerns.append("The role is remote; your preference is onsite work.")
            reasons.append("You prefer onsite work.")
        restriction = _GENERIC_LOCATION_WORDS.sub("", where).strip(" ,-()/|")
        if restriction:
            if local:
                reasons.append(f"The listed region ('{where}') matches your '{local}'.")
            elif places:
                score = min(score, 70)
                notes.confirm.append(
                    f"The remote role is listed for '{where}'. Can you work from there?"
                )
                reasons.append(
                    f"Listed for '{where}', which does not match your profile locations."
                )
            else:
                score = min(score, 70)
                reasons.append(
                    f"Listed for '{where}', and your profile has no location to compare."
                )
        else:
            notes.strengths.append("Remote role")
        return result(score, *reasons)

    # hybrid / onsite / unknown policy: everything below needs an actual location to compare.
    if not where.strip():
        return result(None, f"The posting is {status.value} but does not say where.")
    if not places:
        return result(None, "Your profile has no location or preferred locations to compare.")
    if local:
        score = 100
        reasons = [f"The role's location ('{where}') matches your '{local}'."]
        if remote_pref is RemotePreference.REMOTE:
            score = 60 if status is RemoteStatus.ONSITE else 75
            notes.concerns.append(f"You prefer remote work; this role is {status.value}.")
            reasons.append("It is not fully remote, which you prefer.")
        else:
            notes.strengths.append(f"Location matches ({local})")
        return result(score, *reasons)

    relocate = profile.eligibility.willing_to_relocate
    if relocate is True:
        notes.concerns.append(f"The role is in '{where}', which would mean relocating.")
        return result(60, f"The role is in '{where}'; you are willing to relocate.")
    if relocate is None:
        notes.confirm.append(f"The role is in '{where}'. Would you relocate?")
        return result(30, f"The role is in '{where}' and your willingness to relocate is unknown.")
    notes.gaps.append(f"Location mismatch: the role is in '{where}' and you won't relocate")
    return result(0, f"The role is in '{where}'; you do not want to relocate.")


# --- preferences ----------------------------------------------------------------


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9+#.]+", text.casefold()) if t not in _ROLE_NOISE}


def _role_check(
    profile: CandidateProfile, job: JobPosting, notes: _Notes
) -> tuple[float, str] | None:
    roles = profile.preferences.roles
    if not roles:
        return None
    for role in roles:
        if _mentions(job.title, role):
            notes.strengths.append(f"Title matches your target role ({role})")
            return 1.0, f"The title matches your target role '{role}'."
    title = _tokens(job.title)
    best_role, best = max(
        ((r, len(_tokens(r) & title) / max(1, len(_tokens(r)))) for r in roles), key=lambda x: x[1]
    )
    if best >= 0.67:
        return 0.8, f"The title is close to your target role '{best_role}'."
    if best >= 0.34:
        return 0.5, f"The title partly overlaps your target role '{best_role}'."
    notes.role_mismatch = True
    notes.concerns.append(f"'{job.title}' is not one of your target roles.")
    return 0.1, f"The title does not resemble your target roles ({', '.join(roles)})."


def _employment_check(
    profile: CandidateProfile, job: JobPosting, notes: _Notes
) -> tuple[float, str] | None:
    wanted = {
        re.sub(r"[\s-]+", "_", t.strip().casefold()) for t in profile.preferences.employment_types
    }
    if not wanted or job.employment_type is EmploymentType.UNKNOWN:
        return None
    if job.employment_type.value in wanted:
        return (
            1.0,
            f"Employment type ({job.employment_type.value.replace('_', ' ')}) is one you want.",
        )
    notes.concerns.append(f"This is a {job.employment_type.value.replace('_', ' ')} role.")
    return (
        0.2,
        f"Employment type is {job.employment_type.value.replace('_', ' ')}, "
        "which is not in your preferences.",
    )


def _salary_check(
    profile: CandidateProfile, job: JobPosting, notes: _Notes
) -> tuple[float, str] | None:
    minimum = profile.preferences.salary_minimum
    offered = [v for v in (job.salary_min, job.salary_max) if v]
    if not minimum or not offered:
        return None
    # Numbers are only comparable when both sides say they are in the same currency and the
    # posting says what period they cover. Anything else is unknown, never a guess.
    mine, theirs = profile.preferences.salary_currency, job.currency
    if not mine or not theirs:
        missing = "your minimum" if not mine else "the posting"
        notes.concerns.append(f"Salary not compared: the currency is not stated for {missing}.")
        return None
    if mine.upper() != theirs.upper():
        notes.concerns.append(f"Salary is in {theirs} and your minimum is in {mine}; not compared.")
        return None
    if job.salary_period is None:
        notes.concerns.append(
            "Salary not compared: the posting does not say per year, month or hour."
        )
        return None
    top = max(offered) * _ANNUALIZE[job.salary_period]
    if top >= minimum:
        notes.strengths.append("Salary meets your minimum")
        return 1.0, f"The top of the salary range ({top:,}) meets your minimum ({minimum:,})."
    if top >= minimum * 0.9:
        notes.concerns.append("Salary is slightly below your minimum.")
        return (
            0.6,
            f"The top of the salary range ({top:,}) is just under your minimum ({minimum:,}).",
        )
    notes.gaps.append("Salary is below your minimum")
    return 0.2, f"The top of the salary range ({top:,}) is below your minimum ({minimum:,})."


def _preferences(profile: CandidateProfile, job: JobPosting, notes: _Notes) -> FactorResult:
    checks = [
        c
        for c in (
            _role_check(profile, job, notes),
            _employment_check(profile, job, notes),
            _salary_check(profile, job, notes),
        )
        if c is not None
    ]
    weight = WEIGHTS["preferences"]
    if not checks:
        return FactorResult(
            name="preferences", score=None, weight=weight,
            reasons=["Nothing in your preferences could be compared with this posting."],
        )  # fmt: skip
    score = _pct(sum(s for s, _ in checks) / len(checks))
    return FactorResult(
        name="preferences", score=score, weight=weight, reasons=[r for _, r in checks]
    )


# --- combining ------------------------------------------------------------------


def _cap(
    current: Recommendation, ceiling: Recommendation, reason: str, rationale: list[str]
) -> Recommendation:
    if RECOMMENDATION_ORDER.index(current) > RECOMMENDATION_ORDER.index(ceiling):
        rationale.append(reason)
        return ceiling
    return current


def _base_recommendation(score: int) -> Recommendation:
    if score >= STRONG_THRESHOLD:
        return Recommendation.STRONG_MATCH
    if score >= POSSIBLE_THRESHOLD:
        return Recommendation.POSSIBLE_MATCH
    if score >= WEAK_THRESHOLD:
        return Recommendation.WEAK_MATCH
    return Recommendation.DO_NOT_APPLY


def score_match(
    profile: CandidateProfile, job: JobPosting, *, today: date | None = None
) -> JobMatch:
    notes = _Notes()
    skills = _skills(profile, job, notes)
    experience = _experience(profile, job, today or date.today(), notes)
    location = _location(profile, job, notes)
    preferences = _preferences(profile, job, notes)
    eligibility = check_eligibility(profile, job)
    credentials = check_credentials(profile, job)
    notes.concerns.extend(eligibility.concerns)
    notes.strengths.extend(f"Credential confirmed: {item}" for item in credentials.matched)
    notes.gaps.extend(credentials.blockers)
    for question in eligibility.needs_confirmation:
        if question not in notes.confirm:
            notes.confirm.append(question)

    factors = [skills.factor, experience, location, preferences]
    known = [f for f in factors if f.score is not None]
    known_weight = sum(f.weight for f in known)
    raw_overall = (
        _pct(sum(f.weight * (f.score or 0) for f in known) / known_weight / 100)
        if known_weight
        else 0
    )
    confidence = round(known_weight / sum(f.weight for f in factors), 2)
    blockers = [*eligibility.blockers, *credentials.blockers]
    overall = raw_overall
    score_constraints: list[str] = []
    if blockers and overall > BLOCKED_SCORE_CAP:
        overall = BLOCKED_SCORE_CAP
        score_constraints.append(
            f"The raw factor score was {raw_overall}%, capped at {BLOCKED_SCORE_CAP}% because "
            "a mandatory requirement is not met."
        )
    elif notes.role_mismatch and overall > UNRELATED_ROLE_SCORE_CAP:
        overall = UNRELATED_ROLE_SCORE_CAP
        score_constraints.append(
            f"The raw factor score was {raw_overall}%, capped at {UNRELATED_ROLE_SCORE_CAP}% "
            "because the role does not match your target roles."
        )

    rationale = [
        f"Overall {overall}% from {len(known)} of {len(factors)} factors "
        f"(confidence {round(confidence * 100)}%)."
    ]
    rationale.extend(score_constraints)
    recommendation = _base_recommendation(overall)
    if not known:
        recommendation = Recommendation.WEAK_MATCH
        rationale.append("Not enough information in the profile or posting to assess a match.")
    if blockers:
        recommendation = Recommendation.DO_NOT_APPLY
        rationale.extend(blockers)
    elif notes.role_mismatch:
        recommendation = _cap(
            recommendation,
            Recommendation.WEAK_MATCH,
            "The role does not match any target role in your profile.",
            rationale,
        )
    if skills.required_total and skills.required_covered == 0:
        recommendation = _cap(
            recommendation, Recommendation.WEAK_MATCH,
            "None of the required skills are in your profile.", rationale,
        )  # fmt: skip
    elif skills.required_total and len(skills.missing_required) / skills.required_total >= 0.5:
        recommendation = _cap(
            recommendation, Recommendation.POSSIBLE_MATCH,
            "Half or more of the required skills are missing from your profile.", rationale,
        )  # fmt: skip
    if skills.factor.score is None:
        recommendation = _cap(
            recommendation, Recommendation.POSSIBLE_MATCH,
            "Skills could not be assessed, so this cannot be a strong match.", rationale,
        )  # fmt: skip
    if experience.score is not None and experience.score < 50:
        recommendation = _cap(
            recommendation, Recommendation.POSSIBLE_MATCH,
            "Your experience is well below what the posting asks for.", rationale,
        )  # fmt: skip
    if location.score == 0:
        recommendation = _cap(
            recommendation, Recommendation.WEAK_MATCH,
            "The location does not work for you.", rationale,
        )  # fmt: skip
    if known and confidence < 0.5:
        recommendation = _cap(
            recommendation, Recommendation.POSSIBLE_MATCH,
            "Too little could be assessed to call this a strong match.", rationale,
        )  # fmt: skip

    return JobMatch(
        job_id=job.id,
        overall_score=overall,
        skill_score=skills.factor.score,
        experience_score=experience.score,
        location_score=location.score,
        preference_score=preferences.score,
        confidence=confidence,
        recommendation=recommendation,
        matched_skills=skills.matched,
        missing_skills=skills.missing_required,
        preferred_missing_skills=skills.missing_preferred,
        strengths=notes.strengths,
        gaps=notes.gaps,
        concerns=notes.concerns,
        blockers=blockers,
        needs_confirmation=notes.confirm,
        factors=factors,
        rationale=rationale,
    )
