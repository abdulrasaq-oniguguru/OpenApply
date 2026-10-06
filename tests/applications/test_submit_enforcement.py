"""The approved-submit window, against hostile servers and pages, in a real browser."""

from __future__ import annotations

from pathlib import Path

import pytest

from openapply.applications.answers import AnswerContext
from openapply.applications.engine import ApplicationDraft, ApplicationEngine
from openapply.applications.service import prepare_application
from openapply.browser.forms import PlaywrightFormSession, open_form_session
from openapply.browser.page import BrowserError
from openapply.candidate.models import CandidateProfile, Eligibility, Identity
from openapply.candidate.service import CandidateService
from tests.applications.conftest import AppServer

RESUME_BYTES = b"%PDF-1.4 binary \r\n--not-a-boundary\r\n\x00\x01 resume body"


def _profile_with_resume(tmp_path: Path) -> tuple[CandidateProfile, AnswerContext]:
    pdf = tmp_path / "My CV.pdf"
    pdf.write_bytes(RESUME_BYTES)
    service = CandidateService()
    profile = service.attach_resume(
        CandidateProfile(
            identity=Identity(full_name="Ada Lovelace", email="ada@example.com"),
            eligibility=Eligibility(),
        ),
        pdf,
    )
    return profile, AnswerContext(
        profile=profile,
        resume_path=service.resume_file(profile),
        resume_status=service.resume_status(profile),
    )


async def _ready(
    session: PlaywrightFormSession, profile: CandidateProfile, context: AnswerContext
) -> tuple[ApplicationEngine, ApplicationDraft]:
    engine, draft = await prepare_application(session, profile, context, provider=None, job=None)
    assert draft.blockers() == [], draft.blockers()
    return engine, draft


def _host(app: AppServer) -> str:
    return app.url.removeprefix("http://")


# --- finding 1: a confirmed site must not be able to forward the application ----------------------


@pytest.mark.parametrize("page", ["redirect_307", "redirect_308"])
async def test_a_method_preserving_redirect_to_another_site_is_blocked(
    server: AppServer, other_server: AppServer, tmp_path: Path, page: str
) -> None:
    profile, context = _profile_with_resume(tmp_path)
    async with open_form_session(
        f"{server.url}/{page}.html", allow_local=True, headless=True
    ) as session:
        engine, draft = await _ready(session, profile, context)
        # the user is shown, and confirms, a same-site destination
        assert draft.target_origin_warning() is None
        with pytest.raises(BrowserError, match=_host(other_server).replace(".", r"\.")):
            await engine.submit(draft, confirmed=True)
    # the unconfirmed site never saw a single request, let alone the application and résumé
    assert other_server.posts == []


async def test_a_same_site_307_is_followed_and_the_file_survives_both_hops(
    server: AppServer, other_server: AppServer, tmp_path: Path
) -> None:
    profile, context = _profile_with_resume(tmp_path)
    async with open_form_session(
        f"{server.url}/redirect_same_site.html", allow_local=True, headless=True
    ) as session:
        engine, draft = await _ready(session, profile, context)
        result = await engine.submit(draft, confirmed=True)
    assert "Thank you for applying" in result.excerpt
    assert [p.path for p in server.posts] == ["/redirect307_same", "/submit"]
    final = server.posts[-1]  # the re-sent request, after the redirect
    assert final.fields["first_name"] == ["Ada"] and final.fields["email"] == ["ada@example.com"]
    assert final.files["resume"][1] == RESUME_BYTES  # binary content intact, byte for byte
    assert other_server.posts == []


# --- only the form's own submission may count as the application going out ----------------------


async def test_a_same_site_decoy_cannot_make_a_blocked_application_look_sent(
    server: AppServer,
) -> None:
    profile = CandidateProfile(
        identity=Identity(full_name="Ada Lovelace", email="ada@example.com"),
    )
    async with open_form_session(
        f"{server.url}/decoy_beacon.html", allow_local=True, headless=True
    ) as session:
        engine, draft = await _ready(session, profile, AnswerContext(profile=profile))
        with pytest.raises(BrowserError, match=r"forms\.third-party\.example") as caught:
            await engine.submit(draft, confirmed=True)
    # the decoy (carrying the entered values) did reach the confirmed site, so the message must
    # not claim that nothing was sent, and must not claim success either
    assert any(p.path == "/beacon" for p in server.posts)
    assert not any(p.path == "/submit" for p in server.posts)  # the real application never went
    assert "cannot confirm the application itself did" in str(caught.value)


# --- files the tool cannot vouch for -------------------------------------------------------------


async def test_a_file_the_user_attached_by_hand_is_refused_rather_than_sent_empty(
    server: AppServer, tmp_path: Path
) -> None:
    profile, context = _profile_with_resume(tmp_path)
    async with open_form_session(
        f"{server.url}/redirect_same_site.html", allow_local=True, headless=True
    ) as session:
        engine, draft = await _ready(session, profile, context)
        resume = next(f for f in draft.scan.fields if f.name == "resume")
        # swap in a different file behind the engine's back, as a person could in the window
        other = tmp_path / "Hand Attached.pdf"
        other.write_bytes(b"%PDF-1.4 something OpenApply never saw")
        await session._page.locator(f'[data-oa-id="{resume.id}"]').set_input_files(str(other))
        with pytest.raises(BrowserError, match="did not attach"):
            await engine.submit(draft, confirmed=True)
    assert server.posts == []  # refused before anything was sent
