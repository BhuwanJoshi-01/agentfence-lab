# SPDX-License-Identifier: Apache-2.0
"""Atomic, private file writes (AF-0105).

Reports and state files are written to a temporary file in the destination directory,
flushed and fsynced, then moved into place with :func:`os.replace`. A crash leaves
either the old file or the new file, never a partial one, and the temporary file is
removed on failure. Files are created ``0600`` from the start (no chmod-after-write
window).
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from agentfence.storage.paths import PRIVATE_FILE_MODE


def atomic_write_bytes(path: Path, data: bytes, *, mode: int = PRIVATE_FILE_MODE) -> Path:
    """Atomically replace ``path`` with ``data``. The parent directory must exist."""
    if mode & 0o077:
        raise ValueError(f"refusing to write {path} with group/other access (mode {mode:04o})")
    directory = path.parent
    if not directory.is_dir():
        raise FileNotFoundError(f"destination directory does not exist: {directory}")
    if path.is_symlink():
        raise PermissionError(f"refusing to replace symlink {path}")
    if path.is_dir():
        raise IsADirectoryError(f"refusing to replace directory {path}")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=directory)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        raise
    _fsync_directory(directory)
    return path


def atomic_write_text(path: Path, text: str, *, mode: int = PRIVATE_FILE_MODE) -> Path:
    return atomic_write_bytes(path, text.encode("utf-8"), mode=mode)


def atomic_write_json(path: Path, payload: Any, *, mode: int = PRIVATE_FILE_MODE) -> Path:
    """Deterministic JSON: sorted keys, UTF-8, trailing newline. Serializes before opening."""
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False)
    return atomic_write_text(path, text + "\n", mode=mode)


def _fsync_directory(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
