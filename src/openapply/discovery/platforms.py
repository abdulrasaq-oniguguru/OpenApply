"""Supported discovery sources and their public extraction surfaces."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from openapply.jobs.models import SourcePlatform


class ExtractionApproach(StrEnum):
    PAGE = "public_page"
    JSON_API = "public_json_api"
    RSS = "public_rss"


@dataclass(frozen=True)
class PlatformSource:
    key: str
    platform: SourcePlatform
    name: str
    job_types: str
    approach: ExtractionApproach
    source_url: str
    supports_query: bool = True
    attribution: str | None = None

    def as_dict(self) -> dict[str, str | bool | None]:
        return {
            "key": self.key,
            "platform": self.platform.value,
            "name": self.name,
            "job_types": self.job_types,
            "approach": self.approach.value,
            "source_url": self.source_url,
            "supports_query": self.supports_query,
            "attribution": self.attribution,
        }


PLATFORM_SOURCES: tuple[PlatformSource, ...] = (
    PlatformSource(
        "mercor",
        SourcePlatform.MERCOR,
        "Mercor",
        "AI, software, expert and generalist roles",
        ExtractionApproach.PAGE,
        "https://work.mercor.com/explore",
    ),
    PlatformSource(
        "outlier",
        SourcePlatform.OUTLIER,
        "Outlier AI",
        "AI training, coding and AI evaluation",
        ExtractionApproach.PAGE,
        "https://app.outlier.ai/opportunities",
    ),
    PlatformSource(
        "dataannotation",
        SourcePlatform.DATAANNOTATION,
        "DataAnnotation",
        "Programming, AI training and evaluation",
        ExtractionApproach.PAGE,
        "https://www.dataannotation.tech/",
    ),
    PlatformSource(
        "alignerr",
        SourcePlatform.ALIGNERR,
        "Alignerr",
        "AI model evaluation and specialist work",
        ExtractionApproach.PAGE,
        "https://www.alignerr.com/jobs",
    ),
    PlatformSource(
        "micro1",
        SourcePlatform.MICRO1,
        "micro1",
        "Software engineering, AI training and specialist work",
        ExtractionApproach.PAGE,
        "https://www.micro1.ai/experts/opportunities",
    ),
    PlatformSource(
        "himalayas",
        SourcePlatform.HIMALAYAS,
        "Himalayas",
        "Remote development, engineering and design roles",
        ExtractionApproach.JSON_API,
        "https://himalayas.app/jobs/api",
        attribution="Job data sourced from Himalayas; retain the Himalayas listing link.",
    ),
    PlatformSource(
        "remotive",
        SourcePlatform.REMOTIVE,
        "Remotive",
        "Remote technical and non-technical roles",
        ExtractionApproach.JSON_API,
        "https://remotive.com/api/remote-jobs",
        attribution="Job data sourced from Remotive; retain the Remotive listing link.",
    ),
    PlatformSource(
        "wellfound",
        SourcePlatform.WELLFOUND,
        "Wellfound",
        "Startup, engineering and AI roles",
        ExtractionApproach.PAGE,
        "https://wellfound.com/jobs",
    ),
    PlatformSource(
        "we-work-remotely",
        SourcePlatform.WE_WORK_REMOTELY,
        "We Work Remotely",
        "Global remote roles",
        ExtractionApproach.RSS,
        "https://weworkremotely.com/remote-jobs.rss",
        attribution="Job links sourced from We Work Remotely.",
    ),
)

_BY_KEY = {source.key: source for source in PLATFORM_SOURCES}
_ALIASES = {
    "outlier-ai": "outlier",
    "data-annotation": "dataannotation",
    "weworkremotely": "we-work-remotely",
    "we-work-remote": "we-work-remotely",
    "wwr": "we-work-remotely",
}


def normalize_platform_key(value: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "-", value.strip().casefold()).strip("-")
    return _ALIASES.get(key, key)


def get_platform_source(value: str) -> PlatformSource:
    key = normalize_platform_key(value)
    try:
        return _BY_KEY[key]
    except KeyError as exc:
        supported = ", ".join(_BY_KEY)
        raise ValueError(
            f"Unknown discovery platform '{value}'. Choose one of: {supported}."
        ) from exc


def platform_source_documents() -> list[dict[str, str | bool | None]]:
    return [source.as_dict() for source in PLATFORM_SOURCES]
