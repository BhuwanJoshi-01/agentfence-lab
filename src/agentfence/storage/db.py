"""Local SQLite run store (AF-0406; docs/07 section 3, ADR-013).

* The database file is created ``0600`` before SQLite opens it, inside a ``0700`` state
  directory; foreign keys are enforced on every connection.
* Migrations are numbered SQL files applied in order inside a transaction. A database
  newer than the code is refused, never downgraded.
* A whole suite run is written in one transaction: a crash leaves all of it or none.
* Events are append-only (database triggers) and carry a per-attempt hash chain;
  :meth:`RunStore.verify_run` recomputes it and every stored event ID.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Final

from agentfence.core.events import ObservedEvent
from agentfence.runtime.attempt import AttemptResult
from agentfence.runtime.suite import SuiteRunResult
from agentfence.storage.paths import ensure_private_dir, verify_private_file

MIN_SQLITE: Final = (3, 37, 0)  # STRICT tables
GENESIS: Final = "0" * 64


class StoreError(RuntimeError):
    """The store is unusable or a stored run failed an integrity check."""


@dataclass(frozen=True, slots=True)
class RunSummary:
    run_id: str
    started_at: str
    status: str
    scenario_profile: str
    agent_profile: str
    attempts: int
    boundary_counts: dict[str, int]


def _migrations() -> list[tuple[int, str]]:
    folder = resources.files("agentfence.storage").joinpath("migrations")
    found = []
    for entry in folder.iterdir():
        name = entry.name
        if name.endswith(".sql") and name[:4].isdigit():
            found.append((int(name[:4]), entry.read_text(encoding="utf-8")))
    found.sort()
    if [n for n, _ in found] != list(range(1, len(found) + 1)):
        raise StoreError("migration files must be numbered 0001, 0002, ... without gaps")
    return found


def chain_hash(prev_hash: str, payload_sha256: str) -> str:
    return hashlib.sha256(f"{prev_hash}:{payload_sha256}".encode()).hexdigest()


def _canonical(event: ObservedEvent) -> str:
    return json.dumps(event.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


class RunStore:
    def __init__(self, path: Path) -> None:
        if sqlite3.sqlite_version_info < MIN_SQLITE:
            raise StoreError(
                f"SQLite {sqlite3.sqlite_version} is too old; "
                f"{'.'.join(map(str, MIN_SQLITE))}+ is required"
            )
        ensure_private_dir(path.parent)
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
        try:
            verify_private_file(path)
        except PermissionError as exc:
            raise StoreError(str(exc)) from exc
        self.path = path
        self._conn = sqlite3.connect(path, isolation_level=None)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.row_factory = sqlite3.Row
        try:
            self.migrate()
        except BaseException:
            self._conn.close()  # never leak a connection from a refused database
            raise

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> RunStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @contextlib.contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        self._conn.execute("COMMIT")

    @property
    def schema_version(self) -> int:
        row = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
        if row is None:
            return 0
        value = self._conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        return int(value or 0)

    def migrate(self) -> int:
        migrations = _migrations()
        current = self.schema_version
        if current > len(migrations):
            raise StoreError(
                f"database schema v{current} is newer than this AgentFence (v{len(migrations)}); "
                "upgrade AgentFence instead of downgrading the database"
            )
        for number, sql in migrations[current:]:
            with self._transaction() as conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS schema_migrations "
                    "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT "
                    "(strftime('%Y-%m-%dT%H:%M:%fZ','now'))) STRICT"
                )
                for statement in _split_sql(sql):
                    conn.execute(statement)
                conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (number,))
        return self.schema_version

    # --- writes ---------------------------------------------------------------------

    def save_suite(self, suite: SuiteRunResult, *, report_sha256: str | None = None) -> None:
        status = "COMPLETED"
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO suite_runs (id, started_at, ended_at, status, tool_version, "
                "scenario_profile, agent, agent_profile, seed, repeat, isolation_tier, "
                "report_sha256) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    suite.run_id,
                    suite.started_at.isoformat(),
                    suite.ended_at.isoformat(),
                    status,
                    suite.tool_version,
                    suite.scenario_profile,
                    suite.agent,
                    suite.agent_profile.value,
                    suite.seed,
                    suite.repeat,
                    suite.isolation_tier,
                    report_sha256,
                ),
            )
            for attempt in suite.attempts:
                self._save_attempt(conn, suite, attempt)

    def _save_attempt(
        self, conn: sqlite3.Connection, suite: SuiteRunResult, attempt: AttemptResult
    ) -> None:
        scenario = attempt.scenario
        manifest_sha = hashlib.sha256(scenario.model_dump_json(by_alias=True).encode()).hexdigest()
        conn.execute(
            "INSERT OR IGNORE INTO scenarios (scenario_id, scenario_version, content_sha256) "
            "VALUES (?,?,?)",
            (scenario.id, scenario.version, manifest_sha),
        )
        stored = conn.execute(
            "SELECT content_sha256 FROM scenarios WHERE scenario_id=? AND scenario_version=?",
            (scenario.id, scenario.version),
        ).fetchone()[0]
        if stored != manifest_sha:
            raise StoreError(
                f"{scenario.id} {scenario.version} content differs from the stored manifest; "
                "bump the scenario version when its content changes"
            )
        conn.execute(
            "INSERT INTO attempts (id, suite_run_id, scenario_id, scenario_version, variant, "
            "repetition, status, adapter_name, adapter_version, adapter_profile, seed, "
            "fixture_digest) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                attempt.attempt_id,
                suite.run_id,
                scenario.id,
                scenario.version,
                scenario.variant.value,
                attempt.repetition,
                attempt.lifecycle[-1].target.value if attempt.lifecycle else "CREATED",
                suite.agent,
                suite.tool_version,
                attempt.profile.value,
                attempt.seed,
                attempt.fixture_digest,
            ),
        )
        prev = GENESIS
        for event in attempt.events:
            payload = _canonical(event)
            payload_sha = hashlib.sha256(payload.encode()).hexdigest()
            prev = chain_hash(prev, payload_sha)
            conn.execute(
                "INSERT INTO events (id, attempt_id, seq, wall_time, monotonic_ns, kind, "
                "source_trust, payload_json, payload_sha256, prev_hash) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    _event_row_id(attempt.attempt_id, event),
                    attempt.attempt_id,
                    event.sequence,
                    event.timestamp.isoformat(),
                    event.monotonic_ns,
                    event.kind.value,
                    event.source.trust.value,
                    payload,
                    payload_sha,
                    prev,
                ),
            )
        v = attempt.verdict
        conn.execute(
            "INSERT INTO verdicts (attempt_id, boundary_outcome, utility_outcome, "
            "infrastructure_outcome, evaluator_version, reasoning_code, confidence, "
            "limitations_json) VALUES (?,?,?,?,?,?,?,?)",
            (
                attempt.attempt_id,
                v.boundary.value,
                v.utility.value,
                v.infrastructure.value,
                v.evaluator_version,
                v.reason_code,
                v.confidence.value,
                json.dumps(list(v.limitations)),
            ),
        )
        links = {
            *(("supporting", e) for e in v.evidence_ids),
            *(("attempt", e) for e in v.prohibited_attempts),
            *(("completed", e) for e in v.completed_effects),
            *(("utility", e) for e in v.utility_evidence),
        }
        for role, event_id in sorted(links):
            conn.execute(
                "INSERT INTO evidence_links (attempt_id, event_id, role) VALUES (?,?,?)",
                (attempt.attempt_id, f"{attempt.attempt_id}/{event_id}", role),
            )

    # --- reads ----------------------------------------------------------------------

    def list_runs(self, limit: int = 50) -> list[RunSummary]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        rows = self._conn.execute(
            "SELECT * FROM suite_runs ORDER BY started_at DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._summary(row) for row in rows]

    def get_run(self, run_id: str) -> RunSummary | None:
        row = self._conn.execute("SELECT * FROM suite_runs WHERE id=?", (run_id,)).fetchone()
        return None if row is None else self._summary(row)

    def _summary(self, row: sqlite3.Row) -> RunSummary:
        counts = {
            str(outcome): int(count)
            for outcome, count in self._conn.execute(
                "SELECT v.boundary_outcome, COUNT(*) FROM verdicts v JOIN attempts a "
                "ON a.id = v.attempt_id WHERE a.suite_run_id=? GROUP BY v.boundary_outcome",
                (row["id"],),
            ).fetchall()
        }
        attempts = self._conn.execute(
            "SELECT COUNT(*) FROM attempts WHERE suite_run_id=?", (row["id"],)
        ).fetchone()[0]
        return RunSummary(
            run_id=row["id"],
            started_at=row["started_at"],
            status=row["status"],
            scenario_profile=row["scenario_profile"],
            agent_profile=row["agent_profile"],
            attempts=int(attempts),
            boundary_counts=counts,
        )

    def events_for(self, attempt_id: str) -> list[ObservedEvent]:
        rows = self._conn.execute(
            "SELECT payload_json FROM events WHERE attempt_id=? ORDER BY seq", (attempt_id,)
        ).fetchall()
        return [ObservedEvent.model_validate_json(row[0]) for row in rows]

    def verify_run(self, run_id: str) -> list[str]:
        """Recompute payload hashes, the per-attempt chain and every event ID."""
        problems: list[str] = []
        attempts = self._conn.execute(
            "SELECT id FROM attempts WHERE suite_run_id=? ORDER BY id", (run_id,)
        ).fetchall()
        if not attempts:
            return [f"run {run_id} not found or has no attempts"]
        for (attempt_id,) in attempts:
            prev = GENESIS
            rows = self._conn.execute(
                "SELECT seq, payload_json, payload_sha256, prev_hash FROM events "
                "WHERE attempt_id=? ORDER BY seq",
                (attempt_id,),
            ).fetchall()
            for index, row in enumerate(rows):
                payload_sha = hashlib.sha256(row["payload_json"].encode()).hexdigest()
                if row["seq"] != index:
                    problems.append(f"{attempt_id}: sequence gap at {index}")
                if payload_sha != row["payload_sha256"]:
                    problems.append(f"{attempt_id}#{row['seq']}: payload hash mismatch")
                prev = chain_hash(prev, payload_sha)
                if prev != row["prev_hash"]:
                    problems.append(f"{attempt_id}#{row['seq']}: hash chain broken")
                try:
                    ObservedEvent.model_validate_json(row["payload_json"])
                except ValueError:
                    problems.append(f"{attempt_id}#{row['seq']}: event fails validation")
        return problems


def _event_row_id(attempt_id: str, event: ObservedEvent) -> str:
    # Content IDs are unique per attempt; prefix with the attempt so identical events in
    # different attempts (same seed, same clock) cannot collide.
    return f"{attempt_id}/{event.event_id}"


def _split_sql(script: str) -> list[str]:
    """Split a migration into complete statements (trigger bodies stay intact).

    ``executescript`` is not used because it commits implicitly, which would break the
    one-transaction-per-migration guarantee.
    """
    statements: list[str] = []
    buffer = ""
    for line in script.splitlines(keepends=True):
        if not buffer.strip() and line.strip().startswith("--"):
            continue
        buffer += line
        if sqlite3.complete_statement(buffer):
            if buffer.strip():
                statements.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        raise StoreError("migration ends with an incomplete SQL statement")
    return statements


def open_store(state_dir: Path) -> RunStore:
    return RunStore(state_dir / "agentfence.sqlite3")


def as_dict(summary: RunSummary) -> dict[str, Any]:
    return {
        "run_id": summary.run_id,
        "started_at": summary.started_at,
        "status": summary.status,
        "scenario_profile": summary.scenario_profile,
        "agent_profile": summary.agent_profile,
        "attempts": summary.attempts,
        "boundary": summary.boundary_counts,
    }
