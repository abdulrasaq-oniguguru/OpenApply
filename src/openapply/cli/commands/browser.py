"""`openapply browser ...`: manage the browser OpenApply drives."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict
from typing import Annotated

import typer
from rich.console import Console

from openapply.browser.page import BrowserError
from openapply.browser.sessions import BrowserSessionStore, run_login_browser

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


@app.command("login")
def login(
    url: str = typer.Argument(
        "https://accounts.google.com/",
        help="Public sign-in or job-site URL to open in OpenApply's browser.",
    ),
) -> None:
    """Sign in directly in OpenApply's dedicated reusable browser, then close it."""
    console.print(
        "Enter passwords and one-time codes only in the browser window. "
        "Close that window when sign-in is complete."
    )
    try:
        run_login_browser(url)
    except BrowserError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(
        "[green]Browser profile saved. Login will be verified when the site is used.[/green]"
    )


@app.command("session")
def session_status(
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show whether the reusable OpenApply browser profile is ready."""
    status = BrowserSessionStore().status()
    if as_json:
        typer.echo(
            json.dumps(
                {"schema": "openapply-browser-session/v1", **asdict(status)},
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    console.print(f"{status.state}: {status.detail or 'No reusable session yet.'}")
