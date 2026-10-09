"""Optional paired Telegram conversation channel."""

from __future__ import annotations

import asyncio
import os
from typing import Annotated

import typer
from rich.console import Console

from openapply.channels.telegram import TelegramChannel, TelegramError

app = typer.Typer(help="Pair and run the optional Telegram channel.", no_args_is_help=True)
console = Console()


def _channel(token: str | None) -> TelegramChannel:
    value = token or os.environ.get("OPENAPPLY_TELEGRAM_BOT_TOKEN")
    if not value:
        raise TelegramError(
            "Set OPENAPPLY_TELEGRAM_BOT_TOKEN or pass --token. The token remains outside "
            "OpenApply's database."
        )
    return TelegramChannel(value)


@app.command("pair")
def pair(token: Annotated[str | None, typer.Option(hidden=True)] = None) -> None:
    """Create a 15-minute pairing code for a private Telegram chat."""
    try:
        code = _channel(token).create_pairing_code()
    except TelegramError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    console.print(f"Send this to your bot in a private chat: [bold]/start {code}[/bold]")
    console.print("The code expires in 15 minutes and can be used once.")


@app.command("run")
def run(
    timezone: Annotated[str, typer.Option()] = "UTC",
    token: Annotated[str | None, typer.Option(hidden=True)] = None,
) -> None:
    """Run Telegram long polling for the paired private chat."""
    try:
        channel = _channel(token)
        asyncio.run(channel.run_forever(timezone=timezone))
    except TelegramError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    except KeyboardInterrupt:
        console.print("Telegram channel stopped.")


@app.command("disconnect")
def disconnect(token: Annotated[str | None, typer.Option(hidden=True)] = None) -> None:
    """Revoke every active Telegram pairing for this local OpenApply profile."""
    count = _channel(token).disconnect()
    console.print(f"Disconnected {count} Telegram binding(s).")
