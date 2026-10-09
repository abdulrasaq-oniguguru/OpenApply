"""Persistent personal-agent task worker commands."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from openapply.candidate.service import CandidateService
from openapply.candidate.storage import ProfileError
from openapply.jobs.matcher import score_match
from openapply.jobs.models import JobPosting
from openapply.storage.repositories import OpportunityRepository
from openapply.worker.models import Task
from openapply.worker.queue import TaskQueue
from openapply.worker.runner import Worker

app = typer.Typer(help="Queue and run resumable background work.", no_args_is_help=True)
console = Console()


def _task_json(task: Task) -> str:
    return json.dumps(task.model_dump(mode="json"), indent=2, ensure_ascii=False)


@app.command("add-job")
def add_job(
    url: str,
    provider: Annotated[str | None, typer.Option()] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Queue one job URL for reading and matching."""
    payload: dict[str, object] = {"url": url}
    if provider:
        payload["provider"] = provider
    task = TaskQueue().enqueue("analyze_job", payload)
    if as_json:
        typer.echo(_task_json(task))
    else:
        console.print(f"Queued job analysis: {task.id}")


@app.command("discover")
def discover(url: str, as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Queue bounded discovery from a company career/listing page."""
    task = TaskQueue().enqueue("discover_source", {"url": url})
    if as_json:
        typer.echo(_task_json(task))
    else:
        console.print(f"Queued source discovery: {task.id}")


@app.command("list")
def list_tasks(
    as_json: Annotated[bool, typer.Option("--json")] = False,
    limit: Annotated[int, typer.Option(min=1, max=500)] = 100,
) -> None:
    """Show recent durable tasks."""
    tasks = TaskQueue().list(limit=limit)
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "schema": "openapply-tasks/v1",
                    "tasks": [task.model_dump(mode="json") for task in tasks],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    table = Table("ID", "Type", "State", "Attempts", "Next run", title="Agent tasks")
    for task in tasks:
        table.add_row(
            task.id[:8], task.type, task.state.value, str(task.attempts), task.next_run_at or "-"
        )
    console.print(table)


@app.command("rematch")
def rematch(
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Recompute saved opportunity matches from the current profile, without AI calls."""
    try:
        profile = CandidateService().load()
    except ProfileError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    if profile is None:
        console.print("No profile yet. Import or create one before refreshing matches.")
        raise typer.Exit(1)

    repository = OpportunityRepository()
    updated = 0
    for opportunity in repository.list(limit=500):
        job = JobPosting.model_validate(opportunity["job"])
        repository.update_match(str(opportunity["id"]), score_match(profile, job))
        updated += 1
    result = {"schema": "openapply-rematch/v1", "updated": updated}
    if as_json:
        typer.echo(json.dumps(result, indent=2))
    else:
        console.print(f"Refreshed {updated} saved opportunity match(es).")


@app.command("pause")
def pause() -> None:
    """Pause acquisition of new background tasks."""
    TaskQueue().set_paused(True)
    console.print("Worker paused. Any operation already in progress may finish.")


@app.command("resume")
def resume() -> None:
    """Allow the worker to acquire ready tasks again."""
    TaskQueue().set_paused(False)
    console.print("Worker resumed.")


@app.command("run")
def run(
    once: Annotated[bool, typer.Option(help="Process at most one ready task.")] = False,
    poll_seconds: Annotated[float, typer.Option(min=0.5, max=300)] = 5.0,
) -> None:
    """Run the local durable worker."""
    worker = Worker()
    if once:
        worked = asyncio.run(worker.run_once())
        console.print("Processed one task." if worked else "No task is ready.")
        return
    console.print("Worker running. Press Ctrl+C to stop safely between operations.")
    try:
        asyncio.run(worker.run_forever(poll_seconds=poll_seconds))
    except KeyboardInterrupt:
        console.print("Worker stopped.")
