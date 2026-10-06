"""Providers that are detected in Milestone 1 but cannot generate yet."""

from __future__ import annotations

from openapply.providers.base import CliProvider
from openapply.providers.errors import ProviderNotInstalled, ProviderUnavailable
from openapply.providers.models import AgentResponse


class _DetectOnlyProvider(CliProvider):
    supports_generation = False

    async def is_available(self) -> bool:
        # No verified, documented auth probe in M1: report installed only.
        return False

    async def status_detail(self) -> str:
        if not await self.is_installed():
            return "not installed"
        return "installed (generation not implemented yet)"

    async def generate(self, prompt: str, *, timeout: float | None = None) -> AgentResponse:
        if not await self.is_installed():
            raise ProviderNotInstalled(self.name, f"{self.executable} not found on PATH")
        raise ProviderUnavailable(self.name, "Generation is not implemented yet for this provider")


class GeminiProvider(_DetectOnlyProvider):
    name = "gemini"
    display_name = "Gemini CLI"
    executable = "gemini"


class CopilotProvider(_DetectOnlyProvider):
    name = "copilot"
    display_name = "GitHub Copilot CLI"
    executable = "copilot"
