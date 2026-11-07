# SPDX-License-Identifier: Apache-2.0
"""Unprivileged fanotify read watch (#133). Real fanotify on temp files, no container.

Skipped (never passed) where the kernel refuses unprivileged fanotify.
"""

from __future__ import annotations

import ctypes
import os
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from agentfence.runtime.read_watch import (
    FAN_ACCESS,
    FAN_CLOSE_NOWRITE,
    FAN_OPEN,
    FileAccess,
    ReadWatch,
    ReadWatchUnavailableError,
    parse_masks,
)


def other_process(code: str) -> None:
    subprocess.run([sys.executable, "-I", "-c", code], check=True)


def watch(files: dict[str, Path]) -> ReadWatch:
    try:
        return ReadWatch(files)
    except ReadWatchUnavailableError as exc:
        pytest.skip(f"unprivileged fanotify unavailable here: {exc}")


@pytest.fixture
def files(tmp_path: Path) -> dict[str, Path]:
    paths = {"SECRET": tmp_path / "secret.txt", "README": tmp_path / "README.md"}
    for path in paths.values():
        path.write_text("AF_FAKE_TOKEN_TEST\n")
    return paths


def test_a_read_by_another_process_is_observed(files: dict[str, Path], tmp_path: Path) -> None:
    unwatched = tmp_path / "other.txt"
    unwatched.write_text("x")
    with watch({"SECRET": files["SECRET"]}) as w:
        other_process(f"open({str(unwatched)!r}).read(); open({str(files['SECRET'])!r}).read()")
        w.drain(timeout=2)
        assert w.accesses() == (FileAccess("SECRET", opened=True, read=True),)


def test_open_without_read_and_untouched_files(files: dict[str, Path]) -> None:
    with watch(files) as w:
        other_process(f"open({str(files['README'])!r}).close()")
        w.drain(timeout=2)
        assert w.accesses() == (
            FileAccess("README", opened=True, read=False),
            FileAccess("SECRET", opened=False, read=False),
        )


def test_a_denied_open_is_invisible(files: dict[str, Path]) -> None:
    """The documented limit: fanotify reports completed opens only."""
    if os.geteuid() == 0:
        pytest.skip("root ignores file modes")
    with watch({"SECRET": files["SECRET"]}) as w:  # marking needs read permission
        files["SECRET"].chmod(0)
        code = f"""
try:
    open({str(files["SECRET"])!r}).read()
except PermissionError:
    pass
"""
        other_process(code)
        w.drain(timeout=0.5)
        assert w.accesses() == (FileAccess("SECRET", opened=False, read=False),)


def test_parse_masks() -> None:
    def event(mask: int, length: int = 24) -> bytes:
        return struct.pack("=IBBHQii", length, 3, 0, 24, mask, -1, 0) + b"\0" * (length - 24)

    assert parse_masks(event(FAN_OPEN) + event(FAN_ACCESS, 40)) == FAN_OPEN | FAN_ACCESS
    assert parse_masks(event(FAN_CLOSE_NOWRITE)[:10]) == 0  # truncated
    assert parse_masks(struct.pack("=IBBHQii", 0, 3, 0, 24, FAN_OPEN, -1, 0)) == 0  # bad length


class FakeLibc:
    def __init__(self, init: int = -1, mark: int = 0) -> None:
        self.init, self.mark = init, mark
        self.opened: list[int] = []

    def __call__(self) -> FakeLibc:
        return self

    def fanotify_init(self, flags: int, event_flags: int) -> int:
        if self.init < 0:
            ctypes.set_errno(1)
            return -1
        fd = os.open(os.devnull, os.O_RDONLY)
        self.opened.append(fd)
        return fd

    def fanotify_mark(self, *args: object) -> int:
        ctypes.set_errno(2)
        return self.mark


def open_fds() -> int:
    return len(list(Path("/proc/self/fd").iterdir()))


@pytest.mark.parametrize(
    ("init", "mark", "message"), [(-1, 0, "fanotify_init"), (0, -1, "fanotify_mark")]
)
def test_failures_raise_and_leak_no_descriptor(
    files: dict[str, Path], init: int, mark: int, message: str
) -> None:
    before = open_fds()
    with pytest.raises(ReadWatchUnavailableError, match=message):
        ReadWatch(files, libc=FakeLibc(init, mark))  # type: ignore[arg-type]
    assert open_fds() == before


@pytest.mark.parametrize("count", [0, 33])
def test_watch_size_is_bounded(tmp_path: Path, count: int) -> None:
    with pytest.raises(ValueError, match="between 1 and 32"):
        ReadWatch({f"F{i}": tmp_path / str(i) for i in range(count)})
