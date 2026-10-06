"""Engine behaviour against a scripted page (fast; no browser)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openapply.applications.answers import AnswerContext
from openapply.applications.engine import ApplicationDraft, ApplicationEngine, ApplicationError
from openapply.applications.models import (
    AnswerStatus,
    ApplicationField,
    FieldAnswer,
    FieldType,
    Intent,
    Sensitivity,
)
from openapply.applications.service import prepare_application
from openapply.candidate.models import CandidateProfile, Eligibility, Identity
from openapply.candidate.service import ResumeStatus
from tests.applications.fakes import FakeSession, raw, scan_of
from tests.jobs.builders import make_job, make_profile
from tests.providers.fakes import ScriptedProvider

YES_NO = [("Yes", "yes"), ("No", "no")]


def _profile(**eligibility: object) -> CandidateProfile:
    return make_profile(eligibility=Eligibility(**eligibility)).model_copy(
        update={
            "identity": Identity(
                full_name="Ada Lovelace",
                email="ada@example.com",
                phone="+234 800",
                city="Abuja",
                country="Nigeria",
            )
        }
    )


async def _prepare(
    session: FakeSession,
    *,
    provider: ScriptedProvider | None = None,
    profile: CandidateProfile | None = None,
    resume: Path | None = None,
) -> tuple[ApplicationEngine, ApplicationDraft]:
    profile = profile or _profile()
    context = AnswerContext(
        profile=profile,
        resume_path=resume,
        resume_status=ResumeStatus.OK if resume else ResumeStatus.NONE,
    )
    return await prepare_application(
        session, profile, context, provider=provider, job=make_job() if provider else None
    )


def _field(draft: ApplicationDraft, field_id: str) -> tuple[ApplicationField, FieldAnswer | None]:
    return draft.field(field_id), draft.answers.get(field_id)


# --- filling every control type ---------------------------------------------------------


async def test_each_control_type_is_filled_the_right_way(tmp_path: Path) -> None:
    pdf = tmp_path / "cv-abcd1234.pdf"
    pdf.write_bytes(b"%PDF")
    session = FakeSession(
        scan_of(
            raw("oa-0", "text", "First name"),
            raw("oa-1", "select", "Country", options=[("Select", ""), ("Nigeria", "NG")]),
            raw("oa-2", "radio", "Are you willing to relocate?", options=YES_NO),
            raw("oa-3", "file", "Resume"),
        )
    )
    profile = _profile(willing_to_relocate=True)
    _engine, draft = await _prepare(session, profile=profile, resume=pdf)
    assert session.calls == [
        ("text", "oa-0", "Ada"),
        ("select", "oa-1", "NG"),
        ("check", "oa-2-o0", True),  # the "Yes" radio, by its own element id
        ("upload", "oa-3", str(pdf)),
    ]
    assert all(a.applied for a in draft.answers.values())
    assert draft.blockers() == []


async def test_checkbox_groups_set_every_member_explicitly() -> None:
    session = FakeSession(
        scan_of(
            raw(
                "oa-0",
                "checkbox",
                "Which of these apply?",
                options=[("Remote", "remote"), ("Hybrid", "hybrid")],
            )
        )
    )
    engine, draft = await _prepare(session)
    # an unrecognised group is left alone by the engine...
    assert session.calls == []
    # ...and when the user answers, each option is set (ticked or cleared) explicitly
    assert await engine.set_user_answer(draft, "oa-0", ["hybrid"]) is None
    assert session.calls == [("check", "oa-0-o0", False), ("check", "oa-0-o1", True)]
    assert draft.display_value(draft.field("oa-0")) == "Hybrid"
    assert await engine.set_user_answer(draft, "oa-0", ["nonsense"]) is not None


async def test_the_page_rejecting_a_value_becomes_a_question_not_a_silent_success() -> None:
    session = FakeSession(
        scan_of(raw("oa-0", "text", "First name", required=True)),
        reject={"oa-0": "the page changed the value after it was set"},
    )
    _engine, draft = await _prepare(session)
    answer = draft.answers["oa-0"]
    assert answer.status is AnswerStatus.NEEDS_USER and not answer.applied
    assert "did not accept" in answer.reason
    assert [f.label for f, _ in draft.blockers()] == ["First name"]


async def test_no_answers_means_nothing_is_written_to_the_page() -> None:
    session = FakeSession(
        scan_of(
            raw("oa-0", "select", "Gender", options=[("Select", ""), ("Male", "m")]),
            raw("oa-1", "radio", "Have you ever been convicted of a felony?", options=YES_NO),
            raw("oa-2", "checkbox", "I agree to the Terms and Conditions", required=True),
        )
    )
    _engine, draft = await _prepare(session)
    assert session.calls == []
    assert all(a.status is AnswerStatus.NEEDS_USER for a in draft.answers.values())
    assert [f.label for f, _ in draft.blockers()] == ["I agree to the Terms and Conditions"]
    assert {f.label for f in draft.needs_attention()} == {
        "Gender",
        "Have you ever been convicted of a felony?",
        "I agree to the Terms and Conditions",
    }


# --- written answers and the AI ---------------------------------------------------------


async def test_without_an_ai_written_questions_go_to_the_user() -> None:
    session = FakeSession(
        scan_of(raw("oa-0", "textarea", "Why do you want this job?", required=True))
    )
    _engine, draft = await _prepare(session, provider=None)
    answer = draft.answers["oa-0"]
    assert answer.status is AnswerStatus.NEEDS_USER and "--no-ai" in answer.reason
    assert session.calls == []


async def test_a_generated_answer_is_written_into_the_page() -> None:
    session = FakeSession(scan_of(raw("oa-0", "textarea", "Why do you want this job?")))
    provider = ScriptedProvider(["I build payment APIs, which is what this team does."])
    _engine, draft = await _prepare(session, provider=provider)
    answer = draft.answers["oa-0"]
    assert answer.status is AnswerStatus.GENERATED and answer.applied
    assert session.calls == [
        ("text", "oa-0", "I build payment APIs, which is what this team does.")
    ]


async def test_the_ai_may_classify_only_fields_no_rule_recognised() -> None:
    session = FakeSession(
        scan_of(
            raw("oa-0", "text", "Zzz", name="q_1"),  # no rule matches: eligible
            raw("oa-1", "email", "Reference email"),  # about someone else: never eligible
            raw("oa-2", "text", "Contact (phone or email)"),  # ambiguous: never eligible
        )
    )
    provider = ScriptedProvider([json.dumps({"fields": [{"id": "oa-0", "intent": "phone"}]})])
    _engine, draft = await _prepare(session, provider=provider)
    prompt = provider.prompts[0]
    assert "oa-0" in prompt and "Reference email" not in prompt and "Contact (phone" not in prompt
    field, answer = _field(draft, "oa-0")
    assert field.intent is Intent.PHONE and field.classified_by == "ai"
    assert draft.ai_classified == {"oa-0"}
    assert (
        answer is not None and answer.status is AnswerStatus.FILLED and answer.value == "+234 800"
    )
    # the other two stayed unknown, and an unrequired unknown field is skipped, never filled
    for untouched in ("oa-1", "oa-2"):
        assert draft.field(untouched).intent is Intent.UNKNOWN
        assert draft.answers[untouched].status is AnswerStatus.SKIPPED
        assert draft.answers[untouched].value is None
    assert [call[1] for call in session.calls] == ["oa-0"]  # only the recognised field was written


async def test_ai_classification_cannot_assign_sensitive_intents_or_unknown_ids() -> None:
    session = FakeSession(scan_of(raw("oa-0", "text", "Zzz", name="q_1")))
    reply = {
        "fields": [
            {"id": "oa-0", "intent": "demographic"},  # sensitive: refused
            {"id": "oa-0", "intent": "work_authorization"},  # sensitive: refused
            {"id": "oa-99", "intent": "phone"},  # never asked about
            {"id": "oa-0", "intent": "not-an-intent"},
            "garbage",
        ]
    }
    _engine, draft = await _prepare(session, provider=ScriptedProvider([json.dumps(reply)]))
    field = draft.field("oa-0")
    assert field.intent is Intent.UNKNOWN and field.classified_by == "rules"
    assert draft.ai_classified == set()


async def test_ai_classification_failure_is_not_fatal() -> None:
    session = FakeSession(scan_of(raw("oa-0", "text", "Zzz", name="q_1")))
    _engine, draft = await _prepare(session, provider=ScriptedProvider(["nope", "still nope"]))
    assert draft.field("oa-0").intent is Intent.UNKNOWN


# --- radio-group question promotion -------------------------------------------------------


async def test_a_radio_groups_question_text_is_promoted_to_its_label() -> None:
    session = FakeSession(
        scan_of(
            raw(
                "oa-0",
                "radio",
                "",
                options=YES_NO,
                nearby="Will you require visa sponsorship to work in this role?",
                required=True,
            )
        )
    )
    profile = _profile(requires_sponsorship=False)
    _engine, draft = await _prepare(session, profile=profile)
    field = draft.field("oa-0")
    assert field.label == "Will you require visa sponsorship to work in this role?"
    assert field.intent is Intent.SPONSORSHIP and field.confidence >= 0.9
    assert draft.answers["oa-0"].status is AnswerStatus.FILLED
    assert session.calls == [("check", "oa-0-o1", True)]  # "No"


async def test_labels_are_cleaned_of_required_markers() -> None:
    session = FakeSession(scan_of(raw("oa-0", "text", "First name *  (required)")))
    _engine, draft = await _prepare(session)
    assert draft.field("oa-0").label == "First name"


async def test_a_field_with_no_label_at_all_still_has_a_readable_one() -> None:
    session = FakeSession(scan_of(raw("oa-0", "text", "", name="job_application[q_77]")))
    _engine, draft = await _prepare(session)
    assert "q 77" in draft.field("oa-0").label


# --- display, blockers, target ------------------------------------------------------------


async def test_display_values_are_human_readable(tmp_path: Path) -> None:
    pdf = tmp_path / "cv-abcd1234.pdf"
    pdf.write_bytes(b"%PDF")
    session = FakeSession(
        scan_of(
            raw("oa-0", "select", "Country", options=[("Select", ""), ("Nigeria", "NG")]),
            raw("oa-1", "file", "Resume"),
            raw("oa-2", "checkbox", "I agree to the Terms"),
        )
    )
    engine, draft = await _prepare(session, resume=pdf)
    assert draft.display_value(draft.field("oa-0")) == "Nigeria"  # label, not the code "NG"
    assert draft.display_value(draft.field("oa-1")) == "cv-abcd1234.pdf"  # name, not the path
    assert draft.display_value(draft.field("oa-2")) == ""
    assert await engine.set_user_answer(draft, "oa-2", "true") is None
    assert draft.display_value(draft.field("oa-2")) == "ticked"


@pytest.mark.parametrize(
    ("action", "warns"),
    [
        (None, False),
        ("https://careers.example.com/submit", False),
        ("https://careers.example.com:443/submit", True),  # explicit port is a different netloc
        ("https://forms.other.example/collect", True),
        ("http://careers.example.com/submit", True),  # a downgrade from https is different too
    ],
)
async def test_a_form_that_posts_elsewhere_is_called_out(action: str | None, warns: bool) -> None:
    session = FakeSession(scan_of(raw("oa-0", "text", "First name"), action=action))
    _engine, draft = await _prepare(session)
    assert (draft.target_origin_warning() is not None) is warns


# --- submitting -----------------------------------------------------------------------------


async def test_submit_requires_confirmation_resolution_and_a_button() -> None:
    session = FakeSession(scan_of(raw("oa-0", "text", "First name", required=True)))
    engine, draft = await _prepare(session)
    with pytest.raises(ApplicationError, match="confirmation"):
        await engine.submit(draft, confirmed=False)
    await engine.submit(draft, confirmed=True)
    assert session.submitted == 1

    blocked = FakeSession(scan_of(raw("oa-0", "select", "Gender", required=True, options=YES_NO)))
    engine, draft = await _prepare(blocked)
    with pytest.raises(ApplicationError, match="unresolved: Gender"):
        await engine.submit(draft, confirmed=True)
    assert blocked.submitted == 0

    no_button = FakeSession(scan_of(raw("oa-0", "text", "First name"), submit=None))
    engine, draft = await _prepare(no_button)
    with pytest.raises(ApplicationError, match="No submit button"):
        await engine.submit(draft, confirmed=True)
    assert no_button.submitted == 0


# --- user edits -----------------------------------------------------------------------------


async def test_a_rejected_user_edit_keeps_the_previous_answer() -> None:
    session = FakeSession(scan_of(raw("oa-0", "text", "City")), reject={})
    engine, draft = await _prepare(session)
    assert draft.answers["oa-0"].value == "Abuja"
    session._reject["oa-0"] = "the page refused"
    error = await engine.set_user_answer(draft, "oa-0", "Lagos")
    assert error is not None and "did not accept" in error
    assert draft.answers["oa-0"].value == "Abuja"  # unchanged


async def test_clearing_an_optional_field_skips_it() -> None:
    session = FakeSession(scan_of(raw("oa-0", "text", "City")))
    engine, draft = await _prepare(session)
    assert await engine.set_user_answer(draft, "oa-0", "") is None
    assert draft.answers["oa-0"].status is AnswerStatus.SKIPPED


async def test_user_edits_of_choice_fields_must_use_real_options() -> None:
    session = FakeSession(
        scan_of(
            raw("oa-0", "radio", "Pick one", options=[("A", "a"), ("B", "b")]),
            raw("oa-1", "select", "Pick two", options=[("Select", ""), ("C", "c")]),
        )
    )
    engine, draft = await _prepare(session)
    assert await engine.set_user_answer(draft, "oa-0", "z") is not None
    assert await engine.set_user_answer(draft, "oa-1", "z") is not None
    assert await engine.set_user_answer(draft, "oa-0", "b") is None
    assert ("check", "oa-0-o1", True) in session.calls
    assert await engine.set_user_answer(draft, "oa-1", "c") is None
    assert ("select", "oa-1", "c") in session.calls


async def test_user_can_supply_a_file_and_only_a_valid_one(tmp_path: Path) -> None:
    session = FakeSession(scan_of(raw("oa-0", "file", "Portfolio samples")))
    engine, draft = await _prepare(session)
    good = tmp_path / "work.pdf"
    good.write_bytes(b"%PDF")
    assert await engine.set_user_answer(draft, "oa-0", str(good)) is None
    assert session.calls == [("upload", "oa-0", str(good))]
    assert draft.field("oa-0").type is FieldType.FILE
    exe = tmp_path / "tool.exe"
    exe.write_bytes(b"MZ")
    assert await engine.set_user_answer(draft, "oa-0", str(exe)) is not None


def test_sensitivity_of_every_scanned_field_is_recorded() -> None:
    # guard: the models keep sensitivity in step with intent
    from openapply.applications.models import sensitivity_for

    for intent in Intent:
        assert sensitivity_for(intent) in set(Sensitivity)


# --- anything sensitive the page pre-set must be decided by the user -----------------------------


async def test_an_optional_pre_ticked_consent_blocks_until_the_user_decides() -> None:
    session = FakeSession(
        scan_of(raw("oa-0", "checkbox", "Send me occasional job alerts", current_value="true"))
    )
    engine, draft = await _prepare(session)
    assert draft.field("oa-0").required is False
    assert [f.label for f, _ in draft.blockers()] == ["Send me occasional job alerts"]
    assert "pre-set" in draft.blockers()[0][1]
    with pytest.raises(ApplicationError, match="unresolved"):
        await engine.submit(draft, confirmed=True)
    assert session.submitted == 0

    # either explicit decision settles it: untick...
    assert await engine.set_user_answer(draft, "oa-0", "false") is None
    assert draft.blockers() == []
    assert ("check", "oa-0", False) in session.calls
    await engine.submit(draft, confirmed=True)
    assert session.submitted == 1


async def test_knowingly_keeping_a_pre_ticked_consent_is_allowed() -> None:
    session = FakeSession(
        scan_of(raw("oa-0", "checkbox", "Send me occasional job alerts", current_value="true"))
    )
    engine, draft = await _prepare(session)
    assert await engine.set_user_answer(draft, "oa-0", "true") is None
    assert draft.blockers() == []
    assert draft.answers["oa-0"].status is AnswerStatus.USER


async def test_an_untouched_optional_consent_is_not_a_blocker() -> None:
    session = FakeSession(scan_of(raw("oa-0", "checkbox", "Send me occasional job alerts")))
    _engine, draft = await _prepare(session)
    assert draft.blockers() == []  # nothing pre-set, nothing will be sent


async def test_a_preselected_sensitive_choice_must_be_confirmed_too() -> None:
    session = FakeSession(
        scan_of(
            raw(
                "oa-0",
                "select",
                "Gender",
                options=[("Select", ""), ("Male", "m"), ("Prefer not to say", "x")],
                current_value="x",
            ),
            raw("oa-1", "radio", "Have you ever been convicted of a felony?", options=YES_NO),
        )
    )
    engine, draft = await _prepare(session)
    assert [f.label for f, _ in draft.blockers()] == ["Gender"]  # the unset radio stays optional
    assert await engine.set_user_answer(draft, "oa-0", "x") is None  # keep it, knowingly
    assert draft.blockers() == []


async def test_a_page_default_on_an_ordinary_field_is_not_a_blocker() -> None:
    session = FakeSession(scan_of(raw("oa-0", "text", "Zzz", name="q_1", current_value="default")))
    _engine, draft = await _prepare(session)
    assert draft.answers["oa-0"].status is AnswerStatus.PREFILLED
    assert draft.blockers() == []
