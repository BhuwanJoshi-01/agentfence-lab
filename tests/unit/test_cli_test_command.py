"""`agentfence test` (AF-0502, FR-001): runs the mock, saves the report, sets exit codes."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentfence import cli
from agentfence.reporting.json_report import SuiteReport
from agentfence.storage.db import RunStore


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    work = tmp_path / "cwd"
    home.mkdir()
    work.mkdir()
    for var in (
        "XDG_STATE_HOME",
        "XDG_CONFIG_HOME",
        "AGENTFENCE_CONFIG",
        "AGENTFENCE_STATE_DIR",
        "AGENTFENCE_LOG_LEVEL",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(work)
    return home


def state(home: Path) -> Path:
    return home / ".local" / "state" / "agentfence"


def invoke(*args: str) -> tuple[int, str, str]:
    result = CliRunner().invoke(cli.app, ["test", *args])
    return result.exit_code, result.stdout, result.stderr


@pytest.mark.parametrize(
    ("profile", "code"),
    [
        ("safe", 0),
        ("unsafe", 1),
        ("refusal", 0),
        ("no-exposure", 0),
        ("no-telemetry", 0),
        ("timeout", 4),
    ],
)
def test_exit_codes_follow_docs_06(home: Path, profile: str, code: int) -> None:
    exit_code, out, err = invoke("--agent", "mock", "--agent-profile", profile)
    assert exit_code == code, err
    assert "Disclaimer:" in out


def test_strict_turns_inconclusive_into_exit_5(home: Path) -> None:
    assert invoke("--agent-profile", "no-telemetry", "--strict")[0] == 5


def test_strict_from_config(home: Path) -> None:
    Path("agentfence.yaml").write_text("evaluation:\n  fail_on_inconclusive: true\n")
    assert invoke("--agent-profile", "no-telemetry")[0] == 5


def test_human_summary(home: Path) -> None:
    _, out, _ = invoke("--agent-profile", "unsafe")
    assert "AF-README-001              FAIL           utility=COMPLETE" in out
    assert "Benign controls            1/1 COMPLETE" in out
    assert "No containment claim is made." in out
    assert "Report: $HOME/.local/state/agentfence/runs/" in out
    assert str(home) not in out


def test_json_output_is_the_saved_report(home: Path) -> None:
    code, out, _ = invoke("--agent-profile", "safe", "--json")
    assert code == 0
    report = SuiteReport.model_validate_json(out)
    saved = state(home) / "runs" / report.run_id / "report.json"
    assert saved.read_text() == out
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    assert stat.S_IMODE(saved.parent.stat().st_mode) == 0o700


def test_run_is_recorded_and_verifiable(home: Path) -> None:
    _, out, _ = invoke("--agent-profile", "unsafe", "--json")
    run_id = json.loads(out)["run_id"]
    with RunStore(state(home) / "agentfence.sqlite3") as store:
        summary = store.get_run(run_id)
        assert summary is not None
        assert summary.boundary_counts == {"FAIL": 1, "NOT_TRIGGERED": 1}
        assert store.verify_run(run_id) == []


def test_workspaces_are_cleaned_up(home: Path) -> None:
    invoke("--agent-profile", "unsafe")
    leftovers = [p for p in (state(home) / "work").rglob("*") if p.is_file()]
    assert leftovers == []


def test_cases_selection_adds_benign_pair(home: Path) -> None:
    _, out, _ = invoke("--cases", "AF-README-001", "--json")
    assert [r["scenario_id"] for r in json.loads(out)["results"]] == [
        "AF-README-001",
        "AF-README-001-B",
    ]


def test_benign_only_selection(home: Path) -> None:
    _, out, _ = invoke("--cases", "AF-README-001-B", "--json")
    assert [r["scenario_id"] for r in json.loads(out)["results"]] == ["AF-README-001-B"]


def test_repeat_and_seed(home: Path) -> None:
    _, out, _ = invoke("--repeat", "2", "--seed", "7", "--json")
    data = json.loads(out)
    assert (data["repeat"], data["seed"], data["summary"]["attempts"]) == (2, 7, 4)


def test_repeat_in_summary(home: Path) -> None:
    _, out, _ = invoke("--repeat", "2", "--agent-profile", "unsafe")
    assert "AF-README-001 #1" in out
    assert "Repeat: 2" in out


@pytest.mark.parametrize(
    ("args", "code", "fragment"),
    [
        (["--agent", "codex"], 6, "not supported"),
        (["--agent-profile", "reckless"], 2, "unknown mock profile"),
        (["--cases", "AF-NOPE-001"], 2, "unknown scenario ID"),
        (["--profile", "nothing-here"], 2, "no scenarios are bundled"),
        (["--profile", "../etc"], 2, "scenarios.profile"),
        (["--repeat", "0"], 2, "scenarios.repeat"),
        (["--seed", "-1"], 2, ""),
    ],
)
def test_usage_errors(home: Path, args: list[str], code: int, fragment: str) -> None:
    exit_code, out, err = invoke(*args)
    assert exit_code == code
    assert out == ""
    assert fragment in err


def test_config_cannot_be_weakened_from_project(home: Path) -> None:
    Path("agentfence.yaml").write_text("runtime:\n  no_host_mounts: false\n")
    exit_code, _, err = invoke()
    assert exit_code == 2
    assert "cannot be overridden" in err


def test_config_supplies_defaults(home: Path) -> None:
    Path("agentfence.yaml").write_text("agent:\n  profile: unsafe\nscenarios:\n  repeat: 2\n")
    exit_code, out, _ = invoke("--json")
    assert exit_code == 1
    assert json.loads(out)["repeat"] == 2


def test_cli_flags_override_config(home: Path) -> None:
    Path("agentfence.yaml").write_text("agent:\n  profile: unsafe\n")
    assert invoke("--agent-profile", "safe")[0] == 0


def test_unwritable_state_is_an_infrastructure_failure(home: Path) -> None:
    target = state(home)
    target.mkdir(parents=True)
    target.chmod(0o755)
    exit_code, _, err = invoke()
    assert exit_code == 4
    assert "chmod 700" in err
