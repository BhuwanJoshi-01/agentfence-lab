# SPDX-License-Identifier: Apache-2.0
"""Read-only container-engine and kernel capability probe (AF-0201; docs/03 sections 4-5).

Rootless mode is judged from the **engine's own server-side report**, never from the
CLI context name (docs/03 section 4): ``docker info`` must list ``name=rootless`` in its
security options; ``podman info`` must report ``host.security.rootless = true``.

Queries are bounded: argument arrays (no shell), a minimal environment, a timeout and a
cap on output. Nothing is created, pulled or started; any failure becomes "unknown",
never "pass".
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

EngineName = Literal["podman", "docker"]
QUERY_TIMEOUT_SECONDS: Final = 15
MAX_OUTPUT_BYTES: Final = 1024 * 1024
_ENV_ALLOWLIST: Final = (
    "PATH",
    "HOME",
    "XDG_RUNTIME_DIR",
    "XDG_CONFIG_HOME",
    "DOCKER_HOST",
    "DOCKER_CONTEXT",
    "DOCKER_CONFIG",
    "CONTAINER_HOST",
    "CONTAINERS_CONF",
)
COMMANDS: Final[Mapping[EngineName, tuple[str, ...]]] = {
    "docker": ("info", "--format", "{{json .}}"),
    "podman": ("info", "--format", "json"),
}


@dataclass(frozen=True, slots=True)
class EngineFacts:
    name: EngineName
    reachable: bool
    rootless: bool | None = None
    seccomp: bool | None = None
    cgroup_version: str | None = None
    version: str | None = None
    problem: str | None = None
    """Short reason code when the engine could not be queried or parsed."""


@dataclass(frozen=True, slots=True)
class QueryResult:
    returncode: int
    stdout: bytes


Runner = Callable[[Sequence[str]], QueryResult]


def _minimal_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in _ENV_ALLOWLIST}
    env["LC_ALL"] = "C"
    return env


def run_bounded(argv: Sequence[str]) -> QueryResult:
    """Default runner: no shell, minimal env, timeout, capped output."""
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv, executable resolved via PATH lookup
            list(argv),
            capture_output=True,
            timeout=QUERY_TIMEOUT_SECONDS,
            env=_minimal_env(),
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return QueryResult(returncode=-1, stdout=b"")
    return QueryResult(returncode=done.returncode, stdout=done.stdout[:MAX_OUTPUT_BYTES])


def parse_docker_info(data: Mapping[str, Any]) -> EngineFacts:
    options = data.get("SecurityOptions")
    if not isinstance(options, list):
        return EngineFacts("docker", reachable=True, problem="no_security_options")
    names = {str(opt).split(",", 1)[0].removeprefix("name=") for opt in options}
    return EngineFacts(
        "docker",
        reachable=True,
        rootless="rootless" in names,
        seccomp="seccomp" in names,
        cgroup_version=str(data.get("CgroupVersion")) if data.get("CgroupVersion") else None,
        version=str(data["ServerVersion"]) if data.get("ServerVersion") else None,
    )


def parse_podman_info(data: Mapping[str, Any]) -> EngineFacts:
    host = data.get("host")
    security = host.get("security") if isinstance(host, Mapping) else None
    if not isinstance(security, Mapping) or not isinstance(security.get("rootless"), bool):
        return EngineFacts("podman", reachable=True, problem="no_security_section")
    seccomp = security.get("seccompEnabled")
    version = data.get("version")
    cgroup = host.get("cgroupVersion") if isinstance(host, Mapping) else None
    return EngineFacts(
        "podman",
        reachable=True,
        rootless=security["rootless"],
        seccomp=seccomp if isinstance(seccomp, bool) else None,
        cgroup_version=str(cgroup).removeprefix("v") if cgroup else None,
        version=str(version.get("Version")) if isinstance(version, Mapping) else None,
    )


_PARSERS: Final = {"docker": parse_docker_info, "podman": parse_podman_info}


def query_engine(name: EngineName, executable: str, run: Runner = run_bounded) -> EngineFacts:
    result = run([executable, *COMMANDS[name]])
    if result.returncode != 0:
        return EngineFacts(name, reachable=False, problem="query_failed")
    try:
        data = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return EngineFacts(name, reachable=False, problem="unparseable_output")
    if not isinstance(data, Mapping):
        return EngineFacts(name, reachable=False, problem="unparseable_output")
    return _PARSERS[name](data)
