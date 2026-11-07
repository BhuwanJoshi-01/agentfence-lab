# SPDX-License-Identifier: Apache-2.0
"""Ctrl-C and crash recovery (AF-0407; docs/02 section 5, docs/06 section 10)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agentfence import cli
from agentfence.runtime import suite as suite_module
from agentfence.runtime.cleanup import reconcile_stale_workspaces
from agentfence.storage.paths import InsecurePathError


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


def state(home: Path) -> Path:
    return home / ".local" / "state" / "agentfence"


def test_ctrl_c_mid_suite_cancels_cleanly(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = suite_module.run_attempt
    calls = {"n": 0}

    def interrupt_second(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt
        return real(*args, **kwargs)

    monkeypatch.setattr(suite_module, "run_attempt", interrupt_second)
    result = CliRunner().invoke(cli.app, ["test", "--cases", "AF-README-001"])
    assert result.exit_code == 130
    assert result.stdout == ""
    assert "run cancelled; no results were saved" in result.stderr
    runs = state(home) / "runs"
    assert not runs.exists() or list(runs.iterdir()) == []
    leftovers = [p for p in (state(home) / "work").rglob("*") if p.is_file()]
    assert leftovers == []


def test_interrupt_inside_an_attempt_still_removes_its_workspace(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agentfence.runtime.attempt as attempt_module  # noqa: PLC0415

    def interrupt(*_a: Any, **_k: Any) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(attempt_module, "run_utility", interrupt)
    result = CliRunner().invoke(cli.app, ["test", "--cases", "AF-README-001"])
    assert result.exit_code == 130
    assert [p for p in (state(home) / "work").rglob("*") if p.is_file()] == []


def plant_stale(work: Path) -> str:
    run_id = str(uuid.uuid4())
    leftover = work / run_id / "att_0000dead" / "src"
    leftover.mkdir(parents=True)
    (leftover / "calculator.py").write_text("stale")
    work.chmod(0o700)
    return run_id


def test_next_run_removes_workspaces_left_by_a_killed_run(home: Path) -> None:
    work = state(home) / "work"
    work.mkdir(parents=True, mode=0o700)
    state(home).chmod(0o700)
    stale = plant_stale(work)
    result = CliRunner().invoke(cli.app, ["test", "--cases", "AF-README-001", "--json"])
    assert result.exit_code == 0
    assert "removed 1 stale workspace(s)" in result.stderr
    assert not (work / stale).exists()
    json.loads(result.stdout)  # stdout stays pure JSON


def test_reconcile_never_follows_symlinks(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir(mode=0o700)
    outside = tmp_path / "precious"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    link_id = str(uuid.uuid4())
    (work / link_id).symlink_to(outside)
    inner = work / plant_stale(work)
    (inner / "att_0000dead" / "escape").symlink_to(outside)
    result = reconcile_stale_workspaces(work)
    assert set(result.removed) == {link_id, inner.name}
    assert (outside / "keep.txt").read_text() == "keep"
    assert list(work.iterdir()) == []


def test_reconcile_leaves_unknown_entries_alone(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir(mode=0o700)
    (work / "notes.txt").write_text("not ours to delete")
    (work / "not-a-uuid").mkdir()
    upper = str(uuid.uuid4()).upper()
    (work / upper).mkdir()
    result = reconcile_stale_workspaces(work)
    assert result.removed == ()
    assert set(result.skipped) == {"notes.txt", "not-a-uuid", upper}
    assert (work / "notes.txt").exists()


def test_reconcile_skips_uuid_named_files(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir(mode=0o700)
    name = str(uuid.uuid4())
    (work / name).write_text("a file, not a workspace")
    assert reconcile_stale_workspaces(work).skipped == (name,)


def test_reconcile_missing_dir_is_a_no_op(tmp_path: Path) -> None:
    result = reconcile_stale_workspaces(tmp_path / "absent")
    assert (result.removed, result.skipped) == ((), ())


def test_reconcile_refuses_a_loose_work_dir(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    plant = work / str(uuid.uuid4())
    plant.mkdir()
    work.chmod(0o777)
    with pytest.raises(InsecurePathError):
        reconcile_stale_workspaces(work)
    assert plant.exists()


def test_root_help_lists_cancelled_exit_code() -> None:
    text = CliRunner(env={"COLUMNS": "100"}).invoke(cli.app, ["--help"]).stdout
    assert "130  cancelled with Ctrl-C" in text
