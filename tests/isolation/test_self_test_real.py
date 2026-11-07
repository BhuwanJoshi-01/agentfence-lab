# SPDX-License-Identifier: Apache-2.0
"""The isolation self-test against a real rootless Podman (AF-0205, AF-0206)."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime

from agentfence.core.vocabulary import CheckStatus
from agentfence.runtime.sandbox import self_test


def labelled(podman: str, kind: str) -> list[str]:
    args = ["ps", "--all", "-q"] if kind == "container" else ["volume", "ls", "-q"]
    out = subprocess.run(
        [podman, *args, "--filter", "label=io.agentfence.run"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [line for line in out.splitlines() if line]


def test_every_isolation_property_holds_and_nothing_is_left_behind(podman: str) -> None:
    before = set(labelled(podman, "container")), set(labelled(podman, "volume"))
    attestation = self_test(podman, "real", now=datetime.now(UTC))
    failed = {c.id: c.observed for c in attestation.checks if c.status is not CheckStatus.PASS}
    assert not failed, failed
    assert attestation.passed and attestation.verify_id()
    assert (set(labelled(podman, "container")), set(labelled(podman, "volume"))) == before
