# SPDX-License-Identifier: Apache-2.0
"""XDG-aware state and config locations with private permissions (AF-0105).

Defaults (docs/02 section 9, docs/06 section 6):

* state:  ``$AGENTFENCE_STATE_DIR`` or ``$XDG_STATE_HOME/agentfence`` or
  ``~/.local/state/agentfence``
* config: ``$XDG_CONFIG_HOME/agentfence`` or ``~/.config/agentfence``
* run artifacts: ``<state>/runs/<uuid>/`` with mode ``0700``; files ``0600``

Per the XDG Base Directory spec, relative ``XDG_*`` values are ignored. An explicit
``AGENTFENCE_STATE_DIR`` that is relative is an error rather than silently ignored,
because the user asked for it.

Existing directories are never loosened *or* silently tightened: if a directory we
would write into is group/world-accessible or owned by someone else, we refuse and
tell the user how to fix it.
"""

from __future__ import annotations

import os
import stat
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

APP_DIR_NAME: Final = "agentfence"
PRIVATE_DIR_MODE: Final = 0o700
PRIVATE_FILE_MODE: Final = 0o600
USER_CONFIG_FILENAME: Final = "config.yaml"
PROJECT_CONFIG_FILENAME: Final = "agentfence.yaml"


class InsecurePathError(PermissionError):
    """A path we would read or write is not private to the current user."""


class PathConfigError(ValueError):
    """An environment-provided path is unusable."""


@dataclass(frozen=True, slots=True)
class AppPaths:
    state_dir: Path
    config_dir: Path

    @property
    def runs_dir(self) -> Path:
        return self.state_dir / "runs"

    @property
    def database_path(self) -> Path:
        return self.state_dir / "agentfence.sqlite3"

    @property
    def user_config_file(self) -> Path:
        return self.config_dir / USER_CONFIG_FILENAME

    def run_dir(self, run_id: str) -> Path:
        """Directory for one run. ``run_id`` must be a canonical UUID string."""
        try:
            parsed = uuid.UUID(run_id)
        except ValueError as exc:
            raise PathConfigError(f"run id {run_id!r} is not a UUID") from exc
        if str(parsed) != run_id:
            raise PathConfigError(f"run id {run_id!r} is not in canonical UUID form")
        return self.runs_dir / run_id


def _xdg_base(env: Mapping[str, str], var: str, fallback: Path) -> Path:
    value = env.get(var, "")
    if value and Path(value).is_absolute():
        return Path(value)
    return fallback


def resolve_paths(env: Mapping[str, str] | None = None, home: Path | None = None) -> AppPaths:
    """Compute locations without touching the filesystem."""
    env = os.environ if env is None else env
    home = Path(env.get("HOME") or Path.home()) if home is None else home
    explicit_state = env.get("AGENTFENCE_STATE_DIR", "")
    if explicit_state:
        if not Path(explicit_state).is_absolute():
            raise PathConfigError(
                f"AGENTFENCE_STATE_DIR must be an absolute path, got {explicit_state!r}"
            )
        state = Path(explicit_state)
    else:
        state = _xdg_base(env, "XDG_STATE_HOME", home / ".local" / "state") / APP_DIR_NAME
    config = _xdg_base(env, "XDG_CONFIG_HOME", home / ".config") / APP_DIR_NAME
    return AppPaths(
        state_dir=Path(os.path.normpath(state)), config_dir=Path(os.path.normpath(config))
    )


def ensure_private_dir(path: Path) -> Path:
    """Create ``path`` (and missing parents) as ``0700``, or verify an existing one.

    Raises :class:`InsecurePathError` if the final directory is a symlink, not owned by
    the current user, or accessible to group/other. Parents that already exist are not
    modified.
    """
    if path.is_symlink():
        raise InsecurePathError(f"{path} is a symlink; refusing to use it as a private directory")
    missing: list[Path] = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=PRIVATE_DIR_MODE)
        except FileExistsError:
            continue  # created concurrently; the final directory is verified below
        directory.chmod(PRIVATE_DIR_MODE)  # exact mode regardless of umask
    _verify_private(path, expect_dir=True)
    return path


def _verify_private(path: Path, *, expect_dir: bool) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        raise InsecurePathError(f"{path} is a symlink; refusing to use it")
    if expect_dir and not stat.S_ISDIR(info.st_mode):
        raise InsecurePathError(f"{path} exists but is not a directory")
    if info.st_uid != os.getuid():
        raise InsecurePathError(f"{path} is owned by uid {info.st_uid}, not the current user")
    loose = stat.S_IMODE(info.st_mode) & 0o077
    if loose:
        raise InsecurePathError(
            f"{path} has mode {stat.S_IMODE(info.st_mode):04o}; group/other access is not "
            f"allowed for AgentFence state. Fix with: chmod {'700' if expect_dir else '600'} "
            f"'{path}'"
        )


def verify_private_file(path: Path) -> None:
    """Raise :class:`InsecurePathError` unless ``path`` is a private regular file we own."""
    _verify_private(path, expect_dir=False)
    if not path.is_file():
        raise InsecurePathError(f"{path} is not a regular file")


def safe_child(root: Path, *parts: str) -> Path:
    """Join name components under ``root``; each part must be a single plain name."""
    for part in parts:
        if not part or part in {".", ".."} or "/" in part or "\\" in part or "\x00" in part:
            raise PathConfigError(f"unsafe path component {part!r}")
    candidate = root.joinpath(*parts)
    resolved_root = root.resolve()
    resolved = candidate.resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise PathConfigError(f"{candidate} resolves outside {root}")
    return candidate
