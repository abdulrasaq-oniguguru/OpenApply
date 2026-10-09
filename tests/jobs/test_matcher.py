from __future__ import annotations

from datetime import date

import pytest

from openapply.candidate.models import (
    CandidateProfile,
    Certification,
    Eligibility,
    Experience,
    Preferences,
    RemotePreference,
)
from openapply.jobs.match_models import JobMatch, Recommendation
from openapply.jobs.matcher import score_match
from openapply.jobs.models import EmploymentType, JobPosting, RemoteStatus, SalaryPeriod
from tests.jobs.builders import TODAY, make_job, make_profile


def _match(profile: CandidateProfile | None = None, job: JobPosting | None = None) -> JobMatch:
    return score_match(profile or make_profile(), job or make_job(), today=TODAY)


# --- the five required scenarios --------------------------------------------------


def test_strong_match() -> None:
    m = _match()
    assert m.recommendation is Recommendation.STRONG_MATCH
    assert m.overall_score >= 75
    assert m.matched_skills[:4] == ["Python", "Django", "PostgreSQL", "REST APIs"]
    assert m.missing_skills == []
    assert m.preferred_missing_skills == ["Kubernetes", "Go"]
    assert m.skill_score is not None and m.skill_score >= 80
    assert m.experience_score == 100
    assert m.blockers == []
    assert "Python (required)" in m.strengths


def test_weak_match() -> None:
    profile = make_profile(
        skills=["Figma", "Photoshop"],
        experience=[
            Experience(company="Studio", title="Designer", start=date(2025, 6, 1), current=True)
        ],
        preferences=Preferences(roles=["UX Designer"]),
    )
    job = make_job(
        title="Senior Backend Engineer",
        requirements=[
            "5+ years of Python experience",
            "Django and PostgreSQL",
            "Kubernetes in production",
        ],
        location="Berlin, Germany",
        remote_status=RemoteStatus.ONSITE,
    )
    m = score_match(profile, job, today=TODAY)
    assert m.recommendation in {Recommendation.WEAK_MATCH, Recommendation.DO_NOT_APPLY}
    assert m.overall_score < 40
    assert {"Python", "Django", "PostgreSQL", "Kubernetes"} <= set(m.missing_skills)


def test_location_mismatch_caps_an_otherwise_perfect_match() -> None:
    job = make_job(location="Berlin, Germany", remote_status=RemoteStatus.ONSITE)
    m = _match(job=job)  # make_profile: Abuja, Nigeria, will NOT relocate
    assert m.location_score == 0
    assert m.skill_score is not None and m.skill_score >= 80
    assert m.recommendation is Recommendation.WEAK_MATCH
    assert any("Location mismatch" in g for g in m.gaps)
    assert any("location does not work" in line for line in m.rationale)


@pytest.mark.parametrize(
    ("willing", "score", "asks_user"),
    [(True, 60, False), (None, 30, True)],
)
def test_relocation_willingness_is_never_guessed(
    willing: bool | None, score: int, asks_user: bool
) -> None:
    profile = make_profile(
        eligibility=Eligibility(authorized_countries=["Nigeria"], willing_to_relocate=willing)
    )
    job = make_job(location="Berlin, Germany", remote_status=RemoteStatus.ONSITE)
    m = score_match(profile, job, today=TODAY)
    assert m.location_score == score
    assert any("relocate" in q.lower() for q in m.needs_confirmation) is asks_user


def test_missing_required_skills_are_named_and_cap_the_recommendation() -> None:
    job = make_job(
        requirements=[
            "3+ years of experience",
            "Python and Django",
            "Kubernetes experience",
            "Terraform for infrastructure",
        ],
        preferred_requirements=[],
    )
    m = _match(job=job)
    assert m.overall_score >= 75  # strong on paper...
    assert m.missing_skills == ["Kubernetes", "Terraform"]
    assert m.recommendation is Recommendation.POSSIBLE_MATCH  # half the required skills missing
    assert "Missing required skill: Kubernetes" in m.gaps
    assert any("Half or more of the required skills" in line for line in m.rationale)


def test_unknown_sponsorship_is_a_question_not_a_guess() -> None:
    job = make_job(description="We do not offer visa sponsorship for this role.")
    profile = make_profile(eligibility=Eligibility(authorized_countries=["Nigeria"]))
    assert profile.eligibility.requires_sponsorship is None
    m = score_match(profile, job, today=TODAY)
    assert m.blockers == []  # not a conflict: we simply don't know
    assert m.recommendation is not Recommendation.DO_NOT_APPLY
    assert any("sponsorship" in q.lower() for q in m.needs_confirmation)
    assert any("none is offered" in q for q in m.needs_confirmation)  # the question has the context
    assert m.concerns == []  # ...so the same fact is not repeated as a concern
    assert profile.eligibility.requires_sponsorship is None  # matching never fills in answers


def test_known_sponsorship_need_against_no_sponsorship_is_a_blocker() -> None:
    job = make_job(description="We do not offer visa sponsorship for this role.")
    profile = make_profile(
        eligibility=Eligibility(authorized_countries=["Nigeria"], requires_sponsorship=True)
    )
    m = score_match(profile, job, today=TODAY)
    assert m.blockers
    assert m.recommendation is Recommendation.DO_NOT_APPLY
    assert m.skill_score is not None and m.skill_score >= 80  # forced despite great skills


def test_no_sponsorship_needed_is_fine() -> None:
    job = make_job(description="We do not offer visa sponsorship for this role.")
    m = _match(job=job)  # make_profile: requires_sponsorship False
    assert m.blockers == []
    assert all("sponsorship" not in q.lower() for q in m.needs_confirmation)
    assert m.recommendation is Recommendation.STRONG_MATCH


# --- explanation, determinism, unknowns -------------------------------------------


def test_every_factor_explains_itself() -> None:
    m = _match()
    assert [f.name for f in m.factors] == ["skills", "experience", "location", "preferences"]
    for factor in m.factors:
        assert factor.reasons, f"{factor.name} has no explanation"
    assert abs(sum(f.weight for f in m.factors) - 1.0) < 1e-9
    assert m.rationale[0].startswith("Overall")


def test_scoring_is_deterministic() -> None:
    assert _match().model_dump() == _match().model_dump()


def test_unknown_factors_are_excluded_not_counted_as_zero() -> None:
    profile = make_profile(experience=[])  # nothing dated to compare
    m = score_match(profile, make_job(), today=TODAY)
    assert m.experience_score is None
    assert m.confidence == 0.8  # experience's 0.20 weight had no data
    assert any("no dated work history" in c for c in m.concerns)
    known = [f for f in m.factors if f.score is not None]
    expected = round(sum(f.weight * (f.score or 0) for f in known) / sum(f.weight for f in known))
    assert m.overall_score == expected


def test_posting_without_years_leaves_experience_unknown() -> None:
    job = make_job(requirements=["Python", "Django"])
    m = _match(job=job)
    assert m.experience_score is None
    assert any("does not state years" in r for r in m.factors[1].reasons)


def test_nothing_to_assess_is_reported_honestly() -> None:
    profile = make_profile(
        skills=[],
        experience=[],
        city=None,
        country=None,
        preferences=Preferences(),
        eligibility=Eligibility(),
    )
    job = make_job(
        title="Associate",
        requirements=["Good communication"],
        preferred_requirements=[],
        responsibilities=[],
        description="A role.",
        location=None,
        remote_status=RemoteStatus.UNKNOWN,
        employment_type=EmploymentType.UNKNOWN,
        salary_min=None,
        salary_max=None,
    )
    m = score_match(profile, job, today=TODAY)
    assert m.confidence == 0
    assert m.overall_score == 0
    assert m.recommendation is Recommendation.WEAK_MATCH
    assert any("Not enough information" in line for line in m.rationale)


def test_unassessable_skills_cannot_be_a_strong_match() -> None:
    job = make_job(
        title="Associate", requirements=["3-5 years of experience", "Good communication"],
        preferred_requirements=[], responsibilities=[], description=None,
    )  # fmt: skip
    m = _match(job=job)
    assert m.skill_score is None
    assert m.recommendation is not Recommendation.STRONG_MATCH


# --- skills detail -----------------------------------------------------------------


def test_skills_only_in_free_text_earn_partial_credit() -> None:
    listed = make_profile(skills=["Python"], summary=None)
    mention = make_profile(skills=["Python"], summary="Built Kubernetes clusters at scale.")
    job = make_job(requirements=["Python and Kubernetes"], preferred_requirements=[])
    a = score_match(listed, job, today=TODAY)
    b = score_match(mention, job, today=TODAY)
    assert a.skill_score == 50 and "Kubernetes" in a.missing_skills
    assert b.skill_score == 80  # (1.0 + 0.6) / 2
    assert "Kubernetes" in b.matched_skills
    assert any("only mentioned" in g for g in b.gaps)


def test_skill_aliases_and_experience_skills_count() -> None:
    profile = make_profile(
        skills=["postgres", "k8s"],
        experience=[
            Experience(
                company="A", title="Dev", start=date(2020, 1, 1), current=True, skills=["Terraform"]
            )
        ],
    )
    job = make_job(requirements=["PostgreSQL, Kubernetes and Terraform"], preferred_requirements=[])
    m = score_match(profile, job, today=TODAY)
    assert m.missing_skills == []
    assert m.skill_score == 100


def test_title_skills_count_as_required() -> None:
    job = make_job(title="Kubernetes Platform Engineer", requirements=["Linux"])
    m = _match(job=job)
    assert "Kubernetes" in m.missing_skills


def test_skills_in_the_description_matter_less() -> None:
    job = make_job(
        requirements=["Python"], preferred_requirements=[],
        description="You will also touch Kubernetes occasionally.", responsibilities=[],
    )  # fmt: skip
    m = _match(job=job)
    assert m.skill_score == round(100 * 1.0 / 1.4)  # context weight 0.4, missing
    assert m.missing_skills == []  # a context skill is not a *required* skill
    assert any("Mentioned in the role" in g for g in m.gaps)


# --- experience --------------------------------------------------------------------


def test_less_experience_than_required_scores_proportionally_and_caps() -> None:
    profile = make_profile(
        experience=[Experience(company="A", title="Dev", start=date(2025, 6, 1), current=True)]
    )  # one year
    job = make_job(
        requirements=["Python", "5+ years of professional experience"], preferred_requirements=[]
    )
    m = score_match(profile, job, today=TODAY)
    assert m.overall_score >= 75  # strong on paper...
    assert m.experience_score == 20
    assert any(g.startswith("Experience:") for g in m.gaps)
    assert m.recommendation is Recommendation.POSSIBLE_MATCH
    assert any("well below" in line for line in m.rationale)


def test_overqualification_is_noted() -> None:
    job = make_job(requirements=["Python", "1-2 years of experience"])
    m = _match(job=job)  # ~4.4 years vs 1-2
    assert m.experience_score == 90
    assert any("more senior" in c for c in m.concerns)


# --- location ----------------------------------------------------------------------


def test_region_limited_remote_asks_instead_of_guessing() -> None:
    m = _match(job=make_job(location="Remote (Africa)"))
    assert m.location_score == 70
    assert any("Africa" in q for q in m.needs_confirmation)


def test_remote_listed_for_the_candidates_own_country_is_fine() -> None:
    m = _match(job=make_job(location="Remote (Nigeria)"))
    assert m.location_score == 100
    assert m.needs_confirmation == []


def test_unrestricted_remote_is_a_clean_match() -> None:
    m = _match(job=make_job(location="Remote - Worldwide"))
    assert m.location_score == 100
    assert "Remote role" in m.strengths


def test_local_onsite_matches_unless_candidate_wants_remote() -> None:
    job = make_job(location="Abuja, Nigeria", remote_status=RemoteStatus.ONSITE)
    remote_lover = make_profile()  # prefers remote
    assert score_match(remote_lover, job, today=TODAY).location_score == 60
    flexible = make_profile(preferences=Preferences(remote_preference=RemotePreference.ANY))
    assert score_match(flexible, job, today=TODAY).location_score == 100


def test_candidate_without_location_leaves_location_unknown() -> None:
    profile = make_profile(
        city=None, country=None, eligibility=Eligibility(),
        preferences=Preferences(roles=["Backend Engineer"]),
    )  # fmt: skip
    job = make_job(location="Berlin, Germany", remote_status=RemoteStatus.ONSITE)
    assert score_match(profile, job, today=TODAY).location_score is None


def test_candidate_preferring_onsite_for_a_remote_role() -> None:
    profile = make_profile(preferences=Preferences(remote_preference=RemotePreference.ONSITE))
    m = score_match(profile, make_job(), today=TODAY)
    assert m.location_score == 50


# --- preferences -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "at_least"),
    [("Senior Backend Engineer II", 100), ("Backend Developer", 40), ("Marketing Manager", 0)],
)
def test_role_matching(title: str, at_least: int) -> None:
    profile = make_profile(
        preferences=Preferences(roles=["Backend Engineer"], remote_preference=RemotePreference.ANY)
    )
    job = make_job(
        title=title, salary_min=None, salary_max=None, employment_type=EmploymentType.UNKNOWN
    )
    m = score_match(profile, job, today=TODAY)
    assert m.preference_score is not None
    if at_least == 100:
        assert m.preference_score == 100
    elif at_least == 40:
        assert 40 <= m.preference_score < 100
    else:
        assert m.preference_score <= 10
        assert any("not one of your target roles" in c for c in m.concerns)


def test_salary_checks() -> None:
    prefs = Preferences(salary_minimum=100_000, salary_currency="USD")
    low = make_job(salary_min=40_000, salary_max=60_000)
    near = make_job(salary_min=80_000, salary_max=95_000)
    high = make_job(salary_min=90_000, salary_max=120_000)
    profile = make_profile(preferences=prefs)
    assert score_match(profile, low, today=TODAY).preference_score == 20
    assert score_match(profile, near, today=TODAY).preference_score == 60
    assert score_match(profile, high, today=TODAY).preference_score == 100


def test_salary_periods_are_annualized_and_currency_mismatch_is_not_compared() -> None:
    profile = make_profile(preferences=Preferences(salary_minimum=60_000, salary_currency="USD"))
    monthly = make_job(salary_min=4_000, salary_max=6_000, salary_period=SalaryPeriod.MONTH)
    assert score_match(profile, monthly, today=TODAY).preference_score == 100  # 72,000 a year
    euros = make_job(currency="EUR")
    m = score_match(profile, euros, today=TODAY)
    assert m.preference_score is None
    assert any("not compared" in c for c in m.concerns)


def test_employment_type_preference() -> None:
    profile = make_profile(preferences=Preferences(employment_types=["Full-Time"]))
    ok = score_match(profile, make_job(), today=TODAY)
    contract = score_match(profile, make_job(employment_type=EmploymentType.CONTRACT), today=TODAY)
    assert ok.preference_score == 100
    assert contract.preference_score == 20
    assert any("contract" in c for c in contract.concerns)


def test_nothing_to_compare_in_preferences_is_unknown() -> None:
    profile = make_profile(preferences=Preferences())
    assert score_match(profile, make_job(), today=TODAY).preference_score is None


def test_no_required_skill_covered_caps_at_weak_even_when_everything_else_fits() -> None:
    job = make_job(requirements=["3+ years of experience", "Kubernetes"], preferred_requirements=[])
    m = _match(job=job)
    assert m.skill_score == 0
    assert m.overall_score >= 55  # the other factors alone would make this a possible match
    assert m.recommendation is Recommendation.WEAK_MATCH
    assert any("None of the required skills" in line for line in m.rationale)


def test_blockers_override_everything_and_are_listed_in_the_rationale() -> None:
    job = make_job(description="Sponsorship is not available.")
    profile = make_profile(
        eligibility=Eligibility(authorized_countries=["Nigeria"], requires_sponsorship=True)
    )
    m = score_match(profile, job, today=TODAY)
    assert m.recommendation is Recommendation.DO_NOT_APPLY
    assert any("Sponsorship is not available" in line for line in m.rationale)  # evidence quoted
    assert any("you need sponsorship" in line for line in m.rationale)


@pytest.mark.parametrize("title", ["General Physician", "Oncologist"])
def test_regulated_medical_role_cannot_score_high_without_credentials(title: str) -> None:
    job = make_job(
        title=title,
        requirements=["3+ years of professional experience"],
        preferred_requirements=[],
    )

    match = _match(job=job)

    assert match.overall_score <= 25
    assert match.recommendation is Recommendation.DO_NOT_APPLY
    assert any("medical degree or physician licence" in item for item in match.blockers)
    if title == "Oncologist":
        assert any("oncology specialist" in item for item in match.blockers)


def test_documented_medical_credentials_remove_the_hard_blocker() -> None:
    profile = make_profile().model_copy(
        update={
            "certifications": [
                Certification(name="Medical licence", issuer="Medical Council"),
                Certification(name="Board certification in Oncology"),
            ]
        }
    )
    job = make_job(
        title="Oncologist",
        requirements=["Valid medical licence", "Oncology board certification"],
    )

    match = _match(profile=profile, job=job)

    assert match.blockers == []


def test_unrelated_target_role_caps_score_even_when_other_factors_fit() -> None:
    job = make_job(
        title="Marketing Manager",
        requirements=["3+ years of professional experience", "Python"],
        preferred_requirements=[],
    )

    match = _match(job=job)

    assert match.overall_score <= 45
    assert match.recommendation in {Recommendation.WEAK_MATCH, Recommendation.DO_NOT_APPLY}
    assert any("does not match your target roles" in line for line in match.rationale)
