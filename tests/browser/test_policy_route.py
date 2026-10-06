"""The request handler itself, driven with fake routes (offline; no browser needed)."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import pytest

from openapply.browser.policy import RequestPolicy

EMAIL = "ada@example.com"
PAGE = "https://careers.example.com/apply"
CAPTCHA = "https://www.google.com/recaptcha/api2/userverify"


class FakeResponse:
    status = 200

    def __init__(self) -> None:
        self.headers = {"content-type": "text/plain"}

    async def body(self) -> bytes:
        return b"ok"


class FakeRequest:
    def __init__(
        self, url: str, *, method: str = "GET", body: bytes | None = None, kind: str = "fetch"
    ) -> None:
        self.url = url
        self.method = method
        self.resource_type = kind
        self.post_data_buffer = body
        self.headers: dict[str, str] = {}


class FakeRoute:
    def __init__(self, request: FakeRequest) -> None:
        self.request = request
        self.outcome = ""

    async def abort(self) -> None:
        self.outcome = "aborted"

    async def fetch(self, **kwargs: Any) -> FakeResponse:
        return FakeResponse()

    async def fulfill(self, **kwargs: Any) -> None:
        self.outcome = "served"

    async def continue_(self) -> None:
        self.outcome = "continued"


def _policy(*, quarantined: bool = True) -> RequestPolicy:
    policy = RequestPolicy(allow_local=True, block_types=frozenset(), timeout_ms=1000)
    if quarantined:
        policy.quarantine([PAGE])
        policy.add_markers([EMAIL, "ada.lovelace.long.value"])
    return policy


async def _go(policy: RequestPolicy, request: FakeRequest) -> str:
    route = FakeRoute(request)
    await policy.route(route)  # type: ignore[arg-type]
    return route.outcome


# --- CAPTCHA providers are trusted with their own traffic, not with the user's details --------


async def test_a_captcha_request_without_the_users_details_is_served() -> None:
    policy = _policy()
    assert await _go(policy, FakeRequest(f"{CAPTCHA}?k=abc123&v=token")) == "served"
    assert policy.blocked_hosts() == []


@pytest.mark.parametrize(
    "url",
    [
        f"{CAPTCHA}?e={EMAIL}",
        f"{CAPTCHA}?e={quote(EMAIL)}",  # percent-encoded
        f"{CAPTCHA}?e={quote(EMAIL).replace('%40', '%2540')}x" + f"&also={EMAIL}",
        "https://www.google.com/recaptcha/api.js?render=ada.lovelace.long.value",
        "https://challenges.cloudflare.com/turnstile/v0/x?name=ada.lovelace.long.value",
    ],
)
async def test_a_captcha_request_carrying_the_users_details_is_blocked_and_reported(
    url: str,
) -> None:
    policy = _policy()
    assert await _go(policy, FakeRequest(url)) == "aborted"
    (reported,) = policy.blocked_hosts()
    assert reported.endswith("(contained your details)")


async def test_a_captcha_post_body_is_checked_too() -> None:
    policy = _policy()
    leak = FakeRequest(CAPTCHA, method="POST", body=f"data=1&email={quote(EMAIL)}".encode())
    assert await _go(policy, leak) == "aborted"
    clean = FakeRequest(CAPTCHA, method="POST", body=b"response=abcdef&k=site")
    assert await _go(_policy(), clean) == "served"


async def test_short_values_are_not_used_to_recognise_requests() -> None:
    """A 3-letter first name would false-positive on random CAPTCHA tokens, so it is ignored."""
    policy = _policy()
    policy.add_markers(["Ada"])
    assert await _go(policy, FakeRequest(f"{CAPTCHA}?token=xxAdaxx")) == "served"


async def test_values_registered_for_the_submit_window_count_as_well() -> None:
    policy = _policy(quarantined=True)
    with policy.submit_window(only_to=PAGE, markers=["typed-by-hand-in-the-browser"]):
        leak = FakeRequest(f"{CAPTCHA}?x=typed-by-hand-in-the-browser")
        assert await _go(policy, leak) == "aborted"


# --- the rest of the quarantine, unchanged ----------------------------------------------------


async def test_the_pages_own_site_may_receive_the_details_it_is_the_recipient() -> None:
    policy = _policy()
    own = FakeRequest(f"{PAGE}/validate?email={EMAIL}")
    assert await _go(policy, own) == "served"


async def test_another_site_is_blocked_with_or_without_details() -> None:
    policy = _policy()
    assert await _go(policy, FakeRequest(f"https://evil.example/c?e={EMAIL}")) == "aborted"
    assert await _go(policy, FakeRequest("https://cdn.example.net/app.js")) == "aborted"
    assert policy.blocked_hosts() == ["evil.example", "cdn.example.net"]  # plain host names


async def test_nothing_is_restricted_before_the_form_is_read() -> None:
    policy = _policy(quarantined=False)
    assert await _go(policy, FakeRequest(f"{CAPTCHA}?e={EMAIL}")) == "served"
    assert await _go(policy, FakeRequest("https://cdn.example.net/app.js")) == "served"
    assert policy.blocked_hosts() == []
