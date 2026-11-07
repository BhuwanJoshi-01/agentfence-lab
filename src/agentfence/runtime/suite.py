# SPDX-License-Identifier: Apache-2.0
"""Run a set of scenarios as one suite (docs/02 section 5 controller loop).

Scenarios run in ID order, each ``repeat`` times, every attempt in its own fresh
workspace. A suite has a UUID run ID, start and end times, and the inputs needed to
reproduce it (scenario versions, mock profile, seed, tool version).
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agentfence import __version__
from agentfence.adapters.mock import MockProfile
from agentfence.core.scenario_manifest import ScenarioManifest
from agentfence.runtime.attempt import ISOLATION_TIER, AttemptResult, run_attempt


@dataclass(frozen=True, slots=True)
class SuiteRunResult:
    run_id: str
    started_at: datetime
    ended_at: datetime
    scenario_profile: str
    agent: str
    agent_profile: MockProfile
    seed: int
    repeat: int
    tool_version: str
    isolation_tier: str
    attempts: tuple[AttemptResult, ...]


def run_suite(  # noqa: PLR0913 - every input is part of reproducibility metadata
    scenarios: Sequence[ScenarioManifest],
    profile: MockProfile,
    *,
    scenario_profile: str,
    seed: int,
    workdir: Path,
    repeat: int = 1,
    clock: Callable[[], datetime] | None = None,
    run_id: str | None = None,
) -> SuiteRunResult:
    if repeat < 1:
        raise ValueError("repeat must be at least 1")
    if not scenarios:
        raise ValueError("no scenarios selected")
    ids = [s.id for s in scenarios]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate scenario IDs in suite")
    clock = clock or (lambda: datetime.now(UTC))
    run_id = run_id or str(uuid.uuid4())
    if str(uuid.UUID(run_id)) != run_id:
        raise ValueError("run_id must be a canonical UUID")
    started = clock()
    attempts = tuple(
        run_attempt(
            scenario,
            profile,
            run_id=run_id,
            seed=seed,
            workdir=workdir / run_id,
            repetition=rep,
            clock=clock,
        )
        for scenario in sorted(scenarios, key=lambda s: s.id)
        for rep in range(repeat)
    )
    with contextlib.suppress(OSError):
        (workdir / run_id).rmdir()  # attempts already deleted their workspaces
    return SuiteRunResult(
        run_id=run_id,
        started_at=started,
        ended_at=clock(),
        scenario_profile=scenario_profile,
        agent="mock",
        agent_profile=profile,
        seed=seed,
        repeat=repeat,
        tool_version=__version__,
        isolation_tier=ISOLATION_TIER,
        attempts=attempts,
    )
