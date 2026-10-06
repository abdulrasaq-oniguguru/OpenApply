"""`openapply apply` through the CLI: control flow with a scripted page, plus real Chrome."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openapply.browser.page import BrowserError, BrowserNotInstalled
from openapply.candidate.models import CandidateProfile, Eligibility, Identity, Links, Preferences
from openapply.candidate.service import CandidateService
from openapply.candidate.storage import ProfileStorage
from openapply.cli.app import app
from openapply.jobs.models import JobPosting
from tests.applications.conftest import AppServer
from tests.applications.fakes import FakeSession, raw, scan_of
from tests.jobs.builders import make_job
from tests.providers.fakes import ScriptedProvider

runner = CliRunner()
URL = "https://careers.example.com/apply"
WHY = "I build payment APIs in Python, which is the work this role describes."


def _save_profile(*, resume: Path | None = None, **overrides: object) -> CandidateProfile:
    profile = CandidateProfile(
        identity=Identity(
            full_name="Ada Lovelace",
            email="ada@example.com",
            phone="+234 800 000 0000",
            city="Abuja",
            country="Nigeria",
        ),
        links=Links(linkedin="https://linkedin.com/in/ada"),
        skills=["Python"],
        preferences=Preferences(salary_minimum=50_000, salary_currency="USD"),
        eligibility=Eligibility(
            authorized_countries=["Nigeria", "USA"],
            requires_sponsorship=False,
            willing_to_relocate=True,
        ),
    ).model_copy(update=overrides)
    if resume is not None:
        profile = CandidateService().attach_resume(profile, resume)
    ProfileStorage().save(profile)
    return profile


def _page() -> FakeSession:
    return FakeSession(
        scan_of(
            raw("oa-0", "text", "First name *", required=True),
            raw("oa-1", "textarea", "Why do you want this job? *", required=True),
            raw("oa-2", "select", "Gender", options=[("Select", ""), ("Male", "m")]),
        )
    )


@pytest.fixture
def scripted_page(monkeypatch: pytest.MonkeyPatch) -> FakeSession:
    session = _page()

    @contextlib.asynccontextmanager
    async def fake_open(url: str, **kwargs: object) -> AsyncIterator[FakeSession]:
        yield session

    monkeypatch.setattr("openapply.cli.commands.apply._open_session", fake_open)
    return session


# --- before anything opens ----------------------------------------------------------------


def test_apply_needs_a_profile(scripted_page: FakeSession) -> None:
    result = runner.invoke(app, ["apply", URL, "--no-ai"])
    assert result.exit_code == 1
    assert "openapply setup" in result.output
    assert scripted_page.calls == []


def test_apply_refuses_an_incomplete_profile(scripted_page: FakeSession) -> None:
    ProfileStorage().save(CandidateProfile())
    result = runner.invoke(app, ["apply", URL, "--no-ai"])
    assert result.exit_code == 1 and "incomplete" in result.output


def test_apply_reports_an_unreadable_profile_cleanly(isolated_home: Path) -> None:
    isolated_home.mkdir(parents=True)
    (isolated_home / "profile.json").write_text("{broken", encoding="utf-8")
    result = runner.invoke(app, ["apply", URL, "--no-ai"])
    assert result.exit_code == 2 and "Traceback" not in result.output


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "http://localhost:9/x", "https://u:p@x.example/"]
)
def test_apply_rejects_unsafe_urls_before_opening_a_browser(
    scripted_page: FakeSession, url: str
) -> None:
    _save_profile()
    result = runner.invoke(app, ["apply", url, "--no-ai"])
    assert result.exit_code == 2 and scripted_page.calls == []


# --- preview-only never submits ------------------------------------------------------------


def test_preview_only_shows_the_plan_and_never_submits(scripted_page: FakeSession) -> None:
    _save_profile()
    result = runner.invoke(app, ["apply", URL, "--no-ai", "--preview-only"])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "First name" in out and "Ada" in out and "profile: name" in out
    assert "Needs your confirmation" in out and "Gender" in out
    assert "block submitting" in out and "Why do you want this job?" in out
    assert "Preview only: nothing was sent" in out
    assert scripted_page.submitted == 0
    assert ("text", "oa-0", "Ada") in scripted_page.calls


def test_preview_only_with_everything_settled_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession(scan_of(raw("oa-0", "text", "First name", required=True)))

    @contextlib.asynccontextmanager
    async def fake_open(url: str, **kwargs: object) -> AsyncIterator[FakeSession]:
        yield session

    monkeypatch.setattr("openapply.cli.commands.apply._open_session", fake_open)
    _save_profile()
    result = runner.invoke(app, ["apply", URL, "--no-ai", "--preview-only"])
    assert result.exit_code == 0 and "Nothing required is missing" in result.output


# --- interactive flow -------------------------------------------------------------------------


def test_quitting_sends_nothing(scripted_page: FakeSession) -> None:
    _save_profile()
    result = runner.invoke(app, ["apply", URL, "--no-ai", "--headless"], input="q\n")
    assert result.exit_code == 0 and scripted_page.submitted == 0
    assert "Nothing was sent" in result.output


def test_end_of_input_sends_nothing(scripted_page: FakeSession) -> None:
    _save_profile()
    result = runner.invoke(app, ["apply", URL, "--no-ai", "--headless"], input="")
    assert result.exit_code == 0 and scripted_page.submitted == 0


def test_submit_is_blocked_until_the_user_answers_the_required_question(
    scripted_page: FakeSession,
) -> None:
    _save_profile()
    result = runner.invoke(
        app,
        ["apply", URL, "--no-ai", "--headless"],
        input="s\ne\n2\nIn my own words.\n\ns\nsubmit\n",
    )
    assert result.exit_code == 0, result.output
    out = result.output
    assert (
        out.index("Cannot submit yet")
        < out.index("Updated.")
        < out.index("The submit button was clicked")
    )
    assert ("text", "oa-1", "In my own words.") in scripted_page.calls
    assert scripted_page.submitted == 1


def test_a_wrong_confirmation_sends_nothing(scripted_page: FakeSession) -> None:
    _save_profile()
    result = runner.invoke(
        app,
        ["apply", URL, "--no-ai", "--headless"],
        input="e\n2\nWords.\n\ns\nyes\nq\n",
    )
    assert result.exit_code == 0 and scripted_page.submitted == 0
    assert "Cancelled. Nothing was sent." in result.output


# --- the AI ---------------------------------------------------------------------------------------


def _with_ai(monkeypatch: pytest.MonkeyPatch, replies: list[str | Exception]) -> ScriptedProvider:
    provider = ScriptedProvider(replies)
    monkeypatch.setattr("openapply.cli.commands.apply._build_provider", lambda *a, **k: provider)
    job: JobPosting = make_job()
    monkeypatch.setattr("openapply.cli.commands.apply._read_job", lambda *a, **k: job)
    return provider


def test_written_answers_are_generated_and_shown_for_review(
    scripted_page: FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save_profile()
    provider = _with_ai(monkeypatch, [WHY])
    result = runner.invoke(app, ["apply", URL, "--preview-only"])
    assert result.exit_code == 0, result.output
    assert "Generated responses" in result.output and "read them first" in result.output
    assert "Backend Engineer" in result.output  # the job title heads the preview
    assert ("text", "oa-1", WHY) in scripted_page.calls
    assert len(provider.prompts) == 1


def test_max_answers_zero_leaves_written_questions_to_the_user(
    scripted_page: FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save_profile()
    provider = _with_ai(monkeypatch, [WHY])
    result = runner.invoke(app, ["apply", URL, "--preview-only", "--max-answers", "0"])
    assert result.exit_code == 0 and provider.prompts == []
    assert "answer this one yourself" in result.output


def test_no_provider_selected_is_a_warning_not_a_failure(scripted_page: FakeSession) -> None:
    _save_profile()  # settings have no default provider
    result = runner.invoke(app, ["apply", URL, "--preview-only"])
    assert result.exit_code == 0, result.output
    assert "No AI provider selected" in result.output and "left to you" in result.output
    assert scripted_page.submitted == 0


def test_an_explicitly_requested_bad_provider_is_an_error(scripted_page: FakeSession) -> None:
    _save_profile()
    result = runner.invoke(app, ["apply", URL, "--provider", "bogus", "--preview-only"])
    assert result.exit_code == 2 and scripted_page.calls == []


# --- browser failures ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "needle"),
    [
        (
            BrowserNotInstalled("No usable browser found. Run `openapply browser install`."),
            "browser install",
        ),
        (BrowserError("Timed out loading x"), "Timed out"),
    ],
)
def test_browser_failures_are_friendly(
    monkeypatch: pytest.MonkeyPatch, error: Exception, needle: str
) -> None:
    @contextlib.asynccontextmanager
    async def failing_open(url: str, **kwargs: object) -> AsyncIterator[FakeSession]:
        raise error
        yield  # pragma: no cover

    monkeypatch.setattr("openapply.cli.commands.apply._open_session", failing_open)
    _save_profile()
    result = runner.invoke(app, ["apply", URL, "--no-ai"])
    assert result.exit_code == 1 and needle in result.output and "Traceback" not in result.output


def test_hostile_page_text_cannot_break_the_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    hostile = "[/oops] [bold red]PWNED[/] \x1b[2J"
    session = FakeSession(scan_of(raw("oa-0", "text", hostile, required=True)))

    @contextlib.asynccontextmanager
    async def fake_open(url: str, **kwargs: object) -> AsyncIterator[FakeSession]:
        yield session

    monkeypatch.setattr("openapply.cli.commands.apply._open_session", fake_open)
    _save_profile()
    result = runner.invoke(app, ["apply", URL, "--no-ai", "--preview-only"])
    assert result.exit_code == 0, result.output
    assert "\x1b" not in result.output and "PWNED" in result.output


# --- real Chrome, end to end ----------------------------------------------------------------------


def _profile_with_resume(tmp_path: Path) -> CandidateProfile:
    pdf = tmp_path / "My CV.pdf"
    pdf.write_bytes(b"%PDF-1.4 resume bytes")
    return _save_profile(resume=pdf)


def test_real_browser_full_application_through_the_cli(server: AppServer, tmp_path: Path) -> None:
    _profile_with_resume(tmp_path)
    url = f"{server.url}/simple_application.html"
    result = runner.invoke(
        app,
        ["apply", url, "--allow-local", "--headless", "--no-ai"],
        # edit field 9 (the written question), then submit with the typed confirmation
        input="e\n9\nI like building payment APIs.\n\ns\nsubmit\n",
    )
    assert result.exit_code == 0, result.output
    assert "The submit button was clicked" in result.output
    assert len(server.posts) == 1
    post = server.posts[0]
    assert post.fields["first_name"] == ["Ada"] and post.fields["country"] == ["NG"]
    assert post.fields["why"] == ["I like building payment APIs."]
    assert post.files["resume"][1] == b"%PDF-1.4 resume bytes"


def test_real_browser_preview_of_a_cross_site_form_warns_and_sends_nothing(
    server: AppServer, tmp_path: Path
) -> None:
    _save_profile()
    url = f"{server.url}/traps.html"
    result = runner.invoke(
        app, ["apply", url, "--allow-local", "--headless", "--no-ai", "--preview-only"]
    )
    assert result.exit_code == 0, result.output
    assert "different site" in result.output and "forms.third-party.example" in result.output
    assert "password field" in result.output
    assert server.posts == []


def test_real_browser_sensitive_form_is_not_answered_for_the_user(
    server: AppServer, tmp_path: Path
) -> None:
    _save_profile()
    url = f"{server.url}/sensitive_questions.html"
    result = runner.invoke(
        app, ["apply", url, "--allow-local", "--headless", "--no-ai", "--preview-only"]
    )
    assert result.exit_code == 0, result.output
    out = result.output
    for question in (
        "Gender",
        "convicted of a felony",
        "security clearance",
        "Terms and Conditions",
    ):
        assert question in out
    assert "Needs your confirmation" in out and "never filled in for you" in out
    assert "block submitting" in out  # the required terms checkbox
    assert server.posts == []
