"""`openapply profile import-site`: fill contact details from the user's own website."""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from openapply.browser.browser import PlaywrightFetcher
from openapply.browser.page import BrowserError, PageFetcher
from openapply.candidate.models import CandidateProfile
from openapply.candidate.service import CandidateService
from openapply.candidate.site_import import Found, SiteFindings, extract_from_page
from openapply.candidate.storage import ProfileError
from openapply.cli.render import esc

console = Console()

_LABELS = {
    "full_name": "Name",
    "email": "Email",
    "phone": "Phone",
    "city": "City",
    "country": "Country",
    "linkedin": "LinkedIn",
    "github": "GitHub",
}
_LINK_FIELDS = {"linkedin", "github"}


def _current(profile: CandidateProfile, name: str) -> str | None:
    value: str | None = getattr(profile.links if name in _LINK_FIELDS else profile.identity, name)
    return value or None


def apply_value(profile: CandidateProfile, name: str, value: str) -> CandidateProfile:
    if name in _LINK_FIELDS:
        links = profile.links.model_copy(update={name: value})
        return profile.model_copy(update={"links": links})
    identity = profile.identity.model_copy(update={name: value})
    return profile.model_copy(update={"identity": identity})


def _choose(name: str, options: list[Found]) -> Found | None:
    console.print(f"\n[yellow]{_LABELS[name]}: the site gives different values.[/yellow]")
    for number, option in enumerate(options, 1):
        console.print(f"  {number}. {esc(option.value)}  [dim]({esc(option.source)})[/dim]")
    answer = typer.prompt("Pick a number, or press Enter to skip", default="", show_default=False)
    if answer.isdigit() and 1 <= int(answer) <= len(options):
        return options[int(answer) - 1]
    return None


def review_and_apply(
    profile: CandidateProfile, findings: SiteFindings, *, assume_yes: bool
) -> CandidateProfile | None:
    """Show every proposed change; return the updated profile, or None if nothing is saved."""
    chosen = dict(findings.fields)
    for name, options in findings.ambiguous.items():
        picked = None if assume_yes else _choose(name, options)
        if picked is not None:
            chosen[name] = picked
    table = Table(title="From your website", title_justify="left")
    for column in ("Field", "Now", "Found", "Source"):
        table.add_column(column)
    changes: dict[str, Found] = {}
    for name, label in _LABELS.items():
        found = chosen.get(name)
        now = _current(profile, name)
        if found is None or now == found.value:
            continue
        changes[name] = found
        table.add_row(label, esc(now or "-"), esc(found.value), esc(found.source))
    if not changes:
        console.print("Nothing new to add from the site.")
        return None
    console.print(table)
    if not assume_yes and not typer.confirm("Save these to your profile?", default=False):
        console.print("Nothing saved.")
        return None
    for name, found in changes.items():
        try:
            profile = apply_value(profile, name, found.value)
        except ValueError as exc:
            console.print(f"[red]Skipped {_LABELS[name]}: {esc(exc)}[/red]")
    return profile


def import_site(
    url: Annotated[str, typer.Argument(help="Your own website or profile page.")],
    yes: Annotated[bool, typer.Option("--yes", help="Save settled values without asking.")] = False,
) -> None:
    """Read your name, email, phone and profile links from your website's code."""
    fetcher: PageFetcher = PlaywrightFetcher()
    try:
        page = asyncio.run(fetcher.fetch(url))
    except BrowserError as exc:
        console.print(f"[red]{esc(exc)}[/red]")
        raise typer.Exit(2) from exc
    findings = extract_from_page(page)
    service = CandidateService()
    try:
        profile = service.load_or_new()
    except ProfileError as exc:
        console.print(f"[red]{esc(exc)}[/red]")
        raise typer.Exit(2) from exc
    updated = review_and_apply(profile, findings, assume_yes=yes)
    if updated is None:
        return
    if not updated.links.portfolio:
        links = updated.links.model_copy(update={"portfolio": page.final_url})
        updated = updated.model_copy(update={"links": links})
    service.save(updated)
    console.print("[green]Profile updated.[/green] Review it with `openapply profile show`.")
