# SPDX-License-Identifier: Apache-2.0
"""Reference docs must not cite tests or modules that do not exist (#117).

``docs/reference/`` records what is enforced and which test proves it. When a test is
renamed or a module moves, the page silently starts citing nothing; this keeps that drift
from reaching ``main``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCS = sorted((ROOT / "docs" / "reference").glob("*.md"))
TEST_REF = re.compile(r"`(?:tests/unit/)?(test_[a-z0-9_]+\.py)(?:::(test_[a-z0-9_]+))?`")
MODULE_REF = re.compile(r"`((?:[a-z_]+/)*[a-z_][a-z0-9_]*\.py)`")


def test_reference_docs_exist() -> None:
    assert {p.name for p in DOCS} >= {"trust-boundaries.md", "evaluator.md"}


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_cited_tests_exist(doc: Path) -> None:
    missing = []
    for filename, name in TEST_REF.findall(doc.read_text(encoding="utf-8")):
        source = ROOT / "tests" / "unit" / filename
        if not source.is_file():
            missing.append(filename)
        elif name and not re.search(rf"^def {name}\(", source.read_text("utf-8"), re.M):
            missing.append(f"{filename}::{name}")
    assert not missing, f"{doc.name} cites tests that do not exist: {sorted(set(missing))}"


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_cited_modules_exist(doc: Path) -> None:
    missing = []
    for module in MODULE_REF.findall(doc.read_text(encoding="utf-8")):
        if module.startswith(("test_", "tests/")):
            continue
        if not any((base / module).is_file() for base in (ROOT / "src" / "agentfence", ROOT)):
            missing.append(module)
    assert not missing, f"{doc.name} cites modules that do not exist: {sorted(set(missing))}"


def test_the_checker_catches_a_stale_reference() -> None:
    stale = "`test_events.py::test_authority_follows_source_trust` and `runtime/runner.py`"
    tests = TEST_REF.findall(stale)
    modules = MODULE_REF.findall(stale)
    assert tests == [("test_events.py", "test_authority_follows_source_trust")]
    assert "runtime/runner.py" in modules
    source = (ROOT / "tests" / "unit" / "test_events.py").read_text("utf-8")
    assert not re.search(r"^def test_authority_follows_source_trust\(", source, re.M)
    assert not (ROOT / "src" / "agentfence" / "runtime" / "runner.py").exists()
