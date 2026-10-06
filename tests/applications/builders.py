"""Builders for application fields: classified by the real rules, like a scanned form."""

from __future__ import annotations

from collections.abc import Sequence

from openapply.applications.models import (
    ApplicationField,
    FieldOption,
    FieldType,
    Intent,
    sensitivity_for,
)
from openapply.applications.questions import Signals, classify

YES_NO = (("Yes", "yes"), ("No", "no"))
_counter = {"n": 0}


def make_field(
    label: str,
    field_type: FieldType = FieldType.TEXT,
    *,
    options: Sequence[tuple[str, str]] | None = None,
    required: bool = False,
    current_value: str | None = None,
    hint: str | None = None,
    name: str | None = None,
    max_length: int | None = None,
    accept: str | None = None,
    numeric: bool = False,
    intent: Intent | None = None,
    confidence: float | None = None,
) -> ApplicationField:
    result = classify(
        Signals(field_type=field_type, label=label, name=name or "", nearby=hint or "")
    )
    chosen = intent or result.intent
    _counter["n"] += 1
    return ApplicationField(
        id=f"oa-{_counter['n']}",
        label=label,
        type=field_type,
        required=required,
        options=[FieldOption(label=lab, value=val) for lab, val in (options or ())],
        current_value=current_value,
        sensitivity=sensitivity_for(chosen),
        confidence=result.confidence if confidence is None else confidence,
        intent=chosen,
        name=name,
        hint=hint,
        max_length=max_length,
        accept=accept,
        numeric=numeric,
    )
