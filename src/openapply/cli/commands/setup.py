"""`openapply setup`: interactive candidate profile creation."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.console import Console

from openapply.candidate.models import (
    CandidateProfile,
    Eligibility,
    Identity,
    Links,
    Preferences,
    RemotePreference,
)
from openapply.candidate.parser import LocalResumeTextExtractor, ResumeExtractionError
from openapply.candidate.resume_import import local_draft, merge_resume_draft
from openapply.candidate.service import CandidateService, ResumeError
from openapply.candidate.storage import ProfileError
from openapply.cli import prompts as ask
from openapply.config.settings import ConfigError, load_settings, save_settings
from openapply.providers.models import ProviderStatus
from openapply.providers.registry import ProviderRegistry

console = Console()


def _check_email(value: str) -> None:
    Identity(email=value)


def _detect_providers() -> list[ProviderStatus]:
    return asyncio.run(ProviderRegistry.from_settings(load_settings()).detect())


def _section(title: str) -> None:
    console.print(f"\n[bold]{title}[/bold]")


def _prompt_identity(current: Identity) -> Identity:
    _section("About you")
    full_name = ask.ask_text("Full name", current.full_name or None, required=True)
    email = ask.ask_text("Email", current.email or None, required=True, validate=_check_email)
    return Identity(
        full_name=full_name or "",
        email=email or "",
        preferred_name=ask.ask_text("Preferred name", current.preferred_name),
        phone=ask.ask_text("Phone", current.phone),
        city=ask.ask_text("City", current.city),
        country=ask.ask_text("Country", current.country),
        timezone=ask.ask_text("Timezone (e.g. Africa/Lagos)", current.timezone),
    )


def _prompt_links(current: Links) -> Links:
    _section("Links")
    return Links(
        linkedin=ask.ask_text("LinkedIn URL", current.linkedin),
        github=ask.ask_text("GitHub URL", current.github),
        portfolio=ask.ask_text("Portfolio URL", current.portfolio),
    )


def _prompt_preferences(current: Preferences) -> Preferences:
    _section("What you're looking for")
    roles = ask.ask_list("Target roles", current.roles)
    industries = ask.ask_list("Industries", current.industries)
    employment_types = ask.ask_list("Employment types (e.g. full-time)", current.employment_types)
    remote = ask.ask_choice(
        "Remote preference",
        [r.value for r in RemotePreference],
        current.remote_preference.value if current.remote_preference else None,
    )
    locations = ask.ask_list("Preferred locations", current.locations)
    salary = ask.ask_int("Minimum salary (number)", current.salary_minimum)
    currency = (
        ask.ask_text("Salary currency (e.g. USD)", current.salary_currency) if salary else None
    )
    return Preferences(
        roles=roles,
        industries=industries,
        remote_preference=RemotePreference(remote) if remote else None,
        locations=locations,
        salary_minimum=salary,
        salary_currency=currency,
        employment_types=employment_types,
    )


def _prompt_eligibility(current: Eligibility) -> Eligibility:
    _section("Eligibility")
    console.print(
        "[dim]Answer only what you are sure of. 'unknown' means OpenApply will ask you each "
        "time instead of guessing.[/dim]"
    )
    return Eligibility(
        authorized_countries=ask.ask_list(
            "Countries you are authorized to work in", current.authorized_countries
        ),
        requires_sponsorship=ask.ask_tristate(
            "Will you require visa sponsorship?", current.requires_sponsorship
        ),
        willing_to_relocate=ask.ask_tristate("Willing to relocate?", current.willing_to_relocate),
    )


def _prompt_skills(profile: CandidateProfile) -> CandidateProfile:
    _section("Skills and summary")
    return profile.model_copy(
        update={
            "skills": ask.ask_list("Skills", profile.skills),
            "summary": ask.ask_text("Short professional summary", profile.summary),
        }
    )


def _prompt_resume(service: CandidateService, profile: CandidateProfile) -> CandidateProfile:
    _section("Resume")
    current = profile.resume.original_name if profile.resume else None
    while True:
        raw = ask.ask_text("Path to resume (.pdf or .docx)", current)
        if raw is None or (profile.resume and raw == current):
            return profile
        try:
            attached = service.attach_resume(profile, Path(raw.strip("\"'")))
        except ResumeError as exc:
            console.print(f"[red]{exc}[/red]")
            current = None
            continue
        resume_path = service.resume_file(attached)
        if resume_path is None or attached.resume is None:
            return attached
        try:
            text = LocalResumeTextExtractor().extract_text(resume_path)
            return merge_resume_draft(attached, local_draft(text), attached.resume)
        except ResumeExtractionError as exc:
            console.print(f"[yellow]Resume attached, but profile extraction failed: {exc}[/yellow]")
            return attached


def _prompt_default_provider() -> None:
    _section("AI provider")
    try:
        settings = load_settings()
        usable = [s for s in _detect_providers() if s.available and s.supports_generation]
    except ConfigError as exc:
        console.print(f"[yellow]Skipping provider choice: {exc}[/yellow]")
        return
    if not usable:
        console.print(
            "[yellow]No ready AI provider found. Run `openapply doctor` for details.[/yellow]"
        )
        return
    names = [s.name for s in usable]
    current = settings.default_provider if settings.default_provider in names else names[0]
    console.print(f"Ready providers: {', '.join(names)}")
    choice = ask.ask_choice("Default provider", names, current) or current
    if choice != settings.default_provider:
        save_settings(settings.with_default(choice))
        console.print(f"Default provider set to [bold]{choice}[/bold]")


def setup() -> None:
    """Create or update your local candidate profile."""
    service = CandidateService()
    try:
        profile = service.load_or_new()
    except ProfileError as exc:
        console.print(f"[red]{exc}[/red]")
        console.print("Fix or remove the file, or use `openapply profile edit`.")
        raise typer.Exit(2) from exc

    console.print("[bold]OpenApply setup[/bold]")
    console.print(
        "[dim]Enter keeps the current value. '-' clears it. Data stays on this machine.[/dim]"
    )

    profile = profile.model_copy(
        update={
            "identity": _prompt_identity(profile.identity),
            "links": _prompt_links(profile.links),
        }
    )
    profile = _prompt_skills(profile)
    profile = profile.model_copy(
        update={
            "preferences": _prompt_preferences(profile.preferences),
            "eligibility": _prompt_eligibility(profile.eligibility),
        }
    )
    profile = _prompt_resume(service, profile)
    path = service.save(profile)
    _prompt_default_provider()

    console.print(f"\n[green]Profile saved[/green] to {path}")
    console.print(
        "Work experience, education and projects are edited with `openapply profile edit`."
    )
    unknown = service.unknown_eligibility(profile)
    if unknown:
        console.print(f"[yellow]Unconfirmed eligibility answers:[/yellow] {', '.join(unknown)}")
