"""`openapply browser ...`: manage the browser OpenApply drives."""

from __future__ import annotations

import subprocess
import sys

import typer
from rich.console import Console

app = typer.Typer(help="Manage the automation browser.", no_args_is_help=True)
console = Console()


@app.command("install")
def install() -> None:
    """Download Chromium for Playwright (one-time, a few hundred MB)."""
    console.print("Installing Chromium via Playwright...")
    result = subprocess.run(  # fixed argv, no shell, our own interpreter
        [sys.executable, "-m", "playwright", "install", "chromium"], check=False
    )
    if result.returncode != 0:
        console.print("[red]Chromium installation failed.[/red]")
        raise typer.Exit(result.returncode)
    console.print("[green]Chromium is installed.[/green]")
