from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from openapply.candidate.service import CandidateService
from openapply.cli.app import app
from openapply.config.settings import load_settings
from openapply.providers.models import ProviderStatus
from openapply.providers.registry import ProviderRegistry
from openapply.storage.repositories import OpportunityRepository
from tests.jobs.builders import make_job, make_profile

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


def test_tools_json_is_agent_readable() -> None:
    result = runner.invoke(app, ["tools", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema"] == "openapply-tools/v1"
    commands = {item["command"] for item in payload["tools"]}
    assert "openapply start" in commands
    assert "openapply worker list --json" in commands
    assert "openapply worker platforms --json" in commands
    assert all(item["access"] for item in payload["tools"])


def test_worker_platforms_json_lists_public_sources() -> None:
    result = runner.invoke(app, ["worker", "platforms", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema"] == "openapply-platforms/v1"
    sources = {item["key"]: item for item in payload["platforms"]}
    assert sources["himalayas"]["approach"] == "public_json_api"
    assert sources["we-work-remotely"]["approach"] == "public_rss"
    assert sources["outlier"]["approach"] == "public_page"


def test_worker_discover_platform_defaults_to_profile_role() -> None:
    CandidateService().save(make_profile())

    result = runner.invoke(app, ["worker", "discover-platform", "remotive", "--json"])

    assert result.exit_code == 0, result.output
    task = json.loads(result.output)
    assert task["type"] == "discover_platform"
    assert task["payload"] == {
        "platform": "remotive",
        "query": "Backend Engineer",
    }


def test_worker_list_json_exposes_task_state() -> None:
    queued = runner.invoke(app, ["worker", "add-job", "https://example.com/jobs/1", "--json"])
    assert queued.exit_code == 0, queued.output
    task_id = json.loads(queued.output)["id"]

    result = runner.invoke(app, ["worker", "list", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema"] == "openapply-tasks/v1"
    assert payload["tasks"][0]["id"] == task_id
    assert payload["tasks"][0]["state"] == "queued"


def test_worker_rematch_refreshes_saved_scores_without_ai() -> None:
    CandidateService().save(make_profile())
    repository = OpportunityRepository()
    opportunity_id = repository.save(
        make_job(
            title="General Physician",
            requirements=["3+ years of professional experience"],
            preferred_requirements=[],
        )
    )

    result = runner.invoke(app, ["worker", "rematch", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["updated"] == 1
    stored = repository.get(opportunity_id)["match"]
    assert isinstance(stored, dict)
    assert stored["overall_score"] <= 25
    assert stored["recommendation"] == "do_not_apply"


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
