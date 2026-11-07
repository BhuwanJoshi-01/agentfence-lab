# SPDX-License-Identifier: Apache-2.0
"""Every source, test, manifest and migration file declares its license (AF-0704)."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TAG = "SPDX-License-Identifier: Apache-2.0"
FILES = sorted(
    [
        *ROOT.joinpath("src").rglob("*.py"),
        *ROOT.joinpath("tests").rglob("*.py"),
        *ROOT.joinpath("src/agentfence/scenarios").rglob("*.yaml"),
        *ROOT.joinpath("src/agentfence/storage/migrations").rglob("*.sql"),
    ]
)


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_file_starts_with_spdx_identifier(path: Path) -> None:
    first = path.read_text(encoding="utf-8").splitlines()[:1]
    assert first and TAG in first[0], f"add '# {TAG}' as the first line"


def test_license_and_notice_present() -> None:
    assert "Apache License" in ROOT.joinpath("LICENSE").read_text()
    notice = ROOT.joinpath("NOTICE").read_text()
    assert "AgentFence Lab" in notice
    assert "Apache License, Version 2.0" in notice


def test_package_metadata_declares_the_license() -> None:
    text = ROOT.joinpath("pyproject.toml").read_text()
    assert 'license = "Apache-2.0"' in text
    assert '"NOTICE"' in text
