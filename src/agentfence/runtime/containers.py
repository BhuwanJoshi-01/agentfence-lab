# SPDX-License-Identifier: Apache-2.0
"""Container launch arguments for a Tier 0 attempt (AF-0203; docs/03 section 4).

:func:`build_run_argv` turns an :class:`~agentfence.core.sandbox_policy.IsolationPolicy`
into the argument vector for ``podman run`` or ``docker run``. The options it emits are
then checked by :func:`audit_run_options`, an independent **allowlist** auditor: every
option must be one this module knows, in ``--name=value`` form, with a value the policy
permits, and every mandatory control must be present. Anything else raises
:class:`PolicyViolationError`. A bug in the builder therefore produces a refusal, never a
container with a control silently missing (docs/03: "respond with UNSUPPORTED rather than
silently stripping defenses").

Engine differences handled here:

* Podman passes the host's proxy variables into containers unless ``--http-proxy=false``.
* Podman's ``--read-only`` adds writable tmpfs mounts on ``/run``, ``/tmp`` and
  ``/var/tmp`` unless ``--read-only-tmpfs=false``; the policy wants only its own
  size-limited ``/tmp``.

Nothing here starts a container. Whether a running container really has these
properties is verified against a rootless engine in AF-0205 and AF-0206.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Final, Literal

from agentfence.core.sandbox_policy import (
    LABEL_PREFIX,
    TMP_PATH,
    WORKSPACE_PATH,
    IsolationPolicy,
    is_forbidden_env_name,
)

Engine = Literal["podman", "docker"]
_TMPFS_OPTIONS: Final = "rw,noexec,nosuid,nodev"
_SAFE_SECURITY_OPTS: Final = frozenset({"no-new-privileges"})
_ALLOWED: Final = frozenset(
    {
        "--rm",
        "--read-only",
        "--read-only-tmpfs",
        "--http-proxy",
        "--cap-drop",
        "--security-opt",
        "--network",
        "--pull",
        "--user",
        "--workdir",
        "--hostname",
        "--memory",
        "--memory-swap",
        "--pids-limit",
        "--cpus",
        "--tmpfs",
        "--mount",
        "--label",
        "--env",
    }
)
_REQUIRED: Final = (
    "--rm",
    "--read-only",
    "--cap-drop",
    "--security-opt",
    "--network",
    "--pull",
    "--user",
    "--memory",
    "--memory-swap",
    "--pids-limit",
    "--cpus",
    "--tmpfs",
    "--mount",
)
_PODMAN_REQUIRED: Final = ("--http-proxy", "--read-only-tmpfs")
_FLAGS_WITHOUT_VALUE: Final = frozenset({"--rm", "--read-only"})
_POSITIVE_INT: Final = re.compile(r"^[1-9][0-9]{0,9}$")
_MEMORY: Final = re.compile(r"^[1-9][0-9]{0,6}m$")
_CPUS: Final = re.compile(r"^(?:0|[1-9][0-9]?)(?:\.[0-9]{1,3})?$")
_USER: Final = re.compile(r"^([1-9][0-9]{0,9}):([1-9][0-9]{0,9})$")
_TMPFS: Final = re.compile(rf"^{TMP_PATH}:{_TMPFS_OPTIONS},size=[1-9][0-9]{{0,3}}m$")
_MOUNT: Final = re.compile(
    rf"^type=volume,source=agentfence-[a-z0-9][a-z0-9-]{{0,94}},target={WORKSPACE_PATH}$"
)
_LABEL: Final = re.compile(rf"^{re.escape(LABEL_PREFIX)}\.(run|attempt)=[A-Za-z0-9][\w.-]*$")
_ENV: Final = re.compile(r"^([A-Z_][A-Z0-9_]{0,63})=([^\x00\n\r]*)$")


class PolicyViolationError(ValueError):
    """The launch arguments do not match the strict sandbox policy. Never launch."""

    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = tuple(problems)


def _run_options(engine: Engine, policy: IsolationPolicy) -> list[str]:
    limits = policy.limits
    options = [
        "--rm",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        f"--network={policy.network}",
        f"--pull={policy.pull}",
        f"--user={policy.uid}:{policy.gid}",
        f"--workdir={WORKSPACE_PATH}",
        "--hostname=sandbox",
        f"--memory={limits.memory_mb}m",
        f"--memory-swap={limits.memory_mb}m",
        f"--pids-limit={limits.pids}",
        f"--cpus={limits.cpu_quota:g}",
        f"--tmpfs={TMP_PATH}:{_TMPFS_OPTIONS},size={policy.tmp_size_mb}m",
        f"--mount=type=volume,source={policy.workspace_volume},target={WORKSPACE_PATH}",
    ]
    if engine == "podman":
        options += ["--http-proxy=false", "--read-only-tmpfs=false"]
    options += [f"--label={key}={value}" for key, value in sorted(policy.labels.items())]
    options += [f"--env={name}={value}" for name, value in sorted(policy.env.items())]
    return options


def _check_value(name: str, value: str, engine: Engine) -> str | None:
    """Problem with one option's value, or None. Only values the policy can produce pass."""
    checks: dict[str, bool] = {
        "--cap-drop": value == "ALL",
        "--security-opt": value in _SAFE_SECURITY_OPTS,
        "--network": value == "none",
        "--pull": value == "never",
        "--workdir": value == WORKSPACE_PATH,
        "--hostname": value == "sandbox",
        "--memory": bool(_MEMORY.match(value)),
        "--memory-swap": bool(_MEMORY.match(value)),
        "--pids-limit": bool(_POSITIVE_INT.match(value)),
        "--cpus": bool(_CPUS.match(value)) and float(value) > 0,
        "--tmpfs": bool(_TMPFS.match(value)),
        "--mount": bool(_MOUNT.match(value)),
        "--label": bool(_LABEL.match(value)),
        "--http-proxy": value == "false" and engine == "podman",
        "--read-only-tmpfs": value == "false" and engine == "podman",
    }
    if name == "--user":
        return None if _USER.match(value) else f"--user must be a non-root UID:GID, got {value!r}"
    if name == "--env":
        match = _ENV.match(value)
        if not match:
            return "--env must be NAME=value with a plain variable name"
        if is_forbidden_env_name(match.group(1)):
            return f"--env {match.group(1)} is not allowed in a sandbox"
        return None
    if not checks[name]:
        return f"{name}={value} is not allowed by the sandbox policy"
    return None


def audit_run_options(options: Sequence[str], engine: Engine) -> list[str]:
    """Every problem with ``options`` (empty when they satisfy the policy)."""
    problems: list[str] = []
    seen: dict[str, list[str]] = {}
    for option in options:
        name, has_value, value = option.partition("=")
        if name not in _ALLOWED:
            problems.append(f"option {name!r} is not allowed by the sandbox policy")
            continue
        if (name in _FLAGS_WITHOUT_VALUE) == has_value.startswith("="):
            problems.append(f"option {option!r} has the wrong form")
            continue
        seen.setdefault(name, []).append(value)
        if has_value and (problem := _check_value(name, value, engine)):
            problems.append(problem)
    required = _REQUIRED + (_PODMAN_REQUIRED if engine == "podman" else ())
    problems.extend(f"mandatory option {name} is missing" for name in required if name not in seen)
    for name, values in seen.items():
        if name not in {"--label", "--env"} and len(values) > 1:
            problems.append(f"option {name} is given more than once")
    if seen.get("--memory") != seen.get("--memory-swap"):
        problems.append("--memory-swap must equal --memory (no swap)")
    if sorted(v.split("=", 1)[0] for v in seen.get("--label", [])) != [
        f"{LABEL_PREFIX}.attempt",
        f"{LABEL_PREFIX}.run",
    ]:
        problems.append("exactly one run label and one attempt label are required")
    return problems


def build_run_argv(
    engine: Engine, policy: IsolationPolicy, command: Sequence[str]
) -> tuple[str, ...]:
    """``run`` arguments for ``engine`` (without the executable), audited before return."""
    if not command:
        raise ValueError("command must not be empty")
    if any("\x00" in arg for arg in command):
        raise ValueError("command arguments must not contain NUL bytes")
    options = _run_options(engine, policy)
    problems = audit_run_options(options, engine)
    if problems:
        raise PolicyViolationError(problems)
    return ("run", *options, policy.image, *command)
