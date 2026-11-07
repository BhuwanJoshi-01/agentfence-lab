# SPDX-License-Identifier: Apache-2.0
"""Bounded process supervisor (AF-0204): real child processes, positive and adversarial."""

from __future__ import annotations

import contextlib
import os
import signal
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from types import FrameType

import pytest

from agentfence.runtime.supervisor import (
    SUPERVISED_ENV,
    ProcessOutcome,
    ProcessSpec,
    RecursiveInvocationError,
    supervise,
)

PY = sys.executable


def spec(code: str, tmp_path: Path, *args: str, **overrides: object) -> ProcessSpec:
    options: dict[str, object] = {"timeout_seconds": 10.0, "term_grace_seconds": 0.5}
    options.update(overrides)
    return ProcessSpec(argv=(PY, "-I", "-c", code, *args), cwd=tmp_path, **options)  # type: ignore[arg-type]


def gone(pid: int, within: float = 5.0) -> bool:
    """True once ``pid`` no longer exists (or is a zombie awaiting its new parent)."""
    end = time.monotonic() + within
    while time.monotonic() < end:
        try:
            state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        except (FileNotFoundError, ProcessLookupError):
            return True
        if state == "Z":
            return True
        time.sleep(0.02)
    return False


def wait_for_pid(path: Path, within: float = 5.0) -> int:
    end = time.monotonic() + within
    while time.monotonic() < end:
        text = path.read_text() if path.exists() else ""
        if text.endswith("\n"):
            return int(text)
        time.sleep(0.01)
    raise AssertionError(f"{path} was never written")


@pytest.fixture
def reap_escaped() -> Iterator[list[int]]:
    """PIDs of processes a test deliberately let escape; killed afterwards."""
    pids: list[int] = []
    yield pids
    for pid in pids:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)


# --- normal operation ----------------------------------------------------------------------


def test_captures_output_and_exit_code(tmp_path: Path) -> None:
    code = "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"
    result = supervise(spec(code, tmp_path))
    assert result.outcome is ProcessOutcome.EXITED
    assert (result.exit_code, result.signal) == (3, None)
    assert (result.stdout.data, result.stderr.data) == (b"out\n", b"err\n")
    assert result.stdout.complete and result.stderr.complete
    assert not (result.escalated_to_kill or result.stragglers_killed)


def test_runs_in_the_given_directory_with_stdin_closed(tmp_path: Path) -> None:
    code = "import os, sys; print(os.getcwd()); print(repr(sys.stdin.read()))"
    result = supervise(spec(code, tmp_path))
    assert result.stdout.data.decode().splitlines() == [str(tmp_path), "''"]


def test_no_shell_metacharacters_are_interpreted(tmp_path: Path) -> None:
    payload = f"; touch {tmp_path / 'pwned'} && echo $HOME `id`"
    result = supervise(spec("import sys; print(sys.argv[1])", tmp_path, payload))
    assert result.stdout.data == payload.encode() + b"\n"
    assert not (tmp_path / "pwned").exists()


def test_environment_is_explicit_and_marked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AF_FAKE_PARENT_ONLY", "should-not-leak")
    code = "import os; print(sorted(os.environ.items()))"
    result = supervise(spec(code, tmp_path, env={"GREETING": "hi"}))
    seen = dict(eval(result.stdout.data))  # noqa: S307 - our own child's repr output
    assert seen["GREETING"] == "hi"
    assert seen[SUPERVISED_ENV] == "1"
    assert "AF_FAKE_PARENT_ONLY" not in seen
    assert "PATH" not in seen
    assert set(seen) <= {"GREETING", SUPERVISED_ENV, "LC_CTYPE"}  # LC_CTYPE: PEP 538


def test_signal_death_is_reported_as_a_signal(tmp_path: Path) -> None:
    result = supervise(spec("import os, signal; os.kill(os.getpid(), signal.SIGABRT)", tmp_path))
    assert result.outcome is ProcessOutcome.EXITED
    assert (result.exit_code, result.signal) == (None, signal.SIGABRT)


# --- bounds ---------------------------------------------------------------------------------


def test_output_is_capped_without_blocking_the_child(tmp_path: Path) -> None:
    code = "import sys; sys.stdout.buffer.write(b'x' * 3_000_000); sys.stderr.write('e' * 10)"
    result = supervise(spec(code, tmp_path, max_stdout_bytes=1000, max_stderr_bytes=4))
    assert result.outcome is ProcessOutcome.EXITED
    assert result.exit_code == 0
    assert result.stdout.data == b"x" * 1000
    assert result.stdout.dropped_bytes == 3_000_000 - 1000
    assert result.stdout.truncated
    assert (result.stderr.data, result.stderr.dropped_bytes) == (b"eeee", 6)


def test_timeout_terminates_with_sigterm(tmp_path: Path) -> None:
    started = time.monotonic()
    result = supervise(spec("import time; time.sleep(60)", tmp_path, timeout_seconds=0.3))
    assert result.outcome is ProcessOutcome.TIMED_OUT
    assert result.signal == signal.SIGTERM
    assert not result.escalated_to_kill
    assert time.monotonic() - started < 10


def test_sigterm_ignoring_child_is_killed_after_the_grace_period(tmp_path: Path) -> None:
    code = (
        "import signal, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "print('ready', flush=True)\n"
        "time.sleep(60)\n"
    )
    result = supervise(spec(code, tmp_path, timeout_seconds=0.5, term_grace_seconds=0.3))
    assert result.outcome is ProcessOutcome.TIMED_OUT
    assert result.escalated_to_kill
    assert result.signal == signal.SIGKILL
    assert result.stdout.data == b"ready\n"


def test_timeout_kills_grandchildren_in_the_group(tmp_path: Path) -> None:
    pidfile = tmp_path / "grandchild.pid"
    code = (
        "import subprocess, sys, time\n"
        "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(pidfile)!r}, 'w').write(f'{{g.pid}}\\n')\n"
        "time.sleep(60)\n"
    )
    result = supervise(spec(code, tmp_path, timeout_seconds=1.0))
    assert result.outcome is ProcessOutcome.TIMED_OUT
    assert gone(wait_for_pid(pidfile))


def test_background_stragglers_are_killed_after_a_normal_exit(tmp_path: Path) -> None:
    """A leader that exits while a child keeps the pipes open must not hang the supervisor."""
    pidfile = tmp_path / "straggler.pid"
    code = (
        "import subprocess, sys\n"
        "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(pidfile)!r}, 'w').write(f'{{g.pid}}\\n')\n"
        "print('leader done')\n"
    )
    started = time.monotonic()
    result = supervise(spec(code, tmp_path))
    assert time.monotonic() - started < 8
    assert result.outcome is ProcessOutcome.EXITED
    assert result.exit_code == 0
    assert result.stragglers_killed
    assert result.stdout.data == b"leader done\n"
    assert result.stdout.complete
    assert gone(wait_for_pid(pidfile))


def test_a_process_that_escapes_the_group_cannot_stall_teardown(
    tmp_path: Path, reap_escaped: list[int]
) -> None:
    """Documented limit: setsid leaves the group. Teardown stays bounded and says so."""
    pidfile = tmp_path / "escaped.pid"
    code = (
        "import os, time\n"
        "if os.fork() == 0:\n"
        "    os.setsid()\n"
        f"    open({str(pidfile)!r}, 'w').write(f'{{os.getpid()}}\\n')\n"
        "    time.sleep(60)\n"
        "    os._exit(0)\n"
        f"while not open({str(pidfile)!r}, 'a+').tell():\n"
        "    time.sleep(0.01)\n"
    )
    started = time.monotonic()
    result = supervise(spec(code, tmp_path, drain_seconds=0.3))
    reap_escaped.append(wait_for_pid(pidfile))
    assert time.monotonic() - started < 8
    assert result.outcome is ProcessOutcome.EXITED
    assert not result.stdout.complete
    assert not result.stragglers_killed


# --- refusal and failure --------------------------------------------------------------------


def test_recursive_invocation_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SUPERVISED_ENV, "1")
    with pytest.raises(RecursiveInvocationError):
        supervise(spec("print('never')", tmp_path))


def test_a_supervised_child_cannot_supervise_again(tmp_path: Path) -> None:
    code = (
        "from pathlib import Path\n"
        "from agentfence.runtime.supervisor import ProcessSpec, supervise\n"
        f"supervise(ProcessSpec(({PY!r}, '-c', 'pass'), Path('/'), 5.0))\n"
    )
    src = str(Path(__file__).resolve().parents[2] / "src")
    argv = (PY, "-c", code)
    result = supervise(ProcessSpec(argv, tmp_path, 20.0, env={"PYTHONPATH": src}))
    assert result.exit_code == 1
    assert b"RecursiveInvocationError" in result.stderr.data


def test_missing_executable_is_a_start_failure(tmp_path: Path) -> None:
    result = supervise(ProcessSpec((str(tmp_path / "nope"),), tmp_path, 5.0))
    assert result.outcome is ProcessOutcome.START_FAILED
    assert result.start_error is not None
    assert result.start_error.startswith("FileNotFoundError")
    assert result.exit_code is None


def test_missing_working_directory_is_a_start_failure(tmp_path: Path) -> None:
    result = supervise(ProcessSpec((PY, "-c", "pass"), tmp_path / "absent", 5.0))
    assert result.outcome is ProcessOutcome.START_FAILED


@pytest.mark.parametrize(
    ("argv", "cwd", "timeout", "extra", "message"),
    [
        ((), "/", 1.0, {}, "argv must not be empty"),
        (("python3", "-c", "pass"), "/", 1.0, {}, "absolute path"),
        ((PY, "a\x00b"), "/", 1.0, {}, "NUL"),
        ((PY,), "relative", 1.0, {}, "cwd must be"),
        ((PY,), "/", 0.0, {}, "timeout_seconds"),
        ((PY,), "/", float("inf"), {}, "timeout_seconds"),
        ((PY,), "/", 1.0, {"term_grace_seconds": -1.0}, "term_grace_seconds"),
        ((PY,), "/", 1.0, {"max_stdout_bytes": -1}, "output caps"),
        ((PY,), "/", 1.0, {"env": {"A=B": "x"}}, "invalid environment"),
        ((PY,), "/", 1.0, {"env": {"": "x"}}, "invalid environment"),
        ((PY,), "/", 1.0, {"env": {"A": "x\x00"}}, "NUL"),
    ],
)
def test_invalid_specs_are_rejected(
    argv: tuple[str, ...], cwd: str, timeout: float, extra: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        ProcessSpec(argv, Path(cwd), timeout, **extra)  # type: ignore[arg-type]


# --- interruption ---------------------------------------------------------------------------


def test_ctrl_c_tears_down_the_whole_group(tmp_path: Path) -> None:
    pidfile = tmp_path / "grandchild.pid"
    code = (
        "import subprocess, sys, time\n"
        "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(pidfile)!r}, 'w').write(f'{{g.pid}}\\n')\n"
        "time.sleep(60)\n"
    )

    def interrupt(signum: int, frame: FrameType | None) -> None:
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGALRM, interrupt)
    signal.setitimer(signal.ITIMER_REAL, 1.0)
    try:
        with pytest.raises(KeyboardInterrupt):
            supervise(spec(code, tmp_path, timeout_seconds=30.0))
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    assert gone(wait_for_pid(pidfile))
