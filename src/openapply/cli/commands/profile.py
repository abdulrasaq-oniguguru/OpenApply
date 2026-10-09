"""`openapply profile show|edit`."""

from __future__ import annotations

from typing import Annotated

import click
import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from openapply.candidate.models import CandidateProfile
from openapply.candidate.service import CandidateService, ResumeStatus
from openapply.candidate.storage import ProfileError
from openapply.cli.commands import profile_import

app = typer.Typer(help="View and edit your candidate profile.", no_args_is_help=True)
console = Console()
app.command("import-site")(profile_import.import_site)

UNKNOWN = "[yellow]UNKNOWN (will ask)[/yellow]"


def _load_or_exit(service: CandidateService) -> CandidateProfile:
    try:
        profile = service.load()
    except ProfileError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    if profile is None:
        console.print("No profile yet. Run `openapply setup` first.")
        raise typer.Exit(1)
    return profile


def _tri(value: bool | None) -> str:
    return UNKNOWN if value is None else ("yes" if value else "no")


def _join(values: list[str]) -> str:
    return ", ".join(values) if values else "[dim]-[/dim]"


def _row(table: Table, label: str, value: str | None) -> None:
    table.add_row(label, value if value else "[dim]-[/dim]")


@app.command("show")
def show(
    as_json: Annotated[bool, typer.Option("--json", help="Print the raw profile JSON.")] = False,
) -> None:
    """Display the stored profile."""
    service = CandidateService()
    profile = _load_or_exit(service)
    if as_json:
        typer.echo(profile.model_dump_json(indent=2))
        return

    i, p, e = profile.identity, profile.preferences, profile.eligibility
    table = Table(title="Candidate profile", show_header=False, title_justify="left")
    table.add_column(style="bold")
    table.add_column()
    _row(table, "Name", i.full_name)
    _row(table, "Preferred name", i.preferred_name)
    _row(table, "Email", i.email)
    _row(table, "Phone", i.phone)
    _row(table, "Location", ", ".join(x for x in (i.city, i.country) if x))
    _row(table, "Timezone", i.timezone)
    _row(table, "LinkedIn", profile.links.linkedin)
    _row(table, "GitHub", profile.links.github)
    _row(table, "Portfolio", profile.links.portfolio)
    _row(table, "Summary", profile.summary)
    _row(table, "Skills", _join(profile.skills))
    _row(
        table,
        "Experience",
        "; ".join(f"{x.title} @ {x.company}" for x in profile.experience) or None,
    )
    _row(
        table,
        "Education",
        "; ".join(x.institution for x in profile.education) or None,
    )
    _row(table, "Target roles", _join(p.roles))
    _row(table, "Remote preference", p.remote_preference.value if p.remote_preference else None)
    _row(table, "Locations", _join(p.locations))
    salary = f"{p.salary_minimum:,} {p.salary_currency or ''}".strip() if p.salary_minimum else None
    _row(table, "Minimum salary", salary)
    _row(table, "Authorized countries", _join(e.authorized_countries))
    table.add_row("Needs sponsorship", _tri(e.requires_sponsorship))
    table.add_row("Willing to relocate", _tri(e.willing_to_relocate))
    status = service.resume_status(profile)
    if profile.resume is None:
        _row(table, "Resume", None)
    elif status is ResumeStatus.MISSING:
        table.add_row("Resume", f"[red]missing on disk:[/red] {profile.resume.original_name}")
    elif status is ResumeStatus.MODIFIED:
        table.add_row("Resume", f"[red]modified since added:[/red] {profile.resume.original_name}")
    else:
        table.add_row("Resume", f"{profile.resume.original_name} ({service.resume_file(profile)})")
    console.print(table)


@app.command("edit")
def edit() -> None:
    """Edit the full profile as JSON in your $EDITOR (or Notepad on Windows)."""
    service = CandidateService()
    profile = _load_or_exit(service)
    text = profile.model_dump_json(indent=2)
    original = text

    while True:
        edited = click.edit(text, extension=".json")
        if edited is None or edited.strip() == original.strip():
            console.print("No changes.")
            return
        text = edited
        try:
            updated = CandidateProfile.model_validate_json(edited)
        except ValidationError as exc:
            console.print("[red]The edited profile is not valid:[/red]")
            for err in exc.errors():
                loc = ".".join(str(part) for part in err["loc"]) or "(root)"
                console.print(f"  - {loc}: {err['msg']}")
            if not typer.confirm("Re-open the editor to fix it?", default=True):
                console.print("Changes discarded.")
                raise typer.Exit(1) from None
            continue
        if updated.resume != profile.resume:
            console.print("[red]The resume reference can't be edited by hand; use `setup`.[/red]")
            updated = updated.model_copy(update={"resume": profile.resume})
        service.save(updated)
        console.print("[green]Profile updated.[/green]")
        return
