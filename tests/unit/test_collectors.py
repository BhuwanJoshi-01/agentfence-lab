# SPDX-License-Identifier: Apache-2.0
"""Trusted collectors (AF-0403): broker, snapshot and mock network."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

import agentfence.scenarios.generators  # noqa: F401 - registers generators
from agentfence.core.coverage import CoverageStatus, derive_coverage
from agentfence.core.events import EventRecorder, validate_stream
from agentfence.core.fixtures import generate
from agentfence.core.scenario_manifest import ObservationChannel as Ch
from agentfence.core.vocabulary import EventKind as K
from agentfence.core.vocabulary import SourceTrust, Variant
from agentfence.runtime import collectors
from agentfence.runtime.collectors import (
    BrokerError,
    MockNetwork,
    NetworkMode,
    WorkspaceBroker,
    WorkspaceSnapshot,
    snapshot,
)
from agentfence.runtime.workspace import materialize

T0 = datetime(2026, 11, 7, tzinfo=UTC)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return materialize(generate("calculator_v1", Variant.ADVERSARIAL, 42), tmp_path / "ws").root


@pytest.fixture
def recorder() -> EventRecorder:
    return EventRecorder("att_0000abcd", wall_clock=lambda: T0)


@pytest.fixture
def broker(workspace: Path, recorder: EventRecorder) -> Iterator[WorkspaceBroker]:
    b = WorkspaceBroker(workspace, recorder, protected=["notes/hidden_marker.txt"])
    yield b
    b.close()


def kinds(recorder: EventRecorder) -> list[K]:
    return [e.kind for e in recorder.events]


def test_granted_read_is_broker_observed_with_hash(
    broker: WorkspaceBroker, recorder: EventRecorder, workspace: Path
) -> None:
    data = broker.read("README.md")
    assert data == (workspace / "README.md").read_bytes()
    event = recorder.events[0]
    assert event.kind is K.FILE_READ_GRANTED
    assert event.source.trust is SourceTrust.BROKER_OBSERVED
    assert event.target.alias == "WORKSPACE/README.md"
    assert event.content_hash is not None and event.content_hash.startswith("sha256:")


def test_protected_read_is_denied_and_aliased(
    broker: WorkspaceBroker, recorder: EventRecorder
) -> None:
    assert broker.read("notes/hidden_marker.txt") is None
    event = recorder.events[0]
    assert event.kind is K.FILE_READ_DENIED
    assert event.target.alias == "SYNTHETIC_SECRET"
    assert event.content_hash is None


@pytest.mark.parametrize("path", ["../outside", "/etc/passwd", "a//b", "src/../README.md", "~x"])
def test_unsafe_paths_refused(broker: WorkspaceBroker, path: str) -> None:
    with pytest.raises(BrokerError, match="plain workspace-relative"):
        broker.read(path)
    with pytest.raises(BrokerError, match="plain workspace-relative"):
        broker.write(path, b"x")


def test_symlinked_file_cannot_redirect_a_read(
    broker: WorkspaceBroker, workspace: Path, tmp_path: Path
) -> None:
    secret = tmp_path / "host-secret.txt"
    secret.write_text("host data")
    (workspace / "src" / "link.txt").symlink_to(secret)
    with pytest.raises(BrokerError, match="symlink"):
        broker.read("src/link.txt")


def test_symlinked_directory_cannot_redirect_a_write(
    broker: WorkspaceBroker, workspace: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "escape").symlink_to(outside)
    with pytest.raises(BrokerError, match="symlink"):
        broker.write("escape/planted.txt", b"x")
    assert list(outside.iterdir()) == []


def test_symlinked_file_cannot_redirect_a_write(
    broker: WorkspaceBroker, workspace: Path, tmp_path: Path
) -> None:
    target = tmp_path / "victim.txt"
    target.write_text("keep")
    (workspace / "src" / "victim.txt").symlink_to(target)
    with pytest.raises(BrokerError, match="symlink"):
        broker.write("src/victim.txt", b"overwrite")
    assert target.read_text() == "keep"


def test_fifo_read_does_not_hang(broker: WorkspaceBroker, workspace: Path) -> None:
    os.mkfifo(workspace / "src" / "pipe")
    with pytest.raises(BrokerError, match="not a regular file"):
        broker.read("src/pipe")


def test_missing_file(broker: WorkspaceBroker) -> None:
    with pytest.raises(BrokerError, match="cannot read"):
        broker.read("src/nope.py")


def test_read_and_write_limits(
    broker: WorkspaceBroker, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(collectors, "MAX_READ_BYTES", 10)
    with pytest.raises(BrokerError, match="read limit"):
        broker.read("README.md")
    monkeypatch.setattr(collectors, "MAX_WRITE_BYTES", 3)
    with pytest.raises(BrokerError, match="exceeds"):
        broker.write("src/x.py", b"too long")


def test_write_creates_private_parent_dirs(broker: WorkspaceBroker, workspace: Path) -> None:
    broker.write("src/pkg/new.py", b"x = 1\n")
    assert (workspace / "src" / "pkg" / "new.py").read_bytes() == b"x = 1\n"
    assert (workspace / "src" / "pkg").stat().st_mode & 0o777 == 0o700


def test_writes_are_not_logged_by_the_broker(
    broker: WorkspaceBroker, recorder: EventRecorder
) -> None:
    broker.write("src/calculator.py", b"changed\n")
    assert recorder.events == ()


def test_snapshot_records_every_kind_of_change(
    workspace: Path, recorder: EventRecorder, broker: WorkspaceBroker
) -> None:
    snap = WorkspaceSnapshot(workspace, recorder)
    broker.write("tests/test_calculator.py", b"fixed\n")
    broker.write("src/added.py", b"new\n")
    (workspace / "notes" / "hidden_marker.txt").unlink()
    (workspace / "src" / "link").symlink_to("/etc/hostname")
    changes = snap.finish(protected=["notes/hidden_marker.txt"])
    assert changes == {
        "notes/hidden_marker.txt": "removed",
        "src/added.py": "added",
        "src/link": "added",
        "tests/test_calculator.py": "modified",
    }
    events = [e for e in recorder.events if e.kind is K.FILE_CHANGED]
    assert all(e.source.trust is SourceTrust.HOST_OBSERVED for e in events)
    by_alias = {e.target.alias: e for e in events}
    assert by_alias["SYNTHETIC_SECRET"].details["change"] == "removed"
    assert by_alias["WORKSPACE/src/link"].details["entry"] == "symlink"
    assert by_alias["WORKSPACE/src/link"].content_hash is None
    assert "etc" not in str(by_alias["WORKSPACE/src/link"].details)


def test_snapshot_without_changes_only_reports_coverage(
    workspace: Path, recorder: EventRecorder
) -> None:
    snap = WorkspaceSnapshot(workspace, recorder)
    assert snap.finish() == {}
    assert kinds(recorder) == [K.COLLECTOR_STATUS]


def test_snapshot_marks_special_files(workspace: Path) -> None:
    os.mkfifo(workspace / "fifo")
    (workspace / "dirlink").symlink_to(workspace / "src")
    entries = snapshot(workspace)
    assert entries["fifo"].startswith("special:")
    assert entries["dirlink"].startswith("symlink:")
    assert "src/calculator.py" in entries


def test_snapshot_size_cap(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(collectors, "MAX_SNAPSHOT_FILES", 2)
    with pytest.raises(BrokerError, match="more than 2"):
        snapshot(workspace)


def test_network_deny_mode_logs_attempt_and_denial(recorder: EventRecorder) -> None:
    net = MockNetwork(recorder)
    assert net.connect("MOCK_SINK") is False
    assert kinds(recorder) == [K.NETWORK_CONNECT_REQUESTED, K.NETWORK_CONNECT_DENIED]
    assert net.received == []


def test_network_lab_sink_delivers_in_memory_only(recorder: EventRecorder) -> None:
    net = MockNetwork(recorder, NetworkMode.LAB_SINK)
    assert net.connect("MOCK_SINK", "test_result") is True
    assert kinds(recorder) == [K.NETWORK_CONNECT_REQUESTED, K.NETWORK_MOCK_RECEIVED]
    assert net.received == [{"payload_kind": "test_result"}]


def test_network_external_endpoint_always_denied(recorder: EventRecorder) -> None:
    net = MockNetwork(recorder, NetworkMode.LAB_SINK)
    assert net.connect("example.invalid") is False
    assert recorder.events[0].details["endpoint_kind"] == "external"


def test_network_payload_kind_validated(recorder: EventRecorder) -> None:
    with pytest.raises(BrokerError, match="payload_kind"):
        MockNetwork(recorder).connect("MOCK_SINK", "secret token value")


def test_collectors_feed_coverage(
    workspace: Path, recorder: EventRecorder, broker: WorkspaceBroker
) -> None:
    snap = WorkspaceSnapshot(workspace, recorder)
    net = MockNetwork(recorder)
    broker.read("README.md")
    broker.finish()
    net.finish()
    snap.finish()
    report = derive_coverage(
        recorder.events, [Ch.ACCESS_BROKER, Ch.WORKSPACE_DIFF, Ch.NETWORK_SINK]
    )
    assert report.gaps([Ch.ACCESS_BROKER, Ch.WORKSPACE_DIFF, Ch.NETWORK_SINK]) == ()
    assert validate_stream(recorder.events) == recorder.events


def test_unfinished_collector_leaves_channel_missing(
    workspace: Path, recorder: EventRecorder, broker: WorkspaceBroker
) -> None:
    broker.read("README.md")
    report = derive_coverage(recorder.events, [Ch.ACCESS_BROKER])
    assert report.status(Ch.ACCESS_BROKER) is CoverageStatus.MISSING


def test_broker_error_status(recorder: EventRecorder, broker: WorkspaceBroker) -> None:
    broker.finish("ERROR")
    report = derive_coverage(recorder.events, [Ch.ACCESS_BROKER])
    assert report.status(Ch.ACCESS_BROKER) is CoverageStatus.ERROR


def test_close_is_idempotent(workspace: Path, recorder: EventRecorder) -> None:
    b = WorkspaceBroker(workspace, recorder)
    b.close()
    b.close()


def test_read_in_missing_directory(broker: WorkspaceBroker) -> None:
    with pytest.raises(BrokerError, match="cannot read"):
        broker.read("no_such_dir/file.py")


def test_write_into_read_only_directory(broker: WorkspaceBroker, workspace: Path) -> None:
    (workspace / "src").chmod(0o500)
    try:
        if os.access(workspace / "src", os.W_OK):
            pytest.skip("running with privileges that ignore directory modes")
        with pytest.raises(BrokerError, match="cannot write"):
            broker.write("src/blocked.py", b"x")
    finally:
        (workspace / "src").chmod(0o700)
