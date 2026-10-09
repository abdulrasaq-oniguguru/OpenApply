"""One dedicated, reusable browser profile for logins across job sites.

OpenApply never collects credentials. A person enters them directly into the visible browser,
then closes that browser so a worker can later reuse the site's own cookies. Metadata contains
only state and the public origin—not cookies, passwords, URL queries, or page contents.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Route, WebSocketRoute, async_playwright

from openapply.browser.browser import launch_persistent_browser
from openapply.browser.page import BrowserError
from openapply.config.atomic import atomic_write_text, ensure_private_dir
from openapply.config.paths import browser_profile_dir, browser_session_path
from openapply.security.urls import UnsafeURLError, is_private_host, validate_job_url

DEFAULT_LOGIN_URL = "https://accounts.google.com/"


@dataclass(frozen=True)
class BrowserSessionStatus:
    state: str = "empty"  # empty, opening, ready, error
    origin: str | None = None
    updated_at: str | None = None
    pid: int | None = None
    detail: str | None = None

    @property
    def ready(self) -> bool:
        return self.state == "ready"


def _safe_origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}/"


def _process_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class BrowserSessionStore:
    def __init__(
        self,
        *,
        profile_dir: Path | None = None,
        metadata_path: Path | None = None,
    ) -> None:
        self.profile_dir = profile_dir or browser_profile_dir()
        self.metadata_path = metadata_path or browser_session_path()

    def status(self) -> BrowserSessionStatus:
        if not self.metadata_path.is_file():
            return BrowserSessionStatus()
        try:
            data = json.loads(self.metadata_path.read_text(encoding="utf-8"))
            status = BrowserSessionStatus(
                state=str(data.get("state", "empty")),
                origin=str(data["origin"]) if data.get("origin") else None,
                updated_at=str(data["updated_at"]) if data.get("updated_at") else None,
                pid=int(data["pid"]) if data.get("pid") else None,
                detail=str(data["detail"])[:300] if data.get("detail") else None,
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return BrowserSessionStatus(state="error", detail="Session metadata is unreadable.")
        if status.state == "opening" and not _process_alive(status.pid):
            return BrowserSessionStatus(
                state="error",
                origin=status.origin,
                updated_at=status.updated_at,
                detail="The login browser stopped before its session was saved.",
            )
        return status

    def write(self, status: BrowserSessionStatus) -> None:
        ensure_private_dir(self.metadata_path.parent)
        atomic_write_text(
            self.metadata_path,
            json.dumps(asdict(status), ensure_ascii=False, sort_keys=True),
            private=True,
        )

    def mark_opening(self, url: str, *, pid: int | None = None) -> None:
        self.write(
            BrowserSessionStatus(
                state="opening",
                origin=_safe_origin(url),
                updated_at=datetime.now(UTC).isoformat(),
                pid=pid or os.getpid(),
                detail="Sign in directly in the visible browser, then close its window.",
            )
        )

    def mark_ready(self, url: str) -> None:
        self.write(
            BrowserSessionStatus(
                state="ready",
                origin=_safe_origin(url),
                updated_at=datetime.now(UTC).isoformat(),
                detail="Dedicated browser profile saved. Login is verified when a site is used.",
            )
        )

    def mark_error(self, url: str, detail: str) -> None:
        self.write(
            BrowserSessionStatus(
                state="error",
                origin=_safe_origin(url),
                updated_at=datetime.now(UTC).isoformat(),
                detail=detail[:300],
            )
        )

    def clear(self) -> None:
        """Delete only OpenApply's dedicated profile and its non-secret status metadata."""
        if self.status().state == "opening":
            raise BrowserError("Close the OpenApply login browser before forgetting its session.")
        target = self.profile_dir.resolve()
        parent = self.metadata_path.parent.resolve()
        if target.parent != parent or target.name != "browser-profile":
            raise BrowserError("Refusing to delete an unexpected browser-profile location.")
        if target.is_symlink():
            raise BrowserError("Refusing to delete a linked browser profile.")
        if target.is_dir():
            shutil.rmtree(target)
        with contextlib.suppress(FileNotFoundError):
            self.metadata_path.unlink()


async def _public_route(route: Route) -> None:
    parts = urlsplit(route.request.url)
    if parts.scheme in {"http", "https"} and is_private_host(parts.hostname or ""):
        await route.abort()
        return
    await route.continue_()


async def _public_websocket(route: WebSocketRoute) -> None:
    parts = urlsplit(route.url)
    if is_private_host(parts.hostname or ""):
        with contextlib.suppress(PlaywrightError):
            await route.close(code=1008, reason="blocked by OpenApply")
        return
    route.connect_to_server()


async def open_login_browser(
    url: str = DEFAULT_LOGIN_URL, *, store: BrowserSessionStore | None = None
) -> None:
    """Open a visible reusable profile and wait until the person closes the browser."""
    store = store or BrowserSessionStore()
    try:
        url = validate_job_url(url)
    except UnsafeURLError as exc:
        raise BrowserError(str(exc)) from exc
    ensure_private_dir(store.profile_dir)
    store.mark_opening(url)
    try:
        async with async_playwright() as playwright:
            context, _label = await launch_persistent_browser(
                playwright,
                str(store.profile_dir),
                headless=False,
                service_workers="allow",
            )
            await context.route("**/*", _public_route)
            await context.route_web_socket("**/*", _public_websocket)
            pages = context.pages
            page = pages[0] if pages else await context.new_page()
            await page.goto(url, wait_until="domcontentloaded")
            with contextlib.suppress(PlaywrightError):
                await context.wait_for_event("close")
        store.mark_ready(url)
    except (BrowserError, PlaywrightError) as exc:
        store.mark_error(url, str(exc))
        raise


def run_login_browser(url: str = DEFAULT_LOGIN_URL) -> None:
    asyncio.run(open_login_browser(url))


def start_login_process(
    url: str = DEFAULT_LOGIN_URL, *, store: BrowserSessionStore | None = None
) -> BrowserSessionStatus:
    """Start the visible login browser without blocking the local web server."""
    store = store or BrowserSessionStore()
    try:
        url = validate_job_url(url)
    except UnsafeURLError as exc:
        raise BrowserError(str(exc)) from exc
    current = store.status()
    if current.state == "opening":
        raise BrowserError("The OpenApply login browser is already open.")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(  # noqa: S603 - fixed local module; URL was validated above
        [sys.executable, "-m", "openapply.cli.app", "browser", "login", url],
        close_fds=True,
        creationflags=creationflags,
    )
    store.mark_opening(url, pid=process.pid)
    return store.status()
