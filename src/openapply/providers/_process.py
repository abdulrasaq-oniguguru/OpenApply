"""Safe async subprocess execution shared by CLI-backed providers."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from openapply.providers.errors import ProviderExecutionError, ProviderTimeout
from openapply.security.redact import redact

log = logging.getLogger("openapply.providers.process")


@dataclass(frozen=True)
class ProcessResult:
    stdout: str
    stderr: str
    return_code: int
    duration: float


ProcessRunner = Callable[..., Awaitable[ProcessResult]]


async def run_process(
    argv: Sequence[str],
    *,
    provider: str,
    stdin_text: str | None = None,
    timeout: float | None = None,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> ProcessResult:
    """Run ``argv`` without a shell. The prompt goes via stdin, never argv.

    Kills the child on timeout or cancellation. Only the executable name is
    logged, never arguments or output.
    """
    log.info("running %s", Path(argv[0]).name)
    start = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE if stdin_text is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=dict(env) if env is not None else None,
        )
    except OSError as exc:
        raise ProviderExecutionError(provider, f"Could not start process: {exc}") from exc

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(stdin_text.encode("utf-8") if stdin_text is not None else None),
            timeout,
        )
    except TimeoutError as exc:
        await _kill(proc)
        raise ProviderTimeout(provider, f"Timed out after {timeout}s") from exc
    except asyncio.CancelledError:
        await _kill(proc)
        raise

    duration = time.monotonic() - start
    result = ProcessResult(
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
        return_code=proc.returncode if proc.returncode is not None else -1,
        duration=duration,
    )
    log.debug("%s exited %s in %.2fs", Path(argv[0]).name, result.return_code, duration)
    log.debug("stderr (redacted): %s", redact(result.stderr[-300:]))
    return result


async def _kill(proc: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(ProcessLookupError):
        proc.kill()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(proc.wait(), 5)
