"""Read-only host facts for ``agentfence doctor`` (AF-0102).

Gathering facts (:func:`gather_host_facts`) is separated from judging them
(:func:`assess_host`), so judgement is pure and fully unit-testable.

This milestone checks prerequisites only. Rootless runtime *verification*, cgroup
controller delegation, seccomp, user namespaces and LSM checks are AF-0201. Until those
exist, ``runtime.rootless`` is reported ``UNKNOWN`` and the isolation state is
``UNSUPPORTED``. The fail-closed outcome is intentional: an unverified host must never
look ready.

Nothing here writes files, starts processes, contacts a container daemon or uses the
network.
"""

from __future__ import annotations

import os
import platform
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from agentfence.core.capabilities import CapabilityCheck, HostCapabilities
from agentfence.core.redaction import scrub_text
from agentfence.core.vocabulary import CheckStatus

MIN_PYTHON: Final = (3, 12)
SUPPORTED_ARCHES: Final = frozenset({"x86_64", "amd64"})
CONTAINER_RUNTIMES: Final = ("podman", "docker")
OPTIONAL_TOOLS: Final = ("git",)
CGROUP2_MARKER: Final = Path("/sys/fs/cgroup/cgroup.controllers")


@dataclass(frozen=True, slots=True)
class HostFacts:
    system: str
    machine: str
    python_version: tuple[int, int, int]
    euid: int
    executables: Mapping[str, str | None] = field(default_factory=dict)
    cgroup2: bool | None = None
    home: str | None = None


def gather_host_facts(
    *,
    which: Callable[[str], str | None] = shutil.which,
    cgroup_marker: Path = CGROUP2_MARKER,
) -> HostFacts:
    """Collect facts without side effects. Missing information becomes ``None``."""
    try:
        cgroup2: bool | None = cgroup_marker.is_file()
    except OSError:
        cgroup2 = None
    major, minor, micro = (int(part) for part in platform.python_version_tuple()[:3])
    return HostFacts(
        system=platform.system().lower(),
        machine=platform.machine().lower(),
        python_version=(major, minor, micro),
        euid=os.geteuid(),
        executables={name: which(name) for name in (*CONTAINER_RUNTIMES, *OPTIONAL_TOOLS)},
        cgroup2=cgroup2,
        home=os.environ.get("HOME"),
    )


def _check(  # noqa: PLR0913 - keyword-only check builder
    cid: str,
    *,
    ok: bool | None,
    required: bool,
    summary: str,
    detail: str = "",
    remedy: str = "",
) -> CapabilityCheck:
    status = CheckStatus.UNKNOWN if ok is None else (CheckStatus.PASS if ok else CheckStatus.FAIL)
    return CapabilityCheck(
        id=cid,
        status=status,
        required=required,
        summary=summary,
        detail=detail,
        remedy="" if status is CheckStatus.PASS else remedy,
    )


def assess_host(facts: HostFacts) -> HostCapabilities:
    """Turn facts into PASS/FAIL/UNKNOWN checks. Pure."""
    py = ".".join(str(p) for p in facts.python_version)
    runtimes = {n: p for n, p in facts.executables.items() if n in CONTAINER_RUNTIMES and p}
    runtime_detail = ", ".join(
        f"{name}: {scrub_text(path, home=facts.home)}" for name, path in sorted(runtimes.items())
    )
    git_path = facts.executables.get("git")

    checks = (
        _check(
            "platform.os",
            ok=facts.system == "linux",
            required=True,
            summary=f"operating system is {facts.system or 'unknown'}",
            remedy="v0.1 supports Linux only; macOS/Windows need a future VM backend.",
        ),
        _check(
            "platform.arch",
            ok=facts.machine in SUPPORTED_ARCHES,
            required=True,
            summary=f"CPU architecture is {facts.machine or 'unknown'}",
            remedy="v0.1 is tested on x86_64 only.",
        ),
        _check(
            "python.version",
            ok=facts.python_version[:2] >= MIN_PYTHON,
            required=True,
            summary=f"Python {py}",
            remedy="Install Python 3.12 or newer.",
        ),
        _check(
            "process.not_root",
            ok=facts.euid != 0,
            required=True,
            summary="running as an unprivileged user" if facts.euid else "running as root",
            remedy="Run AgentFence as a normal user; it never needs root.",
        ),
        _check(
            "runtime.available",
            ok=bool(runtimes),
            required=True,
            summary="container runtime found" if runtimes else "no podman or docker on PATH",
            detail=runtime_detail,
            remedy="Install rootless Podman (recommended) or rootless Docker.",
        ),
        _check(
            "cgroup.v2",
            ok=facts.cgroup2,
            required=True,
            summary={
                True: "cgroup v2 unified hierarchy mounted",
                False: "cgroup v2 not detected",
                None: "could not read cgroup state",
            }[facts.cgroup2],
            remedy="Boot with the unified cgroup v2 hierarchy so resource limits are enforced.",
        ),
        _check(
            "runtime.rootless",
            ok=None,
            required=True,
            summary="rootless isolation not yet verified",
            detail="Runtime verification lands in AF-0201. Until then, sandbox runs are refused.",
            remedy="Nothing to do yet. This check stays UNKNOWN until verification exists.",
        ),
        _check(
            "tool.git",
            ok=git_path is not None,
            required=False,
            summary="git found" if git_path else "git not found",
            remedy="Install git to use fixture diffs in later milestones.",
        ),
    )
    return HostCapabilities(os=facts.system, arch=facts.machine, python=py, checks=checks)
