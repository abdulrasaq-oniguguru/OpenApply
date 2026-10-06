"""User settings persisted in ``config.toml``."""

from __future__ import annotations

import tomllib
from pathlib import Path

import tomli_w
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from openapply.config.atomic import atomic_write_text
from openapply.config.paths import config_path

DEFAULT_OLLAMA_URL = "http://localhost:11434"


# Keys written by earlier versions. Dropped on load (and so on the next save).
# Remove after one release.
_LEGACY_KEYS = ("provider_timeout_seconds",)


class ConfigError(Exception):
    """The config file exists but cannot be used."""


class OllamaSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = DEFAULT_OLLAMA_URL
    model: str | None = None


class Settings(BaseModel):
    """Everything here is non-secret by design: no tokens, ever."""

    model_config = ConfigDict(extra="forbid")

    default_provider: str | None = None
    models: dict[str, str] = Field(default_factory=dict)
    ollama: OllamaSettings = Field(default_factory=OllamaSettings)

    def model_for(self, provider: str) -> str | None:
        if provider == "ollama":
            return self.ollama.model
        return self.models.get(provider)

    def with_default(self, provider: str, model: str | None = None) -> Settings:
        update: dict[str, object] = {"default_provider": provider}
        if model is not None:
            if provider == "ollama":
                update["ollama"] = self.ollama.model_copy(update={"model": model})
            else:
                update["models"] = {**self.models, provider: model}
        return self.model_copy(update=update)


def load_settings(path: Path | None = None) -> Settings:
    target = path or config_path()
    if not target.exists():
        return Settings()
    try:
        data = tomllib.loads(target.read_text(encoding="utf-8"))
        for key in _LEGACY_KEYS:
            data.pop(key, None)
        return Settings.model_validate(data)
    except (tomllib.TOMLDecodeError, ValidationError) as exc:
        raise ConfigError(f"Invalid config file {target}: {exc}") from exc


def save_settings(settings: Settings, path: Path | None = None) -> Path:
    target = path or config_path()
    payload = settings.model_dump(mode="json", exclude_none=True)
    return atomic_write_text(target, tomli_w.dumps(payload))
