"""Deterministic platform detection from a job URL (adapters arrive in Milestone 6)."""

from __future__ import annotations

from urllib.parse import urlsplit

from openapply.jobs.models import SourcePlatform

_HOST_SUFFIXES: tuple[tuple[str, SourcePlatform], ...] = (
    ("greenhouse.io", SourcePlatform.GREENHOUSE),
    ("lever.co", SourcePlatform.LEVER),
    ("ashbyhq.com", SourcePlatform.ASHBY),
    ("linkedin.com", SourcePlatform.LINKEDIN),
    ("indeed.com", SourcePlatform.INDEED),
)


def detect_platform(url: str) -> SourcePlatform:
    host = (urlsplit(url).hostname or "").lower()
    for suffix, platform in _HOST_SUFFIXES:
        if host == suffix or host.endswith("." + suffix):
            return platform
    return SourcePlatform.GENERIC
