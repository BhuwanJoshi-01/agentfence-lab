# SPDX-License-Identifier: Apache-2.0
"""Host-side read watch on chosen files, without privileges (#133; docs/12 Spike B).

Inside a sandbox an agent opens files directly, so nothing trusted sees the read the way
the in-process broker does. Since Linux 5.13 an *unprivileged* process can use fanotify
(``FAN_REPORT_FID``) to watch individual inodes. A rootless Podman volume's files live on
the host, so a watch placed on, say, a synthetic secret in the attempt's volume fires when
the sandboxed process opens or reads it. The container needs no socket, device, extra
capability or network for this: the observation happens entirely on the host.

Verified on Ubuntu 26.04, kernel 7.0, rootless Podman 5.7.0: a strict-policy container's
read of a watched file produced an open+read event, and a read of an unwatched file in
the same volume produced none (``tests/isolation/test_read_watch_real.py``).

What it can and cannot tell (stated so callers do not over-claim):

* It reports that the watched file was **opened** and/or **read**, i.e. a *completed*
  access. A *denied* open (``EACCES``) produces no event at all, so an attempt that the
  file mode blocks is invisible. Callers keep watched files readable when the read
  itself is the evidence.
* Unprivileged events carry no process ID. Anything that touches the file while the
  watch is active is reported, including AgentFence itself; arm the watch only for the
  agent phase, and take snapshots before arming or after closing.
* Marking a file requires read permission on it, for an unprivileged watcher.
* One fanotify group per file keeps every event attributable to its file without
  decoding file handles. The number of watched files is small and fixed per attempt.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import select
import struct
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

FAN_ACCESS: Final = 0x01
FAN_CLOSE_NOWRITE: Final = 0x10
FAN_OPEN: Final = 0x20
_FAN_CLOEXEC: Final = 0x01
_FAN_NONBLOCK: Final = 0x02
_FAN_REPORT_FID: Final = 0x200
_FAN_MARK_ADD: Final = 0x01
_AT_FDCWD: Final = -100
_METADATA: Final = struct.Struct("=IBBHQii")
_WATCHED: Final = FAN_OPEN | FAN_ACCESS | FAN_CLOSE_NOWRITE
MAX_WATCHED_FILES: Final = 32


class ReadWatchUnavailableError(RuntimeError):
    """fanotify is not usable here (old kernel, policy or file system). Never guess."""


@dataclass(frozen=True, slots=True)
class FileAccess:
    alias: str
    opened: bool
    read: bool


def _libc() -> ctypes.CDLL:
    lib = ctypes.CDLL(ctypes.util.find_library("c") or None, use_errno=True)
    lib.fanotify_init.argtypes = [ctypes.c_uint, ctypes.c_uint]
    lib.fanotify_init.restype = ctypes.c_int
    lib.fanotify_mark.argtypes = [
        ctypes.c_int,
        ctypes.c_uint,
        ctypes.c_uint64,
        ctypes.c_int,
        ctypes.c_char_p,
    ]
    lib.fanotify_mark.restype = ctypes.c_int
    return lib


def parse_masks(buffer: bytes) -> int:
    """OR of the event masks in a buffer read from a fanotify descriptor."""
    mask = offset = 0
    while offset + _METADATA.size <= len(buffer):
        length, _version, _reserved, _meta, event_mask, _fd, _pid = _METADATA.unpack_from(
            buffer, offset
        )
        if length < _METADATA.size:
            break
        mask |= event_mask
        offset += length
    return mask


def _watch_one(lib: ctypes.CDLL, alias: str, path: Path) -> int:
    """One fanotify group watching one file; the descriptor is closed if marking fails."""
    fd = int(lib.fanotify_init(_FAN_CLOEXEC | _FAN_NONBLOCK | _FAN_REPORT_FID, os.O_RDONLY))
    if fd < 0:
        raise ReadWatchUnavailableError(f"fanotify_init: {os.strerror(ctypes.get_errno())}")
    if lib.fanotify_mark(fd, _FAN_MARK_ADD, _WATCHED, _AT_FDCWD, os.fsencode(path)) < 0:
        error = os.strerror(ctypes.get_errno())
        os.close(fd)
        raise ReadWatchUnavailableError(f"fanotify_mark {alias}: {error}")
    return fd


class ReadWatch:
    """Watch a fixed set of host files for opens and reads. Use as a context manager."""

    def __init__(
        self, files: Mapping[str, Path], *, libc: Callable[[], ctypes.CDLL] = _libc
    ) -> None:
        if not 0 < len(files) <= MAX_WATCHED_FILES:
            raise ValueError(f"watch between 1 and {MAX_WATCHED_FILES} files")
        lib = libc()
        self._fds: dict[str, int] = {}
        self._masks: dict[str, int] = dict.fromkeys(files, 0)
        try:
            for alias, path in files.items():
                self._fds[alias] = _watch_one(lib, alias, path)
        except BaseException:
            self.close()
            raise

    def drain(self, timeout: float = 0.0) -> None:
        """Collect pending events, waiting up to ``timeout`` seconds for the first."""
        ready, _, _ = select.select(list(self._fds.values()), [], [], timeout)
        for alias, fd in self._fds.items():
            if fd not in ready and timeout > 0:
                continue
            while True:
                try:
                    chunk = os.read(fd, 8192)
                except BlockingIOError:
                    break
                if not chunk:
                    break
                self._masks[alias] |= parse_masks(chunk)

    def accesses(self) -> tuple[FileAccess, ...]:
        """Every watched file with whether it was opened and read so far."""
        self.drain()
        return tuple(
            FileAccess(alias, opened=bool(mask & FAN_OPEN), read=bool(mask & FAN_ACCESS))
            for alias, mask in sorted(self._masks.items())
        )

    def close(self) -> None:
        for fd in self._fds.values():
            os.close(fd)
        self._fds = {}

    def __enter__(self) -> ReadWatch:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
