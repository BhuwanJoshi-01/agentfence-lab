"""Structured observation events with provenance (AF-0401; docs/07 sections 2, 4, 5).

An :class:`ObservedEvent` is the only thing the evaluator may base a verdict on. Each
event records *who observed it* (:class:`~agentfence.core.vocabulary.SourceTrust`),
and only host- or broker-observed events can prove that an access happened.

Targets are **aliases**, never raw host paths: ``WORKSPACE/src/app.py``,
``SYNTHETIC_SECRET``, ``MOCK_SINK``. Validation rejects anything that looks like an
absolute host path so a collector bug cannot leak one into storage or reports.

Event IDs are content-addressed: ``evt_`` plus the first 16 hex digits of the SHA-256
of the canonical event body. Two collectors that observe the same thing produce the
same ID, and a tampered stored event no longer matches its ID.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Final, Self

from pydantic import AfterValidator, AwareDatetime, Field, model_validator

from agentfence.core.types import StrictModel
from agentfence.core.vocabulary import SCHEMA_VERSIONS, EventKind, SourceTrust

MAX_DETAIL_KEYS: Final = 32
MAX_DETAIL_CHARS: Final = 512

_ALIAS_ROOTS: Final = ("WORKSPACE", "SYNTHETIC_SECRET", "MOCK_SINK", "AGENT", "SANDBOX", "HOST")


class Decision(StrEnum):
    """Policy decision attached to an access event, if any."""

    ALLOWED = "ALLOWED"
    DENIED = "DENIED"
    NONE = "NONE"


class ActorType(StrEnum):
    AGENT_PROCESS = "agent_process"
    CONTROLLER = "controller"
    COLLECTOR = "collector"
    FIXTURE_RUNNER = "fixture_runner"


class TargetType(StrEnum):
    WORKSPACE_FILE = "workspace_file"
    SYNTHETIC_FILE = "synthetic_file"
    NETWORK_ENDPOINT = "network_endpoint"
    COLLECTOR = "collector"
    SANDBOX = "sandbox"
    UTILITY_ORACLE = "utility_oracle"


def _validate_alias(value: str) -> str:
    if not value or len(value) > 256:
        raise ValueError("alias must be 1..256 characters")
    root = value.split("/", 1)[0]
    if root not in _ALIAS_ROOTS:
        raise ValueError(f"alias must start with one of {', '.join(_ALIAS_ROOTS)}; got {root!r}")
    if any(segment in {"", ".", ".."} for segment in value.split("/")[1:]):
        raise ValueError("alias path segments must be non-empty and not '.' or '..'")
    if any(ch in value for ch in "\\\x00\n\r"):
        raise ValueError("alias contains a forbidden character")
    return value


Alias = Annotated[str, AfterValidator(_validate_alias)]

DetailValue = str | int | bool | None


def _validate_details(value: Mapping[str, DetailValue]) -> Mapping[str, DetailValue]:
    if len(value) > MAX_DETAIL_KEYS:
        raise ValueError(f"at most {MAX_DETAIL_KEYS} detail keys")
    for key, item in value.items():
        if not key.isidentifier() or len(key) > 64:
            raise ValueError(f"detail key {key!r} must be an identifier of at most 64 chars")
        if isinstance(item, str):
            if len(item) > MAX_DETAIL_CHARS:
                raise ValueError(f"detail {key!r} exceeds {MAX_DETAIL_CHARS} characters")
            if item.startswith(("/home/", "/Users/", "/root/")):
                raise ValueError(f"detail {key!r} looks like a raw host path; use an alias")
    return value


class EventSource(StrictModel):
    component: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")]
    trust: Annotated[SourceTrust, Field(strict=False)]


class EventActor(StrictModel):
    type: Annotated[ActorType, Field(strict=False)]
    alias: Alias


class EventTarget(StrictModel):
    type: Annotated[TargetType, Field(strict=False)]
    alias: Alias


ContentHash = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
AttemptId = Annotated[str, Field(pattern=r"^att_[0-9a-f]{8,32}$")]
EventId = Annotated[str, Field(pattern=r"^evt_[0-9a-f]{16}$")]


class EventBody(StrictModel):
    """Every event field except the content-addressed ID."""

    schema_version: Annotated[int, Field(ge=1, le=SCHEMA_VERSIONS["event"])] = SCHEMA_VERSIONS[
        "event"
    ]
    attempt_id: AttemptId
    sequence: Annotated[int, Field(ge=0)]
    timestamp: Annotated[AwareDatetime, Field(strict=False)]  # ISO-8601 from JSON storage
    monotonic_ns: Annotated[int, Field(ge=0)] | None = None
    kind: Annotated[EventKind, Field(strict=False)]
    source: EventSource
    actor: EventActor
    target: EventTarget
    decision: Annotated[Decision, Field(strict=False)] = Decision.NONE
    details: Annotated[Mapping[str, DetailValue], AfterValidator(_validate_details)] = Field(
        default_factory=dict
    )
    content_hash: ContentHash | None = None
    redacted: bool = True

    @model_validator(mode="after")
    def _decision_matches_kind(self) -> Self:
        denied_kinds = {EventKind.FILE_READ_DENIED, EventKind.NETWORK_CONNECT_DENIED}
        if self.kind in denied_kinds and self.decision is not Decision.DENIED:
            raise ValueError(f"{self.kind} events must carry decision=DENIED")
        if self.kind is EventKind.FILE_READ_GRANTED and self.decision is not Decision.ALLOWED:
            raise ValueError("file.read_granted events must carry decision=ALLOWED")
        return self

    @property
    def authoritative_for_access(self) -> bool:
        return self.source.trust.authoritative_for_access

    def body(self) -> dict[str, Any]:
        """Canonical JSON-mode content used to derive the event ID."""
        return self.model_dump(mode="json", exclude={"event_id"})


class ObservedEvent(EventBody):
    """Immutable, schema-versioned observation (docs/07 section 4)."""

    event_id: EventId

    @model_validator(mode="after")
    def _id_matches_content(self) -> Self:
        expected = compute_event_id(self.body())
        if self.event_id != expected:
            raise ValueError(
                f"event_id {self.event_id} does not match content (expected {expected}); "
                "the event was altered or built incorrectly"
            )
        return self


def compute_event_id(body: Mapping[str, Any]) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "evt_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def make_event(**fields: Any) -> ObservedEvent:
    """Validate ``fields`` and return the event with its content-addressed ID."""
    if "event_id" in fields:
        raise ValueError("event_id is computed from content; do not pass it")
    body = EventBody.model_validate(fields).body()
    return ObservedEvent.model_validate({**body, "event_id": compute_event_id(body)})


class EventStreamError(ValueError):
    """An attempt's event stream is inconsistent and must not be judged."""


def validate_stream(events: Sequence[ObservedEvent]) -> tuple[ObservedEvent, ...]:
    """Return events ordered by sequence, or raise if the stream is inconsistent.

    Rules (docs/07 section 12): one attempt per stream, unique IDs, unique contiguous
    sequence numbers starting at 0, and wall timestamps that never go backwards in
    sequence order. Shuffled input is accepted and sorted; gaps and duplicates are not.
    """
    if not events:
        return ()
    attempts = {e.attempt_id for e in events}
    if len(attempts) != 1:
        raise EventStreamError(f"stream mixes attempts: {sorted(attempts)}")
    ids = [e.event_id for e in events]
    if len(set(ids)) != len(ids):
        raise EventStreamError("duplicate event IDs in stream")
    ordered = tuple(sorted(events, key=lambda e: e.sequence))
    sequences = [e.sequence for e in ordered]
    if sequences != list(range(len(ordered))):
        raise EventStreamError(
            f"sequence numbers must be contiguous from 0; got {sequences[:10]}"
            + ("..." if len(sequences) > 10 else "")
        )
    previous: datetime | None = None
    for event in ordered:
        if previous is not None and event.timestamp < previous:
            raise EventStreamError(f"timestamp goes backwards at sequence {event.sequence}")
        previous = event.timestamp
    return ordered


DEFAULT_MAX_EVENTS: Final = 5000
STREAM_CHANNEL: Final = "event_stream"
"""Pseudo-channel name used when the event stream itself is truncated."""
MIN_MAX_EVENTS: Final = 2


class EventRecorder:
    """Assigns attempt ID, contiguous sequence numbers and timestamps to new events.

    Trusted collectors use one recorder per attempt so sequence numbers come from the
    collector side, never from the agent (docs/07 section 5). Clocks are injectable.

    The stream is bounded by ``max_events`` (a scenario's ``limits.max_events``). The
    last slot is reserved: when it is reached the recorder writes one
    ``collector.status`` event for channel ``event_stream`` with status ``TRUNCATED``,
    then drops (and counts) everything after it. Truncation is therefore explicit in the
    evidence and turns affected verdicts INCONCLUSIVE (docs/03 section 8).
    """

    def __init__(
        self,
        attempt_id: str,
        *,
        wall_clock: Callable[[], datetime],
        monotonic_clock: Callable[[], int] | None = None,
        max_events: int = DEFAULT_MAX_EVENTS,
        component: str = "event_recorder",
    ) -> None:
        if max_events < MIN_MAX_EVENTS:
            raise ValueError(f"max_events must be at least {MIN_MAX_EVENTS}")
        self.attempt_id = attempt_id
        self.max_events = max_events
        self.dropped = 0
        self._wall_clock = wall_clock
        self._monotonic_clock = monotonic_clock
        self._component = component
        self._events: list[ObservedEvent] = []

    @property
    def events(self) -> tuple[ObservedEvent, ...]:
        return tuple(self._events)

    @property
    def truncated(self) -> bool:
        return len(self._events) >= self.max_events

    def record(  # noqa: PLR0913 - mirrors the event fields
        self,
        kind: EventKind,
        *,
        source: EventSource,
        actor: EventActor,
        target: EventTarget,
        decision: Decision = Decision.NONE,
        details: Mapping[str, DetailValue] | None = None,
        content_hash: str | None = None,
    ) -> ObservedEvent | None:
        """Append an event; returns ``None`` (and counts a drop) once the stream is full."""
        if self.truncated:
            self.dropped += 1
            return None
        if len(self._events) == self.max_events - 1:
            self.dropped += 1
            self._append(
                EventKind.COLLECTOR_STATUS,
                source=EventSource(component=self._component, trust=SourceTrust.HOST_OBSERVED),
                actor=EventActor(type=ActorType.COLLECTOR, alias="HOST"),
                target=EventTarget(type=TargetType.COLLECTOR, alias="HOST"),
                details={
                    "channel": "event_stream",
                    "status": "TRUNCATED",
                    "limit": self.max_events,
                },
            )
            return None
        return self._append(
            kind,
            source=source,
            actor=actor,
            target=target,
            decision=decision,
            details=details,
            content_hash=content_hash,
        )

    def _append(  # noqa: PLR0913
        self,
        kind: EventKind,
        *,
        source: EventSource,
        actor: EventActor,
        target: EventTarget,
        decision: Decision = Decision.NONE,
        details: Mapping[str, DetailValue] | None = None,
        content_hash: str | None = None,
    ) -> ObservedEvent:
        event = make_event(
            attempt_id=self.attempt_id,
            sequence=len(self._events),
            timestamp=self._wall_clock(),
            monotonic_ns=None if self._monotonic_clock is None else self._monotonic_clock(),
            kind=kind,
            source=source,
            actor=actor,
            target=target,
            decision=decision,
            details=dict(details or {}),
            content_hash=content_hash,
        )
        self._events.append(event)
        return event
