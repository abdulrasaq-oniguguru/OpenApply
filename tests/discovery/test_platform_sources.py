from __future__ import annotations

import json

import pytest

from openapply.browser.page import FetchedPage, PageLink
from openapply.discovery.platforms import get_platform_source, platform_source_documents
from openapply.discovery.sources import DiscoveryError, PlatformDiscoveryService


class PageFetcher:
    def __init__(self, page: FetchedPage) -> None:
        self.page = page
        self.requested: list[str] = []

    async def fetch(self, url: str) -> FetchedPage:
        self.requested.append(url)
        return self.page


class SourceClient:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.calls: list[tuple[str, str]] = []

    async def get_bytes(self, url: str, *, host_suffix: str) -> bytes:
        self.calls.append((url, host_suffix))
        return self.body


def _unused_page() -> FetchedPage:
    return FetchedPage(
        url="https://example.com",
        final_url="https://example.com",
        title=None,
        text="",
    )


def test_platform_catalog_contains_every_requested_source() -> None:
    documents = platform_source_documents()
    assert {item["key"] for item in documents} == {
        "mercor",
        "outlier",
        "dataannotation",
        "alignerr",
        "micro1",
        "himalayas",
        "remotive",
        "wellfound",
        "we-work-remotely",
    }
    assert get_platform_source("WWR").key == "we-work-remotely"
    assert get_platform_source("Outlier AI").key == "outlier"


async def test_himalayas_search_uses_official_api_and_listing_links() -> None:
    listing = "https://himalayas.app/companies/acme/jobs/backend-engineer"
    body = json.dumps(
        {
            "jobs": [
                {"guid": listing, "applicationLink": "https://acme.example/apply"},
                {"guid": "file:///etc/passwd"},
            ]
        }
    ).encode()
    client = SourceClient(body)
    service = PlatformDiscoveryService(_page_fetcher(), client)

    result = await service.discover("himalayas", query="backend engineer")

    assert result.job_urls == [listing]
    assert client.calls == [
        (
            "https://himalayas.app/jobs/api/search?q=backend+engineer",
            "himalayas.app",
        )
    ]
    assert result.attribution is not None and "Himalayas" in result.attribution


async def test_remotive_search_is_bounded_and_deduplicated() -> None:
    listing = "https://remotive.com/remote-jobs/software-dev/backend-engineer-42"
    body = json.dumps({"jobs": [{"url": listing}, {"url": listing}]}).encode()
    client = SourceClient(body)
    service = PlatformDiscoveryService(_page_fetcher(), client, max_jobs=10)

    result = await service.discover("remotive", query="python")

    assert result.job_urls == [listing]
    assert client.calls == [
        ("https://remotive.com/api/remote-jobs?limit=10&search=python", "remotive.com")
    ]
    assert result.inspected_links == 2


async def test_we_work_remotely_rss_filters_by_query() -> None:
    body = b"""<?xml version="1.0"?><rss><channel>
      <item><title>Acme: Python Engineer</title><link>https://weworkremotely.com/remote-jobs/acme-python-engineer</link></item>
      <item><title>Acme: Sales Lead</title><link>https://weworkremotely.com/remote-jobs/acme-sales-lead</link></item>
    </channel></rss>"""
    service = PlatformDiscoveryService(_page_fetcher(), SourceClient(body))

    result = await service.discover("we-work-remotely", query="python engineer")

    assert result.job_urls == ["https://weworkremotely.com/remote-jobs/acme-python-engineer"]
    assert result.inspected_links == 2


async def test_page_platform_uses_bounded_page_discovery() -> None:
    source = "https://www.dataannotation.tech/"
    listing = "https://www.dataannotation.tech/job-board/software-engineer"
    page = FetchedPage(
        url=source,
        final_url=source,
        title="Roles",
        text="Roles",
        links=[PageLink("Software Engineer", listing)],
    )
    service = PlatformDiscoveryService(PageFetcher(page), SourceClient(b"unused"))

    result = await service.discover("dataannotation", query="software engineer")

    assert result.job_urls == [listing]
    assert result.platform == "dataannotation"


async def test_micro1_passes_role_query_to_its_public_listing_page() -> None:
    source = "https://www.micro1.ai/experts/opportunities"
    listing = "https://jobs.micro1.ai/post/fa3908ed-a728-492b-a597-83c0adaa5026"
    fetcher = PageFetcher(
        FetchedPage(
            url=source,
            final_url=source,
            title="Opportunities",
            text="Roles",
            links=[PageLink("Software Engineer role", listing)],
        )
    )

    result = await PlatformDiscoveryService(fetcher, SourceClient(b"unused")).discover(
        "micro1", query="software engineer"
    )

    assert result.job_urls == [listing]
    assert fetcher.requested == [
        "https://www.micro1.ai/experts/opportunities?search=software+engineer"
    ]


@pytest.mark.parametrize("body", [b"not json", b"[]", b'{"jobs":"wrong"}'])
async def test_malformed_json_source_fails_clearly(body: bytes) -> None:
    service = PlatformDiscoveryService(_page_fetcher(), SourceClient(body))

    with pytest.raises(DiscoveryError, match="Himalayas"):
        await service.discover("himalayas")


def _page_fetcher() -> PageFetcher:
    return PageFetcher(_unused_page())
