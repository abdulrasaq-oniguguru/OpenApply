from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class TaskState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_PROVIDER = "waiting_provider"
    NEEDS_USER = "needs_user"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Task(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: str
    payload: dict[str, object] = Field(default_factory=dict)
    state: TaskState
    checkpoint: dict[str, object] = Field(default_factory=dict)
    attempts: int
    next_run_at: str | None = None
    lease_owner: str | None = None
    lease_expires_at: str | None = None
    last_error: str | None = None
    created_at: str
    updated_at: str
