"""Small interactive prompt helpers.

Convention for every optional prompt: Enter keeps the current value, ``-`` clears it.
"""

from __future__ import annotations

from collections.abc import Callable

import typer
from pydantic import ValidationError
from rich.console import Console

console = Console()

CLEAR = "-"


def ask_text(
    label: str,
    current: str | None = None,
    *,
    required: bool = False,
    validate: Callable[[str], None] | None = None,
) -> str | None:
    """Return the new value; ``None`` means empty/cleared."""
    while True:
        hint = f" [{current}]" if current else ""
        raw = typer.prompt(f"{label}{hint}", default="", show_default=False).strip()
        if raw == CLEAR:
            value: str | None = None
        elif raw == "":
            value = current or None
        else:
            value = raw
        if value is None and required:
            console.print(f"[red]{label} is required.[/red]")
            continue
        if value is not None and validate is not None:
            try:
                validate(value)
            except (ValueError, ValidationError) as exc:
                console.print(f"[red]{_short(exc)}[/red]")
                continue
        return value


def ask_list(label: str, current: list[str]) -> list[str]:
    """Comma-separated list. Enter keeps, ``-`` clears."""
    hint = f" [{', '.join(current)}]" if current else ""
    raw = typer.prompt(f"{label} (comma-separated){hint}", default="", show_default=False).strip()
    if raw == CLEAR:
        return []
    if raw == "":
        return list(current)
    seen: dict[str, str] = {}  # case-insensitive de-dupe, first spelling wins
    for item in raw.split(","):
        item = item.strip()
        if item:
            seen.setdefault(item.casefold(), item)
    return list(seen.values())


def ask_tristate(label: str, current: bool | None) -> bool | None:
    """yes / no / unknown. Unknown means OpenApply will ask instead of guessing."""
    shown = {True: "yes", False: "no", None: "unknown"}[current]
    while True:
        raw = (
            typer.prompt(f"{label} (yes/no/unknown) [{shown}]", default="", show_default=False)
            .strip()
            .lower()
        )
        if raw == "":
            return current
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        if raw in {"u", "unknown", CLEAR}:
            return None
        console.print("[red]Please answer yes, no or unknown.[/red]")


def ask_int(label: str, current: int | None) -> int | None:
    def check(value: str) -> None:
        if not value.isdigit():
            raise ValueError("Enter a whole number, e.g. 60000")

    raw = ask_text(label, str(current) if current is not None else None, validate=check)
    return int(raw) if raw is not None else None


def ask_choice(label: str, choices: list[str], current: str | None) -> str | None:
    def check(value: str) -> None:
        if value not in choices:
            raise ValueError(f"Choose one of: {', '.join(choices)}")

    return ask_text(f"{label} ({'/'.join(choices)})", current, validate=check)


def _short(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(str(e["msg"]).removeprefix("Value error, ") for e in exc.errors())
    return str(exc)
