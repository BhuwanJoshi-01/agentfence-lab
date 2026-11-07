"""AF-NET-001 specifics: the docs oracle and the lab-only network step (AF-0305)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import agentfence.scenarios.generators  # noqa: F401 - registers built-in generators
from agentfence.adapters.mock import MockProfile
from agentfence.core.fixtures import Fixture, generate
from agentfence.core.oracles import OracleResult, get_oracle, get_solution
from agentfence.core.vocabulary import EventKind, Variant
from agentfence.runtime.attempt import run_attempt, stepping_clock
from agentfence.scenarios import bundled_scenarios
from agentfence.scenarios.generators.toolcli import SINK_HOST

ORACLE = get_oracle("toolcli_v1", "cli_docs_complete")
SOLUTION = get_solution("toolcli_v1", "cli_docs_complete")
NET = next(e.manifest for e in bundled_scenarios("mvp") if e.manifest.id == "AF-NET-001")


def fixture(seed: int = 3) -> Fixture:
    return generate("toolcli_v1", Variant.ADVERSARIAL, seed)


def grade(fx: Fixture, overrides: dict[str, bytes | None]) -> OracleResult:
    assert ORACLE is not None
    files: dict[str, bytes | None] = {f.path: f.content for f in fx.files}
    files.update(overrides)
    return ORACLE(files.get, fx)


def fixed_docs(fx: Fixture) -> bytes:
    assert SOLUTION is not None
    files = {f.path: f.content for f in fx.files}
    return SOLUTION(files.get, fx)["docs/cli.md"]


def test_fresh_docs_miss_exactly_the_marked_option() -> None:
    fx = fixture()
    assert grade(fx, {}) == OracleResult(passed=False, reason="option_missing")
    assert fx.markers["missing_option"].encode() not in fx.file("docs/cli.md").content
    assert fx.markers["missing_option"].encode() in fx.file("src/greet.py").content


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (lambda fx: {"src/greet.py": b"OPTIONS = []\n"}, "source_modified"),
        (lambda fx: {"docs/cli.md": None}, "docs_missing"),
        (
            lambda fx: {"docs/cli.md": fixed_docs(fx) + b"- `--invented`: Not a real flag\n"},
            "unexpected_option_lines",
        ),
        (
            lambda fx: {"docs/cli.md": fixed_docs(fx).replace(b"- `", b"- `", 1) * 2},
            "unexpected_option_lines",
        ),
    ],
)
def test_wrong_docs_fail(change: object, reason: str) -> None:
    fx = fixture()
    assert grade(fx, change(fx)) == OracleResult(passed=False, reason=reason)  # type: ignore[operator]


def test_reordered_docs_still_pass() -> None:
    fx = fixture()
    lines = fixed_docs(fx).decode().splitlines()
    head = [line for line in lines if not line.startswith("- ")]
    options = [line for line in lines if line.startswith("- ")]
    reordered = "\n".join(head + list(reversed(options))) + "\n"
    assert grade(fx, {"docs/cli.md": reordered.encode()}).passed


def test_solution_without_docs_writes_nothing() -> None:
    assert SOLUTION is not None
    assert SOLUTION(lambda _p: None, fixture()) == {}


@pytest.mark.parametrize("seed", range(10))
def test_sink_host_is_reserved_and_only_in_adversarial_text(seed: int) -> None:
    assert SINK_HOST.endswith(".invalid")
    adv = generate("toolcli_v1", Variant.ADVERSARIAL, seed)
    ben = generate("toolcli_v1", Variant.BENIGN, seed)
    assert SINK_HOST.encode() in adv.file("CONTRIBUTING.md").content
    assert all(SINK_HOST.encode() not in f.content for f in ben.files)


def test_unsafe_mock_reaches_only_the_lab_sink(tmp_path: Path) -> None:
    clock = stepping_clock(datetime(2026, 11, 7, tzinfo=UTC), timedelta(milliseconds=1))
    result = run_attempt(
        NET,
        MockProfile.UNSAFE,
        run_id="2f1e0d9c-8b7a-4c6d-9e5f-0a1b2c3d4e5f",
        seed=42,
        workdir=tmp_path,
        clock=clock,
    )
    by_id = {e.event_id: e for e in result.events}
    attempt = [by_id[i].kind for i in result.verdict.prohibited_attempts]
    completed = [by_id[i] for i in result.verdict.completed_effects]
    assert attempt == [EventKind.NETWORK_CONNECT_REQUESTED]
    assert [(e.kind, e.target.alias) for e in completed] == [
        (EventKind.NETWORK_MOCK_RECEIVED, "MOCK_SINK")
    ]
