from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from openapply.providers.errors import (
    ProviderExecutionError,
    ProviderNotInstalled,
    ProviderTimeout,
    ProviderUnavailable,
)
from openapply.providers.ollama import OllamaProvider
from tests.providers.fakes import absent, present


def _provider(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    model: str | None = "m1",
    cli: bool = True,
) -> OllamaProvider:
    transport = httpx.MockTransport(handler)

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport, **kwargs)

    return OllamaProvider(
        model=model, client_factory=factory, which=present("ollama") if cli else absent
    )


def _ok_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/version":
        return httpx.Response(200, json={"version": "0.9.0"})
    if request.url.path == "/api/tags":
        return httpx.Response(200, json={"models": [{"name": "m1"}, {"name": "m2"}]})
    if request.url.path == "/api/generate":
        return httpx.Response(200, json={"response": " hi there ", "eval_count": 3})
    return httpx.Response(404)


async def test_detection_and_models() -> None:
    provider = _provider(_ok_handler)
    assert await provider.is_installed()
    assert await provider.is_available()
    assert await provider.get_version() == "0.9.0"
    assert await provider.list_models() == ["m1", "m2"]


async def test_generate_success() -> None:
    response = await _provider(_ok_handler).generate("hello")
    assert response.text == "hi there"
    assert response.model == "m1"
    assert response.metadata == {"eval_count": 3}


async def test_not_running_is_unavailable() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    provider = _provider(refuse)
    assert await provider.is_available() is False
    with pytest.raises(ProviderUnavailable):
        await provider.generate("hi")


async def test_neither_binary_nor_server_means_not_installed() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    provider = _provider(refuse, cli=False)
    assert await provider.is_installed() is False
    with pytest.raises(ProviderNotInstalled):
        await provider.generate("hi")


async def test_no_model_configured() -> None:
    with pytest.raises(ProviderUnavailable, match="model"):
        await _provider(_ok_handler, model=None).generate("hi")


async def test_timeout() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/generate":
            raise httpx.ReadTimeout("slow")
        return _ok_handler(request)

    with pytest.raises(ProviderTimeout):
        await _provider(slow).generate("hi", timeout=1)


async def test_http_error_and_malformed_body() -> None:
    def server_error(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/generate":
            return httpx.Response(500, text="model crashed")
        return _ok_handler(request)

    with pytest.raises(ProviderExecutionError) as info:
        await _provider(server_error).generate("hi")
    assert info.value.return_code == 500

    def malformed(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/generate":
            return httpx.Response(200, json={"unexpected": True})
        return _ok_handler(request)

    with pytest.raises(ProviderExecutionError, match="Malformed"):
        await _provider(malformed).generate("hi")
