# SPDX-License-Identifier: Apache-2.0
"""Shared strict pydantic building blocks.

``StrictModel`` forbids unknown keys and type coercion. Security invariants use
:data:`LockedTrue` / :data:`LockedFalse`, which reject look-alikes such as ``1``,
``"true"`` or ``"yes"`` that plain ``Literal[True]`` would accept.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Annotated, Final, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field


class StrictModel(BaseModel):
    """Immutable, no extra keys, no implicit coercion."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
        str_strip_whitespace=False,
        use_enum_values=False,
    )


INVARIANT_HINT: Final = "this safety invariant cannot be overridden in v0.1"


def _require_exact(expected: bool) -> BeforeValidator:
    def check(value: object) -> object:
        if value is not expected:
            raise ValueError(f"must be {str(expected).lower()}; {INVARIANT_HINT}")
        return value

    return BeforeValidator(check)


LockedTrue = Annotated[Literal[True], _require_exact(True)]
LockedFalse = Annotated[Literal[False], _require_exact(False)]

SCENARIO_ID_PATTERN: Final = r"^AF-[A-Z]{2,12}-\d{3}(-B)?$"
SEMVER_PATTERN: Final = r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"
SLUG_PATTERN: Final = r"^[a-z][a-z0-9-]{0,62}$"

ScenarioId = Annotated[str, Field(pattern=SCENARIO_ID_PATTERN)]
SemVer = Annotated[str, Field(pattern=SEMVER_PATTERN)]
Slug = Annotated[str, Field(pattern=SLUG_PATTERN)]

_GLOB_ALLOWED: Final = re.compile(r"^[A-Za-z0-9._\-/*?\[\]]+$")
MAX_PATH_CHARS: Final = 256


def _validate_relative_pattern(value: str) -> str:
    """Workspace-relative path or glob. No traversal, absolute paths, home or drive refs.

    This is a *syntactic* gate only. Canonical resolution against the real
    workspace root (symlinks, TOCTOU) happens at use time in the loader/runtime.
    """
    if not value or len(value) > MAX_PATH_CHARS:
        raise ValueError(f"path must be 1..{MAX_PATH_CHARS} characters")
    if not _GLOB_ALLOWED.fullmatch(value):
        raise ValueError(
            "path may only contain letters, digits, '.', '_', '-', '/', '*', '?', '[', ']'"
        )
    if value.startswith("/"):
        raise ValueError("absolute paths are not allowed; use a workspace-relative path")
    segments = value.split("/")
    if any(seg == "" for seg in segments):
        raise ValueError("empty path segments ('//' or trailing '/') are not allowed")
    if any(seg in {".", ".."} for seg in segments):
        raise ValueError("'.' and '..' path segments are not allowed")
    return value


RelativePattern = Annotated[str, AfterValidator(_validate_relative_pattern)]


def _validate_display_text(value: str) -> str:
    """Reject control and invisible formatting characters in human-facing text.

    Unicode categories ``Cc`` (C0/C1 controls, ESC, BEL, DEL) and ``Cf`` (format
    characters such as bidi overrides U+202E and zero-width joiners) can rewrite a
    terminal or disguise text ("Trojan Source"). Untrusted manifests must not carry them.
    """
    for index, char in enumerate(value):
        if unicodedata.category(char) in {"Cc", "Cf"}:
            raise ValueError(
                f"control or invisible formatting character U+{ord(char):04X} at position "
                f"{index} is not allowed"
            )
    return value


DisplayText = Annotated[str, AfterValidator(_validate_display_text)]

__all__ = [
    "INVARIANT_HINT",
    "DisplayText",
    "LockedFalse",
    "LockedTrue",
    "RelativePattern",
    "ScenarioId",
    "SemVer",
    "Slug",
    "StrictModel",
]
