"""Job schemas.

Two layers on purpose:

* ``JobExtraction`` is what we ask the AI to produce. It is lenient (unknown enum
  values become ``unknown``, over-long strings are truncated) so a slightly sloppy
  but usable answer does not burn a retry.
* ``JobPosting`` is the normalized record the rest of OpenApply uses. Its identity
  and provenance fields (id, source_url, platform, timestamp) are computed by us,
  never taken from model output.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from openapply.security.text import strip_control_chars
from openapply.security.urls import canonicalize_url, safe_application_url

MAX_ITEM_CHARS = 600
MAX_ITEMS = 40
MAX_DESCRIPTION_CHARS = 6000


class RemoteStatus(StrEnum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"
    UNKNOWN = "unknown"


class EmploymentType(StrEnum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    INTERNSHIP = "internship"
    TEMPORARY = "temporary"
    UNKNOWN = "unknown"


class SalaryPeriod(StrEnum):
    YEAR = "year"
    MONTH = "month"
    WEEK = "week"
    DAY = "day"
    HOUR = "hour"


class SourcePlatform(StrEnum):
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"
    LINKEDIN = "linkedin"
    INDEED = "indeed"
    MERCOR = "mercor"
    OUTLIER = "outlier"
    DATAANNOTATION = "dataannotation"
    ALIGNERR = "alignerr"
    MICRO1 = "micro1"
    HIMALAYAS = "himalayas"
    REMOTIVE = "remotive"
    WELLFOUND = "wellfound"
    WE_WORK_REMOTELY = "we_work_remotely"
    GENERIC = "generic"


def _clean(value: Any, limit: int) -> Any:
    if isinstance(value, str):
        value = " ".join(strip_control_chars(value).split())
        return value[:limit] or None
    return value


def _short(value: Any) -> Any:
    return _clean(value, MAX_ITEM_CHARS)


def _long(value: Any) -> Any:
    if isinstance(value, str):
        # Keep paragraph breaks in descriptions.
        lines = [" ".join(line.split()) for line in strip_control_chars(value).splitlines()]
        return "\n".join(line for line in lines if line)[:MAX_DESCRIPTION_CHARS] or None
    return value


def _enum_or_unknown(enum: type[StrEnum]) -> Any:
    def convert(value: Any) -> Any:
        if value is None:
            return enum("unknown")
        text = str(value).strip().lower().replace("-", "_").replace(" ", "_")
        try:
            return enum(text)
        except ValueError:
            return enum("unknown")

    return BeforeValidator(convert)


def _string_list(value: Any) -> Any:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if isinstance(value, list):
        cleaned = [_clean(item, MAX_ITEM_CHARS) for item in value if isinstance(item, str)]
        return [item for item in cleaned if item][:MAX_ITEMS]
    return value


ShortText = Annotated[str | None, BeforeValidator(_short)]
LongText = Annotated[str | None, BeforeValidator(_long)]
TextList = Annotated[list[str], BeforeValidator(_string_list)]
Remote = Annotated[RemoteStatus, _enum_or_unknown(RemoteStatus)]
Employment = Annotated[EmploymentType, _enum_or_unknown(EmploymentType)]


def _salary_number(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        digits = value.replace(",", "").strip()
        try:
            value = float(digits)
        except ValueError:
            return None
    if isinstance(value, int | float):
        return int(value) if value >= 0 else None
    return None


def _salary_period(value: Any) -> Any:
    try:
        return SalaryPeriod(str(value).strip().lower()) if value else None
    except ValueError:
        return None


def _http_url(value: Any) -> Any:
    """Keep absolute http(s) URLs and same-site relative paths; drop everything else.

    Relative paths (``/apply``) are resolved against the posting URL later. Anything with
    another scheme (``javascript:``, ``file:``, ``data:``) is discarded.
    """
    if not isinstance(value, str):
        return None
    value = value.strip()
    parts = urlsplit(value)
    if parts.scheme in {"http", "https"}:
        return value if parts.hostname else None
    if not parts.scheme and not parts.netloc and value.startswith(("/", "?")):
        return value
    return None


class JobExtraction(BaseModel):
    """The JSON object the AI is asked to return."""

    model_config = ConfigDict(extra="ignore")

    is_job_posting: bool = True
    title: ShortText = None
    company: ShortText = None
    location: ShortText = None
    employment_type: Employment = EmploymentType.UNKNOWN
    remote_status: Remote = RemoteStatus.UNKNOWN
    description: LongText = None
    responsibilities: TextList = Field(default_factory=list)
    requirements: TextList = Field(default_factory=list)
    preferred_requirements: TextList = Field(default_factory=list)
    salary_min: Annotated[int | None, BeforeValidator(_salary_number)] = None
    salary_max: Annotated[int | None, BeforeValidator(_salary_number)] = None
    currency: ShortText = None
    salary_period: Annotated[SalaryPeriod | None, BeforeValidator(_salary_period)] = None
    application_url: Annotated[str | None, BeforeValidator(_http_url)] = None

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, value: str | None) -> str | None:
        return value.upper()[:8] if value else None

    @model_validator(mode="after")
    def _order_salary(self) -> JobExtraction:
        if self.salary_min and self.salary_max and self.salary_min > self.salary_max:
            self.salary_min, self.salary_max = self.salary_max, self.salary_min
        return self


class JobPosting(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source_url: str
    source_platform: SourcePlatform = SourcePlatform.GENERIC
    title: str
    company: str | None = None
    location: str | None = None
    employment_type: EmploymentType = EmploymentType.UNKNOWN
    remote_status: RemoteStatus = RemoteStatus.UNKNOWN
    description: str | None = None
    responsibilities: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    preferred_requirements: list[str] = Field(default_factory=list)
    salary_min: int | None = None
    salary_max: int | None = None
    currency: str | None = None
    salary_period: SalaryPeriod | None = None
    application_url: str | None = None
    extracted_at: datetime
    # Full visible page text (truncated). Needed by later milestones; never logged.
    raw_text: str | None = Field(default=None, repr=False)

    @classmethod
    def from_extraction(
        cls,
        extraction: JobExtraction,
        *,
        source_url: str,
        platform: SourcePlatform,
        raw_text: str | None = None,
        now: datetime | None = None,
    ) -> JobPosting:
        if not extraction.title:
            raise ValueError("no job title was extracted")
        return cls(
            id=job_id(source_url),
            source_url=source_url,
            source_platform=platform,
            title=extraction.title,
            company=extraction.company,
            location=extraction.location,
            employment_type=extraction.employment_type,
            remote_status=extraction.remote_status,
            description=extraction.description,
            responsibilities=extraction.responsibilities,
            requirements=extraction.requirements,
            preferred_requirements=extraction.preferred_requirements,
            salary_min=extraction.salary_min,
            salary_max=extraction.salary_max,
            currency=extraction.currency,
            salary_period=extraction.salary_period,
            application_url=safe_application_url(extraction.application_url, source_url),
            extracted_at=now or datetime.now(UTC),
            raw_text=raw_text,
        )


def job_id(source_url: str) -> str:
    """Deterministic id: the same posting URL always yields the same id."""
    return hashlib.sha256(canonicalize_url(source_url).encode("utf-8")).hexdigest()[:16]
