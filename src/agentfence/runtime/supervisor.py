# SPDX-License-Identifier: Apache-2.0
"""Bounded process supervisor (AF-0204; docs/05 section 10, docs/02 section 5).

The supervisor owns a child process's whole lifetime:

* **No shell.** The child is started from an argument vector whose first element is an
  absolute path, so neither shell parsing nor ``PATH`` lookup can change what runs.
* **Explicit environment.** The child gets exactly the variables the caller passes, plus
  :data:`SUPERVISED_ENV`. Nothing is inherited from the host environment.
* **Own process group.** The child starts a new session, so a timeout or Ctrl-C signals
  the child *and everything it started* that stayed in its group.
* **Deadline with escalation.** At the deadline the group gets ``SIGTERM``; after
  ``term_grace_seconds`` it gets ``SIGKILL``.
* **No stragglers.** When the leader has exited (normally or not), any process still in
  its group is killed.
* **Bounded capture.** stdout and stderr are read concurrently into capped buffers; the
  excess is read and counted but not kept, so a chatty child never blocks on a full pipe.
  stdin is ``/dev/null``.
* **Bounded teardown.** Pipes are drained for at most ``drain_seconds`` after exit. A
  process that escaped the group (``setsid``) and still holds a pipe cannot make the
  supervisor wait; the stream is then reported as incomplete.
* **No recursion.** Starting a supervised process from inside one raises
  :class:`RecursiveInvocationError` (docs/05 section 10: refuse self-invoking loops).

The implementation is synchronous, like the rest of the code base, so file descriptors
and the child are released deterministically in ``finally`` blocks, including when Ctrl-C
interrupts a run (AF-0407).

Limits, stated plainly: a process that calls ``setsid`` or double-forks into a new session
leaves the group and is not killed here. Containing that needs a cgroup or container
(AF-0203/AF-0205); this module is the process-level layer under it, not a sandbox.
"""

from __future__ import annotations

import enum
import math
import os
import selectors
import signal
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

SUPERVISED_ENV: Final = "AGENTFENCE_SUPERVISED"
"""Set to ``1`` in every supervised child; its presence in our own environment means we
are running inside a supervised process and must not start another."""

DEFAULT_MAX_STDOUT_BYTES: Final = 1024 * 1024
DEFAULT_MAX_STDERR_BYTES: Final = 256 * 1024
_READ_CHUNK: Final = 64 * 1024
_POLL_SECONDS: Final = 0.05


class RecursiveInvocationError(RuntimeError):
    """A supervised process tried to start another supervised process."""


class ProcessOutcome(enum.StrEnum):
    EXITED = "EXITED"
    """The process ended before its deadline (any exit code or signal)."""
    TIMED_OUT = "TIMED_OUT"
    """The deadline passed; the process group was terminated."""
    START_FAILED = "START_FAILED"
    """The executable could not be started (missing, not executable, bad cwd)."""


def _check_text(value: str, what: str) -> None:
    if "\x00" in value:
        raise ValueError(f"{what} must not contain NUL bytes")


@dataclass(frozen=True, slots=True)
class ProcessSpec:
    """What to run and the bounds to run it under. Validated on construction."""

    argv: tuple[str, ...]
    cwd: Path
    timeout_seconds: float
    env: Mapping[str, str] = field(default_factory=dict)
    max_stdout_bytes: int = DEFAULT_MAX_STDOUT_BYTES
    max_stderr_bytes: int = DEFAULT_MAX_STDERR_BYTES
    term_grace_seconds: float = 2.0
    drain_seconds: float = 2.0

    def __post_init__(self) -> None:
        if not self.argv:
            raise ValueError("argv must not be empty")
        for arg in self.argv:
            _check_text(arg, "argv")
        if not Path(self.argv[0]).is_absolute():
            raise ValueError("argv[0] must be an absolute path; PATH lookup is not used")
        if not self.cwd.is_absolute():
            raise ValueError("cwd must be an absolute path")
        if not (math.isfinite(self.timeout_seconds) and self.timeout_seconds > 0):
            raise ValueError("timeout_seconds must be a positive number")
        for name in ("term_grace_seconds", "drain_seconds"):
            value = getattr(self, name)
            if not (math.isfinite(value) and value >= 0):
                raise ValueError(f"{name} must be zero or positive")
        if self.max_stdout_bytes < 0 or self.max_stderr_bytes < 0:
            raise ValueError("output caps must be zero or positive")
        for key, value in self.env.items():
            if not key or "=" in key:
                raise ValueError(f"invalid environment variable name: {key!r}")
            _check_text(key, "environment variable names")
            _check_text(value, "environment variable values")


@dataclass(frozen=True, slots=True)
class CapturedStream:
    data: bytes
    """The first ``max_*_bytes`` bytes the process wrote."""
    dropped_bytes: int
    """Bytes read past the cap and discarded."""
    complete: bool
    """End of stream was reached. False when teardown gave up on a pipe still held open."""

    @property
    def truncated(self) -> bool:
        return self.dropped_bytes > 0


_EMPTY: Final = CapturedStream(b"", 0, complete=True)


@dataclass(frozen=True, slots=True)
class ProcessResult:
    outcome: ProcessOutcome
    exit_code: int | None
    """Exit status when the process exited normally."""
    signal: int | None
    """Signal number when the process was killed by a signal."""
    stdout: CapturedStream
    stderr: CapturedStream
    duration_seconds: float
    escalated_to_kill: bool = False
    """``SIGTERM`` was not enough within the grace period and ``SIGKILL`` was sent."""
    stragglers_killed: bool = False
    """Processes were still in the group after the leader exited and were killed."""
    start_error: str | None = None


class _Pump:
    __slots__ = ("buffer", "cap", "dropped", "eof")

    def __init__(self, cap: int) -> None:
        self.cap = cap
        self.buffer = bytearray()
        self.dropped = 0
        self.eof = False

    def read_from(self, fd: int) -> None:
        try:
            chunk = os.read(fd, _READ_CHUNK)
        except BlockingIOError:
            return
        if not chunk:
            self.eof = True
            return
        keep = chunk[: max(self.cap - len(self.buffer), 0)]
        self.buffer += keep
        self.dropped += len(chunk) - len(keep)

    def result(self) -> CapturedStream:
        return CapturedStream(bytes(self.buffer), self.dropped, complete=self.eof)


def _signal_group(pgid: int, signum: signal.Signals) -> bool:
    """Signal every process in the group. False when the group no longer exists."""
    try:
        os.killpg(pgid, signum)
    except ProcessLookupError:
        return False
    return True


def _pump_until(
    selector: selectors.BaseSelector,
    done: Callable[[], bool],
    deadline: float,
) -> bool:
    """Read whatever is available until ``done()`` or the deadline. True if done."""
    while not done():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        wait = min(remaining, _POLL_SECONDS)
        if not selector.get_map():
            time.sleep(wait)
            continue
        for key, _ in selector.select(timeout=wait):
            pump: _Pump = key.data
            pump.read_from(key.fd)
            if pump.eof:
                selector.unregister(key.fileobj)
    return True


def supervise(spec: ProcessSpec) -> ProcessResult:
    """Run ``spec`` to completion or deadline and return what happened. Never leaks the
    child, its group or a file descriptor, whether it returns or raises."""
    if os.environ.get(SUPERVISED_ENV):
        raise RecursiveInvocationError(
            f"refusing to start a supervised process from inside one ({SUPERVISED_ENV} is set)"
        )
    env = {**spec.env, SUPERVISED_ENV: "1"}
    started = time.monotonic()
    try:
        proc = subprocess.Popen(  # noqa: S603 - argv vector, absolute executable, no shell
            list(spec.argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=spec.cwd,
            env=env,
            close_fds=True,
            start_new_session=True,
        )
    except OSError as exc:
        return ProcessResult(
            ProcessOutcome.START_FAILED,
            exit_code=None,
            signal=None,
            stdout=_EMPTY,
            stderr=_EMPTY,
            duration_seconds=time.monotonic() - started,
            start_error=f"{type(exc).__name__}: {exc.strerror or exc}",
        )
    assert proc.stdout is not None and proc.stderr is not None  # noqa: S101 - PIPE requested
    pgid = proc.pid  # start_new_session makes the child its own group leader
    out, err = _Pump(spec.max_stdout_bytes), _Pump(spec.max_stderr_bytes)
    selector = selectors.DefaultSelector()
    escalated = stragglers = False
    try:
        for stream, pump in ((proc.stdout, out), (proc.stderr, err)):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, pump)

        def exited() -> bool:
            return proc.poll() is not None

        timed_out = not _pump_until(selector, exited, started + spec.timeout_seconds)
        if timed_out:
            _signal_group(pgid, signal.SIGTERM)
            grace_end = time.monotonic() + spec.term_grace_seconds
            if not _pump_until(selector, exited, grace_end):
                escalated = True
                _signal_group(pgid, signal.SIGKILL)
                proc.wait()
        stragglers = _signal_group(pgid, signal.SIGKILL)
        _pump_until(selector, lambda: out.eof and err.eof, time.monotonic() + spec.drain_seconds)
    except BaseException:
        _signal_group(pgid, signal.SIGKILL)
        proc.wait()
        raise
    finally:
        selector.close()
        proc.stdout.close()
        proc.stderr.close()
    code = proc.returncode
    return ProcessResult(
        ProcessOutcome.TIMED_OUT if timed_out else ProcessOutcome.EXITED,
        exit_code=code if code >= 0 else None,
        signal=-code if code < 0 else None,
        stdout=out.result(),
        stderr=err.result(),
        duration_seconds=time.monotonic() - started,
        escalated_to_kill=escalated,
        stragglers_killed=stragglers,
    )
