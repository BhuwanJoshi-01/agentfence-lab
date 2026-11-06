"""Collector coverage: which observation channels actually watched an attempt (AF-0402).

A dropped log source is not proof that nothing happened (docs/02 section 8). Every
trusted collector reports its own status with a ``collector.status`` event::

    details = {"channel": "workspace_diff", "status": "COMPLETE"}

:func:`derive_coverage` turns those events into a :class:`CoverageReport`. Rules:

* A channel with no status event is ``MISSING``.
* Only ``host_observed`` / ``broker_observed`` status events count; an agent cannot
  vouch for the collectors that watch it.
* If a channel reports several statuses the worst wins: ``ERROR`` > ``TRUNCATED`` >
  ``COMPLETE``.
* If the event stream itself was truncated (``channel="event_stream"``), every channel
  is treated as ``TRUNCATED``: evidence may have been dropped from any of them.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from agentfence.core.events import STREAM_CHANNEL, ObservedEvent
from agentfence.core.scenario_manifest import ObservationChannel
from agentfence.core.vocabulary import EventKind

__all__ = [
    "STREAM_CHANNEL",
    "ChannelCoverage",
    "CoverageReport",
    "CoverageStatus",
    "derive_coverage",
]


class CoverageStatus(StrEnum):
    COMPLETE = "COMPLETE"
    TRUNCATED = "TRUNCATED"
    ERROR = "ERROR"
    MISSING = "MISSING"


_SEVERITY: Final = MappingProxyType(
    {
        CoverageStatus.COMPLETE: 0,
        CoverageStatus.TRUNCATED: 1,
        CoverageStatus.ERROR: 2,
        CoverageStatus.MISSING: 3,
    }
)
_REPORTABLE: Final = frozenset(
    {CoverageStatus.COMPLETE, CoverageStatus.TRUNCATED, CoverageStatus.ERROR}
)


@dataclass(frozen=True, slots=True)
class ChannelCoverage:
    channel: ObservationChannel
    status: CoverageStatus
    evidence: tuple[str, ...] = ()
    """IDs of the ``collector.status`` events that established this status."""


@dataclass(frozen=True, slots=True)
class CoverageReport:
    channels: Mapping[ObservationChannel, ChannelCoverage]
    stream_truncated: bool = False
    ignored_evidence: tuple[str, ...] = field(default=())
    """Status events that were not trusted or named an unknown channel/status."""

    def status(self, channel: ObservationChannel) -> CoverageStatus:
        entry = self.channels.get(channel)
        return CoverageStatus.MISSING if entry is None else entry.status

    def gaps(self, required: Iterable[ObservationChannel]) -> tuple[ObservationChannel, ...]:
        """Required channels that are not ``COMPLETE``, in a stable order."""
        return tuple(
            sorted(
                (c for c in set(required) if self.status(c) is not CoverageStatus.COMPLETE),
                key=lambda c: c.value,
            )
        )

    def complete(self, required: Iterable[ObservationChannel]) -> bool:
        return not self.gaps(required)


def _worse(a: CoverageStatus, b: CoverageStatus) -> CoverageStatus:
    return a if _SEVERITY[a] >= _SEVERITY[b] else b


def derive_coverage(
    events: Sequence[ObservedEvent], expected: Iterable[ObservationChannel]
) -> CoverageReport:
    """Build a coverage report for ``expected`` channels from ``collector.status`` events."""
    expected_set = frozenset(expected)
    statuses: dict[ObservationChannel, CoverageStatus] = {}
    evidence: dict[ObservationChannel, list[str]] = {}
    ignored: list[str] = []
    stream_truncated = False

    for event in events:
        if event.kind is not EventKind.COLLECTOR_STATUS:
            continue
        raw_channel = event.details.get("channel")
        raw_status = event.details.get("status")
        if not event.authoritative_for_access or not isinstance(raw_status, str):
            ignored.append(event.event_id)
            continue
        try:
            status = CoverageStatus(raw_status)
        except ValueError:
            ignored.append(event.event_id)
            continue
        if status not in _REPORTABLE:
            ignored.append(event.event_id)
            continue
        if raw_channel == STREAM_CHANNEL:
            stream_truncated = stream_truncated or status is not CoverageStatus.COMPLETE
            continue
        try:
            channel = ObservationChannel(str(raw_channel))
        except ValueError:
            ignored.append(event.event_id)
            continue
        statuses[channel] = _worse(statuses.get(channel, CoverageStatus.COMPLETE), status)
        evidence.setdefault(channel, []).append(event.event_id)

    report: dict[ObservationChannel, ChannelCoverage] = {}
    for channel in sorted(expected_set | set(statuses), key=lambda c: c.value):
        status = statuses.get(channel, CoverageStatus.MISSING)
        if stream_truncated:
            status = _worse(status, CoverageStatus.TRUNCATED)
        report[channel] = ChannelCoverage(channel, status, tuple(evidence.get(channel, ())))
    return CoverageReport(
        channels=MappingProxyType(report),
        stream_truncated=stream_truncated,
        ignored_evidence=tuple(ignored),
    )
