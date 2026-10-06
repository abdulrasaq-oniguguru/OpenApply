from __future__ import annotations

import pytest

from openapply.providers.errors import ProviderExecutionError
from openapply.security.redact import REDACTED, redact


@pytest.mark.parametrize(
    "text, secret",
    [
        ("Authorization: Bearer abcdef1234567890", "abcdef1234567890"),
        ("curl -H 'authorization: token ghp_abcdefghijklmnopqrstuvwxyz0123'", "ghp_abcdefghij"),
        ("Cookie: session=abc123def456; other=1", "abc123def456"),
        ("api_key=sk-live-1234567890abcdef", "1234567890abcdef"),
        ('{"access_token": "tok_9876543210"}', "tok_9876543210"),
        ("password: hunter2hunter2", "hunter2"),
        ("key sk-abcdefghijklmnopqrstuvwxyz123456 end", "abcdefghijklmnopqrstuvwxyz123456"),
        ("jwt eyJhbGciOiJI.eyJzdWIiOiIx.SflKxwRJSMeKKF2QT4 done", "SflKxwRJSMeKKF2QT4"),
    ],
)
def test_secrets_are_redacted(text: str, secret: str) -> None:
    cleaned = redact(text)
    assert secret not in cleaned
    assert REDACTED in cleaned


def test_ordinary_text_is_untouched() -> None:
    text = "Backend Engineer at Example Corp, 3-5 years of Python"
    assert redact(text) == text


def test_error_messages_are_redacted() -> None:
    err = ProviderExecutionError("x", "failed with Authorization: Bearer abcdef1234567890")
    assert "abcdef1234567890" not in str(err)
