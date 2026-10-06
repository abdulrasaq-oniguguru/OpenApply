"""`openapply providers ...` commands."""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from openapply.config.settings import ConfigError, Settings, load_settings, save_settings
from openapply.providers.errors import ProviderNotInstalled
from openapply.providers.registry import ProviderRegistry

app = typer.Typer(help="Inspect and configure AI providers.", no_args_is_help=True)
console = Console()


def _load_or_exit() -> Settings:
    try:
        return load_settings()
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc


def _yes(value: bool) -> str:
    return "[green]yes[/green]" if value else "[red]no[/red]"


@app.command("list")
def list_providers() -> None:
    """Show detected providers and which one is the default."""
    settings = _load_or_exit()
    statuses = asyncio.run(ProviderRegistry.from_settings(settings).detect())
    table = Table()
    for column in ("NAME", "INSTALLED", "READY", "DEFAULT", "VERSION", "DETAIL"):
        table.add_column(column)
    for s in statuses:
        table.add_row(
            s.name,
            _yes(s.installed),
            _yes(s.available),
            "*" if s.is_default else "",
            s.version or "-",
            s.detail,
        )
    console.print(table)


@app.command("set-default")
def set_default(
    provider: Annotated[str, typer.Argument(help="Provider name, e.g. codex, claude, ollama.")],
    model: Annotated[str | None, typer.Option("--model", "-m", help="Model to use.")] = None,
) -> None:
    """Choose the provider OpenApply uses by default."""
    settings = _load_or_exit()
    registry = ProviderRegistry.from_settings(settings)
    try:
        chosen = registry.get(provider)
    except ProviderNotInstalled as exc:
        console.print(f"[red]{exc.message}[/red]")
        raise typer.Exit(2) from exc
    if not chosen.supports_generation:
        console.print(f"[red]{provider} is detected only; generation is not implemented yet.[/red]")
        raise typer.Exit(2)
    if not asyncio.run(chosen.is_installed()):
        console.print(f"[red]{provider} is not installed on this machine.[/red]")
        raise typer.Exit(1)
    if model is not None and provider == "ollama":
        known = asyncio.run(chosen.list_models()) if hasattr(chosen, "list_models") else []
        if known and model not in known:
            console.print(
                f"[yellow]Warning: '{model}' is not among local models: {', '.join(known)}[/yellow]"
            )
    path = save_settings(settings.with_default(provider, model))
    suffix = f" with model {model}" if model else ""
    console.print(f"Default provider set to [bold]{provider}[/bold]{suffix} ({path})")
