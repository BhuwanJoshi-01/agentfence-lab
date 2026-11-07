# SPDX-License-Identifier: Apache-2.0
"""Rootless Podman sandbox runner and the isolation self-test (AF-0205, AF-0206; docs/03 s4-5).

:func:`run_container` creates the attempt's labelled workspace volume, runs one container
with the audited strict-policy arguments under the process supervisor (deadline, process
group, capped output) and, whatever happens, removes every container and volume labelled
with the run. A timeout therefore cannot leave a container behind.

:func:`self_test` runs :mod:`agentfence.runtime.probes.self_test` in such a container and
judges its observations on the host (:func:`judge`): the container only reports, the host
decides, and a missing or malformed observation is UNKNOWN, never a pass. Limits are
checked as *applied* (cgroup files and a bounded fork test inside the container), not as
requested (docs/03 section 4, AF-0206). Only rootless Podman is supported.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import Any, Final

from agentfence import __version__
from agentfence.core.attestation import AttestationCheck, IsolationAttestation
from agentfence.core.sandbox_policy import SANDBOX_IMAGE, IsolationPolicy
from agentfence.core.vocabulary import CheckStatus
from agentfence.runtime.containers import build_run_argv, build_volume_create_argv
from agentfence.runtime.engine_probe import Runner, minimal_engine_env, run_bounded
from agentfence.runtime.reconcile import ReconcileError, list_labelled, remove_stale
from agentfence.runtime.supervisor import ProcessOutcome, ProcessResult, ProcessSpec, supervise
from agentfence.storage.atomic import atomic_write_text
from agentfence.storage.paths import AppPaths, ensure_private_dir

PROBE_SOURCE: Final = (
    resources.files("agentfence.runtime.probes").joinpath("self_test.py").read_text("utf-8")
)
SELF_TEST_TIMEOUT_SECONDS: Final = 120.0
MIB: Final = 1024 * 1024
MOUNTS_EXACT: Final = frozenset(
    {
        "/",
        "/tmp",  # noqa: S108 - the container's own size-limited tmpfs
        "/workspace",
        "/etc/hosts",
        "/etc/hostname",
        "/etc/passwd",
        "/etc/group",
        "/etc/resolv.conf",
        "/run/.containerenv",
        "/dev",
        "/proc",
        "/sys",
    }
)
MOUNTS_UNDER: Final = ("/dev/", "/proc/", "/sys/")
UNREACHABLE: Final = frozenset({"ENETUNREACH", "EHOSTUNREACH"})

Supervise = Callable[[ProcessSpec], ProcessResult]


class SandboxError(RuntimeError):
    """The sandbox could not be prepared. Never fall back to a weaker mode."""


def remove_run_resources(executable: str, run_id: str, run: Runner = run_bounded) -> int:
    """Force-remove every container and volume labelled with ``run_id``. Returns failures."""
    mine = [r for r in list_labelled("podman", executable, run) if r.run_id == run_id]
    return len(remove_stale(executable, mine, run, engine="podman").failed)


def run_container(  # noqa: PLR0913 - explicit, keyword-only knobs
    executable: str,
    policy: IsolationPolicy,
    command: Sequence[str],
    *,
    timeout: float,
    run: Runner = run_bounded,
    supervise_fn: Supervise = supervise,
    volume_ready: bool = False,
) -> ProcessResult:
    """Run ``command`` in one strict-policy container; always clean up the run's resources.

    ``volume_ready`` means the caller already created (and filled) the labelled workspace
    volume, for example to place fixture files or watches before the agent starts.
    """
    created = volume_ready or (
        run([executable, *build_volume_create_argv("podman", policy)]).returncode == 0
    )
    if not created:
        raise SandboxError(f"could not create workspace volume {policy.workspace_volume}")
    try:
        return supervise_fn(
            ProcessSpec(
                argv=(executable, *build_run_argv("podman", policy, command)),
                cwd=Path("/"),
                timeout_seconds=timeout,
                env=minimal_engine_env(),
                max_stdout_bytes=MIB,
                max_stderr_bytes=64 * 1024,
                term_grace_seconds=5.0,
            )
        )
    finally:
        try:
            failed = remove_run_resources(executable, policy.run_id, run)
        except ReconcileError as exc:
            raise SandboxError(f"cleanup of run {policy.run_id} could not list resources") from exc
        if failed:
            raise SandboxError(f"{failed} resource(s) of run {policy.run_id} could not be removed")


def _host_id(uid_map: object, inside: int) -> int | None:
    if not isinstance(uid_map, list):
        return None
    for line in uid_map:
        parts = str(line).split()
        if len(parts) == 3 and all(p.isdigit() for p in parts):
            start, host, length = (int(p) for p in parts)
            if start <= inside < start + length:
                return host + inside - start
    return None


def _userns_isolated(uid_map: object, sandbox_host_uid: int) -> bool:
    """A real user namespace: not the identity map, and no in-container ID is host root."""
    lines = [" ".join(str(line).split()) for line in uid_map] if isinstance(uid_map, list) else []
    identity = "0 0 4294967295" in lines
    return not identity and sandbox_host_uid != 0 and _host_id(uid_map, 0) != 0


def judge(observed: Mapping[str, Any], policy: IsolationPolicy) -> tuple[AttestationCheck, ...]:
    """PASS/FAIL/UNKNOWN per isolation property from the probe's raw observations."""
    limits = policy.limits
    expected_cpu = f"{round(limits.cpu_quota * 100000)} 100000"
    fork = observed.get("fork")
    fork = fork if isinstance(fork, Mapping) else {}
    mounts = observed.get("mount_targets")
    sockets = observed.get("sockets")
    host_uid = _host_id(observed.get("uid_map"), policy.uid)
    root_host = _host_id(observed.get("uid_map"), 0)
    tmp_bytes = observed.get("tmp_bytes")
    hosts = observed.get("hosts_entries")
    rows: list[tuple[str, str, object, bool | None]] = [
        (
            "process.non_root",
            f"uid {policy.uid}",
            observed.get("uid"),
            None if "uid" not in observed else observed.get("uid") == policy.uid,
        ),
        (
            "userns.mapped",
            "own user namespace; neither root nor the sandbox user is host root",
            f"uid {policy.uid} -> {host_uid}, root -> {root_host}",
            None if host_uid is None else _userns_isolated(observed.get("uid_map"), host_uid),
        ),
        (
            "capabilities.none",
            "CapEff and CapPrm all zero",
            f"{observed.get('cap_eff')}/{observed.get('cap_prm')}",
            None
            if not observed.get("cap_eff")
            else int(str(observed.get("cap_eff")), 16)
            == 0
            == int(str(observed.get("cap_prm") or 0), 16),
        ),
        (
            "no_new_privileges",
            "NoNewPrivs 1",
            observed.get("no_new_privs"),
            None if not observed.get("no_new_privs") else observed.get("no_new_privs") == "1",
        ),
        (
            "seccomp.filter",
            "Seccomp 2 (filter)",
            observed.get("seccomp"),
            None if not observed.get("seccomp") else observed.get("seccomp") == "2",
        ),
        (
            "rootfs.read_only",
            "write to /etc fails with EROFS",
            observed.get("rootfs_write"),
            None if "rootfs_write" not in observed else observed.get("rootfs_write") == "EROFS",
        ),
        (
            "tmp.size_limited",
            f"/tmp writable, at most {policy.tmp_size_mb} MiB",  # noqa: S108 - a label
            f"{observed.get('tmp_write')}, {tmp_bytes} bytes",
            None
            if not isinstance(tmp_bytes, int)
            else observed.get("tmp_write") == "OK" and 0 < tmp_bytes <= policy.tmp_size_mb * MIB,
        ),
        (
            "workspace.writable",
            "/workspace writable by the sandbox user",
            observed.get("workspace_write"),
            None if "workspace_write" not in observed else observed.get("workspace_write") == "OK",
        ),
        (
            "memory.limit",
            f"memory.max {limits.memory_mb * MIB}, swap 0",
            f"{observed.get('memory_max')}, swap {observed.get('memory_swap_max')}",
            None
            if observed.get("memory_max") is None
            else observed.get("memory_max") == str(limits.memory_mb * MIB)
            and observed.get("memory_swap_max") == "0",
        ),
        (
            "pids.limit",
            f"pids.max {limits.pids}, forking refused below it",
            f"pids.max {observed.get('pids_max')}, forked {fork.get('forked')}, "
            f"refused {fork.get('refused') or 'never'}",
            None
            if observed.get("pids_max") is None or not fork
            else observed.get("pids_max") == str(limits.pids)
            and fork.get("refused") == "EAGAIN"
            and isinstance(fork.get("forked"), int)
            and int(str(fork.get("forked"))) < limits.pids,
        ),
        (
            "cpu.limit",
            f"cpu.max {expected_cpu}",
            observed.get("cpu_max"),
            None if observed.get("cpu_max") is None else observed.get("cpu_max") == expected_cpu,
        ),
        (
            "network.none",
            "only lo; public, metadata and host gateway unreachable",
            f"{observed.get('interfaces')}; {observed.get('connect_public')}, "
            f"{observed.get('connect_metadata')}, {observed.get('connect_host_gateway')}",
            None
            if "interfaces" not in observed
            else observed.get("interfaces") == ["lo"]
            and {
                observed.get(k)
                for k in ("connect_public", "connect_metadata", "connect_host_gateway")
            }
            <= UNREACHABLE,
        ),
        (
            "dns.blocked",
            "name resolution fails",
            observed.get("dns"),
            None if "dns" not in observed else observed.get("dns") != "RESOLVED",
        ),
        (
            "hosts.private",
            "/etc/hosts holds only loopback entries; host aliases do not resolve",
            f"{'; '.join(map(str, hosts or []))} | host alias {observed.get('resolve_host_alias')}",
            None
            if not isinstance(hosts, list) or "resolve_host_alias" not in observed
            else all(_loopback_entry(str(h)) for h in hosts)
            and observed.get("resolve_host_alias") != "RESOLVED",
        ),
        (
            "mounts.allowlisted",
            "only engine-managed mounts, /tmp and /workspace",
            ", ".join(_unexpected_mounts(mounts)) or "all expected",
            None if not isinstance(mounts, list) else not _unexpected_mounts(mounts),
        ),
        (
            "sockets.absent",
            "no socket files reachable",
            ", ".join(map(str, sockets or [])) or "none",
            None if not isinstance(sockets, list) else not sockets,
        ),
    ]
    return tuple(
        AttestationCheck(
            id=cid,
            status=CheckStatus.UNKNOWN
            if ok is None
            else (CheckStatus.PASS if ok else CheckStatus.FAIL),
            expected=expected,
            observed=str(seen)[:600],
        )
        for cid, expected, seen, ok in rows
    )


def _loopback_entry(line: str) -> bool:
    address, *names = line.split()
    loopback = address in {"127.0.0.1", "::1"}
    return loopback and all(n.startswith(("localhost", "ip6-")) for n in names)


def _unexpected_mounts(mounts: object) -> list[str]:
    if not isinstance(mounts, list):
        return []
    return [
        str(m) for m in mounts if str(m) not in MOUNTS_EXACT and not str(m).startswith(MOUNTS_UNDER)
    ]


def self_test(  # noqa: PLR0913 - explicit, keyword-only knobs
    executable: str,
    engine_version: str,
    *,
    now: datetime,
    run: Runner = run_bounded,
    supervise_fn: Supervise = supervise,
    timeout: float = SELF_TEST_TIMEOUT_SECONDS,
) -> IsolationAttestation:
    """Start one disposable strict-policy container, probe it, judge it, attest it."""
    tag = uuid.uuid4().hex[:12]
    policy = IsolationPolicy(
        run_id=f"selftest-{tag}",
        attempt_id="probe",
        image=SANDBOX_IMAGE,
        workspace_volume=f"agentfence-selftest-{tag}",
    )
    command = ["python3", "-I", "-c", PROBE_SOURCE, json.dumps({"pids": policy.limits.pids})]
    result = run_container(
        executable, policy, command, timeout=timeout, run=run, supervise_fn=supervise_fn
    )
    observed: Mapping[str, Any] = {}
    if result.outcome is ProcessOutcome.EXITED and result.exit_code == 0:
        try:
            parsed = json.loads(result.stdout.data.decode("utf-8").strip().splitlines()[-1])
            observed = parsed if isinstance(parsed, Mapping) else {}
        except (UnicodeDecodeError, ValueError, IndexError):
            observed = {}
    checks = judge(observed, policy)
    if not observed:
        reason = f"probe did not report ({result.outcome.value}, exit {result.exit_code})"
        checks = tuple(c.model_copy(update={"observed": reason}) for c in checks)
    return IsolationAttestation.create(
        created_at=now,
        tool_version=__version__,
        engine="podman",
        engine_version=engine_version,
        image=SANDBOX_IMAGE,
        policy={
            "uid": policy.uid,
            "network": policy.network,
            "memory_mb": policy.limits.memory_mb,
            "pids": policy.limits.pids,
            "cpu_quota": policy.limits.cpu_quota,
            "tmp_size_mb": policy.tmp_size_mb,
        },
        checks=checks,
    )


def save_attestation(paths: AppPaths, attestation: IsolationAttestation) -> Path:
    folder = ensure_private_dir(paths.state_dir / "attestations")
    text = json.dumps(attestation.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    return atomic_write_text(folder / f"{attestation.attestation_id}.json", text)
