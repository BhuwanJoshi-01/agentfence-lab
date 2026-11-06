# ADR-010: Frozen outcome vocabulary and per-artifact schema versions

- **Status:** accepted
- **Date:** 2026-11-06
- **Task:** AF-0006
- **Related:** ADR-002 (independent axes), ADR-003 (no global score), ADR-005 (deterministic evaluator)

## Context

Outcome labels appear in the SQLite store, JSON and HTML reports, CLI exit codes,
the dashboard and CI scripts written by users. If those strings drift, old reports
stop comparing cleanly and CI scripts silently stop detecting failures.

## Decision

1. All labels live in one module, `agentfence.core.vocabulary`, as `StrEnum` or `IntEnum`
   members. Other modules import them; nobody re-types the strings.
2. The three outcome axes stay separate types: `BoundaryOutcome`, `UtilityOutcome`,
   `InfrastructureOutcome`.
3. Each serialized artifact has its own integer schema version in `SCHEMA_VERSIONS`
   (`config`, `scenario`, `event`, `report`, `attestation`). A scenario's own semantic
   `version` is separate from the manifest `schema_version`.
4. Golden tests (`tests/unit/test_vocabulary.py`) pin every value. If you change one,
   bump the matching schema version, add a changelog entry, and explain the change in
   the PR. Don't just edit the expected value in the test.
5. `SourceTrust.authoritative_for_access` is the single definition of which sources
   can prove an access: `host_observed` and `broker_observed` only.
6. Exit-code precedence lives in a pure function, `run_exit_code()`:
   preflight (3) > infrastructure (4) > FAIL (1) > strict-inconclusive (5) > success (0).
   `NOT_TRIGGERED` never produces a non-zero exit, and `--strict` affects only `INCONCLUSIVE`.

## Consequences

- Adding a new event kind or status is a schema change and needs review.
- The run lifecycle FSM (`agentfence.core.lifecycle`) uses `RunStatus`, which is versioned
  the same way.
