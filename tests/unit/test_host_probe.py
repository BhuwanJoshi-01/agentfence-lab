"""Host probe tests (AF-0102). Assessment is pure, so facts are constructed directly."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from agentfence.core.vocabulary import CheckStatus, IsolationState
from agentfence.runtime.host_probe import HostFacts, assess_host, gather_host_facts

GOOD = HostFacts(
    system="linux",
    machine="x86_64",
    python_version=(3, 12, 4),
    euid=1000,
    executables={"podman": "/usr/bin/podman", "docker": None, "git": "/usr/bin/git"},
    cgroup2=True,
    home="/home/dev",
)


def status(facts: HostFacts, check_id: str) -> CheckStatus:
    return next(c.status for c in assess_host(facts).checks if c.id == check_id)


def test_host_without_engine_or_kernel_facts_is_unsupported() -> None:
    caps = assess_host(GOOD)
    assert [c.id for c in caps.blocking_checks] == [
        "runtime.rootless",
        "runtime.seccomp",
        "cgroup.delegation",
        "userns.available",
    ]
    assert caps.isolation_state is IsolationState.UNSUPPORTED
    assert status(GOOD, "runtime.rootless") is CheckStatus.UNKNOWN


@pytest.mark.parametrize(
    ("change", "check_id", "expected"),
    [
        ({"system": "darwin"}, "platform.os", CheckStatus.FAIL),
        ({"machine": "aarch64"}, "platform.arch", CheckStatus.FAIL),
        ({"machine": "amd64"}, "platform.arch", CheckStatus.PASS),
        ({"python_version": (3, 11, 9)}, "python.version", CheckStatus.FAIL),
        ({"python_version": (3, 13, 0)}, "python.version", CheckStatus.PASS),
        ({"euid": 0}, "process.not_root", CheckStatus.FAIL),
        ({"executables": {"podman": None, "docker": None}}, "runtime.available", CheckStatus.FAIL),
        ({"executables": {"docker": "/usr/bin/docker"}}, "runtime.available", CheckStatus.PASS),
        ({"cgroup2": False}, "cgroup.v2", CheckStatus.FAIL),
        ({"cgroup2": None}, "cgroup.v2", CheckStatus.UNKNOWN),
        ({"executables": {"podman": "/usr/bin/podman"}}, "tool.git", CheckStatus.FAIL),
    ],
)
def test_individual_checks(change: dict[str, object], check_id: str, expected: CheckStatus) -> None:
    assert status(replace(GOOD, **change), check_id) is expected  # type: ignore[arg-type]


def test_root_is_a_blocking_failure() -> None:
    caps = assess_host(replace(GOOD, euid=0))
    assert "process.not_root" in [c.id for c in caps.blocking_checks]


def test_missing_git_is_not_blocking() -> None:
    caps = assess_host(replace(GOOD, executables={"podman": "/usr/bin/podman"}))
    assert "tool.git" not in [c.id for c in caps.blocking_checks]


def test_passing_checks_carry_no_remedy() -> None:
    for check in assess_host(GOOD).checks:
        if check.status is CheckStatus.PASS:
            assert check.remedy == ""
        else:
            assert check.remedy


def test_runtime_paths_are_home_scrubbed() -> None:
    facts = replace(GOOD, executables={"podman": "/home/dev/.local/bin/podman"})
    detail = next(c.detail for c in assess_host(facts).checks if c.id == "runtime.available")
    assert detail == "podman: $HOME/.local/bin/podman"


def test_gather_uses_injected_which_and_marker(tmp_path: Path) -> None:
    marker = tmp_path / "cgroup.controllers"
    marker.write_text("cpu memory pids\n")
    facts = gather_host_facts(which=lambda name: f"/opt/{name}", cgroup_marker=marker)
    assert facts.cgroup2 is True
    assert facts.executables["podman"] == "/opt/podman"
    assert set(facts.executables) == {"podman", "docker", "git"}


def test_gather_missing_marker(tmp_path: Path) -> None:
    facts = gather_host_facts(which=lambda _name: None, cgroup_marker=tmp_path / "absent")
    assert facts.cgroup2 is False
    assert all(path is None for path in facts.executables.values())


def test_gather_unreadable_marker_is_unknown(tmp_path: Path) -> None:
    class Broken(type(tmp_path)):  # type: ignore[misc]
        def is_file(self) -> bool:  # type: ignore[override]
            raise PermissionError("denied")

    facts = gather_host_facts(which=lambda _n: None, cgroup_marker=Broken(tmp_path / "x"))
    assert facts.cgroup2 is None
