# SPDX-License-Identifier: Apache-2.0
"""Integrity of the bundled scenario library (AF-0303, groundwork for AF-0309).

Every bundled manifest must agree with the fixture its generator produces: the attack
surface exists, failure-rule paths exist and sit outside the allowed scope, the benign
control's surface is neutral, and nothing evaluation-related is inside the workspace.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agentfence.core.fixtures import generate, registered_generators
from agentfence.core.globs import matches_any
from agentfence.core.oracles import get_solution
from agentfence.core.scenario_manifest import FailureRuleKind, ScenarioManifest
from agentfence.core.vocabulary import Variant
from agentfence.scenarios import bundled_scenarios
from agentfence.scenarios.loader import LoadedManifest, PackError, load_pack

ENTRIES = bundled_scenarios("mvp")
ADVERSARIAL = [e.manifest for e in ENTRIES if e.manifest.variant is Variant.ADVERSARIAL]
SEEDS = [0, 7, 42]


def test_every_adversarial_scenario_has_its_benign_control() -> None:
    ids = {e.manifest.id for e in ENTRIES}
    assert ADVERSARIAL
    for manifest in ADVERSARIAL:
        assert manifest.benign_pair in ids


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda e: e.manifest.id)
def test_generator_is_registered(entry: LoadedManifest) -> None:
    assert entry.manifest.fixture.generator in registered_generators()


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("m", ADVERSARIAL, ids=lambda m: m.id)
def test_adversarial_fixture_matches_manifest(m: ScenarioManifest, seed: int) -> None:
    assert m.attack_surface is not None
    fixture = generate(m.fixture.generator, Variant.ADVERSARIAL, seed)
    paths = {f.path for f in fixture.files}
    assert m.attack_surface.path in paths
    for rule in m.verdict.failure_if:
        if rule.kind is FailureRuleKind.FORBIDDEN_PATH_MODIFIED and rule.path:
            assert rule.path in paths, f"failure rule targets a file the fixture lacks: {rule.path}"
            assert not matches_any(rule.path, m.boundary.allow)
            assert matches_any(rule.path, m.boundary.deny)
    for assertion in m.legitimate_task.assertions:
        solution = get_solution(m.fixture.generator, assertion.fixture_id)
        assert solution is not None
        files = {f.path: f.content for f in fixture.files}
        for written in solution(files.get, fixture):
            assert matches_any(written, m.boundary.allow), f"legit work writes {written}"
            assert not matches_any(written, m.boundary.deny), f"legit work writes {written}"


@pytest.mark.parametrize("m", ADVERSARIAL, ids=lambda m: m.id)
def test_benign_surface_does_not_carry_the_deviation(m: ScenarioManifest) -> None:
    assert m.attack_surface is not None
    adv = generate(m.fixture.generator, Variant.ADVERSARIAL, 42)
    ben = generate(m.fixture.generator, Variant.BENIGN, 42)
    surface = m.attack_surface.path
    for rule in m.verdict.failure_if:
        if rule.kind is FailureRuleKind.OUTPUT_MARKER_PRESENT:
            needle = adv.markers["output_marker_token"].encode()
        elif rule.path:
            needle = rule.path.encode()
        else:
            continue
        assert needle in adv.file(surface).content
        assert needle not in ben.file(surface).content


def test_bundled_directory_validates_as_a_pack() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "agentfence" / "scenarios" / "mvp"
    assert len(load_pack(root)) == len(ENTRIES)


def test_unknown_generator_is_a_pack_problem(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "agentfence" / "scenarios" / "mvp"
    for source in root.glob("*.yaml"):
        data = yaml.safe_load(source.read_text())
        data["fixture"]["generator"] = "homemade_v1"
        (tmp_path / source.name).write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(PackError, match="unknown fixture generator 'homemade_v1'"):
        load_pack(tmp_path)
