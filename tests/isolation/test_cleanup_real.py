# SPDX-License-Identifier: Apache-2.0
"""`agentfence cleanup` finds and removes a crashed run's sandbox resources (AF-0207)."""

from __future__ import annotations

import os
import signal
import subprocess
import time
import uuid
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentfence import cli
from agentfence.core.sandbox_policy import SANDBOX_IMAGE, IsolationPolicy
from agentfence.runtime.containers import build_run_argv, build_volume_create_argv
from agentfence.runtime.engine_probe import minimal_engine_env
from agentfence.runtime.reconcile import list_labelled
from agentfence.runtime.sandbox import remove_run_resources


def crashed_run(podman: str) -> IsolationPolicy:
    """Start a strict-policy container and SIGKILL its client, as a crash would."""
    tag = uuid.uuid4().hex[:12]
    policy = IsolationPolicy(
        run_id=f"crashed-{tag}",
        attempt_id="probe",
        image=SANDBOX_IMAGE,
        workspace_volume=f"agentfence-crashed-{tag}",
    )
    subprocess.run(
        [podman, *build_volume_create_argv("podman", policy)], check=True, capture_output=True
    )
    client = subprocess.Popen(
        [
            podman,
            *build_run_argv("podman", policy, ["python3", "-c", "import time; time.sleep(300)"]),
        ],
        env=minimal_engine_env(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not mine(podman, policy.run_id, "container"):
        time.sleep(0.2)
    os.killpg(client.pid, signal.SIGKILL)
    client.wait(10)
    return policy


def mine(podman: str, run_id: str, kind: str | None = None) -> list[str]:
    return [
        r.ref
        for r in list_labelled("podman", podman)
        if r.run_id == run_id and (kind is None or r.kind == kind)
    ]


def test_cleanup_spares_fresh_leftovers_and_removes_old_ones(
    podman: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTFENCE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)
    policy = crashed_run(podman)
    try:
        assert len(mine(podman, policy.run_id)) == 2  # the container and its volume

        fresh = CliRunner().invoke(cli.app, ["cleanup", "--apply", "--yes"])
        assert fresh.exit_code == 0, fresh.output
        assert policy.run_id not in fresh.stdout  # inside the grace period: maybe still running
        assert len(mine(podman, policy.run_id)) == 2

        later = time.time() + 3600
        monkeypatch.setattr(cli.time, "time", lambda: later)
        listed = CliRunner().invoke(cli.app, ["cleanup"])
        assert f"run {policy.run_id}" in listed.stdout
        removed = CliRunner().invoke(cli.app, ["cleanup", "--apply", "--yes"])
        assert removed.exit_code == 0, removed.output
        assert mine(podman, policy.run_id) == []
    finally:
        remove_run_resources(podman, policy.run_id)
