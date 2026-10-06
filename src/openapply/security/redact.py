"""Redaction of secret-looking strings before anything is logged or displayed."""

from __future__ import annotations

import re

REDACTED = "[REDACTED]"

_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Authorization headers / bearer tokens
    re.compile(r"(?i)\b(authorization\s*[:=]\s*)(bearer|basic|token)?\s*[^\s\"',;]+"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    # Cookie headers
    re.compile(r"(?i)\b((?:set-)?cookie\s*[:=]\s*)[^\r\n]+"),
    # key=value / key: value secrets
    re.compile(
        r"(?i)\b((?:api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|"
        r"secret|password|passwd|client[_-]?secret|session(?:id)?)\s*[\"']?\s*[:=]\s*[\"']?)"
        r"[^\s\"',;&]+"
    ),
    # Well-known token shapes
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),  # JWT
)


def redact(text: str) -> str:
    """Return ``text`` with credential-looking substrings replaced by ``[REDACTED]``."""
    for pattern in _PATTERNS:
        if pattern.groups:
            text = pattern.sub(lambda m: f"{m.group(1)}{REDACTED}", text)
        else:
            text = pattern.sub(REDACTED, text)
    return text
