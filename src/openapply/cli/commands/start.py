"""One-command launcher for the private desk and durable worker."""

from __future__ import annotations

import asyncio
import threading
from typing import Annotated

import typer
from rich.console import Console

from openapply.worker.runner import Worker

console = Console()


def start(
    host: Annotated[
        str, typer.Option(help="Bind address; loopback is the safe default.")
    ] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1, max=65535)] = 8080,
    poll_seconds: Annotated[
        float, typer.Option(min=0.5, max=300, help="Worker idle polling interval.")
    ] = 5.0,
) -> None:
    """Run the private web desk and task worker in one process."""
    try:
        import uvicorn
    except ImportError as exc:
        console.print("Install the web extra first: `uv sync --extra web`.")
        raise typer.Exit(2) from exc

    if host not in {"127.0.0.1", "::1", "localhost"}:
        console.print(
            "[yellow]This app has no public-internet authentication yet. "
            "Use a loopback address unless you have protected it separately.[/yellow]"
        )

    stop = threading.Event()
    errors: list[BaseException] = []
    server = uvicorn.Server(
        uvicorn.Config(
            "openapply.web.app:create_app",
            host=host,
            port=port,
            factory=True,
            reload=False,
        )
    )

    def run_worker() -> None:
        try:
            asyncio.run(Worker().run_until_stopped(stop, poll_seconds=poll_seconds))
        except BaseException as exc:
            errors.append(exc)
            server.should_exit = True

    worker_thread = threading.Thread(target=run_worker, name="openapply-worker")
    worker_thread.start()
    console.print(f"OpenApply desk: http://{host}:{port}")
    console.print("Worker started. Press Ctrl+C once to stop both safely.")
    try:
        server.run()
    finally:
        stop.set()
        worker_thread.join()
    if errors:
        console.print(f"[red]Worker stopped unexpectedly: {errors[0]}[/red]")
        raise typer.Exit(1)
