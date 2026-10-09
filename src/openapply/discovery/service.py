"""Bounded discovery from user-selected company career/listing pages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from openapply.browser.page import PageFetcher
from openapply.security.urls import UnsafeURLError, canonicalize_url, validate_job_url

_JOB_HINT = re.compile(
    r"job|career|opening|position|vacan|opportunit|greenhouse|lever|ashby|apply", re.IGNORECASE
)
_MERCOR_HOSTS = frozenset({"work.mercor.com", "mercor.com", "www.mercor.com"})
_MERCOR_JOB_PATH = re.compile(r"^/jobs/list_[A-Za-z0-9_-]+/[^/?#]+/?$")
_COLLECTION_PATHS = frozenset(
    {"", "/", "/jobs", "/careers", "/openings", "/positions", "/opportunities", "/explore"}
)


def is_likely_job_detail_url(url: str) -> bool:
    """Reject known listing/navigation URLs before they consume an analysis task."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").casefold()
    path = parsed.path.rstrip("/").casefold()
    if host in _MERCOR_HOSTS:
        return bool(_MERCOR_JOB_PATH.fullmatch(parsed.path))
    return path not in _COLLECTION_PATHS


def is_mercor_url(url: str) -> bool:
    return (urlsplit(url).hostname or "").casefold() in _MERCOR_HOSTS


@dataclass(frozen=True)
class DiscoveryResult:
    source_url: str
    job_urls: list[str]
    inspected_links: int


class DiscoveryService:
    def __init__(self, fetcher: PageFetcher, *, max_jobs: int = 50) -> None:
        self._fetcher = fetcher
        self._max_jobs = max_jobs

    async def discover(self, source_url: str) -> DiscoveryResult:
        page = await self._fetcher.fetch(source_url)
        source_host = (urlsplit(page.final_url).hostname or "").casefold()
        found: dict[str, str] = {}
        for link in page.links:
            if len(found) >= self._max_jobs:
                break
            host = (urlsplit(link.href).hostname or "").casefold()
            known_board = any(
                host == suffix or host.endswith("." + suffix)
                for suffix in ("greenhouse.io", "lever.co", "ashbyhq.com")
            )
            if not known_board and host != source_host:
                continue
            if not _JOB_HINT.search(f"{link.text} {link.href}"):
                continue
            try:
                safe = validate_job_url(link.href)
            except UnsafeURLError:
                continue
            if not is_likely_job_detail_url(safe):
                continue
            found.setdefault(canonicalize_url(safe), safe)
        return DiscoveryResult(
            source_url=page.final_url,
            job_urls=list(found.values()),
            inspected_links=len(page.links),
        )
