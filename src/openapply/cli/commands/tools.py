"""Agent-readable inventory of OpenApply's supported command surface."""

from __future__ import annotations

import json
from typing import Annotated, TypedDict

import typer
from rich.console import Console
from rich.table import Table


class ToolDescription(TypedDict):
    name: str
    command: str
    purpose: str
    access: str


TOOLS: tuple[ToolDescription, ...] = (
    {
        "name": "system_check",
        "command": "openapply doctor",
        "purpose": "Check the browser and local AI providers.",
        "access": "read-only",
    },
    {
        "name": "start_desk",
        "command": "openapply start",
        "purpose": "Run the private desk and durable worker together.",
        "access": "local service",
    },
    {
        "name": "show_profile",
        "command": "openapply profile show --json",
        "purpose": "Inspect the local candidate profile before matching.",
        "access": "private data; read-only",
    },
    {
        "name": "analyze_job",
        "command": "openapply analyze <job-url> --json",
        "purpose": "Extract one job and score it against the saved profile.",
        "access": "opens URL; saves opportunity",
    },
    {
        "name": "discover_jobs",
        "command": "openapply worker discover <listing-url> --json",
        "purpose": "Queue bounded discovery from a user-selected listing page.",
        "access": "opens URL; queues work",
    },
    {
        "name": "list_job_platforms",
        "command": "openapply worker platforms --json",
        "purpose": "List built-in page, JSON API, and RSS discovery sources.",
        "access": "read-only",
    },
    {
        "name": "discover_platform",
        "command": "openapply worker discover-platform <platform> --query <role> --json",
        "purpose": "Queue bounded role-targeted discovery from a built-in public source.",
        "access": "opens public source; queues work",
    },
    {
        "name": "list_tasks",
        "command": "openapply worker list --json",
        "purpose": "Inspect durable task states, errors, and leases.",
        "access": "read-only",
    },
    {
        "name": "refresh_matches",
        "command": "openapply worker rematch --json",
        "purpose": "Recompute all saved scores from the current profile without AI calls.",
        "access": "updates local match results",
    },
    {
        "name": "run_worker",
        "command": "openapply worker run",
        "purpose": "Process queued discovery, matching, and reviewed application work.",
        "access": "background processing; never auto-submits",
    },
    {
        "name": "reviewed_application",
        "command": "openapply apply <application-url> --job-url <job-url>",
        "purpose": "Prepare an application and ask the person to review before submission.",
        "access": "human confirmation required",
    },
)

console = Console()


def tools(
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the stable openapply-tools/v1 document.")
    ] = False,
) -> None:
    """List commands an agent can safely discover and invoke."""
    if as_json:
        typer.echo(
            json.dumps(
                {"schema": "openapply-tools/v1", "tools": list(TOOLS)},
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    table = Table("Tool", "Command", "Access", title="OpenApply tools", title_justify="left")
    for item in TOOLS:
        table.add_row(item["name"], item["command"], item["access"])
    console.print(table)
    console.print("Use `openapply tools --json` for agent-readable details.")
