"""Command-line entry point for AgentFence Lab.

Every command documents whether it is read-only, whether it can call a model and where
it writes artifacts (docs/06_CLI_CONFIGURATION_API.md section 1). Human output goes to
stdout; with ``--json`` stdout carries exactly one JSON document and everything else
(warnings, logs, errors) goes to stderr.
"""

from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer

from agentfence import __version__
from agentfence.core.config import ConfigError, ResolvedConfig, resolve_config
from agentfence.core.redaction import scrub_text
from agentfence.core.vocabulary import SCHEMA_VERSIONS, ExitCode, IsolationState
from agentfence.log import LogConfigError, configure_logging, get_logger
from agentfence.runtime.host_probe import assess_host, gather_host_facts
from agentfence.scenarios import CatalogError, bundled_scenarios
from agentfence.scenarios.loader import PackError, load_pack
from agentfence.storage.config_discovery import discover_config_layers
from agentfence.storage.paths import PathConfigError, resolve_paths

DISTRIBUTION_NAME = "agentfence-lab"
log = get_logger("cli")

EXIT_CODES = (
    (0, "success (may include INCONCLUSIVE unless --strict)"),
    (1, "one or more observed boundary FAILs"),
    (2, "usage, configuration or validation error"),
    (3, "isolation or security preflight failure"),
    (4, "infrastructure failure or timeout"),
    (5, "INCONCLUSIVE result under --strict"),
    (6, "unsupported target, agent or configuration"),
)
# "\b" stops click from re-wrapping the paragraph, keeping one code per line.
EXIT_CODE_HELP = "\b\nExit codes:\n" + "\n".join(f"  {c}  {text}" for c, text in EXIT_CODES)

app = typer.Typer(
    name="agentfence",
    help=(
        "AgentFence Lab: evidence-first security regression testing for AI coding agents.\n\n"
        "Pre-alpha. Only read-only commands are implemented; nothing here calls a model, "
        "launches an agent or uses the network.\n\n" + EXIT_CODE_HELP
    ),
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
    rich_markup_mode=None,
)
scenarios_app = typer.Typer(
    help="Inspect bundled scenarios. Read-only.",
    no_args_is_help=True,
    rich_markup_mode=None,
)
app.add_typer(scenarios_app, name="scenarios")

JsonFlag = Annotated[bool, typer.Option("--json", help="Emit exactly one JSON document on stdout.")]


def _fail(message: str, code: ExitCode) -> NoReturn:
    typer.echo(f"agentfence: error: {scrub_text(message, home=os.environ.get('HOME'))}", err=True)
    raise typer.Exit(int(code))


def _emit_json(payload: dict[str, Any]) -> None:
    typer.echo(json.dumps(payload, sort_keys=True, ensure_ascii=False))


@app.callback()
def _root() -> None:
    """Configure stderr logging before any command runs."""
    try:
        configure_logging()
    except LogConfigError as exc:
        _fail(str(exc), ExitCode.USAGE_ERROR)


def version_info() -> dict[str, Any]:
    """Return version metadata as plain data. Pure and side-effect free."""
    return {
        "name": DISTRIBUTION_NAME,
        "version": __version__,
        "python": platform.python_version(),
        "platform": f"{platform.system().lower()}-{platform.machine()}",
    }


@app.command()
def version(json_output: JsonFlag = False) -> None:
    """Print the AgentFence Lab version.

    Read-only. Does not call a model, start a sandbox or touch the network.
    """
    info = version_info()
    if json_output:
        _emit_json(info)
        return
    typer.echo(f"AgentFence Lab {info['version']} (python {info['python']}, {info['platform']})")


def _load_config(config: Path | None) -> ResolvedConfig:
    try:
        paths = resolve_paths()
        layers = discover_config_layers(paths=paths, cwd=Path.cwd(), explicit=config)
        return resolve_config(layers)
    except (ConfigError, PathConfigError) as exc:
        _fail(str(exc), ExitCode.USAGE_ERROR)


def _config_sources(resolved: ResolvedConfig) -> list[str]:
    home = os.environ.get("HOME")
    return sorted({scrub_text(origin, home=home) for origin in resolved.provenance.values()})


@app.command()
def doctor(
    json_output: JsonFlag = False,
    config: Annotated[
        Path | None,
        typer.Option("--config", help="Project config file (default: ./agentfence.yaml)."),
    ] = None,
) -> None:
    """Check host prerequisites and validate configuration.

    Read-only: writes no files, starts no processes or containers, never calls a model
    and never uses the network. Exits 0 only when every required check passes, 3 when
    sandbox runs would be refused on this host, 2 on invalid configuration.
    """
    resolved = _load_config(config)
    sources = _config_sources(resolved)
    log.debug("config resolved", extra={"fields": {"command": "doctor", "count": len(sources)}})
    caps = assess_host(gather_host_facts())
    state = caps.isolation_state
    code = ExitCode.SUCCESS if state is IsolationState.OFFLINE_SAFE else ExitCode.PREFLIGHT_FAILURE
    for check in caps.checks:
        log.info(
            "host check",
            extra={"fields": {"check_id": check.id, "status": check.status.value}},
        )
    if json_output:
        _emit_json(
            {
                "schema_version": SCHEMA_VERSIONS["attestation"],
                "tool_version": __version__,
                "host": caps.model_dump(mode="json"),
                "isolation_state": state.value,
                "blocking_checks": [c.id for c in caps.blocking_checks],
                "config_sources": sources,
                "exit_code": int(code),
            }
        )
        raise typer.Exit(int(code))

    typer.echo(f"AgentFence Lab {__version__} doctor (read-only)")
    typer.echo(f"Host: {caps.os} {caps.arch} | Python {caps.python}")
    typer.echo("")
    width = max(len(c.id) for c in caps.checks)
    for check in caps.checks:
        label = check.id if check.required else f"{check.id} (optional)"
        typer.echo(f"  {check.status.value:<8} {label:<{width + 11}} {check.summary}")
        if check.detail:
            typer.echo(f"  {'':<8} {'':<{width + 11}} {check.detail}")
    typer.echo("")
    typer.echo(f"Isolation: {state.value}")
    blocking = caps.blocking_checks
    if blocking:
        typer.echo("Blocking checks:")
        for check in blocking:
            typer.echo(f"  {check.id} [{check.status.value}]: {check.remedy}")
    typer.echo(f"Config: {', '.join(sources) if sources else 'built-in defaults'}")
    if code is not ExitCode.SUCCESS:
        typer.echo("Sandbox runs will be refused on this host until the blocking checks pass.")
    raise typer.Exit(int(code))


@scenarios_app.command("list")
def scenarios_list(
    profile: Annotated[str, typer.Option("--profile", help="Scenario profile.")] = "mvp",
    json_output: JsonFlag = False,
) -> None:
    """List bundled scenarios for a profile.

    Read-only. Every manifest is validated against the strict schema; an invalid bundle
    is an error rather than a partial list.
    """
    try:
        entries = bundled_scenarios(profile)
    except CatalogError as exc:
        _fail(str(exc), ExitCode.USAGE_ERROR)
    log.info("catalog loaded", extra={"fields": {"profile": profile, "count": len(entries)}})
    rows = [
        {
            "id": e.manifest.id,
            "version": e.manifest.version,
            "variant": e.manifest.variant.value,
            "category": e.manifest.category.value,
            "title": e.manifest.title,
            "benign_pair": e.manifest.benign_pair,
        }
        for e in entries
    ]
    if json_output:
        _emit_json(
            {"schema_version": SCHEMA_VERSIONS["scenario"], "profile": profile, "scenarios": rows}
        )
        return
    if not rows:
        typer.echo(
            f"No scenarios are bundled for profile '{profile}' yet. The five MVP scenarios "
            "and their benign controls arrive in milestone M3 (AF-0303..AF-0307)."
        )
        return
    home = os.environ.get("HOME")
    for row in rows:
        title = scrub_text(str(row["title"]), home=home)  # manifest text is untrusted
        typer.echo(
            f"{row['id']:<18} {row['version']:<8} {row['variant']:<12} "
            f"{row['category']:<22} {title}"
        )


@scenarios_app.command("validate")
def scenarios_validate(
    path: Annotated[
        Path,
        typer.Argument(help="A manifest file or a directory of *.yaml manifests."),
    ],
    json_output: JsonFlag = False,
) -> None:
    """Validate scenario manifests and their benign pairs.

    Read-only. Manifests are parsed as data and never executed. Every problem in the
    pack is reported at once. Exits 0 when all manifests are valid, 2 otherwise.
    """
    home = os.environ.get("HOME")
    try:
        entries = load_pack(path)
    except PackError as exc:
        problems = [
            {"source": scrub_text(p.source, home=home), "message": scrub_text(p.message, home=home)}
            for p in exc.problems
        ]
        if json_output:
            _emit_json(
                {
                    "schema_version": SCHEMA_VERSIONS["scenario"],
                    "valid": False,
                    "problems": problems,
                }
            )
        else:
            for problem in problems:
                typer.echo(f"INVALID  {problem['source']}: {problem['message']}")
            typer.echo(f"{len(problems)} problem(s) found.")
        raise typer.Exit(int(ExitCode.USAGE_ERROR)) from None
    rows = [
        {
            "source": scrub_text(e.source, home=home),  # file names are untrusted
            "id": e.manifest.id,
            "version": e.manifest.version,
            "variant": e.manifest.variant.value,
            "sha256": e.sha256,
        }
        for e in entries
    ]
    if json_output:
        _emit_json(
            {"schema_version": SCHEMA_VERSIONS["scenario"], "valid": True, "manifests": rows}
        )
        return
    for row in rows:
        typer.echo(
            f"OK       {row['id']:<18} {row['version']:<8} {row['variant']:<12} {row['source']}"
        )
    typer.echo(f"{len(rows)} manifest(s) valid.")


def main() -> None:
    """Console-script entry point."""
    app(prog_name="agentfence")


__all__ = ["app", "main", "version_info"]
