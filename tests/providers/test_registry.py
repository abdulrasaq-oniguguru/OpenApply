from __future__ import annotations

import pytest

from openapply.config.settings import Settings
from openapply.providers.detect_only import GeminiProvider
from openapply.providers.errors import ProviderNotInstalled, ProviderUnavailable
from openapply.providers.registry import ProviderRegistry
from tests.providers.fakes import FakeRunner, absent, present, result


async def test_detect_reports_each_provider_and_default() -> None:
    registry = ProviderRegistry.from_settings(Settings(default_provider="codex"))
    statuses = {s.name: s for s in await registry.detect()}
    assert set(statuses) == {"codex", "claude", "gemini", "copilot", "ollama"}
    assert statuses["codex"].is_default is True
    assert sum(s.is_default for s in statuses.values()) == 1


async def test_unknown_provider_lookup() -> None:
    registry = ProviderRegistry.from_settings(Settings())
    with pytest.raises(ProviderNotInstalled):
        registry.get("nope")


async def test_detect_only_provider_cannot_generate() -> None:
    gemini = GeminiProvider(
        runner=FakeRunner(lambda a, s: result("1.0\n")), which=present("gemini")
    )
    assert await gemini.is_installed()
    assert await gemini.get_version() == "1.0"
    assert gemini.supports_generation is False
    with pytest.raises(ProviderUnavailable):
        await gemini.generate("hi")

    missing = GeminiProvider(which=absent)
    with pytest.raises(ProviderNotInstalled):
        await missing.generate("hi")
