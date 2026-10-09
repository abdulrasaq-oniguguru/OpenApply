"""Persistent personal-agent task worker commands."""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from openapply.worker.queue import TaskQueue
from openapply.worker.runner import Worker

app = typer.Typer(help="Queue and run resumable background work.", no_args_is_help=True)
console = Console()


@app.command("add-job")
def add_job(url: str, provider: Annotated[str | None, typer.Option()] = None) -> None:
    """Queue one job URL for reading and matching."""
    payload: dict[str, object] = {"url": url}
    if provider:
        payload["provider"] = provider
    task = TaskQueue().enqueue("analyze_job", payload)
    console.print(f"Queued job analysis: {task.id}")


@app.command("discover")
def discover(url: str) -> None:
    """Queue bounded discovery from a company career/listing page."""
    task = TaskQueue().enqueue("discover_source", {"url": url})
    console.print(f"Queued source discovery: {task.id}")


@app.command("list")
def list_tasks() -> None:
    """Show recent durable tasks."""
    table = Table("ID", "Type", "State", "Attempts", "Next run", title="Agent tasks")
    for task in TaskQueue().list():
        table.add_row(
            task.id[:8], task.type, task.state.value, str(task.attempts), task.next_run_at or "-"
        )
    console.print(table)


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
