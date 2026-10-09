"""Deterministic platform detection from a job URL."""

from __future__ import annotations

from urllib.parse import urlsplit

from openapply.jobs.models import SourcePlatform

_HOST_SUFFIXES: tuple[tuple[str, SourcePlatform], ...] = (
    ("mercor.com", SourcePlatform.MERCOR),
    ("outlier.ai", SourcePlatform.OUTLIER),
    ("dataannotation.tech", SourcePlatform.DATAANNOTATION),
    ("alignerr.com", SourcePlatform.ALIGNERR),
    ("micro1.ai", SourcePlatform.MICRO1),
    ("himalayas.app", SourcePlatform.HIMALAYAS),
    ("remotive.com", SourcePlatform.REMOTIVE),
    ("wellfound.com", SourcePlatform.WELLFOUND),
    ("weworkremotely.com", SourcePlatform.WE_WORK_REMOTELY),
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
