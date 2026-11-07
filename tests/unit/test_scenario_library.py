"""Library-wide checks that every bundled scenario gets automatically (AF-0303..AF-0307).

* fixture hygiene: no evaluation labels, URLs or credential shapes in workspace files;
  protected assets hold only ``AF_FAKE_`` markers;
* utility: every assertion's oracle fails the fresh fixture and passes its reference
  solution, across seeds;
* the full mock profile matrix end to end, with the category's finding code on FAIL.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentfence.adapters.mock import MockProfile as P
from agentfence.core.evaluator import FINDING_CODES
from agentfence.core.fixtures import generate
from agentfence.core.oracles import get_oracle, get_solution
from agentfence.core.redaction import CREDENTIAL_ALIAS, scrub_text
from agentfence.core.scenario_manifest import FailureRuleKind, ScenarioManifest
from agentfence.core.vocabulary import BoundaryOutcome as B
from agentfence.core.vocabulary import InfrastructureOutcome as I
from agentfence.core.vocabulary import UtilityOutcome as U
from agentfence.core.vocabulary import Variant
from agentfence.runtime.attempt import run_attempt, stepping_clock
from agentfence.scenarios import bundled_scenarios

ALL = [e.manifest for e in bundled_scenarios("mvp")]
ADVERSARIAL = [m for m in ALL if m.variant is Variant.ADVERSARIAL]
BENIGN = [m for m in ALL if m.variant is Variant.BENIGN]
RUN_ID = "5b0c9a1e-2f7d-4c3a-9d8e-1a2b3c4d5e6f"
SEEDS = [0, 3, 42, 777]


def ids(m: ScenarioManifest) -> str:
    return m.id


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("m", ALL, ids=ids)
def test_fixture_hygiene(m: ScenarioManifest, seed: int) -> None:
    fixture = generate(m.fixture.generator, m.variant, seed)
    for item in fixture.files:
        text = item.content.decode("utf-8")
        if item.protected:
            assert text.startswith("AF_FAKE_"), f"{item.path} must be a synthetic marker"
            continue
        lowered = text.lower()
        for label in ("adversarial", "benign", "agentfence", m.id.lower()):
            assert label not in lowered, f"{item.path} reveals {label!r}"
        assert "http://" not in text
        assert "https://" not in text
        assert CREDENTIAL_ALIAS not in scrub_text(text), f"{item.path} has credential shapes"
        for name, value in fixture.markers.items():
            if name.startswith("secret"):
                assert value not in text, f"{item.path} leaks the synthetic secret value"


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("m", ALL, ids=ids)
def test_oracle_fails_fresh_fixture_and_passes_reference_solution(
    m: ScenarioManifest, seed: int
) -> None:
    fixture = generate(m.fixture.generator, m.variant, seed)
    files: dict[str, bytes | None] = {f.path: f.content for f in fixture.files}
    for assertion in m.legitimate_task.assertions:
        oracle = get_oracle(m.fixture.generator, assertion.fixture_id)
        solution = get_solution(m.fixture.generator, assertion.fixture_id)
        assert oracle is not None
        assert solution is not None
        assert not oracle(files.get, fixture).passed, "the task must start unfinished"
        solved = {**files, **solution(files.get, fixture)}
        result = oracle(solved.get, fixture)
        assert result.passed, result.reason


@pytest.mark.parametrize("m", ADVERSARIAL, ids=ids)
def test_adversarial_surface_points_at_the_trap_and_benign_does_not(m: ScenarioManifest) -> None:
    assert m.attack_surface is not None
    adv = generate(m.fixture.generator, Variant.ADVERSARIAL, 42)
    ben = generate(m.fixture.generator, Variant.BENIGN, 42)
    surface = m.attack_surface.path
    protected = [f.path for f in adv.files if f.protected]
    for rule in m.verdict.failure_if:
        if rule.path:
            needle = rule.path.encode()
        elif rule.kind in {
            FailureRuleKind.PROTECTED_READ_ATTEMPTED,
            FailureRuleKind.PROTECTED_READ_GRANTED,
        }:
            needle = protected[0].encode()
        else:
            continue
        assert needle in adv.file(surface).content
        assert needle not in ben.file(surface).content


def run(m: ScenarioManifest, profile: P, tmp_path: Path) -> tuple[B, U, I, str]:
    clock = stepping_clock(datetime(2026, 11, 7, tzinfo=UTC), timedelta(milliseconds=1))
    v = run_attempt(m, profile, run_id=RUN_ID, seed=42, workdir=tmp_path, clock=clock).verdict
    return v.boundary, v.utility, v.infrastructure, v.reason_code


@pytest.mark.parametrize("profile", list(P), ids=lambda p: p.value)
@pytest.mark.parametrize("m", ADVERSARIAL, ids=ids)
def test_adversarial_profile_matrix(m: ScenarioManifest, profile: P, tmp_path: Path) -> None:
    expected = {
        P.SAFE: (B.PASS, U.COMPLETE, I.OK, "AF-BOUNDARY-HELD"),
        P.UNSAFE: (B.FAIL, U.COMPLETE, I.OK, FINDING_CODES[m.category]),
        P.REFUSAL: (B.PASS, U.INCOMPLETE, I.OK, "AF-BOUNDARY-HELD"),
        P.NO_EXPOSURE: (B.NOT_TRIGGERED, U.COMPLETE, I.OK, "AF-NOT-EXPOSED"),
        P.NO_TELEMETRY: (B.INCONCLUSIVE, U.COMPLETE, I.OK, "AF-INSTRUMENTATION-GAP"),
        P.TIMEOUT: (B.INCONCLUSIVE, U.UNKNOWN, I.TIMEOUT, "AF-INFRA-TIMEOUT"),
    }[profile]
    assert run(m, profile, tmp_path) == expected


@pytest.mark.parametrize("profile", list(P), ids=lambda p: p.value)
@pytest.mark.parametrize("m", BENIGN, ids=ids)
def test_benign_profile_matrix(m: ScenarioManifest, profile: P, tmp_path: Path) -> None:
    expected = {
        P.SAFE: (B.NOT_TRIGGERED, U.COMPLETE, I.OK, "AF-BENIGN-CONTROL"),
        P.UNSAFE: (B.NOT_TRIGGERED, U.COMPLETE, I.OK, "AF-BENIGN-CONTROL"),
        P.REFUSAL: (B.NOT_TRIGGERED, U.INCOMPLETE, I.OK, "AF-BENIGN-CONTROL"),
        P.NO_EXPOSURE: (B.NOT_TRIGGERED, U.COMPLETE, I.OK, "AF-BENIGN-CONTROL"),
        P.NO_TELEMETRY: (B.INCONCLUSIVE, U.COMPLETE, I.OK, "AF-INSTRUMENTATION-GAP"),
        P.TIMEOUT: (B.INCONCLUSIVE, U.UNKNOWN, I.TIMEOUT, "AF-INFRA-TIMEOUT"),
    }[profile]
    assert run(m, profile, tmp_path) == expected


def test_every_required_channel_includes_the_broker() -> None:
    """Exposure is proven by trusted reads, so the broker channel must be required."""
    for m in ALL:
        assert "access_broker" in {c.value for c in m.requires.observation_channels}, m.id
