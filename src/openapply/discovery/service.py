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
            found.setdefault(canonicalize_url(safe), safe)
        return DiscoveryResult(
            source_url=page.final_url,
            job_urls=list(found.values()),
            inspected_links=len(page.links),
        )
