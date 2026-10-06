from __future__ import annotations

from pathlib import Path

import pytest

from openapply.config.paths import app_dir, config_path
from openapply.config.settings import ConfigError, Settings, load_settings, save_settings


def test_paths_follow_env_override(isolated_home: Path) -> None:
    assert app_dir() == isolated_home
    assert config_path() == isolated_home / "config.toml"


def test_missing_config_gives_defaults() -> None:
    settings = load_settings()
    assert settings.default_provider is None
    assert settings.ollama.base_url == "http://localhost:11434"


def test_round_trip_with_models() -> None:
    settings = Settings().with_default("ollama", "qwen3.5:9b").with_default("codex", "gpt-x")
    path = save_settings(settings)
    assert path.exists()
    loaded = load_settings()
    assert loaded.default_provider == "codex"
    assert loaded.model_for("ollama") == "qwen3.5:9b"
    assert loaded.model_for("codex") == "gpt-x"


def test_invalid_config_raises_clear_error() -> None:
    path = config_path()
    path.parent.mkdir(parents=True)
    path.write_text("default_provider = [", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings()


def test_unknown_keys_are_rejected() -> None:
    path = config_path()
    path.parent.mkdir(parents=True)
    path.write_text('api_key = "nope"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings()


def test_concurrent_saves_never_collide(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    target = tmp_path / "cfg" / "config.toml"
    providers = ["codex", "claude", "ollama", "codex"] * 6
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(lambda name: save_settings(Settings(default_provider=name), target), providers)
        )
    assert load_settings(target).default_provider in {"codex", "claude", "ollama"}
    assert [p.name for p in target.parent.iterdir()] == ["config.toml"]  # no temp litter


def test_legacy_timeout_key_is_discarded_and_not_rewritten() -> None:
    path = config_path()
    path.parent.mkdir(parents=True)
    path.write_text(
        'default_provider = "codex"\nprovider_timeout_seconds = 300.0\n', encoding="utf-8"
    )
    settings = load_settings()
    assert settings.default_provider == "codex"
    save_settings(settings)
    assert "provider_timeout_seconds" not in path.read_text(encoding="utf-8")
