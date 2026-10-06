from __future__ import annotations

from datetime import date

import pytest

from openapply.candidate.models import CandidateProfile, Eligibility, Experience
from openapply.jobs.eligibility import canonical_country, check_eligibility
from openapply.jobs.experience import candidate_years, required_years
from openapply.jobs.skills import SkillIndex, canonicalize
from tests.jobs.builders import make_job, make_profile

# --- skills ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Experience with Django and PostgreSQL, k8s a plus",
            ["Django", "PostgreSQL", "Kubernetes"],
        ),
        ("Go beyond expectations. We go to market fast.", []),
        ("Python, Go, or Rust", ["Python", "Go", "Rust"]),
        ("Proficient in golang and Node.js (not just node)", ["Go", "Node.js"]),
        ("R&D team; C-suite exposure", []),
        ("We spark creativity and move swift", []),
        ("Strong C++ and C# skills, .NET a bonus", ["C++", "C#", ".NET"]),
        ("REST APIs, restful services; the rest of the team", ["REST APIs"]),
        (
            "Spring Boot microservices on AWS with CI/CD",
            ["Spring Boot", "Microservices", "AWS", "CI/CD"],
        ),
        ("Guardrails and Javascript tooling", ["JavaScript"]),
        ("", []),
    ],
)
def test_skill_detection(text: str, expected: list[str]) -> None:
    assert SkillIndex().find(text) == expected


def test_skills_are_ordered_by_first_appearance() -> None:
    assert SkillIndex().find("SQL first, then Python, then Docker, then SQL again") == [
        "SQL",
        "Python",
        "Docker",
    ]


def test_candidate_skills_outside_the_vocabulary_are_still_detected() -> None:
    assert SkillIndex().find("We use Obscuro daily") == []
    assert SkillIndex(["Obscuro"]).find("We use Obscuro daily") == ["Obscuro"]
    assert SkillIndex(["R"]).find("Experience with R and statistics; R&D") == ["R"]


def test_canonicalize() -> None:
    assert canonicalize("postgres") == "PostgreSQL"
    assert canonicalize("  K8S ") == "Kubernetes"
    assert canonicalize("Some Niche Tool") == "Some Niche Tool"


# --- experience --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("items", "expected"),
    [
        (["3-5 years of Python"], (3, 5)),
        (["3" + chr(0x2013) + "5 years experience"], (3, 5)),
        (["2 to 4 years"], (2, 4)),
        (["5+ years"], (5, None)),
        (["at least 4 years of experience"], (4, None)),
        (["minimum of 6 yrs"], (6, None)),
        (["Experience with Django"], None),
        ([], None),
        (["3+ years of Python", "10 years of Kubernetes", "2 years of SQL"], (3, None)),
    ],
)
def test_required_years(items: list[str], expected: tuple[int, int | None] | None) -> None:
    assert required_years(items) == expected


def _exp(start: date, end: date | None = None, *, current: bool = False) -> Experience:
    return Experience(company="X", title="Dev", start=start, end=end, current=current)


def _profile_with(*entries: Experience) -> CandidateProfile:
    return make_profile(experience=list(entries))


def test_candidate_years_sums_and_merges_overlaps() -> None:
    today = date(2026, 6, 1)
    separate = _profile_with(
        _exp(date(2020, 1, 1), date(2022, 1, 1)), _exp(date(2023, 1, 1), date(2025, 1, 1))
    )
    assert candidate_years(separate, today) == 4.0
    overlapping = _profile_with(
        _exp(date(2020, 1, 1), date(2023, 1, 1)), _exp(date(2021, 1, 1), date(2024, 1, 1))
    )
    assert candidate_years(overlapping, today) == 4.0  # 2020-2024, not 3 + 3
    nested = _profile_with(
        _exp(date(2020, 1, 1), date(2024, 1, 1)), _exp(date(2021, 1, 1), date(2022, 1, 1))
    )
    assert candidate_years(nested, today) == 4.0


def test_candidate_years_current_roles_run_to_today_and_undated_roles_are_ignored() -> None:
    today = date(2026, 1, 1)
    assert candidate_years(_profile_with(_exp(date(2024, 1, 1), current=True)), today) == 2.0
    assert candidate_years(_profile_with(_exp(date(2024, 1, 1))), today) == 2.0  # no end = ongoing
    undated = Experience(company="X", title="Dev")
    assert candidate_years(_profile_with(undated), today) is None
    assert candidate_years(_profile_with(), today) is None


def test_candidate_years_ignores_future_and_inverted_ranges() -> None:
    today = date(2026, 1, 1)
    future = _profile_with(_exp(date(2027, 1, 1), date(2028, 1, 1)))  # hasn't started yet
    assert candidate_years(future, today) is None
    started_now_ends_later = _profile_with(_exp(date(2025, 1, 1), date(2030, 1, 1)))
    assert candidate_years(started_now_ends_later, today) == 1.0  # capped at today
    inverted = _profile_with(_exp(date(2024, 1, 1), date(2022, 1, 1)))
    assert candidate_years(inverted, today) is None


# --- eligibility -------------------------------------------------------------------


def _findings(text: str, eligibility: Eligibility | None = None):  # type: ignore[no-untyped-def]
    profile = make_profile(eligibility=eligibility or Eligibility())
    job = make_job(description=text, requirements=[], raw_text=None)
    return check_eligibility(profile, job)


@pytest.mark.parametrize(
    "text",
    [
        "We do not offer visa sponsorship.",
        "Unable to sponsor work visas at this time.",
        "No visa sponsorship is available.",
        "Sponsorship is not available for this position.",
        "Candidates must not require sponsorship.",
        "We will not sponsor candidates for this role.",
        "Applicants must be able to work without sponsorship.",
        "We're not able to provide visa sponsorship.",
    ],
)
def test_no_sponsorship_phrases_are_recognised(text: str) -> None:
    findings = _findings(text, Eligibility(requires_sponsorship=True))
    assert findings.blockers, text


@pytest.mark.parametrize(
    "text",
    [
        "We offer visa sponsorship for the right candidate.",
        "Visa sponsorship available.",
        "We sponsor visas. Do not hesitate to ask about sponsorship.",
        "Relocation support and sponsorship provided.",
        "A great place to work.",
        "",
    ],
)
def test_ordinary_or_positive_text_is_not_a_no_sponsorship_signal(text: str) -> None:
    findings = _findings(text, Eligibility(requires_sponsorship=True))
    assert findings.blockers == [] and findings.concerns == [], text


def test_sponsorship_answers_decide_blocker_question_or_nothing() -> None:
    text = "We do not offer visa sponsorship."
    assert _findings(text, Eligibility(requires_sponsorship=True)).blockers
    unknown = _findings(text, Eligibility(requires_sponsorship=None))
    assert unknown.blockers == []
    assert unknown.needs_confirmation  # asked once, with its context...
    assert unknown.concerns == []  # ...not repeated as a concern
    fine = _findings(text, Eligibility(requires_sponsorship=False))
    assert fine.blockers == [] and fine.concerns == [] and fine.needs_confirmation == []


def test_work_authorization_country_is_compared_with_the_profile() -> None:
    text = "You must be legally authorized to work in the United States."
    ok = _findings(text, Eligibility(authorized_countries=["USA"]))
    assert ok.concerns == []
    other = _findings(text, Eligibility(authorized_countries=["Nigeria"]))
    assert any("United States" in c for c in other.concerns) and other.blockers == []
    unknown = _findings(text, Eligibility())
    assert unknown.needs_confirmation and "United States" in unknown.needs_confirmation[0]


def test_clearance_and_citizenship_always_need_the_users_word() -> None:
    cleared = _findings("An active security clearance is required.", Eligibility())
    assert any("clearance" in q for q in cleared.needs_confirmation)
    citizen = _findings("Must be a U.S. citizen.", Eligibility(requires_sponsorship=False))
    assert any("citizenship" in q.lower() for q in citizen.needs_confirmation)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("US", "United States"),
        ("U.S.A.", "usa"),
        ("the UK", "United Kingdom"),
        ("Nigeria", "nigeria"),
    ],
)
def test_country_aliases(a: str, b: str) -> None:
    assert canonical_country(a) == canonical_country(b)
