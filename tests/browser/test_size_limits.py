"""Hostile-size pages: limits must apply before data is transferred into Python."""

from __future__ import annotations

import asyncio
import http.server
import threading

import pytest

from openapply.browser import policy as policy_module
from openapply.browser.browser import PlaywrightFetcher, chromium_status
from openapply.browser.page import (
    MAX_JSON_LD_BLOCK_CHARS,
    MAX_PAGE_CHARS,
    BrowserError,
    normalize_text,
)


@pytest.fixture(scope="module")
def chromium_ready() -> bool:
    if not asyncio.run(chromium_status())[0]:
        pytest.skip("No usable browser (run `openapply browser install`)")
    return True


def _serve(
    body: bytes, content_type: str = "text/html; charset=utf-8"
) -> http.server.ThreadingHTTPServer:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _stop(server: http.server.ThreadingHTTPServer) -> None:
    server.shutdown()
    server.server_close()


async def test_oversized_text_and_json_ld_are_capped_before_transfer(
    chromium_ready: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    big_text = "Lorem ipsum dolor sit amet. " * 150_000  # ~4 MB of visible text
    huge_ld = '{"@type":"JobPosting","title":"HUGE","pad":"' + "x" * 1_000_000 + '"}'
    small_ld = '{"@type":"JobPosting","title":"small"}'
    body = (
        "<html><head><title>Big</title>"
        f'<script type="application/ld+json">{huge_ld}</script>'
        f'<script type="application/ld+json">{small_ld}</script>'
        f"</head><body><p>{big_text}</p></body></html>"
    ).encode()
    assert len(body) > 4_000_000
    assert len(huge_ld) > MAX_JSON_LD_BLOCK_CHARS

    received: list[int] = []

    def spy(raw: str, **kwargs: int) -> str:
        received.append(len(raw))
        return normalize_text(raw, **kwargs)

    monkeypatch.setattr("openapply.browser.browser.normalize_text", spy)
    server = _serve(body)
    try:
        fetcher = PlaywrightFetcher(allow_local=True, timeout_ms=30_000)
        page = await fetcher.fetch(f"http://127.0.0.1:{server.server_address[1]}/")
    finally:
        _stop(server)

    assert received
    assert max(received) <= MAX_PAGE_CHARS  # sliced inside the page, before transfer
    assert len(page.text) <= MAX_PAGE_CHARS
    assert [item["title"] for item in page.json_ld] == ["small"]  # the 1 MB block never arrived


async def test_response_declaring_an_enormous_length_is_refused(
    chromium_ready: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(policy_module, "MAX_RESPONSE_BYTES", 1_000)
    server = _serve(b"<html><body>" + b"x" * 5_000 + b"</body></html>")
    try:
        fetcher = PlaywrightFetcher(allow_local=True, timeout_ms=10_000)
        with pytest.raises(BrowserError):
            await fetcher.fetch(f"http://127.0.0.1:{server.server_address[1]}/")
    finally:
        _stop(server)
