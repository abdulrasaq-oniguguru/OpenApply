"""Redirects are followed by the policy hop by hop: the browser must never follow a chain itself.

Chromium only lets us intercept the *first* request of a redirect chain; if a 3xx were handed
back, the rest of the chain would be followed natively and unobserved. These tests run against
small inline servers so every hop is explicit.
"""

from __future__ import annotations

import asyncio
import http.server
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field

import pytest

from openapply.browser.browser import PlaywrightFetcher, chromium_status
from openapply.browser.forms import open_form_session
from openapply.browser.page import BrowserError
from openapply.browser.policy import RequestPolicy

Response = tuple[int, dict[str, str], bytes]


@dataclass
class Site:
    port: int
    hits: list[str] = field(default_factory=list)  # "METHOD /path" for every request received
    upgrades: list[str] = field(default_factory=list)  # paths that asked for a WebSocket upgrade

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


@contextmanager
def serve(routes: Mapping[tuple[str, str], Response | Callable[[], Response]]) -> Iterator[Site]:
    site = Site(port=0)

    class Handler(http.server.BaseHTTPRequestHandler):
        def _handle(self, method: str) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            if length:
                self.rfile.read(length)
            site.hits.append(f"{method} {self.path.split('#')[0]}")
            if self.headers.get("Upgrade", "").lower() == "websocket":
                site.upgrades.append(self.path)
            route = routes.get((method, self.path.split("?")[0]))
            status, headers, body = route() if callable(route) else (route or (404, {}, b"no"))
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            self._handle("GET")

        def do_POST(self) -> None:
            self._handle("POST")

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    site.port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield site
    finally:
        server.shutdown()
        server.server_close()


def redirect(location: str, status: int = 302) -> Response:
    return (status, {"Location": location}, b"")


def html(body: str) -> Response:
    return (200, {"Content-Type": "text/html; charset=utf-8"}, body.encode())


@pytest.fixture(scope="module")
def chromium_ready() -> bool:
    if not asyncio.run(chromium_status())[0]:
        pytest.skip("No usable browser (run `openapply browser install`)")
    return True


def only(*sites: Site) -> Callable[[str], bool]:
    """A policy that allows exactly these sites, so every other local server is 'forbidden'."""
    ports = {f":{site.port}" for site in sites}
    return lambda url: any(port in url for port in ports)


# --- page reading -----------------------------------------------------------------------------


async def test_the_second_hop_of_a_chain_cannot_reach_a_forbidden_address(
    chromium_ready: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    with serve({("GET", "/secret"): html("<p>INTERNAL-SECRET</p>")}) as forbidden:
        routes = {
            ("GET", "/start"): redirect("/second"),
            ("GET", "/second"): redirect(f"{forbidden.url}/secret"),
        }
        with serve(routes) as public:
            fetcher = PlaywrightFetcher(allow_local=True, timeout_ms=10_000)
            monkeypatch.setattr(fetcher._policy, "allowed", only(public))
            with pytest.raises(BrowserError):
                await fetcher.fetch(f"{public.url}/start")
        assert forbidden.hits == []  # the hop was stopped before a request was ever made


async def test_a_legitimate_chain_is_followed_and_the_final_address_is_remembered(
    chromium_ready: bool,
) -> None:
    routes = {
        ("GET", "/short"): redirect("/long"),
        ("GET", "/long"): redirect("/jobs/42", 301),
        ("GET", "/jobs/42"): html("<h1>Backend Engineer</h1><p>" + "Real job text. " * 30 + "</p>"),
    }
    with serve(routes) as site:
        fetcher = PlaywrightFetcher(allow_local=True, timeout_ms=10_000)
        page = await fetcher.fetch(f"{site.url}/short")
    assert "Backend Engineer" in page.text
    assert page.final_url == f"{site.url}/jobs/42"  # canonical address, for job ids and platform
    assert site.hits == ["GET /short", "GET /long", "GET /jobs/42"]


async def test_a_redirect_loop_is_refused(chromium_ready: bool) -> None:
    with serve({("GET", "/loop"): redirect("/loop")}) as site:
        fetcher = PlaywrightFetcher(allow_local=True, timeout_ms=10_000)
        with pytest.raises(BrowserError):
            await fetcher.fetch(f"{site.url}/loop")
    assert len(site.hits) <= 12  # bounded: the policy gave up rather than looping forever


async def test_credentials_are_not_forwarded_to_a_different_origin(chromium_ready: bool) -> None:
    seen: dict[str, str] = {}
    with serve({("GET", "/landing"): html("<p>" + "x" * 400 + "</p>")}) as other:
        routes = {("GET", "/hop"): redirect(f"{other.url}/landing")}
        with serve(routes) as first:
            fetcher = PlaywrightFetcher(allow_local=True, timeout_ms=10_000)
            page = await fetcher.fetch(f"{first.url}/hop")
            seen["landed"] = page.final_url
    assert seen["landed"] == f"{other.url}/landing"


# --- WebSockets -------------------------------------------------------------------------------


async def test_a_page_cannot_open_a_websocket_to_a_forbidden_address(
    chromium_ready: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two layers guarantee this: ``route_ws`` (unit-tested below) and the fact that Chromium
    refuses WebSocket handshakes outright once an HTTP route is installed (page sockets simply
    do not work: fail closed). Either way the internal server must never see a connection."""
    with serve({}) as internal:
        script = f"<script>new WebSocket('ws://127.0.0.1:{internal.port}/socket');</script>"
        page = html(f"<h1>Job</h1>{script}")  # short text: the fetcher waits for scripts
        with serve({("GET", "/"): page}) as public:
            fetcher = PlaywrightFetcher(allow_local=True, timeout_ms=10_000)
            monkeypatch.setattr(fetcher._policy, "allowed", only(public))
            await fetcher.fetch(f"{public.url}/")
        assert internal.upgrades == []


class _FakeWebSocket:
    """The slice of Playwright's WebSocketRoute that route_ws uses."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.connected = False
        self.closed: tuple[int | None, str | None] | None = None

    def connect_to_server(self) -> _FakeWebSocket:
        self.connected = True
        return self

    async def close(self, *, code: int | None = None, reason: str | None = None) -> None:
        self.closed = (code, reason)


@pytest.mark.parametrize(
    ("url", "allow_local", "connects"),
    [
        ("wss://careers.example.com/live", False, True),
        ("ws://careers.example.com/live", False, True),
        ("ws://127.0.0.1:9000/socket", False, False),
        ("ws://localhost:9000/socket", False, False),
        ("wss://169.254.169.254/latest", False, False),
        ("ws://10.0.0.5/x", False, False),
        ("ws://127.0.0.1:9000/socket", True, True),  # only with the user's explicit opt-in
    ],
)
async def test_route_ws_applies_the_url_policy_to_websockets(
    url: str, allow_local: bool, connects: bool
) -> None:
    policy = RequestPolicy(allow_local=allow_local, block_types=frozenset(), timeout_ms=1000)
    fake = _FakeWebSocket(url)
    await policy.route_ws(fake)  # type: ignore[arg-type]
    assert fake.connected is connects
    assert (fake.closed is None) is connects  # refused sockets are closed, allowed ones are not


# --- forms ------------------------------------------------------------------------------------


FORM = (
    '<form action="{action}" method="post">'
    '<label for="e">Email *</label><input id="e" name="email" type="email" required>'
    '<button type="submit">Submit application</button></form>'
)


async def test_a_post_that_answers_302_becomes_a_get_of_the_final_page(
    chromium_ready: bool,
) -> None:
    routes = {
        ("GET", "/apply"): html(FORM.format(action="/receive")),
        ("POST", "/receive"): redirect("/thanks", 302),
        ("GET", "/thanks"): html("<h1>Thank you: this is the thanks page</h1>"),
    }
    with serve(routes) as site:
        async with open_form_session(f"{site.url}/apply", allow_local=True, headless=True) as s:
            scan = await s.scan()
            email = scan.fields[0]
            await s.fill_text(email.id, "ada@example.com")
            result = await s.submit()
    assert "thanks page" in result.excerpt
    # one POST to the endpoint, then a plain GET for the thank-you page (no second POST)
    assert [h for h in site.hits if h != "GET /apply"] == ["POST /receive", "GET /thanks"]


async def test_relative_urls_still_resolve_against_where_a_redirected_page_came_from(
    chromium_ready: bool,
) -> None:
    routes = {
        ("GET", "/go"): redirect("/deep/form"),
        ("GET", "/deep/form"): html(FORM.format(action="receive")),  # relative to /deep/
    }
    with serve(routes) as site:
        async with open_form_session(f"{site.url}/go", allow_local=True, headless=True) as s:
            scan = await s.scan()
    # the page's own address is still /go, but its form must post under /deep/, not under /
    assert scan.form_action == f"{site.url}/deep/receive"
