"""The human-review loop, driven by a scripted person and a scripted page."""

from __future__ import annotations

import io

from rich.console import Console

from openapply.applications.answers import AnswerContext
from openapply.applications.engine import ApplicationDraft, ApplicationEngine
from openapply.applications.models import AnswerStatus
from openapply.applications.service import prepare_application
from openapply.cli.review import InputClosed, Outcome, review_loop, submit_token
from tests.applications.fakes import FakeSession, raw, scan_of
from tests.jobs.builders import make_job, make_profile
from tests.providers.fakes import ScriptedProvider

YES_NO = [("Yes", "yes"), ("No", "no")]


class Person:
    """Answers prompts from a script; running out means the input stream closed."""

    def __init__(self, *replies: str) -> None:
        self._replies = list(replies)
        self.prompts: list[str] = []

    async def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self._replies:
            raise InputClosed
        return self._replies.pop(0)


def _console() -> tuple[Console, io.StringIO]:
    buffer = io.StringIO()
    return Console(file=buffer, force_terminal=False, width=120, color_system=None), buffer


async def _start(
    session: FakeSession, provider: ScriptedProvider | None = None
) -> tuple[ApplicationEngine, ApplicationDraft]:
    profile = make_profile()
    return await prepare_application(
        session,
        profile,
        AnswerContext(profile=profile),
        provider=provider,
        job=make_job() if provider else None,
    )


def _simple_page(**kwargs: object) -> FakeSession:
    return FakeSession(
        scan_of(
            raw("oa-0", "text", "First name *", required=True),
            raw("oa-1", "text", "Why do you want this job? *", required=True),
            raw(
                "oa-2", "select", "Gender", options=[("Select", ""), ("Male", "m"), ("Female", "f")]
            ),
            raw("oa-3", "checkbox", "I agree to the Terms and Conditions *", required=True),
            **kwargs,  # type: ignore[arg-type]
        )
    )


async def _run(
    session: FakeSession, person: Person, *, headless: bool = False
) -> tuple[Outcome, str]:
    engine, draft = await _start(session)
    console, buffer = _console()
    outcome = await review_loop(
        console, person, engine, draft, session, make_job(), headless=headless
    )
    return outcome, buffer.getvalue()


# --- quitting never sends ---------------------------------------------------------------


async def test_quit_sends_nothing() -> None:
    session = _simple_page()
    outcome, out = await _run(session, Person("q"))
    assert outcome is Outcome.QUIT and session.submitted == 0
    assert "Nothing was sent" in out


async def test_a_closed_input_stream_quits_and_never_submits() -> None:
    session = _simple_page()
    outcome, _ = await _run(session, Person())  # EOF straight away
    assert outcome is Outcome.QUIT and session.submitted == 0


async def test_input_closing_at_the_confirmation_prompt_never_submits() -> None:
    session = FakeSession(scan_of(raw("oa-0", "text", "First name", required=True)))
    outcome, out = await _run(session, Person("s"))  # asked to type 'submit', then EOF
    assert outcome is Outcome.QUIT and session.submitted == 0
    assert "Nothing was sent" in out


async def test_unknown_menu_choices_are_refused() -> None:
    session = _simple_page()
    outcome, out = await _run(session, Person("x", "", "q"))
    assert outcome is Outcome.QUIT and "Choose R, E, S or Q" in out


# --- review ---------------------------------------------------------------------------------


async def test_review_brings_the_browser_forward_and_waits_for_the_person() -> None:
    session = _simple_page()
    person = Person("r", "", "q")
    outcome, out = await _run(session, person)
    assert outcome is Outcome.QUIT
    assert session.brought_to_front == 1
    assert "Press Enter when you are done reviewing" in person.prompts
    assert "Nothing has been sent" in out


async def test_review_is_unavailable_when_the_browser_is_hidden() -> None:
    session = _simple_page()
    outcome, out = await _run(session, Person("r", "q"), headless=True)
    assert outcome is Outcome.QUIT and session.brought_to_front == 0
    assert "not available with --headless" in out


# --- submitting -----------------------------------------------------------------------------


async def test_submit_is_refused_while_something_required_is_unresolved() -> None:
    session = _simple_page()
    outcome, out = await _run(session, Person("s", "q"))
    assert outcome is Outcome.QUIT and session.submitted == 0
    assert "Cannot submit yet" in out
    assert "Why do you want this job?" in out and "Terms and Conditions" in out


async def test_a_wrong_confirmation_cancels() -> None:
    session = FakeSession(scan_of(raw("oa-0", "text", "First name", required=True)))
    outcome, out = await _run(session, Person("s", "yes please", "q"))
    assert outcome is Outcome.QUIT and session.submitted == 0
    assert "Cancelled. Nothing was sent." in out


async def test_typing_the_confirmation_submits_exactly_once() -> None:
    session = FakeSession(scan_of(raw("oa-0", "text", "First name", required=True)))
    person = Person("s", "submit")
    outcome, out = await _run(session, person)
    assert outcome is Outcome.SUBMITTED and session.submitted == 1
    assert "Type 'submit'" in person.prompts[-1]
    assert "cannot verify that the employer received it" in out
    assert "https://careers.example.com/thanks" in out


async def test_the_confirmation_is_case_sensitive_and_exact() -> None:
    session = FakeSession(scan_of(raw("oa-0", "text", "First name", required=True)))
    outcome, _ = await _run(session, Person("s", "SUBMIT", "s", " submit x", "q"))
    assert outcome is Outcome.QUIT and session.submitted == 0


async def test_a_form_that_posts_to_another_site_needs_that_sites_name() -> None:
    session = FakeSession(
        scan_of(
            raw("oa-0", "text", "First name", required=True),
            action="https://forms.other.example/collect?token=abc",
        )
    )
    engine, draft = await _start(session)
    assert submit_token(draft) == "forms.other.example"

    console, buffer = _console()
    person = Person("s", "submit", "s", "forms.other.example")
    outcome = await review_loop(console, person, engine, draft, session, make_job(), headless=True)
    out = buffer.getvalue()
    assert outcome is Outcome.SUBMITTED and session.submitted == 1
    assert "different site" in out and "forms.other.example" in out
    assert "token=abc" not in out  # query strings are not echoed to the screen


async def test_the_same_site_form_needs_only_submit() -> None:
    session = FakeSession(
        scan_of(raw("oa-0", "text", "First name"), action="https://careers.example.com/apply")
    )
    _engine, draft = await _start(session)
    assert submit_token(draft) == "submit"


async def test_the_confirmation_mentions_written_answers() -> None:
    session = FakeSession(scan_of(raw("oa-0", "textarea", "Why do you want this job?")))
    provider = ScriptedProvider(["I build payment APIs, which is what this team does."])
    engine, draft = await _start(session, provider)
    console, buffer = _console()
    outcome = await review_loop(
        console, Person("s", "submit"), engine, draft, session, make_job(), headless=True
    )
    assert outcome is Outcome.SUBMITTED
    assert "1 answer(s) were written by the AI" in buffer.getvalue()


# --- editing --------------------------------------------------------------------------------


async def test_editing_resolves_blockers_and_then_the_application_can_be_sent() -> None:
    session = _simple_page()
    person = Person(
        "e",
        "2", "I build payment APIs and want to do it here.",  # the free-text question
        "4", "yes",  # tick the consent (the user's own decision)
        "",  # back to the menu
        "s", "submit",
    )  # fmt: skip
    outcome, out = await _run(session, person)
    assert outcome is Outcome.SUBMITTED and session.submitted == 1
    assert ("text", "oa-1", "I build payment APIs and want to do it here.") in session.calls
    assert ("check", "oa-3", True) in session.calls
    assert out.count("Updated.") == 2
    # the sensitive select the user never touched stayed untouched
    assert not any(call[1] == "oa-2" for call in session.calls)


async def test_edit_choices_for_selects_use_numbers_and_reject_bad_ones() -> None:
    session = _simple_page()
    person = Person("e", "3", "9", "3", "2", "", "q")  # bad number, then option 2 ("Male")
    outcome, out = await _run(session, person)
    assert outcome is Outcome.QUIT
    assert "That is not one of the numbers above" in out
    assert ("select", "oa-2", "m") in session.calls


async def test_edit_input_is_validated_and_a_blank_keeps_the_current_value() -> None:
    session = FakeSession(scan_of(raw("oa-0", "text", "City", max_length=5)))
    person = Person("e", "1", "Johannesburg", "1", "", "1", "Lagos", "", "q")
    outcome, out = await _run(session, person)
    assert outcome is Outcome.QUIT
    assert "Not changed" in out and "too long" in out  # 12 characters, limit 5
    assert ("text", "oa-0", "Lagos") in session.calls
    assert ("text", "oa-0", "Johannesburg") not in session.calls


async def test_edit_checkbox_groups_and_files() -> None:
    session = FakeSession(
        scan_of(
            raw("oa-0", "checkbox", "Which apply?", options=[("A", "a"), ("B", "b")]),
            raw("oa-1", "file", "Portfolio samples"),
        )
    )
    person = Person("e", "1", "2", "2", "/no/such/file.pdf", "", "q")
    outcome, out = await _run(session, person)
    assert outcome is Outcome.QUIT
    assert ("check", "oa-0-o0", False) in session.calls and (
        "check",
        "oa-0-o1",
        True,
    ) in session.calls
    assert "does not exist" in out


async def test_hostile_field_labels_cannot_break_the_menus() -> None:
    hostile = "[/oops] [bold red]PWNED[/] \x1b[2J"
    session = FakeSession(scan_of(raw("oa-0", "text", hostile)))
    person = Person("e", "1", "", "", "q")
    outcome, out = await _run(session, person)
    assert outcome is Outcome.QUIT
    assert "[/oops]" in out and "\x1b" not in out


async def test_nothing_is_marked_sent_by_editing() -> None:
    session = _simple_page()
    engine, draft = await _start(session)
    assert await engine.set_user_answer(draft, "oa-1", "My words.") is None
    assert draft.answers["oa-1"].status is AnswerStatus.USER
    assert session.submitted == 0


async def test_the_preview_says_what_the_page_tried_to_contact_while_details_were_filled_in() -> (
    None
):
    session = _simple_page()
    session.blocked = ["evil.example", "tracker.example:8443"]
    outcome, out = await _run(session, Person("q"))
    assert outcome is Outcome.QUIT
    assert "blocked from contacting other sites" in out
    assert "evil.example" in out and "tracker.example:8443" in out


async def test_the_preview_is_quiet_when_the_page_behaved() -> None:
    session = _simple_page()
    _outcome, out = await _run(session, Person("q"))
    assert "The page tried to contact" not in out
    assert "blocked from contacting other sites" in out  # the standing notice is always shown
