"""Structured (JSON -> Pydantic) generation on top of any provider.

AI output is never trusted: it is parsed leniently from text, validated against a
schema, and retried once with a precise, *sanitized* description of what was wrong.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from openapply.providers.base import AgentProvider
from openapply.providers.models import AgentResponse

log = logging.getLogger("openapply.providers.structured")

_FENCE_RE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*\n(?P<body>.*?)\n\s*```\s*$", re.S)


class NoJsonObjectError(ValueError):
    """The reply contained no JSON object. The message is fixed text, safe to show."""

    def __init__(self) -> None:
        super().__init__("no JSON object found in the reply")


class StructuredOutputError(Exception):
    """The provider answered, but never with a usable object."""

    def __init__(self, message: str, *, attempts: int) -> None:
        self.attempts = attempts
        super().__init__(message)


@dataclass(frozen=True)
class StructuredResult[T: BaseModel]:
    value: T
    response: AgentResponse
    attempts: int


def extract_json_object(text: str) -> dict[str, Any]:
    """Find the first JSON object in ``text`` (tolerates code fences and surrounding prose)."""
    candidate = text.strip()
    fenced = _FENCE_RE.match(candidate)
    if fenced:
        candidate = fenced.group("body").strip()
    decoder = json.JSONDecoder()
    start = candidate.find("{")
    while start != -1:
        try:
            value, _ = decoder.raw_decode(candidate, start)
        except json.JSONDecodeError:
            start = candidate.find("{", start + 1)
            continue
        if isinstance(value, dict):
            return value
        start = candidate.find("{", start + 1)
    raise NoJsonObjectError


# Fixed phrases keyed by Pydantic's stable error *types*. Pydantic's own ``msg`` is never
# used: for custom validators it can embed the rejected (model-supplied) value.
_PHRASES = {
    "missing": "required field is missing",
    "string_type": "must be a string",
    "int_type": "must be a whole number",
    "int_parsing": "must be a whole number",
    "float_type": "must be a number",
    "float_parsing": "must be a number",
    "bool_type": "must be true or false",
    "bool_parsing": "must be true or false",
    "list_type": "must be a list",
    "dict_type": "must be an object",
    "model_type": "must be an object",
    "none_required": "must be null",
    "literal_error": "is not an allowed value",
    "enum": "is not an allowed value",
    "extra_forbidden": "is not an allowed field",
    "string_too_long": "is too long",
    "too_long": "has too many items",
    "string_too_short": "is too short",
    "too_short": "has too few items",
    "value_error": "failed validation",
    "assertion_error": "failed validation",
}
_UNKNOWN_FIELD = "<field>"


def schema_field_names(schema: type[BaseModel]) -> frozenset[str]:
    """Every property name the schema defines, at any nesting depth."""
    names: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            props = node.get("properties")
            if isinstance(props, dict):
                names.update(k for k in props if isinstance(k, str))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    try:
        walk(schema.model_json_schema())
    except Exception:  # exotic types without a JSON schema
        names.update(schema.model_fields)
    return frozenset(names)


def describe_validation_error(exc: ValidationError, schema: type[BaseModel]) -> str:
    """Describe what was wrong using only text *we* control.

    Output is built from (a) field names that exist in ``schema`` and list indexes, and
    (b) fixed phrases chosen by Pydantic's error type. No model-supplied value, key or
    message can reach the correction prompt.
    """
    known = schema_field_names(schema)
    parts = []
    for err in exc.errors()[:8]:
        loc = ".".join(
            str(p) if isinstance(p, int) or p in known else _UNKNOWN_FIELD for p in err["loc"]
        )
        phrase = _PHRASES.get(err["type"], "is invalid")
        parts.append(f"{loc or '(root)'}: {phrase}")
    return "; ".join(parts)


def default_repair_prompt(original_prompt: str, problem: str) -> str:
    return (
        f"{original_prompt}\n\n"
        "CORRECTION REQUIRED\n"
        f"Your previous reply could not be used ({problem}).\n"
        "Reply again with ONLY the corrected JSON object. No prose, no code fences."
    )


async def generate_structured[T: BaseModel](
    provider: AgentProvider,
    prompt: str,
    schema: type[T],
    *,
    timeout: float | None = None,
    max_attempts: int = 2,
    repair_prompt: Callable[[str, str], str] = default_repair_prompt,
) -> StructuredResult[T]:
    """Ask ``provider`` for a ``schema`` object, retrying once if the reply is unusable.

    Provider failures (not installed, timeout, rate limit, ...) propagate immediately:
    only *malformed output* is retried.
    """
    current_prompt = prompt
    problem = "no attempt made"
    for attempt in range(1, max_attempts + 1):
        response = await provider.generate(current_prompt, timeout=timeout)
        try:
            return StructuredResult(
                value=schema.model_validate(extract_json_object(response.text)),
                response=response,
                attempts=attempt,
            )
        except ValidationError as exc:
            problem = describe_validation_error(exc, schema)
        except NoJsonObjectError as exc:
            problem = str(exc)  # fixed text
        except ValueError:
            problem = "the reply could not be processed"
        log.info("structured output attempt %d/%d unusable: %s", attempt, max_attempts, problem)
        current_prompt = repair_prompt(prompt, problem)
    raise StructuredOutputError(
        f"The AI did not return a valid {schema.__name__} after {max_attempts} attempts "
        f"({problem}).",
        attempts=max_attempts,
    )
