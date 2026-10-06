"""Scan the local fixtures in a real browser and check what the engine understands."""

from __future__ import annotations

import pytest

from openapply.applications.answers import AnswerContext
from openapply.applications.engine import ApplicationDraft, ApplicationEngine
from openapply.applications.models import FieldType, Intent, Sensitivity
from openapply.browser.forms import open_form_session
from openapply.browser.page import BrowserError
from tests.applications.conftest import AppServer
from tests.jobs.builders import make_profile


async def scan(server: AppServer, page: str) -> ApplicationDraft:
    profile = make_profile()
    async with open_form_session(
        f"{server.url}/{page}.html", allow_local=True, headless=True
    ) as session:
        return await ApplicationEngine(session, profile, AnswerContext(profile=profile)).scan()


def by_label(draft: ApplicationDraft, text: str):  # type: ignore[no-untyped-def]
    matches = [f for f in draft.scan.fields if text.lower() in f.label.lower()]
    assert len(matches) == 1, f"{text!r} matched {[f.label for f in matches]}"
    return matches[0]


async def test_simple_application(server: AppServer) -> None:
    draft = await scan(server, "simple_application")
    assert draft.scan.submit_label == "Submit application"
    assert draft.scan.form_action == f"{server.url}/submit"
    assert draft.target_origin_warning() is None
    assert [f.type for f in draft.scan.fields] == [
        FieldType.TEXT, FieldType.TEXT, FieldType.EMAIL, FieldType.PHONE, FieldType.TEXT,
        FieldType.SELECT, FieldType.TEXT, FieldType.FILE, FieldType.TEXTAREA,
    ]  # fmt: skip
    first = by_label(draft, "First name")
    assert first.required and first.intent is Intent.FIRST_NAME and first.label == "First name"
    assert by_label(draft, "Phone").required is False
    assert by_label(draft, "Email").intent is Intent.EMAIL
    country = by_label(draft, "Country")
    assert country.intent is Intent.COUNTRY
    assert [o.label for o in country.options] == ["Select...", "United States", "Nigeria", "Ghana"]
    assert country.current_value is None  # the "Select..." placeholder is not a value
    resume = by_label(draft, "Resume")
    assert resume.intent is Intent.RESUME and resume.accept == ".pdf,.docx"
    why = by_label(draft, "Why are you interested")
    assert why.intent is Intent.OPEN_ENDED and why.max_length == 500 and why.required


async def test_greenhouse_like_form(server: AppServer) -> None:
    draft = await scan(server, "greenhouse_like")
    # the job-alert newsletter form is not the application
    assert not any("alert" in (f.name or "") for f in draft.scan.fields)
    assert "Several forms were found" in " ".join(draft.warnings)
    assert draft.scan.submit_label == "Submit Application"
    # a visually hidden file input behind an "Attach" button is still found and labelled
    resume = next(f for f in draft.scan.fields if f.type is FieldType.FILE)
    assert resume.intent is Intent.RESUME and resume.required
    assert by_label(draft, "authorized to work").intent is Intent.WORK_AUTHORIZATION
    assert by_label(draft, "require sponsorship").intent is Intent.SPONSORSHIP
    for label in ("Gender", "Veteran Status", "Disability Status"):
        assert by_label(draft, label).sensitivity is Sensitivity.REQUIRES_USER
    assert by_label(draft, "Why do you want").intent is Intent.OPEN_ENDED
    assert by_label(draft, "LinkedIn").intent is Intent.LINKEDIN
    assert by_label(draft, "Website").intent is Intent.PORTFOLIO


async def test_lever_like_form_with_implicit_labels_and_radio_cards(server: AppServer) -> None:
    draft = await scan(server, "lever_like")
    assert by_label(draft, "Full name").intent is Intent.FULL_NAME
    assert by_label(draft, "Current company").intent is Intent.CURRENT_COMPANY
    assert by_label(draft, "GitHub").intent is Intent.GITHUB
    assert by_label(draft, "Portfolio").intent is Intent.PORTFOLIO
    # radio groups: one question each, with the question promoted from nearby text to the label
    sponsor = by_label(draft, "visa sponsorship")
    assert sponsor.type is FieldType.RADIO and sponsor.required
    assert sponsor.intent is Intent.SPONSORSHIP and sponsor.confidence >= 0.9
    assert [o.label for o in sponsor.options] == ["Yes", "No"]
    assert all(o.element_id for o in sponsor.options)
    assert by_label(draft, "relocate").intent is Intent.RELOCATION
    # the consent checkbox is recognised as a legal acknowledgement, never to be auto-ticked
    consent = by_label(draft, "I consent")
    assert consent.type is FieldType.CHECKBOX and consent.required
    assert consent.intent is Intent.LEGAL_ACK
    # its placeholder invites a cover letter; either way it is a written (generated) answer
    assert by_label(draft, "Additional information").intent in {
        Intent.COVER_LETTER,
        Intent.OPEN_ENDED,
    }


async def test_ambiguous_fields_are_not_guessed(server: AppServer) -> None:
    draft = await scan(server, "ambiguous_fields")
    intents = {f.name: f.intent for f in draft.scan.fields}
    assert intents["f1"] is Intent.FULL_NAME  # "Name"
    assert intents["f2"] is Intent.EMAIL  # only a placeholder says so
    assert intents["f6"] is Intent.PHONE  # only an aria-label says so
    assert intents["f7"] is Intent.LOCATION
    assert intents["f8"] is Intent.CURRENT_COMPANY
    assert intents["f14"] is Intent.OPEN_ENDED
    # genuinely unclear, or about someone else: left unknown rather than filled
    for name in ("q_4821", "f4", "f5", "f9", "f10", "f11", "f12"):
        assert intents[name] is Intent.UNKNOWN, name
    unknown = draft_unclassified(draft)
    assert {"q_4821", "f5", "f11", "f12"} <= unknown  # no rule matched: AI-eligible
    assert "f9" not in unknown and "f10" not in unknown and "f4" not in unknown  # never AI-eligible
    eligible = by_label_or_name(draft, "eligible")
    assert eligible.type is FieldType.RADIO
    assert eligible.intent is Intent.WORK_AUTHORIZATION  # question text lives in nearby prose


def draft_unclassified(draft: ApplicationDraft) -> set[str]:
    """Names of fields whose intent is unknown *because no rule matched* (not by design)."""
    return {
        f.name or ""
        for f in draft.scan.fields
        if f.intent is Intent.UNKNOWN and f.confidence == 0.0
    }


def by_label_or_name(draft: ApplicationDraft, name: str):  # type: ignore[no-untyped-def]
    return next(f for f in draft.scan.fields if f.name == name)


async def test_traps_honeypots_logins_and_search_are_left_alone(server: AppServer) -> None:
    draft = await scan(server, "traps")
    names = {f.name for f in draft.scan.fields}
    assert names == {"first_name", "email", "about"}  # nothing a person could not see
    for trap in ("website_url", "fax", "company_secondary", "trap", "csrf", "pass", "user", "q"):
        assert trap not in names
    assert any("password field" in w for w in draft.warnings)
    assert draft.scan.submit_label == "Apply now"
    warning = draft.target_origin_warning()
    assert warning is not None and "forms.third-party.example" in warning


async def test_sensitive_questions_are_classified_as_such(server: AppServer) -> None:
    draft = await scan(server, "sensitive_questions")
    expected = {
        "auth": (Intent.WORK_AUTHORIZATION, Sensitivity.HIGH),
        "sponsor": (Intent.SPONSORSHIP, Sensitivity.HIGH),
        "relocate": (Intent.RELOCATION, Sensitivity.HIGH),
        "gender": (Intent.DEMOGRAPHIC, Sensitivity.REQUIRES_USER),
        "felony": (Intent.CRIMINAL, Sensitivity.REQUIRES_USER),
        "clearance": (Intent.CLEARANCE, Sensitivity.REQUIRES_USER),
        "terms": (Intent.LEGAL_ACK, Sensitivity.REQUIRES_USER),
        "marketing": (Intent.LEGAL_ACK, Sensitivity.REQUIRES_USER),
        "salary": (Intent.SALARY, Sensitivity.MEDIUM),
    }
    by_name = {f.name: f for f in draft.scan.fields}
    for name, (intent, sensitivity) in expected.items():
        assert by_name[name].intent is intent, name
        assert by_name[name].sensitivity is sensitivity, name
    assert by_name["marketing"].current_value == "true"  # pre-ticked by the page
    # the question text came from the <legend>, since the radios themselves have no label
    assert by_name["auth"].label.startswith("Are you legally authorized")
    assert [o.label for o in by_name["auth"].options] == ["Yes", "No"]


async def test_untrusted_urls_are_refused_before_a_browser_starts() -> None:
    with pytest.raises(BrowserError, match="--allow-local"):
        async with open_form_session("http://127.0.0.1:9/x", headless=True):
            pass
    with pytest.raises(BrowserError, match="http"):
        async with open_form_session("file:///etc/passwd", headless=True):
            pass
