"""Materialize a fixture into a private, fresh workspace directory (AF-0302).

The workspace root must not exist yet (or be an empty private directory we own). Every
directory is created ``0700`` and every file with ``O_CREAT | O_EXCL | O_NOFOLLOW``, so a
pre-planted file or symlink makes materialization fail instead of being written through.
Protected synthetic assets are ``0600``; other files ``0644`` inside the private root.

After writing, files are re-read without following symlinks and hashed; the result must
match the fixture digest or materialization fails.
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final

from agentfence.core.fixtures import Fixture
from agentfence.storage.paths import PRIVATE_DIR_MODE, ensure_private_dir

PROTECTED_MODE: Final = 0o600
REGULAR_MODE: Final = 0o644


class WorkspaceError(RuntimeError):
    """The workspace could not be created safely."""


@dataclass(frozen=True, slots=True)
class MaterializedWorkspace:
    root: Path
    digest: str
    hashes: MappingProxyType[str, str]


def _write_new(path: Path, data: bytes, mode: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(path, flags, mode)
    try:
        os.fchmod(fd, mode)
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]
    finally:
        os.close(fd)


def _read_nofollow(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise WorkspaceError(f"{path} is not a regular file")
        return handle.read()


def materialize(fixture: Fixture, root: Path) -> MaterializedWorkspace:
    if root.exists() and not root.is_symlink() and root.is_dir() and any(root.iterdir()):
        raise WorkspaceError(f"workspace root {root} is not empty")
    try:
        ensure_private_dir(root)
    except PermissionError as exc:
        raise WorkspaceError(str(exc)) from exc
    for item in fixture.files:
        target = root / item.path
        parent = target.parent
        missing: list[Path] = []
        while parent != root and not parent.exists():
            missing.append(parent)
            parent = parent.parent
        for directory in reversed(missing):
            directory.mkdir(mode=PRIVATE_DIR_MODE)
        try:
            _write_new(target, item.content, PROTECTED_MODE if item.protected else REGULAR_MODE)
        except FileExistsError as exc:
            raise WorkspaceError(f"refusing to overwrite existing {item.path}") from exc
        except OSError as exc:
            raise WorkspaceError(f"cannot create {item.path}: {exc.strerror}") from exc
    hashes = {
        item.path: hashlib.sha256(_read_nofollow(root / item.path)).hexdigest()
        for item in fixture.files
    }
    expected = {item.path: item.sha256 for item in fixture.files}
    if hashes != expected:
        changed = sorted(p for p in expected if hashes.get(p) != expected[p])
        raise WorkspaceError(f"materialized files do not match the fixture: {changed}")
    return MaterializedWorkspace(root=root, digest=fixture.digest, hashes=MappingProxyType(hashes))
