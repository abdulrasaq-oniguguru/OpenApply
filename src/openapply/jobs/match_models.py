"""Result types for candidate/job matching.

Scores are 0-100 integers (or ``None`` when the signal is unknown). Unknown is never
treated as zero or as a match: it is excluded from the overall score and lowers
``confidence`` instead.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from openapply.security.text import strip_control_chars

MAX_AI_ITEMS = 6
MAX_AI_ITEM_CHARS = 300
MAX_AI_SUMMARY_CHARS = 800


class Recommendation(StrEnum):
    STRONG_MATCH = "strong_match"
    POSSIBLE_MATCH = "possible_match"
    WEAK_MATCH = "weak_match"
    DO_NOT_APPLY = "do_not_apply"


# Worst to best, used when capping a recommendation.
RECOMMENDATION_ORDER = (
    Recommendation.DO_NOT_APPLY,
    Recommendation.WEAK_MATCH,
    Recommendation.POSSIBLE_MATCH,
    Recommendation.STRONG_MATCH,
)


class FactorResult(BaseModel):
    """One scored dimension and the reasons behind it."""

    name: str
    score: int | None  # 0-100, None = unknown
    weight: float
    reasons: list[str] = Field(default_factory=list)


class AIAnalysis(BaseModel):
    """Qualitative notes from the AI. Never changes a score or the recommendation."""

    model_config = ConfigDict(extra="ignore")

    summary: Annotated[str | None, BeforeValidator(lambda v: _text(v, MAX_AI_SUMMARY_CHARS))] = None
    strengths: Annotated[list[str], BeforeValidator(lambda v: _items(v))] = Field(
        default_factory=list
    )
    gaps: Annotated[list[str], BeforeValidator(lambda v: _items(v))] = Field(default_factory=list)
    concerns: Annotated[list[str], BeforeValidator(lambda v: _items(v))] = Field(
        default_factory=list
    )


def _text(value: Any, limit: int) -> Any:
    if isinstance(value, str):
        return " ".join(strip_control_chars(value).split())[:limit] or None
    return None if value is None else value


def _items(value: Any) -> Any:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if isinstance(value, list):
        cleaned = [_text(v, MAX_AI_ITEM_CHARS) for v in value if isinstance(v, str)]
        return [c for c in cleaned if c][:MAX_AI_ITEMS]
    return value


class JobMatch(BaseModel):
    job_id: str
    overall_score: int  # 0-100, over the factors that could be assessed
    skill_score: int | None = None
    experience_score: int | None = None
    location_score: int | None = None
    preference_score: int | None = None
    confidence: float  # 0-1: share of the scoring weight that had real data
    recommendation: Recommendation
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)  # required, not in the profile
    preferred_missing_skills: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)  # hard conflicts; force do_not_apply
    needs_confirmation: list[str] = Field(default_factory=list)  # answers only the user can give
    factors: list[FactorResult] = Field(default_factory=list)
    rationale: list[str] = Field(default_factory=list)  # why this recommendation (incl. caps)
    ai_analysis: AIAnalysis | None = None
