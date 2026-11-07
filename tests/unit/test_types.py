# SPDX-License-Identifier: Apache-2.0
"""Shared strict type tests: locked invariants and relative path patterns."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentfence.core.types import LockedFalse, LockedTrue, RelativePattern, StrictModel


class _Flags(StrictModel):
    on: LockedTrue = True
    off: LockedFalse = False


class _PathHolder(StrictModel):
    path: RelativePattern


@pytest.mark.parametrize("value", [1, "true", "yes", "on", 1.0, False, None])
def test_locked_true_rejects_everything_but_true(value: object) -> None:
    with pytest.raises(ValidationError, match="cannot be overridden"):
        _Flags.model_validate({"on": value})


@pytest.mark.parametrize("value", [0, "false", "no", True, None])
def test_locked_false_rejects_everything_but_false(value: object) -> None:
    with pytest.raises(ValidationError, match="cannot be overridden"):
        _Flags.model_validate({"off": value})


def test_locked_defaults_validate() -> None:
    flags = _Flags()
    assert flags.on is True
    assert flags.off is False


def test_unknown_keys_rejected() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        _Flags.model_validate({"on": True, "surprise": 1})


def test_models_are_immutable() -> None:
    flags = _Flags()
    with pytest.raises(ValidationError):
        flags.on = True  # type: ignore[misc]


@pytest.mark.parametrize(
    "path", ["README.md", "src/**", "tests/*.py", "notes/hidden_marker.txt", "a/b-c_d.e/[ab]?"]
)
def test_relative_patterns_accepted(path: str) -> None:
    assert _PathHolder(path=path).path == path


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        ("", "1.."),
        ("/etc/passwd", "absolute"),
        ("../outside", "'..'"),
        ("src/../../x", "'..'"),
        ("./src", "'.'"),
        ("src//x", "empty path segments"),
        ("src/", "empty path segments"),
        ("~/.ssh/id_ed25519", "may only contain"),
        ("C:\\Windows", "may only contain"),
        ("src/$(whoami)", "may only contain"),
        ("a b", "may only contain"),
        ("src/\x00x", "may only contain"),
        ("x" * 257, "1.."),
    ],
)
def test_unsafe_patterns_rejected(path: str, reason: str) -> None:
    with pytest.raises(ValidationError) as info:
        _PathHolder(path=path)
    assert reason in str(info.value)
