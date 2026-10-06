"""Model-supplied apply links are untrusted and must obey the same policy as page URLs."""

from __future__ import annotations

import json

import pytest

from openapply.browser.page import FetchedPage
from openapply.jobs.extractor import JobExtractor
from openapply.jobs.models import JobExtraction, JobPosting, SourcePlatform
from openapply.security.urls import safe_application_url
from tests.jobs.test_extractor import GOOD, LONG_TEXT
from tests.providers.fakes import ScriptedProvider

SOURCE = "https://careers.example.com/jobs/42"


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        (None, SOURCE),
        ("", SOURCE),
        ("/apply", "https://careers.example.com/apply"),
        ("?step=apply", "https://careers.example.com/jobs/42?step=apply"),
        # cross-origin public links are legitimate (applications often live on an ATS domain)
        ("https://boards.greenhouse.io/acme/jobs/1", "https://boards.greenhouse.io/acme/jobs/1"),
        ("http://8.8.8.8/apply", "http://8.8.8.8/apply"),
    ],
)
def test_safe_links_are_resolved(candidate: str | None, expected: str) -> None:
    assert safe_application_url(candidate, SOURCE) == expected


@pytest.mark.parametrize(
    "candidate",
    [
        "http://169.254.169.254/latest/meta-data",  # cloud metadata
        "http://10.0.0.5/admin",
        "http://192.168.1.1/",
        "http://127.0.0.1:8080/apply",
        "http://localhost/apply",
        "http://[::1]/apply",
        "https://printer.local/apply",
        "//169.254.169.254/latest",  # scheme-relative form of the same thing
        "https://user:hunter2@evil.example/apply",  # embedded credentials
        "https://u@careers.example.com/apply",
        "javascript:alert(1)",
        "file:///etc/passwd",
        "data:text/html,<script>1</script>",
        "ftp://example.com/x",
    ],
)
def test_unsafe_links_fall_back_to_the_posting_page(candidate: str) -> None:
    assert safe_application_url(candidate, SOURCE) == SOURCE


def test_credentials_are_never_kept_in_the_result() -> None:
    result = safe_application_url("https://user:hunter2@evil.example/apply", SOURCE)
    assert "hunter2" not in result
    assert "@" not in result


def test_a_local_posting_may_link_within_its_own_host_only() -> None:
    local = "http://127.0.0.1:8765/job.html"  # user opted in with --allow-local
    assert safe_application_url("/apply", local) == "http://127.0.0.1:8765/apply"
    assert safe_application_url("http://127.0.0.1:8765/x", local) == "http://127.0.0.1:8765/x"
    # ...but not to some other private host, and credentials are still refused
    assert safe_application_url("http://10.0.0.5/x", local) == local
    assert safe_application_url("http://169.254.169.254/x", local) == local
    assert safe_application_url("http://u:p@127.0.0.1:8765/x", local) == local
    # a public link from a local page is fine
    assert safe_application_url("https://example.com/apply", local) == "https://example.com/apply"


def test_a_public_posting_cannot_unlock_local_links() -> None:
    # The opt-in is derived from the *posting's* host, never from the link itself.
    assert safe_application_url("http://127.0.0.1:8765/x", SOURCE) == SOURCE


def test_from_extraction_applies_the_policy() -> None:
    extraction = JobExtraction.model_validate(
        {"title": "A", "application_url": "http://169.254.169.254/latest/meta-data"}
    )
    job = JobPosting.from_extraction(extraction, source_url=SOURCE, platform=SourcePlatform.GENERIC)
    assert job.application_url == SOURCE


async def test_extractor_warns_when_it_discards_an_unsafe_link() -> None:
    reply = json.dumps({**GOOD, "application_url": "http://169.254.169.254/latest/meta-data"})
    page = FetchedPage(url=SOURCE, final_url=SOURCE, title="Job", text=LONG_TEXT)
    result = await JobExtractor(ScriptedProvider([reply])).extract(page)
    assert result.job.application_url == SOURCE
    assert any("apply link" in w and "ignored" in w for w in result.warnings)


async def test_extractor_keeps_and_does_not_warn_about_a_good_cross_origin_link() -> None:
    link = "https://boards.greenhouse.io/acme/jobs/1"
    reply = json.dumps({**GOOD, "application_url": link})
    page = FetchedPage(url=SOURCE, final_url=SOURCE, title="Job", text=LONG_TEXT)
    result = await JobExtractor(ScriptedProvider([reply])).extract(page)
    assert result.job.application_url == link
    assert result.warnings == []
