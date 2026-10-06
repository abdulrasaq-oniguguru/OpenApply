"""Heuristic detection of prompt-injection attempts in untrusted page text.

This is a *warning* signal for the user, never a security boundary. The real
defence is structural: untrusted content is delimited, labelled as data, and the
provider is run with no tools (see ``openapply.prompts``).
"""

from __future__ import annotations

import re

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "tells the AI to ignore its instructions",
        re.compile(
            r"ignore\s+(?:all\s+|any\s+|your\s+)?(?:the\s+)?(?:previous|prior|above|earlier|system)\s+"
            r"(?:instructions?|prompts?|rules?)",
            re.I,
        ),
    ),
    (
        "tells the AI to disregard its instructions",
        re.compile(
            r"disregard\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|prior|above|system)\s+"
            r"(?:instructions?|prompts?)",
            re.I,
        ),
    ),
    (
        "addresses an AI assistant directly",
        re.compile(r"\b(?:ai|llm)\s+(?:assistant|agent|model)\b", re.I),
    ),
    ("references the system prompt", re.compile(r"\bsystem\s+prompt\b", re.I)),
    (
        "asks for secrets or credentials",
        re.compile(
            r"(?:api[_ -]?key|password|private\s+key|id_rsa|\.ssh|credentials?)\b.{0,60}"
            r"(?:send|upload|reveal|print|include|output)|"
            r"(?:send|upload|reveal|print|include|output).{0,60}"
            r"(?:api[_ -]?key|password|private\s+key|id_rsa|\.ssh)",
            re.I | re.S,
        ),
    ),
    (
        "tells the AI to change its role or behaviour",
        re.compile(
            r"\byou\s+are\s+now\b|\bnew\s+instructions?\s*:|\bact\s+as\s+(?:a|an|the)\b", re.I
        ),
    ),
)


def find_injection_markers(text: str) -> list[str]:
    """Return human-readable reasons the text looks like an injection attempt (deduplicated)."""
    return [reason for reason, pattern in _PATTERNS if pattern.search(text)]
