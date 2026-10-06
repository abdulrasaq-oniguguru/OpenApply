"""The request policy shared by every browser context OpenApply opens.

One implementation of the security-critical rules, used by the page fetcher and the form
session alike:

* every request is checked against the URL policy (http(s) only, no private/local addresses
  unless the user opted in), and so is every WebSocket
* **redirects are followed here, hop by hop, never by the browser.** Chromium only gives us
  the *first* request of a redirect chain: if a 3xx were handed back, the browser would follow
  the rest of the chain natively and unobserved (it could end at an internal address, or a
  307/308 could carry a POST body to another site). So each hop is checked against the policy
  and the original request is fulfilled with the final, non-redirect response
* responses that declare a very large length are refused

During the user-approved submit there is one more rule (see ``submit_window``): a POST may
only go to the origin the user confirmed.
"""

from __future__ import annotations

import contextlib
import logging
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote_plus, urljoin, urlsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Route, WebSocketRoute

from openapply.security.urls import is_private_host

log = logging.getLogger("openapply.browser")

MAX_RESPONSE_BYTES = 10 * 1024 * 1024
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_REDIRECTS = 10
MIN_MARKER_CHARS = 4  # shorter field values are too ambiguous to recognise a request by
_SAFE_METHODS = frozenset({"GET", "HEAD"})
_METHOD_PRESERVING_REDIRECTS = frozenset({307, 308})
_BODY_HEADERS = frozenset({"content-length", "content-type", "content-encoding"})
_CROSS_ORIGIN_DROPPED_HEADERS = frozenset({"authorization", "cookie", "proxy-authorization"})
_BOUNDARY = re.compile(r'boundary=(?:"([^"]+)"|([^;\s]+))', re.IGNORECASE)
_FILENAME = re.compile(rb'filename="([^"]*)"')
_HEAD_TAG = re.compile(rb"<head[^>]*>", re.IGNORECASE)
_BASE_TAG = re.compile(rb"<base[\s>]", re.IGNORECASE)


def first_line(exc: Exception) -> str:
    return (str(exc).strip().splitlines() or [type(exc).__name__])[0][:300]


def declared_length(headers: dict[str, str]) -> int:
    """Content-Length if the server declared one (chunked responses are bounded by timeouts)."""
    try:
        return int(headers.get("content-length", "0"))
    except ValueError:
        return 0


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


_CAPTCHA_HOSTS = ("hcaptcha.com", "challenges.cloudflare.com", "recaptcha.net")
_RECAPTCHA_HOSTS = frozenset({"google.com", "www.google.com", "gstatic.com", "www.gstatic.com"})


def is_captcha_url(url: str) -> bool:
    """CAPTCHA providers that application forms legitimately need while the form is open.

    Deliberately narrow: google.com and gstatic.com are only allowed under /recaptcha/.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host in _RECAPTCHA_HOSTS:
        return parts.path.startswith("/recaptcha/")
    return any(host == h or host.endswith("." + h) for h in _CAPTCHA_HOSTS)


def _http_origin(url: str) -> str:
    """Like ``origin_of`` but a WebSocket counts as the HTTP origin it belongs to."""
    parts = urlsplit(url)
    scheme = {"ws": "http", "wss": "https"}.get(parts.scheme, parts.scheme)
    return f"{scheme}://{parts.netloc}".lower()


def rebuild_multipart(content_type: str, body: bytes, uploads: Mapping[str, bytes]) -> bytes | None:
    """Fill in the file contents Chromium withholds from intercepted multipart requests.

    Request interception shows a file part's headers (so its filename) but not its bytes, so
    re-sending the body as-is would deliver an *empty* file. Parts whose filename matches a
    file OpenApply itself uploaded are filled from those bytes. Returns the body unchanged if
    nothing needs filling, and ``None`` if a file part cannot be matched (a file we cannot
    vouch for): the caller must not send such a request.
    """
    match = _BOUNDARY.search(content_type)
    if match is None:
        return body
    boundary = (match.group(1) or match.group(2)).encode("utf-8")
    delimiter = b"--" + boundary
    chunks = body.split(delimiter)
    rebuilt = [chunks[0]]
    for chunk in chunks[1:]:
        head_end = chunk.find(b"\r\n\r\n")
        if chunk.startswith(b"--") or head_end == -1:
            rebuilt.append(chunk)  # the closing delimiter, or something we leave alone
            continue
        headers, content = chunk[:head_end], chunk[head_end + 4 :]
        tail = b""
        if content.endswith(b"\r\n"):
            content, tail = content[:-2], b"\r\n"
        named = _FILENAME.search(headers)
        if named is not None and named.group(1) and content == b"":
            data = uploads.get(named.group(1).decode("utf-8", "replace"))
            if data is None:
                return None
            content = data
        rebuilt.append(headers + b"\r\n\r\n" + content + tail)
    return delimiter.join(rebuilt)


def carries_form_data(body: bytes | None, markers: Sequence[str]) -> bool:
    """Does this request body contain any of the values that were entered in the form?"""
    if not body or not markers:
        return False
    text = body.decode("utf-8", "ignore")
    haystacks = (text, unquote_plus(text))  # urlencoded forms escape "@" and spaces
    return any(marker in haystack for marker in markers for haystack in haystacks)


def inject_base_href(html: bytes, final_url: str) -> bytes:
    """Make relative URLs resolve against where a redirected page really came from."""
    if _BASE_TAG.search(html):
        return html
    tag = f'<base href="{final_url}">'.encode()
    head = _HEAD_TAG.search(html)
    if head is None:
        return tag + html
    return html[: head.end()] + tag + html[head.end() :]


@dataclass
class PostLog:
    """What happened to non-GET requests during an approved submit."""

    sent: int = 0  # POSTs let through to the confirmed site
    # A POST made by the form's own navigation, carrying the form's data, reached the confirmed
    # site. Tracked apart from other traffic: a stray same-site beacon must not look like it.
    sent_form_navigation: bool = False
    sent_script_data: bool = False  # a script (xhr/fetch) POST carrying form data got through
    blocked: list[str] = field(default_factory=list)  # hosts that were refused
    blocked_data: bool = False  # a refused request was the form, or carried its data
    unverifiable_upload: bool = False  # a file in the body could not be vouched for


class RequestPolicy:
    def __init__(self, *, allow_local: bool, block_types: frozenset[str], timeout_ms: int) -> None:
        self._allow_local = allow_local
        self._block_types = block_types
        self._timeout_ms = timeout_ms
        self._uploads: dict[str, bytes] = {}
        self._final_urls: dict[str, str] = {}
        self._window = False
        self._confirmed_origin = ""
        self._markers: tuple[str, ...] = ()
        self._post_log: PostLog | None = None
        self._quarantine: frozenset[str] | None = None
        self._quarantine_hosts: list[str] = []
        self._standing_markers: set[str] = set()

    def quarantine(self, origins: Sequence[str]) -> None:
        """From now on the page may only talk to ``origins`` (plus CAPTCHA providers).

        Called once the form has been read, i.e. before any of the user's details are put into
        it. Page scripts can read what has been filled in and send it anywhere with a plain GET
        (an image beacon, a ``fetch``), long before the user has approved anything, so every
        cross-origin request is refused while the details are present, whatever its method.
        The form's declared ``action`` is page-controlled and is NOT trusted here: the only way
        another site becomes reachable is the user typing its name for the final submit.
        """
        self._quarantine = frozenset(_http_origin(o) for o in origins)

    def add_markers(self, values: Sequence[str]) -> None:
        """Remember values the user's details consist of, to recognise them in outgoing requests.

        Registered from what OpenApply itself writes into the form (and from what is in the
        fields at submit), not read back from the page, which controls what it reports.
        """
        self._standing_markers.update(v for v in values if len(v) >= MIN_MARKER_CHARS)

    def _leaks_to_captcha(self, request_url: str, body: bytes | None) -> bool:
        """A CAPTCHA provider is trusted with its own traffic, not with the user's details."""
        markers = (*self._standing_markers, *self._markers)
        return carries_form_data(request_url.encode(), markers) or carries_form_data(body, markers)

    def quarantine_allows(self, url: str) -> bool:
        if self._quarantine is None:
            return True
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https", "ws", "wss"}:
            return True  # data:, blob:, about: never leave the machine
        return _http_origin(url) in self._quarantine or is_captcha_url(url)

    def blocked_hosts(self) -> list[str]:
        """Hosts the page tried to contact while the user's details were filled in."""
        return list(dict.fromkeys(self._quarantine_hosts))

    def _quarantine_blocks(self, url: str, method: str) -> bool:
        if self._quarantine is None or self.quarantine_allows(url):
            return False
        # during the approved submit, the confirmed site may receive the POST
        confirmed_post = (
            self._window
            and method not in _SAFE_METHODS
            and origin_of(url) == self._confirmed_origin
        )
        return not confirmed_post

    def _note_quarantine_block(
        self, url: str, *, is_navigation: bool, body: bytes | None, detail: str = ""
    ) -> None:
        host = urlsplit(url).netloc
        self._quarantine_hosts.append(f"{host} ({detail})" if detail else host)
        log.info("blocked a request to another site while the form is filled in")
        if self._post_log is None:
            return
        self._post_log.blocked.append(host)
        carries = carries_form_data(url.encode(), self._markers) or carries_form_data(
            body, self._markers
        )
        if is_navigation or carries:
            self._post_log.blocked_data = True

    def register_upload(self, filename: str, data: bytes) -> None:
        """Remember a file OpenApply itself put into the form, so its bytes can be restored."""
        self._uploads[filename] = data

    def final_url_for(self, url: str) -> str:
        """Where a navigation to ``url`` really ended up after redirects (``url`` if none)."""
        return self._final_urls.get(url, url)

    @contextlib.contextmanager
    def submit_window(self, *, only_to: str, markers: Sequence[str]) -> Iterator[PostLog]:
        """For the final, user-approved submit: POSTs may only go to ``only_to``'s origin.

        Enforced where the data leaves, not by a check made beforehand: page scripts run
        between a click and the browser's own post and can change the destination in that gap.
        A non-GET request to any other origin is aborted before it is sent, and so is a 307/308
        redirect (which would carry the body along) to another origin. Multipart bodies get
        their file contents back (see ``rebuild_multipart``). ``markers`` are values entered in
        the form, used to tell the real submission from unrelated traffic.
        """
        log_ = PostLog()
        self._confirmed_origin = origin_of(only_to)
        self._markers = tuple(m for m in markers if len(m) >= MIN_MARKER_CHARS)
        self._post_log = log_
        self._window = True
        try:
            yield log_
        finally:
            self._window = False
            self._post_log = None

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https", "ws", "wss"}:
            return parts.scheme in {"about", "blob", "data"}  # internal, harmless
        return self._allow_local or not is_private_host(parts.hostname or "")

    async def route_ws(self, ws: WebSocketRoute) -> None:
        """WebSockets never pass through request routing, so they get the same URL policy."""
        if self.allowed(ws.url) and self.quarantine_allows(ws.url):
            ws.connect_to_server()
            return
        if self.allowed(ws.url):
            self._quarantine_hosts.append(urlsplit(ws.url).netloc)
        log.info("blocked a WebSocket to a disallowed address")
        with contextlib.suppress(PlaywrightError):
            await ws.close(code=1008, reason="blocked by OpenApply")

    def _block_post(self, url: str, *, form_related: bool) -> None:
        if self._post_log is None:
            return
        self._post_log.blocked.append(urlsplit(url).netloc)
        if form_related:
            self._post_log.blocked_data = True

    @staticmethod
    def _hop_headers(
        request_headers: dict[str, str], *, drop_body: bool, cross_origin: bool
    ) -> dict[str, str]:
        dropped = {"host"}
        if drop_body:
            dropped |= _BODY_HEADERS
        if cross_origin:
            dropped |= _CROSS_ORIGIN_DROPPED_HEADERS
        return {k: v for k, v in request_headers.items() if k.lower() not in dropped}

    async def route(self, route: Route) -> None:
        request = route.request
        if request.resource_type in self._block_types or not self.allowed(request.url):
            await self._abort(route)
            return
        method = request.method.upper()
        in_window = self._window and method not in _SAFE_METHODS
        is_navigation = request.resource_type == "document"
        body = request.post_data_buffer if in_window else None
        carries = in_window and carries_form_data(body, self._markers)
        send_body: bytes | None = None  # None: the original body is used as it is

        if self._quarantine_blocks(request.url, method):
            self._note_quarantine_block(request.url, is_navigation=is_navigation, body=body)
            await self._abort(route)
            return
        if (
            self._quarantine is not None
            and _http_origin(request.url) not in self._quarantine
            and is_captcha_url(request.url)
            and self._leaks_to_captcha(request.url, request.post_data_buffer)
        ):
            # allowed only because it is a CAPTCHA provider, and it is carrying the user's data
            self._note_quarantine_block(
                request.url, is_navigation=is_navigation, body=body, detail="contained your details"
            )
            await self._abort(route)
            return

        if in_window:
            if origin_of(request.url) != self._confirmed_origin:
                log.info("blocked a POST to a site that was not the confirmed destination")
                self._block_post(request.url, form_related=carries or is_navigation)
                await self._abort(route)
                return
            content_type = request.headers.get("content-type", "")
            if body is not None and content_type.lower().startswith("multipart/form-data"):
                send_body = rebuild_multipart(content_type, body, self._uploads)
                if send_body is None:
                    log.info("blocked a POST containing a file OpenApply did not upload")
                    if self._post_log is not None:
                        self._post_log.unverifiable_upload = True
                    self._block_post(request.url, form_related=True)
                    await self._abort(route)
                    return

        url = request.url
        fetch_headers: dict[str, str] | None = None
        response = None
        for _hop in range(MAX_REDIRECTS + 1):
            kwargs: dict[str, Any] = {"url": url, "max_redirects": 0, "timeout": self._timeout_ms}
            if method != request.method.upper():
                kwargs["method"] = method
            if fetch_headers is not None:
                kwargs["headers"] = fetch_headers
            if send_body is not None:
                kwargs["post_data"] = send_body
            try:
                response = await route.fetch(**kwargs)
            except PlaywrightError as exc:
                log.debug("request failed: %s", first_line(exc))
                await self._abort(route)
                return
            if declared_length(response.headers) > MAX_RESPONSE_BYTES:
                log.info("blocked an oversized response")
                await self._abort(route)
                return
            location = response.headers.get("location")
            if not (300 <= response.status < 400 and location):
                break

            # A redirect: follow it here, checking the hop, instead of handing it to the browser.
            target = urljoin(url, location)
            if not self.allowed(target):
                log.info("blocked a redirect to a disallowed address")
                await self._abort(route)
                return
            keeps_method = response.status in _METHOD_PRESERVING_REDIRECTS
            next_method = method if keeps_method or method in _SAFE_METHODS else "GET"
            if self._quarantine_blocks(target, next_method):
                self._note_quarantine_block(target, is_navigation=is_navigation, body=body)
                await self._abort(route)
                return
            if (
                in_window
                and next_method not in _SAFE_METHODS
                and origin_of(target) != self._confirmed_origin
            ):
                log.info("blocked a redirect that would carry the form to another site")
                self._block_post(target, form_related=True)
                await self._abort(route)
                return
            drop_body = next_method != method  # 301/302/303 turn a POST into a body-less GET
            fetch_headers = self._hop_headers(
                request.headers,
                drop_body=drop_body or (method in _SAFE_METHODS),
                cross_origin=origin_of(target) != origin_of(url),
            )
            if drop_body:
                # An explicit empty body, so the original one is not re-sent with the GET.
                send_body = b""
            method, url = next_method, target
        else:
            log.info("blocked a redirect loop")
            await self._abort(route)
            return

        assert response is not None
        if in_window and self._post_log is not None:
            self._post_log.sent += 1
            if carries and is_navigation:
                self._post_log.sent_form_navigation = True
            elif carries:
                self._post_log.sent_script_data = True
        await self._fulfill(route, response, request.url, url, is_navigation)

    async def _fulfill(
        self, route: Route, response: Any, original_url: str, final_url: str, is_navigation: bool
    ) -> None:
        with contextlib.suppress(PlaywrightError):
            if is_navigation and final_url != original_url:
                self._final_urls[original_url] = final_url
                content_type = response.headers.get("content-type", "")
                if "html" in content_type.lower():
                    body = inject_base_href(await response.body(), final_url)
                    await route.fulfill(response=response, body=body)
                    return
            await route.fulfill(response=response)

    @staticmethod
    async def _abort(route: Route) -> None:
        with contextlib.suppress(PlaywrightError):  # the page may already be gone
            await route.abort()
