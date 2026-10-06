"""Provider error hierarchy. Messages are always passed through redaction."""

from __future__ import annotations

import re

from openapply.security.redact import redact


class ProviderError(Exception):
    def __init__(self, provider: str, message: str) -> None:
        self.provider = provider
        self.message = redact(message)
        super().__init__(f"[{provider}] {self.message}")


class ProviderNotInstalled(ProviderError):
    pass


class ProviderUnavailable(ProviderError):
    pass


class ProviderAuthenticationRequired(ProviderError):
    pass


class ProviderRateLimited(ProviderError):
    pass


class ProviderTimeout(ProviderError):
    pass


class ProviderExecutionError(ProviderError):
    def __init__(self, provider: str, message: str, *, return_code: int | None = None) -> None:
        self.return_code = return_code
        super().__init__(provider, message)


# Only phrases that unambiguously mean what they say. Anything else stays a
# plain ProviderExecutionError.
_AUTH = re.compile(
    r"not logged in|please (?:log ?in|run .{0,30}login)|login required|"
    r"authentication required|not authenticated|invalid api key|unauthorized",
    re.IGNORECASE,
)
_RATE = re.compile(
    r"rate[ _-]?limit|too many requests|usage limit|quota exceeded|limit reached",
    re.IGNORECASE,
)


def classify_failure(provider: str, output: str, return_code: int | None) -> ProviderError:
    snippet = redact(output.strip())[-500:] or "no output"
    if _AUTH.search(output):
        return ProviderAuthenticationRequired(provider, f"Authentication required: {snippet}")
    if _RATE.search(output):
        return ProviderRateLimited(provider, f"Rate or usage limit reached: {snippet}")
    return ProviderExecutionError(
        provider, f"Exited with code {return_code}: {snippet}", return_code=return_code
    )
