from __future__ import annotations

import asyncio
import threading

import pytest
import uvicorn
from typer.testing import CliRunner

from openapply.cli.app import app
from openapply.worker.runner import Worker


def test_start_runs_desk_and_worker_then_stops_both(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_started = threading.Event()
    worker_stopped = threading.Event()
    server_ran = threading.Event()

    async def fake_worker(
        self: Worker, stop: threading.Event, *, poll_seconds: float = 5.0
    ) -> None:
        worker_started.set()
        await asyncio.to_thread(stop.wait)
        worker_stopped.set()

    class FakeServer:
        def __init__(self, config: object) -> None:
            self.should_exit = False

        def run(self) -> None:
            assert worker_started.wait(1)
            server_ran.set()

    monkeypatch.setattr(Worker, "run_until_stopped", fake_worker)
    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    monkeypatch.setattr(uvicorn, "Config", lambda *args, **kwargs: object())

    result = CliRunner().invoke(app, ["start", "--port", "8765"])

    assert result.exit_code == 0, result.output
    assert server_ran.is_set() and worker_stopped.is_set()
    assert "http://127.0.0.1:8765" in result.output
