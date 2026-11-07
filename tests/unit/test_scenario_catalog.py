"""Bundled scenario catalog tests (AF-0102)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from agentfence.scenarios import CatalogError, bundled_scenarios

DATA = Path(__file__).parent / "data"


def adversarial() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load((DATA / "readme_adversarial.yaml").read_text())
    return loaded


def benign() -> dict[str, Any]:
    data = copy.deepcopy(adversarial())
    data["id"] = "AF-README-001-B"
    data["fixture"]["variant"] = "benign"
    data.pop("benign_pair")
    data.pop("attack_surface")
    data["verdict"]["failure_if"] = []
    return data


def write(base: Path, profile: str, name: str, data: dict[str, Any] | str) -> None:
    folder = base / profile
    folder.mkdir(parents=True, exist_ok=True)
    text = data if isinstance(data, str) else yaml.safe_dump(data, sort_keys=False)
    (folder / name).write_text(text, encoding="utf-8")


def test_real_package_bundles_the_readme_pair() -> None:
    ids = [e.manifest.id for e in bundled_scenarios("mvp")]
    assert "AF-README-001" in ids
    assert "AF-README-001-B" in ids


def test_unknown_profile_in_real_package_is_empty() -> None:
    assert bundled_scenarios("does-not-exist") == []


def test_missing_profile_is_empty(tmp_path: Path) -> None:
    assert bundled_scenarios("nope", base=tmp_path) == []


def test_lists_valid_manifests_sorted(tmp_path: Path) -> None:
    write(tmp_path, "mvp", "b-readme-benign.yaml", benign())
    write(tmp_path, "mvp", "a-readme.yaml", adversarial())
    write(tmp_path, "mvp", "notes.txt", "ignored, not yaml")
    entries = bundled_scenarios("mvp", base=tmp_path)
    assert [e.source for e in entries] == ["mvp/a-readme.yaml", "mvp/b-readme-benign.yaml"]
    assert all(len(e.sha256) == 64 for e in entries)
    assert [e.manifest.id for e in entries] == ["AF-README-001", "AF-README-001-B"]


def test_invalid_manifest_fails_whole_listing(tmp_path: Path) -> None:
    write(tmp_path, "mvp", "a.yaml", adversarial())
    write(tmp_path, "mvp", "b.yaml", {**adversarial(), "id": "AF-OTHER-001", "setup": "sh x"})
    with pytest.raises(CatalogError, match=r"mvp/b\.yaml: setup: unknown field"):
        bundled_scenarios("mvp", base=tmp_path)


def test_hostile_yaml_rejected(tmp_path: Path) -> None:
    write(tmp_path, "mvp", "a.yaml", "a: &x 1\nb: *x\n")
    with pytest.raises(CatalogError, match="anchors"):
        bundled_scenarios("mvp", base=tmp_path)


def test_duplicate_ids_rejected(tmp_path: Path) -> None:
    write(tmp_path, "mvp", "a.yaml", adversarial())
    write(tmp_path, "mvp", "b.yaml", adversarial())
    with pytest.raises(CatalogError, match="duplicate id AF-README-001"):
        bundled_scenarios("mvp", base=tmp_path)


@pytest.mark.parametrize("profile", ["../etc", "MVP", "", "a/b", "-x"])
def test_profile_names_are_validated(profile: str) -> None:
    with pytest.raises(CatalogError, match="invalid profile"):
        bundled_scenarios(profile)


def test_too_many_manifests_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import agentfence.scenarios as catalog  # noqa: PLC0415

    monkeypatch.setattr(catalog, "MAX_MANIFESTS_PER_PROFILE", 1)
    write(tmp_path, "mvp", "a.yaml", adversarial())
    write(tmp_path, "mvp", "b.yaml", benign())
    with pytest.raises(CatalogError, match="more than 1"):
        bundled_scenarios("mvp", base=tmp_path)


def test_unpaired_bundled_scenario_rejected(tmp_path: Path) -> None:
    write(tmp_path, "mvp", "a.yaml", adversarial())
    with pytest.raises(CatalogError, match="benign pair AF-README-001-B not found"):
        bundled_scenarios("mvp", base=tmp_path)


def test_oversized_bundled_manifest_rejected(tmp_path: Path) -> None:
    write(tmp_path, "mvp", "a.yaml", "a: " + "x" * 300_000)
    with pytest.raises(CatalogError, match="too large"):
        bundled_scenarios("mvp", base=tmp_path)
