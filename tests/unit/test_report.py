"""Metrics, suite runner and JSON report (AF-0501, ADR-003)."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agentfence.adapters.mock import MockProfile
from agentfence.core.evaluator import Confidence, Verdict
from agentfence.core.metrics import Ratio, compute_metrics
from agentfence.core.vocabulary import BoundaryOutcome as B
from agentfence.core.vocabulary import InfrastructureOutcome as I
from agentfence.core.vocabulary import UtilityOutcome as U
from agentfence.core.vocabulary import Variant
from agentfence.reporting.json_report import (
    SuiteReport,
    build_report,
    report_json,
    report_schema,
)
from agentfence.runtime.attempt import stepping_clock
from agentfence.runtime.suite import SuiteRunResult, run_suite
from agentfence.scenarios import bundled_scenarios

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_FILE = ROOT / "src" / "agentfence" / "schemas" / "report.v1.schema.json"
GOLDEN = ROOT / "tests" / "golden" / "report_mvp_unsafe_v1.json"
UPDATE = os.environ.get("AGENTFENCE_UPDATE_GOLDEN") == "1"
RUN_ID = "7e23c178-70d2-4484-9bb1-89cf95da92b4"
SCENARIOS = [e.manifest for e in bundled_scenarios("mvp")]


def suite(tmp_path: Path, profile: MockProfile = MockProfile.UNSAFE, **kw: Any) -> SuiteRunResult:
    clock = stepping_clock(datetime(2026, 11, 7, tzinfo=UTC), timedelta(milliseconds=1))
    return run_suite(
        SCENARIOS,
        profile,
        scenario_profile="mvp",
        seed=42,
        workdir=tmp_path / "work",
        clock=clock,
        run_id=RUN_ID,
        **kw,
    )


# --- metrics -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("num", "den", "low", "high"),
    [(5, 5, 0.566, 1.0), (0, 5, 0.0, 0.434), (1, 5, 0.036, 0.624), (50, 100, 0.404, 0.596)],
)
def test_wilson_interval_matches_reference_values(
    num: int, den: int, low: float, high: float
) -> None:
    interval = Ratio(num, den).wilson_95
    assert interval is not None
    assert interval == pytest.approx((low, high), abs=1e-3)


def test_empty_ratio_has_no_value() -> None:
    assert Ratio(0, 0).value is None
    assert Ratio(0, 0).wilson_95 is None


@pytest.mark.parametrize(("num", "den"), [(-1, 3), (4, 3)])
def test_ratio_bounds(num: int, den: int) -> None:
    with pytest.raises(ValueError, match="numerator"):
        Ratio(num, den)


def verdict(variant: Variant, boundary: B, utility: U) -> Verdict:
    return Verdict(
        scenario_id="AF-X-001" + ("-B" if variant is Variant.BENIGN else ""),
        scenario_version="1.0.0",
        variant=variant,
        boundary=boundary,
        utility=utility,
        infrastructure=I.OK,
        reason_code="X",
        confidence=Confidence.HIGH,
    )


def test_metrics_denominators_follow_docs_04() -> None:
    adv, ben = Variant.ADVERSARIAL, Variant.BENIGN
    metrics = compute_metrics(
        [
            verdict(adv, B.PASS, U.COMPLETE),
            verdict(adv, B.FAIL, U.COMPLETE),
            verdict(adv, B.INCONCLUSIVE, U.UNKNOWN),
            verdict(adv, B.NOT_TRIGGERED, U.COMPLETE),
            verdict(ben, B.NOT_TRIGGERED, U.INCOMPLETE),
            verdict(ben, B.NOT_TRIGGERED, U.COMPLETE),
        ]
    )
    assert (metrics.attempts, metrics.adversarial, metrics.benign) == (6, 4, 2)
    assert metrics.boundary_counts == {"PASS": 1, "FAIL": 1, "INCONCLUSIVE": 1, "NOT_TRIGGERED": 3}
    assert metrics.exposure_coverage == Ratio(2, 4)
    assert metrics.violation_rate == Ratio(1, 2)  # INCONCLUSIVE excluded
    assert metrics.utility_rate == Ratio(4, 5)  # UNKNOWN excluded
    assert metrics.benign_incomplete_rate == Ratio(1, 2)
    assert metrics.inconclusive_rate == Ratio(1, 6)
    assert metrics.no_exposure_rate == Ratio(1, 4)


def test_metrics_of_nothing() -> None:
    metrics = compute_metrics([])
    assert metrics.violation_rate == Ratio(0, 0)
    assert metrics.violation_rate.value is None


# --- suite -----------------------------------------------------------------------------


def test_suite_runs_in_id_order_with_repetitions(tmp_path: Path) -> None:
    result = suite(tmp_path, repeat=2)
    assert [(a.scenario.id, a.repetition) for a in result.attempts] == [
        ("AF-README-001", 0),
        ("AF-README-001", 1),
        ("AF-README-001-B", 0),
        ("AF-README-001-B", 1),
    ]
    assert result.isolation_tier == "mock-inprocess"
    assert list((tmp_path / "work").iterdir()) == []


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"repeat": 0}, "repeat"),
        ({"run_id": "../../etc"}, "badly formed|UUID"),
        ({"run_id": RUN_ID.upper()}, "canonical UUID"),
    ],
)
def test_suite_input_validation(tmp_path: Path, kwargs: dict[str, Any], message: str) -> None:
    clock = stepping_clock(datetime(2026, 11, 7, tzinfo=UTC), timedelta(milliseconds=1))
    params: dict[str, Any] = {"run_id": RUN_ID, **kwargs}
    with pytest.raises(ValueError, match=message):
        run_suite(
            SCENARIOS,
            MockProfile.SAFE,
            scenario_profile="mvp",
            seed=1,
            workdir=tmp_path,
            clock=clock,
            **params,
        )


def test_suite_rejects_empty_and_duplicate_selection(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no scenarios"):
        run_suite([], MockProfile.SAFE, scenario_profile="mvp", seed=1, workdir=tmp_path)
    with pytest.raises(ValueError, match="duplicate"):
        run_suite(
            [SCENARIOS[0], SCENARIOS[0]],
            MockProfile.SAFE,
            scenario_profile="mvp",
            seed=1,
            workdir=tmp_path,
        )


def test_default_run_id_is_a_fresh_uuid(tmp_path: Path) -> None:
    first = run_suite(SCENARIOS, MockProfile.SAFE, scenario_profile="mvp", seed=1, workdir=tmp_path)
    second = run_suite(
        SCENARIOS, MockProfile.SAFE, scenario_profile="mvp", seed=1, workdir=tmp_path
    )
    assert first.run_id != second.run_id


# --- report ----------------------------------------------------------------------------


def test_report_round_trips_through_its_model(tmp_path: Path) -> None:
    report = build_report(suite(tmp_path))
    assert SuiteReport.model_validate_json(report_json(report)) == report


def test_report_is_deterministic_and_matches_golden(tmp_path: Path) -> None:
    text = report_json(build_report(suite(tmp_path)))
    assert text == report_json(build_report(suite(tmp_path / "again")))
    if UPDATE:
        GOLDEN.write_text(text, encoding="utf-8")
    assert text == GOLDEN.read_text(encoding="utf-8")


def test_report_contents(tmp_path: Path) -> None:
    data = json.loads(report_json(build_report(suite(tmp_path))))
    assert data["schema_version"] == 1
    assert data["isolation"]["tier"] == "mock-inprocess"
    assert "No containment claim" in data["isolation"]["claim"]
    assert data["redaction"] == {
        "enabled": True,
        "raw_transcript_included": False,
        "file_contents_included": False,
    }
    assert "does not certify agent security" in data["disclaimer"]
    fail = next(r for r in data["results"] if r["scenario_id"] == "AF-README-001")
    assert (fail["boundary"], fail["finding_code"]) == ("FAIL", "AF-READ-001")
    event_ids = {e["event_id"] for e in fail["events"]}
    assert set(fail["evidence_ids"]) <= event_ids
    assert fail["impact_scope"] == "synthetic laboratory fixture only"


def test_report_leaks_no_paths_or_secrets(tmp_path: Path) -> None:
    text = report_json(build_report(suite(tmp_path)))
    assert str(tmp_path) not in text
    assert "/home/" not in text
    assert "AF_FAKE_" not in text
    assert "unreviewed" not in text  # file contents never included


def test_schema_file_is_in_sync() -> None:
    current = json.dumps(report_schema(), indent=2, sort_keys=True) + "\n"
    if UPDATE:
        SCHEMA_FILE.write_text(current, encoding="utf-8")
    assert current == SCHEMA_FILE.read_text(encoding="utf-8"), (
        "regenerate with AGENTFENCE_UPDATE_GOLDEN=1"
    )


def test_report_validates_from_parsed_json(tmp_path: Path) -> None:
    """Regression: json.load + model_validate must work, not only model_validate_json."""
    report = build_report(suite(tmp_path))
    assert SuiteReport.model_validate(json.loads(report_json(report))) == report


@pytest.mark.parametrize(
    ("mutate", "location"),
    [
        (lambda d: d.update(schema_version=2), ("schema_version",)),
        (lambda d: d.update(surprise=True), ("surprise",)),
        (lambda d: d.pop("run_id"), ("run_id",)),
        (
            lambda d: d["redaction"].update(raw_transcript_included=True),
            ("redaction", "raw_transcript_included"),
        ),
        (
            lambda d: d["results"][0].update(impact_scope="credential stolen"),
            ("results", 0, "impact_scope"),
        ),
        (lambda d: d["results"][0].update(variant="hostile"), ("results", 0, "variant")),
    ],
)
def test_report_schema_acceptance(
    tmp_path: Path, mutate: Any, location: tuple[str | int, ...]
) -> None:
    data = json.loads(report_json(build_report(suite(tmp_path))))
    mutate(data)
    with pytest.raises(ValidationError) as info:
        SuiteReport.model_validate(data)
    assert [tuple(e["loc"]) for e in info.value.errors()] == [location]
