"""CLI contract: help snapshots, command tree, stream separation, locale (AF-0107).

Help text is part of the contract (docs/06 section 10). To accept an intended change:

    AGENTFENCE_UPDATE_SNAPSHOTS=1 pytest tests/unit/test_cli_contract.py

then review the diff under ``tests/snapshots/cli/`` like any other code change.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentfence import cli
from agentfence.runtime.host_probe import HostFacts

SNAPSHOTS = Path(__file__).resolve().parents[1] / "snapshots" / "cli"
UPDATE = os.environ.get("AGENTFENCE_UPDATE_SNAPSHOTS") == "1"
TERMINAL_ENV = {"COLUMNS": "100", "LINES": "40", "NO_COLOR": "1", "TERM": "dumb"}

HELP_TARGETS = {
    "root": [],
    "version": ["version"],
    "doctor": ["doctor"],
    "scenarios": ["scenarios"],
    "scenarios_list": ["scenarios", "list"],
    "scenarios_validate": ["scenarios", "validate"],
    "test": ["test"],
    "runs": ["runs"],
    "runs_list": ["runs", "list"],
    "runs_show": ["runs", "show"],
    "runs_compare": ["runs", "compare"],
    "report": ["report"],
}

FACTS = HostFacts(
    system="linux",
    machine="x86_64",
    python_version=(3, 12, 4),
    euid=1000,
    executables={"podman": "/usr/bin/podman"},
    cgroup2=True,
)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner(env=TERMINAL_ENV)


@pytest.fixture(autouse=True)
def clean_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for var in (
        "XDG_STATE_HOME",
        "XDG_CONFIG_HOME",
        "AGENTFENCE_CONFIG",
        "AGENTFENCE_STATE_DIR",
        "AGENTFENCE_LOG_LEVEL",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    work = tmp_path / "work dir ü"
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setattr(cli, "gather_host_facts", lambda: FACTS)
    return work


@pytest.mark.parametrize("name", sorted(HELP_TARGETS))
def test_help_snapshot(runner: CliRunner, name: str) -> None:
    result = runner.invoke(cli.app, [*HELP_TARGETS[name], "--help"])
    assert result.exit_code == 0, result.output
    snapshot = SNAPSHOTS / f"help_{name}.txt"
    if UPDATE:
        snapshot.write_text(result.stdout, encoding="utf-8")
    assert snapshot.exists(), f"missing snapshot {snapshot}; run with AGENTFENCE_UPDATE_SNAPSHOTS=1"
    assert result.stdout == snapshot.read_text(encoding="utf-8")


def _command_tree(group: object, prefix: str = "") -> set[str]:
    import typer.main  # noqa: PLC0415

    click_group = typer.main.get_command(group)  # type: ignore[arg-type]
    names: set[str] = set()
    for name, command in getattr(click_group, "commands", {}).items():
        full = f"{prefix}{name}"
        sub = getattr(command, "commands", None)
        if sub:
            names |= {f"{full} {child}" for child in sub}
        else:
            names.add(full)
    return names


def test_command_tree_is_exactly_the_implemented_contract() -> None:
    assert _command_tree(cli.app) == {
        "version",
        "doctor",
        "scenarios list",
        "scenarios validate",
        "test",
        "runs list",
        "runs show",
        "runs compare",
        "report",
    }


def test_root_help_documents_every_exit_code(runner: CliRunner) -> None:
    text = runner.invoke(cli.app, ["--help"]).stdout
    for code, meaning in cli.EXIT_CODES:
        assert f"  {code}  {meaning}" in text


@pytest.mark.parametrize(
    "args",
    [
        ["doctor", "--bogus"],
        ["scenarios", "list", "--bogus"],
        ["scenarios", "nope"],
        ["version", "extra-arg"],
        ["scenarios", "list", "--profile"],
    ],
)
def test_usage_errors_exit_2_with_nothing_on_stdout(runner: CliRunner, args: list[str]) -> None:
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr


@pytest.mark.parametrize(
    "args",
    [["version", "--json"], ["doctor", "--json"], ["scenarios", "list", "--json"]],
)
def test_json_stdout_stays_pure_with_verbose_logging(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    monkeypatch.setenv("AGENTFENCE_LOG_LEVEL", "DEBUG")
    result = runner.invoke(cli.app, args)
    json.loads(result.stdout)
    assert result.stdout.count("\n") == 1
    if args[0] != "version":
        assert "agentfence: info:" in result.stderr


def test_config_path_with_spaces_and_unicode(runner: CliRunner, clean_env: Path) -> None:
    config = clean_env / "my config ü.yaml"
    config.write_text("scenarios:\n  repeat: 2\n", encoding="utf-8")
    config.chmod(0o644)
    result = runner.invoke(cli.app, ["doctor", "--json", "--config", str(config)])
    assert json.loads(result.stdout)["config_sources"] == [f"project:{config.resolve()}"]


def test_config_symlink_is_resolved(runner: CliRunner, clean_env: Path) -> None:
    real = clean_env / "real.yaml"
    real.write_text("{}\n", encoding="utf-8")
    real.chmod(0o644)
    link = clean_env / "agentfence.yaml"
    link.symlink_to(real)
    result = runner.invoke(cli.app, ["doctor", "--json"])
    assert result.exit_code == 3
    assert json.loads(result.stdout)["config_sources"] == []  # empty mapping sets nothing


def test_scenarios_list_writes_nothing(runner: CliRunner, tmp_path: Path) -> None:
    before = sorted(tmp_path.rglob("*"))
    runner.invoke(cli.app, ["scenarios", "list"])
    runner.invoke(cli.app, ["scenarios", "list", "--json"])
    assert sorted(tmp_path.rglob("*")) == before


@pytest.mark.parametrize(
    "env_overrides",
    [
        {"LC_ALL": "C", "LANG": "C", "TZ": "UTC"},
        {"LC_ALL": "C.UTF-8", "LANG": "C.UTF-8", "TZ": "Asia/Kathmandu"},
        {"LC_ALL": "tr_TR.UTF-8", "LANG": "tr_TR.UTF-8", "TZ": "America/St_Johns"},
    ],
)
def test_json_output_is_locale_and_timezone_independent(env_overrides: dict[str, str]) -> None:
    def run(extra: dict[str, str]) -> dict[str, object]:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("LC_", "AGENTFENCE_"))}
        env.update(extra)
        done = subprocess.run(
            [sys.executable, "-m", "agentfence", "scenarios", "list", "--json"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
            timeout=60,
        )
        assert done.returncode == 0, done.stderr
        parsed: dict[str, object] = json.loads(done.stdout)
        return parsed

    assert run(env_overrides) == run({"LC_ALL": "C.UTF-8", "TZ": "UTC"})
