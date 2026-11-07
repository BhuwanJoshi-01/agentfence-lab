# SPDX-License-Identifier: Apache-2.0
"""End-to-end golden outcomes for the whole bundled library (AF-0408; docs/10 section 4).

Every bundled scenario runs through the real pipeline (fixture, workspace, collectors,
utility oracle, evaluator) under every mock profile. ``tests/golden/mvp_suite_v1.json``
pins what a reviewer cares about, without run-specific IDs: the three outcome axes,
finding code, confidence, limitations, evidence counts and final lifecycle status.

A partial-evidence case (a trusted violation while another collector failed) is pinned
too: it must stay FAIL at MEDIUM confidence with the gap named.

Regenerate after a deliberate, reviewed change:

    AGENTFENCE_UPDATE_GOLDEN=1 pytest tests/unit/test_mvp_suite_golden.py
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from agentfence.adapters import mock
from agentfence.adapters.mock import MockProfile
from agentfence.core.evaluator import EVALUATOR_VERSION
from agentfence.runtime.attempt import AttemptResult, run_attempt, stepping_clock
from agentfence.scenarios import bundled_scenarios

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "mvp_suite_v1.json"
UPDATE = os.environ.get("AGENTFENCE_UPDATE_GOLDEN") == "1"
RUN_ID = "11111111-2222-4333-8444-555555555555"
SCENARIOS = [e.manifest for e in bundled_scenarios("mvp")]


def summarize(result: AttemptResult) -> dict[str, Any]:
    v = result.verdict
    return {
        "boundary": v.boundary.value,
        "utility": v.utility.value,
        "infrastructure": v.infrastructure.value,
        "finding_code": v.reason_code,
        "confidence": v.confidence.value,
        "limitations": list(v.limitations),
        "coverage_gaps": [g.value for g in v.coverage_gaps],
        "evidence": len(v.evidence_ids),
        "attempts": len(v.prohibited_attempts),
        "completed_effects": len(v.completed_effects),
        "final_status": result.lifecycle[-1].target.value,
    }


def run(scenario: Any, profile: MockProfile, tmp_path: Path) -> AttemptResult:
    clock = stepping_clock(datetime(2026, 11, 7, tzinfo=UTC), timedelta(milliseconds=1))
    return run_attempt(scenario, profile, run_id=RUN_ID, seed=42, workdir=tmp_path, clock=clock)


def current(tmp_path: Path) -> dict[str, Any]:
    table = {
        f"{s.id}/{p.value}": summarize(run(s, p, tmp_path)) for s in SCENARIOS for p in MockProfile
    }
    return {"evaluator_version": EVALUATOR_VERSION, "outcomes": table}


def test_every_scenario_and_profile_matches_golden(tmp_path: Path) -> None:
    snapshot = current(tmp_path)
    if UPDATE:
        GOLDEN.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", "utf-8")
    stored = json.loads(GOLDEN.read_text("utf-8"))
    assert stored["evaluator_version"] == EVALUATOR_VERSION, "regenerate goldens with the bump"
    changed = sorted(
        k for k in snapshot["outcomes"] if stored["outcomes"].get(k) != snapshot["outcomes"][k]
    )
    assert not changed, f"outcomes changed for {changed[:5]}; review and regenerate"
    assert sorted(stored["outcomes"]) == sorted(snapshot["outcomes"])


def test_golden_covers_every_outcome_value() -> None:
    stored = json.loads(GOLDEN.read_text("utf-8"))["outcomes"].values()
    assert {o["boundary"] for o in stored} == {"PASS", "FAIL", "INCONCLUSIVE", "NOT_TRIGGERED"}
    assert {o["utility"] for o in stored} == {"COMPLETE", "INCOMPLETE", "UNKNOWN"}
    assert {o["infrastructure"] for o in stored} == {"OK", "TIMEOUT"}


def test_partial_evidence_keeps_the_violation_at_medium_confidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unsafe behavior observed while the access broker's collector failed."""
    original = mock.plan

    def unsafe_without_broker(*args: Any, **kwargs: Any) -> mock.Plan:
        plan = original(*args, **kwargs)
        return mock.Plan(plan.actions, stalls=plan.stalls, drops_collector=True)

    monkeypatch.setattr("agentfence.runtime.attempt.mock.plan", unsafe_without_broker)
    readme = next(s for s in SCENARIOS if s.id == "AF-README-001")
    result = summarize(run(readme, MockProfile.UNSAFE, tmp_path))
    assert result["boundary"] == "FAIL"
    assert result["confidence"] == "MEDIUM"
    assert result["coverage_gaps"] == ["access_broker"]
    assert any("not complete: access_broker" in note for note in result["limitations"])
