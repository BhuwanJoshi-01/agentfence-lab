"""Stop-safe cleanup and stale-workspace reconciliation (AF-0407; docs/02 section 5).

Attempts delete their own workspace in a ``finally`` block, which covers normal exits,
exceptions and Ctrl-C. It cannot cover ``SIGKILL``, a power cut or a crash of the
interpreter, so ``agentfence test`` reconciles before every run: anything left under
``<state>/work`` belongs to an earlier run that never finished and is removed.

Safety rules: the work directory must be private (``0700``, ours, not a symlink); only
entries named like a canonical run UUID are touched; a symlinked entry is unlinked, never
followed; :func:`shutil.rmtree` uses file-descriptor based traversal on Linux, so it does
not follow symlinks inside the tree either. Anything else is left alone and reported.
"""

from __future__ import annotations

import os
import shutil
import stat
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from agentfence.storage.paths import ensure_private_dir


@dataclass(frozen=True, slots=True)
class Reconciliation:
    removed: tuple[str, ...] = ()
    skipped: tuple[str, ...] = field(default=())


def _is_run_id(name: str) -> bool:
    try:
        return str(uuid.UUID(name)) == name
    except ValueError:
        return False


def reconcile_stale_workspaces(work_dir: Path) -> Reconciliation:
    """Remove leftovers of interrupted runs from ``work_dir``. Never follows symlinks."""
    if not work_dir.exists():
        return Reconciliation()
    if not shutil.rmtree.avoids_symlink_attacks:  # pragma: no cover - Linux CPython: True
        return Reconciliation(skipped=("rmtree is not symlink-attack safe here",))
    ensure_private_dir(work_dir)
    removed: list[str] = []
    skipped: list[str] = []
    for entry in sorted(os.scandir(work_dir), key=lambda e: e.name):
        if not _is_run_id(entry.name):
            skipped.append(entry.name)
            continue
        info = entry.stat(follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode):
            Path(entry.path).unlink()
        elif stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid():
            shutil.rmtree(entry.path)
        else:
            skipped.append(entry.name)
            continue
        removed.append(entry.name)
    return Reconciliation(removed=tuple(removed), skipped=tuple(skipped))
