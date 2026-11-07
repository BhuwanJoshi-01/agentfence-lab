# SPDX-License-Identifier: Apache-2.0
"""Isolation self-test probe. Runs *inside* the sandbox container (AF-0205, AF-0206).

Passed to ``python3 -I -c`` as source, so it must stay standard-library only and
self-contained. It never judges anything: it reports what it observes as one JSON object
on stdout, and the host decides PASS/FAIL (``agentfence.runtime.sandbox``). Every probe
is bounded: one-second socket timeouts, a capped filesystem walk, and a fork test that
stops at the expected PID limit plus a margin.
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import signal
import socket
import stat
import sys
import time

KNOWN_SOCKETS = (
    "/var/run/docker.sock",
    "/run/docker.sock",
    "/run/podman/podman.sock",
    "/run/dbus/system_bus_socket",
    "/tmp/.X11-unix",
    "/run/user",
)
WALK_ROOTS = ("/", "/run", "/var", "/tmp", "/home", "/root", "/workspace", "/mnt", "/media")
WALK_SKIP = ("/proc", "/sys", "/dev", "/usr", "/lib")
MAX_WALK_ENTRIES = 20000


def _err(exc: OSError) -> str:
    return errno.errorcode.get(exc.errno or 0, type(exc).__name__)


def _read(path: str) -> str | None:
    try:
        with open(path) as handle:
            return handle.read().strip()
    except OSError:
        return None


def _try_write(path: str) -> str:
    try:
        with open(path, "w") as handle:
            handle.write("probe")
    except OSError as exc:
        return _err(exc)
    return "OK"


def _connect(host: str, port: int) -> str:
    try:
        with socket.create_connection((host, port), timeout=1):
            return "CONNECTED"
    except OSError as exc:
        return _err(exc) if exc.errno else type(exc).__name__


def _resolve(name: str) -> str:
    try:
        socket.getaddrinfo(name, 443)
    except OSError as exc:
        return type(exc).__name__
    return "RESOLVED"


def _sockets() -> list[str]:
    found = [p for p in KNOWN_SOCKETS if os.path.lexists(p)]
    seen = 0
    for root in WALK_ROOTS:
        for path, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not os.path.join(path, d).startswith(WALK_SKIP)]
            for name in files:
                seen += 1
                if seen > MAX_WALK_ENTRIES:
                    return [*found, "<walk limit reached>"]
                full = os.path.join(path, name)
                try:
                    if stat.S_ISSOCK(os.lstat(full).st_mode):
                        found.append(full)
                except OSError:
                    continue
    return sorted(set(found))


def _mount_targets() -> list[str]:
    text = _read("/proc/self/mountinfo") or ""
    return sorted({line.split()[4] for line in text.splitlines() if len(line.split()) > 4})


def _fork_until_refused(limit: int) -> dict[str, object]:
    children: list[int] = []
    refused = ""
    try:
        for _ in range(limit):
            try:
                pid = os.fork()
            except OSError as exc:
                refused = _err(exc)
                break
            if pid == 0:
                time.sleep(30)
                os._exit(0)
            children.append(pid)
    finally:
        for pid in children:
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)
        for pid in children:
            with contextlib.suppress(OSError):
                os.waitpid(pid, 0)
    return {"forked": len(children), "refused": refused}


def observe(expected: dict[str, int]) -> dict[str, object]:
    status = dict(
        line.split(":\t", 1)
        for line in (_read("/proc/self/status") or "").splitlines()
        if ":\t" in line
    )
    tmp = os.statvfs("/tmp")
    return {
        "uid": os.getuid(),
        "gid": os.getgid(),
        "uid_map": (_read("/proc/self/uid_map") or "").split("\n"),
        "cap_eff": status.get("CapEff", ""),
        "cap_prm": status.get("CapPrm", ""),
        "no_new_privs": status.get("NoNewPrivs", ""),
        "seccomp": status.get("Seccomp", ""),
        "rootfs_write": _try_write("/etc/agentfence-probe"),
        "tmp_write": _try_write("/tmp/agentfence-probe"),
        "tmp_bytes": tmp.f_blocks * tmp.f_frsize,
        "workspace_write": _try_write("/workspace/.agentfence-probe"),
        "memory_max": _read("/sys/fs/cgroup/memory.max"),
        "memory_swap_max": _read("/sys/fs/cgroup/memory.swap.max"),
        "pids_max": _read("/sys/fs/cgroup/pids.max"),
        "cpu_max": _read("/sys/fs/cgroup/cpu.max"),
        "interfaces": sorted(os.listdir("/sys/class/net")),
        "connect_public": _connect("1.1.1.1", 53),
        "connect_metadata": _connect("169.254.169.254", 80),
        "connect_host_gateway": _connect("10.0.2.2", 22),
        "dns": _resolve("example.com"),
        "mount_targets": _mount_targets(),
        "sockets": _sockets(),
        "fork": _fork_until_refused(int(expected["pids"]) + 32),
    }


if __name__ == "__main__":
    print(json.dumps(observe(json.loads(sys.argv[1]))))
