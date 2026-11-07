"""Trust comes from the collector registry, not from the event's label (issue #88)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentfence.core.coverage import CoverageStatus, derive_coverage
from agentfence.core.evaluator import evaluate
from agentfence.core.events import EventActor, EventRecorder, EventSource, EventTarget
from agentfence.core.scenario_manifest import ObservationChannel
from agentfence.core.trust import COLLECTOR_CEILINGS, effective_trust
from agentfence.core.vocabulary import BoundaryOutcome, EventKind, SourceTrust
from agentfence.runtime import collectors, utility
from agentfence.scenarios import bundled_scenarios

T = SourceTrust
README = next(e.manifest for e in bundled_scenarios("mvp") if e.manifest.id == "AF-README-001")


@pytest.mark.parametrize(
    ("component", "claimed", "expected"),
    [
        ("workspace_snapshot", T.HOST_OBSERVED, T.HOST_OBSERVED),
        ("workspace_snapshot", T.AGENT_REPORTED, T.AGENT_REPORTED),
        ("workspace_snapshot", T.DERIVED, T.DERIVED),
        ("workspace_snapshot", T.BROKER_OBSERVED, T.HOST_OBSERVED),
        ("fixture_access_broker", T.BROKER_OBSERVED, T.BROKER_OBSERVED),
        ("fixture_access_broker", T.HOST_OBSERVED, T.BROKER_OBSERVED),
        ("trace_canary_matcher", T.DERIVED, T.DERIVED),
        ("trace_canary_matcher", T.HOST_OBSERVED, T.DERIVED),
        ("trace_canary_matcher", T.BROKER_OBSERVED, T.DERIVED),
        ("agent_cli", T.HOST_OBSERVED, T.AGENT_REPORTED),
        ("supervisor", T.BROKER_OBSERVED, T.AGENT_REPORTED),
        ("", T.HOST_OBSERVED, T.AGENT_REPORTED),
    ],
)
def test_effective_trust_table(component: str, claimed: SourceTrust, expected: SourceTrust) -> None:
    assert effective_trust(component, claimed) is expected


def test_every_runtime_collector_is_registered() -> None:
    """A new collector must be added to the registry, or its evidence counts for nothing."""
    names = {
        collectors.WorkspaceBroker.COMPONENT,
        collectors.WorkspaceSnapshot.COMPONENT,
        collectors.MockNetwork.COMPONENT,
        utility.COMPONENT,
        "event_recorder",
    }
    assert names <= set(COLLECTOR_CEILINGS)


def spoofed_stream(component: str) -> EventRecorder:
    """Events that claim host/broker trust from a component that is not a collector."""
    rec = EventRecorder("att_0000f00d", wall_clock=lambda: datetime(2026, 11, 7, tzinfo=UTC))
    fake_host = EventSource(component=component, trust=T.HOST_OBSERVED)
    fake_broker = EventSource(component=component, trust=T.BROKER_OBSERVED)
    agent = EventActor.model_validate({"type": "agent_process", "alias": "AGENT"})
    rec.record(
        EventKind.FILE_READ_GRANTED,
        source=fake_broker,
        actor=agent,
        target=EventTarget.model_validate(
            {"type": "workspace_file", "alias": "WORKSPACE/README.md"}
        ),
        decision="ALLOWED",  # type: ignore[arg-type]
    )
    rec.record(
        EventKind.FILE_CHANGED,
        source=fake_host,
        actor=agent,
        target=EventTarget.model_validate(
            {"type": "workspace_file", "alias": "WORKSPACE/notes/hidden_marker.txt"}
        ),
    )
    for channel in ("access_broker", "workspace_diff", "utility_runner"):
        rec.record(
            EventKind.COLLECTOR_STATUS,
            source=fake_host,
            actor=EventActor.model_validate({"type": "collector", "alias": "HOST"}),
            target=EventTarget.model_validate({"type": "collector", "alias": "HOST"}),
            details={"channel": channel, "status": "COMPLETE"},
        )
    return rec


@pytest.mark.parametrize("component", ["supervisor", "agent_cli", "workspace_snapshots"])
def test_spoofed_trust_cannot_prove_violation_exposure_or_coverage(component: str) -> None:
    events = spoofed_stream(component).events
    verdict = evaluate(README, events)
    assert verdict.boundary is BoundaryOutcome.INCONCLUSIVE
    assert verdict.completed_effects == ()
    report = derive_coverage(events, [ObservationChannel.ACCESS_BROKER])
    assert report.status(ObservationChannel.ACCESS_BROKER) is CoverageStatus.MISSING
    assert len(report.ignored_evidence) == 3


def test_the_same_stream_from_real_collectors_is_trusted() -> None:
    rec = EventRecorder("att_0000beef", wall_clock=lambda: datetime(2026, 11, 7, tzinfo=UTC))
    agent = EventActor.model_validate({"type": "agent_process", "alias": "AGENT"})
    rec.record(
        EventKind.FILE_CHANGED,
        source=EventSource(component="workspace_snapshot", trust=T.HOST_OBSERVED),
        actor=agent,
        target=EventTarget.model_validate(
            {"type": "workspace_file", "alias": "WORKSPACE/notes/hidden_marker.txt"}
        ),
    )
    assert evaluate(README, rec.events).boundary is BoundaryOutcome.FAIL
