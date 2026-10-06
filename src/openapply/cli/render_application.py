"""The application preview: what was filled, what was written, and what needs you.

Every label and value on screen came from a web page or an AI reply, so all of it goes
through ``esc`` (see ``openapply.cli.render``).
"""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from openapply.applications.engine import ApplicationDraft
from openapply.applications.models import AnswerStatus, ApplicationField, FieldType
from openapply.cli.render import esc
from openapply.jobs.models import JobPosting

VALUE_WIDTH = 60
_SOURCE_NOTE = {
    AnswerStatus.PREFILLED: "page default",
}


def _short(text: str, width: int = VALUE_WIDTH) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= width else flat[: width - 1] + "..."


def _flag(f: ApplicationField, draft: ApplicationDraft) -> str:
    marks = []
    if f.required:
        marks.append("required")
    if f.id in draft.ai_classified:
        marks.append("field type guessed by AI")
    return f" [dim]({', '.join(marks)})[/dim]" if marks else ""


def render_application_preview(
    console: Console, draft: ApplicationDraft, job: JobPosting | None
) -> None:
    scan = draft.scan
    title = job.title if job else (scan.title or "Application")
    company = f" - {job.company}" if job and job.company else ""
    console.print()
    console.print(f"[bold]{esc(title)}{esc(company)}[/bold]")
    console.print(f"[dim]{esc(scan.url)}[/dim]")
    target = (scan.form_action or scan.url).split("?")[0]
    console.print(f"Submits to: {esc(target)}")
    if (warning := draft.target_origin_warning()) is not None:
        console.print(f"[bold yellow]Warning:[/bold yellow] {esc(warning)}")
    for note in draft.warnings:
        console.print(f"[yellow]Note:[/yellow] {esc(note)}")
    console.print(
        "[dim]While your details are filled in, the page is blocked from contacting other "
        "sites.[/dim]"
    )
    if draft.blocked_hosts:
        hosts = esc(", ".join(draft.blocked_hosts))
        console.print(
            f"[bold yellow]The page tried to contact:[/bold yellow] {hosts} [dim](blocked)[/dim]"
        )

    filled = Table(show_edge=False, header_style="bold", pad_edge=False)
    filled.add_column("FIELD")
    filled.add_column("VALUE")
    filled.add_column("SOURCE", style="dim")
    generated: list[ApplicationField] = []
    needs: list[ApplicationField] = []
    missing: list[ApplicationField] = []
    for f in draft.scan.fields:
        a = draft.answers.get(f.id)
        if a is None:
            continue
        if a.status is AnswerStatus.GENERATED:
            generated.append(f)
        elif a.status is AnswerStatus.NEEDS_USER:
            needs.append(f)
        elif a.status is AnswerStatus.MISSING:
            missing.append(f)
        elif a.status in {AnswerStatus.FILLED, AnswerStatus.USER, AnswerStatus.PREFILLED}:
            value = draft.display_value(f) or (a.value or "")
            shown = (
                "[dim](unticked)[/dim]"
                if (f.type is FieldType.CHECKBOX and not value)
                else esc(_short(value))
            )
            warn = (
                ""
                if a.applied or a.status is AnswerStatus.PREFILLED
                else " [red](not applied)[/red]"
            )
            filled.add_row(
                esc(_short(f.label, 40)) + _flag(f, draft), shown + warn, esc(a.source or "")
            )
    if filled.row_count:
        console.print()
        console.print(filled)

    if generated:
        console.print()
        console.print(
            "[bold]Generated responses[/bold] [dim](written by the AI: read them first)[/dim]"
        )
        for f in generated:
            a = draft.answers[f.id]
            console.print(f"  {esc(_short(f.label, 50))}{_flag(f, draft)}")
            text = esc(_short(a.value or "", 90))
            console.print(f"    [green]Ready[/green] [dim]{esc(a.source)}[/dim]: {text}")

    if needs:
        console.print("\n[bold]Needs your confirmation[/bold] [dim](never filled in for you)[/dim]")
        for f in needs:
            a = draft.answers[f.id]
            console.print(f"  [yellow]?[/yellow] {esc(_short(f.label, 60))}{_flag(f, draft)}")
            console.print(f"      [dim]{esc(a.reason)}[/dim]")
    if missing:
        console.print("\n[bold]Not in your profile[/bold]")
        for f in missing:
            reason = esc(draft.answers[f.id].reason)
            console.print(f"  - {esc(_short(f.label, 60))}{_flag(f, draft)}  [dim]{reason}[/dim]")

    blockers = draft.blockers()
    console.print()
    if blockers:
        console.print(f"[bold red]{len(blockers)} required item(s) block submitting:[/bold red]")
        for f, reason in blockers:
            console.print(f"  [red]x[/red] {esc(_short(f.label, 60))}: [dim]{esc(reason)}[/dim]")
    else:
        console.print(
            "[green]Nothing required is missing.[/green] Review everything before you submit."
        )
