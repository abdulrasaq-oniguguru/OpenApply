from __future__ import annotations

import json

import pytest

from openapply.providers.claude import ClaudeProvider
from openapply.providers.errors import (
    ProviderAuthenticationRequired,
    ProviderExecutionError,
    ProviderNotInstalled,
)
from tests.providers.fakes import FakeRunner, absent, present, result

AUTH_PAYLOAD = json.dumps(
    {
        "loggedIn": True,
        "email": "someone@example.com",
        "orgId": "org-123",
        "authMethod": "claude.ai",
    }
)


def _provider(
    reply: str, code: int = 0, stderr: str = "", logged_in: bool = True
) -> tuple[ClaudeProvider, FakeRunner]:
    def handler(argv: list[str], stdin: str | None):  # type: ignore[no-untyped-def]
        if "--version" in argv:
            return result("2.0.0 (Claude Code)\n")
        if argv[1:3] == ["auth", "status"]:
            return result(AUTH_PAYLOAD if logged_in else json.dumps({"loggedIn": False}))
        return result(reply, stderr, code)

    runner = FakeRunner(handler)
    return ClaudeProvider(runner=runner, which=present("claude")), runner


async def test_missing_provider() -> None:
    provider = ClaudeProvider(runner=FakeRunner(lambda a, s: result()), which=absent)
    assert await provider.is_installed() is False
    assert await provider.is_available() is False
    with pytest.raises(ProviderNotInstalled):
        await provider.generate("hi")


async def test_availability_reads_only_logged_in_flag() -> None:
    provider, _ = _provider("")
    assert await provider.is_available() is True
    assert await provider.get_version() == "2.0.0 (Claude Code)"
    provider, _ = _provider("", logged_in=False)
    assert await provider.is_available() is False


async def test_generate_parses_json_and_drops_unsafe_metadata() -> None:
    payload = json.dumps(
        {
            "is_error": False,
            "result": " the answer ",
            "duration_ms": 1200,
            "num_turns": 1,
            "session_id": "abc-session",
            "total_cost_usd": 0.01,
            "modelUsage": {"claude-test-1": {}},
        }
    )
    provider, runner = _provider(payload)
    response = await provider.generate("PROMPT")

    assert response.text == "the answer"
    assert response.model == "claude-test-1"
    assert response.metadata == {"duration_ms": 1200, "num_turns": 1}
    assert "abc-session" not in response.model_dump_json()
    assert response.raw_output is None
    argv = runner.calls[-1]["argv"]
    assert runner.calls[-1]["stdin"] == "PROMPT"
    assert argv[argv.index("--tools") + 1] == ""  # all tools disabled
    assert "--no-session-persistence" in argv


async def test_malformed_output_raises() -> None:
    provider, _ = _provider("not json at all")
    with pytest.raises(ProviderExecutionError, match="malformed"):
        await provider.generate("hi")


async def test_missing_result_field_raises() -> None:
    provider, _ = _provider(json.dumps({"is_error": False}))
    with pytest.raises(ProviderExecutionError):
        await provider.generate("hi")


async def test_error_payload_is_classified() -> None:
    payload = json.dumps({"is_error": True, "result": "Not logged in · Please run /login"})
    provider, _ = _provider(payload, code=1)
    with pytest.raises(ProviderAuthenticationRequired):
        await provider.generate("hi")


async def test_nonzero_exit_without_json() -> None:
    provider, _ = _provider("", code=3, stderr="crashed")
    with pytest.raises(ProviderExecutionError) as info:
        await provider.generate("hi")
    assert info.value.return_code == 3
