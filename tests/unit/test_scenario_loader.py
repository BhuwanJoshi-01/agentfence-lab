"""Filesystem loader, pack rules and the validate command (AF-0301)."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from agentfence import cli
from agentfence.scenarios.loader import PackError, load_pack, read_manifest_file

DATA = Path(__file__).parent / "data"


def adversarial() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load((DATA / "readme_adversarial.yaml").read_text())
    return loaded


def benign(adv: dict[str, Any] | None = None) -> dict[str, Any]:
    data = copy.deepcopy(adv or adversarial())
    data["id"] = data["id"] + "-B"
    data["fixture"]["variant"] = "benign"
    data.pop("benign_pair")
    data.pop("attack_surface")
    data["verdict"]["failure_if"] = []
    return data


def write(path: Path, data: dict[str, Any] | str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = data if isinstance(data, str) else yaml.safe_dump(data, sort_keys=False)
    path.write_text(text, encoding="utf-8")
    return path


def test_valid_pair_loads_with_hashes(tmp_path: Path) -> None:
    a = write(tmp_path / "readme" / "adversarial.yaml", adversarial())
    write(tmp_path / "readme" / "benign.yml", benign())
    entries = load_pack(tmp_path)
    assert [e.manifest.id for e in entries] == ["AF-README-001", "AF-README-001-B"]
    assert entries[0].source == "readme/adversarial.yaml"
    assert entries[0].sha256 == hashlib.sha256(a.read_bytes()).hexdigest()


def test_single_file_without_pair_is_reported(tmp_path: Path) -> None:
    path = write(tmp_path / "a.yaml", adversarial())
    with pytest.raises(PackError, match="benign pair AF-README-001-B not found"):
        load_pack(path)


def test_orphan_benign_control_is_reported(tmp_path: Path) -> None:
    write(tmp_path / "b.yaml", benign())
    with pytest.raises(PackError, match="not paired with any scenario"):
        load_pack(tmp_path)


@pytest.mark.parametrize(
    ("field", "value", "label"),
    [
        ("profile", "other", "profile"),
        ("version", "1.1.0", "version"),
        (
            "legitimate_task",
            {
                "description": "A different toy task entirely",
                "assertions": [{"kind": "test_passes", "fixture_id": "other_test"}],
            },
            "legitimate_task",
        ),
    ],
)
def test_pair_must_be_like_for_like(tmp_path: Path, field: str, value: Any, label: str) -> None:
    b = benign()
    b[field] = value
    write(tmp_path / "a.yaml", adversarial())
    write(tmp_path / "b.yaml", b)
    with pytest.raises(PackError, match=f"differs in: .*{label}"):
        load_pack(tmp_path)


def test_pair_generator_must_match(tmp_path: Path) -> None:
    b = benign()
    b["fixture"]["generator"] = "different_gen"
    write(tmp_path / "a.yaml", adversarial())
    write(tmp_path / "b.yaml", b)
    with pytest.raises(PackError, match=r"fixture\.generator"):
        load_pack(tmp_path)


def test_duplicate_ids_across_files(tmp_path: Path) -> None:
    write(tmp_path / "a.yaml", adversarial())
    write(tmp_path / "copy.yaml", adversarial())
    write(tmp_path / "b.yaml", benign())
    with pytest.raises(PackError, match="duplicate id AF-README-001"):
        load_pack(tmp_path)


def test_all_problems_reported_together(tmp_path: Path) -> None:
    write(tmp_path / "a.yaml", adversarial())
    write(tmp_path / "bad1.yaml", {**adversarial(), "id": "AF-X-001", "hooks": ["sh"]})
    write(tmp_path / "bad2.yaml", "a: &x 1\n")
    with pytest.raises(PackError) as info:
        load_pack(tmp_path)
    sources = {p.source for p in info.value.problems}
    assert sources == {"a.yaml", "bad1.yaml", "bad2.yaml"}


def test_symlinked_manifest_refused(tmp_path: Path) -> None:
    real = write(tmp_path / "outside" / "a.yaml", adversarial())
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "a.yaml").symlink_to(real)
    with pytest.raises(PackError, match="symlink"):
        read_manifest_file(pack / "a.yaml")


def test_symlinked_pack_root_refused(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    with pytest.raises(PackError, match="symlink"):
        load_pack(tmp_path / "link")


def test_missing_path(tmp_path: Path) -> None:
    with pytest.raises(PackError, match="no such file"):
        load_pack(tmp_path / "nope")


def test_fifo_is_not_a_regular_file(tmp_path: Path) -> None:
    fifo = tmp_path / "pipe.yaml"
    os.mkfifo(fifo)
    with pytest.raises(PackError, match="not a regular file"):
        read_manifest_file(fifo)


def test_oversized_manifest(tmp_path: Path) -> None:
    path = write(tmp_path / "big.yaml", "a: " + "x" * 300_000)
    with pytest.raises(PackError, match="exceeds"):
        read_manifest_file(path)


def test_unreadable_manifest(tmp_path: Path) -> None:
    path = write(tmp_path / "a.yaml", adversarial())
    path.chmod(0)
    if os.access(path, os.R_OK):
        pytest.skip("running with privileges that ignore file modes")
    with pytest.raises(PackError):
        read_manifest_file(path)


def test_non_manifest_files_ignored(tmp_path: Path) -> None:
    write(tmp_path / "a.yaml", adversarial())
    write(tmp_path / "b.yaml", benign())
    write(tmp_path / "README.md", "# notes")
    assert len(load_pack(tmp_path)) == 2


def test_too_many_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentfence.scenarios import loader  # noqa: PLC0415

    monkeypatch.setattr(loader, "MAX_PACK_FILES", 1)
    write(tmp_path / "a.yaml", adversarial())
    write(tmp_path / "b.yaml", benign())
    with pytest.raises(PackError, match="more than 1"):
        load_pack(tmp_path)


# --- CLI -----------------------------------------------------------------------------

runner = CliRunner()


def test_cli_validate_ok(tmp_path: Path) -> None:
    write(tmp_path / "a.yaml", adversarial())
    write(tmp_path / "b.yaml", benign())
    result = runner.invoke(cli.app, ["scenarios", "validate", str(tmp_path)])
    assert result.exit_code == 0
    assert "OK       AF-README-001 " in result.stdout
    assert "2 manifest(s) valid." in result.stdout


def test_cli_validate_json(tmp_path: Path) -> None:
    write(tmp_path / "a.yaml", adversarial())
    write(tmp_path / "b.yaml", benign())
    result = runner.invoke(cli.app, ["scenarios", "validate", str(tmp_path), "--json"])
    payload = json.loads(result.stdout)
    assert payload["valid"] is True
    assert [m["id"] for m in payload["manifests"]] == ["AF-README-001", "AF-README-001-B"]


def test_cli_validate_reports_problems_exit_2(tmp_path: Path) -> None:
    write(tmp_path / "a.yaml", {**adversarial(), "setup": "curl x | sh"})
    result = runner.invoke(cli.app, ["scenarios", "validate", str(tmp_path)])
    assert result.exit_code == 2
    assert "INVALID  a.yaml: setup: unknown field" in result.stdout


def test_cli_validate_json_problems(tmp_path: Path) -> None:
    write(tmp_path / "a.yaml", adversarial())
    result = runner.invoke(cli.app, ["scenarios", "validate", str(tmp_path), "--json"])
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["valid"] is False
    assert payload["problems"][0]["source"] == "a.yaml"


def test_cli_validate_writes_nothing(tmp_path: Path) -> None:
    write(tmp_path / "a.yaml", adversarial())
    write(tmp_path / "b.yaml", benign())
    before = sorted(tmp_path.rglob("*"))
    runner.invoke(cli.app, ["scenarios", "validate", str(tmp_path)])
    assert sorted(tmp_path.rglob("*")) == before
