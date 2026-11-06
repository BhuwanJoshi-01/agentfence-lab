"""Event model tests (AF-0401; docs/07 sections 2, 4, 5, 12)."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from itertools import count
from typing import Any

import pytest
from pydantic import ValidationError

from agentfence.core.events import (
    Decision,
    EventActor,
    EventRecorder,
    EventSource,
    EventStreamError,
    EventTarget,
    ObservedEvent,
    compute_event_id,
    make_event,
    validate_stream,
)
from agentfence.core.vocabulary import EventKind, SourceTrust

T0 = datetime(2026, 11, 6, 10, 0, tzinfo=UTC)
BROKER = {"component": "fixture_access_broker", "trust": "broker_observed"}
AGENT = {"type": "agent_process", "alias": "AGENT"}
SECRET = {"type": "synthetic_file", "alias": "SYNTHETIC_SECRET"}


def fields(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "attempt_id": "att_e94be48d",
        "sequence": 0,
        "timestamp": T0,
        "kind": "file.read_denied",
        "source": BROKER,
        "actor": AGENT,
        "target": SECRET,
        "decision": "DENIED",
        "details": {"reason": "outside_authorized_task_scope"},
    }
    base.update(overrides)
    return base


def test_make_event_assigns_stable_content_id() -> None:
    first = make_event(**fields())
    second = make_event(**fields())
    assert first.event_id == second.event_id
    assert first.event_id.startswith("evt_")
    assert len(first.event_id) == 20


def test_different_content_gives_different_id() -> None:
    assert make_event(**fields()).event_id != make_event(**fields(sequence=1)).event_id


def test_round_trip_through_json() -> None:
    event = make_event(**fields(monotonic_ns=123, content_hash="sha256:" + "a" * 64))
    restored = ObservedEvent.model_validate_json(event.model_dump_json())
    assert restored == event


def test_tampered_event_rejected() -> None:
    data = make_event(**fields()).model_dump(mode="json")
    data["source"]["trust"] = "host_observed"
    with pytest.raises(ValidationError, match="does not match content"):
        ObservedEvent.model_validate(data)


def test_event_id_cannot_be_supplied() -> None:
    with pytest.raises(ValueError, match="computed from content"):
        make_event(**fields(), event_id="evt_" + "0" * 16)


def test_compute_event_id_ignores_key_order() -> None:
    assert compute_event_id({"a": 1, "b": 2}) == compute_event_id({"b": 2, "a": 1})


@pytest.mark.parametrize(
    ("trust", "authoritative"),
    [("host_observed", True), ("broker_observed", True), ("agent_reported", False)],
)
def test_authority_follows_source_trust(trust: str, *, authoritative: bool) -> None:
    event = make_event(**fields(source={"component": "x_collector", "trust": trust}))
    assert event.authoritative_for_access is authoritative


@pytest.mark.parametrize(
    ("kind", "decision"),
    [
        ("file.read_denied", "NONE"),
        ("file.read_denied", "ALLOWED"),
        ("network.connect_denied", "NONE"),
        ("file.read_granted", "DENIED"),
        ("file.read_granted", "NONE"),
    ],
)
def test_decision_must_match_kind(kind: str, decision: str) -> None:
    with pytest.raises(ValidationError, match="decision="):
        make_event(**fields(kind=kind, decision=decision))


@pytest.mark.parametrize(
    "alias",
    [
        "/home/dev/.ssh/id_ed25519",
        "/etc/passwd",
        "workspace/src/app.py",
        "WORKSPACE/../etc",
        "WORKSPACE//x",
        "WORKSPACE/./x",
        "WORKSPACE/a\\b",
        "",
    ],
)
def test_target_must_be_a_safe_alias(alias: str) -> None:
    with pytest.raises(ValidationError):
        make_event(**fields(target={"type": "workspace_file", "alias": alias}))


@pytest.mark.parametrize("alias", ["WORKSPACE", "WORKSPACE/src/app.py", "MOCK_SINK", "SANDBOX"])
def test_valid_aliases(alias: str) -> None:
    make_event(**fields(target={"type": "workspace_file", "alias": alias}))


@pytest.mark.parametrize(
    "details",
    [
        {"path": "/home/dev/project"},
        {"bad key": "x"},
        {"x": "y" * 600},
        {f"k{i}": i for i in range(40)},
        {"nested": {"a": 1}},
    ],
)
def test_details_are_bounded_and_path_free(details: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        make_event(**fields(details=details))


@pytest.mark.parametrize(
    "override",
    [
        {"timestamp": datetime(2026, 11, 6, 10, 0)},
        {"attempt_id": "attempt-1"},
        {"sequence": -1},
        {"monotonic_ns": -5},
        {"kind": "file.teleported"},
        {"content_hash": "md5:abc"},
        {"source": {"component": "Broker!", "trust": "broker_observed"}},
        {"source": {"component": "broker", "trust": "totally_trusted"}},
        {"schema_version": 2},
        {"surprise": True},
    ],
)
def test_invalid_fields_rejected(override: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        make_event(**fields(**override))


def test_iso_timestamp_string_accepted_from_storage() -> None:
    event = make_event(**fields(timestamp="2026-11-06T10:00:00Z"))
    assert event.timestamp == T0


def stream(n: int) -> list[ObservedEvent]:
    return [
        make_event(**fields(sequence=i, timestamp=T0 + timedelta(seconds=i), details={"i": i}))
        for i in range(n)
    ]


def test_validate_stream_sorts_shuffled_input() -> None:
    events = stream(6)
    shuffled = events[:]
    random.Random(7).shuffle(shuffled)
    assert validate_stream(shuffled) == tuple(events)


def test_empty_stream_is_valid() -> None:
    assert validate_stream([]) == ()


def test_stream_rejects_gaps() -> None:
    events = stream(4)
    with pytest.raises(EventStreamError, match="contiguous"):
        validate_stream([events[0], events[1], events[3]])


def test_stream_must_start_at_zero() -> None:
    with pytest.raises(EventStreamError, match="contiguous"):
        validate_stream(stream(3)[1:])


def test_stream_rejects_duplicate_ids() -> None:
    event = stream(1)[0]
    with pytest.raises(EventStreamError, match="duplicate event IDs"):
        validate_stream([event, event])


def test_stream_rejects_duplicate_sequence() -> None:
    a = make_event(**fields(sequence=0, details={"v": 1}))
    b = make_event(**fields(sequence=0, details={"v": 2}))
    with pytest.raises(EventStreamError, match="contiguous"):
        validate_stream([a, b])


def test_stream_rejects_mixed_attempts() -> None:
    a = make_event(**fields())
    b = make_event(**fields(attempt_id="att_aaaaaaaa", sequence=1))
    with pytest.raises(EventStreamError, match="mixes attempts"):
        validate_stream([a, b])


def test_stream_rejects_time_going_backwards() -> None:
    a = make_event(**fields(sequence=0, timestamp=T0))
    b = make_event(**fields(sequence=1, timestamp=T0 - timedelta(seconds=1)))
    with pytest.raises(EventStreamError, match="backwards"):
        validate_stream([a, b])


def test_recorder_assigns_contiguous_sequence_and_clock() -> None:
    ticks = count(1000, 5)
    recorder = EventRecorder(
        "att_0123abcd",
        wall_clock=lambda: T0,
        monotonic_clock=lambda: next(ticks),
    )
    src = EventSource(component="snapshot_collector", trust=SourceTrust.HOST_OBSERVED)
    actor = EventActor.model_validate(AGENT)
    for name in ("a.py", "b.py", "c.py"):
        recorder.record(
            EventKind.FILE_CHANGED,
            source=src,
            actor=actor,
            target=EventTarget.model_validate(
                {"type": "workspace_file", "alias": f"WORKSPACE/src/{name}"}
            ),
            content_hash="sha256:" + "0" * 64,
        )
    events = recorder.events
    assert [e.sequence for e in events] == [0, 1, 2]
    assert [e.monotonic_ns for e in events] == [1000, 1005, 1010]
    assert all(e.attempt_id == "att_0123abcd" for e in events)
    assert validate_stream(events) == events


def test_recorder_without_monotonic_clock_stores_null() -> None:
    recorder = EventRecorder("att_0123abcd", wall_clock=lambda: T0)
    event = recorder.record(
        EventKind.FILE_READ_DENIED,
        source=EventSource.model_validate(BROKER),
        actor=EventActor.model_validate(AGENT),
        target=EventTarget.model_validate(SECRET),
        decision=Decision.DENIED,
    )
    assert event.monotonic_ns is None
