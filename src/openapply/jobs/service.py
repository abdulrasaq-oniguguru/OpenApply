"""Job use-cases: analyze a posting from a URL, and match it against a candidate profile."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from openapply.browser.page import PageFetcher
from openapply.candidate.models import CandidateProfile
from openapply.jobs.experience import candidate_years
from openapply.jobs.extractor import ExtractionResult, JobExtractor
from openapply.jobs.match_models import AIAnalysis, JobMatch
from openapply.jobs.matcher import score_match
from openapply.jobs.models import JobPosting
from openapply.prompts.match_analysis import build_match_analysis_prompt
from openapply.providers.base import AgentProvider
from openapply.providers.errors import ProviderError
from openapply.providers.structured import StructuredOutputError, generate_structured

log = logging.getLogger("openapply.jobs")


class JobService:
    """Browser and AI are injected, so both can be replaced in tests."""

    def __init__(
        self, fetcher: PageFetcher, provider: AgentProvider, *, timeout: float | None = None
    ) -> None:
        self._fetcher = fetcher
        self._extractor = JobExtractor(provider, timeout=timeout)

    async def analyze(self, url: str) -> ExtractionResult:
        page = await self._fetcher.fetch(url)
        return await self._extractor.extract(page)


@dataclass(frozen=True)
class MatchResult:
    match: JobMatch
    warnings: list[str] = field(default_factory=list)


class MatchService:
    """Deterministic scoring, plus optional AI notes that can never change the score."""

    def __init__(
        self, provider: AgentProvider | None = None, *, timeout: float | None = None
    ) -> None:
        self._provider = provider
        self._timeout = timeout

    async def match(
        self,
        profile: CandidateProfile,
        job: JobPosting,
        *,
        with_ai: bool = True,
        today: date | None = None,
    ) -> MatchResult:
        match = score_match(profile, job, today=today)
        warnings: list[str] = []
        if with_ai and self._provider is not None:
            years = candidate_years(profile, today or date.today())
            prompt = build_match_analysis_prompt(profile, job, years)
            try:
                result = await generate_structured(
                    self._provider, prompt, AIAnalysis, timeout=self._timeout
                )
            except (ProviderError, StructuredOutputError) as exc:
                # The score does not depend on the AI, so its failure is not fatal.
                message = exc.message if isinstance(exc, ProviderError) else str(exc)
                warnings.append(f"AI analysis unavailable: {message}")
                log.info("AI analysis failed: %s", message)
            else:
                match = match.model_copy(update={"ai_analysis": result.value})
        return MatchResult(match=match, warnings=warnings)
