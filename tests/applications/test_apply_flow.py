"""End to end in a real browser: what an application would actually send."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from pathlib import Path

import pytest

from openapply.applications.answers import AnswerContext
from openapply.applications.engine import ApplicationDraft, ApplicationEngine, ApplicationError
from openapply.applications.models import AnswerStatus
from openapply.applications.service import prepare_application
from openapply.browser.forms import PlaywrightFormSession, open_form_session
from openapply.browser.page import BrowserError
from openapply.candidate.models import (
    CandidateProfile,
    Eligibility,
    Identity,
    Links,
    Preferences,
)
from openapply.candidate.service import CandidateService
from tests.applications.conftest import AppServer
from tests.jobs.builders import make_job
from tests.providers.fakes import ScriptedProvider

WHY = "I build payment APIs in Python and Django, which is exactly what this role needs."


def _profile() -> CandidateProfile:
    return CandidateProfile(
        identity=Identity(
            full_name="Ada Lovelace",
            email="ada@example.com",
            phone="+234 800 000 0000",
            city="Abuja",
            country="Nigeria",
        ),
        links=Links(linkedin="https://linkedin.com/in/ada"),
        skills=["Python", "Django"],
        preferences=Preferences(salary_minimum=50_000, salary_currency="USD"),
        eligibility=Eligibility(
            authorized_countries=["Nigeria", "USA"],
            requires_sponsorship=False,
            willing_to_relocate=True,
        ),
    )


def _with_resume(tmp_path: Path) -> tuple[CandidateProfile, AnswerContext]:
    pdf = tmp_path / "My CV.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake resume body")
    service = CandidateService()
    profile = service.attach_resume(_profile(), pdf)
    return profile, AnswerContext(
        profile=profile,
        resume_path=service.resume_file(profile),
        resume_status=service.resume_status(profile),
    )


async def _prepare(
    server: AppServer,
    page: str,
    profile: CandidateProfile,
    context: AnswerContext,
    provider: ScriptedProvider | None,
) -> tuple[AbstractAsyncContextManager[PlaywrightFormSession], ApplicationEngine, ApplicationDraft]:
    """Open ``page`` and prepare it; returns the live session pieces for further steps."""
    manager = open_form_session(f"{server.url}/{page}.html", allow_local=True, headless=True)
    session = await manager.__aenter__()
    try:
        engine, draft = await prepare_application(
            session, profile, context, provider=provider, job=make_job() if provider else None
        )
    except BaseException:
        await manager.__aexit__(None, None, None)
        raise
    return manager, engine, draft


def _status(draft: ApplicationDraft, name: str) -> AnswerStatus:
    field = next(f for f in draft.scan.fields if f.name == name)
    return draft.answers[field.id].status


async def test_a_simple_application_is_filled_and_submitted_with_the_right_data(
    server: AppServer, tmp_path: Path
) -> None:
    profile, context = _with_resume(tmp_path)
    provider = ScriptedProvider([WHY])
    manager, engine, draft = await _prepare(
        server, "simple_application", profile, context, provider
    )
    try:
        assert draft.blockers() == []
        assert _status(draft, "why") is AnswerStatus.GENERATED
        assert _status(draft, "first_name") is AnswerStatus.FILLED
        result = await engine.submit(draft, confirmed=True)
    finally:
        await manager.__aexit__(None, None, None)

    assert result.navigated and "Thank you for applying" in result.excerpt
    assert len(server.posts) == 1
    post = server.posts[0]
    assert post.path == "/submit"
    assert post.fields == {
        "first_name": ["Ada"],
        "last_name": ["Lovelace"],
        "email": ["ada@example.com"],
        "phone": ["+234 800 000 0000"],
        "city": ["Abuja"],
        "country": ["NG"],
        "linkedin": ["https://linkedin.com/in/ada"],
        "why": [WHY],
    }
    filename, content = post.files["resume"]
    assert filename.endswith(".pdf") and content == b"%PDF-1.4 fake resume body"


async def test_nothing_is_sent_without_confirmation_or_while_something_is_unresolved(
    server: AppServer, tmp_path: Path
) -> None:
    profile, context = _with_resume(tmp_path)
    # no AI: the required "why" question cannot be answered, so it blocks the submit
    manager, engine, draft = await _prepare(server, "simple_application", profile, context, None)
    try:
        why = next(f for f in draft.scan.fields if f.name == "why")
        assert [f.name for f, _ in draft.blockers()] == ["why"]
        assert draft.answers[why.id].status is AnswerStatus.NEEDS_USER

        with pytest.raises(ApplicationError, match="confirmation"):
            await engine.submit(draft, confirmed=False)
        with pytest.raises(ApplicationError, match="unresolved"):
            await engine.submit(draft, confirmed=True)
        assert server.posts == []  # nothing left the machine

        assert await engine.set_user_answer(draft, why.id, "My own words.") is None
        assert draft.blockers() == []
        await engine.submit(draft, confirmed=True)
    finally:
        await manager.__aexit__(None, None, None)
    assert server.posts[0].fields["why"] == ["My own words."]


async def test_sensitive_questions_are_answered_only_from_explicit_profile_answers(
    server: AppServer,
) -> None:
    profile = _profile()
    context = AnswerContext(profile=profile)
    manager, engine, draft = await _prepare(server, "sensitive_questions", profile, context, None)
    try:
        # explicit profile answers were used...
        assert _status(draft, "auth") is AnswerStatus.FILLED  # "United States" is in the profile
        assert _status(draft, "sponsor") is AnswerStatus.FILLED
        assert _status(draft, "relocate") is AnswerStatus.FILLED
        assert _status(draft, "salary") is AnswerStatus.FILLED
        # ...and everything that is only the user's to answer was left alone
        for name in ("gender", "felony", "clearance", "terms"):
            assert _status(draft, name) is AnswerStatus.NEEDS_USER, name

        # The unticked required consent blocks the submit, and so does the OPTIONAL consent the
        # page pre-ticked: left alone it would be sent without the user ever deciding.
        assert [f.name for f, _ in draft.blockers()] == ["terms", "marketing"]
        with pytest.raises(ApplicationError):
            await engine.submit(draft, confirmed=True)
        terms = next(f for f in draft.scan.fields if f.name == "terms")
        marketing = next(f for f in draft.scan.fields if f.name == "marketing")
        assert await engine.set_user_answer(draft, terms.id, "true") is None
        assert [f.name for f, _ in draft.blockers()] == ["marketing"]  # still undecided
        with pytest.raises(ApplicationError, match=r"job alerts"):
            await engine.submit(draft, confirmed=True)
        assert server.posts == []
        # the user decides to untick it
        assert await engine.set_user_answer(draft, marketing.id, "false") is None
        assert draft.blockers() == []
        await engine.submit(draft, confirmed=True)
    finally:
        await manager.__aexit__(None, None, None)

    sent = server.posts[0].fields
    assert sent["first_name"] == ["Ada"] and sent["email"] == ["ada@example.com"]
    assert sent["auth"] == ["yes"] and sent["sponsor"] == ["no"] and sent["relocate"] == ["yes"]
    assert sent["salary"] == ["50000 USD"]
    assert sent["terms"] == ["on"]  # ticked by the user
    assert "marketing" not in sent  # unticked by the user's decision, so not sent
    assert sent["gender"] == [""] and sent["clearance"] == [""]  # untouched selects
    assert "felony" not in sent  # no radio was chosen for the criminal-history question


async def test_a_user_may_knowingly_keep_a_pre_ticked_consent(server: AppServer) -> None:
    profile = _profile()
    manager, engine, draft = await _prepare(
        server, "sensitive_questions", profile, AnswerContext(profile=profile), None
    )
    try:
        for name in ("terms", "marketing"):
            field = next(f for f in draft.scan.fields if f.name == name)
            assert await engine.set_user_answer(draft, field.id, "true") is None
        assert draft.blockers() == []
        await engine.submit(draft, confirmed=True)
    finally:
        await manager.__aexit__(None, None, None)
    # sent because the user explicitly chose to keep it, not because the page pre-ticked it
    assert server.posts[0].fields["marketing"] == ["on"]


async def test_a_cross_origin_formaction_on_the_submit_button_is_the_destination_shown(
    server: AppServer,
) -> None:
    profile = _profile()
    manager, _engine, draft = await _prepare(
        server, "formaction_override", profile, AnswerContext(profile=profile), None
    )
    try:
        # the form's own action is same-site, but the button posts elsewhere: that is what counts
        assert draft.scan.form_action == "https://forms.third-party.example/collect"
        warning = draft.target_origin_warning()
        assert warning is not None and "forms.third-party.example" in warning
    finally:
        await manager.__aexit__(None, None, None)
    assert server.posts == []


async def test_a_destination_changed_after_review_is_refused_at_the_click(
    server: AppServer, tmp_path: Path
) -> None:
    profile, context = _with_resume(tmp_path)
    async with open_form_session(
        f"{server.url}/simple_application.html", allow_local=True, headless=True
    ) as session:
        engine = ApplicationEngine(session, profile, context)
        draft = await engine.scan()
        engine.plan_deterministic(draft)
        await engine.fill(draft)
        why = next(f for f in draft.scan.fields if f.name == "why")
        assert await engine.set_user_answer(draft, why.id, "Words.") is None
        assert draft.blockers() == []  # everything required is settled; this would submit
        # after the user "reviewed" it, the page quietly re-points the submit button
        await session._page.evaluate(
            "document.querySelector('[data-oa-submit]').setAttribute("
            "'formaction', 'https://forms.third-party.example/collect')"
        )
        with pytest.raises(BrowserError, match="destination changed"):
            await engine.submit(draft, confirmed=True)
    assert server.posts == []  # nothing left the machine


async def test_unknown_eligibility_leaves_the_question_unanswered(server: AppServer) -> None:
    profile = _profile().model_copy(
        update={"eligibility": Eligibility()}  # the user has told us nothing
    )
    manager, _engine, draft = await _prepare(
        server, "sensitive_questions", profile, AnswerContext(profile=profile), None
    )
    try:
        for name in ("auth", "sponsor", "relocate"):
            assert _status(draft, name) is AnswerStatus.NEEDS_USER, name
    finally:
        await manager.__aexit__(None, None, None)


async def test_user_edits_are_validated_and_written(server: AppServer, tmp_path: Path) -> None:
    profile, context = _with_resume(tmp_path)
    manager, engine, draft = await _prepare(server, "simple_application", profile, context, None)
    try:
        field = {f.name: f for f in draft.scan.fields}
        too_long = await engine.set_user_answer(draft, field["why"].id, "x" * 501)
        assert too_long is not None and "too long" in too_long
        assert await engine.set_user_answer(draft, field["country"].id, "XX") is not None
        assert (
            await engine.set_user_answer(draft, field["first_name"].id, "") is not None
        )  # required
        bad_type = tmp_path / "notes.txt"
        bad_type.write_text("x")
        error = await engine.set_user_answer(draft, field["resume"].id, str(bad_type))
        assert error is not None and ".pdf and .docx" in error
        missing = await engine.set_user_answer(draft, field["resume"].id, str(tmp_path / "no.pdf"))
        assert missing is not None and "does not exist" in missing

        # rejected edits changed nothing
        assert draft.answers[field["first_name"].id].value == "Ada"
        # a valid one does
        assert await engine.set_user_answer(draft, field["city"].id, "Lagos") is None
        assert draft.answers[field["city"].id].status is AnswerStatus.USER
        assert await engine.set_user_answer(draft, field["why"].id, "Fine.") is None
        await engine.submit(draft, confirmed=True)
    finally:
        await manager.__aexit__(None, None, None)
    assert server.posts[0].fields["city"] == ["Lagos"]


async def test_fields_the_scanner_never_reported_cannot_be_edited(server: AppServer) -> None:
    profile = _profile()
    manager, engine, draft = await _prepare(
        server, "simple_application", profile, AnswerContext(profile=profile), None
    )
    try:
        with pytest.raises(KeyError):
            await engine.set_user_answer(draft, "oa-999", "x")
    finally:
        await manager.__aexit__(None, None, None)


async def test_the_engine_never_touches_honeypots_or_logins(server: AppServer) -> None:
    profile = _profile()
    async with open_form_session(
        f"{server.url}/traps.html", allow_local=True, headless=True
    ) as session:
        engine = ApplicationEngine(session, profile, AnswerContext(profile=profile))
        draft = await engine.scan()
        engine.plan_deterministic(draft)
        await engine.fill(draft)
        assert {f.name for f in draft.scan.fields} == {"first_name", "email", "about"}
        assert draft.target_origin_warning() is not None  # posts to a different site
    assert server.posts == []


@pytest.mark.parametrize("page", ["late_formaction", "late_form_action"])
async def test_a_destination_changed_after_the_click_is_blocked_on_the_wire(
    server: AppServer, page: str
) -> None:
    """Page scripts run between the click and the browser's own post; a check made before the
    click cannot see them. The destination is enforced where the data leaves."""
    profile = _profile()
    manager, engine, draft = await _prepare(
        server, page, profile, AnswerContext(profile=profile), None
    )
    try:
        # at scan time the form looks entirely same-site: the user is shown (and confirms) that
        assert draft.target_origin_warning() is None
        assert draft.blockers() == []
        with pytest.raises(BrowserError, match=r"forms\.third-party\.example"):
            await engine.submit(draft, confirmed=True)
    finally:
        await manager.__aexit__(None, None, None)
    assert server.posts == []  # and the data never left the machine


async def test_other_sites_are_blocked_during_submit_but_the_application_still_goes(
    server: AppServer,
) -> None:
    profile = _profile()
    manager, engine, draft = await _prepare(
        server, "beacon_on_submit", profile, AnswerContext(profile=profile), None
    )
    try:
        result = await engine.submit(draft, confirmed=True)
    finally:
        await manager.__aexit__(None, None, None)
    assert result.blocked_hosts == ("analytics.third-party.example",)
    assert len(server.posts) == 1  # the application itself reached the confirmed site
    assert server.posts[0].fields["first_name"] == ["Ada"]
