"""Reading and filling application forms in a live browser page.

This module knows about the DOM and nothing about job applications: it reports what the
page contains (``RawScan``) and performs primitive fill actions. Deciding what a field *means*
and what to put in it is the job of ``openapply.applications``.

Trust: the scan script runs in the page's own JavaScript world, so a hostile page could
tamper with its output. Everything it returns is validated here: ids must match a strict
pattern before they are ever placed in a selector, strings and lists are bounded, and the
field kind must be one we know.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Protocol

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator, Page, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeout
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

from openapply.browser.browser import (
    SETTLE_TIMEOUT_MS,
    launch_browser,
    launch_persistent_browser,
)
from openapply.browser.page import BrowserError, normalize_text
from openapply.browser.policy import MAX_UPLOAD_BYTES, RequestPolicy, first_line
from openapply.security.text import strip_control_chars
from openapply.security.urls import UnsafeURLError, validate_job_url

log = logging.getLogger("openapply.browser")

MAX_FIELDS = 120
MAX_TEXT = 300
MAX_OPTIONS = 300
MAX_WARNINGS = 10
ACTION_TIMEOUT_MS = 5_000
SUBMIT_SETTLE_MS = 15_000
RESULT_EXCERPT_CHARS = 400
# Keep images and fonts: a person is going to look at (and may need to solve) this page.
SESSION_BLOCKED_TYPES = frozenset({"media"})

_SCAN_JS = Path(__file__).with_name("scan_forms.js").read_text(encoding="utf-8")
_ID_RE = re.compile(r"^oa-\d{1,4}(-o\d{1,4})?$")
_ENTERED_VALUES_JS = """() => Array.from(document.querySelectorAll('input, textarea, select'))
  .filter((el) => !['password', 'hidden', 'file', 'checkbox', 'radio', 'submit', 'button', 'image']
    .includes((el.type || '').toLowerCase()))
  .map((el) => (el.value || '').trim())
  .filter((v) => v.length >= 4 && v.length <= 2000)
  .slice(0, 60)"""
# Must stay in step with `effectiveAction` in scan_forms.js.
_EFFECTIVE_ACTION_JS = """(el) => {
  if (el.hasAttribute('formaction')) return el.formAction || null;
  return el.form && el.form.action ? el.form.action : null;
}"""
_EFFECTIVE_METHOD_JS = """(el) => {
  if (el.hasAttribute('formmethod')) return (el.formMethod || 'get').toLowerCase();
  return el.form ? (el.form.method || 'get').toLowerCase() : 'get';
}"""
_INSTALL_SUBMIT_GUARD_JS = """({action, method}) => {
  window.__openapplySubmitBlocked = null;
  const guard = (event) => {
    const form = event.target;
    const submitter = event.submitter;
    const currentAction = submitter && submitter.hasAttribute('formaction')
      ? (submitter.formAction || null)
      : (form && form.action ? form.action : null);
    const currentMethod = submitter && submitter.hasAttribute('formmethod')
      ? (submitter.formMethod || 'get').toLowerCase()
      : (form ? (form.method || 'get').toLowerCase() : 'get');
    if (currentAction !== action || currentMethod !== method) {
      event.preventDefault();
      event.stopImmediatePropagation();
      window.__openapplySubmitBlocked = {action: currentAction, method: currentMethod};
    }
    document.removeEventListener('submit', guard, true);
  };
  document.addEventListener('submit', guard, true);
}"""
_SUBMIT_GUARD_RESULT_JS = "() => window.__openapplySubmitBlocked || null"
KINDS = frozenset(
    {
        "text",
        "email",
        "tel",
        "url",
        "number",
        "date",
        "textarea",
        "select",
        "radio",
        "checkbox",
        "file",
    }
)


def _text(value: object) -> str:
    return " ".join(strip_control_chars(str(value or "")).split())[:MAX_TEXT]


Text = Annotated[str, BeforeValidator(_text)]


class RawOption(BaseModel):
    model_config = ConfigDict(extra="ignore")

    value: Text = ""
    label: Text = ""
    element_id: str | None = None
    checked: bool = False

    @field_validator("element_id")
    @classmethod
    def _valid_element_id(cls, value: str | None) -> str | None:
        if value is not None and not _ID_RE.match(value):
            raise ValueError("invalid element id")
        return value


class RawField(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    kind: str
    label: Text = ""
    aria_label: Text = ""
    legend: Text = ""
    placeholder: Text = ""
    nearby: Text = ""
    hint: Text = ""
    name: Text = ""
    required: bool = False
    current_value: Text | None = None
    options: list[RawOption] = Field(default_factory=list, max_length=MAX_OPTIONS)
    max_length: int | None = Field(default=None, ge=1, le=100_000)
    accept: Text | None = None
    numeric: bool = False

    @field_validator("id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        if not re.match(r"^oa-\d{1,4}$", value):
            raise ValueError("invalid field id")
        return value

    @field_validator("kind")
    @classmethod
    def _valid_kind(cls, value: str) -> str:
        if value not in KINDS:
            raise ValueError(f"unknown field kind {value!r}")
        return value


class RawScan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: str
    title: Text | None = None
    form_action: str | None = None
    submit_label: Text | None = None
    fields: list[RawField] = Field(default_factory=list, max_length=MAX_FIELDS)
    warnings: list[Text] = Field(default_factory=list, max_length=MAX_WARNINGS)


@dataclass(frozen=True)
class FillResult:
    ok: bool
    actual: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class SubmitResult:
    url_before: str
    url_after: str
    navigated: bool
    excerpt: str
    blocked_hosts: tuple[str, ...] = ()  # other sites the page tried to POST to (refused)


class FormSession(Protocol):
    """A live application page. Real browser in production, a double in tests."""

    async def scan(self) -> RawScan: ...
    async def fill_text(self, field_id: str, value: str) -> FillResult: ...
    async def select_option(self, field_id: str, value: str) -> FillResult: ...
    async def set_checked(self, element_id: str, checked: bool) -> FillResult: ...
    async def upload(self, field_id: str, path: Path) -> FillResult: ...
    async def submit(self) -> SubmitResult: ...
    async def bring_to_front(self) -> None: ...
    async def blocked_hosts(self) -> list[str]: ...


def _read_upload(path: Path) -> bytes:
    with path.open("rb") as handle:
        data = handle.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise OSError(errno.EFBIG, "file is larger than 10 MB")
    return data


def _locator(page: Page, element_id: str) -> Locator:
    if not _ID_RE.match(element_id):  # never put unvalidated text into a selector
        raise BrowserError(f"Refusing to use an invalid element id: {element_id!r}")
    return page.locator(f'[data-oa-id="{element_id}"]')


def _closed(exc: PlaywrightError) -> BrowserError:
    message = str(exc)
    if "closed" in message.lower():
        return BrowserError("The browser window was closed.")
    return BrowserError(f"Browser problem: {first_line(exc)}")


class PlaywrightFormSession:
    def __init__(self, page: Page, timeout_ms: int, policy: RequestPolicy) -> None:
        self._page = page
        self._timeout_ms = timeout_ms
        self._policy = policy
        self._scanned = False
        self._approved_action: str | None = None

    async def scan(self) -> RawScan:
        try:
            raw = await self._page.evaluate(
                _SCAN_JS, {"maxFields": MAX_FIELDS, "maxText": MAX_TEXT, "maxOptions": MAX_OPTIONS}
            )
        except PlaywrightError as exc:
            raise _closed(exc) from exc
        try:
            scan = RawScan.model_validate(raw)
        except ValueError as exc:
            raise BrowserError("The page returned form data that could not be trusted") from exc
        # The destination shown to (and confirmed by) the user. submit() re-checks it.
        self._approved_action = scan.form_action
        self._scanned = True
        # The form has been read, so none of the user's details are in it yet. From here until
        # the session ends the page may only talk to its own site: scripts could otherwise read
        # whatever gets filled in and send it elsewhere before the user has approved anything.
        self._policy.quarantine([self._page.url, self._policy.final_url_for(self._page.url)])
        return scan

    async def blocked_hosts(self) -> list[str]:
        """Other sites the page tried to contact while the user's details were filled in."""
        return self._policy.blocked_hosts()

    async def _after_fill(self, locator: Locator, expected: str) -> FillResult:
        actual = await locator.input_value(timeout=ACTION_TIMEOUT_MS)
        if actual == expected:
            return FillResult(ok=True, actual=actual)
        return FillResult(
            ok=False, actual=actual, error="the page changed the value after it was set"
        )

    async def fill_text(self, field_id: str, value: str) -> FillResult:
        locator = _locator(self._page, field_id)
        try:
            await locator.fill(value, timeout=ACTION_TIMEOUT_MS)
            self._policy.add_markers([value])  # so the page cannot smuggle it out later
            return await self._after_fill(locator, value)
        except PlaywrightTimeout:
            return FillResult(ok=False, error="timed out waiting for the field")
        except PlaywrightError as exc:
            raise _closed(exc) from exc

    async def select_option(self, field_id: str, value: str) -> FillResult:
        locator = _locator(self._page, field_id)
        try:
            await locator.select_option(value=value, timeout=ACTION_TIMEOUT_MS)
            return await self._after_fill(locator, value)
        except PlaywrightTimeout:
            return FillResult(ok=False, error="timed out waiting for the field")
        except PlaywrightError as exc:
            raise _closed(exc) from exc

    async def set_checked(self, element_id: str, checked: bool) -> FillResult:
        locator = _locator(self._page, element_id)
        try:
            try:
                await locator.set_checked(checked, timeout=ACTION_TIMEOUT_MS)
            except PlaywrightTimeout:
                # Custom-styled controls hide the real input; clicking it directly still works.
                if await locator.is_checked() != checked:
                    await locator.evaluate("el => el.click()")
            actual = await locator.is_checked()
        except PlaywrightError as exc:
            raise _closed(exc) from exc
        if actual == checked:
            return FillResult(ok=True, actual=str(actual).lower())
        return FillResult(ok=False, actual=str(actual).lower(), error="the page refused the change")

    async def upload(self, field_id: str, path: Path) -> FillResult:
        locator = _locator(self._page, field_id)
        try:
            data = await asyncio.to_thread(_read_upload, path)
            await locator.set_input_files(str(path), timeout=ACTION_TIMEOUT_MS)
            count = await locator.evaluate("el => el.files ? el.files.length : 0")
        except OSError as exc:
            return FillResult(ok=False, error=f"could not read the file ({exc.strerror})")
        except PlaywrightTimeout:
            return FillResult(ok=False, error="timed out waiting for the upload field")
        except PlaywrightError as exc:
            raise _closed(exc) from exc
        if count == 1:
            # Remember the bytes: request interception hides file contents, and the policy
            # restores them (and refuses files it cannot vouch for) when the form is submitted.
            self._policy.register_upload(path.name, data)
            return FillResult(ok=True, actual=path.name)
        return FillResult(ok=False, error="the page did not accept the file")

    async def _excerpt(self) -> tuple[str, str]:
        """(url, short visible-text excerpt); tolerant of a navigation in progress."""
        for _ in range(2):
            try:
                text = await self._page.evaluate(
                    "(n) => document.body ? document.body.innerText.slice(0, n) : ''",
                    RESULT_EXCERPT_CHARS * 4,
                )
                return self._page.url, normalize_text(text, limit=RESULT_EXCERPT_CHARS)
            except PlaywrightError:
                await self._page.wait_for_timeout(500)
        return self._page.url, ""

    async def _entered_values(self) -> list[str]:
        """Text currently in the form's fields (page-supplied, used only to recognise requests)."""
        try:
            values = await self._page.evaluate(_ENTERED_VALUES_JS)
        except PlaywrightError:
            return []
        return [v for v in values if isinstance(v, str)] if isinstance(values, list) else []

    async def submit(self) -> SubmitResult:
        button = self._page.locator('[data-oa-submit="1"]')
        try:
            if await button.count() != 1:
                raise BrowserError("The submit button is no longer on the page.")
            if not self._scanned:
                raise BrowserError("The form must be scanned and reviewed before submitting.")
            # A page can change where a button posts after it was reviewed (or hide it behind
            # `formaction`). Re-read the destination now and refuse if it is not the one shown.
            current = await button.evaluate(_EFFECTIVE_ACTION_JS)
            if current != self._approved_action:
                raise BrowserError(
                    "The form's destination changed after you reviewed it, so nothing was sent."
                )
            approved_method = await button.evaluate(_EFFECTIVE_METHOD_JS)
            await self._page.evaluate(
                _INSTALL_SUBMIT_GUARD_JS,
                {"action": current, "method": approved_method},
            )
            before = self._page.url
            # Page scripts run between the click and the browser's own post, so the destination
            # is enforced on the wire, not just checked here: during this window a POST to any
            # site other than the confirmed one (redirects included) is aborted before it is
            # sent. The values entered in the form let the policy tell the real submission
            # from unrelated traffic (see RequestPolicy.submit_window).
            confirmed = self._approved_action or self._page.url
            markers = await self._entered_values()
            self._policy.add_markers(markers)
            with self._policy.submit_window(only_to=confirmed, markers=markers) as posts:
                await button.click(timeout=ACTION_TIMEOUT_MS)
                with contextlib.suppress(PlaywrightTimeout, PlaywrightError):
                    await self._page.wait_for_load_state("networkidle", timeout=SUBMIT_SETTLE_MS)
                await self._page.wait_for_timeout(500)
            blocked_submit = await self._page.evaluate(_SUBMIT_GUARD_RESULT_JS)
            if isinstance(blocked_submit, dict):
                attempted = str(blocked_submit.get("action") or "an unknown destination")
                outcome = (
                    "Nothing was sent."
                    if posts.sent == 0
                    else "Some requests did reach the confirmed site, but OpenApply cannot "
                    "confirm the application itself did; check the site before relying on it."
                )
                raise BrowserError(
                    f"The form changed its destination or method during the click to {attempted}; "
                    f"{outcome}"
                )
            after, excerpt = await self._excerpt()
        except PlaywrightTimeout as exc:
            raise BrowserError("Timed out clicking the submit button.") from exc
        except PlaywrightError as exc:
            raise _closed(exc) from exc
        blocked = tuple(dict.fromkeys(posts.blocked))
        if posts.unverifiable_upload:
            raise BrowserError(
                "The form contains a file that OpenApply did not attach, so it could not be "
                "sent safely. Attach files with the Edit option instead. Nothing was sent."
            )
        if posts.blocked_data and not posts.sent_form_navigation:
            # The form (or a copy of its data) was refused. A same-site beacon must not make
            # that look like success, so only the form's own navigation counts.
            outcome = (
                "Nothing was sent."
                if posts.sent == 0
                else "Some requests did reach the confirmed site, but OpenApply cannot confirm "
                "the application itself did; check the site before relying on it."
            )
            raise BrowserError(
                f"The page tried to send your data to {', '.join(blocked)}, which is not the "
                f"destination you confirmed. {outcome}"
            )
        if blocked and posts.sent == 0:
            raise BrowserError(
                f"The page tried to send data to {', '.join(blocked)}, which is not the "
                "destination you confirmed, and nothing reached the confirmed site."
            )
        return SubmitResult(
            url_before=before,
            url_after=after,
            navigated=after != before,
            excerpt=excerpt,
            blocked_hosts=blocked,
        )

    async def bring_to_front(self) -> None:
        with contextlib.suppress(PlaywrightError):
            await self._page.bring_to_front()


@contextlib.asynccontextmanager
async def open_form_session(
    url: str,
    *,
    allow_local: bool = False,
    headless: bool = False,
    timeout_ms: int = 30_000,
    profile_dir: Path | None = None,
) -> AsyncIterator[PlaywrightFormSession]:
    """Open ``url`` under the shared request policy.

    A supplied ``profile_dir`` reuses the dedicated OpenApply session; otherwise the context is
    ephemeral. Headed by default because manual application review remains supported.
    """
    try:
        url = validate_job_url(url, allow_local=allow_local)
    except UnsafeURLError as exc:
        raise BrowserError(str(exc)) from exc
    policy = RequestPolicy(
        allow_local=allow_local, block_types=SESSION_BLOCKED_TYPES, timeout_ms=timeout_ms
    )
    async with async_playwright() as playwright:
        context = None
        browser = None
        try:
            if profile_dir is not None:
                context, _label = await launch_persistent_browser(
                    playwright,
                    str(profile_dir),
                    headless=headless,
                )
            else:
                browser, _label = await launch_browser(playwright, headless=headless)
                context = await browser.new_context(
                    accept_downloads=False, permissions=[], service_workers="block"
                )
            await context.route("**/*", policy.route)
            await context.route_web_socket("**/*", policy.route_ws)
            page = await context.new_page()
            page.set_default_timeout(timeout_ms)
            try:
                await page.goto(url, wait_until="domcontentloaded")
            except PlaywrightTimeout as exc:
                raise BrowserError(f"Timed out loading {url}") from exc
            except PlaywrightError as exc:
                raise BrowserError(f"Could not load {url}: {first_line(exc)}") from exc
            with contextlib.suppress(PlaywrightTimeout):
                await page.wait_for_load_state("networkidle", timeout=SETTLE_TIMEOUT_MS)
            if not policy.allowed(page.url):
                raise BrowserError("The page redirected to a private/local address; refusing")
            yield PlaywrightFormSession(page, timeout_ms, policy)
        finally:
            if context is not None:
                with contextlib.suppress(PlaywrightError):
                    await context.close()
            if browser is not None:
                with contextlib.suppress(PlaywrightError):
                    await browser.close()
