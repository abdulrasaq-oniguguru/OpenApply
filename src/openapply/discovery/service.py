"""Bounded discovery from user-selected company career/listing pages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from openapply.browser.page import PageFetcher
from openapply.jobs.models import SourcePlatform
from openapply.jobs.platform import detect_platform
from openapply.security.urls import UnsafeURLError, canonicalize_url, validate_job_url

_JOB_HINT = re.compile(
    r"job|career|opening|position|vacan|opportunit|greenhouse|lever|ashby|apply", re.IGNORECASE
)
_MERCOR_HOSTS = frozenset({"work.mercor.com", "mercor.com", "www.mercor.com"})
_MERCOR_JOB_PATH = re.compile(r"^/jobs/list_[A-Za-z0-9_-]+/[^/?#]+/?$")
_MERCOR_LISTING_ID = re.compile(r"^[A-Za-z0-9_-]{6,200}$")
_COLLECTION_PATHS = frozenset(
    {"", "/", "/jobs", "/careers", "/openings", "/positions", "/opportunities", "/explore"}
)


def mercor_listing_id(url: str) -> str | None:
    """Return a safe listing id from Mercor's legacy explore URL, if present."""
    parsed = urlsplit(url)
    if parsed.path.rstrip("/").casefold() != "/explore":
        return None
    values = [
        item
        for key, items in parse_qs(parsed.query, keep_blank_values=True).items()
        if key.casefold() == "listingid"
        for item in items
    ]
    if len(values) != 1 or not _MERCOR_LISTING_ID.fullmatch(values[0]):
        return None
    return values[0]


def is_likely_job_detail_url(url: str) -> bool:
    """Reject known listing/navigation URLs before they consume an analysis task."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").casefold()
    path = parsed.path.rstrip("/").casefold()
    if host in _MERCOR_HOSTS:
        return bool(_MERCOR_JOB_PATH.fullmatch(parsed.path) or mercor_listing_id(url))
    platform = detect_platform(url)
    if platform is SourcePlatform.OUTLIER:
        return bool(re.fullmatch(r"/opportunities/[^/]+/?", parsed.path))
    if platform is SourcePlatform.DATAANNOTATION:
        return bool(re.fullmatch(r"/job-board/[^/]+/?", parsed.path))
    if platform is SourcePlatform.ALIGNERR:
        return bool(re.fullmatch(r"/(?:[a-z]{2}/)?jobs/[^/]+/?", parsed.path))
    if platform is SourcePlatform.MICRO1:
        return (host == "jobs.micro1.ai" or host.endswith(".jobs.micro1.ai")) and bool(
            re.fullmatch(r"/post/[^/]+/?", parsed.path)
        )
    if platform is SourcePlatform.HIMALAYAS:
        return bool(re.fullmatch(r"/companies/[^/]+/jobs/[^/]+/?", parsed.path))
    if platform is SourcePlatform.REMOTIVE:
        return bool(re.fullmatch(r"/remote-jobs/[^/]+/[^/]+/?", parsed.path))
    if platform is SourcePlatform.WELLFOUND:
        return bool(re.fullmatch(r"/jobs/[^/]+/?", parsed.path))
    if platform is SourcePlatform.WE_WORK_REMOTELY:
        return bool(re.fullmatch(r"/remote-jobs/[^/]+/?", parsed.path))
    return path not in _COLLECTION_PATHS


def is_mercor_url(url: str) -> bool:
    return (urlsplit(url).hostname or "").casefold() in _MERCOR_HOSTS


@dataclass(frozen=True)
class DiscoveryResult:
    source_url: str
    job_urls: list[str]
    inspected_links: int
    platform: str | None = None
    query: str | None = None
    attribution: str | None = None


class DiscoveryService:
    def __init__(self, fetcher: PageFetcher, *, max_jobs: int = 50) -> None:
        self._fetcher = fetcher
        self._max_jobs = max_jobs

    async def discover(self, source_url: str, *, query: str | None = None) -> DiscoveryResult:
        page = await self._fetcher.fetch(source_url)
        source_host = (urlsplit(page.final_url).hostname or "").casefold()
        source_platform = detect_platform(page.final_url)
        query_terms = tuple(query.casefold().split()) if query else ()
        found: dict[str, str] = {}
        for link in page.links:
            if len(found) >= self._max_jobs:
                break
            host = (urlsplit(link.href).hostname or "").casefold()
            known_board = any(
                host == suffix or host.endswith("." + suffix)
                for suffix in ("greenhouse.io", "lever.co", "ashbyhq.com")
            )
            link_platform = detect_platform(link.href)
            same_platform = (
                source_platform is not SourcePlatform.GENERIC and link_platform is source_platform
            )
            if not known_board and host != source_host and not same_platform:
                continue
            try:
                safe = validate_job_url(link.href)
            except UnsafeURLError:
                continue
            if not is_likely_job_detail_url(safe):
                continue
            searchable = f"{link.text} {link.href}".casefold()
            if link_platform is SourcePlatform.GENERIC and not _JOB_HINT.search(searchable):
                continue
            if query_terms and not all(term in searchable for term in query_terms):
                continue
            found.setdefault(canonicalize_url(safe), safe)
        return DiscoveryResult(
            source_url=page.final_url,
            job_urls=list(found.values()),
            inspected_links=len(page.links),
            platform=(
                source_platform.value if source_platform is not SourcePlatform.GENERIC else None
            ),
            query=query,
        )
