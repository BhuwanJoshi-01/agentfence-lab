"""SQLite run store (AF-0406, ADR-013)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from agentfence.adapters.mock import MockProfile
from agentfence.core.scenario_manifest import ScenarioManifest
from agentfence.runtime.attempt import stepping_clock
from agentfence.runtime.suite import SuiteRunResult, run_suite
from agentfence.scenarios import bundled_scenarios
from agentfence.storage import db
from agentfence.storage.db import RunStore, StoreError, _split_sql, as_dict, open_store

SCENARIOS = [
    e.manifest for e in bundled_scenarios("mvp") if e.manifest.id.startswith("AF-README-001")
]


def make_suite(tmp_path: Path, run_id: str | None = None, **kw: Any) -> SuiteRunResult:
    clock = stepping_clock(datetime(2026, 11, 7, tzinfo=UTC), timedelta(milliseconds=1))
    return run_suite(
        kw.pop("scenarios", SCENARIOS),
        kw.pop("profile", MockProfile.UNSAFE),
        scenario_profile="mvp",
        seed=42,
        workdir=tmp_path / "work",
        clock=clock,
        run_id=run_id,
        **kw,
    )


@pytest.fixture
def store(tmp_path: Path) -> Iterator[RunStore]:
    s = RunStore(tmp_path / "state" / "agentfence.sqlite3")
    yield s
    s.close()


def raw(store: RunStore) -> sqlite3.Connection:
    return store._conn


def test_fresh_store_is_migrated_private_and_wal(tmp_path: Path, store: RunStore) -> None:
    assert store.schema_version == 1
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert store.path.parent.stat().st_mode & 0o777 == 0o700
    assert raw(store).execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert raw(store).execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_reopening_does_not_reapply_migrations(tmp_path: Path, store: RunStore) -> None:
    store.close()
    with RunStore(store.path) as again:
        assert again.schema_version == 1
        rows = raw(again).execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
        assert rows == 1


def test_save_and_read_back(tmp_path: Path, store: RunStore) -> None:
    suite = make_suite(tmp_path)
    store.save_suite(suite, report_sha256="ab" * 32)
    summary = store.get_run(suite.run_id)
    assert summary is not None
    assert (summary.attempts, summary.boundary_counts) == (2, {"FAIL": 1, "NOT_TRIGGERED": 1})
    assert as_dict(summary)["agent_profile"] == "unsafe"
    for attempt in suite.attempts:
        assert tuple(store.events_for(attempt.attempt_id)) == attempt.events
    assert store.verify_run(suite.run_id) == []


def test_evidence_links_point_at_stored_events(tmp_path: Path, store: RunStore) -> None:
    suite = make_suite(tmp_path)
    store.save_suite(suite)
    orphans = (
        raw(store)
        .execute(
            "SELECT COUNT(*) FROM evidence_links l LEFT JOIN events e ON e.id = l.event_id "
            "WHERE e.id IS NULL"
        )
        .fetchone()[0]
    )
    assert orphans == 0
    roles = {r[0] for r in raw(store).execute("SELECT role FROM evidence_links")}
    assert {"supporting", "completed", "utility"} <= roles


def test_list_runs_newest_first_and_limit(tmp_path: Path, store: RunStore) -> None:
    ids = []
    for i in range(3):
        suite = make_suite(tmp_path / str(i))
        store.save_suite(suite)
        ids.append(suite.run_id)
    listed = [r.run_id for r in store.list_runs(limit=2)]
    assert len(listed) == 2
    assert set(listed) <= set(ids)
    with pytest.raises(ValueError, match="limit"):
        store.list_runs(limit=0)
    assert store.get_run("00000000-0000-0000-0000-000000000000") is None


def test_saving_the_same_run_twice_fails_and_leaves_one_copy(
    tmp_path: Path, store: RunStore
) -> None:
    suite = make_suite(tmp_path)
    store.save_suite(suite)
    with pytest.raises(sqlite3.IntegrityError):
        store.save_suite(suite)
    assert raw(store).execute("SELECT COUNT(*) FROM suite_runs").fetchone()[0] == 1
    assert store.verify_run(suite.run_id) == []


def test_save_is_atomic(tmp_path: Path, store: RunStore, monkeypatch: pytest.MonkeyPatch) -> None:
    suite = make_suite(tmp_path)
    original = RunStore._save_attempt
    calls = {"n": 0}

    def crash_on_second(self: RunStore, conn: sqlite3.Connection, s: Any, a: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("power cut")
        original(self, conn, s, a)

    monkeypatch.setattr(RunStore, "_save_attempt", crash_on_second)
    with pytest.raises(RuntimeError, match="power cut"):
        store.save_suite(suite)
    for table in ("suite_runs", "attempts", "events", "verdicts", "evidence_links"):
        assert raw(store).execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0  # noqa: S608


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE events SET kind = 'agent.started'",
        "DELETE FROM events",
        "UPDATE verdicts SET boundary_outcome = 'PASS'",
    ],
)
def test_evidence_is_append_only(tmp_path: Path, store: RunStore, statement: str) -> None:
    store.save_suite(make_suite(tmp_path))
    with pytest.raises(sqlite3.IntegrityError, match=r"append-only|immutable"):
        raw(store).execute(statement)


@pytest.mark.parametrize(
    ("statement", "params"),
    [
        (
            "INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "att_x",
                "no-such-run",
                "AF-X-001",
                "1.0.0",
                "adversarial",
                0,
                "COMPLETED",
                "mock",
                "0",
                "safe",
                1,
                "d",
            ),
        ),
        (
            "INSERT INTO verdicts VALUES (?,?,?,?,?,?,?,?)",
            ("no-such-attempt", "PASS", "COMPLETE", "OK", "1", "X", "HIGH", "[]"),
        ),
    ],
)
def test_foreign_keys_enforced(store: RunStore, statement: str, params: tuple[Any, ...]) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        raw(store).execute(statement, params)


def test_outcome_vocabulary_is_checked(tmp_path: Path, store: RunStore) -> None:
    suite = make_suite(tmp_path)
    store.save_suite(suite)
    attempt = suite.attempts[0].attempt_id
    raw(store).execute("DROP TRIGGER verdicts_no_update")
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        raw(store).execute(
            "UPDATE verdicts SET boundary_outcome='SAFE' WHERE attempt_id=?", (attempt,)
        )


def test_verify_detects_tampering(tmp_path: Path, store: RunStore) -> None:
    """Someone with direct DB access drops the trigger and edits evidence."""
    suite = make_suite(tmp_path)
    store.save_suite(suite)
    raw(store).execute("DROP TRIGGER events_no_update")
    raw(store).execute(
        "UPDATE events SET payload_json = replace(payload_json, 'broker_observed', "
        "'host_observed') WHERE seq = 0"
    )
    problems = store.verify_run(suite.run_id)
    assert any("payload hash mismatch" in p for p in problems)
    assert any("hash chain broken" in p for p in problems)
    assert any("fails validation" in p for p in problems)


def test_verify_detects_deleted_event(tmp_path: Path, store: RunStore) -> None:
    suite = make_suite(tmp_path)
    store.save_suite(suite)
    raw(store).execute("DROP TRIGGER events_no_delete")
    raw(store).execute("PRAGMA foreign_keys = OFF")
    raw(store).execute("DELETE FROM events WHERE seq = 1")
    assert any("sequence gap" in p for p in store.verify_run(suite.run_id))


def test_verify_unknown_run(store: RunStore) -> None:
    assert store.verify_run("nope") == ["run nope not found or has no attempts"]


def test_changed_manifest_needs_a_new_version(tmp_path: Path, store: RunStore) -> None:
    store.save_suite(make_suite(tmp_path / "a"))
    data = SCENARIOS[0].model_dump(mode="json")
    data["title"] = "Same version, different content"
    changed = [ScenarioManifest.model_validate(data), SCENARIOS[1]]
    with pytest.raises(StoreError, match="bump the scenario version"):
        store.save_suite(make_suite(tmp_path / "b", scenarios=changed))


def test_newer_schema_is_refused(tmp_path: Path, store: RunStore) -> None:
    raw(store).execute("INSERT INTO schema_migrations (version) VALUES (99)")
    store.close()
    with pytest.raises(StoreError, match="newer than this AgentFence"):
        RunStore(store.path)


def test_old_sqlite_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db.sqlite3, "sqlite_version_info", (3, 31, 1))
    with pytest.raises(StoreError, match="too old"):
        RunStore(tmp_path / "s" / "a.sqlite3")


def test_loose_database_file_refused(tmp_path: Path) -> None:
    path = tmp_path / "state" / "agentfence.sqlite3"
    path.parent.mkdir(mode=0o700)
    path.write_bytes(b"")
    path.chmod(0o644)
    with pytest.raises(StoreError, match="chmod 600"):
        RunStore(path)


def test_symlinked_database_refused(tmp_path: Path) -> None:
    real = tmp_path / "real.sqlite3"
    real.write_bytes(b"")
    real.chmod(0o600)
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    (state / "agentfence.sqlite3").symlink_to(real)
    with pytest.raises(StoreError, match="symlink"):
        RunStore(state / "agentfence.sqlite3")


def test_migration_numbering_must_be_contiguous(monkeypatch: pytest.MonkeyPatch) -> None:
    class Fake:
        def __init__(self, name: str) -> None:
            self.name = name

        def read_text(self, encoding: str) -> str:
            return "SELECT 1;"

    class Folder:
        def iterdir(self) -> list[Fake]:
            return [Fake("0001_a.sql"), Fake("0003_c.sql"), Fake("notes.txt")]

    class Files:
        def joinpath(self, _name: str) -> Folder:
            return Folder()

    monkeypatch.setattr(db.resources, "files", lambda _pkg: Files())
    with pytest.raises(StoreError, match="without gaps"):
        db._migrations()


def test_split_sql_keeps_trigger_bodies_and_rejects_incomplete() -> None:
    script = (
        "-- comment\nCREATE TABLE t (x INTEGER);\n"
        "CREATE TRIGGER t1 BEFORE DELETE ON t\nBEGIN SELECT RAISE(ABORT, 'no'); END;\n"
    )
    statements = _split_sql(script)
    assert len(statements) == 2
    assert statements[1].endswith("END;")
    with pytest.raises(StoreError, match="incomplete"):
        _split_sql("CREATE TABLE t (x INTEGER")


def test_open_store_uses_state_dir(tmp_path: Path) -> None:
    with open_store(tmp_path / "state") as s:
        assert s.path == tmp_path / "state" / "agentfence.sqlite3"
