"""Exercise the real subprocess runner with harmless Python child processes."""

from __future__ import annotations

import asyncio
import io
import logging
import sys

import pytest

from openapply.logging_setup import RedactingFilter
from openapply.providers._process import run_process
from openapply.providers.errors import ProviderExecutionError, ProviderTimeout


async def test_captures_output_and_stdin() -> None:
    code = "import sys; print(sys.stdin.read().upper()); print('err', file=sys.stderr)"
    out = await run_process([sys.executable, "-c", code], provider="t", stdin_text="abc")
    assert out.stdout.strip() == "ABC"
    assert out.stderr.strip() == "err"
    assert out.return_code == 0


async def test_nonzero_exit_is_reported() -> None:
    out = await run_process([sys.executable, "-c", "import sys; sys.exit(7)"], provider="t")
    assert out.return_code == 7


async def test_timeout_kills_child() -> None:
    with pytest.raises(ProviderTimeout):
        await run_process(
            [sys.executable, "-c", "import time; time.sleep(30)"], provider="t", timeout=0.5
        )


async def test_cancellation_kills_child() -> None:
    task = asyncio.create_task(
        run_process([sys.executable, "-c", "import time; time.sleep(30)"], provider="t")
    )
    await asyncio.sleep(0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_missing_executable() -> None:
    with pytest.raises(ProviderExecutionError):
        await run_process(["definitely-not-a-real-binary-xyz"], provider="t")


async def test_credential_looking_output_is_not_logged() -> None:
    secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
    code = (
        "import sys;"
        f"print('Authorization: Bearer {secret}', file=sys.stderr);"
        f"print('password=hunter2hunter2', file=sys.stderr)"
    )
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactingFilter())
    logger = logging.getLogger("openapply.providers.process")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        await run_process([sys.executable, "-c", code], provider="t")
    finally:
        logger.removeHandler(handler)
    logged = stream.getvalue()
    assert "stderr (redacted)" in logged
    assert secret not in logged
    assert "hunter2" not in logged
