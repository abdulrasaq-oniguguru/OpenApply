from __future__ import annotations

from openapply.browser.page import FetchedPage, PageLink
from openapply.discovery.service import DiscoveryService, is_likely_job_detail_url


class Fetcher:
    def __init__(self, page: FetchedPage) -> None:
        self.page = page

    async def fetch(self, url: str) -> FetchedPage:
        return self.page


async def test_mercor_discovery_keeps_only_single_job_pages() -> None:
    source = "https://work.mercor.com/explore"
    valid = "https://work.mercor.com/jobs/list_AAABnhjAupH8rg501CtL_6ao/generalist"
    page = FetchedPage(
        url=source,
        final_url=source,
        title="Explore jobs",
        text="Jobs",
        links=[
            PageLink("Generalist job", valid),
            PageLink(
                "Generalist",
                "https://work.mercor.com/explore?listingId=list_AAABlegacy123",
            ),
            PageLink("Jobs", "https://work.mercor.com/jobs"),
            PageLink("Explore opportunities", source),
            PageLink(
                "Incomplete job link",
                "https://work.mercor.com/jobs/list_AAABnhjAupH8rg501CtL_6ao",
            ),
        ],
    )

    result = await DiscoveryService(Fetcher(page)).discover(source)

    assert result.job_urls == [
        valid,
        "https://work.mercor.com/explore?listingId=list_AAABlegacy123",
    ]


async def test_generic_collection_page_is_not_queued_as_its_own_job() -> None:
    source = "https://careers.example.com/jobs"
    detail = "https://careers.example.com/jobs/backend-engineer"
    page = FetchedPage(
        url=source,
        final_url=source,
        title="Jobs",
        text="Open positions",
        links=[PageLink("All jobs", source), PageLink("Backend job", detail)],
    )

    result = await DiscoveryService(Fetcher(page)).discover(source)

    assert result.job_urls == [detail]


def test_mercor_referral_query_does_not_hide_a_valid_detail_path() -> None:
    assert is_likely_job_detail_url(
        "https://work.mercor.com/jobs/list_AAABm4DP9oA5-g42trxEBJcC/generalist?referralCode=x"
    )


def test_mercor_legacy_explore_url_requires_one_safe_listing_id() -> None:
    assert is_likely_job_detail_url(
        "https://work.mercor.com/explore?listingId=list_AAABnhjAupH8rg501CtL_6ao"
    )
    assert not is_likely_job_detail_url("https://work.mercor.com/explore")
    assert not is_likely_job_detail_url("https://work.mercor.com/explore?listingId=bad/value")


async def test_platform_family_links_and_role_query_are_supported() -> None:
    source = "https://www.micro1.ai/experts/opportunities"
    matching = "https://jobs.micro1.ai/post/fa3908ed-a728-492b-a597-83c0adaa5026"
    unrelated = "https://jobs.micro1.ai/post/11111111-1111-1111-1111-111111111111"
    page = FetchedPage(
        url=source,
        final_url=source,
        title="Opportunities",
        text="Roles",
        links=[
            PageLink("Software Engineer opportunity", matching),
            PageLink("Finance Expert opportunity", unrelated),
        ],
    )

    result = await DiscoveryService(Fetcher(page)).discover(source, query="software engineer")

    assert result.job_urls == [matching]
    assert result.platform == "micro1"
    assert result.query == "software engineer"
