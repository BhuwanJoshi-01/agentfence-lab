# SPDX-License-Identifier: Apache-2.0
"""Golden tests: the outcome vocabulary is a public contract (AF-0006).

If one of these fails you are changing a stored/exported string. Bump the
schema version and document the reason instead of editing the expectation.
"""

from __future__ import annotations

import itertools

import pytest

from agentfence.core.vocabulary import (
    SCHEMA_VERSIONS,
    BoundaryOutcome,
    CheckStatus,
    EventKind,
    ExitCode,
    InfrastructureOutcome,
    IsolationState,
    SourceTrust,
    UtilityOutcome,
    Variant,
    run_exit_code,
)

B = BoundaryOutcome


def test_boundary_values_are_frozen() -> None:
    assert [m.value for m in BoundaryOutcome] == ["PASS", "FAIL", "INCONCLUSIVE", "NOT_TRIGGERED"]


def test_utility_and_infrastructure_values_are_frozen() -> None:
    assert [m.value for m in UtilityOutcome] == ["COMPLETE", "INCOMPLETE", "UNKNOWN"]
    assert [m.value for m in InfrastructureOutcome] == ["OK", "ERROR", "TIMEOUT", "UNSUPPORTED"]


def test_other_enums_are_frozen() -> None:
    assert [m.value for m in Variant] == ["benign", "adversarial"]
    assert [m.value for m in CheckStatus] == ["PASS", "FAIL", "UNKNOWN"]
    assert [m.value for m in IsolationState] == [
        "OFFLINE_SAFE",
        "CONTROLLED_EGRESS",
        "UNSUPPORTED",
    ]


def test_event_taxonomy_matches_docs_07() -> None:
    assert {m.value for m in EventKind} == {
        "agent.started",
        "agent.tool_reported",
        "file.read_requested",
        "file.read_granted",
        "file.read_denied",
        "file.changed",
        "network.connect_requested",
        "network.connect_denied",
        "network.mock_received",
        "test.utility_result",
        "collector.status",
        "sandbox.started",
        "sandbox.stopped",
        "policy.violation",
    }


@pytest.mark.parametrize(
    ("trust", "authoritative"),
    [
        (SourceTrust.HOST_OBSERVED, True),
        (SourceTrust.BROKER_OBSERVED, True),
        (SourceTrust.SANDBOX_REPORTED, False),
        (SourceTrust.AGENT_REPORTED, False),
        (SourceTrust.DERIVED, False),
    ],
)
def test_only_host_and_broker_sources_prove_access(
    trust: SourceTrust, *, authoritative: bool
) -> None:
    assert trust.authoritative_for_access is authoritative


def test_exit_codes_match_docs_06() -> None:
    assert {m.name: m.value for m in ExitCode} == {
        "SUCCESS": 0,
        "BOUNDARY_FAIL": 1,
        "USAGE_ERROR": 2,
        "PREFLIGHT_FAILURE": 3,
        "INFRA_FAILURE": 4,
        "STRICT_INCONCLUSIVE": 5,
        "UNSUPPORTED": 6,
        "CANCELLED": 130,
    }


def test_schema_versions_are_read_only() -> None:
    assert SCHEMA_VERSIONS["scenario"] == 1
    with pytest.raises(TypeError):
        SCHEMA_VERSIONS["scenario"] = 2  # type: ignore[index]


def test_clean_run_exits_zero() -> None:
    code = run_exit_code(
        preflight_failed=False, infra_failed=False, boundaries=[B.PASS], strict=False
    )
    assert code is ExitCode.SUCCESS


def test_inconclusive_is_zero_unless_strict() -> None:
    kwargs = {"preflight_failed": False, "infra_failed": False, "boundaries": [B.INCONCLUSIVE]}
    assert run_exit_code(**kwargs, strict=False) is ExitCode.SUCCESS
    assert run_exit_code(**kwargs, strict=True) is ExitCode.STRICT_INCONCLUSIVE


def test_not_triggered_never_fails_the_exit_code() -> None:
    code = run_exit_code(
        preflight_failed=False, infra_failed=False, boundaries=[B.NOT_TRIGGERED], strict=True
    )
    assert code is ExitCode.SUCCESS


@pytest.mark.parametrize(
    ("preflight", "infra", "boundaries", "strict", "expected"),
    [
        (True, True, [B.FAIL, B.INCONCLUSIVE], True, ExitCode.PREFLIGHT_FAILURE),
        (False, True, [B.FAIL, B.INCONCLUSIVE], True, ExitCode.INFRA_FAILURE),
        (False, False, [B.FAIL, B.INCONCLUSIVE], True, ExitCode.BOUNDARY_FAIL),
        (False, False, [B.PASS, B.INCONCLUSIVE], True, ExitCode.STRICT_INCONCLUSIVE),
        (False, False, [], False, ExitCode.SUCCESS),
    ],
)
def test_exit_precedence(
    preflight: bool, infra: bool, boundaries: list[B], strict: bool, expected: ExitCode
) -> None:
    got = run_exit_code(
        preflight_failed=preflight, infra_failed=infra, boundaries=boundaries, strict=strict
    )
    assert got is expected


def test_exit_precedence_exhaustively_picks_highest() -> None:
    order = [3, 4, 1, 5, 0]
    for preflight, infra, has_fail, has_inc, strict in itertools.product([False, True], repeat=5):
        boundaries = [B.PASS]
        if has_fail:
            boundaries.append(B.FAIL)
        if has_inc:
            boundaries.append(B.INCONCLUSIVE)
        expected = {0}
        if preflight:
            expected.add(3)
        if infra:
            expected.add(4)
        if has_fail:
            expected.add(1)
        if has_inc and strict:
            expected.add(5)
        want = next(c for c in order if c in expected)
        got = run_exit_code(
            preflight_failed=preflight, infra_failed=infra, boundaries=boundaries, strict=strict
        )
        assert got == want
