# SPDX-License-Identifier: Apache-2.0
"""Runtime capability probe (AF-0201, #85).

Engine outputs are recorded fixtures (``tests/unit/data/engines/``): the rootful Docker one
mirrors the maintainer's dev box; the others cover rootless Docker and Podman.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from agentfence.core.vocabulary import CheckStatus, IsolationState
from agentfence.runtime import engine_probe
from agentfence.runtime.engine_probe import EngineFacts, QueryResult, query_engine
from agentfence.runtime.host_probe import (
    HostFacts,
    assess_host,
    delegated_controllers_path,
    gather_host_facts,
)

DATA = Path(__file__).parent / "data" / "engines"


def fixture(name: str) -> bytes:
    return (DATA / name).read_bytes()


def runner(outputs: dict[str, QueryResult]):  # type: ignore[no-untyped-def]
    seen: list[list[str]] = []

    def run(argv: Sequence[str]) -> QueryResult:
        seen.append(list(argv))
        return outputs[Path(argv[0]).name]

    run.seen = seen  # type: ignore[attr-defined]
    return run


@pytest.mark.parametrize(
    ("engine", "file", "rootless", "seccomp", "version"),
    [
        ("docker", "docker_rootful.json", False, True, "29.9.2"),
        ("docker", "docker_rootless.json", True, True, "29.9.2"),
        ("podman", "podman_rootless.json", True, True, "5.6.1"),
        ("podman", "podman_rootful_noseccomp.json", False, False, "5.6.1"),
    ],
)
def test_engine_reports_are_parsed(
    engine: str, file: str, *, rootless: bool, seccomp: bool, version: str
) -> None:
    run = runner({engine: QueryResult(0, fixture(file))})
    facts = query_engine(engine, f"/usr/bin/{engine}", run)  # type: ignore[arg-type]
    assert (facts.reachable, facts.rootless, facts.seccomp, facts.version) == (
        True,
        rootless,
        seccomp,
        version,
    )
    assert facts.cgroup_version == "2"


def test_query_uses_fixed_argv_without_a_shell() -> None:
    run = runner({"docker": QueryResult(0, fixture("docker_rootful.json"))})
    query_engine("docker", "/usr/bin/docker", run)
    assert run.seen == [["/usr/bin/docker", "info", "--format", "{{json .}}"]]  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("result", "problem"),
    [
        (QueryResult(1, b"Cannot connect to the Docker daemon"), "query_failed"),
        (QueryResult(-1, b""), "query_failed"),
        (QueryResult(0, b"not json"), "unparseable_output"),
        (QueryResult(0, b"\\xff"), "unparseable_output"),
        (QueryResult(0, b"[1, 2]"), "unparseable_output"),
    ],
)
def test_failures_are_unreachable_not_rootless(result: QueryResult, problem: str) -> None:
    facts = query_engine("docker", "docker", runner({"docker": result}))
    assert (facts.reachable, facts.rootless, facts.problem) == (False, None, problem)


@pytest.mark.parametrize(
    ("engine", "payload", "problem"),
    [
        ("docker", {"ServerVersion": "1"}, "no_security_options"),
        ("podman", {"host": {}}, "no_security_section"),
        ("podman", {"host": {"security": {"rootless": "yes"}}}, "no_security_section"),
        ("podman", {"host": "weird"}, "no_security_section"),
    ],
)
def test_incomplete_reports_never_claim_rootless(engine: str, payload: dict, problem: str) -> None:  # type: ignore[type-arg]
    run = runner({engine: QueryResult(0, json.dumps(payload).encode())})
    facts = query_engine(engine, engine, run)  # type: ignore[arg-type]
    assert facts.rootless is None
    assert facts.problem == problem


def test_bounded_runner_handles_missing_executable() -> None:
    assert engine_probe.run_bounded(["/nonexistent/agentfence-engine"]).returncode == -1


def test_bounded_runner_uses_minimal_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "should-not-leak")
    monkeypatch.setenv("DOCKER_HOST", "unix:///run/user/1000/docker.sock")
    env = engine_probe._minimal_env()
    assert "OPENAI_API_KEY" not in env
    assert env["DOCKER_HOST"] == "unix:///run/user/1000/docker.sock"
    assert env["LC_ALL"] == "C"


# --- host assessment -------------------------------------------------------------------

BASE = HostFacts(
    system="linux",
    machine="x86_64",
    python_version=(3, 12, 4),
    euid=1000,
    executables={"podman": "/usr/bin/podman", "docker": "/usr/bin/docker"},
    cgroup2=True,
    delegated_controllers=frozenset({"cpu", "memory", "pids", "io"}),
    userns_max=60358,
    apparmor_userns_restricted=False,
)
ROOTFUL_DOCKER = EngineFacts("docker", True, rootless=False, seccomp=True, version="29.9.2")
ROOTLESS_PODMAN = EngineFacts("podman", True, rootless=True, seccomp=True, version="5.6.1")


def status(facts: HostFacts, check: str) -> CheckStatus:
    return next(c.status for c in assess_host(facts).checks if c.id == check)


def test_rootful_engine_is_a_fail_with_reason() -> None:
    """#85: a rootful engine is FAIL, never UNKNOWN or PASS."""
    caps = assess_host(replace(BASE, engines=(ROOTFUL_DOCKER,)))
    check = next(c for c in caps.checks if c.id == "runtime.rootless")
    assert check.status is CheckStatus.FAIL
    assert check.summary == "engine reports a rootful daemon: docker 29.9.2"
    assert "ADR-011" in check.remedy
    assert caps.isolation_state is IsolationState.UNSUPPORTED


def test_one_rootless_engine_is_enough() -> None:
    caps = assess_host(replace(BASE, engines=(ROOTLESS_PODMAN, ROOTFUL_DOCKER)))
    assert status(replace(BASE, engines=(ROOTLESS_PODMAN, ROOTFUL_DOCKER)), "runtime.rootless") is (
        CheckStatus.PASS
    )
    assert caps.isolation_state is IsolationState.OFFLINE_SAFE


def test_unreachable_engines_are_unknown() -> None:
    down = EngineFacts("docker", False, problem="query_failed")
    caps = assess_host(replace(BASE, engines=(down,)))
    check = next(c for c in caps.checks if c.id == "runtime.rootless")
    assert check.status is CheckStatus.UNKNOWN
    assert "docker: query_failed" in check.detail


def test_seccomp_judged_on_the_rootless_engine() -> None:
    no_seccomp = replace(ROOTLESS_PODMAN, seccomp=False)
    assert status(replace(BASE, engines=(no_seccomp,)), "runtime.seccomp") is CheckStatus.FAIL
    assert status(replace(BASE, engines=()), "runtime.seccomp") is CheckStatus.UNKNOWN


@pytest.mark.parametrize(
    ("controllers", "expected"),
    [
        (frozenset({"cpu", "memory", "pids"}), CheckStatus.PASS),
        (frozenset({"memory", "pids"}), CheckStatus.FAIL),
        (frozenset(), CheckStatus.FAIL),
        (None, CheckStatus.UNKNOWN),
    ],
)
def test_cgroup_delegation(controllers: frozenset[str] | None, expected: CheckStatus) -> None:
    facts = replace(BASE, engines=(ROOTLESS_PODMAN,), delegated_controllers=controllers)
    assert status(facts, "cgroup.delegation") is expected


@pytest.mark.parametrize(
    ("userns", "expected"),
    [(60358, CheckStatus.PASS), (0, CheckStatus.FAIL), (None, CheckStatus.UNKNOWN)],
)
def test_user_namespaces(userns: int | None, expected: CheckStatus) -> None:
    assert status(replace(BASE, userns_max=userns), "userns.available") is expected


@pytest.mark.parametrize(
    ("restricted", "expected"),
    [(False, CheckStatus.PASS), (True, CheckStatus.UNKNOWN), (None, CheckStatus.UNKNOWN)],
)
def test_apparmor_restriction_is_informational(
    restricted: bool | None, expected: CheckStatus
) -> None:
    facts = replace(BASE, engines=(ROOTLESS_PODMAN,), apparmor_userns_restricted=restricted)
    assert status(facts, "apparmor.userns") is expected
    assert assess_host(facts).isolation_state is IsolationState.OFFLINE_SAFE


def test_gather_reads_kernel_files_and_queries_engines(tmp_path: Path) -> None:
    files = {
        delegated_controllers_path(__import__("os").getuid()): "cpu memory pids",
        Path("/proc/sys/user/max_user_namespaces"): "100",
        Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns"): "1",
    }
    run = runner({"docker": QueryResult(0, fixture("docker_rootful.json"))})
    facts = gather_host_facts(
        which=lambda n: "/usr/bin/docker" if n == "docker" else None,
        cgroup_marker=tmp_path / "missing",
        run=run,
        read=files.get,
    )
    assert facts.engines == (
        EngineFacts(
            "docker", True, rootless=False, seccomp=True, cgroup_version="2", version="29.9.2"
        ),
    )
    assert facts.delegated_controllers == frozenset({"cpu", "memory", "pids"})
    assert (facts.userns_max, facts.apparmor_userns_restricted) == (100, True)


def test_gather_tolerates_unreadable_or_odd_kernel_files(tmp_path: Path) -> None:
    files = {
        Path("/proc/sys/user/max_user_namespaces"): "many",
        Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns"): "2",
    }
    facts = gather_host_facts(
        which=lambda _n: None, cgroup_marker=tmp_path / "x", run=runner({}), read=files.get
    )
    assert (facts.engines, facts.delegated_controllers, facts.userns_max) == ((), None, None)
    assert facts.apparmor_userns_restricted is None


def test_bounded_runner_captures_and_caps_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine_probe, "MAX_OUTPUT_BYTES", 5)
    result = engine_probe.run_bounded([sys.executable, "-c", "print('0123456789')"])
    assert (result.returncode, result.stdout) == (0, b"01234")


def test_unreadable_kernel_file_is_none(tmp_path: Path) -> None:
    from agentfence.runtime.host_probe import _read  # noqa: PLC0415

    assert _read(tmp_path / "absent") is None
    (tmp_path / "bin").write_bytes(b"\xff\xfe")
    assert _read(tmp_path / "bin") is None


# --- the default runner goes through the process supervisor (AF-0204) ----------------------


def fake_engine(tmp_path: Path, body: str) -> str:
    script = tmp_path / "fake-engine"
    script.write_text(f"#!{sys.executable} -I\nimport json, os, sys, time\n{body}\n")
    script.chmod(0o700)
    return str(script)


def test_run_bounded_returns_exit_code_and_stdout(tmp_path: Path) -> None:
    engine = fake_engine(tmp_path, "print(json.dumps(sys.argv[1:])); sys.exit(0)")
    result = engine_probe.run_bounded([engine, "info", "--format", "json"])
    assert result == QueryResult(0, b'["info", "--format", "json"]\n')


def test_run_bounded_passes_only_the_allowlisted_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AF_FAKE_UNRELATED", "x")
    monkeypatch.setenv("DOCKER_HOST", "unix:///nonexistent.sock")
    engine = fake_engine(tmp_path, "print(json.dumps(dict(os.environ)))")
    seen = json.loads(engine_probe.run_bounded([engine]).stdout)
    assert seen["DOCKER_HOST"] == "unix:///nonexistent.sock"
    assert seen["LC_ALL"] == "C"
    assert "AF_FAKE_UNRELATED" not in seen


def test_run_bounded_timeout_and_relative_executable_are_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engine_probe, "QUERY_TIMEOUT_SECONDS", 0.2)
    slow = fake_engine(tmp_path, "time.sleep(30)")
    assert engine_probe.run_bounded([slow]) == QueryResult(-1, b"")
    assert engine_probe.run_bounded(["docker", "info"]) == QueryResult(-1, b"")
