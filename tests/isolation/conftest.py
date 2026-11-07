# SPDX-License-Identifier: Apache-2.0
"""Real-engine isolation tests (docs/10 section 5).

Every test here starts real containers. It runs only on a host where rootless Podman is
verified the way ``agentfence doctor`` verifies it and the pinned image is present;
anywhere else it is skipped, and CI deselects it with ``-m "not isolation"``.
"""

from __future__ import annotations

import pytest

from agentfence.runtime.host_probe import gather_host_facts


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "tests/isolation/" in str(item.path).replace("\\", "/"):
            item.add_marker(pytest.mark.isolation)


@pytest.fixture(scope="session")
def podman() -> str:
    facts = gather_host_facts()
    path = facts.executables.get("podman")
    rootless = any(e.name == "podman" and e.rootless for e in facts.engines)
    if not (path and rootless and facts.sandbox_image_present):
        pytest.skip("needs rootless Podman with the pinned sandbox image")
    return path
