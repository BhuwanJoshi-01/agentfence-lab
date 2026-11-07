# SPDX-License-Identifier: Apache-2.0
"""Run comparison (AF-0504; docs/06 section 2, docs/10 section 3 comparator cases)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agentfence import cli
from agentfence.adapters.mock import MockProfile
from agentfence.core.comparisons import Change, compare_runs
from agentfence.reporting.json_report import AttemptReport, SuiteReport, build_report
from agentfence.runtime.attempt import stepping_clock
from agentfence.runtime.suite import run_suite
from agentfence.scenarios import bundled_scenarios

README = [e.manifest for e in bundled_scenarios("mvp") if e.manifest.id.startswith("AF-README")]


def make(
    tmp_path: Path,
    profile: MockProfile,
    *,
    seed: int = 42,
    repeat: int = 1,
    run_id: str = "7e23c178-70d2-4484-9bb1-89cf95da92b4",
) -> SuiteReport:
    clock = stepping_clock(datetime(2026, 11, 7, tzinfo=UTC), timedelta(milliseconds=1))
    return build_report(
        run_suite(
            README,
            profile,
            scenario_profile="mvp",
            seed=seed,
            repeat=repeat,
            workdir=tmp_path,
            clock=clock,
            run_id=run_id,
        )
    )


def edit(report: SuiteReport, sid: str, **changes: Any) -> SuiteReport:
    results = tuple(
        r.model_copy(update=changes) if r.scenario_id == sid else r for r in report.results
    )
    return report.model_copy(update={"results": results})


def change_for(base: SuiteReport, cand: SuiteReport, sid: str = "AF-README-001") -> Change:
    return next(p.change for p in compare_runs(base, cand).pairs if p.scenario_id == sid)


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        (("PASS", "COMPLETE"), ("PASS", "COMPLETE"), Change.UNCHANGED),
        (("PASS", "COMPLETE"), ("FAIL", "COMPLETE"), Change.REGRESSED),
        (("NOT_TRIGGERED", "COMPLETE"), ("FAIL", "COMPLETE"), Change.REGRESSED),
        (("INCONCLUSIVE", "UNKNOWN"), ("FAIL", "COMPLETE"), Change.REGRESSED),
        (("PASS", "COMPLETE"), ("NOT_TRIGGERED", "COMPLETE"), Change.REGRESSED),
        (("PASS", "COMPLETE"), ("INCONCLUSIVE", "COMPLETE"), Change.REGRESSED),
        (("FAIL", "COMPLETE"), ("PASS", "COMPLETE"), Change.IMPROVED),
        (("FAIL", "COMPLETE"), ("INCONCLUSIVE", "UNKNOWN"), Change.IMPROVED),
        (("PASS", "COMPLETE"), ("PASS", "INCOMPLETE"), Change.REGRESSED),
        (("PASS", "INCOMPLETE"), ("PASS", "COMPLETE"), Change.IMPROVED),
        (("PASS", "COMPLETE"), ("PASS", "UNKNOWN"), Change.CHANGED),
        (("INCONCLUSIVE", "COMPLETE"), ("NOT_TRIGGERED", "COMPLETE"), Change.CHANGED),
        (("NOT_TRIGGERED", "COMPLETE"), ("PASS", "COMPLETE"), Change.CHANGED),
    ],
)
def test_classification_table(
    tmp_path: Path, before: tuple[str, str], after: tuple[str, str], expected: Change
) -> None:
    base = edit(
        make(tmp_path, MockProfile.SAFE), "AF-README-001", boundary=before[0], utility=before[1]
    )
    cand = edit(base, "AF-README-001", boundary=after[0], utility=after[1])
    assert change_for(base, cand) == expected


def test_real_safe_to_unsafe_regresses(tmp_path: Path) -> None:
    result = compare_runs(
        make(tmp_path / "a", MockProfile.SAFE), make(tmp_path / "b", MockProfile.UNSAFE)
    )
    assert result.has_regressions
    assert result.count(Change.REGRESSED) == 1
    assert result.count(Change.UNCHANGED) == 1


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("scenario_version", "2.0.0", "scenario version 1.0.0 vs 2.0.0"),
        ("evaluator_version", "9.9.9", "evaluator"),
        ("fixture_digest", "0" * 64, "different fixture"),
    ],
)
def test_incomparable_pairs_explain_why(
    tmp_path: Path, field: str, value: str, reason: str
) -> None:
    base = make(tmp_path, MockProfile.SAFE)
    cand = edit(make(tmp_path / "u", MockProfile.UNSAFE), "AF-README-001", **{field: value})
    pair = next(p for p in compare_runs(base, cand).pairs if p.scenario_id == "AF-README-001")
    assert pair.change is Change.INCOMPARABLE
    assert any(reason in r for r in pair.reasons)


def test_different_seed_is_incomparable(tmp_path: Path) -> None:
    result = compare_runs(
        make(tmp_path / "a", MockProfile.SAFE), make(tmp_path / "b", MockProfile.SAFE, seed=7)
    )
    assert {p.change for p in result.pairs} == {Change.INCOMPARABLE}
    assert not result.has_regressions


@pytest.mark.parametrize(
    ("update", "reason"),
    [
        ({"isolation": {"tier": "rootless-podman", "claim": "x"}}, "isolation tier"),
        ({"agent": {"name": "codex", "version": "1", "profile": "x"}}, "agent mock vs codex"),
    ],
)
def test_run_level_mismatch_makes_everything_incomparable(
    tmp_path: Path, update: dict[str, Any], reason: str
) -> None:
    base = make(tmp_path, MockProfile.SAFE)
    cand = SuiteReport.model_validate({**base.model_dump(mode="json"), **update})
    result = compare_runs(base, cand)
    assert any(reason in r for r in result.run_level_reasons)
    assert {p.change for p in result.pairs} == {Change.INCOMPARABLE}


def test_added_and_removed_scenarios(tmp_path: Path) -> None:
    base = make(tmp_path / "a", MockProfile.SAFE)
    cand = make(tmp_path / "b", MockProfile.SAFE, repeat=2)
    result = compare_runs(base, cand)
    assert result.count(Change.ADDED) == 2  # repetition 1 of both scenarios
    reverse = compare_runs(cand, base)
    assert reverse.count(Change.REMOVED) == 2
    added = next(p for p in result.pairs if p.change is Change.ADDED)
    assert added.baseline is None
    assert added.candidate is not None


def test_pairs_are_sorted_and_keyed_by_repetition(tmp_path: Path) -> None:
    report = make(tmp_path, MockProfile.SAFE, repeat=2)
    keys = [(p.scenario_id, p.repetition) for p in compare_runs(report, report).pairs]
    assert keys == sorted(keys)
    assert all(isinstance(r, AttemptReport) for r in report.results)


# --- CLI -------------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    for var in (
        "XDG_STATE_HOME",
        "XDG_CONFIG_HOME",
        "AGENTFENCE_CONFIG",
        "AGENTFENCE_STATE_DIR",
        "AGENTFENCE_LOG_LEVEL",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    return home


def new_run(*args: str) -> str:
    out = CliRunner().invoke(cli.app, ["test", "--cases", "AF-README-001", "--json", *args])
    return str(json.loads(out.stdout)["run_id"])


def test_cli_compare_exit_codes(home: Path) -> None:
    safe, unsafe = new_run("--agent-profile", "safe"), new_run("--agent-profile", "unsafe")
    runner = CliRunner()
    worse = runner.invoke(cli.app, ["runs", "compare", safe, unsafe])
    assert worse.exit_code == 1
    assert "REGRESSED" in worse.stdout
    assert "regressed=1" in worse.stdout
    better = runner.invoke(cli.app, ["runs", "compare", unsafe, safe])
    assert better.exit_code == 0
    assert "IMPROVED" in better.stdout


def test_cli_compare_json(home: Path) -> None:
    safe, other_seed = new_run(), new_run("--seed", "9")
    result = CliRunner().invoke(cli.app, ["runs", "compare", safe, other_seed, "--json"])
    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["counts"]["INCOMPARABLE"] == 2
    assert payload["pairs"][0]["reasons"] == ["different fixture (seed or generator version)"]


def test_cli_compare_unknown_run(home: Path) -> None:
    run = new_run()
    result = CliRunner().invoke(
        cli.app, ["runs", "compare", run, "00000000-0000-0000-0000-000000000000"]
    )
    assert result.exit_code == 2
    assert "no run with ID" in result.stderr


def test_cli_compare_refuses_tampered_runs(home: Path) -> None:
    first, second = new_run(), new_run("--agent-profile", "unsafe")
    path = home / ".local/state/agentfence/runs" / second / "report.json"
    path.write_text(path.read_text().replace('"FAIL"', '"PASS"'))
    result = CliRunner().invoke(cli.app, ["runs", "compare", first, second])
    assert result.exit_code == 4


def test_test_baseline_flags_regressions(home: Path) -> None:
    safe = new_run()
    result = CliRunner().invoke(
        cli.app,
        ["test", "--cases", "AF-README-001", "--agent-profile", "unsafe", "--baseline", safe],
    )
    assert result.exit_code == 1
    assert f"Compared with baseline {safe}: regressed=1" in result.stdout
    assert "REGRESSED    AF-README-001 #0: PASS/COMPLETE -> FAIL/COMPLETE" in result.stdout


def test_test_baseline_regression_raises_exit_code_even_without_fail(home: Path) -> None:
    safe = new_run()
    result = CliRunner().invoke(
        cli.app,
        ["test", "--cases", "AF-README-001", "--agent-profile", "refusal", "--baseline", safe],
    )
    assert result.exit_code == 1  # PASS/COMPLETE -> PASS/INCOMPLETE regressed utility


def test_test_baseline_without_regression_keeps_exit_0(home: Path) -> None:
    safe = new_run()
    result = CliRunner().invoke(
        cli.app, ["test", "--cases", "AF-README-001", "--baseline", safe, "--json"]
    )
    assert result.exit_code == 0
    SuiteReport.model_validate_json(result.stdout)  # JSON stays exactly the report


def test_test_baseline_timeout_keeps_higher_precedence_code(home: Path) -> None:
    safe = new_run()
    result = CliRunner().invoke(
        cli.app,
        ["test", "--cases", "AF-README-001", "--agent-profile", "timeout", "--baseline", safe],
    )
    assert result.exit_code == 4


@pytest.mark.parametrize("bad", ["00000000-0000-0000-0000-000000000000", "nope"])
def test_test_rejects_unknown_baseline_before_running(home: Path, bad: str) -> None:
    new_run()
    before = len(list((home / ".local/state/agentfence/runs").iterdir()))
    result = CliRunner().invoke(cli.app, ["test", "--baseline", bad])
    assert result.exit_code == 2
    assert len(list((home / ".local/state/agentfence/runs").iterdir())) == before
