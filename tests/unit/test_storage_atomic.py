# SPDX-License-Identifier: Apache-2.0
"""Atomic write tests (AF-0105)."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

import pytest

from agentfence.storage import atomic
from agentfence.storage.atomic import atomic_write_bytes, atomic_write_json, atomic_write_text


def leftovers(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir() if p.name.endswith(".tmp"))


def test_new_file_is_0600(tmp_path: Path) -> None:
    target = atomic_write_text(tmp_path / "report.txt", "hello")
    assert target.read_text() == "hello"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert leftovers(tmp_path) == []


def test_replacing_existing_file_tightens_mode(tmp_path: Path) -> None:
    target = tmp_path / "report.txt"
    target.write_text("old")
    target.chmod(0o644)
    atomic_write_text(target, "new")
    assert target.read_text() == "new"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_json_is_deterministic_and_newline_terminated(tmp_path: Path) -> None:
    path = atomic_write_json(tmp_path / "r.json", {"b": 1, "a": "ü"})
    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert text.index('"a"') < text.index('"b"')
    assert json.loads(text) == {"a": "ü", "b": 1}


def test_unserializable_json_leaves_old_file_untouched(tmp_path: Path) -> None:
    target = tmp_path / "r.json"
    target.write_text("{}")
    with pytest.raises(ValueError, match="JSON"):
        atomic_write_json(target, {"x": float("nan")})
    assert target.read_text() == "{}"
    assert leftovers(tmp_path) == []


def test_failure_mid_write_cleans_temp_and_keeps_old(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "state.json"
    target.write_text("original")

    def boom(_fd: Any) -> None:
        raise OSError("disk full (simulated)")

    monkeypatch.setattr(atomic.os, "fsync", boom)
    with pytest.raises(OSError, match="simulated"):
        atomic_write_bytes(target, b"replacement")
    assert target.read_text() == "original"
    assert leftovers(tmp_path) == []


def test_interrupt_cleans_temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupt(_fd: Any) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(atomic.os, "fsync", interrupt)
    with pytest.raises(KeyboardInterrupt):
        atomic_write_bytes(tmp_path / "x", b"data")
    assert list(tmp_path.iterdir()) == []


def test_refuses_loose_modes(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="group/other"):
        atomic_write_bytes(tmp_path / "x", b"", mode=0o644)


def test_refuses_to_replace_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("keep")
    link = tmp_path / "report.txt"
    link.symlink_to(outside)
    with pytest.raises(PermissionError, match="symlink"):
        atomic_write_text(link, "overwrite")
    assert outside.read_text() == "keep"


def test_refuses_to_replace_directory(tmp_path: Path) -> None:
    (tmp_path / "d").mkdir()
    with pytest.raises(IsADirectoryError):
        atomic_write_text(tmp_path / "d", "x")


def test_missing_parent_directory(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="does not exist"):
        atomic_write_text(tmp_path / "nope" / "x", "y")


def test_unicode_and_spaces_in_filename(tmp_path: Path) -> None:
    target = atomic_write_text(tmp_path / "rapport final ü.html", "<p>ok</p>")
    assert target.read_text() == "<p>ok</p>"
