"""`openapply analyze URL`: read a job page, show it, and score it against your profile."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated

import typer
from rich.console import Console

from openapply.browser.browser import PlaywrightFetcher
from openapply.browser.page import BrowserError, BrowserNotInstalled, PageFetcher
from openapply.candidate.models import CandidateProfile
from openapply.candidate.service import CandidateService
from openapply.candidate.storage import ProfileError
from openapply.cli.render import esc, render_job, render_match
from openapply.config.settings import ConfigError, Settings, load_settings
from openapply.jobs.extractor import ExtractionError
from openapply.jobs.match_models import JobMatch
from openapply.jobs.service import JobService, MatchService
from openapply.providers.base import AgentProvider
from openapply.providers.errors import (
    ProviderAuthenticationRequired,
    ProviderError,
    ProviderRateLimited,
)
from openapply.providers.registry import ProviderRegistry
from openapply.providers.structured import StructuredOutputError
from openapply.security.urls import UnsafeURLError, validate_job_url

console = Console()
err = Console(stderr=True)

DEFAULT_TIMEOUT_SECONDS = 300.0


def _build_fetcher(allow_local: bool) -> PageFetcher:
    return PlaywrightFetcher(allow_local=allow_local)


def _build_provider(settings: Settings, name: str | None, model: str | None) -> AgentProvider:
    chosen = name or settings.default_provider
    if chosen is None:
        raise ProviderError(
            "openapply",
            "No AI provider selected. Run `openapply providers set-default <name>` "
            "or pass --provider.",
        )
    if model is not None:
        settings = settings.with_default(chosen, model)
    provider = ProviderRegistry.from_settings(settings).get(chosen)
    if not provider.supports_generation:
        raise ProviderError(chosen, "This provider is detected only; it cannot generate yet.")
    return provider


def _fail(message: str, code: int = 1) -> typer.Exit:
    err.print(f"[red]{esc(message)}[/red]")
    return typer.Exit(code)


def _warn(message: str) -> None:
    err.print(f"[yellow]Warning:[/yellow] {esc(message)}")


def _load_profile(*, wanted: bool, quiet: bool) -> CandidateProfile | None:
    """The saved profile, or None. Missing/unreadable profiles never fail an analysis."""
    if not wanted:
        return None
    try:
        profile = CandidateService().load()
    except ProfileError as exc:
        _warn(f"Skipping the match: {exc}")
        return None
    if profile is None and not quiet:
        err.print("[dim]Run `openapply setup` to see how well you match this job.[/dim]")
    return profile


def analyze(
    url: Annotated[str, typer.Argument(help="Job posting URL (http or https).")],
    provider: Annotated[
        str | None, typer.Option("--provider", "-p", help="Provider to use.")
    ] = None,
    model: Annotated[str | None, typer.Option("--model", "-m", help="Model override.")] = None,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print {job, match, warnings} as JSON.")
    ] = False,
    match: Annotated[
        bool, typer.Option("--match/--no-match", help="Score the job against your profile.")
    ] = True,
    ai_notes: Annotated[
        bool,
        typer.Option("--ai/--no-ai", help="Add AI notes to the match (one extra AI call)."),
    ] = True,
    allow_local: Annotated[
        bool, typer.Option("--allow-local", help="Allow localhost/private addresses.")
    ] = False,
    timeout: Annotated[
        float, typer.Option("--timeout", min=1, help="AI call timeout in seconds.")
    ] = DEFAULT_TIMEOUT_SECONDS,
) -> None:
    """Read a job posting, then score it against your profile if you have one."""
    try:
        url = validate_job_url(url, allow_local=allow_local)
    except UnsafeURLError as exc:
        raise _fail(str(exc), 2) from exc
    try:
        settings = load_settings()
        ai = _build_provider(settings, provider, model)
    except ConfigError as exc:
        raise _fail(str(exc), 2) from exc
    except ProviderError as exc:
        raise _fail(exc.message, 2) from exc

    service = JobService(_build_fetcher(allow_local), ai, timeout=timeout)
    try:
        with err.status(f"Reading the page and asking {ai.name}..."):
            result = asyncio.run(service.analyze(url))
    except BrowserNotInstalled as exc:
        raise _fail(str(exc)) from exc
    except BrowserError as exc:
        raise _fail(f"Browser: {exc}") from exc
    except ProviderAuthenticationRequired as exc:
        raise _fail(f"{exc.message}\nSign in to {ai.name} in its own app, then retry.") from exc
    except ProviderRateLimited as exc:
        raise _fail(f"{exc.message}\nTry again later or use --provider.") from exc
    except ProviderError as exc:
        raise _fail(exc.message) from exc
    except (StructuredOutputError, ExtractionError) as exc:
        raise _fail(str(exc)) from exc

    warnings = list(result.warnings)
    job_match: JobMatch | None = None
    profile = _load_profile(wanted=match, quiet=as_json)
    if profile is not None:
        matcher = MatchService(ai if ai_notes else None, timeout=timeout)
        try:
            with err.status("Scoring the match..."):
                matched = asyncio.run(matcher.match(profile, result.job, with_ai=ai_notes))
        except ProviderError as exc:  # not reachable today; kept so scoring can never crash analyze
            raise _fail(exc.message) from exc
        job_match = matched.match
        warnings += matched.warnings

    for warning in warnings:
        _warn(warning)
    if as_json:
        payload = {
            "job": json.loads(result.job.model_dump_json(exclude={"raw_text"})),
            "match": json.loads(job_match.model_dump_json()) if job_match else None,
            "warnings": warnings,
        }
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    render_job(console, result)
    if job_match is not None:
        render_match(console, job_match)
