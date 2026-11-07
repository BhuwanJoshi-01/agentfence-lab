"""Read-only host facts for ``agentfence doctor`` (AF-0102, AF-0201).

Gathering facts (:func:`gather_host_facts`) is separated from judging them
(:func:`assess_host`), so judgement is pure and fully unit-testable.

Facts: platform, Python, euid, executables on PATH, cgroup v2, controllers delegated to
the user's systemd slice, user-namespace limits, AppArmor's unprivileged-userns switch,
and each container engine's own report of rootless mode and seccomp
(:mod:`agentfence.runtime.engine_probe`). Engine queries are bounded, read-only
subprocesses; nothing is written, pulled or started. Anything that cannot be read is
``UNKNOWN``, which never counts as a pass.
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
from agentfence.runtime.engine_probe import (
    EngineFacts,
    EngineName,
    Runner,
    query_engine,
    run_bounded,
)

MIN_PYTHON: Final = (3, 12)
SUPPORTED_ARCHES: Final = frozenset({"x86_64", "amd64"})
CONTAINER_RUNTIMES: Final[tuple[EngineName, ...]] = ("podman", "docker")
OPTIONAL_TOOLS: Final = ("git",)
CGROUP2_MARKER: Final = Path("/sys/fs/cgroup/cgroup.controllers")
REQUIRED_CONTROLLERS: Final = frozenset({"cpu", "memory", "pids"})
USERNS_MAX: Final = Path("/proc/sys/user/max_user_namespaces")
APPARMOR_USERNS: Final = Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns")


def delegated_controllers_path(uid: int) -> Path:
    return Path(f"/sys/fs/cgroup/user.slice/user-{uid}.slice/user@{uid}.service/cgroup.controllers")


@dataclass(frozen=True, slots=True)
class HostFacts:
    system: str
    machine: str
    python_version: tuple[int, int, int]
    euid: int
    executables: Mapping[str, str | None] = field(default_factory=dict)
    cgroup2: bool | None = None
    home: str | None = None
    engines: tuple[EngineFacts, ...] = ()
    delegated_controllers: frozenset[str] | None = None
    userns_max: int | None = None
    apparmor_userns_restricted: bool | None = None


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None


def gather_host_facts(
    *,
    which: Callable[[str], str | None] = shutil.which,
    cgroup_marker: Path = CGROUP2_MARKER,
    run: Runner = run_bounded,
    read: Callable[[Path], str | None] = _read,
) -> HostFacts:
    """Collect facts without side effects. Missing information becomes ``None``."""
    try:
        cgroup2: bool | None = cgroup_marker.is_file()
    except OSError:
        cgroup2 = None
    major, minor, micro = (int(part) for part in platform.python_version_tuple()[:3])
    executables = {name: which(name) for name in (*CONTAINER_RUNTIMES, *OPTIONAL_TOOLS)}
    engines = tuple(
        query_engine(name, path, run)
        for name, path in ((n, executables.get(n)) for n in CONTAINER_RUNTIMES)
        if path is not None
    )
    uid = os.getuid()
    controllers = read(delegated_controllers_path(uid))
    userns = read(USERNS_MAX)
    apparmor = read(APPARMOR_USERNS)
    return HostFacts(
        system=platform.system().lower(),
        machine=platform.machine().lower(),
        python_version=(major, minor, micro),
        euid=os.geteuid(),
        executables=executables,
        cgroup2=cgroup2,
        home=os.environ.get("HOME"),
        engines=engines,
        delegated_controllers=frozenset(controllers.split()) if controllers is not None else None,
        userns_max=int(userns) if userns is not None and userns.isdigit() else None,
        apparmor_userns_restricted=(apparmor == "1") if apparmor in {"0", "1"} else None,
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


def _describe(engine: EngineFacts) -> str:
    return f"{engine.name} {engine.version or '(unknown version)'}"


def _engine_checks(facts: HostFacts) -> list[CapabilityCheck]:
    queried = [e for e in facts.engines if e.reachable and e.problem is None]
    rootless = [e for e in queried if e.rootless]
    chosen = rootless[0] if rootless else (queried[0] if queried else None)
    problems = ", ".join(f"{e.name}: {e.problem}" for e in facts.engines if e.problem)
    if rootless:
        rootless_ok: bool | None = True
        summary = f"{_describe(rootless[0])} reports rootless mode"
    elif queried:
        rootless_ok = False
        summary = "engine reports a rootful daemon: " + ", ".join(_describe(e) for e in queried)
    else:
        rootless_ok = None
        summary = "no container engine could be queried"
    controllers = facts.delegated_controllers
    missing = sorted(REQUIRED_CONTROLLERS - controllers) if controllers is not None else []
    restricted = facts.apparmor_userns_restricted
    return [
        _check(
            "runtime.rootless",
            ok=rootless_ok,
            required=True,
            summary=summary,
            detail=problems,
            remedy=(
                "Use rootless Podman (recommended) or rootless Docker. A rootful engine is "
                "never used for sandbox runs (ADR-011)."
            ),
        ),
        _check(
            "runtime.seccomp",
            ok=None if chosen is None else chosen.seccomp,
            required=True,
            summary=(
                "no engine to check"
                if chosen is None
                else f"{chosen.name} seccomp {'enabled' if chosen.seccomp else 'not reported'}"
            ),
            remedy="Enable the engine's default seccomp profile; never disable it.",
        ),
        _check(
            "cgroup.delegation",
            ok=None if controllers is None else not missing,
            required=True,
            summary=(
                "cannot read the user slice's delegated controllers"
                if controllers is None
                else "cpu, memory and pids delegated to your user"
                if not missing
                else f"controllers not delegated to your user: {', '.join(missing)}"
            ),
            remedy="Delegate cpu, memory and pids via systemd so resource limits are enforced.",
        ),
        _check(
            "userns.available",
            ok=None if facts.userns_max is None else facts.userns_max > 0,
            required=True,
            summary=(
                "cannot read user.max_user_namespaces"
                if facts.userns_max is None
                else f"user.max_user_namespaces = {facts.userns_max}"
            ),
            remedy="Rootless engines need user namespaces; set user.max_user_namespaces > 0.",
        ),
        _check(
            "apparmor.userns",
            ok=None if restricted in (None, True) else True,
            required=False,
            summary=(
                "AppArmor does not restrict unprivileged user namespaces"
                if restricted is False
                else "AppArmor restricts unprivileged user namespaces; rootless engines need "
                "their AppArmor profile"
                if restricted
                else "AppArmor user-namespace setting not present"
            ),
            remedy="Use a distro-packaged rootless engine that ships its AppArmor profile.",
        ),
    ]


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
        *_engine_checks(facts),
        _check(
            "tool.git",
            ok=git_path is not None,
            required=False,
            summary="git found" if git_path else "git not found",
            remedy="Install git to use fixture diffs in later milestones.",
        ),
    )
    return HostCapabilities(os=facts.system, arch=facts.machine, python=py, checks=checks)
