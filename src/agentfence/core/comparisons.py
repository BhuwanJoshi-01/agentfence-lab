"""Run-to-run comparison (AF-0504; docs/06 section 2, docs/10 section 3).

Attempts are matched on ``(scenario_id, variant, repetition)``. A matched pair is only
compared when everything that could change the meaning of a verdict is the same:
scenario version, evaluator version, fixture digest (seed and generator version),
isolation tier and agent. Otherwise the pair is ``INCOMPARABLE`` with the reasons. A
scenario present in only one run is ``ADDED`` or ``REMOVED``.

Classification of comparable pairs (boundary axis first, utility second):

* ``REGRESSED``: boundary got worse (anything -> FAIL, or PASS -> NOT_TRIGGERED or
  INCONCLUSIVE), or utility went COMPLETE -> INCOMPLETE;
* ``IMPROVED``: FAIL -> anything else, or utility INCOMPLETE -> COMPLETE;
* ``CHANGED``: any other difference (e.g. INCONCLUSIVE -> NOT_TRIGGERED);
* ``UNCHANGED``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from agentfence.reporting.json_report import AttemptReport, SuiteReport


class Change(StrEnum):
    REGRESSED = "REGRESSED"
    IMPROVED = "IMPROVED"
    CHANGED = "CHANGED"
    UNCHANGED = "UNCHANGED"
    INCOMPARABLE = "INCOMPARABLE"
    ADDED = "ADDED"
    REMOVED = "REMOVED"


Key = tuple[str, str, int]

_WORSE_BOUNDARY: Final = {
    ("PASS", "NOT_TRIGGERED"),
    ("PASS", "INCONCLUSIVE"),
}


@dataclass(frozen=True, slots=True)
class PairComparison:
    scenario_id: str
    variant: str
    repetition: int
    change: Change
    baseline: str | None = None
    """``boundary/utility`` in the baseline, or ``None`` if absent."""
    candidate: str | None = None
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RunComparison:
    baseline_run: str
    candidate_run: str
    pairs: tuple[PairComparison, ...]
    run_level_reasons: tuple[str, ...] = field(default=())

    def count(self, change: Change) -> int:
        return sum(p.change is change for p in self.pairs)

    @property
    def has_regressions(self) -> bool:
        return self.count(Change.REGRESSED) > 0


def _index(results: Iterable[AttemptReport]) -> dict[Key, AttemptReport]:
    return {(r.scenario_id, r.variant, r.repetition): r for r in results}


def _incomparable_reasons(
    a: AttemptReport, b: AttemptReport, run_reasons: tuple[str, ...]
) -> tuple[str, ...]:
    reasons = list(run_reasons)
    if a.scenario_version != b.scenario_version:
        reasons.append(f"scenario version {a.scenario_version} vs {b.scenario_version}")
    if a.evaluator_version != b.evaluator_version:
        reasons.append(f"evaluator {a.evaluator_version} vs {b.evaluator_version}")
    if a.fixture_digest != b.fixture_digest:
        reasons.append("different fixture (seed or generator version)")
    return tuple(reasons)


_UTILITY_CHANGE: Final = {
    ("COMPLETE", "INCOMPLETE"): Change.REGRESSED,
    ("INCOMPLETE", "COMPLETE"): Change.IMPROVED,
}


def _boundary_change(before: str, after: str) -> Change | None:
    if before == after:
        return None
    if after == "FAIL":
        return Change.REGRESSED
    if before == "FAIL":
        return Change.IMPROVED
    if (before, after) in _WORSE_BOUNDARY:
        return Change.REGRESSED
    return Change.CHANGED


def _classify(a: AttemptReport, b: AttemptReport) -> Change:
    boundary = _boundary_change(a.boundary, b.boundary)
    if boundary is not None:
        return boundary
    if a.utility == b.utility:
        return Change.UNCHANGED
    return _UTILITY_CHANGE.get((a.utility, b.utility), Change.CHANGED)


def compare_runs(baseline: SuiteReport, candidate: SuiteReport) -> RunComparison:
    run_reasons: list[str] = []
    if baseline.isolation.tier != candidate.isolation.tier:
        run_reasons.append(
            f"isolation tier {baseline.isolation.tier} vs {candidate.isolation.tier}"
        )
    if baseline.agent.name != candidate.agent.name:
        run_reasons.append(f"agent {baseline.agent.name} vs {candidate.agent.name}")
    base_index, cand_index = _index(baseline.results), _index(candidate.results)
    pairs: list[PairComparison] = []
    for key in sorted(set(base_index) | set(cand_index)):
        sid, variant, rep = key
        a, b = base_index.get(key), cand_index.get(key)
        if a is None or b is None:
            pairs.append(
                PairComparison(
                    sid,
                    variant,
                    rep,
                    Change.ADDED if a is None else Change.REMOVED,
                    baseline=None if a is None else f"{a.boundary}/{a.utility}",
                    candidate=None if b is None else f"{b.boundary}/{b.utility}",
                )
            )
            continue
        reasons = _incomparable_reasons(a, b, tuple(run_reasons))
        change = Change.INCOMPARABLE if reasons else _classify(a, b)
        pairs.append(
            PairComparison(
                sid,
                variant,
                rep,
                change,
                baseline=f"{a.boundary}/{a.utility}",
                candidate=f"{b.boundary}/{b.utility}",
                reasons=reasons,
            )
        )
    return RunComparison(
        baseline_run=baseline.run_id,
        candidate_run=candidate.run_id,
        pairs=tuple(pairs),
        run_level_reasons=tuple(run_reasons),
    )
