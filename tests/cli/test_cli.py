from __future__ import annotations

import pytest
from typer.testing import CliRunner

from openapply.cli.app import app
from openapply.config.settings import load_settings
from openapply.providers.models import ProviderStatus
from openapply.providers.registry import ProviderRegistry

runner = CliRunner()


def _status(name: str, installed: bool, available: bool, generation: bool = True) -> ProviderStatus:
    return ProviderStatus(
        name=name,
        display_name=name.title(),
        installed=installed,
        available=available,
        detail="ok" if available else "not installed",
        supports_generation=generation,
    )


@pytest.fixture
def fake_detect(monkeypatch: pytest.MonkeyPatch) -> list[ProviderStatus]:
    statuses = [_status("codex", True, True), _status("gemini", False, False)]

    async def detect(self: ProviderRegistry) -> list[ProviderStatus]:
        return statuses

    monkeypatch.setattr(ProviderRegistry, "detect", detect)
    return statuses


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "openapply" in result.output


def test_doctor_succeeds_with_a_usable_provider(fake_detect: list[ProviderStatus]) -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "OpenApply System Check" in result.output
    assert "Codex" in result.output


def test_doctor_fails_without_a_usable_provider(
    fake_detect: list[ProviderStatus],
) -> None:
    fake_detect[:] = [_status("codex", False, False)]
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "No usable AI provider" in result.output


def test_providers_list(fake_detect: list[ProviderStatus]) -> None:
    result = runner.invoke(app, ["providers", "list"])
    assert result.exit_code == 0
    assert "codex" in result.output
    assert "gemini" in result.output


def test_set_default_persists(monkeypatch: pytest.MonkeyPatch) -> None:
    async def installed(self: object) -> bool:
        return True

    monkeypatch.setattr("openapply.providers.codex.CodexProvider.is_installed", installed)
    result = runner.invoke(app, ["providers", "set-default", "codex", "--model", "gpt-x"])
    assert result.exit_code == 0, result.output
    settings = load_settings()
    assert settings.default_provider == "codex"
    assert settings.model_for("codex") == "gpt-x"


def test_set_default_rejects_unknown_provider() -> None:
    result = runner.invoke(app, ["providers", "set-default", "bogus"])
    assert result.exit_code == 2
    assert load_settings().default_provider is None


def test_set_default_rejects_uninstalled_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    async def missing(self: object) -> bool:
        return False

    monkeypatch.setattr("openapply.providers.claude.ClaudeProvider.is_installed", missing)
    result = runner.invoke(app, ["providers", "set-default", "claude"])
    assert result.exit_code == 1
    assert load_settings().default_provider is None


def test_set_default_rejects_detect_only_provider() -> None:
    result = runner.invoke(app, ["providers", "set-default", "gemini"])
    assert result.exit_code == 2


@pytest.mark.parametrize("args", [["providers", "list"], ["providers", "set-default", "codex"]])
def test_providers_commands_report_invalid_config_cleanly(args: list[str]) -> None:
    from openapply.config.paths import config_path

    path = config_path()
    path.parent.mkdir(parents=True)
    path.write_text("default_provider = [", encoding="utf-8")
    result = runner.invoke(app, args)
    assert result.exit_code == 2
    assert "Invalid config file" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
