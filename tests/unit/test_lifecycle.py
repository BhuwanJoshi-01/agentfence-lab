# SPDX-License-Identifier: Apache-2.0
"""Run lifecycle FSM tests (docs/02 section 5)."""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import count

import pytest

from agentfence.core.lifecycle import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    InvalidTransitionError,
    RunLifecycle,
    RunStatus,
)

HAPPY_PATH = [
    RunStatus.PREFLIGHTING,
    RunStatus.MATERIALIZING,
    RunStatus.RUNNING,
    RunStatus.COLLECTING,
    RunStatus.EVALUATING,
    RunStatus.COMPLETED,
]


def make() -> RunLifecycle:
    ticks = count(1000, 10)
    return RunLifecycle(
        wall_clock=lambda: datetime(2026, 11, 6, tzinfo=UTC),
        monotonic_clock=lambda: next(ticks),
    )


def test_happy_path_records_every_transition() -> None:
    run = make()
    for state in HAPPY_PATH:
        run.advance(state, f"enter {state}")
    assert run.status is RunStatus.COMPLETED
    assert [t.target for t in run.history] == HAPPY_PATH
    assert [t.monotonic_ns for t in run.history] == [1000, 1010, 1020, 1030, 1040, 1050]
    assert all(t.reason for t in run.history)


def test_cannot_skip_evaluation_to_completed() -> None:
    run = make()
    for state in HAPPY_PATH[:3]:
        run.advance(state, "ok")
    with pytest.raises(InvalidTransitionError, match="RUNNING to COMPLETED"):
        run.advance(RunStatus.COMPLETED, "process exited 0")
    assert run.status is RunStatus.RUNNING


def test_backward_transition_rejected() -> None:
    run = make()
    run.advance(RunStatus.PREFLIGHTING, "start")
    run.advance(RunStatus.MATERIALIZING, "preflight ok")
    with pytest.raises(InvalidTransitionError):
        run.advance(RunStatus.PREFLIGHTING, "again")


@pytest.mark.parametrize("terminal", sorted(TERMINAL_STATES))
def test_terminal_states_have_no_exits(terminal: RunStatus) -> None:
    assert terminal.terminal
    assert ALLOWED_TRANSITIONS[terminal] == frozenset()


def test_empty_reason_rejected() -> None:
    run = make()
    with pytest.raises(InvalidTransitionError, match="reason"):
        run.advance(RunStatus.PREFLIGHTING, "   ")
    assert run.history == []


def test_timeout_only_from_running_or_collecting() -> None:
    sources = {s for s, targets in ALLOWED_TRANSITIONS.items() if RunStatus.TIMED_OUT in targets}
    assert sources == {RunStatus.RUNNING, RunStatus.COLLECTING}


def test_cancel_and_abort_reachable_from_every_non_terminal_state() -> None:
    for state, targets in ALLOWED_TRANSITIONS.items():
        if state.terminal:
            continue
        assert {RunStatus.CANCELLED, RunStatus.ABORTED} <= targets


def test_preflight_rejection_is_terminal() -> None:
    run = make()
    run.advance(RunStatus.PREFLIGHTING, "start")
    run.advance(RunStatus.REJECTED, "rootless runtime not verified")
    with pytest.raises(InvalidTransitionError, match="terminal"):
        run.advance(RunStatus.MATERIALIZING, "try anyway")


def test_every_state_has_a_transition_entry() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(RunStatus)


def test_default_clocks_are_utc_and_monotonic() -> None:
    run = RunLifecycle()
    first = run.advance(RunStatus.PREFLIGHTING, "start")
    second = run.advance(RunStatus.MATERIALIZING, "preflight ok")
    assert first.wall_time.tzinfo is UTC
    assert second.monotonic_ns >= first.monotonic_ns
