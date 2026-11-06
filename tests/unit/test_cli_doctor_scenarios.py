"""CLI contract tests for ``doctor`` and ``scenarios list`` (AF-0102, FR-001)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentfence import cli
from agentfence.core.capabilities import HostCapabilities
from agentfence.core.vocabulary import CheckStatus
from agentfence.runtime.host_probe import HostFacts, assess_host

FACTS = HostFacts(
    system="linux",
    machine="x86_64",
    python_version=(3, 12, 4),
    euid=1000,
    executables={"podman": "/usr/bin/podman", "docker": None, "git": "/usr/bin/git"},
    cgroup2=True,
    home="/home/dev",
)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point HOME/XDG at an empty temp tree and run from an empty cwd."""
    home = tmp_path / "home"
    work = tmp_path / "work"
    home.mkdir()
    work.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("AGENTFENCE_CONFIG", raising=False)
    monkeypatch.delenv("AGENTFENCE_STATE_DIR", raising=False)
    monkeypatch.delenv("AGENTFENCE_LOG_LEVEL", raising=False)
    monkeypatch.chdir(work)
    monkeypatch.setattr(cli, "gather_host_facts", lambda: FACTS)
    return tmp_path


def all_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file())


def test_doctor_human_output_and_exit_3(runner: CliRunner) -> None:
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 3
    assert "doctor (read-only)" in result.stdout
    assert "UNKNOWN  runtime.rootless" in result.stdout
    assert "Isolation: UNSUPPORTED" in result.stdout
    assert "Config: built-in defaults" in result.stdout
    assert result.stderr == ""


def test_doctor_json_is_single_document(runner: CliRunner) -> None:
    result = runner.invoke(cli.app, ["doctor", "--json"])
    assert result.exit_code == 3
    payload = json.loads(result.stdout)
    assert payload["isolation_state"] == "UNSUPPORTED"
    assert payload["blocking_checks"] == ["runtime.rootless"]
    assert payload["exit_code"] == 3
    assert payload["host"]["os"] == "linux"
    assert {c["id"] for c in payload["host"]["checks"]} >= {"platform.os", "cgroup.v2"}


def test_doctor_exit_0_only_when_every_required_check_passes(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    def all_pass(facts: HostFacts) -> HostCapabilities:
        caps = assess_host(facts)
        checks = tuple(c.model_copy(update={"status": CheckStatus.PASS}) for c in caps.checks)
        return caps.model_copy(update={"checks": checks})

    monkeypatch.setattr(cli, "assess_host", all_pass)
    result = runner.invoke(cli.app, ["doctor", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["isolation_state"] == "OFFLINE_SAFE"


def test_doctor_reports_failed_required_check(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "gather_host_facts", lambda: replace(FACTS, euid=0))
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 3
    assert "process.not_root [FAIL]" in result.stdout


def test_doctor_writes_nothing(runner: CliRunner, isolated_env: Path) -> None:
    before = all_files(isolated_env)
    runner.invoke(cli.app, ["doctor"])
    runner.invoke(cli.app, ["doctor", "--json"])
    assert all_files(isolated_env) == before
    assert not (isolated_env / "home" / ".local").exists()


def test_doctor_lists_project_config_source(runner: CliRunner, isolated_env: Path) -> None:
    config = isolated_env / "work" / "agentfence.yaml"
    config.write_text("scenarios:\n  repeat: 2\n", encoding="utf-8")
    result = runner.invoke(cli.app, ["doctor", "--json"])
    assert json.loads(result.stdout)["config_sources"] == [f"project:{config.resolve()}"]


def test_doctor_scrubs_home_in_config_source(runner: CliRunner, isolated_env: Path) -> None:
    user_cfg = isolated_env / "home" / ".config" / "agentfence" / "config.yaml"
    user_cfg.parent.mkdir(parents=True)
    user_cfg.write_text("scenarios:\n  repeat: 2\n", encoding="utf-8")
    result = runner.invoke(cli.app, ["doctor", "--json"])
    assert json.loads(result.stdout)["config_sources"] == [
        "user:$HOME/.config/agentfence/config.yaml"
    ]


@pytest.mark.parametrize(
    ("config_text", "fragment"),
    [
        ("runtime:\n  no_host_mounts: false\n", "cannot be overridden"),
        ("runtime:\n  hooks: [x]\n", "runtime.hooks: unknown key"),
        ("a: 1\na: 2\n", "duplicate key"),
        ("ui:\n  listen: 0.0.0.0\n", "not loopback"),
    ],
)
def test_doctor_invalid_config_exits_2_on_stderr(
    runner: CliRunner, isolated_env: Path, config_text: str, fragment: str
) -> None:
    (isolated_env / "work" / "agentfence.yaml").write_text(config_text, encoding="utf-8")
    result = runner.invoke(cli.app, ["doctor", "--json"])
    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr.startswith("agentfence: error: ")
    assert fragment in result.stderr


def test_doctor_missing_explicit_config_exits_2(runner: CliRunner) -> None:
    result = runner.invoke(cli.app, ["doctor", "--config", "missing.yaml"])
    assert result.exit_code == 2
    assert "not found" in result.stderr


def test_relative_state_dir_env_exits_2(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTFENCE_STATE_DIR", "relative/state")
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 2
    assert "absolute" in result.stderr


def test_invalid_log_level_exits_2(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTFENCE_LOG_LEVEL", "LOUD")
    result = runner.invoke(cli.app, ["version"])
    assert result.exit_code == 2
    assert "AGENTFENCE_LOG_LEVEL" in result.stderr


def test_scenarios_list_empty_profile_is_honest(runner: CliRunner) -> None:
    result = runner.invoke(cli.app, ["scenarios", "list"])
    assert result.exit_code == 0
    assert "No scenarios are bundled for profile 'mvp' yet" in result.stdout


def test_scenarios_list_json(runner: CliRunner) -> None:
    result = runner.invoke(cli.app, ["scenarios", "list", "--json", "--profile", "mvp"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"profile": "mvp", "scenarios": [], "schema_version": 1}


def test_scenarios_list_rows(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentfence.core.scenario_manifest import ScenarioManifest  # noqa: PLC0415
    from agentfence.core.strict_yaml import load_mapping  # noqa: PLC0415
    from agentfence.scenarios import CatalogEntry  # noqa: PLC0415

    data = Path(__file__).parent / "data" / "readme_adversarial.yaml"
    manifest = ScenarioManifest.model_validate(load_mapping(data.read_bytes(), source="t"))
    monkeypatch.setattr(cli, "bundled_scenarios", lambda _p: [CatalogEntry("mvp/x.yaml", manifest)])
    human = runner.invoke(cli.app, ["scenarios", "list"])
    assert human.stdout.startswith("AF-README-001      1.0.0    adversarial  prompt-injection")
    payload = json.loads(runner.invoke(cli.app, ["scenarios", "list", "--json"]).stdout)
    assert payload["scenarios"][0]["benign_pair"] == "AF-README-001-B"


@pytest.mark.parametrize("profile", ["../etc", "MVP"])
def test_scenarios_list_bad_profile_exits_2(runner: CliRunner, profile: str) -> None:
    result = runner.invoke(cli.app, ["scenarios", "list", "--profile", profile])
    assert result.exit_code == 2
    assert "invalid profile" in result.stderr


def test_scenarios_without_subcommand_shows_help(runner: CliRunner) -> None:
    result = runner.invoke(cli.app, ["scenarios"])
    assert "list" in result.output
