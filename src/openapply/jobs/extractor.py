"""Turn a fetched page into a validated ``JobPosting`` using an AI provider."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from urllib.parse import urldefrag, urljoin

from openapply.browser.page import FetchedPage, render_json_ld
from openapply.jobs.models import JobExtraction, JobPosting
from openapply.jobs.platform import detect_platform
from openapply.prompts.job_extraction import build_job_extraction_prompt
from openapply.providers.base import AgentProvider
from openapply.providers.structured import generate_structured
from openapply.security.injection import find_injection_markers

log = logging.getLogger("openapply.jobs")

MAX_PROMPT_CHARS = 24_000
MIN_USEFUL_CHARS = 200


class ExtractionError(Exception):
    """The job could not be extracted."""


class NotAJobPosting(ExtractionError):
    """The page was read, but it is not a single job posting."""


@dataclass(frozen=True)
class ExtractionResult:
    job: JobPosting
    provider: str
    model: str | None
    attempts: int
    warnings: list[str] = field(default_factory=list)


class JobExtractor:
    def __init__(self, provider: AgentProvider, *, timeout: float | None = None) -> None:
        self._provider = provider
        self._timeout = timeout

    async def extract(self, page: FetchedPage) -> ExtractionResult:
        structured = render_json_ld(page.json_ld)
        if not page.text.strip() and not structured:
            raise ExtractionError("The page had no readable text (login wall or empty page?)")

        warnings: list[str] = []
        text = page.text
        if len(text) > MAX_PROMPT_CHARS:
            text = text[:MAX_PROMPT_CHARS]
            warnings.append(f"Page text was truncated to {MAX_PROMPT_CHARS:,} characters.")
        elif len(text) < MIN_USEFUL_CHARS and not structured:
            warnings.append("The page had very little text; the result may be incomplete.")

        markers = find_injection_markers(f"{page.title or ''}\n{page.text}\n{structured or ''}")
        if markers:
            warnings.append(
                "This page contains text that looks like instructions to an AI "
                f"({'; '.join(markers)}). It was treated as data and ignored."
            )
            log.info("possible prompt injection in job page: %s", "; ".join(markers))

        source_url = urldefrag(page.final_url).url
        prompt = build_job_extraction_prompt(
            source_url=source_url,
            page_title=page.title,
            text=text,
            structured_data=structured,
        )
        result = await generate_structured(
            self._provider, prompt, JobExtraction, timeout=self._timeout
        )
        extraction = result.value
        if not extraction.is_job_posting:
            raise NotAJobPosting(
                "The page does not look like a single job posting "
                "(it may be a login wall, an error page, or a list of jobs)."
            )
        try:
            job = JobPosting.from_extraction(
                extraction,
                source_url=source_url,
                platform=detect_platform(source_url),
                raw_text=text,
            )
        except ValueError as exc:
            raise ExtractionError(f"The AI's answer was unusable: {exc}") from exc
        raw_link = extraction.application_url
        if (
            raw_link
            and job.application_url == source_url
            and urljoin(source_url, raw_link) != source_url
        ):
            warnings.append(
                "The apply link found by the AI was unsafe or invalid and was ignored; "
                "the posting page is used instead."
            )
        return ExtractionResult(
            job=job,
            provider=result.response.provider,
            model=result.response.model,
            attempts=result.attempts,
            warnings=warnings,
        )
