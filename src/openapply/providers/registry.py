"""Registry of known providers: detection and lookup.

Fallback chains (codex -> claude -> ...) are intentionally not implemented in
v1. ``ProviderRegistry`` is the single place a future fallback policy would
plug in, driven by ``ProviderRateLimited`` / ``ProviderUnavailable`` errors.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

from openapply.config.settings import Settings
from openapply.providers.base import AgentProvider
from openapply.providers.claude import ClaudeProvider
from openapply.providers.codex import CodexProvider
from openapply.providers.detect_only import CopilotProvider, GeminiProvider
from openapply.providers.errors import ProviderNotInstalled
from openapply.providers.models import ProviderStatus
from openapply.providers.ollama import OllamaProvider


class ProviderRegistry:
    def __init__(self, providers: Iterable[AgentProvider], default: str | None = None) -> None:
        self._providers = {p.name: p for p in providers}
        self.default = default

    @classmethod
    def from_settings(cls, settings: Settings) -> ProviderRegistry:
        providers: list[AgentProvider] = [
            CodexProvider(model=settings.model_for("codex")),
            ClaudeProvider(model=settings.model_for("claude")),
            GeminiProvider(model=settings.model_for("gemini")),
            CopilotProvider(model=settings.model_for("copilot")),
            OllamaProvider(base_url=settings.ollama.base_url, model=settings.ollama.model),
        ]
        return cls(providers, default=settings.default_provider)

    @property
    def names(self) -> list[str]:
        return list(self._providers)

    def get(self, name: str) -> AgentProvider:
        try:
            return self._providers[name]
        except KeyError:
            raise ProviderNotInstalled(
                name, f"Unknown provider. Known: {', '.join(self.names)}"
            ) from None

    async def _status(self, provider: AgentProvider) -> ProviderStatus:
        installed = await provider.is_installed()
        available = installed and await provider.is_available()
        return ProviderStatus(
            name=provider.name,
            display_name=provider.display_name,
            installed=installed,
            available=available,
            version=await provider.get_version() if installed else None,
            detail=await provider.status_detail(),
            is_default=provider.name == self.default,
            supports_generation=provider.supports_generation,
        )

    async def detect(self) -> list[ProviderStatus]:
        return list(await asyncio.gather(*(self._status(p) for p in self._providers.values())))
