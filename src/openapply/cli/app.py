"""OpenApply command-line entry point."""

from __future__ import annotations

import contextlib
import errno
import os
import sys
from typing import Annotated

import typer

from openapply import __version__
from openapply.cli.commands import agent as agent_cmd
from openapply.cli.commands import analyze as analyze_cmd
from openapply.cli.commands import apply as apply_cmd
from openapply.cli.commands import browser as browser_cmd
from openapply.cli.commands import doctor as doctor_cmd
from openapply.cli.commands import interview as interview_cmd
from openapply.cli.commands import profile as profile_cmd
from openapply.cli.commands import providers as providers_cmd
from openapply.cli.commands import setup as setup_cmd
from openapply.cli.commands import telegram as telegram_cmd
from openapply.cli.commands import worker as worker_cmd
from openapply.logging_setup import configure_logging

app = typer.Typer(
    name="openapply",
    help="Local-first AI job application assistant that uses the AI tools you already have.",
    no_args_is_help=True,
)
app.command("doctor")(doctor_cmd.doctor)
app.command("setup")(setup_cmd.setup)
app.command("analyze")(analyze_cmd.analyze)
app.command("apply")(apply_cmd.apply)
app.add_typer(browser_cmd.app, name="browser")
app.add_typer(providers_cmd.app, name="providers")
app.add_typer(profile_cmd.app, name="profile")
app.add_typer(interview_cmd.app, name="interview")
app.add_typer(agent_cmd.app, name="agent")
app.add_typer(worker_cmd.app, name="worker")
app.add_typer(telegram_cmd.app, name="telegram")


def _version(value: bool) -> None:
    if value:
        typer.echo(f"openapply {__version__}")
        raise typer.Exit


@app.callback()
def main_callback(
    verbose: Annotated[bool, typer.Option("--verbose", help="Show progress logs.")] = False,
    debug: Annotated[bool, typer.Option("--debug", help="Show debug logs (redacted).")] = False,
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Show version.")
    ] = False,
) -> None:
    configure_logging(verbose=verbose, debug=debug)


def _make_output_safe() -> None:
    """Never crash on a legacy console (e.g. cp1252) that cannot encode symbols."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    _make_output_safe()
    try:
        app()
    except OSError as exc:
        # `openapply ... | head`: Windows reports a closed pipe as EINVAL, not BrokenPipeError.
        # Only that exact case is swallowed; real file errors carry a filename and still raise.
        if exc.errno not in {errno.EPIPE, errno.EINVAL} or exc.filename is not None:
            raise
        with contextlib.suppress(OSError):
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(1)


if __name__ == "__main__":
    main()
