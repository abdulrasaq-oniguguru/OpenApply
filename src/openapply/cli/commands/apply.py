"""`openapply apply URL`: fill an application form, then review it before anything is sent."""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from typing import Annotated

import typer
from rich.console import Console

from openapply.applications.answers import AnswerContext
from openapply.applications.generation import MAX_GENERATED
from openapply.applications.service import prepare_application
from openapply.browser.forms import FormSession, open_form_session
from openapply.browser.page import BrowserError, BrowserNotInstalled
from openapply.candidate.service import CandidateService
from openapply.candidate.storage import ProfileError
from openapply.cli.commands.analyze import _build_fetcher, _build_provider
from openapply.cli.render import esc
from openapply.cli.render_application import render_application_preview
from openapply.cli.review import InputClosed, Outcome, review_loop
from openapply.config.settings import ConfigError, load_settings
from openapply.jobs.extractor import ExtractionError
from openapply.jobs.models import JobPosting
from openapply.jobs.service import JobService
from openapply.providers.base import AgentProvider
from openapply.providers.errors import ProviderError
from openapply.providers.structured import StructuredOutputError
from openapply.security.urls import UnsafeURLError, validate_job_url

console = Console()
err = Console(stderr=True)

DEFAULT_TIMEOUT_SECONDS = 300.0
NAVIGATION_TIMEOUT_MS = 30_000


def _open_session(
    url: str, *, allow_local: bool, headless: bool, timeout_ms: int
) -> AbstractAsyncContextManager[FormSession]:
    return open_form_session(url, allow_local=allow_local, headless=headless, timeout_ms=timeout_ms)


async def _terminal_ask(prompt: str) -> str:
    """Read a line on a worker thread so the browser connection keeps running meanwhile."""
    try:
        return await asyncio.to_thread(typer.prompt, prompt, default="", show_default=False)
    except (typer.Abort, EOFError) as exc:
        raise InputClosed from exc


def _fail(message: str, code: int = 1) -> typer.Exit:
    err.print(f"[red]{esc(message)}[/red]")
    return typer.Exit(code)


def _warn(message: str) -> None:
    err.print(f"[yellow]Warning:[/yellow] {esc(message)}")


def _read_job(
    url: str, ai: AgentProvider, *, allow_local: bool, timeout: float
) -> JobPosting | None:
    """The job behind the form (for written answers). Failure is a warning, never fatal."""
    service = JobService(_build_fetcher(allow_local), ai, timeout=timeout)
    try:
        with err.status("Reading the job posting..."):
            return asyncio.run(service.analyze(url)).job
    except (BrowserError, ProviderError, StructuredOutputError, ExtractionError) as exc:
        reason = exc.message if isinstance(exc, ProviderError) else str(exc)
        _warn(f"Could not read the job ({reason}); written answers will be left to you.")
        return None


async def _run(
    url: str,
    *,
    profile_context: AnswerContext,
    ai: AgentProvider | None,
    job: JobPosting | None,
    allow_local: bool,
    headless: bool,
    preview_only: bool,
    timeout: float,
    max_answers: int,
) -> Outcome:
    async with _open_session(
        url, allow_local=allow_local, headless=headless, timeout_ms=NAVIGATION_TIMEOUT_MS
    ) as session:
        with err.status("Reading the form and filling what your profile knows..."):
            engine, draft = await prepare_application(
                session,
                profile_context.profile,
                profile_context,
                provider=ai,
                job=job,
                timeout=timeout,
                max_generated=max_answers,
            )
        if preview_only:
            render_application_preview(console, draft, job)
            return Outcome.PREVIEWED
        return await review_loop(
            console, _terminal_ask, engine, draft, session, job, headless=headless
        )


def apply(
    url: Annotated[str, typer.Argument(help="Application page URL (http or https).")],
    provider: Annotated[
        str | None, typer.Option("--provider", "-p", help="Provider for written answers.")
    ] = None,
    model: Annotated[str | None, typer.Option("--model", "-m", help="Model override.")] = None,
    job_url: Annotated[
        str | None,
        typer.Option(
            "--job-url", help="The job posting page, when it is not the application page itself."
        ),
    ] = None,
    ai: Annotated[
        bool, typer.Option("--ai/--no-ai", help="Use the AI to write free-text answers.")
    ] = True,
    headless: Annotated[
        bool, typer.Option("--headless", help="Hide the browser window (no visual review).")
    ] = False,
    preview_only: Annotated[
        bool, typer.Option("--preview-only", help="Fill and preview, then stop. Never submits.")
    ] = False,
    allow_local: Annotated[
        bool, typer.Option("--allow-local", help="Allow localhost/private addresses.")
    ] = False,
    timeout: Annotated[
        float, typer.Option("--timeout", min=1, help="AI call timeout in seconds.")
    ] = DEFAULT_TIMEOUT_SECONDS,
    max_answers: Annotated[
        int, typer.Option("--max-answers", min=0, help="Most written answers to generate.")
    ] = MAX_GENERATED,
) -> None:
    """Fill an application form from your profile, then review it before anything is sent.

    Nothing is submitted without you: you review the filled form, then type a confirmation.
    Sensitive questions (visa, demographics, criminal history, consents) are never answered
    for you unless your profile explicitly says so, and consents are never ticked.
    """
    try:
        url = validate_job_url(url, allow_local=allow_local)
        # the posting is often a different page from the application form (e.g. Lever's /apply)
        posting_url = (
            validate_job_url(job_url, allow_local=allow_local) if job_url is not None else url
        )
    except UnsafeURLError as exc:
        raise _fail(str(exc), 2) from exc

    service = CandidateService()
    try:
        profile = service.load()
    except ProfileError as exc:
        raise _fail(str(exc), 2) from exc
    if profile is None:
        raise _fail("No profile yet. Run `openapply setup` first.")
    if missing := service.missing_required(profile):
        raise _fail(
            f"Your profile is incomplete (missing {', '.join(missing)}). Run `openapply setup`."
        )

    ai_provider: AgentProvider | None = None
    if ai:
        try:
            ai_provider = _build_provider(load_settings(), provider, model)
        except ConfigError as exc:
            raise _fail(str(exc), 2) from exc
        except ProviderError as exc:
            if provider is not None:  # asked for explicitly: do not quietly carry on without it
                raise _fail(exc.message, 2) from exc
            _warn(f"{exc.message} Written answers will be left to you.")
    job = (
        _read_job(posting_url, ai_provider, allow_local=allow_local, timeout=timeout)
        if ai_provider
        else None
    )

    context = AnswerContext(
        profile=profile,
        resume_path=service.resume_file(profile),
        resume_status=service.resume_status(profile),
    )
    try:
        outcome = asyncio.run(
            _run(
                url,
                profile_context=context,
                ai=ai_provider,
                job=job,
                allow_local=allow_local,
                headless=headless,
                preview_only=preview_only,
                timeout=timeout,
                max_answers=max_answers,
            )
        )
    except BrowserNotInstalled as exc:
        raise _fail(str(exc)) from exc
    except BrowserError as exc:
        raise _fail(f"Browser: {exc}") from exc
    if outcome is Outcome.PREVIEWED:
        console.print()
        console.print(
            "[dim]Preview only: nothing was sent. Run again without --preview-only to review "
            "and submit.[/dim]"
        )
