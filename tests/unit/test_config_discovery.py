"""Config file discovery tests (AF-0105)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agentfence.core.config import ConfigError, ConfigSource, resolve_config
from agentfence.storage.config_discovery import discover_config_layers
from agentfence.storage.paths import AppPaths


@pytest.fixture
def paths(tmp_path: Path) -> AppPaths:
    config_dir = tmp_path / "config" / "agentfence"
    config_dir.mkdir(parents=True)
    return AppPaths(state_dir=tmp_path / "state", config_dir=config_dir)


def write(path: Path, text: str, mode: int = 0o644) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)
    return path


def test_no_files_means_no_layers(paths: AppPaths, tmp_path: Path) -> None:
    assert discover_config_layers(paths=paths, cwd=tmp_path, env={}) == []


def test_user_and_project_layers_in_order(paths: AppPaths, tmp_path: Path) -> None:
    write(paths.user_config_file, "scenarios:\n  repeat: 2\n")
    project_dir = tmp_path / "repo"
    write(project_dir / "agentfence.yaml", "scenarios:\n  repeat: 3\n")
    layers = discover_config_layers(paths=paths, cwd=project_dir, env={})
    assert [layer.source for layer in layers] == [ConfigSource.USER, ConfigSource.PROJECT]
    assert resolve_config(layers).config.scenarios.repeat == 3


def test_explicit_flag_beats_env_and_cwd(paths: AppPaths, tmp_path: Path) -> None:
    write(tmp_path / "agentfence.yaml", "scenarios:\n  repeat: 3\n")
    env_file = write(tmp_path / "env.yaml", "scenarios:\n  repeat: 4\n")
    flag_file = write(tmp_path / "flag.yaml", "scenarios:\n  repeat: 5\n")
    env = {"AGENTFENCE_CONFIG": str(env_file)}
    layers = discover_config_layers(paths=paths, cwd=tmp_path, env=env, explicit=flag_file)
    assert resolve_config(layers).config.scenarios.repeat == 5
    layers = discover_config_layers(paths=paths, cwd=tmp_path, env=env)
    assert resolve_config(layers).config.scenarios.repeat == 4


def test_relative_explicit_and_env_paths_are_cwd_relative(paths: AppPaths, tmp_path: Path) -> None:
    write(tmp_path / "conf" / "a.yaml", "scenarios:\n  repeat: 6\n")
    layers = discover_config_layers(paths=paths, cwd=tmp_path, env={}, explicit=Path("conf/a.yaml"))
    assert resolve_config(layers).config.scenarios.repeat == 6
    layers = discover_config_layers(
        paths=paths, cwd=tmp_path, env={"AGENTFENCE_CONFIG": "conf/a.yaml"}
    )
    assert resolve_config(layers).config.scenarios.repeat == 6


def test_no_upward_search(paths: AppPaths, tmp_path: Path) -> None:
    write(tmp_path / "agentfence.yaml", "scenarios:\n  repeat: 9\n")
    child = tmp_path / "child"
    child.mkdir()
    assert discover_config_layers(paths=paths, cwd=child, env={}) == []


def test_missing_explicit_file_is_an_error(paths: AppPaths, tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        discover_config_layers(paths=paths, cwd=tmp_path, env={}, explicit=Path("missing.yaml"))


@pytest.mark.parametrize("loose", [0o666, 0o664, 0o646])
def test_world_or_group_writable_config_refused(
    paths: AppPaths, tmp_path: Path, loose: int
) -> None:
    write(tmp_path / "agentfence.yaml", "{}\n", mode=loose)
    with pytest.raises(ConfigError, match="chmod go-w"):
        discover_config_layers(paths=paths, cwd=tmp_path, env={})


def test_project_cannot_weaken_invariants_via_discovery(paths: AppPaths, tmp_path: Path) -> None:
    write(tmp_path / "agentfence.yaml", "runtime:\n  no_host_mounts: false\n")
    layers = discover_config_layers(paths=paths, cwd=tmp_path, env={})
    with pytest.raises(ConfigError, match="cannot be overridden"):
        resolve_config(layers)


def test_foreign_owned_config_refused(
    paths: AppPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(tmp_path / "agentfence.yaml", "{}\n")
    real_uid = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: real_uid + 1 if real_uid else 12345)
    if real_uid == 0:
        pytest.skip("root-owned files are accepted by design")
    with pytest.raises(ConfigError, match="owned by uid"):
        discover_config_layers(paths=paths, cwd=tmp_path, env={})
