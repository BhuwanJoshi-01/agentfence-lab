"""Trusted collectors for the Tier 0 mock lab (AF-0403; docs/03 section 6, docs/07 section 2).

Three independent observation channels, all recording into one attempt's
:class:`~agentfence.core.events.EventRecorder` so sequence numbers stay contiguous:

* :class:`WorkspaceBroker` (``access_broker``): the only way the mock agent reads or
  writes workspace files. Reads of protected synthetic assets are **denied** and logged
  (``file.read_denied``); other reads are granted and logged with a content hash
  (``file.read_granted``). Writes are applied but not logged here: modifications are
  proven by the snapshot collector, never by the broker's say-so.
* :class:`WorkspaceSnapshot` (``workspace_diff``): before/after SHA-256 of every file;
  each difference becomes a host-observed ``file.changed`` event. A digest proves a
  change, never a read (docs/02 section 8).
* :class:`MockNetwork` (``network_sink``): there are no real sockets. A connection
  attempt is either denied (``network.connect_denied``) or delivered to the in-memory
  lab sink (``network.mock_received``), depending on the scenario policy.

Each collector reports its own coverage with ``finish()``; a collector that never
finishes leaves its channel ``MISSING`` and the verdict ``INCONCLUSIVE``.

File access walks the path one component at a time with ``O_NOFOLLOW`` directory file
descriptors, so a symlink planted anywhere in the workspace cannot redirect a read or
write outside it (docs/03 section 8, TOCTOU).
"""

from __future__ import annotations

import errno
import hashlib
import os
import re
import stat
from collections.abc import Iterable, Mapping
from enum import StrEnum
from pathlib import Path
from typing import Final

from agentfence.core.events import (
    ActorType,
    Decision,
    EventActor,
    EventRecorder,
    EventSource,
    EventTarget,
    TargetType,
)
from agentfence.core.scenario_manifest import ObservationChannel
from agentfence.core.vocabulary import EventKind, SourceTrust

PROTECTED_ALIAS: Final = "SYNTHETIC_SECRET"
MAX_READ_BYTES: Final = 1024 * 1024
MAX_WRITE_BYTES: Final = 1024 * 1024
MAX_SNAPSHOT_FILES: Final = 2000
_PATH: Final = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")
_DIR_FLAGS: Final = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC

AGENT: Final = EventActor(type=ActorType.AGENT_PROCESS, alias="AGENT")
HOST: Final = EventActor(type=ActorType.COLLECTOR, alias="HOST")


class BrokerError(RuntimeError):
    """A requested workspace operation was refused for safety reasons."""


def check_workspace_path(path: str) -> str:
    if not _PATH.fullmatch(path) or any(seg in {".", ".."} for seg in path.split("/")):
        raise BrokerError(f"path {path!r} is not a plain workspace-relative path")
    return path


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _open_dir_chain(root_fd: int, parts: list[str], *, create: bool) -> int:
    """Walk ``parts`` under ``root_fd`` without following symlinks; returns a dir fd."""
    current = os.dup(root_fd)
    try:
        for part in parts:
            try:
                nxt = os.open(part, _DIR_FLAGS, dir_fd=current)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, 0o700, dir_fd=current)
                nxt = os.open(part, _DIR_FLAGS, dir_fd=current)
            os.close(current)
            current = nxt
    except BaseException:
        os.close(current)
        raise
    return current


def _status(
    recorder: EventRecorder, component: str, channel: ObservationChannel, value: str
) -> None:
    recorder.record(
        EventKind.COLLECTOR_STATUS,
        source=EventSource(component=component, trust=SourceTrust.HOST_OBSERVED),
        actor=HOST,
        target=EventTarget(type=TargetType.COLLECTOR, alias="HOST"),
        details={"channel": channel.value, "status": value},
    )


class WorkspaceBroker:
    """Instrumented file access for the mock agent inside one materialized workspace."""

    COMPONENT: Final = "fixture_access_broker"

    def __init__(
        self, root: Path, recorder: EventRecorder, *, protected: Iterable[str] = ()
    ) -> None:
        self.root = root
        self.recorder = recorder
        self.protected = frozenset(check_workspace_path(p) for p in protected)
        self._source = EventSource(component=self.COMPONENT, trust=SourceTrust.BROKER_OBSERVED)
        self._root_fd = os.open(root, _DIR_FLAGS)

    def close(self) -> None:
        if self._root_fd >= 0:
            os.close(self._root_fd)
            self._root_fd = -1

    def _target(self, path: str) -> EventTarget:
        if path in self.protected:
            return EventTarget(type=TargetType.SYNTHETIC_FILE, alias=PROTECTED_ALIAS)
        return EventTarget(type=TargetType.WORKSPACE_FILE, alias=f"WORKSPACE/{path}")

    def read(self, path: str) -> bytes | None:
        """Read a file for the agent. Protected assets are denied; returns ``None`` then."""
        check_workspace_path(path)
        if path in self.protected:
            self.recorder.record(
                EventKind.FILE_READ_DENIED,
                source=self._source,
                actor=AGENT,
                target=self._target(path),
                decision=Decision.DENIED,
                details={"reason": "outside_authorized_task_scope"},
            )
            return None
        *dirs, name = path.split("/")
        try:
            dir_fd = _open_dir_chain(self._root_fd, dirs, create=False)
            try:
                fd = os.open(
                    name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=dir_fd
                )
            finally:
                os.close(dir_fd)
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise BrokerError(f"refusing to follow a symlink while reading {path}") from exc
            raise BrokerError(f"cannot read {path}: {exc.strerror}") from exc
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise BrokerError(f"{path} is not a regular file")
            data = handle.read(MAX_READ_BYTES + 1)
        if len(data) > MAX_READ_BYTES:
            raise BrokerError(f"{path} exceeds the {MAX_READ_BYTES}-byte read limit")
        self.recorder.record(
            EventKind.FILE_READ_GRANTED,
            source=self._source,
            actor=AGENT,
            target=self._target(path),
            decision=Decision.ALLOWED,
            content_hash=_sha256(data),
        )
        return data

    def write(self, path: str, data: bytes) -> None:
        """Apply a write for the agent. Evidence of the change comes from the snapshot."""
        check_workspace_path(path)
        if len(data) > MAX_WRITE_BYTES:
            raise BrokerError(f"write to {path} exceeds {MAX_WRITE_BYTES} bytes")
        *dirs, name = path.split("/")
        try:
            dir_fd = _open_dir_chain(self._root_fd, dirs, create=True)
            try:
                flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC
                fd = os.open(name, flags, 0o644, dir_fd=dir_fd)
            finally:
                os.close(dir_fd)
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise BrokerError(f"refusing to follow a symlink while writing {path}") from exc
            raise BrokerError(f"cannot write {path}: {exc.strerror}") from exc
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)

    def finish(self, status: str = "COMPLETE") -> None:
        _status(self.recorder, self.COMPONENT, ObservationChannel.ACCESS_BROKER, status)


def _link_marker(path: Path) -> str:
    # Hash the link target: it may name a host path and must never be stored raw.
    return "symlink:" + hashlib.sha256(str(path.readlink()).encode()).hexdigest()[:16]


def snapshot(root: Path) -> dict[str, str]:
    """Map every workspace entry to ``sha256:<hex>`` (files) or a ``symlink:``/``special:``
    marker. Never follows symlinks; refuses absurdly large trees."""
    result: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(dirpath)
        for name in sorted(dirnames):
            full = base / name
            if full.is_symlink():
                result[full.relative_to(root).as_posix()] = _link_marker(full)
        for name in sorted(filenames):
            full = base / name
            rel = full.relative_to(root).as_posix()
            info = full.lstat()
            if stat.S_ISLNK(info.st_mode):
                result[rel] = _link_marker(full)
            elif stat.S_ISREG(info.st_mode):
                fd = os.open(full, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
                with os.fdopen(fd, "rb") as handle:
                    result[rel] = _sha256(handle.read())
            else:
                result[rel] = "special:" + stat.filemode(info.st_mode)
            if len(result) > MAX_SNAPSHOT_FILES:
                raise BrokerError(f"workspace has more than {MAX_SNAPSHOT_FILES} entries")
    return result


class WorkspaceSnapshot:
    """Before/after digest collector for the ``workspace_diff`` channel."""

    COMPONENT: Final = "workspace_snapshot"

    def __init__(self, root: Path, recorder: EventRecorder) -> None:
        self.root = root
        self.recorder = recorder
        self.before = snapshot(root)
        self._source = EventSource(component=self.COMPONENT, trust=SourceTrust.HOST_OBSERVED)

    def finish(self, *, protected: Iterable[str] = ()) -> dict[str, str]:
        """Record ``file.changed`` per difference, then report coverage. Returns changes."""
        protected_set = frozenset(protected)
        after = snapshot(self.root)
        changes: dict[str, str] = {}
        for path in sorted(set(self.before) | set(after)):
            old, new = self.before.get(path), after.get(path)
            if old == new:
                continue
            change = "added" if old is None else "removed" if new is None else "modified"
            changes[path] = change
            target = (
                EventTarget(type=TargetType.SYNTHETIC_FILE, alias=PROTECTED_ALIAS)
                if path in protected_set
                else EventTarget(type=TargetType.WORKSPACE_FILE, alias=f"WORKSPACE/{path}")
            )
            details: dict[str, str | int | bool | None] = {"change": change}
            if new is not None and not new.startswith("sha256:"):
                details["entry"] = new.split(":", 1)[0]  # symlink / special, never its target
            self.recorder.record(
                EventKind.FILE_CHANGED,
                source=self._source,
                actor=AGENT,
                target=target,
                details=details,
                content_hash=new if new is not None and new.startswith("sha256:") else None,
            )
        _status(self.recorder, self.COMPONENT, ObservationChannel.WORKSPACE_DIFF, "COMPLETE")
        return changes


class NetworkMode(StrEnum):
    DENY = "deny"
    """Tier 0 default: every attempt is blocked and logged."""
    LAB_SINK = "lab_sink"
    """Attempts to the mock sink alias are delivered in memory; nothing leaves the process."""


class MockNetwork:
    """Network collector with no real sockets (``network_sink`` channel)."""

    COMPONENT: Final = "mock_network"
    SINK_ALIAS: Final = "MOCK_SINK"

    def __init__(self, recorder: EventRecorder, mode: NetworkMode = NetworkMode.DENY) -> None:
        self.recorder = recorder
        self.mode = mode
        self.received: list[Mapping[str, str]] = []
        self._source = EventSource(component=self.COMPONENT, trust=SourceTrust.HOST_OBSERVED)

    def connect(self, endpoint: str, payload_kind: str = "report") -> bool:
        """Simulate an outbound attempt. Returns whether the lab sink received it."""
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", payload_kind):
            raise BrokerError("payload_kind must be a short identifier")
        target = EventTarget(type=TargetType.NETWORK_ENDPOINT, alias=self.SINK_ALIAS)
        details = {"endpoint_kind": "lab_sink" if endpoint == self.SINK_ALIAS else "external"}
        self.recorder.record(
            EventKind.NETWORK_CONNECT_REQUESTED,
            source=self._source,
            actor=AGENT,
            target=target,
            details=details,
        )
        if self.mode is NetworkMode.LAB_SINK and endpoint == self.SINK_ALIAS:
            self.received.append({"payload_kind": payload_kind})
            self.recorder.record(
                EventKind.NETWORK_MOCK_RECEIVED,
                source=self._source,
                actor=AGENT,
                target=target,
                details={"payload_kind": payload_kind},
            )
            return True
        self.recorder.record(
            EventKind.NETWORK_CONNECT_DENIED,
            source=self._source,
            actor=AGENT,
            target=target,
            decision=Decision.DENIED,
            details={**details, "reason": "egress_not_permitted"},
        )
        return False

    def finish(self, status: str = "COMPLETE") -> None:
        _status(self.recorder, self.COMPONENT, ObservationChannel.NETWORK_SINK, status)
