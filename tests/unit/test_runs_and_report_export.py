# SPDX-License-Identifier: Apache-2.0
"""Saved runs: listing, verified export and reproduction (AF-0506)."""

from __future__ import annotations

import contextlib
import hashlib
import json
import shlex
import sqlite3
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentfence import cli
from agentfence.core.events import ObservedEvent
from agentfence.core.vocabulary import EventKind
from agentfence.reporting.export import reproduce_command
from agentfence.reporting.json_report import SuiteReport
from agentfence.storage.db import RunStore


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    (tmp_path / "cwd").mkdir()
    for var in (
        "XDG_STATE_HOME",
        "XDG_CONFIG_HOME",
        "AGENTFENCE_CONFIG",
        "AGENTFENCE_STATE_DIR",
        "AGENTFENCE_LOG_LEVEL",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path / "cwd")
    return home


def af(*args: str) -> tuple[int, str, str]:
    result = CliRunner().invoke(cli.app, list(args))
    return result.exit_code, result.stdout, result.stderr


def new_run(*args: str) -> str:
    pinned = [] if "--cases" in args else ["--cases", "AF-README-001"]
    _, out, _ = af("test", "--json", *pinned, *args)
    return str(json.loads(out)["run_id"])


def run_dir(home: Path, run_id: str) -> Path:
    return home / ".local" / "state" / "agentfence" / "runs" / run_id


@contextlib.contextmanager
def database(home: Path) -> Iterator[sqlite3.Connection]:
    """sqlite3's own context manager commits but never closes; this one does both."""
    conn = sqlite3.connect(home / ".local" / "state" / "agentfence" / "agentfence.sqlite3")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def test_runs_list_before_any_run(home: Path) -> None:
    code, out, err = af("runs", "list")
    assert (code, out) == (2, "")
    assert "no runs recorded yet" in err


def test_runs_list_text_and_json(home: Path) -> None:
    first = new_run("--agent-profile", "unsafe")
    second = new_run("--agent-profile", "safe")
    _, out, _ = af("runs", "list")
    assert first in out
    assert second in out
    assert "mock-unsafe" in out
    _, out, _ = af("runs", "list", "--json", "--limit", "1")
    rows = json.loads(out)["runs"]
    assert len(rows) == 1
    assert rows[0]["isolation_tier"] == "mock-inprocess"


def test_runs_show_verifies_and_explains(home: Path) -> None:
    run_id = new_run("--agent-profile", "unsafe")
    code, out, _ = af("runs", "show", run_id)
    assert code == 0
    assert "Integrity: report hash and evidence hash chain verified" in out
    assert "Reproduce with: agentfence test --agent mock --agent-profile unsafe" in out
    assert "Fixture digests: AF-README-001 " in out
    code, out, _ = af("runs", "show", run_id, "--json")
    assert SuiteReport.model_validate_json(out).run_id == run_id


def test_report_json_is_byte_identical_to_saved_report(home: Path) -> None:
    run_id = new_run()
    _, out, _ = af("report", run_id, "--format", "json")
    assert out == (run_dir(home, run_id) / "report.json").read_text()


def test_report_text_includes_summary_and_reproduction(home: Path) -> None:
    run_id = new_run("--agent-profile", "unsafe")
    _, out, _ = af("report", run_id)
    assert "AF-README-001              FAIL" in out
    assert "Scenario versions: AF-README-001 1.0.0, AF-README-001-B 1.0.0" in out


def test_report_output_writes_new_private_file_only(home: Path, tmp_path: Path) -> None:
    run_id = new_run()
    target = tmp_path / "export.json"
    code, out, err = af("report", run_id, "--format", "json", "--output", str(target))
    assert (code, out) == (0, "")
    assert "Wrote" in err
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    code, _, err = af("report", run_id, "--output", str(target))
    assert code == 2
    assert "already exists" in err


def test_report_output_into_missing_directory(home: Path, tmp_path: Path) -> None:
    run_id = new_run()
    code, _, err = af("report", run_id, "--output", str(tmp_path / "nope" / "x.txt"))
    assert code == 2
    assert "does not exist" in err


@pytest.mark.parametrize(
    "run_id", ["00000000-0000-0000-0000-000000000000", "not-a-uuid", "../../etc/passwd"]
)
def test_unknown_or_malformed_run_ids_exit_2(home: Path, run_id: str) -> None:
    new_run()
    code, out, _ = af("report", run_id)
    assert (code, out) == (2, "")


def test_modified_report_is_refused(home: Path) -> None:
    run_id = new_run("--agent-profile", "unsafe")
    path = run_dir(home, run_id) / "report.json"
    path.write_text(path.read_text().replace('"FAIL"', '"PASS"', 1))
    code, out, err = af("report", run_id)
    assert (code, out) == (4, "")
    assert "does not match the hash" in err


def test_deleted_report_is_refused(home: Path) -> None:
    run_id = new_run()
    (run_dir(home, run_id) / "report.json").unlink()
    code, _, err = af("runs", "show", run_id)
    assert code == 4
    assert "missing or unreadable" in err


def test_symlinked_report_is_refused(home: Path, tmp_path: Path) -> None:
    run_id = new_run()
    path = run_dir(home, run_id) / "report.json"
    decoy = tmp_path / "decoy.json"
    decoy.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(decoy)
    assert af("report", run_id)[0] == 4


def test_tampered_evidence_is_refused_even_with_matching_report(home: Path) -> None:
    run_id = new_run("--agent-profile", "unsafe")
    with database(home) as conn:
        conn.execute("DROP TRIGGER events_no_update")
        conn.execute(
            "UPDATE events SET payload_json = replace(payload_json, 'host_observed', "
            "'agent_reported') WHERE seq = 1"
        )
    code, _, err = af("report", run_id)
    assert code == 4
    assert "stored evidence failed verification" in err


def test_report_citing_missing_evidence_is_refused(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stored events verify, but the report cites an event the store does not hold."""
    run_id = new_run("--agent-profile", "unsafe")
    original = RunStore.events_for

    def without_cited(self: RunStore, attempt_id: str) -> list[ObservedEvent]:
        return [e for e in original(self, attempt_id) if e.kind is not EventKind.FILE_CHANGED]

    monkeypatch.setattr(RunStore, "events_for", without_cited)
    code, _, err = af("report", run_id)
    assert code == 4
    assert "cites evidence that is not stored" in err


def test_report_for_a_different_run_is_refused(home: Path) -> None:
    first = new_run()
    second = new_run()
    src = run_dir(home, first) / "report.json"
    dst = run_dir(home, second) / "report.json"
    dst.write_bytes(src.read_bytes())
    with database(home) as conn:
        digest = hashlib.sha256(src.read_bytes()).hexdigest()
        conn.execute("UPDATE suite_runs SET report_sha256 = ? WHERE id = ?", (digest, second))
    code, _, err = af("report", second)
    assert code == 4
    assert "different run" in err


def test_reproduce_command_reproduces_verdicts(home: Path) -> None:
    run_id = new_run("--agent-profile", "unsafe", "--repeat", "2", "--seed", "11")
    _, original, _ = af("report", run_id, "--format", "json")
    report = SuiteReport.model_validate_json(original)
    argv = shlex.split(reproduce_command(report))
    assert argv[:2] == ["agentfence", "test"]
    _, again, _ = af(*argv[1:], "--json")
    rerun = SuiteReport.model_validate_json(again)
    assert rerun.run_id != report.run_id

    def outcome(r: SuiteReport) -> list[tuple[str, int, str, str, str]]:
        return [
            (x.scenario_id, x.repetition, x.boundary, x.utility, x.fixture_digest)
            for x in r.results
        ]

    assert outcome(rerun) == outcome(report)


def test_reproduce_lists_benign_control_that_ran_alone(home: Path) -> None:
    run_id = new_run("--cases", "AF-README-001-B")
    _, out, _ = af("report", run_id, "--format", "json")
    assert "--cases AF-README-001-B" in reproduce_command(SuiteReport.model_validate_json(out))


def test_loose_database_is_an_infrastructure_failure(home: Path) -> None:
    new_run()
    (home / ".local" / "state" / "agentfence" / "agentfence.sqlite3").chmod(0o644)
    assert af("runs", "list")[0] == 4
