"""Conversation reports and local personal app."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console

from openapply.conversations.service import ConversationService
from openapply.reports.service import ReportService

app = typer.Typer(
    help="Chat with and inspect your personal application agent.", no_args_is_help=True
)
console = Console()


@app.command("chat")
def chat(message: str, timezone: Annotated[str, typer.Option()] = "UTC") -> None:
    """Send one message to the local agent conversation."""
    reply = ConversationService().chat(message, channel="cli", timezone=timezone)
    console.print(reply.assistant_message.text)


@app.command("report")
def report(
    timezone: Annotated[str, typer.Option(help="IANA timezone, e.g. Africa/Lagos.")] = "UTC",
    details: Annotated[bool, typer.Option(help="Show each application.")] = False,
) -> None:
    """Show today's factual application activity."""
    value = ReportService().today(timezone)
    console.print(f"Today: {value.short_text}")
    if details:
        for item in value.items:
            company = f" at {item.company}" if item.company else ""
            console.print(f"  {item.id[:8]}  {item.title}{company} — {item.state}")


@app.command("serve")
def serve(
    host: Annotated[
        str, typer.Option(help="Bind address; loopback is the safe default.")
    ] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1, max=65535)] = 8080,
) -> None:
    """Run the private OpenApply web app."""
    try:
        import uvicorn
    except ImportError as exc:
        console.print("Install the web extra first: `uv sync --extra web`.")
        raise typer.Exit(2) from exc
    if host not in {"127.0.0.1", "::1", "localhost"}:
        console.print(
            "[yellow]This app has no public-internet authentication yet. "
            "Bind to loopback and use an SSH tunnel.[/yellow]"
        )
    uvicorn.run("openapply.web.app:create_app", host=host, port=port, reload=False, factory=True)
