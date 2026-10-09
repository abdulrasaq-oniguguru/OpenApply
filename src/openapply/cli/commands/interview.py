"""Interview commands for building confirmed career evidence."""

from __future__ import annotations

from typing import Annotated
from uuid import uuid4

import typer
from rich.console import Console

from openapply.interviews.models import EvidenceState
from openapply.interviews.service import InterviewService

app = typer.Typer(help="Build and review your personal career knowledge.", no_args_is_help=True)
console = Console()


@app.command("start")
def start(
    topic: Annotated[str, typer.Option(help="Interview focus.")] = "career story",
) -> None:
    """Start or resume an interview, one question at a time."""
    service = InterviewService()
    session = service.active() or service.start(topic)
    while session.current_turn is not None and session.status.value == "active":
        console.print(f"\n[bold]{session.current_turn.question}[/bold]")
        answer = typer.prompt("Your answer (blank to pause)", default="", show_default=False)
        if not answer.strip():
            console.print("Paused. Run `openapply interview start` to continue.")
            return
        session = service.answer(session.id, answer, request_id=str(uuid4()))
    console.print("[green]Interview complete.[/green] Review proposed evidence below.")


@app.command("evidence")
def evidence() -> None:
    """List proposed and confirmed knowledge from interviews."""
    items = InterviewService().list_evidence()
    if not items:
        console.print("No interview evidence yet.")
        return
    for item in items:
        console.print(
            f"[bold]{item.id[:8]}[/bold] [{item.state.value}] {item.kind}: "
            f"{item.claim.get('summary', item.source_quote)}"
        )


@app.command("confirm")
def confirm(item_id: str, revision: Annotated[int, typer.Option(min=1)] = 1) -> None:
    """Confirm a proposed evidence item by its full ID."""
    item = InterviewService().update_evidence(
        item_id, expected_revision=revision, state=EvidenceState.CONFIRMED
    )
    console.print(f"[green]Confirmed:[/green] {item.claim.get('summary', item.source_quote)}")
