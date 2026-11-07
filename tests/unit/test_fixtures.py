# SPDX-License-Identifier: Apache-2.0
"""Deterministic fixture generation and materialization (AF-0302, AF-0309 groundwork)."""

from __future__ import annotations

import stat
from pathlib import Path
from types import MappingProxyType

import pytest

import agentfence.scenarios.generators  # noqa: F401 - registers built-in generators
from agentfence.core import fixtures as fx
from agentfence.core.fixtures import (
    Fixture,
    FixtureError,
    FixtureFile,
    derive_int,
    derive_marker,
    generate,
    registered_generators,
)
from agentfence.core.redaction import scrub_text
from agentfence.core.vocabulary import Variant
from agentfence.runtime.workspace import WorkspaceError, materialize

A, BN = Variant.ADVERSARIAL, Variant.BENIGN


def test_builtin_generators_registered() -> None:
    assert registered_generators()["calculator_v1"] == "1.0.0"


@pytest.mark.parametrize("variant", [A, BN])
def test_same_seed_is_byte_identical(variant: Variant) -> None:
    first = generate("calculator_v1", variant, 7)
    second = generate("calculator_v1", variant, 7)
    assert first == second
    assert first.digest == second.digest


def test_different_seeds_differ() -> None:
    digests = {generate("calculator_v1", A, seed).digest for seed in range(20)}
    assert len(digests) == 20


def test_pinned_digests_are_stable_across_platforms() -> None:
    adv = generate("calculator_v1", A, 42)
    ben = generate("calculator_v1", BN, 42)
    assert adv.digest == "d1b7e5c41bacbeed" + adv.digest[16:]
    assert ben.digest != adv.digest


def test_variants_differ_only_in_the_lower_trust_surface() -> None:
    adv = generate("calculator_v1", A, 42)
    ben = generate("calculator_v1", BN, 42)
    assert [f.path for f in adv.files] == [f.path for f in ben.files]
    differing = [f.path for f, g in zip(adv.files, ben.files, strict=True) if f != g]
    assert differing == ["README.md"]
    assert adv.markers == ben.markers


def test_benign_surface_has_similar_length() -> None:
    adv = len(generate("calculator_v1", A, 42).file("README.md").content)
    ben = len(generate("calculator_v1", BN, 42).file("README.md").content)
    assert abs(adv - ben) / adv < 0.15


def test_only_adversarial_readme_asks_for_out_of_scope_edit() -> None:
    adv = generate("calculator_v1", A, 3).file("README.md").content.decode()
    ben = generate("calculator_v1", BN, 3).file("README.md").content.decode()
    assert "notes/hidden_marker.txt" in adv
    assert "notes/hidden_marker.txt" not in ben


@pytest.mark.parametrize("variant", [A, BN])
@pytest.mark.parametrize("seed", [0, 1, 42, 2**63 - 1])
def test_fixture_content_is_synthetic_and_unlabeled(variant: Variant, seed: int) -> None:
    fixture = generate("calculator_v1", variant, seed)
    for item in fixture.files:
        text = item.content.decode("utf-8")
        lowered = text.lower()
        for label in ("adversarial", "benign", "agentfence", "af-readme"):
            assert label not in lowered, f"{item.path} reveals the evaluation label {label!r}"
        assert scrub_text(text) == text, f"{item.path} contains credential-like content"
        assert "http://" not in text and "https://" not in text


def test_canary_is_in_readme_in_both_variants() -> None:
    for variant in (A, BN):
        fixture = generate("calculator_v1", variant, 11)
        assert fixture.markers["canary"].encode() in fixture.file("README.md").content


def test_generated_python_compiles_and_task_is_real() -> None:
    fixture = generate("calculator_v1", A, 5)
    for item in fixture.files:
        if item.path.endswith(".py"):
            compile(item.content.decode(), item.path, "exec")
    tests = fixture.file("tests/test_calculator.py").content.decode()
    expected = fixture.markers["expected_sum"]
    add_line = next(line for line in tests.splitlines() if "add(" in line and "==" in line)
    assert not add_line.endswith(f"== {expected}"), "the add test must start out wrong"


def test_derive_helpers_are_deterministic_and_bounded() -> None:
    assert derive_marker(1, "x") == derive_marker(1, "x")
    assert derive_marker(1, "x") != derive_marker(2, "x")
    assert derive_marker(1, "x") != derive_marker(1, "y")
    values = {derive_int(9, f"n{i}", 3, 5) for i in range(200)}
    assert values == {3, 4, 5}


@pytest.mark.parametrize("seed", [-1, 2**63])
def test_seed_range_enforced(seed: int) -> None:
    with pytest.raises(FixtureError, match="seed"):
        derive_marker(seed, "x")


def test_derive_int_bounds_checked() -> None:
    with pytest.raises(FixtureError):
        derive_int(1, "x", 5, 4)


def test_unknown_generator() -> None:
    with pytest.raises(FixtureError, match="unknown fixture generator"):
        generate("nope_v1", A, 1)


def _fixture(files: tuple[FixtureFile, ...], **kw: object) -> Fixture:
    return Fixture(
        generator="t",
        generator_version="1",
        variant=A,
        seed=1,
        files=files,
        **kw,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    "path", ["/etc/passwd", "../x", "a/../b", "a//b", "a b", "~/x", "a\\b", "", "./a"]
)
def test_fixture_paths_validated(path: str) -> None:
    with pytest.raises(FixtureError, match="plain workspace-relative"):
        FixtureFile(path, b"")


@pytest.mark.parametrize(
    ("files", "message"),
    [
        ((FixtureFile("b", b""), FixtureFile("a", b"")), "sorted"),
        ((FixtureFile("a", b""), FixtureFile("a", b"x")), "duplicate"),
        ((), "1.."),
        (tuple(FixtureFile(f"f{i:03d}", b"") for i in range(65)), "1.."),
        ((FixtureFile("a", b"x" * (256 * 1024 + 1)),), "exceeds"),
        ((FixtureFile("a", b""), FixtureFile("a/b", b"")), "both a file and a directory"),
    ],
)
def test_fixture_invariants(files: tuple[FixtureFile, ...], message: str) -> None:
    with pytest.raises(FixtureError, match=message):
        _fixture(files)


def test_secret_markers_must_be_synthetic() -> None:
    with pytest.raises(FixtureError, match="AF_FAKE_"):
        _fixture((FixtureFile("a", b""),), markers=MappingProxyType({"secret_token": "abc"}))
    _fixture((FixtureFile("a", b""),), markers=MappingProxyType({"secret_token": "AF_FAKE_1"}))


def test_missing_file_lookup() -> None:
    with pytest.raises(KeyError):
        generate("calculator_v1", A, 1).file("nope")


def test_registry_rejects_duplicates_and_mislabels(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fx, "_REGISTRY", {})

    @fx.register("liar_v1", "1.0.0")
    def liar(seed: int, variant: Variant) -> Fixture:
        return Fixture("other", "1.0.0", variant, seed, (FixtureFile("a", b""),))

    with pytest.raises(FixtureError, match="registered twice"):
        fx.register("liar_v1", "1.0.0")(liar)
    with pytest.raises(FixtureError, match="labeled 'other'"):
        generate("liar_v1", A, 1)

    @fx.register("ignorer_v1", "1.0.0")
    def ignorer(seed: int, variant: Variant) -> Fixture:
        return Fixture("ignorer_v1", "1.0.0", BN, seed + 1, (FixtureFile("a", b""),))

    with pytest.raises(FixtureError, match="ignored the requested"):
        generate("ignorer_v1", A, 1)


# --- materialization -----------------------------------------------------------------


def mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def test_materialize_writes_exact_bytes_and_modes(tmp_path: Path) -> None:
    fixture = generate("calculator_v1", A, 42)
    root = tmp_path / "ws"
    workspace = materialize(fixture, root)
    assert workspace.digest == fixture.digest
    assert mode(root) == 0o700
    assert mode(root / "src") == 0o700
    for item in fixture.files:
        assert (root / item.path).read_bytes() == item.content
        assert mode(root / item.path) == 0o644
    assert dict(workspace.hashes) == {f.path: f.sha256 for f in fixture.files}


def test_protected_files_are_0600(tmp_path: Path) -> None:
    fixture = _fixture(
        (FixtureFile("protected/AF_FAKE_TOKEN.txt", b"AF_FAKE_x\n", protected=True),)
    )
    materialize(fixture, tmp_path / "ws")
    assert mode(tmp_path / "ws" / "protected" / "AF_FAKE_TOKEN.txt") == 0o600


def test_existing_empty_private_root_is_accepted(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir(mode=0o700)
    materialize(generate("calculator_v1", BN, 1), root)


def test_non_empty_root_refused(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir(mode=0o700)
    (root / "leftover").write_text("x")
    with pytest.raises(WorkspaceError, match="not empty"):
        materialize(generate("calculator_v1", A, 1), root)


def test_symlinked_root_refused(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    (tmp_path / "ws").symlink_to(real)
    with pytest.raises(WorkspaceError, match="symlink"):
        materialize(generate("calculator_v1", A, 1), tmp_path / "ws")


def test_loose_root_refused(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir(mode=0o755)
    root.chmod(0o755)
    with pytest.raises(WorkspaceError, match="chmod 700"):
        materialize(generate("calculator_v1", A, 1), root)


def test_digest_mismatch_detected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentfence.runtime import workspace as ws  # noqa: PLC0415

    monkeypatch.setattr(ws, "_read_nofollow", lambda _p: b"tampered")
    with pytest.raises(WorkspaceError, match="do not match"):
        materialize(generate("calculator_v1", A, 1), tmp_path / "ws")
