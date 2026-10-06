"""While a person's details are filled into a page, that page may not phone home.

Page scripts can read what has been filled in and send it anywhere with a plain GET (an image
beacon, a ``fetch``, a script tag), long before the user has approved anything.
"""

from __future__ import annotations

import pytest

from openapply.applications.answers import AnswerContext
from openapply.applications.engine import ApplicationEngine
from openapply.applications.service import prepare_application
from openapply.browser.forms import open_form_session
from openapply.browser.page import BrowserError
from openapply.browser.policy import RequestPolicy, is_captcha_url
from openapply.candidate.models import CandidateProfile, Identity
from tests.applications.conftest import AppServer


def _profile() -> CandidateProfile:
    return CandidateProfile(identity=Identity(full_name="Ada Lovelace", email="ada@example.com"))


# --- the attack, in a real browser ---------------------------------------------------------------


async def test_a_page_cannot_leak_filled_in_values_to_another_site(
    server: AppServer, other_server: AppServer
) -> None:
    profile = _profile()
    url = f"{server.url}/exfil_get.html#{other_server.port}"
    async with open_form_session(url, allow_local=True, headless=True) as session:
        _engine, draft = await prepare_application(
            session, profile, AnswerContext(profile=profile), provider=None, job=None
        )
        assert draft.blockers() == []  # the form was filled in with the profile's details
        await session._page.wait_for_timeout(3000)  # outlast the timer and input events
        blocked = await session.blocked_hosts()
    # none of the four channels (image, fetch, beacon, script) reached the other site...
    assert other_server.gets == [] and other_server.posts == []
    # ...and the page's attempts are visible to the user
    assert f"127.0.0.1:{other_server.port}" in blocked


async def test_a_form_switched_to_a_get_aimed_elsewhere_is_blocked_at_the_click(
    server: AppServer, other_server: AppServer
) -> None:
    profile = _profile()
    url = f"{server.url}/get_method_switch.html#{other_server.port}"
    async with open_form_session(url, allow_local=True, headless=True) as session:
        engine = ApplicationEngine(session, profile, AnswerContext(profile=profile))
        draft = await engine.scan()
        engine.plan_deterministic(draft)
        await engine.fill(draft)
        # at scan time this is an ordinary same-site POST, so the user confirms that
        assert draft.target_origin_warning() is None and draft.blockers() == []
        with pytest.raises(BrowserError, match=f"127.0.0.1:{other_server.port}"):
            await engine.submit(draft, confirmed=True)
    assert other_server.gets == [] and other_server.posts == []


async def test_the_forms_declared_site_is_not_trusted_before_the_user_confirms_it(
    server: AppServer,
) -> None:
    """traps.html declares a form action on another site: the page chose that, not the user."""
    profile = _profile()
    async with open_form_session(
        f"{server.url}/traps.html", allow_local=True, headless=True
    ) as session:
        engine = ApplicationEngine(session, profile, AnswerContext(profile=profile))
        draft = await engine.scan()
        action = draft.scan.form_action
        assert action is not None and draft.target_origin_warning() is not None
        # the user has not typed that site's name, so the page may not contact it
        assert session._policy.quarantine_allows(action) is False
        assert session._policy.quarantine_allows(f"{server.url}/anything") is True


async def test_ordinary_same_site_pages_still_work_while_quarantined(server: AppServer) -> None:
    profile = _profile()
    async with open_form_session(
        f"{server.url}/simple_application.html", allow_local=True, headless=True
    ) as session:
        engine, draft = await prepare_application(
            session, profile, AnswerContext(profile=profile), provider=None, job=None
        )
        assert await session.blocked_hosts() == []  # nothing needed another site
        assert any(a.applied for a in draft.answers.values())
        assert engine is not None


# --- the policy rule itself ----------------------------------------------------------------------


def _policy() -> RequestPolicy:
    return RequestPolicy(allow_local=True, block_types=frozenset(), timeout_ms=1000)


def test_nothing_is_restricted_before_the_form_has_been_read() -> None:
    policy = _policy()
    assert policy.quarantine_allows("https://cdn.example.net/app.js")
    assert policy.quarantine_allows("https://anything.example/x")


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("https://careers.example.com/apply", True),  # the page's own site
        ("https://careers.example.com/api/validate?x=1", True),
        ("wss://careers.example.com/live", True),  # its own WebSocket
        ("https://careers.example.com:8443/x", False),  # a different port is a different origin
        ("http://careers.example.com/x", False),  # a different scheme too
        ("https://evil.example/collect?email=ada%40example.com", False),
        ("https://forms.third-party.example/collect", False),  # the form's declared action
        ("https://cdn.example.net/app.js", False),
        ("data:image/png;base64,AAAA", True),  # never leaves the machine
        ("blob:https://careers.example.com/abc", True),
        ("about:blank", True),
        ("https://www.google.com/recaptcha/api2/anchor", True),  # CAPTCHAs are needed
        ("https://www.gstatic.com/recaptcha/releases/x.js", True),
        ("https://newassets.hcaptcha.com/captcha/v1/x.js", True),
        ("https://challenges.cloudflare.com/turnstile/v0/api.js", True),
        ("https://www.google.com/search?q=ada%40example.com", False),  # but not all of google
        ("https://www.google.com/recaptcha-evil/x", False),
        ("https://evil.example/recaptcha/api.js", False),
        ("https://hcaptcha.com.evil.example/x", False),
    ],
)
def test_quarantine_allows_only_the_pages_own_site_and_captcha_providers(
    url: str, allowed: bool
) -> None:
    policy = _policy()
    policy.quarantine(["https://careers.example.com/apply"])
    assert policy.quarantine_allows(url) is allowed


def test_a_redirected_page_may_talk_to_both_the_requested_and_the_final_site() -> None:
    policy = _policy()
    policy.quarantine(["https://short.example/go", "https://boards.example.com/jobs/1"])
    assert policy.quarantine_allows("https://short.example/x")
    assert policy.quarantine_allows("https://boards.example.com/api")
    assert not policy.quarantine_allows("https://third.example/x")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.google.com/recaptcha/api.js", True),
        ("https://google.com/recaptcha/api.js", True),
        ("https://www.google.com/maps", False),
        ("https://sub.hcaptcha.com/x", True),
        ("https://nothcaptcha.com/x", False),
        ("https://www.recaptcha.net/recaptcha/api.js", True),
        ("https://example.com/", False),
    ],
)
def test_captcha_allowlist_is_narrow(url: str, expected: bool) -> None:
    assert is_captcha_url(url) is expected


async def test_an_allowed_captcha_path_cannot_be_used_to_smuggle_the_details_out(
    server: AppServer,
) -> None:
    profile = _profile()
    async with open_form_session(
        f"{server.url}/captcha_leak.html", allow_local=True, headless=True
    ) as session:
        await prepare_application(
            session, profile, AnswerContext(profile=profile), provider=None, job=None
        )
        await session._page.wait_for_timeout(3000)
        blocked = await session.blocked_hosts()
    # refused before it left the machine, and surfaced to the user with a clear label
    assert blocked == ["www.google.com (contained your details)"]
