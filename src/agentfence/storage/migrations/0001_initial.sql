-- SPDX-License-Identifier: Apache-2.0
-- AgentFence Lab storage schema v1 (docs/07 section 3, ADR-013).
-- Applied inside one transaction by agentfence.storage.db.migrate().

CREATE TABLE suite_runs (
    id                TEXT PRIMARY KEY,
    started_at        TEXT NOT NULL,
    ended_at          TEXT NOT NULL,
    status            TEXT NOT NULL,
    tool_version      TEXT NOT NULL,
    scenario_profile  TEXT NOT NULL,
    agent             TEXT NOT NULL,
    agent_profile     TEXT NOT NULL,
    seed              INTEGER NOT NULL,
    repeat            INTEGER NOT NULL CHECK (repeat >= 1),
    isolation_tier    TEXT NOT NULL,
    report_sha256     TEXT
) STRICT;

CREATE TABLE scenarios (
    scenario_id       TEXT NOT NULL,
    scenario_version  TEXT NOT NULL,
    content_sha256    TEXT NOT NULL,
    PRIMARY KEY (scenario_id, scenario_version)
) STRICT;

CREATE TABLE attempts (
    id                TEXT PRIMARY KEY,
    suite_run_id      TEXT NOT NULL REFERENCES suite_runs(id),
    scenario_id       TEXT NOT NULL,
    scenario_version  TEXT NOT NULL,
    variant           TEXT NOT NULL CHECK (variant IN ('adversarial', 'benign')),
    repetition        INTEGER NOT NULL CHECK (repetition >= 0),
    status            TEXT NOT NULL,
    adapter_name      TEXT NOT NULL,
    adapter_version   TEXT NOT NULL,
    adapter_profile   TEXT NOT NULL,
    seed              INTEGER NOT NULL,
    fixture_digest    TEXT NOT NULL,
    FOREIGN KEY (scenario_id, scenario_version) REFERENCES scenarios(scenario_id, scenario_version)
) STRICT;
CREATE INDEX attempts_by_run ON attempts(suite_run_id);

CREATE TABLE events (
    id                TEXT PRIMARY KEY,
    attempt_id        TEXT NOT NULL REFERENCES attempts(id),
    seq               INTEGER NOT NULL CHECK (seq >= 0),
    wall_time         TEXT NOT NULL,
    monotonic_ns      INTEGER,
    kind              TEXT NOT NULL,
    source_trust      TEXT NOT NULL,
    payload_json      TEXT NOT NULL,
    payload_sha256    TEXT NOT NULL,
    prev_hash         TEXT NOT NULL,
    UNIQUE (attempt_id, seq)
) STRICT;

CREATE TABLE verdicts (
    attempt_id        TEXT PRIMARY KEY REFERENCES attempts(id),
    boundary_outcome  TEXT NOT NULL CHECK (boundary_outcome IN ('PASS', 'FAIL', 'INCONCLUSIVE', 'NOT_TRIGGERED')),
    utility_outcome   TEXT NOT NULL CHECK (utility_outcome IN ('COMPLETE', 'INCOMPLETE', 'UNKNOWN')),
    infrastructure_outcome TEXT NOT NULL CHECK (infrastructure_outcome IN ('OK', 'ERROR', 'TIMEOUT', 'UNSUPPORTED')),
    evaluator_version TEXT NOT NULL,
    reasoning_code    TEXT NOT NULL,
    confidence        TEXT NOT NULL CHECK (confidence IN ('HIGH', 'MEDIUM', 'LOW')),
    limitations_json  TEXT NOT NULL
) STRICT;

CREATE TABLE evidence_links (
    attempt_id        TEXT NOT NULL REFERENCES attempts(id),
    event_id          TEXT NOT NULL REFERENCES events(id),
    role              TEXT NOT NULL CHECK (role IN ('supporting', 'attempt', 'completed', 'utility')),
    PRIMARY KEY (attempt_id, event_id, role)
) STRICT;

-- Append-only evidence: events are never changed or removed through normal operation.
CREATE TRIGGER events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER verdicts_no_update BEFORE UPDATE ON verdicts
BEGIN SELECT RAISE(ABORT, 'verdicts are immutable'); END;
