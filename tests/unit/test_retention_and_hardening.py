# SPDX-License-Identifier: Apache-2.0
"""Retention cleanup and report hardening (AF-0505; docs/07 sections 10-11)."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agentfence import cli
from agentfence.adapters.mock import MockProfile
from agentfence.core.fixtures import generate
from agentfence.core.vocabulary import BoundaryOutcome, Variant
from agentfence.runtime.attempt import run_attempt
from agentfence.runtime.cleanup import plan_retention
from agentfence.scenarios import bundled_scenarios
from agentfence.storage.db import EVENTS_NO_DELETE_TRIGGER, RunStore

SCENARIOS = {e.manifest.id: e.manifest for e in bundled_scenarios("mvp")}


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    for var in (
        "XDG_STATE_HOME",
        "XDG_CONFIG_HOME",
        "AGENTFENCE_CONFIG",
        "AGENTFENCE_STATE_DIR",
        "AGENTFENCE_LOG_LEVEL",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    return home


def af(*args: str) -> Any:
    return CliRunner().invoke(cli.app, list(args))


def state(home: Path) -> Path:
    return home / ".local" / "state" / "agentfence"


def new_run(*args: str) -> str:
    return str(json.loads(af("test", "--cases", "AF-README-001", "--json", *args).stdout)["run_id"])


def age_run(home: Path, run_id: str, days: int) -> None:
    started = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    with sqlite3.connect(state(home) / "agentfence.sqlite3") as conn:
        conn.execute("UPDATE suite_runs SET started_at = ? WHERE id = ?", (started, run_id))
    conn.close()


def counts(home: Path) -> dict[str, int]:
    conn = sqlite3.connect(state(home) / "agentfence.sqlite3")
    try:
        return {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608
            for t in ("suite_runs", "attempts", "events", "verdicts", "evidence_links")
        }
    finally:
        conn.close()


def test_nothing_to_clean_without_a_database(home: Path) -> None:
    result = af("cleanup")
    assert (result.exit_code, result.stdout) == (0, "No runs recorded; nothing to clean.\n")


def test_dry_run_deletes_nothing(home: Path) -> None:
    old = new_run()
    age_run(home, old, 45)
    before = counts(home)
    result = af("cleanup")
    assert result.exit_code == 0
    assert f"run     {old}" in result.stdout
    assert "Dry run: nothing was deleted" in result.stdout
    assert counts(home) == before
    assert (state(home) / "runs" / old).exists()


def test_apply_deletes_only_expired_runs_and_restores_the_trigger(home: Path) -> None:
    old, recent = new_run(), new_run()
    age_run(home, old, 45)
    result = af("cleanup", "--apply", "--yes")
    assert result.exit_code == 0
    assert "Deleted 1 run(s)" in result.stdout
    assert not (state(home) / "runs" / old).exists()
    assert (state(home) / "runs" / recent / "report.json").exists()
    assert af("report", recent, "--format", "json").exit_code == 0
    assert af("report", old).exit_code == 2
    conn = sqlite3.connect(state(home) / "agentfence.sqlite3")
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM events")
        trigger_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'events_no_delete'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert trigger_sql == EVENTS_NO_DELETE_TRIGGER.removesuffix(";")


def test_retention_days_come_from_config(home: Path, tmp_path: Path) -> None:
    run = new_run()
    age_run(home, run, 3)
    (tmp_path / "agentfence.yaml").write_text("storage:\n  retention_days: 2\n")
    assert f"run     {run}" in af("cleanup").stdout
    (tmp_path / "agentfence.yaml").write_text("storage:\n  retention_days: 5\n")
    assert "Would delete 0 run(s)" in af("cleanup").stdout


def test_orphans_removed_unknown_entries_kept_symlinks_not_followed(
    home: Path, tmp_path: Path
) -> None:
    new_run()
    runs = state(home) / "runs"
    orphan = runs / str(uuid.uuid4())
    orphan.mkdir()
    (orphan / "report.json").write_text("{}")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    linked = runs / str(uuid.uuid4())
    linked.symlink_to(outside)
    (runs / "notes.txt").write_text("not ours")
    two_hours_ago = (datetime.now(UTC) - timedelta(hours=2)).timestamp()
    for entry in (orphan, linked):
        os.utime(entry, (two_hours_ago, two_hours_ago), follow_symlinks=False)
    in_flight = runs / str(uuid.uuid4())  # a concurrent run that has not saved its row yet
    in_flight.mkdir()
    result = af("cleanup", "--apply", "--yes", "--older-than", "30")
    assert "2 orphaned run folder(s)" in result.stdout
    assert not orphan.exists()
    assert in_flight.exists()
    assert not linked.is_symlink()
    assert (outside / "keep.txt").read_text() == "keep"
    assert (runs / "notes.txt").exists()


def test_explicit_dry_run_flag_and_conflict(home: Path) -> None:
    old = new_run()
    age_run(home, old, 45)
    assert "Dry run: nothing was deleted" in af("cleanup", "--dry-run").stdout
    both = af("cleanup", "--dry-run", "--apply", "--yes")
    assert both.exit_code == 2
    assert "cannot be combined" in both.stderr
    assert (state(home) / "runs" / old).exists()


def test_apply_refuses_without_confirmation_when_not_interactive(home: Path) -> None:
    old = new_run()
    age_run(home, old, 45)
    result = af("cleanup", "--apply")
    assert result.exit_code == 2
    assert "pass --yes" in result.stderr
    assert (state(home) / "runs" / old).exists()


def test_failed_purge_rolls_back_everything(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old = new_run()
    age_run(home, old, 45)
    before = counts(home)
    from agentfence.storage import db  # noqa: PLC0415

    broken = (*db._PURGE_STATEMENTS[:-1], "DELETE FROM no_such_table WHERE id = ?")
    monkeypatch.setattr(db, "_PURGE_STATEMENTS", broken)
    result = af("cleanup", "--apply", "--yes")
    assert result.exit_code == 4
    assert "rolled back" in result.stderr
    assert (state(home) / "runs" / old).exists()
    assert counts(home) == before
    conn = sqlite3.connect(state(home) / "agentfence.sqlite3")
    try:
        triggers = {
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
        }
    finally:
        conn.close()
    assert "events_no_delete" in triggers


def test_negative_retention_is_rejected(home: Path) -> None:
    new_run()
    assert af("cleanup", "--older-than", "-1").exit_code == 2
    with RunStore(state(home) / "agentfence.sqlite3") as store, pytest.raises(ValueError):
        plan_retention(state(home) / "runs", store, older_than_days=-1, now=datetime.now(UTC))


# --- hardening ---------------------------------------------------------------------------


def test_event_cap_truncation_is_inconclusive_end_to_end(tmp_path: Path) -> None:
    readme = SCENARIOS["AF-README-001"]
    tiny = readme.model_copy(update={"limits": readme.limits.model_copy(update={"max_events": 3})})
    result = run_attempt(tiny, MockProfile.SAFE, run_id=str(uuid.uuid4()), seed=1, workdir=tmp_path)
    assert len(result.events) == 3
    assert result.events[-1].details["status"] == "TRUNCATED"
    assert result.dropped_events > 0
    assert result.verdict.boundary is BoundaryOutcome.INCONCLUSIVE


@pytest.mark.parametrize("profile", ["safe", "unsafe"])
def test_synthetic_secret_never_reaches_any_artifact(
    home: Path, tmp_path: Path, profile: str
) -> None:
    token = generate("inventory_v1", Variant.ADVERSARIAL, 0).markers["secret_token"]
    run = json.loads(
        af("test", "--cases", "AF-CRED-001", "--agent-profile", profile, "--json").stdout
    )["run_id"]
    html = tmp_path / f"{profile}.html"
    assert af("report", run, "--format", "html", "--output", str(html)).exit_code == 0
    for artifact in (
        state(home) / "runs" / run / "report.json",
        html,
        state(home) / "agentfence.sqlite3",
    ):
        assert token.encode() not in artifact.read_bytes(), artifact.name
        assert b"AF_FAKE_TOKEN_" not in artifact.read_bytes(), artifact.name


def test_report_output_onto_an_existing_directory_is_refused(home: Path, tmp_path: Path) -> None:
    run = new_run()
    result = af("report", run, "--output", str(tmp_path))
    assert result.exit_code == 2
    assert "already exists" in result.stderr
