# SPDX-License-Identifier: Apache-2.0
"""Sandbox negative tests against a real rootless Podman (AF-0208; docs/03 sections 4, 13).

Each test starts a strict-policy container (``run_container``) and has it attempt something
the policy must stop: reaching host files, the home directory or sockets, leaving through
the network, reaching a service on the host's loopback, exhausting PIDs or memory,
escalating privilege, inheriting the host environment. The last two check abnormal
cleanup: a deadline and a SIGKILLed client must both end with nothing left behind.

Attempts are bounded: small limits, one-second socket timeouts, short deadlines.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from agentfence.core.config import ResourceLimits
from agentfence.core.sandbox_policy import SANDBOX_IMAGE, IsolationPolicy
from agentfence.runtime.containers import build_run_argv, build_volume_create_argv
from agentfence.runtime.engine_probe import minimal_engine_env
from agentfence.runtime.reconcile import list_labelled
from agentfence.runtime.sandbox import remove_run_resources, run_container
from agentfence.runtime.supervisor import ProcessOutcome, ProcessResult

PRELUDE = """
import errno, json, os, socket, sys
def err(fn):
    try:
        fn()
    except OSError as exc:
        return errno.errorcode.get(exc.errno or 0, type(exc).__name__)
    except Exception as exc:
        return type(exc).__name__
    return "OK"
"""


def policy(**limits: Any) -> IsolationPolicy:
    tag = uuid.uuid4().hex[:12]
    return IsolationPolicy(
        run_id=f"negtest-{tag}",
        attempt_id="probe",
        image=SANDBOX_IMAGE,
        workspace_volume=f"agentfence-negtest-{tag}",
        limits=ResourceLimits(**limits) if limits else ResourceLimits(),
    )


def in_sandbox(podman: str, code: str, *, timeout: float = 60, **limits: Any) -> ProcessResult:
    p = policy(**limits)
    return run_container(podman, p, ["python3", "-I", "-c", PRELUDE + code], timeout=timeout)


def report(podman: str, code: str, **limits: Any) -> dict[str, Any]:
    result = in_sandbox(podman, code + "\nprint(json.dumps(out))", **limits)
    assert result.outcome is ProcessOutcome.EXITED, result
    assert result.exit_code == 0, result.stderr.data.decode(errors="replace")[-500:]
    parsed: dict[str, Any] = json.loads(result.stdout.data.decode().strip().splitlines()[-1])
    return parsed


def nothing_labelled(podman: str, run_id: str) -> bool:
    return not [r for r in list_labelled("podman", podman) if r.run_id == run_id]


# --- paths -------------------------------------------------------------------------------


def test_root_filesystem_and_system_paths_are_not_writable(podman: str) -> None:
    out = report(
        podman,
        "out = {p: err(lambda p=p: open(p, 'w').write('x')) for p in "
        "('/etc/passwd-x', '/usr/lib/x', '/bin/x', '/var/x', '/proc/sysrq-trigger')}",
    )
    assert out["/etc/passwd-x"] == out["/usr/lib/x"] == out["/bin/x"] == out["/var/x"] == "EROFS"
    assert out["/proc/sysrq-trigger"] != "OK"


def test_host_files_and_home_are_not_reachable(podman: str, tmp_path: Path) -> None:
    marker = tmp_path / f"host-only-{uuid.uuid4().hex}.txt"
    marker.write_text("AF_FAKE_HOST_ONLY")
    paths = [str(marker), str(tmp_path), str(Path.home()), str(Path.home() / ".ssh")]
    out = report(podman, f"out = {{p: os.path.exists(p) for p in {paths!r}}}")
    assert out == dict.fromkeys(paths, False)


def test_no_engine_session_or_agent_sockets(podman: str) -> None:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    candidates = [
        "/var/run/docker.sock",
        "/run/docker.sock",
        "/run/podman/podman.sock",
        f"{runtime_dir}/podman/podman.sock",
        f"{runtime_dir}/bus",
        "/run/dbus/system_bus_socket",
        "/tmp/.X11-unix",  # noqa: S108 - path checked for absence inside the container
    ]
    out = report(podman, f"out = {{p: os.path.lexists(p) for p in {candidates!r}}}")
    assert out == dict.fromkeys(candidates, False)


# --- network -----------------------------------------------------------------------------


def test_no_egress_to_public_or_metadata_addresses(podman: str) -> None:
    targets = [("1.1.1.1", 443), ("8.8.8.8", 53), ("169.254.169.254", 80), ("10.0.2.2", 22)]
    out = report(
        podman,
        f"out = {{f'{{h}}:{{p}}': err(lambda h=h, p=p: socket.create_connection((h, p), 1)) "
        f"for h, p in {targets!r}}}",
    )
    assert set(out.values()) <= {"ENETUNREACH", "EHOSTUNREACH"}, out


def test_dns_and_host_aliases_do_not_resolve(podman: str) -> None:
    names = ["example.com", "host.containers.internal", "host.docker.internal", "gateway"]
    out = report(
        podman, f"out = {{n: err(lambda n=n: socket.getaddrinfo(n, 80)) for n in {names!r}}}"
    )
    assert "OK" not in out.values(), out


def test_a_service_on_the_host_loopback_is_unreachable(podman: str) -> None:
    accepted: list[object] = []
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        server.settimeout(0.2)
        port = server.getsockname()[1]
        stop = threading.Event()

        def accept() -> None:
            while not stop.is_set():
                try:
                    conn, _ = server.accept()
                except OSError:
                    continue
                accepted.append(conn)
                conn.close()

        thread = threading.Thread(target=accept, daemon=True)
        thread.start()
        try:
            out = report(
                podman,
                f"out = {{'lo': err(lambda: socket.create_connection(('127.0.0.1', {port}), 1))}}",
            )
        finally:
            stop.set()
            thread.join(5)
    assert out["lo"] == "ECONNREFUSED"  # the container's own loopback, nothing listening
    assert accepted == []


# --- resources ---------------------------------------------------------------------------


def test_a_fork_bomb_stops_at_the_pid_limit(podman: str) -> None:
    code = """
import signal, time
kids, refused = [], ""
for _ in range(500):
    try:
        pid = os.fork()
    except OSError as exc:
        refused = errno.errorcode.get(exc.errno, "")
        break
    if pid == 0:
        time.sleep(30)
        os._exit(0)
    kids.append(pid)
for pid in kids:
    os.kill(pid, signal.SIGKILL)
for pid in kids:
    os.waitpid(pid, 0)
out = {"forked": len(kids), "refused": refused}
"""
    out = report(podman, code, pids=32)
    assert out["refused"] == "EAGAIN"
    assert out["forked"] < 32


def test_memory_over_the_limit_is_stopped(podman: str) -> None:
    result = in_sandbox(
        podman,
        "chunks = [bytearray(32 * 1024 * 1024) for _ in range(12)]\nprint('ALLOCATED')",
        memory_mb=64,
    )
    assert b"ALLOCATED" not in result.stdout.data
    assert result.exit_code != 0  # OOM-killed (137) or MemoryError, never success


# --- privilege and environment -----------------------------------------------------------


def test_privilege_escalation_attempts_fail(podman: str) -> None:
    out = report(
        podman,
        "open('/workspace/f', 'w').close()\n"
        "raw = (socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)\n"
        "out = {'setuid': err(lambda: os.setuid(0)),"
        " 'chown': err(lambda: os.chown('/workspace/f', 0, 0)),"
        " 'mknod': err(lambda: os.mknod('/tmp/dev', 0o600 | 0o020000, os.makedev(1, 3))),"
        " 'raw_socket': err(lambda: socket.socket(*raw))}",
    )
    assert out == {"setuid": "EPERM", "chown": "EPERM", "mknod": "EPERM", "raw_socket": "EPERM"}


def image_env_keys(podman: str) -> set[str]:
    out = subprocess.run(
        [podman, "image", "inspect", SANDBOX_IMAGE, "--format", "{{json .Config.Env}}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {item.split("=", 1)[0] for item in json.loads(out)}


def test_host_environment_does_not_leak_in(podman: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the image's declared variables and what the engine always sets may appear."""
    monkeypatch.setenv("AF_FAKE_HOST_SECRET", "AF_FAKE_VALUE_SHOULD_NOT_LEAK")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/ssh-agent.sock")  # noqa: S108 - must not leak in
    out = report(podman, "out = dict(os.environ)")
    assert set(out) <= image_env_keys(podman) | {"HOME", "HOSTNAME", "container"}
    assert not any("AF_FAKE_VALUE" in v for v in out.values())


def test_host_identity_is_not_visible(podman: str) -> None:
    """Podman copies the host's /etc/hosts unless --no-hosts (found by this suite)."""
    out = report(
        podman,
        "import socket as s\n"
        "out = {'hostname': s.gethostname(), 'hosts': open('/etc/hosts').read(),"
        " 'alias': err(lambda: s.getaddrinfo('host.containers.internal', 80))}",
    )
    assert out["hostname"] == "sandbox"
    assert socket.gethostname() not in out["hosts"]
    assert "host.containers.internal" not in out["hosts"]
    assert out["alias"] != "OK"


# --- abnormal cleanup --------------------------------------------------------------------


def test_a_deadline_kills_the_container_and_removes_everything(podman: str) -> None:
    p = policy()
    started = time.monotonic()
    result = run_container(podman, p, ["python3", "-c", "import time; time.sleep(120)"], timeout=3)
    assert result.outcome is ProcessOutcome.TIMED_OUT
    assert time.monotonic() - started < 60
    assert nothing_labelled(podman, p.run_id)


def test_a_sigkilled_client_leaves_nothing_after_reconciliation(podman: str) -> None:
    """SIGKILL skips every finally block; the run label is what lets us find leftovers."""
    p = policy()
    subprocess.run(
        [podman, *build_volume_create_argv("podman", p)], check=True, capture_output=True
    )
    client = subprocess.Popen(
        [podman, *build_run_argv("podman", p, ["python3", "-c", "import time; time.sleep(120)"])],
        env=minimal_engine_env(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not [
            r
            for r in list_labelled("podman", podman)
            if r.run_id == p.run_id and r.kind == "container"
        ]:
            time.sleep(0.2)
        os.killpg(client.pid, signal.SIGKILL)
        client.wait(10)
        assert not nothing_labelled(podman, p.run_id)  # the crash really left something
    finally:
        failures = remove_run_resources(podman, p.run_id)
    assert failures == 0
    assert nothing_labelled(podman, p.run_id)
