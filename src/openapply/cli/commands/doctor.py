"""`openapply doctor`: system check."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console

from openapply.browser.browser import chromium_status
from openapply.candidate.service import CandidateService, ResumeStatus
from openapply.candidate.storage import ProfileError
from openapply.config.paths import app_dir
from openapply.config.settings import ConfigError, load_settings
from openapply.providers.models import ProviderStatus
from openapply.providers.registry import ProviderRegistry

console = Console()


def _line(ok: bool | None, label: str, detail: str) -> str:
    mark = "[green]✓[/green]" if ok else "[yellow]-[/yellow]" if ok is None else "[red]✗[/red]"
    return f"  {mark} {label:<18} {detail}"


def _provider_line(s: ProviderStatus) -> str:
    ok: bool | None = s.available if s.installed else False
    if s.installed and not s.supports_generation:
        ok = None
    version = f" ({s.version})" if s.version else ""
    return _line(ok, s.display_name, f"{s.detail}{version}")


def _browser_status() -> tuple[bool, str]:
    return asyncio.run(chromium_status())


def _profile_line() -> str:
    service = CandidateService()
    try:
        profile = service.load()
    except ProfileError as exc:
        return _line(False, "Profile", f"invalid: {exc}")
    if profile is None:
        return _line(None, "Profile", "not configured. Run `openapply setup`")
    missing = service.missing_required(profile)
    if missing:
        return _line(False, "Profile", f"incomplete, missing {', '.join(missing)}")
    status = service.resume_status(profile)
    if status is ResumeStatus.MISSING:
        return _line(False, "Profile", "configured, but the stored resume file is missing")
    if status is ResumeStatus.MODIFIED:
        return _line(False, "Profile", "stored resume changed since it was added; re-run `setup`")
    unknown = service.unknown_eligibility(profile)
    note = f", {len(unknown)} eligibility answer(s) unconfirmed" if unknown else ""
    return _line(True, "Profile", f"configured{note}")


def doctor() -> None:
    """Check AI providers, browser and candidate profile."""
    try:
        settings = load_settings()
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc

    statuses = asyncio.run(ProviderRegistry.from_settings(settings).detect())

    console.print("[bold]OpenApply System Check[/bold]\n")
    console.print("[bold]AI Providers[/bold]")
    for s in statuses:
        console.print(_provider_line(s))
    default = settings.default_provider or "none (set with `openapply providers set-default`)"
    console.print(f"  Default provider: {default}\n")

    console.print("[bold]Browser[/bold]")
    chromium_ok, chromium_detail = _browser_status()
    console.print(_line(chromium_ok, "Chromium", chromium_detail))
    console.print("\n[bold]Candidate Profile[/bold]")
    console.print(_profile_line())
    console.print(f"\nData directory: {app_dir()}")

    if not any(s.available and s.supports_generation for s in statuses):
        console.print("\n[red]No usable AI provider found.[/red]")
        raise typer.Exit(1)
