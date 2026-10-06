"""Test doubles for subprocess execution."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from openapply.providers._process import ProcessResult
from openapply.providers.base import AgentProvider
from openapply.providers.models import AgentResponse


class FakeRunner:
    """Records calls and returns scripted results. Never starts a process."""

    def __init__(
        self,
        handler: Callable[[list[str], str | None], ProcessResult | Exception],
    ) -> None:
        self._handler = handler
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self,
        argv: list[str],
        *,
        provider: str,
        stdin_text: str | None = None,
        timeout: float | None = None,
        cwd: Path | None = None,
        env: Any = None,
    ) -> ProcessResult:
        self.calls.append({"argv": argv, "stdin": stdin_text, "cwd": cwd, "timeout": timeout})
        outcome = self._handler(argv, stdin_text)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def result(
    stdout: str = "", stderr: str = "", code: int = 0, duration: float = 0.1
) -> ProcessResult:
    return ProcessResult(stdout=stdout, stderr=stderr, return_code=code, duration=duration)


def present(name: str) -> Callable[[str], str | None]:
    return lambda exe: f"/usr/bin/{exe}" if exe == name else None


def absent(_: str) -> str | None:
    return None


class ScriptedProvider(AgentProvider):
    """Provider double: replies from a script, records prompts. Never touches a real tool."""

    name = "scripted"
    display_name = "Scripted"

    def __init__(self, replies: list[str | Exception]) -> None:
        self._replies = list(replies)
        self.prompts: list[str] = []

    async def is_installed(self) -> bool:
        return True

    async def get_version(self) -> str | None:
        return "0"

    async def is_available(self) -> bool:
        return True

    async def generate(self, prompt: str, *, timeout: float | None = None) -> AgentResponse:
        self.prompts.append(prompt)
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return AgentResponse(text=reply, provider=self.name, model="test-model")
