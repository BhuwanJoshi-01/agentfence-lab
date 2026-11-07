# ADR-013: Standard-library sqlite3 with versioned SQL migrations

- **Status:** accepted
- **Date:** 2026-11-07
- **Task:** AF-0406
- **Related:** docs/02 section 9, docs/07 section 3, NFR-010 (small dependency tree), AGENTS.md
  ("lock dependencies; change stack only with recorded ADR")

## Context

The blueprint's stack defaults name SQLAlchemy and Alembic for persistence. What the MVP needs is
small: seven tables, append-only events, foreign keys, a schema version and one writer per run.
The project values a small, auditable dependency tree (NFR-010), and the most important storage
guarantees (append-only rows, foreign keys, integrity hashes) are enforced in SQL either way.

## Decision

1. Use Python's standard-library `sqlite3` module. No ORM.
2. Keep migrations as numbered SQL scripts inside the package (`storage/migrations/NNNN_*.sql`),
   applied in order inside a transaction and recorded in `schema_migrations`. A database with
   a newer version than the code knows is refused, not downgraded.
3. Enforce invariants in the database itself:
   - `PRAGMA foreign_keys = ON` on every connection;
   - triggers reject `UPDATE` and `DELETE` on `events` and `UPDATE` on `verdicts`;
   - every event row stores its payload SHA-256 and a per-attempt hash chain (`prev_hash`).
4. Write a whole suite run in one transaction, so a crash leaves either the full run or nothing.
5. Create the database file `0600` inside the `0700` state directory before SQLite opens it.

## Consequences

- One less dependency family (SQLAlchemy, Alembic, Mako) to pin, audit and license-check.
- Raw SQL has to be written and reviewed carefully. Every query uses parameters, never string
  formatting, and tests cover the triggers and foreign keys directly.
- If a multi-user backend (Postgres) is ever needed, revisit this ADR. The SQL is kept close to
  portable to make that easier.
- The hash chain detects accidental corruption and casual tampering. It does not authenticate
  origin against someone who controls the host (docs/07 section 6).
