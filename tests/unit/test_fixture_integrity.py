"""Fixture integrity and reproducibility (AF-0309; NFR-004, docs/10 section 9).

``tests/golden/fixture_digests_v1.json`` pins the digest of every registered generator
for both variants and several seeds. A generator whose output changes must also bump its
VERSION, so stored runs (which record ``fixture_digest``) stay comparable. To accept an
intended change after bumping the version:

    AGENTFENCE_UPDATE_GOLDEN=1 pytest tests/unit/test_fixture_integrity.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import agentfence.scenarios.generators  # noqa: F401 - registers built-in generators
from agentfence.core.fixtures import generate, registered_generators
from agentfence.core.vocabulary import Variant

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "fixture_digests_v1.json"
UPDATE = os.environ.get("AGENTFENCE_UPDATE_GOLDEN") == "1"
SEEDS = (0, 1, 42, 2**63 - 1)


def current_digests() -> dict[str, str]:
    digests: dict[str, str] = {}
    for name, version in registered_generators().items():
        for variant in Variant:
            for seed in SEEDS:
                key = f"{name}@{version}/{variant.value}/{seed}"
                digests[key] = generate(name, variant, seed).digest
    return digests


def test_fixture_digests_are_pinned() -> None:
    current = current_digests()
    if UPDATE:
        GOLDEN.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    stored: dict[str, str] = json.loads(GOLDEN.read_text(encoding="utf-8"))
    changed = sorted(k for k in current if k in stored and stored[k] != current[k])
    assert not changed, (
        f"generator output changed without a VERSION bump: {changed[:4]}. "
        "Bump the generator VERSION, then regenerate with AGENTFENCE_UPDATE_GOLDEN=1."
    )
    assert sorted(current) == sorted(stored), (
        "generator set or versions changed; regenerate with AGENTFENCE_UPDATE_GOLDEN=1"
    )


def test_every_generator_differs_between_variants_and_seeds() -> None:
    for name in registered_generators():
        by_variant = {generate(name, v, 7).digest for v in Variant}
        assert len(by_variant) == 2, f"{name}: variants must differ"
        by_seed = {generate(name, Variant.ADVERSARIAL, s).digest for s in range(12)}
        assert len(by_seed) > 1, f"{name}: seeds must change the fixture"


_SNIPPET = (
    "import json, agentfence.scenarios.generators\n"
    "from agentfence.core.fixtures import generate, registered_generators\n"
    "from agentfence.core.vocabulary import Variant\n"
    "print(json.dumps({f'{n}/{v.value}/{s}': generate(n, v, s).digest "
    "for n in registered_generators() for v in Variant for s in (0, 42)}, sort_keys=True))\n"
)


@pytest.mark.parametrize(
    "env",
    [
        {"PYTHONHASHSEED": "0", "LC_ALL": "C", "TZ": "UTC"},
        {"PYTHONHASHSEED": "12345", "LC_ALL": "C.UTF-8", "TZ": "Pacific/Chatham"},
        {"PYTHONHASHSEED": "random", "LC_ALL": "tr_TR.UTF-8", "TZ": "Asia/Kathmandu"},
    ],
)
def test_generation_is_identical_across_processes(env: dict[str, str]) -> None:
    """No dependence on hash randomization, locale or timezone."""
    expected = {
        f"{n}/{v.value}/{s}": generate(n, v, s).digest
        for n in registered_generators()
        for v in Variant
        for s in (0, 42)
    }
    base = {k: v for k, v in os.environ.items() if not k.startswith(("LC_", "PYTHONHASH"))}
    done = subprocess.run(
        [sys.executable, "-c", _SNIPPET],
        capture_output=True,
        text=True,
        env={**base, **env},
        check=False,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == expected


def test_materialized_workspace_matches_fixture_digest(tmp_path: Path) -> None:
    from agentfence.runtime.collectors import snapshot  # noqa: PLC0415
    from agentfence.runtime.workspace import materialize  # noqa: PLC0415

    for name in registered_generators():
        fixture = generate(name, Variant.ADVERSARIAL, 42)
        root = materialize(fixture, tmp_path / name).root
        entries = snapshot(root)
        assert {p: h.removeprefix("sha256:") for p, h in entries.items()} == {
            f.path: f.sha256 for f in fixture.files
        }
