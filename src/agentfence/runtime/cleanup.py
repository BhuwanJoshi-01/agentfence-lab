# SPDX-License-Identifier: Apache-2.0
"""Stop-safe cleanup and stale-workspace reconciliation (AF-0407; docs/02 section 5).

Attempts delete their own workspace in a ``finally`` block, which covers normal exits,
exceptions and Ctrl-C. It cannot cover ``SIGKILL``, a power cut or a crash of the
interpreter, so ``agentfence test`` reconciles before every run: anything left under
``<state>/work`` belongs to an earlier run that never finished and is removed.

The same module implements retention (``agentfence cleanup``): runs older than the
configured retention are purged from the database in one transaction (the only path that
may delete evidence) and their folders removed, together with run folders that have no
database record. Dry run is the default. A folder without a record is only treated as
an orphan once it is older than :data:`ORPHAN_GRACE`, because ``agentfence test`` writes the
run folder a moment before it inserts the database row and a concurrent cleanup must not
take it.

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
from datetime import datetime, timedelta
from pathlib import Path

from agentfence.storage.db import RunStore
from agentfence.storage.paths import ensure_private_dir

ORPHAN_GRACE = timedelta(hours=1)


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
        if _remove_owned(Path(entry.path)):
            removed.append(entry.name)
        else:
            skipped.append(entry.name)
    return Reconciliation(removed=tuple(removed), skipped=tuple(skipped))


def _remove_owned(path: Path) -> bool:
    """Unlink a symlink (never follow it) or remove a directory we own. Else do nothing."""
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        path.unlink()
        return True
    if stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid():
        shutil.rmtree(path)
        return True
    return False


@dataclass(frozen=True, slots=True)
class RetentionPlan:
    """What a retention cleanup would delete. Computed without changing anything."""

    cutoff: datetime
    expired_runs: tuple[str, ...]
    orphan_run_dirs: tuple[str, ...]


def plan_retention(
    runs_dir: Path, store: RunStore, *, older_than_days: int, now: datetime
) -> RetentionPlan:
    """Runs started before ``now - older_than_days`` plus settled folders with no record."""
    if older_than_days < 0:
        raise ValueError("older_than_days must be zero or positive")
    cutoff = now - timedelta(days=older_than_days)
    expired = tuple(r.run_id for r in store.runs_started_before(cutoff.isoformat()))
    known = store.all_run_ids()
    orphans: list[str] = []
    settled = (now - ORPHAN_GRACE).timestamp()
    if runs_dir.exists():
        for entry in sorted(os.scandir(runs_dir), key=lambda e: e.name):
            if not _is_run_id(entry.name) or entry.name in known:
                continue
            if entry.stat(follow_symlinks=False).st_mtime < settled:
                orphans.append(entry.name)
    return RetentionPlan(cutoff=cutoff, expired_runs=expired, orphan_run_dirs=tuple(orphans))


def apply_retention(plan: RetentionPlan, runs_dir: Path, store: RunStore) -> RetentionPlan:
    """Delete database rows first (atomically), then the matching run folders."""
    if not shutil.rmtree.avoids_symlink_attacks:  # pragma: no cover - Linux CPython: True
        raise RuntimeError("refusing to delete: rmtree is not symlink-attack safe here")
    store.purge_runs(list(plan.expired_runs))
    for run_id in (*plan.expired_runs, *plan.orphan_run_dirs):
        target = runs_dir / run_id
        if target.exists() or target.is_symlink():
            _remove_owned(target)
    return plan
