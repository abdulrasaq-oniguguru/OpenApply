from __future__ import annotations

from pathlib import Path

import pytest

from openapply.providers.codex import CodexProvider
from openapply.providers.errors import (
    ProviderAuthenticationRequired,
    ProviderExecutionError,
    ProviderNotInstalled,
    ProviderRateLimited,
    ProviderTimeout,
)
from tests.providers.fakes import FakeRunner, absent, present, result


def _codex_handler(reply: str | None, code: int = 0, stderr: str = ""):  # type: ignore[no-untyped-def]
    def handler(argv: list[str], stdin: str | None):  # type: ignore[no-untyped-def]
        if "--version" in argv:
            return result("codex-cli 9.9.9\n")
        if argv[1:3] == ["login", "status"]:
            return result("Logged in using ChatGPT", code=code)
        if reply is not None:
            Path(argv[argv.index("-o") + 1]).write_text(reply, encoding="utf-8")
        return result("", stderr, code)

    return handler


async def test_missing_provider_reports_not_installed() -> None:
    provider = CodexProvider(runner=FakeRunner(_codex_handler("x")), which=absent)
    assert await provider.is_installed() is False
    assert await provider.is_available() is False
    assert await provider.get_version() is None
    with pytest.raises(ProviderNotInstalled):
        await provider.generate("hi")


async def test_installed_version_and_availability() -> None:
    provider = CodexProvider(runner=FakeRunner(_codex_handler("x")), which=present("codex"))
    assert await provider.is_installed() is True
    assert await provider.get_version() == "codex-cli 9.9.9"
    assert await provider.is_available() is True


async def test_not_logged_in_is_unavailable() -> None:
    provider = CodexProvider(runner=FakeRunner(_codex_handler("x", code=1)), which=present("codex"))
    assert await provider.is_available() is False


async def test_generate_success_uses_stdin_and_locked_down_flags() -> None:
    runner = FakeRunner(_codex_handler("  hello world \n"))
    provider = CodexProvider(runner=runner, which=present("codex"), model="gpt-x")
    response = await provider.generate("SECRET PROMPT", timeout=30)

    assert response.text == "hello world"
    assert response.provider == "codex"
    assert response.model == "gpt-x"
    call = runner.calls[-1]
    assert call["stdin"] == "SECRET PROMPT"
    assert "SECRET PROMPT" not in call["argv"]  # prompt never on the command line
    argv = call["argv"]
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert "--ephemeral" in argv
    assert argv[argv.index("-m") + 1] == "gpt-x"
    assert call["timeout"] == 30


async def test_failed_command_raises_execution_error() -> None:
    runner = FakeRunner(_codex_handler(None, code=2, stderr="boom"))
    provider = CodexProvider(runner=runner, which=present("codex"))
    with pytest.raises(ProviderExecutionError) as info:
        await provider.generate("hi")
    assert info.value.return_code == 2


async def test_auth_failure_is_classified() -> None:
    runner = FakeRunner(_codex_handler(None, code=1, stderr="Error: not logged in"))
    provider = CodexProvider(runner=runner, which=present("codex"))
    with pytest.raises(ProviderAuthenticationRequired):
        await provider.generate("hi")


async def test_rate_limit_only_when_output_says_so() -> None:
    limited = CodexProvider(
        runner=FakeRunner(_codex_handler(None, code=1, stderr="You hit your usage limit")),
        which=present("codex"),
    )
    with pytest.raises(ProviderRateLimited):
        await limited.generate("hi")

    plain = CodexProvider(
        runner=FakeRunner(_codex_handler(None, code=1, stderr="segfault")),
        which=present("codex"),
    )
    with pytest.raises(ProviderExecutionError) as info:
        await plain.generate("hi")
    assert not isinstance(info.value, ProviderRateLimited)


async def test_timeout_propagates() -> None:
    def handler(argv: list[str], stdin: str | None):  # type: ignore[no-untyped-def]
        return ProviderTimeout("codex", "Timed out")

    provider = CodexProvider(runner=FakeRunner(handler), which=present("codex"))
    with pytest.raises(ProviderTimeout):
        await provider.generate("hi", timeout=1)


async def test_empty_output_is_an_error() -> None:
    provider = CodexProvider(runner=FakeRunner(_codex_handler("")), which=present("codex"))
    with pytest.raises(ProviderExecutionError):
        await provider.generate("hi")
