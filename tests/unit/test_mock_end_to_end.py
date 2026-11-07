"""End-to-end mock attempts against the bundled scenarios (AF-0308, AF-0408 groundwork).

Every docs/05 mock profile runs the real pipeline: seeded fixture -> private workspace
-> trusted collectors -> utility oracle -> deterministic evaluator.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentfence.adapters import mock
from agentfence.adapters.mock import MockProfile as P
from agentfence.core.lifecycle import RunStatus
from agentfence.core.vocabulary import BoundaryOutcome as B
from agentfence.core.vocabulary import EventKind as K
from agentfence.core.vocabulary import InfrastructureOutcome as I
from agentfence.core.vocabulary import UtilityOutcome as U
from agentfence.runtime.attempt import AttemptResult, run_attempt, stepping_clock
from agentfence.scenarios import bundled_scenarios

SCENARIOS = {e.manifest.id: e.manifest for e in bundled_scenarios("mvp")}
ADV, BEN = "AF-README-001", "AF-README-001-B"
RUN_ID = "7e23c178-70d2-4484-9bb1-89cf95da92b4"

DONE, TIMED = RunStatus.COMPLETED, RunStatus.TIMED_OUT
EXPECTED: dict[tuple[str, P], tuple[B, U, I, str, RunStatus]] = {
    (ADV, P.SAFE): (B.PASS, U.COMPLETE, I.OK, "AF-BOUNDARY-HELD", DONE),
    (ADV, P.UNSAFE): (B.FAIL, U.COMPLETE, I.OK, "AF-READ-001", DONE),
    (ADV, P.REFUSAL): (B.PASS, U.INCOMPLETE, I.OK, "AF-BOUNDARY-HELD", DONE),
    (ADV, P.NO_EXPOSURE): (B.NOT_TRIGGERED, U.COMPLETE, I.OK, "AF-NOT-EXPOSED", DONE),
    (ADV, P.NO_TELEMETRY): (B.INCONCLUSIVE, U.COMPLETE, I.OK, "AF-INSTRUMENTATION-GAP", DONE),
    (ADV, P.TIMEOUT): (B.INCONCLUSIVE, U.UNKNOWN, I.TIMEOUT, "AF-INFRA-TIMEOUT", TIMED),
    (BEN, P.SAFE): (B.NOT_TRIGGERED, U.COMPLETE, I.OK, "AF-BENIGN-CONTROL", DONE),
    (BEN, P.UNSAFE): (B.NOT_TRIGGERED, U.COMPLETE, I.OK, "AF-BENIGN-CONTROL", DONE),
    (BEN, P.REFUSAL): (B.NOT_TRIGGERED, U.INCOMPLETE, I.OK, "AF-BENIGN-CONTROL", DONE),
    (BEN, P.NO_EXPOSURE): (B.NOT_TRIGGERED, U.COMPLETE, I.OK, "AF-BENIGN-CONTROL", DONE),
    (BEN, P.NO_TELEMETRY): (B.INCONCLUSIVE, U.COMPLETE, I.OK, "AF-INSTRUMENTATION-GAP", DONE),
    (BEN, P.TIMEOUT): (B.INCONCLUSIVE, U.UNKNOWN, I.TIMEOUT, "AF-INFRA-TIMEOUT", TIMED),
}


def run(sid: str, profile: P, tmp_path: Path, seed: int = 42, **kw: object) -> AttemptResult:
    clock = stepping_clock(datetime(2026, 11, 7, tzinfo=UTC), timedelta(milliseconds=1))
    kw.setdefault("run_id", RUN_ID)
    return run_attempt(
        SCENARIOS[sid],
        profile,
        seed=seed,
        workdir=tmp_path / "work",
        clock=clock,
        **kw,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(("sid", "profile"), sorted(EXPECTED, key=str))
def test_profile_matrix(sid: str, profile: P, tmp_path: Path) -> None:
    result = run(sid, profile, tmp_path)
    v = result.verdict
    got = (v.boundary, v.utility, v.infrastructure, v.reason_code, result.lifecycle[-1].target)
    assert got == EXPECTED[(sid, profile)]
    assert result.isolation_tier == "mock-inprocess"


def test_every_profile_is_covered() -> None:
    assert {p for _, p in EXPECTED} == set(P)


@pytest.mark.parametrize("seed", [0, 1, 7, 999])
def test_safe_and_unsafe_hold_across_seeds(seed: int, tmp_path: Path) -> None:
    assert run(ADV, P.SAFE, tmp_path, seed).verdict.boundary is B.PASS
    unsafe = run(ADV, P.UNSAFE, tmp_path, seed).verdict
    assert (unsafe.boundary, unsafe.utility) == (B.FAIL, U.COMPLETE)


def test_attempts_are_reproducible(tmp_path: Path) -> None:
    first = run(ADV, P.UNSAFE, tmp_path)
    second = run(ADV, P.UNSAFE, tmp_path)
    assert first.attempt_id == second.attempt_id
    assert [e.event_id for e in first.events] == [e.event_id for e in second.events]
    assert first.verdict == second.verdict
    assert first.fixture_digest == second.fixture_digest


def test_repetitions_get_distinct_attempt_ids(tmp_path: Path) -> None:
    ids = {run(ADV, P.SAFE, tmp_path, repetition=r).attempt_id for r in range(3)}
    assert len(ids) == 3


def test_fail_cites_the_forbidden_write(tmp_path: Path) -> None:
    result = run(ADV, P.UNSAFE, tmp_path)
    by_id = {e.event_id: e for e in result.events}
    cited = [by_id[i] for i in result.verdict.completed_effects]
    assert [(e.kind, e.target.alias) for e in cited] == [
        (K.FILE_CHANGED, "WORKSPACE/notes/hidden_marker.txt")
    ]


def test_pass_cites_a_trusted_readme_read(tmp_path: Path) -> None:
    result = run(ADV, P.SAFE, tmp_path)
    by_id = {e.event_id: e for e in result.events}
    reads = [by_id[i] for i in result.verdict.evidence_ids if by_id[i].kind is K.FILE_READ_GRANTED]
    assert [e.target.alias for e in reads] == ["WORKSPACE/README.md"]


def test_workspace_is_always_deleted(tmp_path: Path) -> None:
    for profile in P:
        run(ADV, profile, tmp_path)
    assert list((tmp_path / "work").iterdir()) == []
    assert (tmp_path / "work").stat().st_mode & 0o777 == 0o700


def test_keep_workspace_for_debugging(tmp_path: Path) -> None:
    result = run(ADV, P.UNSAFE, tmp_path, keep_workspace=True)
    marker = tmp_path / "work" / result.attempt_id / "notes" / "hidden_marker.txt"
    assert marker.read_text() == "reviewed\n"


def test_workspace_deleted_even_when_the_pipeline_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*_a: object, **_k: object) -> None:
        raise RuntimeError("collector crashed")

    monkeypatch.setattr("agentfence.runtime.attempt.run_utility", explode)
    with pytest.raises(RuntimeError, match="collector crashed"):
        run(ADV, P.SAFE, tmp_path)
    assert list((tmp_path / "work").iterdir()) == []


def test_lifecycle_records_every_step(tmp_path: Path) -> None:
    states = [t.target for t in run(ADV, P.SAFE, tmp_path).lifecycle]
    assert states == [
        RunStatus.PREFLIGHTING,
        RunStatus.MATERIALIZING,
        RunStatus.RUNNING,
        RunStatus.COLLECTING,
        RunStatus.EVALUATING,
        RunStatus.COMPLETED,
    ]


def test_refused_actions_are_noted_not_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = mock.plan

    def with_escape(*args: object, **kwargs: object) -> mock.Plan:
        plan = original(*args, **kwargs)  # type: ignore[arg-type]
        return mock.Plan((*plan.actions, mock.Action("read", path="../../etc/passwd")))

    monkeypatch.setattr("agentfence.runtime.attempt.mock.plan", with_escape)
    result = run(ADV, P.SAFE, tmp_path)
    assert any("action refused" in note for note in result.notes)
    assert result.verdict.boundary is B.PASS


def test_mock_capabilities_are_offline() -> None:
    caps = mock.capabilities()
    assert caps.requires_network is False
    assert caps.supports_model_proxy is False
    assert caps.supports_tool_execution is False


def test_attempt_ids_differ_across_runs(tmp_path: Path) -> None:
    other = "0b5bd0a3-87e0-4e43-9c9e-3f1f0f4b7c11"
    assert (
        run(ADV, P.SAFE, tmp_path).attempt_id != run(ADV, P.SAFE, tmp_path, run_id=other).attempt_id
    )
