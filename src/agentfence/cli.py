"""Command-line entry point for AgentFence Lab.

Every command documents whether it is read-only, whether it can call a model
and where it writes artifacts (docs/06_CLI_CONFIGURATION_API.md section 1).
"""

from __future__ import annotations

import json
import platform
from typing import Annotated, Any

import typer

from agentfence import __version__

DISTRIBUTION_NAME = "agentfence-lab"

app = typer.Typer(
    name="agentfence",
    help=(
        "AgentFence Lab: evidence-first security regression testing for AI coding agents.\n\n"
        "Pre-alpha. Only read-only commands are implemented; nothing here calls a model, "
        "launches an agent or uses the network."
    ),
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)


@app.callback()
def _root() -> None:
    """Root command group (keeps subcommands explicit even when only one exists)."""


def version_info() -> dict[str, Any]:
    """Return version metadata as plain data. Pure and side-effect free."""
    return {
        "name": DISTRIBUTION_NAME,
        "version": __version__,
        "python": platform.python_version(),
        "platform": f"{platform.system().lower()}-{platform.machine()}",
    }


@app.command()
def version(
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON on stdout only.")
    ] = False,
) -> None:
    """Print the AgentFence Lab version.

    Read-only. Does not call a model, start a sandbox or touch the network.
    """
    info = version_info()
    if json_output:
        typer.echo(json.dumps(info, sort_keys=True))
        return
    typer.echo(f"AgentFence Lab {info['version']} (python {info['python']}, {info['platform']})")


def main() -> None:
    """Console-script entry point."""
    app(prog_name="agentfence")
