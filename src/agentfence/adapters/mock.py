"""Deterministic offline mock agent (AF-0308; docs/05 section 3, ADR-001).

The mock tests the pipeline, not model behavior. It needs no API key, no network and
no model. Given a scenario, its fixture and a profile it plans a fixed list of actions;
the runner executes them **only** through the trusted collectors (broker, mock
network), so every observation is produced by the same plumbing later adapters use.

Profiles:

* ``safe``: reads the attack surface, completes the task, ignores the trap.
* ``unsafe``: like ``safe`` plus one forbidden synthetic action per failure rule.
* ``refusal``: reads the attack surface and then does nothing (over-refusal).
* ``no-exposure``: completes the task without touching the attack surface.
* ``no-telemetry``: like ``safe``, but the runner simulates a collector failure.
* ``timeout``: reads the surface and then stalls; the runner reports TIMEOUT. The
  timeout is simulated deterministically, never by sleeping.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Literal

from agentfence import __version__
from agentfence.core.capabilities import AdapterCapabilities
from agentfence.core.fixtures import Fixture
from agentfence.core.oracles import Reader, get_solution
from agentfence.core.scenario_manifest import FailureRuleKind, ScenarioManifest

NAME: Final = "mock"


class MockProfile(StrEnum):
    SAFE = "safe"
    UNSAFE = "unsafe"
    REFUSAL = "refusal"
    NO_EXPOSURE = "no-exposure"
    NO_TELEMETRY = "no-telemetry"
    TIMEOUT = "timeout"


@dataclass(frozen=True, slots=True)
class Action:
    kind: Literal["read", "write", "connect"]
    path: str | None = None
    data: bytes | None = None
    endpoint: str | None = None


@dataclass(frozen=True, slots=True)
class Plan:
    actions: tuple[Action, ...]
    stalls: bool = False
    """True: the agent never finishes; the runner records a deterministic TIMEOUT."""
    drops_collector: bool = False
    """True: the runner simulates a failed access-broker collector."""


def capabilities() -> AdapterCapabilities:
    return AdapterCapabilities(
        name=NAME,
        binary_version=__version__,
        noninteractive=True,
        structured_events=True,
        supports_repo_read=True,
        supports_file_write=True,
        supports_tool_execution=False,
        supports_model_proxy=False,
        supports_isolated_config_root=True,
        requires_network=False,
        metadata={"kind": "deterministic-offline-mock"},
    )


def _trap_actions(scenario: ScenarioManifest, fixture: Fixture, read: Reader) -> list[Action]:
    actions: list[Action] = []
    protected = [f.path for f in fixture.files if f.protected]
    for rule in scenario.verdict.failure_if:
        match rule.kind:
            case FailureRuleKind.FORBIDDEN_PATH_MODIFIED if rule.path:
                value = fixture.markers.get("forbidden_marker_value", "modified")
                actions.append(Action("write", path=rule.path, data=f"{value}\n".encode()))
            case FailureRuleKind.PROTECTED_READ_GRANTED | FailureRuleKind.PROTECTED_READ_ATTEMPTED:
                actions.extend(Action("read", path=p) for p in protected[:1])
            case FailureRuleKind.UNAUTHORIZED_CONNECT_ATTEMPTED:
                actions.append(Action("connect", endpoint="MOCK_SINK"))
            case FailureRuleKind.OUTPUT_MARKER_PRESENT if rule.path:
                marker = fixture.markers.get("output_marker", "AF-MOCK-MARKER")
                current = read(rule.path) or b""
                actions.append(Action("write", path=rule.path, data=current + marker.encode()))
            case _:
                continue
    return actions


def plan(scenario: ScenarioManifest, fixture: Fixture, profile: MockProfile, read: Reader) -> Plan:
    """Decide the mock's actions. ``read`` goes through the broker, so planning reads
    (e.g. looking at the test file before fixing it) are observed like any other read."""
    surface = scenario.attack_surface
    exposure = (
        [Action("read", path=surface.path)]
        if surface is not None and profile is not MockProfile.NO_EXPOSURE
        else []
    )
    if profile is MockProfile.REFUSAL:
        return Plan(tuple(exposure))
    if profile is MockProfile.TIMEOUT:
        return Plan(tuple(exposure), stalls=True)
    work: list[Action] = []
    for assertion in scenario.legitimate_task.assertions:
        solution = get_solution(fixture.generator, assertion.fixture_id)
        if solution is None:
            continue
        for path, data in sorted(solution(read, fixture).items()):
            work.append(Action("write", path=path, data=data))
    trap = _trap_actions(scenario, fixture, read) if profile is MockProfile.UNSAFE else []
    return Plan(
        tuple(exposure + work + trap),
        drops_collector=profile is MockProfile.NO_TELEMETRY,
    )
