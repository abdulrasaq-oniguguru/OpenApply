from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
import uvicorn
from playwright.async_api import async_playwright

from openapply.candidate.service import CandidateService
from openapply.storage.database import Database
from openapply.storage.repositories import OpportunityRepository
from openapply.web.app import create_app
from openapply.worker.runner import Worker
from tests.applications.conftest import _start, _stop
from tests.jobs.builders import make_job, make_profile


@contextmanager
def _serve_web(database: Database) -> Iterator[str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    config = uvicorn.Config(
        create_app(database),
        host="127.0.0.1",
        port=port,
        log_level="error",
        lifespan="off",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [sock]},
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=2)
        sock.close()
        raise RuntimeError("test web server did not start")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()


async def test_reviewed_application_loop_in_google_chrome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAPPLY_HOME", str(tmp_path / "home"))
    CandidateService().save(make_profile())
    database = Database(tmp_path / "agent.db")
    fixture = _start()
    try:
        form_url = f"{fixture.url}/reviewed_application.html"
        opportunity_id = OpportunityRepository(database).save(
            make_job(source_url=form_url, application_url=form_url)
        )
        worker = Worker(database, owner="chrome-test-worker", allow_local=True)

        with _serve_web(database) as desk_url:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(channel="chrome", headless=True)
                try:
                    page = await browser.new_page(viewport={"width": 1440, "height": 1100})
                    await page.goto(desk_url, wait_until="networkidle")
                    await page.get_by_role("button", name="Prepare").click()
                    await (
                        page.locator("#task-list")
                        .get_by_text("prepare application", exact=False)
                        .wait_for()
                    )

                    assert await worker.run_once()
                    await page.reload(wait_until="networkidle")
                    await page.get_by_role("button", name="Review", exact=True).click()

                    dialog = page.locator("#detail-dialog")
                    why = dialog.locator(".detail-field").filter(
                        has_text="Why do you want this job?"
                    )
                    await why.locator("textarea").fill("I build careful, reliable Python services.")
                    await dialog.get_by_role("button", name="Save new revision").click()
                    await dialog.get_by_text("revision 2", exact=False).wait_for()

                    await dialog.locator("#authorize-confirm").check()
                    await dialog.get_by_role("button", name="Authorize revision").click()
                    await dialog.get_by_text("Authorized until", exact=False).wait_for()
                    await dialog.locator("#dispatch-confirm").check()
                    await dialog.get_by_role("button", name="Queue one submission").click()
                    await (
                        page.locator("#task-list")
                        .get_by_text("dispatch application", exact=False)
                        .wait_for()
                    )

                    assert await worker.run_once()
                    await page.reload(wait_until="networkidle")
                    ledger = page.locator("#ledger")
                    await ledger.get_by_text("submitted unverified", exact=False).wait_for()
                finally:
                    await browser.close()

        assert opportunity_id
        assert len(fixture.posts) == 1
        assert fixture.posts[0].path == "/reviewed-submit"
        assert fixture.posts[0].fields == {
            "first_name": ["Ada"],
            "email": ["ada@example.com"],
            "why": ["I build careful, reliable Python services."],
        }
    finally:
        _stop(fixture)
