"""Ollama provider using the documented local HTTP API (no cloud credentials involved)."""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from typing import Any

import httpx

from openapply.config.settings import DEFAULT_OLLAMA_URL
from openapply.providers.base import AgentProvider
from openapply.providers.errors import (
    ProviderExecutionError,
    ProviderNotInstalled,
    ProviderTimeout,
    ProviderUnavailable,
)
from openapply.providers.models import AgentResponse


class OllamaProvider(AgentProvider):
    name = "ollama"
    display_name = "Ollama"

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_OLLAMA_URL,
        model: str | None = None,
        client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client_factory = client_factory
        self._which = which

    def _client(self, timeout: float | None) -> httpx.AsyncClient:
        return self._client_factory(base_url=self.base_url, timeout=timeout)

    async def is_installed(self) -> bool:
        if self._which("ollama") is not None:
            return True
        # A remote/containerised daemon counts as installed if it answers.
        return await self._fetch_version() is not None

    async def _fetch_version(self) -> str | None:
        try:
            async with self._client(3.0) as client:
                response = await client.get("/api/version")
            response.raise_for_status()
            version = response.json().get("version")
        except (httpx.HTTPError, ValueError, AttributeError):
            return None
        return version if isinstance(version, str) else None

    async def get_version(self) -> str | None:
        return await self._fetch_version()

    async def list_models(self) -> list[str]:
        try:
            async with self._client(5.0) as client:
                response = await client.get("/api/tags")
            response.raise_for_status()
            models = response.json().get("models", [])
        except (httpx.HTTPError, ValueError, AttributeError):
            return []
        return [m["name"] for m in models if isinstance(m, dict) and "name" in m]

    async def is_available(self) -> bool:
        return await self._fetch_version() is not None

    async def status_detail(self) -> str:
        if not await self.is_installed():
            return "not installed"
        if not await self.is_available():
            return f"not running at {self.base_url}"
        count = len(await self.list_models())
        return f"running, {count} model(s)"

    async def generate(self, prompt: str, *, timeout: float | None = None) -> AgentResponse:
        if not await self.is_installed():
            raise ProviderNotInstalled(self.name, "Ollama not found and not reachable")
        model = self.model
        if model is None:
            raise ProviderUnavailable(
                self.name, "No Ollama model configured; use `providers set-default ollama --model`"
            )
        start = time.monotonic()
        try:
            async with self._client(timeout) as client:
                response = await client.post(
                    "/api/generate", json={"model": model, "prompt": prompt, "stream": False}
                )
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(self.name, f"Timed out after {timeout}s") from exc
        except httpx.ConnectError as exc:
            raise ProviderUnavailable(self.name, f"Ollama not running at {self.base_url}") from exc
        except httpx.HTTPError as exc:
            raise ProviderExecutionError(self.name, f"HTTP error: {exc}") from exc

        if response.status_code != 200:
            raise ProviderExecutionError(
                self.name,
                f"HTTP {response.status_code}: {response.text[:300]}",
                return_code=response.status_code,
            )
        try:
            data: dict[str, Any] = response.json()
            text = data["response"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderExecutionError(self.name, "Malformed Ollama response") from exc
        if not isinstance(text, str) or not text.strip():
            raise ProviderExecutionError(self.name, "Ollama returned an empty response")
        return AgentResponse(
            text=text.strip(),
            provider=self.name,
            model=model,
            raw_output=response.text,
            duration=time.monotonic() - start,
            return_code=0,
            metadata={k: data[k] for k in ("total_duration", "eval_count") if k in data},
        )
