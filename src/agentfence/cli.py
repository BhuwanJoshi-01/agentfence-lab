"""Command-line entry point for AgentFence Lab.

Every command documents whether it is read-only, whether it can call a model and where
it writes artifacts (docs/06_CLI_CONFIGURATION_API.md section 1). Human output goes to
stdout; with ``--json`` stdout carries exactly one JSON document and everything else
(warnings, logs, errors) goes to stderr.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer

from agentfence import __version__
from agentfence.adapters.mock import MockProfile
from agentfence.core.comparisons import Change, RunComparison, compare_runs
from agentfence.core.config import (
    AgentFenceConfig,
    ConfigError,
    ConfigLayer,
    ConfigSource,
    ResolvedConfig,
    resolve_config,
)
from agentfence.core.redaction import scrub_text
from agentfence.core.scenario_manifest import ScenarioManifest
from agentfence.core.vocabulary import (
    SCHEMA_VERSIONS,
    ExitCode,
    InfrastructureOutcome,
    IsolationState,
    run_exit_code,
)
from agentfence.log import LogConfigError, configure_logging, get_logger
from agentfence.reporting.console import render_summary
from agentfence.reporting.export import (
    ExportError,
    VerifiedReport,
    load_verified_report,
    reproduction_notes,
    write_export,
)
from agentfence.reporting.html_report import render_html
from agentfence.reporting.json_report import SuiteReport, build_report, report_json
from agentfence.runtime.host_probe import assess_host, gather_host_facts
from agentfence.runtime.suite import SuiteRunResult, run_suite
from agentfence.scenarios import CatalogError, bundled_scenarios
from agentfence.scenarios.loader import PackError, load_pack
from agentfence.storage.atomic import atomic_write_text
from agentfence.storage.config_discovery import discover_config_layers
from agentfence.storage.db import RunStore, StoreError, as_dict, open_store
from agentfence.storage.paths import (
    AppPaths,
    PathConfigError,
    ensure_private_dir,
    resolve_paths,
)

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
        "Pre-alpha. Nothing here calls a model, launches a real agent or uses the network. "
        "'test' runs the offline mock adapter only.\n\n" + EXIT_CODE_HELP
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
runs_app = typer.Typer(
    help="Inspect saved runs. Read-only.",
    no_args_is_help=True,
    rich_markup_mode=None,
)
app.add_typer(runs_app, name="runs")

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


def _load_config(
    config: Path | None, *, cli_overrides: dict[str, Any] | None = None
) -> ResolvedConfig:
    try:
        paths = resolve_paths()
        layers = discover_config_layers(paths=paths, cwd=Path.cwd(), explicit=config)
        if cli_overrides:
            layers.append(ConfigLayer(ConfigSource.CLI, "command line", cli_overrides))
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


def _select_cases(profile: str, include: tuple[str, ...]) -> list[ScenarioManifest]:
    """Bundled scenarios for ``profile``; selecting a scenario also selects its benign pair."""
    try:
        entries = bundled_scenarios(profile)
    except CatalogError as exc:
        _fail(str(exc), ExitCode.USAGE_ERROR)
    by_id = {e.manifest.id: e.manifest for e in entries}
    if not by_id:
        _fail(f"no scenarios are bundled for profile {profile!r}", ExitCode.USAGE_ERROR)
    if not include:
        return list(by_id.values())
    unknown = [i for i in include if i not in by_id]
    if unknown:
        _fail(
            f"unknown scenario ID(s) for profile {profile!r}: {', '.join(unknown)}. "
            "See 'agentfence scenarios list'.",
            ExitCode.USAGE_ERROR,
        )
    chosen = set(include)
    for sid in include:
        pair = by_id[sid].benign_pair
        if pair:
            chosen.add(pair)
    return [by_id[sid] for sid in sorted(chosen)]


def _cli_overrides(
    agent: str | None,
    agent_profile: str | None,
    profile: str | None,
    repeat: int | None,
    cases: str | None,
) -> dict[str, Any]:
    """Map 'test' options onto the CLI config layer (highest precedence)."""
    overrides: dict[str, Any] = {}
    if agent is not None:
        overrides.setdefault("agent", {})["kind"] = agent
    if agent_profile is not None:
        overrides.setdefault("agent", {})["profile"] = agent_profile
    if profile is not None:
        overrides.setdefault("scenarios", {})["profile"] = profile
    if repeat is not None:
        overrides.setdefault("scenarios", {})["repeat"] = repeat
    if cases is not None:
        overrides.setdefault("scenarios", {})["include"] = [
            c.strip() for c in cases.split(",") if c.strip()
        ]
    return overrides


def _run_and_save(
    selected: list[ScenarioManifest], mock_profile: MockProfile, cfg: AgentFenceConfig, seed: int
) -> tuple[SuiteRunResult, SuiteReport, str, Path]:
    """Run the suite, write report.json (0600) and record the run in the store."""
    try:
        paths = resolve_paths()
        ensure_private_dir(paths.state_dir)
        suite = run_suite(
            selected,
            mock_profile,
            scenario_profile=cfg.scenarios.profile,
            seed=seed,
            repeat=cfg.scenarios.repeat,
            workdir=paths.state_dir / "work",
        )
        report = build_report(suite)
        text = report_json(report)
        run_dir = ensure_private_dir(paths.run_dir(suite.run_id))
        report_path = atomic_write_text(run_dir / "report.json", text)
        with open_store(paths.state_dir) as store:
            store.save_suite(suite, report_sha256=hashlib.sha256(text.encode()).hexdigest())
    except (PathConfigError, PermissionError, StoreError) as exc:
        _fail(str(exc), ExitCode.INFRA_FAILURE)
    return suite, report, text, report_path


def _print_baseline(comparison: RunComparison) -> None:
    counts = ", ".join(f"{c.value.lower()}={comparison.count(c)}" for c in Change)
    typer.echo(f"Compared with baseline {comparison.baseline_run}: {counts}")
    for pair in comparison.pairs:
        if pair.change in {Change.REGRESSED, Change.INCOMPARABLE}:
            typer.echo(
                f"  {pair.change.value:<12} {pair.scenario_id} #{pair.repetition}: "
                f"{pair.baseline} -> {pair.candidate}"
                + (f" ({'; '.join(pair.reasons)})" if pair.reasons else "")
            )


@app.command("test")
def test_command(
    agent: Annotated[
        str | None, typer.Option("--agent", help="Agent adapter. Only 'mock' exists.")
    ] = None,
    agent_profile: Annotated[
        str | None,
        typer.Option(
            "--agent-profile",
            help="Mock behavior: safe, unsafe, refusal, no-exposure, no-telemetry, timeout.",
        ),
    ] = None,
    profile: Annotated[
        str | None, typer.Option("--profile", help="Scenario profile (default: mvp).")
    ] = None,
    cases: Annotated[
        str | None,
        typer.Option("--cases", help="Comma-separated scenario IDs; benign pairs are added."),
    ] = None,
    repeat: Annotated[int | None, typer.Option("--repeat", help="Runs per scenario.")] = None,
    seed: Annotated[int, typer.Option("--seed", min=0, help="Fixture seed.")] = 0,
    strict: Annotated[
        bool, typer.Option("--strict", help="Exit 5 when any result is INCONCLUSIVE.")
    ] = False,
    config: Annotated[
        Path | None,
        typer.Option("--config", help="Project config file (default: ./agentfence.yaml)."),
    ] = None,
    baseline: Annotated[
        str | None,
        typer.Option("--baseline", help="Saved run ID to compare against after this run."),
    ] = None,
    json_output: JsonFlag = False,
) -> None:
    """Run scenarios with the offline mock adapter and save the report.

    Writes report.json under <state>/runs/<run-id>/ and records the run in the local
    SQLite store. Never calls a model, never uses the network, never runs workspace code.
    Exit codes as listed in 'agentfence --help'.
    """
    overrides = _cli_overrides(agent, agent_profile, profile, repeat, cases)
    resolved = _load_config(config, cli_overrides=overrides)
    cfg = resolved.config
    if cfg.agent.kind != "mock":
        _fail(
            f"agent {cfg.agent.kind!r} is not supported; only the offline 'mock' adapter exists "
            "until the M8 live-agent security gate passes (ADR-012)",
            ExitCode.UNSUPPORTED,
        )
    try:
        mock_profile = MockProfile(cfg.agent.profile)
    except ValueError:
        choices = ", ".join(p.value for p in MockProfile)
        _fail(
            f"unknown mock profile {cfg.agent.profile!r}; choose one of: {choices}",
            ExitCode.USAGE_ERROR,
        )
    selected = _select_cases(cfg.scenarios.profile, cfg.scenarios.include)
    baseline_report = _verified(baseline).report if baseline is not None else None

    suite, report, text, report_path = _run_and_save(selected, mock_profile, cfg, seed)
    boundaries = [r.verdict.boundary for r in suite.attempts]
    infra_failed = any(
        r.verdict.infrastructure is not InfrastructureOutcome.OK for r in suite.attempts
    )
    code = run_exit_code(
        preflight_failed=False,
        infra_failed=infra_failed,
        boundaries=boundaries,
        strict=strict or cfg.evaluation.fail_on_inconclusive,
    )
    comparison = compare_runs(baseline_report, report) if baseline_report else None
    if comparison is not None and comparison.has_regressions and code is ExitCode.SUCCESS:
        code = ExitCode.BOUNDARY_FAIL
    log.info("run saved", extra={"fields": {"run_id": suite.run_id, "exit_code": int(code)}})
    if json_output:
        typer.echo(text, nl=False)
        raise typer.Exit(int(code))
    location = scrub_text(str(report_path), home=os.environ.get("HOME"))
    typer.echo(render_summary(report, report_location=location), nl=False)
    if comparison is not None:
        _print_baseline(comparison)
    raise typer.Exit(int(code))


def _open_existing_store() -> tuple[AppPaths, RunStore]:
    try:
        paths = resolve_paths()
    except PathConfigError as exc:
        _fail(str(exc), ExitCode.USAGE_ERROR)
    if not paths.database_path.exists():
        _fail("no runs recorded yet; start one with 'agentfence test'", ExitCode.USAGE_ERROR)
    try:
        return paths, RunStore(paths.database_path)
    except (StoreError, PermissionError) as exc:
        _fail(str(exc), ExitCode.INFRA_FAILURE)


@runs_app.command("list")
def runs_list(
    limit: Annotated[int, typer.Option("--limit", min=1, max=200, help="Rows to show.")] = 20,
    json_output: JsonFlag = False,
) -> None:
    """List saved runs, newest first. Read-only."""
    _, store = _open_existing_store()
    with store:
        rows = [as_dict(r) for r in store.list_runs(limit)]
    if json_output:
        _emit_json({"schema_version": SCHEMA_VERSIONS["report"], "runs": rows})
        return
    for row in rows:
        counts = row["boundary"]
        typer.echo(
            f"{row['run_id']}  {row['started_at'][:19]}  mock-{row['agent_profile']:<13} "
            f"attempts={row['attempts']:<3} pass={counts.get('PASS', 0)} "
            f"fail={counts.get('FAIL', 0)} inconclusive={counts.get('INCONCLUSIVE', 0)} "
            f"not-triggered={counts.get('NOT_TRIGGERED', 0)}"
        )


def _verified(run_id: str) -> VerifiedReport:
    paths, store = _open_existing_store()
    try:
        with store:
            return load_verified_report(paths, store, run_id)
    except PathConfigError as exc:
        _fail(str(exc), ExitCode.USAGE_ERROR)
    except ExportError as exc:
        _fail(str(exc), ExitCode.USAGE_ERROR if exc.not_found else ExitCode.INFRA_FAILURE)


@runs_app.command("show")
def runs_show(
    run_id: Annotated[str, typer.Argument(help="Run ID from 'agentfence runs list'.")],
    json_output: JsonFlag = False,
) -> None:
    """Show a saved run after verifying its report and evidence. Read-only."""
    verified = _verified(run_id)
    if json_output:
        typer.echo(verified.text, nl=False)
        return
    typer.echo(render_summary(verified.report), nl=False)
    typer.echo("Integrity: report hash and evidence hash chain verified")
    for line in reproduction_notes(verified.report):
        typer.echo(line)


@runs_app.command("compare")
def runs_compare(
    baseline: Annotated[str, typer.Argument(help="Baseline run ID.")],
    candidate: Annotated[str, typer.Argument(help="Candidate run ID.")],
    json_output: JsonFlag = False,
) -> None:
    """Compare two saved runs scenario by scenario. Read-only.

    Both runs are verified first. Pairs whose scenario version, evaluator version, fixture
    (seed), isolation tier or agent differ are INCOMPARABLE, never silently compared.
    Exits 1 when anything REGRESSED, otherwise 0.
    """
    base = _verified(baseline).report
    cand = _verified(candidate).report
    result = compare_runs(base, cand)
    code = ExitCode.BOUNDARY_FAIL if result.has_regressions else ExitCode.SUCCESS
    if json_output:
        _emit_json(
            {
                "schema_version": SCHEMA_VERSIONS["report"],
                "baseline_run": result.baseline_run,
                "candidate_run": result.candidate_run,
                "run_level_reasons": list(result.run_level_reasons),
                "counts": {c.value: result.count(c) for c in Change},
                "pairs": [
                    {
                        "scenario_id": p.scenario_id,
                        "variant": p.variant,
                        "repetition": p.repetition,
                        "change": p.change.value,
                        "baseline": p.baseline,
                        "candidate": p.candidate,
                        "reasons": list(p.reasons),
                    }
                    for p in result.pairs
                ],
            }
        )
        raise typer.Exit(int(code))
    typer.echo(f"Baseline:  {result.baseline_run}")
    typer.echo(f"Candidate: {result.candidate_run}")
    for reason in result.run_level_reasons:
        typer.echo(f"Not comparable at run level: {reason}")
    typer.echo("")
    for p in result.pairs:
        label = p.scenario_id + (f" #{p.repetition}" if p.repetition else "")
        before, after = p.baseline or "-", p.candidate or "-"
        line = f"{label:<24} {p.change.value:<13} {before:<26} -> {after}"
        typer.echo(line)
        for reason in p.reasons:
            typer.echo(f"{'':<24} reason: {reason}")
    typer.echo("")
    typer.echo(", ".join(f"{c.value.lower()}={result.count(c)}" for c in Change))
    raise typer.Exit(int(code))


class ReportFormat(StrEnum):
    JSON = "json"
    TEXT = "text"
    HTML = "html"


@app.command("report")
def report_command(
    run_id: Annotated[str, typer.Argument(help="Run ID from 'agentfence runs list'.")],
    output_format: Annotated[
        ReportFormat, typer.Option("--format", help="json, text or html (self-contained).")
    ] = ReportFormat.TEXT,
    output: Annotated[
        Path | None,
        typer.Option("--output", help="Write to this new file (never overwrites)."),
    ] = None,
) -> None:
    """Export a saved run's report after verifying it.

    Read-only unless --output is given; refuses to export if the report or its evidence
    fails verification. HTML is a single offline file with a strict Content-Security-Policy,
    no scripts and no remote resources.
    """
    verified = _verified(run_id)
    if output_format is ReportFormat.JSON:
        text = verified.text
    elif output_format is ReportFormat.HTML:
        text = render_html(verified.report, notes=reproduction_notes(verified.report))
    else:
        text = render_summary(verified.report) + "".join(
            f"{line}\n" for line in reproduction_notes(verified.report)
        )
    if output is None:
        typer.echo(text, nl=False)
        return
    try:
        written = write_export(output, text)
    except (ExportError, OSError) as exc:
        _fail(str(exc), ExitCode.USAGE_ERROR)
    typer.echo(f"Wrote {scrub_text(str(written), home=os.environ.get('HOME'))}", err=True)


def main() -> None:
    """Console-script entry point."""
    app(prog_name="agentfence")


__all__ = ["app", "main", "version_info"]
