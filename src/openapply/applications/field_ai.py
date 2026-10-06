"""AI fallback for fields no rule recognised. Restricted, validated, and optional."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from openapply.applications.models import (
    AI_ASSIGNABLE_INTENTS,
    ApplicationField,
    FieldType,
    Intent,
)
from openapply.prompts.field_classification import MAX_FIELDS, build_field_classification_prompt
from openapply.providers.base import AgentProvider
from openapply.providers.errors import ProviderError
from openapply.providers.structured import StructuredOutputError, generate_structured

log = logging.getLogger("openapply.applications")

AI_CONFIDENCE = 0.6
_FREE_TEXT = frozenset({FieldType.TEXT, FieldType.TEXTAREA})


class _Guess(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = ""
    intent: str = "unknown"


class _Guesses(BaseModel):
    model_config = ConfigDict(extra="ignore")

    fields: list[Any] = Field(default_factory=list, max_length=MAX_FIELDS * 2)


async def classify_with_ai(
    provider: AgentProvider,
    fields: Sequence[ApplicationField],
    *,
    timeout: float | None = None,
) -> dict[str, Intent]:
    """Intents for some of ``fields``. Anything doubtful is simply left out (stays unknown).

    Only ids that were asked about are accepted, only intents from the allowed list are
    accepted, and "open_ended" is only valid for free-text fields. Provider failures return
    an empty result: the AI is an optional extra, never a dependency.
    """
    asked = {f.id: f for f in fields[:MAX_FIELDS]}
    if not asked:
        return {}
    prompt = build_field_classification_prompt(list(asked.values()))
    try:
        result = await generate_structured(provider, prompt, _Guesses, timeout=timeout)
    except (ProviderError, StructuredOutputError) as exc:
        log.info("AI field classification unavailable: %s", exc)
        return {}
    chosen: dict[str, Intent] = {}
    for item in result.value.fields:
        try:
            guess = _Guess.model_validate(item)
            intent = Intent(guess.intent)
        except ValueError:
            continue
        field = asked.get(guess.id)
        if field is None or intent is Intent.UNKNOWN or intent not in AI_ASSIGNABLE_INTENTS:
            continue
        if intent is Intent.OPEN_ENDED and field.type not in _FREE_TEXT:
            continue
        chosen[guess.id] = intent
    return chosen
