"""Find the config files that feed :func:`agentfence.core.config.resolve_config`.

Order (docs/02 section 10): user config, then project config, then CLI overrides.

* user:    ``<config_dir>/config.yaml`` if it exists
* project: ``--config PATH``, else ``$AGENTFENCE_CONFIG``, else ``./agentfence.yaml``
  if it exists in the working directory (no upward search, so a parent directory
  cannot inject settings)

A config file that other users can write is refused: it could be swapped to change
which scenarios run. Group write is tolerated only for a user-private group.
Content-level safety is enforced by the strict schema.
"""

from __future__ import annotations

import grp
import os
import pwd
import stat
from collections.abc import Mapping
from pathlib import Path

from agentfence.core.config import ConfigError, ConfigLayer, ConfigSource, layer_from_file
from agentfence.storage.paths import PROJECT_CONFIG_FILENAME, AppPaths


def is_private_group(gid: int, *, uid: int | None = None) -> bool:
    """True if ``gid`` is the user's own primary group with no other members.

    Debian/Ubuntu create a private group per user and default to ``umask 0002``, so
    ``0664`` files are normal and only writable by that user. This mirrors OpenSSH's
    ``StrictModes`` handling of user-private groups.
    """
    uid = os.getuid() if uid is None else uid
    try:
        account = pwd.getpwuid(uid)
        group = grp.getgrgid(gid)
    except KeyError:
        return False
    if account.pw_gid != gid:
        return False
    return all(member == account.pw_name for member in group.gr_mem)


def _refuse_if_writable_by_others(path: Path) -> None:
    info = path.resolve().stat()
    if info.st_uid not in {os.getuid(), 0}:
        raise ConfigError(f"{path} is owned by uid {info.st_uid}; refusing to load it")
    mode = stat.S_IMODE(info.st_mode)
    world_writable = bool(mode & 0o002)
    group_writable = bool(mode & 0o020) and not is_private_group(info.st_gid)
    if world_writable or group_writable:
        who = "other users" if world_writable else "a shared group"
        raise ConfigError(
            f"{path} is writable by {who} (mode {mode:04o}). Fix with: chmod go-w '{path}'"
        )


def discover_config_layers(
    *,
    paths: AppPaths,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    explicit: Path | None = None,
) -> list[ConfigLayer]:
    env = os.environ if env is None else env
    layers: list[ConfigLayer] = []

    user_file = paths.user_config_file
    if user_file.exists():
        _refuse_if_writable_by_others(user_file)
        layers.append(layer_from_file(user_file, ConfigSource.USER))

    project_file: Path | None
    if explicit is not None:
        project_file = explicit if explicit.is_absolute() else cwd / explicit
    elif env.get("AGENTFENCE_CONFIG"):
        project_file = Path(env["AGENTFENCE_CONFIG"])
        if not project_file.is_absolute():
            project_file = cwd / project_file
    else:
        candidate = cwd / PROJECT_CONFIG_FILENAME
        project_file = candidate if candidate.exists() else None

    if project_file is not None:
        if not project_file.exists():
            raise ConfigError(f"config file not found: {project_file}")
        _refuse_if_writable_by_others(project_file)
        layers.append(layer_from_file(project_file, ConfigSource.PROJECT))
    return layers
