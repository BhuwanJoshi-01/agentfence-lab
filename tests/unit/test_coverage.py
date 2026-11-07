# SPDX-License-Identifier: Apache-2.0
"""Collector coverage and bounded stream tests (AF-0402)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from agentfence.core.coverage import CoverageStatus, derive_coverage
from agentfence.core.events import (
    STREAM_CHANNEL,
    Decision,
    EventActor,
    EventRecorder,
    EventSource,
    EventTarget,
    ObservedEvent,
    validate_stream,
)
from agentfence.core.scenario_manifest import ObservationChannel as Ch
from agentfence.core.vocabulary import EventKind, SourceTrust

T0 = datetime(2026, 11, 6, 10, 0, tzinfo=UTC)
HOST = EventSource(component="workspace_snapshot", trust=SourceTrust.HOST_OBSERVED)
AGENT_SRC = EventSource(component="agent_cli", trust=SourceTrust.AGENT_REPORTED)
COLLECTOR = EventActor.model_validate({"type": "collector", "alias": "HOST"})
COLLECTOR_T = EventTarget.model_validate({"type": "collector", "alias": "HOST"})


def recorder(max_events: int = 100) -> EventRecorder:
    return EventRecorder("att_0123abcd", wall_clock=lambda: T0, max_events=max_events)


def status(rec: EventRecorder, channel: str, value: Any, source: EventSource = HOST) -> None:
    rec.record(
        EventKind.COLLECTOR_STATUS,
        source=source,
        actor=COLLECTOR,
        target=COLLECTOR_T,
        details={"channel": channel, "status": value},
    )


def change(rec: EventRecorder, name: str = "a.py") -> ObservedEvent | None:
    return rec.record(
        EventKind.FILE_CHANGED,
        source=HOST,
        actor=EventActor.model_validate({"type": "agent_process", "alias": "AGENT"}),
        target=EventTarget.model_validate({"type": "workspace_file", "alias": f"WORKSPACE/{name}"}),
    )


REQUIRED = (Ch.WORKSPACE_DIFF, Ch.UTILITY_RUNNER)


def test_all_required_complete() -> None:
    rec = recorder()
    status(rec, "workspace_diff", "COMPLETE")
    status(rec, "utility_runner", "COMPLETE")
    report = derive_coverage(rec.events, REQUIRED)
    assert report.complete(REQUIRED)
    assert report.gaps(REQUIRED) == ()
    assert len(report.channels[Ch.WORKSPACE_DIFF].evidence) == 1


def test_silent_channel_is_missing() -> None:
    rec = recorder()
    status(rec, "workspace_diff", "COMPLETE")
    report = derive_coverage(rec.events, REQUIRED)
    assert report.status(Ch.UTILITY_RUNNER) is CoverageStatus.MISSING
    assert report.gaps(REQUIRED) == (Ch.UTILITY_RUNNER,)


def test_no_events_means_everything_missing() -> None:
    report = derive_coverage([], REQUIRED)
    assert report.gaps(REQUIRED) == (Ch.UTILITY_RUNNER, Ch.WORKSPACE_DIFF)


@pytest.mark.parametrize(
    ("reports", "expected"),
    [
        (["COMPLETE", "TRUNCATED"], CoverageStatus.TRUNCATED),
        (["TRUNCATED", "COMPLETE"], CoverageStatus.TRUNCATED),
        (["COMPLETE", "ERROR", "TRUNCATED"], CoverageStatus.ERROR),
        (["COMPLETE", "COMPLETE"], CoverageStatus.COMPLETE),
    ],
)
def test_worst_status_wins(reports: list[str], expected: CoverageStatus) -> None:
    rec = recorder()
    for value in reports:
        status(rec, "access_broker", value)
    assert derive_coverage(rec.events, [Ch.ACCESS_BROKER]).status(Ch.ACCESS_BROKER) is expected


def test_agent_cannot_vouch_for_collectors() -> None:
    rec = recorder()
    status(rec, "workspace_diff", "COMPLETE", source=AGENT_SRC)
    report = derive_coverage(rec.events, [Ch.WORKSPACE_DIFF])
    assert report.status(Ch.WORKSPACE_DIFF) is CoverageStatus.MISSING
    assert report.ignored_evidence == (rec.events[0].event_id,)


@pytest.mark.parametrize(
    ("channel", "value"),
    [
        ("telepathy", "COMPLETE"),
        ("workspace_diff", "GREAT"),
        ("workspace_diff", "MISSING"),
        ("workspace_diff", 1),
        ("workspace_diff", None),
    ],
)
def test_malformed_status_events_ignored(channel: str, value: Any) -> None:
    rec = recorder()
    status(rec, channel, value)
    report = derive_coverage(rec.events, [Ch.WORKSPACE_DIFF])
    assert report.status(Ch.WORKSPACE_DIFF) is CoverageStatus.MISSING
    assert len(report.ignored_evidence) == 1


def test_unexpected_but_reported_channel_still_listed() -> None:
    rec = recorder()
    status(rec, "network_sink", "COMPLETE")
    report = derive_coverage(rec.events, [Ch.WORKSPACE_DIFF])
    assert set(report.channels) == {Ch.WORKSPACE_DIFF, Ch.NETWORK_SINK}


def test_non_status_events_do_not_create_coverage() -> None:
    rec = recorder()
    change(rec)
    assert derive_coverage(rec.events, [Ch.WORKSPACE_DIFF]).status(Ch.WORKSPACE_DIFF) is (
        CoverageStatus.MISSING
    )


def test_recorder_reserves_last_slot_for_truncation_notice() -> None:
    rec = recorder(max_events=4)
    results = [change(rec, f"f{i}.py") for i in range(10)]
    events = rec.events
    assert len(events) == 4
    assert sum(r is not None for r in results) == 3
    assert rec.truncated
    assert rec.dropped == 7
    last = events[-1]
    assert last.kind is EventKind.COLLECTOR_STATUS
    assert last.details == {"channel": STREAM_CHANNEL, "status": "TRUNCATED", "limit": 4}
    assert last.source.trust is SourceTrust.HOST_OBSERVED
    assert validate_stream(events) == events


def test_truncated_stream_downgrades_every_channel() -> None:
    rec = recorder(max_events=3)
    status(rec, "workspace_diff", "COMPLETE")
    status(rec, "utility_runner", "COMPLETE")
    change(rec)  # triggers truncation notice in the last slot
    report = derive_coverage(rec.events, REQUIRED)
    assert report.stream_truncated
    assert report.status(Ch.WORKSPACE_DIFF) is CoverageStatus.TRUNCATED
    assert report.gaps(REQUIRED) == (Ch.UTILITY_RUNNER, Ch.WORKSPACE_DIFF)


def test_stream_complete_notice_is_harmless() -> None:
    rec = recorder()
    status(rec, STREAM_CHANNEL, "COMPLETE")
    status(rec, "workspace_diff", "COMPLETE")
    report = derive_coverage(rec.events, [Ch.WORKSPACE_DIFF])
    assert not report.stream_truncated
    assert report.complete([Ch.WORKSPACE_DIFF])


def test_truncation_keeps_missing_as_missing() -> None:
    rec = recorder(max_events=2)
    change(rec)
    change(rec)
    report = derive_coverage(rec.events, [Ch.ACCESS_BROKER])
    assert report.status(Ch.ACCESS_BROKER) is CoverageStatus.MISSING


def test_recorder_minimum_capacity() -> None:
    with pytest.raises(ValueError, match="at least 2"):
        recorder(max_events=1)


def test_denied_event_through_bounded_recorder() -> None:
    rec = recorder()
    event = rec.record(
        EventKind.FILE_READ_DENIED,
        source=EventSource(component="fixture_access_broker", trust=SourceTrust.BROKER_OBSERVED),
        actor=EventActor.model_validate({"type": "agent_process", "alias": "AGENT"}),
        target=EventTarget.model_validate({"type": "synthetic_file", "alias": "SYNTHETIC_SECRET"}),
        decision=Decision.DENIED,
    )
    assert event is not None
    assert event.decision is Decision.DENIED
