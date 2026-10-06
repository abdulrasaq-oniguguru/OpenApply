"""Claude Code provider. Runs ``claude -p``; never touches Claude credentials."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from openapply.providers.base import CliProvider
from openapply.providers.errors import (
    ProviderExecutionError,
    ProviderNotInstalled,
    classify_failure,
)
from openapply.providers.models import AgentResponse

_SAFE_METADATA = ("duration_ms", "num_turns", "stop_reason")


class ClaudeProvider(CliProvider):
    name = "claude"
    display_name = "Claude Code"
    executable = "claude"

    async def is_available(self) -> bool:
        if not await self.is_installed():
            return False
        try:
            result = await self._run(["auth", "status"], timeout=20)
        except Exception:
            return False
        if result.return_code != 0:
            return False
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return False
        # Only the boolean is read; the rest of the payload (email, org) is dropped.
        return isinstance(data, dict) and data.get("loggedIn") is True

    async def status_detail(self) -> str:
        if not await self.is_installed():
            return "not installed"
        return "installed / authenticated" if await self.is_available() else "login required"

    async def generate(self, prompt: str, *, timeout: float | None = None) -> AgentResponse:
        if not await self.is_installed():
            raise ProviderNotInstalled(self.name, "claude executable not found on PATH")

        # All tools disabled and no persistence: the model can only return text.
        args = [
            "-p",
            "--output-format",
            "json",
            "--tools",
            "",
            "--no-session-persistence",
            "--disable-slash-commands",
        ]
        if self.model:
            args += ["--model", self.model]

        with tempfile.TemporaryDirectory(prefix="openapply-claude-") as tmp:
            result = await self._run(args, stdin_text=prompt, timeout=timeout, cwd=Path(tmp))

        parsed = _parse_json(result.stdout)
        if result.return_code != 0 or (parsed and parsed.get("is_error")):
            detail = str(parsed.get("result", "")) if parsed else ""
            raise classify_failure(
                self.name, detail or result.stderr or result.stdout, result.return_code
            )
        if parsed is None:
            raise ProviderExecutionError(
                self.name, "Claude returned malformed JSON output", return_code=result.return_code
            )
        text = parsed.get("result")
        if not isinstance(text, str) or not text.strip():
            raise ProviderExecutionError(
                self.name, "Claude JSON output had no result text", return_code=result.return_code
            )
        usage = parsed.get("modelUsage")
        model = next(iter(usage), None) if isinstance(usage, dict) and usage else self.model
        return AgentResponse(
            text=text.strip(),
            provider=self.name,
            model=model,
            raw_output=None,  # envelope holds session_id etc.; keep only text
            duration=result.duration,
            return_code=result.return_code,
            metadata={k: parsed[k] for k in _SAFE_METADATA if k in parsed},
        )


def _parse_json(stdout: str) -> dict[str, Any] | None:
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
