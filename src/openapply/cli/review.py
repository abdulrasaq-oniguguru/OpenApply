"""The human-review loop: Review in browser, Edit values, Submit, Quit.

Nothing is sent without this loop. ``Ask`` is injected so the whole flow can be driven by a
script in tests; in the CLI it reads from the terminal on a worker thread so the browser
connection keeps running while the person thinks.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import StrEnum
from urllib.parse import urlsplit

from rich.console import Console

from openapply.applications.engine import ApplicationDraft, ApplicationEngine, ApplicationError
from openapply.applications.models import AnswerStatus, ApplicationField, FieldType
from openapply.browser.forms import FormSession
from openapply.browser.page import BrowserError
from openapply.cli.render import esc
from openapply.cli.render_application import render_application_preview
from openapply.jobs.models import JobPosting

Ask = Callable[[str], Awaitable[str]]

MENU = "[R]eview in browser  [E]dit values  [S]ubmit  [Q]uit"
_STATUS_WORDS = {
    AnswerStatus.FILLED: "filled",
    AnswerStatus.GENERATED: "written",
    AnswerStatus.USER: "yours",
    AnswerStatus.PREFILLED: "page default",
    AnswerStatus.NEEDS_USER: "needs you",
    AnswerStatus.MISSING: "no data",
    AnswerStatus.SKIPPED: "skipped",
}


class Outcome(StrEnum):
    SUBMITTED = "submitted"
    QUIT = "quit"
    PREVIEWED = "previewed"


class InputClosed(Exception):
    """The input stream ended (Ctrl-D, closed pipe): treated as 'quit'."""


def submit_token(draft: ApplicationDraft) -> str:
    """What the person must type to send. A form that posts to another site needs its name."""
    if draft.target_origin_warning() is not None and draft.scan.form_action:
        return urlsplit(draft.scan.form_action).netloc
    return "submit"


async def _show_fields(console: Console, draft: ApplicationDraft) -> None:
    for number, f in enumerate(draft.scan.fields, start=1):
        a = draft.answers.get(f.id)
        word = _STATUS_WORDS.get(a.status, "") if a else "unanswered"
        req = " *" if f.required else ""
        console.print(f"  {number:>3}. {esc(f.label[:60])}{req} [dim]({word})[/dim]")


async def _ask_new_value(
    console: Console, ask: Ask, draft: ApplicationDraft, f: ApplicationField
) -> str | list[str] | None:
    """Prompt for a replacement value; None means 'keep what is there'."""
    a = draft.answers.get(f.id)
    shown = draft.display_value(f)
    console.print(f"\n[bold]{esc(f.label)}[/bold]")
    if f.hint:
        console.print(f"[dim]{esc(f.hint)}[/dim]")
    if shown:
        console.print(f"Current value: {esc(shown)}")
    elif a is not None and a.value and a.status is AnswerStatus.NEEDS_USER:
        console.print(f"Draft (not applied yet): {esc(a.value)}")

    if f.type in {FieldType.SELECT, FieldType.RADIO}:
        for i, o in enumerate(f.options, start=1):
            console.print(f"  {i}. {esc(o.label)}")
        raw = (await ask("Choose a number (blank to keep)")).strip()
        if not raw:
            return None
        if not raw.isdigit() or not 1 <= int(raw) <= len(f.options):
            console.print("[red]That is not one of the numbers above.[/red]")
            return None
        return f.options[int(raw) - 1].value
    if f.type is FieldType.CHECKBOX and f.options:
        for i, o in enumerate(f.options, start=1):
            console.print(f"  {i}. {esc(o.label)}")
        raw = (await ask("Numbers to tick, comma-separated (blank to keep, '-' for none)")).strip()
        if not raw:
            return None
        if raw == "-":
            return []
        chosen: list[str] = []
        for part in raw.split(","):
            part = part.strip()
            if not part.isdigit() or not 1 <= int(part) <= len(f.options):
                console.print("[red]Use the numbers listed above.[/red]")
                return None
            chosen.append(f.options[int(part) - 1].value)
        return chosen
    if f.type is FieldType.CHECKBOX:
        raw = (await ask("Tick this box? (yes/no, blank to keep)")).strip().lower()
        if not raw:
            return None
        if raw in {"y", "yes"}:
            return "true"
        if raw in {"n", "no"}:
            return "false"
        console.print("[red]Please answer yes or no.[/red]")
        return None
    if f.type is FieldType.FILE:
        raw = (await ask("Path to a .pdf or .docx file (blank to keep)")).strip().strip("\"'")
        return raw or None  # the engine expands "~" and validates the file
    limit = f" (max {f.max_length} characters)" if f.max_length else ""
    raw = await ask(f"New value{limit}; blank to keep, '-' to clear")
    if raw.strip() == "":
        return None
    return "" if raw.strip() == "-" else raw.strip()


async def edit_values(
    console: Console, ask: Ask, engine: ApplicationEngine, draft: ApplicationDraft
) -> None:
    while True:
        console.print("\n[bold]Edit values[/bold]")
        await _show_fields(console, draft)
        raw = (await ask("Field number (blank to go back)")).strip()
        if not raw:
            return
        if not raw.isdigit() or not 1 <= int(raw) <= len(draft.scan.fields):
            console.print("[red]That is not one of the numbers above.[/red]")
            continue
        field = draft.scan.fields[int(raw) - 1]
        value = await _ask_new_value(console, ask, draft, field)
        if value is None:
            continue
        error = await engine.set_user_answer(draft, field.id, value)
        if error:
            console.print(f"[red]Not changed: {esc(error)}[/red]")
        else:
            console.print("[green]Updated.[/green]")


async def submit_flow(
    console: Console, ask: Ask, engine: ApplicationEngine, draft: ApplicationDraft
) -> bool:
    """Returns True if the application was sent."""
    blockers = draft.blockers()
    if blockers:
        console.print("[red]Cannot submit yet. Still needed:[/red]")
        for f, reason in blockers:
            console.print(f"  [red]x[/red] {esc(f.label[:60])}: [dim]{esc(reason)}[/dim]")
        return False

    token = submit_token(draft)
    target = (draft.scan.form_action or draft.scan.url).split("?")[0]
    written = sum(1 for a in draft.answers.values() if a.status is AnswerStatus.GENERATED)
    console.print(f"\nThis will send your application to: [bold]{esc(target)}[/bold]")
    if (warning := draft.target_origin_warning()) is not None:
        console.print(f"[bold yellow]{esc(warning)}[/bold yellow]")
    if written:
        console.print(f"{written} answer(s) were written by the AI; make sure you have read them.")
    answer = await ask(f"Type '{token}' to send it (anything else cancels)")
    if answer.strip() != token:
        console.print("Cancelled. Nothing was sent.")
        return False
    try:
        result = await engine.submit(draft, confirmed=True)
    except (ApplicationError, BrowserError) as exc:
        console.print(f"[red]Not sent: {esc(exc)}[/red]")
        return False
    console.print("[green]The submit button was clicked.[/green]")
    if result.blocked_hosts:
        console.print(
            "[yellow]OpenApply blocked the page from also sending data to: "
            f"{esc(', '.join(result.blocked_hosts))}[/yellow]"
        )
    console.print(f"Page now: {esc(result.url_after)}")
    if result.excerpt:
        console.print(f"[dim]The page says: {esc(result.excerpt)}[/dim]")
    console.print(
        "OpenApply cannot verify that the employer received it. Check the page above "
        "and your email for a confirmation."
    )
    return True


async def review_loop(
    console: Console,
    ask: Ask,
    engine: ApplicationEngine,
    draft: ApplicationDraft,
    session: FormSession,
    job: JobPosting | None,
    *,
    headless: bool,
) -> Outcome:
    draft.blocked_hosts = await session.blocked_hosts()
    render_application_preview(console, draft, job)
    while True:
        console.print()
        try:
            choice = (await ask(MENU)).strip().lower()
        except InputClosed:
            console.print("Input closed. Nothing was sent.")
            return Outcome.QUIT
        if choice in {"q", "quit"}:
            console.print("Nothing was sent.")
            return Outcome.QUIT
        if choice in {"r", "review"}:
            if headless:
                console.print(
                    "[yellow]Review in browser is not available with --headless.[/yellow]"
                )
                continue
            await session.bring_to_front()
            console.print("Look over the form in the browser window. Nothing has been sent.")
            try:
                await ask("Press Enter when you are done reviewing")
            except InputClosed:
                return Outcome.QUIT
        elif choice in {"e", "edit"}:
            try:
                await edit_values(console, ask, engine, draft)
            except InputClosed:
                return Outcome.QUIT
            draft.blocked_hosts = await session.blocked_hosts()
            render_application_preview(console, draft, job)
        elif choice in {"s", "submit"}:
            try:
                if await submit_flow(console, ask, engine, draft):
                    return Outcome.SUBMITTED
            except InputClosed:
                console.print("Input closed. Nothing was sent.")
                return Outcome.QUIT
        else:
            console.print("[red]Choose R, E, S or Q.[/red]")
