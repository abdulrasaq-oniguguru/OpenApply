from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from openapply.applications.answers import AnswerContext, answer_field
from openapply.applications.models import AnswerStatus, FieldAnswer, FieldType, Intent
from openapply.candidate.models import (
    CandidateProfile,
    Eligibility,
    Experience,
    Identity,
    Links,
    Preferences,
)
from openapply.candidate.service import ResumeStatus
from tests.applications.builders import YES_NO, make_field

T = FieldType
FILLED, NEEDS, MISSING = AnswerStatus.FILLED, AnswerStatus.NEEDS_USER, AnswerStatus.MISSING


def _profile(**kwargs: object) -> CandidateProfile:
    base: dict[str, object] = {
        "identity": Identity(
            full_name="Ada King Lovelace",
            email="ada@example.com",
            phone="+234 800 000 0000",
            city="Abuja",
            country="Nigeria",
        ),
        "links": Links(linkedin="https://linkedin.com/in/ada", github="https://github.com/ada"),
        "experience": [
            Experience(
                company="Old Co", title="Junior Dev", start=date(2018, 1, 1), end=date(2020, 1, 1)
            ),
            Experience(
                company="Acme Pay", title="Backend Developer", start=date(2022, 1, 1), current=True
            ),
        ],
        "preferences": Preferences(salary_minimum=60_000, salary_currency="USD"),
        "eligibility": Eligibility(
            authorized_countries=["Nigeria", "USA"],
            requires_sponsorship=False,
            willing_to_relocate=True,
        ),
    }
    base.update(kwargs)
    return CandidateProfile(**base)


def _ctx(profile: CandidateProfile | None = None, **kwargs: object) -> AnswerContext:
    return AnswerContext(profile=profile or _profile(), **kwargs)  # type: ignore[arg-type]


# --- identity -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "ftype", "expected"),
    [
        ("First name", T.TEXT, "Ada"),
        ("Last name", T.TEXT, "King Lovelace"),
        ("Full name", T.TEXT, "Ada King Lovelace"),
        ("Email address", T.EMAIL, "ada@example.com"),
        ("Phone", T.PHONE, "+234 800 000 0000"),
        ("City", T.TEXT, "Abuja"),
        ("Country", T.TEXT, "Nigeria"),
        ("Current location (city and country)", T.TEXT, "Abuja, Nigeria"),
        ("LinkedIn URL", T.TEXT, "https://linkedin.com/in/ada"),
        ("GitHub", T.TEXT, "https://github.com/ada"),
        ("Current company", T.TEXT, "Acme Pay"),
        ("Current job title", T.TEXT, "Backend Developer"),
    ],
)
def test_identity_comes_from_the_profile(label: str, ftype: T, expected: str) -> None:
    answer = answer_field(make_field(label, ftype), _ctx())
    assert answer is not None
    assert answer.status is FILLED and answer.value == expected
    assert answer.source.startswith("profile:")


@pytest.mark.parametrize("label", ["Portfolio", "Preferred name"])
def test_missing_profile_data_is_missing_never_invented(label: str) -> None:
    answer = answer_field(make_field(label), _ctx())
    assert answer is not None and answer.status is MISSING and answer.value is None


@pytest.mark.parametrize(
    "label", ["Street address", "When can you start?", "How did you hear about us?"]
)
def test_things_the_profile_cannot_know_are_missing(label: str) -> None:
    answer = answer_field(make_field(label), _ctx())
    assert answer is not None and answer.status is MISSING


def test_select_values_map_onto_options_including_country_synonyms() -> None:
    options = (("Select...", ""), ("United States", "US"), ("Nigeria", "NG"), ("Ghana", "GH"))
    field = make_field("Country", T.SELECT, options=options)
    answer = answer_field(field, _ctx())
    assert answer is not None and answer.status is FILLED and answer.value == "NG"

    us = _profile(identity=Identity(full_name="A B", email="a@b.co", country="USA"))
    answer = answer_field(make_field("Country", T.SELECT, options=options), _ctx(us))
    assert answer is not None and answer.value == "US"


def test_no_matching_option_is_missing_not_the_closest_guess() -> None:
    field = make_field("Country", T.SELECT, options=(("Ghana", "GH"), ("Kenya", "KE")))
    answer = answer_field(field, _ctx())
    assert answer is not None and answer.status is MISSING and answer.value is None


def test_a_profile_value_overrides_a_misleading_page_default() -> None:
    field = make_field(
        "Country",
        T.SELECT,
        options=(("United States", "US"), ("Nigeria", "NG")),
        current_value="US",
    )
    answer = answer_field(field, _ctx())
    assert answer is not None and answer.value == "NG"


def test_values_longer_than_the_field_allows_go_to_the_user() -> None:
    answer = answer_field(make_field("Full name", max_length=5), _ctx())
    assert answer is not None and answer.status is NEEDS and "limit" in answer.reason


# --- eligibility: explicit answers only ------------------------------------------------


def _sponsorship(
    label: str,
    requires: bool | None,
    options: tuple[tuple[str, str], ...] = YES_NO,
    confidence: float | None = None,
) -> FieldAnswer | None:
    profile = _profile(eligibility=Eligibility(requires_sponsorship=requires))
    return answer_field(
        make_field(label, T.RADIO, options=options, confidence=confidence), _ctx(profile)
    )


def test_sponsorship_unknown_is_always_a_question() -> None:
    answer = _sponsorship("Will you now or in the future require visa sponsorship?", None)
    assert answer is not None and answer.status is NEEDS and answer.value is None


@pytest.mark.parametrize(
    ("requires", "expected"),
    [(True, "yes"), (False, "no")],
)
def test_sponsorship_follows_the_explicit_answer(requires: bool, expected: str) -> None:
    answer = _sponsorship("Will you require visa sponsorship?", requires)
    assert answer is not None and answer.status is FILLED and answer.value == expected
    assert "explicit" in answer.source


@pytest.mark.parametrize(
    ("requires", "expected"),
    [(False, "yes"), (True, "no")],  # "yes, I can work WITHOUT sponsorship" means needs none
)
def test_sponsorship_polarity_is_respected(requires: bool, expected: str) -> None:
    answer = _sponsorship("Are you able to work in the US without sponsorship?", requires)
    assert answer is not None and answer.status is FILLED and answer.value == expected


def test_combined_eligibility_questions_are_never_auto_answered() -> None:
    label = "Are you authorized to work in the US and will you require sponsorship?"
    profile = _profile(
        eligibility=Eligibility(authorized_countries=["USA"], requires_sponsorship=False)
    )
    answer = answer_field(make_field(label, T.RADIO, options=YES_NO), _ctx(profile))
    assert answer is not None and answer.status is NEEDS


def test_unclear_yes_no_options_go_to_the_user() -> None:
    odd = (("I will need sponsorship", "a"), ("I will not need sponsorship", "b"))
    answer = _sponsorship("Will you require visa sponsorship?", True, options=odd)
    assert answer is not None and answer.status is NEEDS


def test_sponsorship_in_a_text_box_is_not_guessed() -> None:
    profile = _profile(eligibility=Eligibility(requires_sponsorship=False))
    answer = answer_field(make_field("Will you require visa sponsorship?", T.TEXT), _ctx(profile))
    assert answer is not None and answer.status is NEEDS


def test_work_authorization_is_affirmative_only_for_an_explicitly_listed_country() -> None:
    field = make_field(
        "Are you legally authorized to work in the United States?", T.RADIO, options=YES_NO
    )
    answer = answer_field(field, _ctx())  # profile lists "USA"
    assert answer is not None and answer.status is FILLED and answer.value == "yes"


def test_a_country_missing_from_the_list_is_never_answered_no() -> None:
    field = make_field("Are you legally authorized to work in Canada?", T.RADIO, options=YES_NO)
    answer = answer_field(field, _ctx())
    assert answer is not None and answer.status is NEEDS and answer.value is None


def test_work_authorization_without_a_named_country_is_a_question() -> None:
    field = make_field(
        "Are you legally authorized to work in the country of this role?", T.RADIO, options=YES_NO
    )
    answer = answer_field(field, _ctx())
    assert answer is not None and answer.status is NEEDS


def test_work_authorization_with_no_listed_countries_is_a_question() -> None:
    profile = _profile(eligibility=Eligibility())
    field = make_field(
        "Are you legally authorized to work in the United States?", T.RADIO, options=YES_NO
    )
    answer = answer_field(field, _ctx(profile))
    assert answer is not None and answer.status is NEEDS


def test_negatively_phrased_authorization_goes_to_the_user() -> None:
    field = make_field(
        "Are you NOT authorized to work in the United States?", T.RADIO, options=YES_NO
    )
    answer = answer_field(field, _ctx())
    assert answer is not None and answer.status is NEEDS


@pytest.mark.parametrize(
    ("willing", "status", "value"),
    [(True, FILLED, "yes"), (False, FILLED, "no"), (None, NEEDS, None)],
)
def test_relocation_uses_only_an_explicit_answer(
    willing: bool | None, status: AnswerStatus, value: str | None
) -> None:
    profile = _profile(eligibility=Eligibility(willing_to_relocate=willing))
    answer = answer_field(
        make_field("Are you willing to relocate?", T.RADIO, options=YES_NO), _ctx(profile)
    )
    assert answer is not None and answer.status is status and answer.value == value


# --- never answered ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "ftype"),
    [
        ("Gender", T.SELECT),
        ("Veteran status", T.SELECT),
        ("Do you have a disability?", T.RADIO),
        ("Have you ever been convicted of a felony?", T.RADIO),
        ("Do you hold an active security clearance?", T.RADIO),
        ("Are you a current government employee?", T.RADIO),
        ("I have read and agree to the Privacy Policy", T.CHECKBOX),
        ("I certify that this information is true", T.CHECKBOX),
        ("Are you at least 18 years of age?", T.RADIO),
    ],
)
def test_requires_user_questions_are_never_answered(label: str, ftype: T) -> None:
    field = make_field(label, ftype, options=YES_NO)
    answer = answer_field(field, _ctx())
    assert answer is not None and answer.status is NEEDS
    assert answer.value is None and answer.values == []


def test_a_pre_ticked_consent_is_flagged_not_silently_accepted() -> None:
    field = make_field(
        "I agree to the Terms and Conditions", T.CHECKBOX, current_value="true", required=True
    )
    answer = answer_field(field, _ctx())
    assert answer is not None and answer.status is NEEDS and "pre-ticked" in answer.reason


# --- salary -------------------------------------------------------------------------------


def test_salary_uses_the_stated_minimum_with_currency() -> None:
    answer = answer_field(make_field("Desired salary"), _ctx())
    assert answer is not None and answer.status is FILLED and answer.value == "60000 USD"


def test_numeric_salary_fields_get_digits_only() -> None:
    answer = answer_field(make_field("Desired salary", numeric=True), _ctx())
    assert answer is not None and answer.value == "60000"


def test_no_salary_preference_means_the_user_declares_it() -> None:
    profile = _profile(preferences=Preferences())
    answer = answer_field(make_field("Salary expectations"), _ctx(profile))
    assert answer is not None and answer.status is NEEDS and answer.value is None


def test_salary_in_a_dropdown_is_not_guessed() -> None:
    field = make_field("Salary expectations", T.SELECT, options=(("<50k", "a"), ("50-70k", "b")))
    answer = answer_field(field, _ctx())
    assert answer is not None and answer.status is NEEDS


# --- documents ---------------------------------------------------------------------------


def test_resume_upload_uses_the_verified_stored_file(tmp_path: Path) -> None:
    pdf = tmp_path / "cv-abcd1234.pdf"
    pdf.write_bytes(b"%PDF")
    answer = answer_field(
        make_field("Resume/CV", T.FILE), _ctx(resume_path=pdf, resume_status=ResumeStatus.OK)
    )
    assert answer is not None and answer.status is FILLED and answer.value == str(pdf)


@pytest.mark.parametrize(
    ("status", "path", "needle"),
    [
        (ResumeStatus.NONE, None, "no resume on file"),
        (ResumeStatus.MISSING, None, "no resume on file"),
        (ResumeStatus.MODIFIED, "x.pdf", "changed since it was added"),
    ],
)
def test_resume_problems_go_to_the_user(
    status: ResumeStatus, path: str | None, needle: str, tmp_path: Path
) -> None:
    ctx = _ctx(resume_path=(tmp_path / path) if path else None, resume_status=status)
    answer = answer_field(make_field("Resume", T.FILE), ctx)
    assert answer is not None and answer.status is NEEDS and needle in answer.reason


def test_resume_must_be_an_accepted_file_type(tmp_path: Path) -> None:
    docx = tmp_path / "cv.docx"
    docx.write_bytes(b"x")
    ctx = _ctx(resume_path=docx, resume_status=ResumeStatus.OK)
    refused = answer_field(make_field("Resume", T.FILE, accept=".pdf"), ctx)
    assert refused is not None and refused.status is NEEDS and ".pdf" in refused.reason
    accepted = answer_field(make_field("Resume", T.FILE, accept=".pdf,.docx"), ctx)
    assert accepted is not None and accepted.status is FILLED


def test_pasting_resume_text_and_cover_letter_files_are_the_users_job() -> None:
    pasted = answer_field(make_field("Paste your resume", T.TEXTAREA, intent=Intent.RESUME), _ctx())
    assert pasted is not None and pasted.status is NEEDS
    cover_file = answer_field(make_field("Cover letter", T.FILE), _ctx())
    assert cover_file is not None and cover_file.status is NEEDS


def test_written_answers_are_left_to_the_generation_module() -> None:
    assert answer_field(make_field("Cover letter", T.TEXTAREA), _ctx()) is None
    assert answer_field(make_field("Why do you want to work here?", T.TEXTAREA), _ctx()) is None
    assert answer_field(make_field("Why are you interested?", T.TEXT), _ctx()) is None


# --- unknown fields -------------------------------------------------------------------------


def test_unknown_fields_are_prefilled_asked_or_skipped_never_guessed() -> None:
    prefilled = answer_field(make_field("Mystery", current_value="page default"), _ctx())
    assert prefilled is not None and prefilled.status is AnswerStatus.PREFILLED
    required = answer_field(make_field("Mystery", required=True), _ctx())
    assert required is not None and required.status is NEEDS
    optional = answer_field(make_field("Mystery"), _ctx())
    assert optional is not None and optional.status is AnswerStatus.SKIPPED


def test_other_peoples_details_are_never_filled_with_the_candidates() -> None:
    answer = answer_field(make_field("Reference email", T.EMAIL, required=True), _ctx())
    assert answer is not None and answer.status is NEEDS and answer.value is None
