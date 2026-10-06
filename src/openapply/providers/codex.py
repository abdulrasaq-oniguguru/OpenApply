"""Codex CLI provider. Runs ``codex exec``; never touches Codex credentials."""

from __future__ import annotations

import tempfile
from pathlib import Path

from openapply.providers.base import CliProvider
from openapply.providers.errors import (
    ProviderAuthenticationRequired,
    ProviderExecutionError,
    ProviderNotInstalled,
    classify_failure,
)
from openapply.providers.models import AgentResponse


class CodexProvider(CliProvider):
    name = "codex"
    display_name = "Codex CLI"
    executable = "codex"

    async def is_available(self) -> bool:
        if not await self.is_installed():
            return False
        try:
            result = await self._run(["login", "status"], timeout=20)
        except Exception:
            return False
        return result.return_code == 0

    async def status_detail(self) -> str:
        if not await self.is_installed():
            return "not installed"
        return "installed / authenticated" if await self.is_available() else "login required"

    async def generate(self, prompt: str, *, timeout: float | None = None) -> AgentResponse:
        if not await self.is_installed():
            raise ProviderNotInstalled(self.name, "codex executable not found on PATH")

        # Read-only sandbox, no session persistence, empty working directory:
        # untrusted job text can never reach files or tools through this call.
        with tempfile.TemporaryDirectory(prefix="openapply-codex-") as tmp:
            workdir = Path(tmp)
            out_file = workdir / "last_message.txt"
            args = [
                "exec",
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
                "--ephemeral",
                "--ignore-user-config",
                "--ignore-rules",
                "--color",
                "never",
                "-o",
                str(out_file),
            ]
            if self.model:
                args += ["-m", self.model]
            args.append("-")  # prompt from stdin

            result = await self._run(args, stdin_text=prompt, timeout=timeout, cwd=workdir)
            text = ""
            if out_file.exists():
                text = out_file.read_text(encoding="utf-8", errors="replace").strip()

        if result.return_code != 0:
            raise classify_failure(self.name, result.stderr or result.stdout, result.return_code)
        if not text:
            if "login" in result.stderr.lower():
                raise ProviderAuthenticationRequired(self.name, "Codex login required")
            raise ProviderExecutionError(
                self.name, "Codex returned no message", return_code=result.return_code
            )
        return AgentResponse(
            text=text,
            provider=self.name,
            model=self.model,
            raw_output=text,
            duration=result.duration,
            return_code=result.return_code,
        )
