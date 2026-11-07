# SPDX-License-Identifier: Apache-2.0
"""XDG paths and private directory tests (AF-0105)."""

from __future__ import annotations

import os
import stat
import uuid
from pathlib import Path

import pytest

from agentfence.storage.paths import (
    InsecurePathError,
    PathConfigError,
    ensure_private_dir,
    resolve_paths,
    safe_child,
    verify_private_file,
)

HOME = Path("/home/tester")


def mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def test_defaults_follow_xdg_fallbacks() -> None:
    paths = resolve_paths(env={}, home=HOME)
    assert paths.state_dir == HOME / ".local/state/agentfence"
    assert paths.config_dir == HOME / ".config/agentfence"
    assert paths.database_path == HOME / ".local/state/agentfence/agentfence.sqlite3"
    assert paths.user_config_file == HOME / ".config/agentfence/config.yaml"


def test_xdg_variables_respected() -> None:
    env = {"XDG_STATE_HOME": "/xdg/state", "XDG_CONFIG_HOME": "/xdg/config"}
    paths = resolve_paths(env=env, home=HOME)
    assert paths.state_dir == Path("/xdg/state/agentfence")
    assert paths.config_dir == Path("/xdg/config/agentfence")


def test_relative_xdg_values_ignored_per_spec() -> None:
    paths = resolve_paths(env={"XDG_STATE_HOME": "rel/state", "XDG_CONFIG_HOME": "."}, home=HOME)
    assert paths.state_dir == HOME / ".local/state/agentfence"
    assert paths.config_dir == HOME / ".config/agentfence"


def test_explicit_state_dir_wins_and_is_normalized() -> None:
    env = {"AGENTFENCE_STATE_DIR": "/data/af/../af-state/", "XDG_STATE_HOME": "/xdg"}
    assert resolve_paths(env=env, home=HOME).state_dir == Path("/data/af-state")


def test_relative_explicit_state_dir_is_an_error() -> None:
    with pytest.raises(PathConfigError, match="absolute"):
        resolve_paths(env={"AGENTFENCE_STATE_DIR": "state"}, home=HOME)


def test_home_from_env_when_not_given() -> None:
    assert resolve_paths(env={"HOME": "/h"}).state_dir == Path("/h/.local/state/agentfence")


def test_run_dir_requires_canonical_uuid() -> None:
    paths = resolve_paths(env={}, home=HOME)
    run_id = str(uuid.uuid4())
    assert paths.run_dir(run_id) == paths.runs_dir / run_id
    for bad in ["../../etc", "not-a-uuid", run_id.upper(), "{" + run_id + "}"]:
        with pytest.raises(PathConfigError):
            paths.run_dir(bad)


def test_ensure_private_dir_creates_0700_chain(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "state"
    old = os.umask(0o002)
    try:
        ensure_private_dir(target)
    finally:
        os.umask(old)
    for path in (tmp_path / "a", tmp_path / "a" / "b", target):
        assert mode(path) == 0o700


def test_ensure_private_dir_is_idempotent(tmp_path: Path) -> None:
    target = tmp_path / "state"
    ensure_private_dir(target)
    ensure_private_dir(target)
    assert mode(target) == 0o700


def test_existing_loose_directory_refused_not_changed(tmp_path: Path) -> None:
    target = tmp_path / "state"
    target.mkdir()
    target.chmod(0o755)
    with pytest.raises(InsecurePathError, match="chmod 700"):
        ensure_private_dir(target)
    assert mode(target) == 0o755


def test_symlinked_state_dir_refused(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    link = tmp_path / "state"
    link.symlink_to(real)
    with pytest.raises(InsecurePathError, match="symlink"):
        ensure_private_dir(link)


def test_file_in_place_of_dir_refused(tmp_path: Path) -> None:
    target = tmp_path / "state"
    target.write_text("x")
    target.chmod(0o600)
    with pytest.raises(InsecurePathError, match="not a directory"):
        ensure_private_dir(target)


def test_verify_private_file(tmp_path: Path) -> None:
    good = tmp_path / "good"
    good.write_text("x")
    good.chmod(0o600)
    verify_private_file(good)
    good.chmod(0o644)
    with pytest.raises(InsecurePathError, match="chmod 600"):
        verify_private_file(good)


def test_verify_private_file_rejects_directory(tmp_path: Path) -> None:
    directory = tmp_path / "d"
    directory.mkdir(mode=0o700)
    with pytest.raises(InsecurePathError, match="not a regular file"):
        verify_private_file(directory)


def test_safe_child_accepts_plain_names(tmp_path: Path) -> None:
    assert safe_child(tmp_path, "runs", "report.json") == tmp_path / "runs" / "report.json"


@pytest.mark.parametrize("part", ["..", ".", "", "a/b", "a\\b", "x\x00y", "/etc"])
def test_safe_child_rejects_unsafe_components(tmp_path: Path, part: str) -> None:
    with pytest.raises(PathConfigError, match="unsafe path component"):
        safe_child(tmp_path, part)


def test_safe_child_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "escape").symlink_to(tmp_path.parent)
    with pytest.raises(PathConfigError, match="outside"):
        safe_child(root, "escape")


def test_foreign_owned_directory_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "state"
    target.mkdir(mode=0o700)
    real_uid = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: real_uid + 1)
    with pytest.raises(InsecurePathError, match="owned by uid"):
        ensure_private_dir(target)
