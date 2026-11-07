# SPDX-License-Identifier: Apache-2.0
"""The host sees a strict-policy container read a watched volume file (#133 spike)."""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

from agentfence.core.sandbox_policy import SANDBOX_IMAGE, IsolationPolicy
from agentfence.runtime.containers import build_volume_create_argv
from agentfence.runtime.read_watch import FileAccess, ReadWatch
from agentfence.runtime.sandbox import remove_run_resources, run_container


def test_reads_inside_the_container_are_observed_from_the_host(podman: str) -> None:
    tag = uuid.uuid4().hex[:12]
    policy = IsolationPolicy(
        run_id=f"readwatch-{tag}",
        attempt_id="probe",
        image=SANDBOX_IMAGE,
        workspace_volume=f"agentfence-readwatch-{tag}",
    )
    subprocess.run(
        [podman, *build_volume_create_argv("podman", policy)], check=True, capture_output=True
    )
    try:
        mount = Path(
            subprocess.run(
                [
                    podman,
                    "volume",
                    "inspect",
                    policy.workspace_volume,
                    "--format",
                    "{{.Mountpoint}}",
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
        names = ("secret.txt", "README.md", "other.txt")
        for name in names:
            (mount / name).write_text("AF_FAKE_TOKEN_SPIKE\n")
            (mount / name).chmod(0o644)
        watched = {"SECRET": mount / "secret.txt", "README": mount / "README.md"}
        code = "open('/workspace/other.txt').read(); open('/workspace/secret.txt').read()"
        with ReadWatch(watched) as watch:
            command = ["python3", "-I", "-c", code]
            result = run_container(podman, policy, command, timeout=60, volume_ready=True)
            watch.drain(timeout=2)
            seen = watch.accesses()
        assert result.exit_code == 0
        assert seen == (
            FileAccess("README", opened=False, read=False),
            FileAccess("SECRET", opened=True, read=True),
        )
    finally:
        remove_run_resources(podman, policy.run_id)
