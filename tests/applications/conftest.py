"""Shared infrastructure for tests that drive a real browser against the local fixtures."""

from __future__ import annotations

import asyncio
import functools
import http.server
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from email.message import Message
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from openapply.browser.browser import chromium_status

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@dataclass
class Post:
    path: str
    fields: dict[str, list[str]] = field(default_factory=dict)
    files: dict[str, tuple[str, bytes]] = field(default_factory=dict)


def parse_post(path: str, content_type: str, body: bytes) -> Post:
    post = Post(path=path)
    if content_type.startswith("multipart/form-data"):
        header = f"Content-Type: {content_type}\r\n\r\n".encode()
        message = BytesParser().parsebytes(header + body)
        parts = message.get_payload()
        assert isinstance(parts, list)
        for part in parts:
            assert isinstance(part, Message)
            name = part.get_param("name", header="content-disposition")
            filename = part.get_filename()
            raw = part.get_payload(decode=True)
            payload = raw if isinstance(raw, bytes) else b""
            if filename is not None:
                post.files[str(name)] = (filename, payload)
            else:
                post.fields.setdefault(str(name), []).append(payload.decode("utf-8"))
    else:
        post.fields = {
            k: v for k, v in parse_qs(body.decode("utf-8"), keep_blank_values=True).items()
        }
    return post


class _Server(http.server.ThreadingHTTPServer):
    posts: list[Post]
    gets: list[str]  # every GET path received (static files and exfiltration attempts alike)
    redirect_target: str = ""  # where /redirect307 and /redirect308 send the POST


class _Handler(http.server.SimpleHTTPRequestHandler):
    server: _Server

    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        self.server.gets.append(self.path)
        super().do_GET()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.server.posts.append(parse_post(self.path, self.headers.get("Content-Type", ""), body))
        # 307/308 keep the method and the body: the redirect a careless client would follow
        for status, prefix in ((307, "/redirect307"), (308, "/redirect308")):
            if self.path.startswith(prefix):
                same_site = self.path.endswith("_same")
                target = "/submit" if same_site else f"{self.server.redirect_target}/submit"
                self.send_response(status)
                self.send_header("Location", target)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        page = b"<html><body><h1>Thank you for applying</h1></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)


@dataclass
class AppServer:
    url: str
    posts: list[Post]
    gets: list[str]
    server: _Server

    @property
    def port(self) -> int:
        return int(self.url.rsplit(":", 1)[1])


def _start() -> AppServer:
    handler = functools.partial(_Handler, directory=str(FIXTURES))
    server = _Server(("127.0.0.1", 0), handler)
    server.posts = []
    server.gets = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return AppServer(
        url=f"http://127.0.0.1:{server.server_address[1]}",
        posts=server.posts,
        gets=server.gets,
        server=server,
    )


def _stop(app: AppServer) -> None:
    app.server.shutdown()
    app.server.server_close()


@pytest.fixture(scope="session")
def app_server() -> Iterator[AppServer]:
    app = _start()
    yield app
    _stop(app)


@pytest.fixture(scope="session")
def other_app_server() -> Iterator[AppServer]:
    """A second site: the destination the user did NOT confirm. It must never see a request."""
    app = _start()
    yield app
    _stop(app)


@pytest.fixture(scope="session")
def browser_ready() -> bool:
    if not asyncio.run(chromium_status())[0]:
        pytest.skip("No usable browser (run `openapply browser install`)")
    return True


@pytest.fixture
def server(app_server: AppServer, browser_ready: bool) -> AppServer:
    app_server.posts.clear()
    app_server.gets.clear()
    return app_server


@pytest.fixture
def other_server(other_app_server: AppServer, server: AppServer) -> AppServer:
    """The unconfirmed site, and the confirmed site's 307/308 redirects point at it."""
    other_app_server.posts.clear()
    other_app_server.gets.clear()
    server.server.redirect_target = other_app_server.url
    return other_app_server
