"""Provider data models. Nothing here may carry credentials."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AgentResponse(BaseModel):
    text: str
    provider: str
    model: str | None = None
    # Transient diagnostics only: never persist or log this field. It may contain
    # unfiltered tool output. Providers whose raw output carries private envelope
    # fields (Claude) leave it as None.
    raw_output: str | None = None
    duration: float = 0.0
    return_code: int | None = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProviderStatus(BaseModel):
    name: str
    display_name: str
    installed: bool
    available: bool
    version: str | None = None
    detail: str = ""
    is_default: bool = False
    supports_generation: bool = True
