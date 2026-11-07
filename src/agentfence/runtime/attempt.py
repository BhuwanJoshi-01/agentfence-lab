"""Run one scenario attempt end to end with the mock adapter (AF-0308; docs/02 section 5).

``CREATED -> PREFLIGHTING -> MATERIALIZING -> RUNNING -> COLLECTING -> EVALUATING ->
COMPLETED`` (or ``RUNNING -> TIMED_OUT``). Steps:

1. generate the seeded fixture and materialize it into a fresh private workspace;
2. attach the trusted collectors (broker, snapshot, mock network) to one recorder;
3. let the mock plan and act **only** through those collectors;
4. finish the collectors, grade utility, and evaluate deterministically;
5. always delete the workspace (``finally``), unless asked to keep it for debugging.

Isolation tier: this runs the *mock* in-process. The mock executes no workspace code,
opens no sockets and touches files only through the broker, but no sandbox is involved,
so results carry ``isolation_tier = "mock-inprocess"`` and make no containment claim
(ADR-011).
"""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path
from typing import Final

import agentfence.scenarios.generators  # noqa: F401 - registers generators, oracles, solutions
from agentfence.adapters import mock
from agentfence.adapters.mock import MockProfile
from agentfence.core.evaluator import Verdict, evaluate
from agentfence.core.events import EventRecorder, ObservedEvent
from agentfence.core.fixtures import Fixture, generate
from agentfence.core.lifecycle import RunLifecycle, RunStatus, Transition
from agentfence.core.scenario_manifest import ScenarioCategory, ScenarioManifest
from agentfence.core.vocabulary import InfrastructureOutcome
from agentfence.runtime.collectors import (
    BrokerError,
    MockNetwork,
    NetworkMode,
    WorkspaceBroker,
    WorkspaceSnapshot,
)
from agentfence.runtime.utility import run_utility
from agentfence.runtime.workspace import materialize
from agentfence.storage.paths import ensure_private_dir

ISOLATION_TIER: Final = "mock-inprocess"


def attempt_id_for(
    run_id: str, scenario: ScenarioManifest, profile: MockProfile, seed: int, rep: int
) -> str:
    """Attempts belong to a suite run (docs/02 section 6), so the run ID is part of the key:
    two runs with identical settings must not produce colliding attempt IDs."""
    key = f"{run_id}|{scenario.id}@{scenario.version}|{profile.value}|{seed}|{rep}"
    return "att_" + hashlib.sha256(key.encode()).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class AttemptResult:
    attempt_id: str
    scenario: ScenarioManifest
    profile: MockProfile
    seed: int
    repetition: int
    fixture_digest: str
    verdict: Verdict
    events: tuple[ObservedEvent, ...]
    lifecycle: tuple[Transition, ...]
    isolation_tier: str = ISOLATION_TIER
    dropped_events: int = 0
    notes: tuple[str, ...] = field(default=())


def stepping_clock(start: datetime, step: timedelta) -> Callable[[], datetime]:
    """Deterministic clock for reproducible event IDs in tests and goldens."""
    ticks = count()
    return lambda: start + step * next(ticks)


def _execute(
    plan: mock.Plan, broker: WorkspaceBroker, network: MockNetwork, notes: list[str]
) -> None:
    """Carry out the mock's actions, only through the trusted collectors."""
    for action in plan.actions:
        try:
            if action.kind == "read" and action.path:
                broker.read(action.path)
            elif action.kind == "write" and action.path and action.data is not None:
                broker.write(action.path, action.data)
            elif action.kind == "connect" and action.endpoint:
                network.connect(action.endpoint)
        except BrokerError as exc:
            notes.append(f"action refused: {exc}")


def _run_agent(  # noqa: PLR0913 - one attempt's moving parts, passed explicitly
    scenario: ScenarioManifest,
    profile: MockProfile,
    fixture: Fixture,
    root: Path,
    *,
    recorder: EventRecorder,
    lifecycle: RunLifecycle,
    notes: list[str],
) -> InfrastructureOutcome:
    """RUNNING through collector shutdown. Returns the infrastructure outcome."""
    protected = [f.path for f in fixture.files if f.protected]
    snapshot = WorkspaceSnapshot(root, recorder)
    broker = WorkspaceBroker(root, recorder, protected=protected)
    network_mode = (
        NetworkMode.LAB_SINK
        if scenario.category is ScenarioCategory.NETWORK_EGRESS
        else NetworkMode.DENY
    )
    network = MockNetwork(recorder, network_mode)
    infra = InfrastructureOutcome.OK
    try:
        lifecycle.advance(RunStatus.RUNNING, f"mock profile {profile.value}")
        plan = mock.plan(scenario, fixture, profile, broker.read)
        _execute(plan, broker, network, notes)
        if plan.stalls:
            infra = InfrastructureOutcome.TIMEOUT
            lifecycle.advance(RunStatus.TIMED_OUT, "mock-timeout: agent did not finish")
        else:
            lifecycle.advance(RunStatus.COLLECTING, "agent finished")
        if plan.drops_collector:
            notes.append("simulated access-broker collector failure")
        else:
            broker.finish()
        network.finish()
        snapshot.finish(protected=protected)
    finally:
        broker.close()
    return infra


def run_attempt(  # noqa: PLR0913 - explicit inputs keep attempts reproducible
    scenario: ScenarioManifest,
    profile: MockProfile,
    *,
    run_id: str,
    seed: int,
    workdir: Path,
    repetition: int = 0,
    clock: Callable[[], datetime] | None = None,
    keep_workspace: bool = False,
) -> AttemptResult:
    clock = clock or (lambda: datetime.now(UTC))
    attempt_id = attempt_id_for(run_id, scenario, profile, seed, repetition)
    lifecycle = RunLifecycle(wall_clock=clock)
    lifecycle.advance(RunStatus.PREFLIGHTING, "mock adapter, in-process, no network")
    ensure_private_dir(workdir)
    root = workdir / attempt_id
    notes: list[str] = []
    fixture = generate(scenario.fixture.generator, scenario.variant, seed)
    recorder = EventRecorder(attempt_id, wall_clock=clock, max_events=scenario.limits.max_events)
    try:
        lifecycle.advance(RunStatus.MATERIALIZING, f"fixture {fixture.generator} seed {seed}")
        materialize(fixture, root)
        infra = _run_agent(
            scenario, profile, fixture, root, recorder=recorder, lifecycle=lifecycle, notes=notes
        )
        if infra is InfrastructureOutcome.OK:
            run_utility(root, scenario, fixture, recorder)
            lifecycle.advance(RunStatus.EVALUATING, "collectors and utility done")
        verdict = evaluate(scenario, recorder.events, infra)
        if lifecycle.status is RunStatus.EVALUATING:
            lifecycle.advance(RunStatus.COMPLETED, f"verdict {verdict.boundary.value}")
    finally:
        if not keep_workspace:
            shutil.rmtree(root, ignore_errors=True)
    return AttemptResult(
        attempt_id=attempt_id,
        scenario=scenario,
        profile=profile,
        seed=seed,
        repetition=repetition,
        fixture_digest=fixture.digest,
        verdict=verdict,
        events=recorder.events,
        lifecycle=tuple(lifecycle.history),
        dropped_events=recorder.dropped,
        notes=tuple(notes),
    )
