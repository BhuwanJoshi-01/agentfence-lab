# SPDX-License-Identifier: Apache-2.0
"""Runtime dependencies must use Apache-2.0-compatible licenses (ADR-014, AF-0706).

Walks the installed runtime dependency closure of ``agentfence-lab`` (dev extras and
platform-excluded requirements are skipped) and reads each distribution's
License-Expression, License field or license classifiers.
"""

from __future__ import annotations

from importlib import metadata

import pytest
from packaging.requirements import Requirement

ALLOWED: frozenset[str] = frozenset(
    {
        "MIT",
        "BSD",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "Apache-2.0",
        "Apache Software",
        "ISC",
        "PSF-2.0",
        "Python Software Foundation",
        "0BSD",
    }
)


def runtime_closure(root: str = "agentfence-lab") -> dict[str, metadata.Distribution]:
    found: dict[str, metadata.Distribution] = {}
    pending = [root]
    while pending:
        name = pending.pop()
        key = name.lower().replace("_", "-")
        if key in found:
            continue
        dist = metadata.distribution(name)
        found[key] = dist
        for raw in dist.requires or []:
            req = Requirement(raw)
            if req.marker is not None and not req.marker.evaluate({"extra": ""}):
                continue
            pending.append(req.name)
    return found


def license_terms(dist: metadata.Distribution) -> set[str]:
    meta = dist.metadata
    terms = {meta.get("License-Expression") or "", meta.get("License") or ""}
    terms |= {
        c.rsplit("::", 1)[-1].strip()
        for c in meta.get_all("Classifier") or []
        if c.startswith("License ::")
    }
    return {t for t in terms if t}


CLOSURE = runtime_closure()


def test_closure_contains_the_known_runtime_dependencies() -> None:
    assert {"typer", "pydantic", "pyyaml", "rich"} <= set(CLOSURE)
    assert "pytest" not in CLOSURE  # dev-only tools are not shipped dependencies


@pytest.mark.parametrize("name", sorted(CLOSURE))
def test_runtime_dependency_license_is_compatible(name: str) -> None:
    terms = license_terms(CLOSURE[name])
    assert terms, f"{name} declares no license"
    assert any(allowed in term for term in terms for allowed in ALLOWED), (
        f"{name} has license {sorted(terms)}; check Apache-2.0 compatibility and ADR-014"
    )
