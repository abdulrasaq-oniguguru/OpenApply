"""Playwright-backed page fetcher.

Hardening choices (this is the component that reads untrusted web pages):

* a fresh, ephemeral context per fetch: no cookies, no saved logins, no user profile
* images, media and fonts are not downloaded; downloads are disabled; no permissions
* every request goes through the shared ``RequestPolicy`` (see ``policy.py``)
* only *visible* text is read (``innerText``), plus JobPosting JSON-LD, size-capped
"""

from __future__ import annotations

import logging

from playwright.async_api import (
    Browser,
    Page,
    Playwright,
    async_playwright,
)
from playwright.async_api import (
    Error as PlaywrightError,
)
from playwright.async_api import (
    TimeoutError as PlaywrightTimeout,
)

from openapply.browser.page import (
    MAX_JSON_LD_BLOCK_CHARS,
    MAX_JSON_LD_BLOCKS,
    MAX_PAGE_CHARS,
    BrowserError,
    BrowserNotInstalled,
    FetchedPage,
    PageLink,
    normalize_text,
    parse_json_ld,
)
from openapply.browser.policy import RequestPolicy, first_line
from openapply.security.urls import UnsafeURLError, validate_job_url

log = logging.getLogger("openapply.browser")

NAVIGATION_TIMEOUT_MS = 30_000
SETTLE_TIMEOUT_MS = 5_000
SHORT_PAGE_CHARS = 300  # below this we assume a JS app is still rendering
BLOCKED_RESOURCE_TYPES = frozenset({"image", "media", "font"})

# Limits are applied *inside the page*, so oversized data is never transferred to Python.
_VISIBLE_TEXT_JS = "(max) => document.body ? document.body.innerText.slice(0, max) : ''"
_JSON_LD_JS = """({blocks, chars}) => Array.from(
    document.querySelectorAll('script[type="application/ld+json"]')
).slice(0, blocks).map(s => s.textContent || '').filter(t => t.length <= chars)"""
MAX_LINKS = 300
MAX_LINK_CHARS = 500
_SEARCH_INPUT_SELECTORS = (
    'input[type="search"]',
    'input[placeholder*="search" i]',
    'input[aria-label*="search" i]',
)
_LINKS_JS = """({limit, chars}) => Array.from(document.querySelectorAll('a[href]')).slice(0, limit)
    .map(a => [(a.innerText || '').trim().slice(0, 80), a.href])
    .filter(([, h]) => h.length <= chars)"""
_INSTALL_HINT = (
    "No usable browser found (Playwright Chromium, Google Chrome or Microsoft Edge). "
    "Run `openapply browser install`."
)
# Tried in order. Reusing an already-installed Chrome/Edge means no download is needed.
_BROWSER_CHOICES: tuple[tuple[str | None, str], ...] = (
    (None, "Playwright Chromium"),
    ("chrome", "Google Chrome"),
    ("msedge", "Microsoft Edge"),
)
_MISSING_MARKERS = ("Executable doesn't exist", "is not found at", "playwright install")


def _is_missing_browser(exc: Exception) -> bool:
    text = str(exc)
    return any(marker in text for marker in _MISSING_MARKERS)


async def launch_browser(playwright: Playwright, *, headless: bool = True) -> tuple[Browser, str]:
    """Launch the first available Chromium-family browser; return it with a display name."""
    for channel, label in _BROWSER_CHOICES:
        try:
            browser = await playwright.chromium.launch(headless=headless, channel=channel)
        except PlaywrightError as exc:
            if _is_missing_browser(exc):
                log.debug("%s not available", label)
                continue
            raise BrowserError(f"Could not start {label}: {first_line(exc)}") from exc
        log.info("using %s", label)
        return browser, label
    raise BrowserNotInstalled(_INSTALL_HINT)


class PlaywrightFetcher:
    """``PageFetcher`` implementation using headless Chromium."""

    def __init__(
        self,
        *,
        allow_local: bool = False,
        timeout_ms: int = NAVIGATION_TIMEOUT_MS,
        headless: bool = True,
    ) -> None:
        self._allow_local = allow_local
        self._timeout_ms = timeout_ms
        self._headless = headless
        self._policy = RequestPolicy(
            allow_local=allow_local, block_types=BLOCKED_RESOURCE_TYPES, timeout_ms=timeout_ms
        )

    async def fetch(self, url: str) -> FetchedPage:
        return await self._fetch(url)

    async def fetch_with_query(self, url: str, query: str) -> FetchedPage:
        """Fetch a listing page after filling its public search box when available."""
        return await self._fetch(url, search_query=query)

    async def _fetch(self, url: str, *, search_query: str | None = None) -> FetchedPage:
        try:
            url = validate_job_url(url, allow_local=self._allow_local)
        except UnsafeURLError as exc:
            raise BrowserError(str(exc)) from exc

        async with async_playwright() as playwright:
            browser, _label = await launch_browser(playwright, headless=self._headless)
            try:
                return await self._load(browser, url, search_query=search_query)
            finally:
                await browser.close()

    async def _load(
        self, browser: Browser, url: str, *, search_query: str | None = None
    ) -> FetchedPage:
        context = await browser.new_context(
            accept_downloads=False,
            permissions=[],
            service_workers="block",
        )
        try:
            await context.route("**/*", self._policy.route)
            await context.route_web_socket("**/*", self._policy.route_ws)
            page = await context.new_page()
            page.set_default_timeout(self._timeout_ms)
            try:
                await page.goto(url, wait_until="domcontentloaded")
            except PlaywrightTimeout as exc:
                raise BrowserError(f"Timed out loading {url}") from exc
            except PlaywrightError as exc:
                raise BrowserError(f"Could not load {url}: {first_line(exc)}") from exc

            await self._settle(page)
            if search_query:
                await self._filter_listing(page, search_query)
            # Redirects are followed by the policy, so the page keeps the address it was asked
            # for; the policy remembers where that navigation really ended up.
            final_url = self._policy.final_url_for(page.url)
            if not self._policy.allowed(final_url):
                raise BrowserError("The page redirected to a private/local address; refusing")

            text = normalize_text(await page.evaluate(_VISIBLE_TEXT_JS, MAX_PAGE_CHARS))
            if len(text) < SHORT_PAGE_CHARS:  # client-side rendered page: give it a moment
                await page.wait_for_timeout(2_000)
                text = normalize_text(await page.evaluate(_VISIBLE_TEXT_JS, MAX_PAGE_CHARS))
            blocks = await page.evaluate(
                _JSON_LD_JS, {"blocks": MAX_JSON_LD_BLOCKS, "chars": MAX_JSON_LD_BLOCK_CHARS}
            )
            raw_links = await page.evaluate(
                _LINKS_JS, {"limit": MAX_LINKS, "chars": MAX_LINK_CHARS}
            )
            return FetchedPage(
                url=url,
                final_url=final_url,
                title=(await page.title()).strip() or None,
                text=text,
                json_ld=parse_json_ld([b for b in blocks if isinstance(b, str)]),
                links=[
                    PageLink(text=t, href=h)
                    for t, h in raw_links
                    if isinstance(t, str) and isinstance(h, str)
                ],
                person_ld=parse_json_ld([b for b in blocks if isinstance(b, str)], "person"),
            )
        except PlaywrightError as exc:
            raise BrowserError(f"Reading the page failed: {first_line(exc)}") from exc
        finally:
            await context.close()

    async def _filter_listing(self, page: Page, query: str) -> None:
        for selector in _SEARCH_INPUT_SELECTORS:
            field = page.locator(selector).first
            if not await field.count():
                continue
            try:
                await field.fill(query)
                await page.wait_for_timeout(1_000)
                await self._settle(page)
            except (PlaywrightError, PlaywrightTimeout):
                log.debug("listing search field could not be used; continuing unfiltered")
            return

    async def _settle(self, page: Page) -> None:
        try:
            await page.wait_for_load_state("networkidle", timeout=SETTLE_TIMEOUT_MS)
        except PlaywrightTimeout:
            log.debug("page did not reach network idle; continuing")


async def chromium_status() -> tuple[bool, str]:
    """(usable, detail): launches the browser briefly to prove it really works."""
    try:
        async with async_playwright() as playwright:
            browser, label = await launch_browser(playwright)
            await browser.close()
            return True, f"{label} ready"
    except BrowserNotInstalled:
        return False, "No browser found. Run `openapply browser install`"
    except (BrowserError, PlaywrightError) as exc:
        return False, f"Browser problem: {first_line(exc)}"
