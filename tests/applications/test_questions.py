from __future__ import annotations

import pytest

from openapply.applications.models import (
    AI_ASSIGNABLE_INTENTS,
    FieldType,
    Intent,
    Sensitivity,
    sensitivity_for,
)
from openapply.applications.questions import Signals, classify, normalize

T = FieldType


@pytest.mark.parametrize(
    ("field_type", "signals", "expected"),
    [
        # identity
        (T.TEXT, {"label": "First Name *"}, Intent.FIRST_NAME),
        (T.TEXT, {"label": "Given name"}, Intent.FIRST_NAME),
        (T.TEXT, {"label": "Last name"}, Intent.LAST_NAME),
        (T.TEXT, {"label": "Surname"}, Intent.LAST_NAME),
        (T.TEXT, {"label": "Full name"}, Intent.FULL_NAME),
        (T.TEXT, {"label": "Name"}, Intent.FULL_NAME),
        (T.TEXT, {"label": "Your name"}, Intent.FULL_NAME),
        (T.TEXT, {"label": "Preferred name"}, Intent.PREFERRED_NAME),
        (T.TEXT, {"label": "firstName"}, Intent.FIRST_NAME),
        (T.TEXT, {"label": "", "name": "first_name"}, Intent.FIRST_NAME),
        (T.TEXT, {"label": "", "name": "lastName"}, Intent.LAST_NAME),
        (T.TEXT, {"label": "", "placeholder": "Your name"}, Intent.FULL_NAME),
        (T.TEXT, {"label": "", "aria_label": "Email"}, Intent.EMAIL),
        # the generic word must not beat the specific one
        (T.EMAIL, {"label": "Email address"}, Intent.EMAIL),
        (T.TEXT, {"label": "Email Address *"}, Intent.EMAIL),
        (T.TEXT, {"label": "LinkedIn URL"}, Intent.LINKEDIN),
        (T.TEXT, {"label": "GitHub URL"}, Intent.GITHUB),
        (T.TEXT, {"label": "LinkedIn Profile"}, Intent.LINKEDIN),
        (T.TEXT, {"label": "Personal website URL"}, Intent.PORTFOLIO),
        (T.TEXT, {"label": "Website"}, Intent.PORTFOLIO),
        (T.TEXT, {"label": "Portfolio"}, Intent.PORTFOLIO),
        (T.TEXT, {"label": "Phone"}, Intent.PHONE),
        (T.TEXT, {"label": "Phone number"}, Intent.PHONE),
        (T.TEXT, {"label": "Mobile number"}, Intent.PHONE),
        (T.PHONE, {"label": ""}, Intent.PHONE),
        # location
        (T.TEXT, {"label": "City"}, Intent.CITY),
        (T.TEXT, {"label": "Country"}, Intent.COUNTRY),
        (T.SELECT, {"label": "Country of residence"}, Intent.COUNTRY),
        (T.TEXT, {"label": "Current location (city and country)"}, Intent.LOCATION),
        (T.TEXT, {"label": "Location"}, Intent.LOCATION),
        (T.TEXT, {"label": "Street address"}, Intent.ADDRESS),
        (T.TEXT, {"label": "Zip / Postal code"}, Intent.ADDRESS),
        (T.TEXT, {"label": "State"}, Intent.ADDRESS),
        # documents and professional
        (T.FILE, {"label": "Resume/CV"}, Intent.RESUME),
        (T.FILE, {"label": "Attach your resume"}, Intent.RESUME),
        (T.TEXTAREA, {"label": "Cover letter"}, Intent.COVER_LETTER),
        (T.FILE, {"label": "Cover Letter"}, Intent.COVER_LETTER),
        (T.TEXT, {"label": "Current company"}, Intent.CURRENT_COMPANY),
        (T.TEXT, {"label": "Company name"}, Intent.CURRENT_COMPANY),
        (T.TEXT, {"label": "Current job title"}, Intent.CURRENT_TITLE),
        (T.TEXT, {"label": "Desired salary"}, Intent.SALARY),
        (T.TEXT, {"label": "Salary expectations"}, Intent.SALARY),
        (T.TEXT, {"label": "When can you start?"}, Intent.AVAILABILITY),
        (T.SELECT, {"label": "Notice period"}, Intent.AVAILABILITY),
        (T.TEXT, {"label": "How did you hear about this job?"}, Intent.REFERRAL_SOURCE),
        # HIGH
        (
            T.RADIO,
            {"label": "Will you now or in the future require visa sponsorship?"},
            Intent.SPONSORSHIP,
        ),
        (
            T.RADIO,
            {"label": "Are you legally authorized to work in the United States?"},
            Intent.WORK_AUTHORIZATION,
        ),
        (T.RADIO, {"label": "Are you willing to relocate?"}, Intent.RELOCATION),
        (  # a sponsorship question that also says "visa status" is still just about sponsorship
            T.SELECT,
            {
                "label": (
                    "Will you now or in the future require sponsorship for employment visa status?"
                )
            },
            Intent.SPONSORSHIP,
        ),
        (T.SELECT, {"label": "What is your current visa status?"}, Intent.WORK_AUTHORIZATION),
        # REQUIRES_USER
        (T.SELECT, {"label": "Gender"}, Intent.DEMOGRAPHIC),
        (T.SELECT, {"label": "Veteran status"}, Intent.DEMOGRAPHIC),
        (T.SELECT, {"label": "Do you have a disability?"}, Intent.DEMOGRAPHIC),
        (T.SELECT, {"label": "Race / Ethnicity"}, Intent.DEMOGRAPHIC),
        (T.TEXT, {"label": "Have you ever been convicted of a felony?"}, Intent.CRIMINAL),
        (T.RADIO, {"label": "Do you hold an active security clearance?"}, Intent.CLEARANCE),
        (
            T.RADIO,
            {"label": "Are you a current government employee?"},
            Intent.GOVERNMENT_RESTRICTION,
        ),
        (T.CHECKBOX, {"label": "I have read and agree to the Privacy Policy"}, Intent.LEGAL_ACK),
        (T.CHECKBOX, {"label": "I certify that the information above is true"}, Intent.LEGAL_ACK),
        (T.CHECKBOX, {"label": "Sign me up for the newsletter"}, Intent.LEGAL_ACK),
        (T.RADIO, {"label": "Are you at least 18 years of age?"}, Intent.LEGAL_ACK),
        # free text
        (T.TEXTAREA, {"label": "Why do you want to work here?"}, Intent.OPEN_ENDED),
        (T.TEXTAREA, {"label": "State your motivation"}, Intent.OPEN_ENDED),  # not "state"/address
        (T.TEXTAREA, {"label": ""}, Intent.OPEN_ENDED),
        (T.TEXT, {"label": "Why are you interested in this role?"}, Intent.OPEN_ENDED),
    ],
)
def test_classification(field_type: FieldType, signals: dict[str, str], expected: Intent) -> None:
    result = classify(Signals(field_type=field_type, **signals))
    assert result.intent is expected, (signals, result)
    assert result.sensitivity is sensitivity_for(expected)


@pytest.mark.parametrize(
    ("field_type", "signals"),
    [
        (T.EMAIL, {"label": "Reference email"}),
        (T.PHONE, {"label": "Emergency contact phone"}),
        (T.TEXT, {"label": "Manager's name"}),
        (T.TEXT, {"label": "Referee full name"}),
        (T.TEXT, {"label": "Spouse phone number"}),
        (T.TEXT, {"label": "", "name": "q_4821"}),
        (T.TEXT, {"label": "Contact (phone or email)"}),
        (T.TEXT, {"label": "Link"}),
        (T.FILE, {"label": "Upload"}),
        (T.DATE, {"label": "Date"}),
    ],
)
def test_ambiguous_or_third_party_fields_are_unknown(
    field_type: FieldType, signals: dict[str, str]
) -> None:
    result = classify(Signals(field_type=field_type, **signals))
    assert result.intent is Intent.UNKNOWN, (signals, result)
    assert result.confidence <= 0.5


def test_clear_labels_beat_nearby_prose() -> None:
    nearby = "We value diversity. Tell us your gender and race in the survey below."
    result = classify(Signals(field_type=T.TEXT, label="Name", nearby=nearby))
    assert result.intent is Intent.FULL_NAME


def test_nearby_prose_can_only_make_a_field_more_careful() -> None:
    sensitive = classify(
        Signals(field_type=T.TEXT, label="", nearby="Have you been convicted of a felony?")
    )
    assert sensitive.intent is Intent.CRIMINAL
    # ...but nearby text alone never *grants* an identity intent
    ordinary = classify(Signals(field_type=T.TEXT, label="", nearby="Enter your email below"))
    assert ordinary.intent is Intent.UNKNOWN


def test_combined_questions_lose_confidence_so_they_are_never_auto_answered() -> None:
    label = "Are you authorized to work in the US and will you require sponsorship?"
    result = classify(Signals(field_type=T.RADIO, label=label))
    assert result.sensitivity is Sensitivity.HIGH
    assert result.confidence <= 0.5


def test_a_plain_sponsorship_question_is_confident_so_it_can_use_an_explicit_answer() -> None:
    label = "Will you now or in the future require sponsorship for employment visa status?"
    result = classify(Signals(field_type=T.SELECT, label=label))
    assert result.intent is Intent.SPONSORSHIP
    assert result.confidence >= 0.9  # not treated as a combined question


def test_a_sensitive_signal_anywhere_beats_an_identity_label() -> None:
    result = classify(Signals(field_type=T.TEXT, label="Name", name="veteran_status"))
    # strong label wins over the field *name*: identity fields stay fillable...
    assert result.intent is Intent.FULL_NAME
    # ...but a sensitive label always wins over everything else
    result = classify(Signals(field_type=T.TEXT, label="Gender", name="first_name"))
    assert result.intent is Intent.DEMOGRAPHIC


def test_ai_may_never_assign_a_sensitive_intent() -> None:
    for intent in Intent:
        if sensitivity_for(intent) in {Sensitivity.HIGH, Sensitivity.REQUIRES_USER}:
            assert intent not in AI_ASSIGNABLE_INTENTS, intent


def test_every_intent_has_a_sensitivity() -> None:
    for intent in Intent:
        assert isinstance(sensitivity_for(intent), Sensitivity)


def test_normalize_splits_camel_case_and_punctuation() -> None:
    assert normalize("firstName") == "first name"
    assert normalize("  Phone-Number * ") == "phone number"
    assert normalize("q_4821") == "q 4821"
