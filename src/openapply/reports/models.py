from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ReportItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    company: str | None = None
    state: str
    created_at: str
    outcome: str | None = None


class ActivityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: str
    end: str
    submitted_unverified: int = 0
    confirmed: int = 0
    uncertain: int = 0
    drafts: int = 0
    needs_user: int = 0
    items: list[ReportItem] = Field(default_factory=list)
    short_text: str


class ApplicationDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    company: str | None = None
    state: str
    source_url: str
    destination: str | None = None
    outcome: str | None = None
    created_at: str
    revision_id: str | None = None
    revision: int | None = None
    blockers: list[dict[str, str]] = Field(default_factory=list)
    authorization: dict[str, object] | None = None
    fields: list[dict[str, object]] = Field(default_factory=list)
