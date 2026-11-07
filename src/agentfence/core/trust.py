# SPDX-License-Identifier: Apache-2.0
"""Collector registry: which component may claim which trust level (issue #88).

``source.trust`` on an event is only a claim made by whoever built the event. The
evaluator must not believe a claim the component is not entitled to, so trust is
resolved against this registry:

* an event from an **unregistered** component is treated as ``agent_reported``;
* an event claiming **more** trust than its component's ceiling is lowered to the
  ceiling (and, if the ceiling is not authoritative, it can no longer prove an access);
* claiming less than the ceiling is always allowed.

The registry is code, reviewed like code. Adding a collector means adding it here.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final

from agentfence.core.vocabulary import SourceTrust

COLLECTOR_CEILINGS: Final = MappingProxyType(
    {
        "fixture_access_broker": SourceTrust.BROKER_OBSERVED,
        "workspace_snapshot": SourceTrust.HOST_OBSERVED,
        "mock_network": SourceTrust.HOST_OBSERVED,
        "utility_runner": SourceTrust.HOST_OBSERVED,
        "event_recorder": SourceTrust.HOST_OBSERVED,
        "trace_canary_matcher": SourceTrust.DERIVED,
    }
)

_RANK: Final = MappingProxyType(
    {
        SourceTrust.AGENT_REPORTED: 0,
        SourceTrust.DERIVED: 1,
        SourceTrust.SANDBOX_REPORTED: 2,
        SourceTrust.HOST_OBSERVED: 3,
        SourceTrust.BROKER_OBSERVED: 3,
    }
)


def effective_trust(component: str, claimed: SourceTrust) -> SourceTrust:
    """The trust the evaluator may rely on for an event from ``component``."""
    ceiling = COLLECTOR_CEILINGS.get(component)
    if ceiling is None:
        return SourceTrust.AGENT_REPORTED
    if claimed is ceiling or _RANK[claimed] < _RANK[ceiling]:
        return claimed
    return ceiling
