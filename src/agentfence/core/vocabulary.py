"""Immutable outcome vocabulary and schema versions (AF-0006).

These strings appear in stored events, JSON reports, exit codes and the
dashboard. Changing a member's value is a breaking schema change: bump the
relevant entry in :data:`SCHEMA_VERSIONS` and record why in the changelog.

Sources: docs/01 section 9, docs/04 section 8.1, docs/06 section 3,
docs/07 section 2, docs/03 section 5.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum
from types import MappingProxyType
from typing import Final

#: Independent schema versions per serialized artifact. A scenario manifest's
#: ``schema_version`` is distinct from the scenario's own semantic ``version``.
SCHEMA_VERSIONS: Final = MappingProxyType(
    {
        "config": 1,
        "scenario": 1,
        "event": 1,
        "report": 1,
        "attestation": 1,
    }
)


class BoundaryOutcome(StrEnum):
    """Did the declared trust boundary hold?"""

    PASS = "PASS"
    """Boundary held after *proven* exposure, with full required observation coverage."""
    FAIL = "FAIL"
    """A trusted source observed a prohibited attempt or effect."""
    INCONCLUSIVE = "INCONCLUSIVE"
    """Required telemetry missing, infrastructure failed, or evidence ambiguous. Never a pass."""
    NOT_TRIGGERED = "NOT_TRIGGERED"
    """The attack surface was never demonstrably encountered. Not a pass."""


class UtilityOutcome(StrEnum):
    """Did the legitimate task succeed? Recorded independently of the boundary (ADR-002)."""

    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    UNKNOWN = "UNKNOWN"


class InfrastructureOutcome(StrEnum):
    """Did the harness itself work?"""

    OK = "OK"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"
    UNSUPPORTED = "UNSUPPORTED"


class Variant(StrEnum):
    """Every adversarial scenario has a matched benign control (docs/04 section 9)."""

    BENIGN = "benign"
    ADVERSARIAL = "adversarial"


class SourceTrust(StrEnum):
    """Who produced an observation (docs/07 section 2)."""

    HOST_OBSERVED = "host_observed"
    BROKER_OBSERVED = "broker_observed"
    SANDBOX_REPORTED = "sandbox_reported"
    AGENT_REPORTED = "agent_reported"
    DERIVED = "derived"

    @property
    def authoritative_for_access(self) -> bool:
        """Only host- or broker-observed events may prove that an access happened."""
        return self in {SourceTrust.HOST_OBSERVED, SourceTrust.BROKER_OBSERVED}


class EventKind(StrEnum):
    """Event taxonomy (docs/07 section 2)."""

    AGENT_STARTED = "agent.started"
    AGENT_TOOL_REPORTED = "agent.tool_reported"
    FILE_READ_REQUESTED = "file.read_requested"
    FILE_READ_GRANTED = "file.read_granted"
    FILE_READ_DENIED = "file.read_denied"
    FILE_CHANGED = "file.changed"
    NETWORK_CONNECT_REQUESTED = "network.connect_requested"
    NETWORK_CONNECT_DENIED = "network.connect_denied"
    NETWORK_MOCK_RECEIVED = "network.mock_received"
    TEST_UTILITY_RESULT = "test.utility_result"
    COLLECTOR_STATUS = "collector.status"
    SANDBOX_STARTED = "sandbox.started"
    SANDBOX_STOPPED = "sandbox.stopped"
    POLICY_VIOLATION = "policy.violation"


class IsolationState(StrEnum):
    """Effective isolation shown to the user *before* a run (docs/03 section 10)."""

    OFFLINE_SAFE = "OFFLINE_SAFE"
    CONTROLLED_EGRESS = "CONTROLLED_EGRESS"
    UNSUPPORTED = "UNSUPPORTED"


class CheckStatus(StrEnum):
    """Result of one preflight/attestation check (docs/03 section 5)."""

    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class ExitCode(IntEnum):
    """Process exit codes (docs/06 section 3)."""

    SUCCESS = 0
    BOUNDARY_FAIL = 1
    USAGE_ERROR = 2
    PREFLIGHT_FAILURE = 3
    INFRA_FAILURE = 4
    STRICT_INCONCLUSIVE = 5
    UNSUPPORTED = 6


# Highest precedence first. USAGE_ERROR and UNSUPPORTED are raised before a run
# starts, so they never compete with run outcomes.
_RUN_EXIT_PRECEDENCE: Final = (
    ExitCode.PREFLIGHT_FAILURE,
    ExitCode.INFRA_FAILURE,
    ExitCode.BOUNDARY_FAIL,
    ExitCode.STRICT_INCONCLUSIVE,
    ExitCode.SUCCESS,
)


def run_exit_code(
    *,
    preflight_failed: bool,
    infra_failed: bool,
    boundaries: list[BoundaryOutcome] | tuple[BoundaryOutcome, ...],
    strict: bool,
) -> ExitCode:
    """Combine run results into one exit code using the documented precedence.

    ``security preflight (3) > infrastructure (4) > FAIL (1) > strict-inconclusive (5) > 0``.
    ``--strict`` changes the exit code for INCONCLUSIVE but never changes a verdict.
    """
    candidates: set[ExitCode] = {ExitCode.SUCCESS}
    if preflight_failed:
        candidates.add(ExitCode.PREFLIGHT_FAILURE)
    if infra_failed:
        candidates.add(ExitCode.INFRA_FAILURE)
    if BoundaryOutcome.FAIL in boundaries:
        candidates.add(ExitCode.BOUNDARY_FAIL)
    if strict and BoundaryOutcome.INCONCLUSIVE in boundaries:
        candidates.add(ExitCode.STRICT_INCONCLUSIVE)
    return next(code for code in _RUN_EXIT_PRECEDENCE if code in candidates)
