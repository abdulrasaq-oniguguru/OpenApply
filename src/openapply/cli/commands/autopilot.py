"""Read and stop standing Autopilot permission from the command line."""

from __future__ import annotations

import json
from typing import Annotated

import typer
from rich.console import Console

from openapply.applications.history import ApplicationHistoryService
from openapply.autopilot.service import AutopilotService
from openapply.worker.queue import TaskQueue

app = typer.Typer(help="Inspect or stop unattended application permission.", no_args_is_help=True)
console = Console()


@app.command("status")
def status(as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Show the current standing permission and today's usage."""
    service = AutopilotService()
    policy = service.active()
    result = {
        "schema": "openapply-autopilot/v1",
        "active": policy is not None,
        "policy": policy.document() if policy else None,
        "submissions_today": service.submissions_today(policy.id) if policy else 0,
    }
    if as_json:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return
    if policy is None:
        console.print("Autopilot is off.")
        return
    console.print(
        f"Autopilot active for {', '.join(policy.allowed_hosts)}; "
        f"{result['submissions_today']}/{policy.max_daily} used today; "
        f"expires {policy.expires_at}."
    )


@app.command("stop")
def stop() -> None:
    """Immediately revoke unused automatic authorizations and queued Autopilot work."""
    service = AutopilotService()
    policy = service.revoke()
    if policy is None:
        console.print("Autopilot is already off.")
        return
    cancelled = TaskQueue(service.database).cancel_autopilot(policy.id)
    revoked = ApplicationHistoryService(service.database).revoke_autopilot_authorizations(
        policy.id
    )
    console.print(
        f"Autopilot stopped. Cancelled {cancelled} task(s); "
        f"revoked {revoked} authorization(s)."
    )
