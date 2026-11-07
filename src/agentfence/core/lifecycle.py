# SPDX-License-Identifier: Apache-2.0
"""Run lifecycle finite state machine (docs/02 section 5).

``CREATED -> PREFLIGHTING -> MATERIALIZING -> RUNNING -> COLLECTING -> EVALUATING -> COMPLETED``

Exceptional terminal states: ``REJECTED``, ``ABORTED``, ``TIMED_OUT``, ``INFRA_ERROR``,
``CANCELLED``. Backward transitions and transitions out of a terminal state are rejected.
Every transition records wall time, monotonic time and a non-empty auditable reason.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final


class RunStatus(StrEnum):
    CREATED = "CREATED"
    PREFLIGHTING = "PREFLIGHTING"
    MATERIALIZING = "MATERIALIZING"
    RUNNING = "RUNNING"
    COLLECTING = "COLLECTING"
    EVALUATING = "EVALUATING"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    ABORTED = "ABORTED"
    TIMED_OUT = "TIMED_OUT"
    INFRA_ERROR = "INFRA_ERROR"
    CANCELLED = "CANCELLED"

    @property
    def terminal(self) -> bool:
        return self in TERMINAL_STATES


TERMINAL_STATES: Final = frozenset(
    {
        RunStatus.COMPLETED,
        RunStatus.REJECTED,
        RunStatus.ABORTED,
        RunStatus.TIMED_OUT,
        RunStatus.INFRA_ERROR,
        RunStatus.CANCELLED,
    }
)

_ALWAYS_EXITABLE: Final = frozenset({RunStatus.ABORTED, RunStatus.CANCELLED})

ALLOWED_TRANSITIONS: Final = MappingProxyType(
    {
        RunStatus.CREATED: frozenset({RunStatus.PREFLIGHTING, RunStatus.REJECTED})
        | _ALWAYS_EXITABLE,
        RunStatus.PREFLIGHTING: frozenset(
            {RunStatus.MATERIALIZING, RunStatus.REJECTED, RunStatus.INFRA_ERROR}
        )
        | _ALWAYS_EXITABLE,
        RunStatus.MATERIALIZING: frozenset({RunStatus.RUNNING, RunStatus.INFRA_ERROR})
        | _ALWAYS_EXITABLE,
        RunStatus.RUNNING: frozenset(
            {RunStatus.COLLECTING, RunStatus.TIMED_OUT, RunStatus.INFRA_ERROR}
        )
        | _ALWAYS_EXITABLE,
        RunStatus.COLLECTING: frozenset(
            {RunStatus.EVALUATING, RunStatus.TIMED_OUT, RunStatus.INFRA_ERROR}
        )
        | _ALWAYS_EXITABLE,
        RunStatus.EVALUATING: frozenset({RunStatus.COMPLETED, RunStatus.INFRA_ERROR})
        | _ALWAYS_EXITABLE,
        **{state: frozenset() for state in TERMINAL_STATES},
    }
)


class InvalidTransitionError(ValueError):
    """Raised when a lifecycle transition is not permitted."""


@dataclass(frozen=True, slots=True)
class Transition:
    source: RunStatus
    target: RunStatus
    reason: str
    wall_time: datetime
    monotonic_ns: int


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class RunLifecycle:
    """Mutable holder of the current status plus its append-only transition history."""

    status: RunStatus = RunStatus.CREATED
    history: list[Transition] = field(default_factory=list)
    wall_clock: Callable[[], datetime] = field(default=_utc_now, repr=False)
    monotonic_clock: Callable[[], int] = field(default=time.monotonic_ns, repr=False)

    def advance(self, target: RunStatus, reason: str) -> Transition:
        if not reason.strip():
            raise InvalidTransitionError("every lifecycle transition needs an auditable reason")
        if target not in ALLOWED_TRANSITIONS[self.status]:
            raise InvalidTransitionError(
                f"cannot move run from {self.status} to {target}; "
                f"allowed: {sorted(ALLOWED_TRANSITIONS[self.status]) or 'none (terminal)'}"
            )
        transition = Transition(
            source=self.status,
            target=target,
            reason=reason,
            wall_time=self.wall_clock(),
            monotonic_ns=self.monotonic_clock(),
        )
        self.history.append(transition)
        self.status = target
        return transition
