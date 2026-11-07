"""Utility runner: score the legitimate task independently of the boundary (AF-0405).

For every assertion in ``scenario.legitimate_task`` the runner looks up the oracle
registered for ``(fixture.generator, assertion.fixture_id)``, lets it inspect the
workspace through a no-follow, non-logging reader, and records a host-observed
``test.utility_result`` event. It then reports the ``utility_runner`` channel:

* ``COMPLETE`` when every assertion was graded;
* ``ERROR`` when an assertion has no registered oracle or the oracle raised, which
  leaves utility ``UNKNOWN`` and makes the boundary verdict ``INCONCLUSIVE`` if the
  scenario requires the channel.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from agentfence.core.events import (
    ActorType,
    EventActor,
    EventRecorder,
    EventSource,
    EventTarget,
    TargetType,
)
from agentfence.core.fixtures import Fixture
from agentfence.core.oracles import Reader, get_oracle
from agentfence.core.scenario_manifest import ObservationChannel, ScenarioManifest
from agentfence.core.vocabulary import EventKind, SourceTrust
from agentfence.runtime.collectors import BrokerError, read_workspace_file

COMPONENT: Final = "utility_runner"
_SOURCE: Final = EventSource(component=COMPONENT, trust=SourceTrust.HOST_OBSERVED)
_ACTOR: Final = EventActor(type=ActorType.FIXTURE_RUNNER, alias="HOST")
_TARGET: Final = EventTarget(type=TargetType.UTILITY_ORACLE, alias="HOST")


def _reader(root: Path) -> Reader:
    def read(path: str) -> bytes | None:
        try:
            return read_workspace_file(root, path)
        except BrokerError:
            return None

    return read


def run_utility(
    root: Path, scenario: ScenarioManifest, fixture: Fixture, recorder: EventRecorder
) -> dict[str, bool]:
    """Grade every legitimate-task assertion. Returns ``fixture_id -> passed``."""
    read = _reader(root)
    results: dict[str, bool] = {}
    status = "COMPLETE"
    for assertion in scenario.legitimate_task.assertions:
        oracle = get_oracle(fixture.generator, assertion.fixture_id)
        if oracle is None:
            status = "ERROR"
            continue
        try:
            outcome = oracle(read, fixture)
        except Exception:  # noqa: BLE001 - an oracle bug must not become a verdict
            status = "ERROR"
            continue
        results[assertion.fixture_id] = outcome.passed
        recorder.record(
            EventKind.TEST_UTILITY_RESULT,
            source=_SOURCE,
            actor=_ACTOR,
            target=_TARGET,
            details={
                "assertion": assertion.fixture_id,
                "result": "pass" if outcome.passed else "fail",
                "reason": outcome.reason,
            },
        )
    recorder.record(
        EventKind.COLLECTOR_STATUS,
        source=_SOURCE,
        actor=EventActor(type=ActorType.COLLECTOR, alias="HOST"),
        target=EventTarget(type=TargetType.COLLECTOR, alias="HOST"),
        details={"channel": ObservationChannel.UTILITY_RUNNER.value, "status": status},
    )
    return results
