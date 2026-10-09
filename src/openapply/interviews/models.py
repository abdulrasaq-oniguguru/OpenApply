from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class InterviewStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"


class InterviewTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    session_id: str
    sequence: int
    question: str
    answer: str | None = None
    created_at: str
    answered_at: str | None = None


class InterviewSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    topic: str
    status: InterviewStatus
    created_at: str
    updated_at: str
    revision: int
    current_turn: InterviewTurn | None = None


class EvidenceState(StrEnum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: str
    source_turn_id: str | None
    source_quote: str
    claim: dict[str, str]
    tags: list[str] = Field(default_factory=list)
    state: EvidenceState
    revision: int
    created_at: str
    updated_at: str


class KnowledgeContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    evidence: list[EvidenceItem] = Field(default_factory=list)
    selection_reasons: dict[str, str] = Field(default_factory=dict)
