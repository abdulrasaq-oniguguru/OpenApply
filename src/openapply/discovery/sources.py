"""Platform discovery through public pages, JSON APIs, and RSS feeds."""

from __future__ import annotations

import json
from typing import Protocol, runtime_checkable
from urllib.parse import urlencode, urljoin, urlsplit

import httpx
from defusedxml import ElementTree

from openapply.browser.page import FetchedPage, PageFetcher
from openapply.discovery.platforms import (
    ExtractionApproach,
    PlatformSource,
    get_platform_source,
)
from openapply.discovery.service import DiscoveryResult, DiscoveryService, is_likely_job_detail_url
from openapply.security.urls import UnsafeURLError, canonicalize_url, validate_job_url

MAX_FEED_BYTES = 5_000_000
MAX_REDIRECTS = 5


class DiscoveryError(ValueError):
    """A supported public source could not provide a usable bounded result."""


class PublicSourceClient(Protocol):
    async def get_bytes(self, url: str, *, host_suffix: str) -> bytes: ...


@runtime_checkable
class SearchablePageFetcher(Protocol):
    async def fetch_with_query(self, url: str, query: str) -> FetchedPage: ...


class _QueryPageFetcher:
    def __init__(self, fetcher: SearchablePageFetcher, query: str) -> None:
        self._fetcher = fetcher
        self._query = query

    async def fetch(self, url: str) -> FetchedPage:
        return await self._fetcher.fetch_with_query(url, self._query)


class HttpxPublicSourceClient:
    """Small bounded HTTP client used only with registry-owned public endpoints."""

    async def get_bytes(self, url: str, *, host_suffix: str) -> bytes:
        current = validate_job_url(url)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                for _ in range(MAX_REDIRECTS + 1):
                    async with client.stream(
                        "GET",
                        current,
                        headers={
                            "Accept": "application/json, application/rss+xml, application/xml"
                        },
                    ) as response:
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                raise DiscoveryError("Public source returned an empty redirect.")
                            current = validate_job_url(urljoin(current, location))
                            host = (urlsplit(current).hostname or "").casefold()
                            if host != host_suffix and not host.endswith("." + host_suffix):
                                raise DiscoveryError(
                                    "Public source redirected outside its platform."
                                )
                            continue
                        response.raise_for_status()
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > MAX_FEED_BYTES:
                                raise DiscoveryError("Public source response exceeded 5 MB.")
                        return bytes(body)
        except DiscoveryError:
            raise
        except (httpx.HTTPError, UnsafeURLError) as exc:
            raise DiscoveryError(f"Could not read public job source: {exc}") from exc
        raise DiscoveryError("Public source redirected too many times.")


class PlatformDiscoveryService:
    def __init__(
        self,
        page_fetcher: PageFetcher,
        source_client: PublicSourceClient | None = None,
        *,
        max_jobs: int = 50,
    ) -> None:
        self._page_fetcher = page_fetcher
        self._source_client = source_client or HttpxPublicSourceClient()
        self._max_jobs = max_jobs

    async def discover(self, platform: str, *, query: str | None = None) -> DiscoveryResult:
        source = get_platform_source(platform)
        query = query.strip() if query and query.strip() else None
        if source.approach is ExtractionApproach.PAGE:
            page_url = source.source_url
            use_search_box = True
            if source.key == "micro1" and query:
                page_url = f"{source.source_url}?{urlencode({'search': query})}"
                use_search_box = False
            fetcher: PageFetcher = self._page_fetcher
            if query and use_search_box and isinstance(fetcher, SearchablePageFetcher):
                fetcher = _QueryPageFetcher(fetcher, query)
            result = await DiscoveryService(fetcher, max_jobs=self._max_jobs).discover(
                page_url, query=query
            )
            return DiscoveryResult(
                source_url=result.source_url,
                job_urls=result.job_urls,
                inspected_links=result.inspected_links,
                platform=source.key,
                query=query,
                attribution=source.attribution,
            )
        body, requested_url = await self._read_source(source, query)
        if source.approach is ExtractionApproach.JSON_API:
            urls, inspected = self._json_urls(source, body)
        else:
            urls, inspected = self._rss_urls(body, query)
        return DiscoveryResult(
            source_url=requested_url,
            job_urls=urls[: self._max_jobs],
            inspected_links=inspected,
            platform=source.key,
            query=query,
            attribution=source.attribution,
        )

    async def _read_source(self, source: PlatformSource, query: str | None) -> tuple[bytes, str]:
        requested_url = source.source_url
        if source.key == "himalayas":
            requested_url = (
                f"https://himalayas.app/jobs/api/search?{urlencode({'q': query})}"
                if query
                else "https://himalayas.app/jobs/api?limit=20"
            )
        elif source.key == "remotive":
            params: dict[str, str | int] = {"limit": self._max_jobs}
            if query:
                params["search"] = query
            requested_url = f"{source.source_url}?{urlencode(params)}"
        host_suffix = (urlsplit(source.source_url).hostname or "").removeprefix("www.")
        body = await self._source_client.get_bytes(requested_url, host_suffix=host_suffix)
        return body, requested_url

    def _json_urls(self, source: PlatformSource, body: bytes) -> tuple[list[str], int]:
        try:
            document = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise DiscoveryError(f"{source.name} returned invalid JSON.") from exc
        if not isinstance(document, dict) or not isinstance(document.get("jobs"), list):
            raise DiscoveryError(f"{source.name} response did not contain a jobs list.")
        jobs = document["jobs"]
        candidates: list[object]
        if source.key == "himalayas":
            candidates = [
                item.get("guid") or item.get("applicationLink")
                for item in jobs
                if isinstance(item, dict)
            ]
        else:
            candidates = [item.get("url") for item in jobs if isinstance(item, dict)]
        return self._safe_unique_urls(candidates), len(jobs)

    def _rss_urls(self, body: bytes, query: str | None) -> tuple[list[str], int]:
        try:
            root = ElementTree.fromstring(body)
        except ElementTree.ParseError as exc:
            raise DiscoveryError("We Work Remotely returned invalid RSS.") from exc
        terms = tuple(query.casefold().split()) if query else ()
        candidates: list[object] = []
        items = root.findall(".//item")
        for item in items:
            title = item.findtext("title") or ""
            description = item.findtext("description") or ""
            searchable = f"{title} {description}".casefold()
            if terms and not all(term in searchable for term in terms):
                continue
            candidates.append(item.findtext("link") or item.findtext("guid"))
        return self._safe_unique_urls(candidates), len(items)

    def _safe_unique_urls(self, candidates: list[object]) -> list[str]:
        found: dict[str, str] = {}
        for candidate in candidates:
            if len(found) >= self._max_jobs:
                break
            if not isinstance(candidate, str):
                continue
            try:
                safe = validate_job_url(candidate)
            except UnsafeURLError:
                continue
            if not is_likely_job_detail_url(safe):
                continue
            found.setdefault(canonicalize_url(safe), safe)
        return list(found.values())
