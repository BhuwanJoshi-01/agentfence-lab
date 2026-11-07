# SPDX-License-Identifier: Apache-2.0
"""Hardened YAML loader tests (AF-0103)."""

from __future__ import annotations

import pytest

from agentfence.core.strict_yaml import MAX_DOCUMENT_BYTES, StrictYAMLError, load_mapping


def test_valid_mapping_round_trips() -> None:
    assert load_mapping("a: 1\nb:\n  c: [x, y]\n", source="t") == {"a": 1, "b": {"c": ["x", "y"]}}


def test_bytes_input_accepted() -> None:
    assert load_mapping(b"a: 1\n", source="t") == {"a": 1}


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("no_host_mounts: true\nno_host_mounts: false\n", "duplicate key 'no_host_mounts'"),
        ("runtime:\n  a: 1\n  a: 2\n", "duplicate key 'a'"),
        ("a: &x [1, 2]\nb: *x\n", "anchors are not allowed"),
        ("a: &x 1\n", "anchors are not allowed"),
        ("- 1\n- 2\n", "top level must be a mapping"),
        ("just a string\n", "top level must be a mapping"),
        ("a: 1\n---\nb: 2\n", "exactly one YAML document"),
        ("", "exactly one YAML document"),
        ("1: one\n", "keys must be strings"),
        ("a:\n  - {2: x}\n", "keys must be strings"),
        ("a: !!python/object/apply:os.system ['true']\n", "could not determine a constructor"),
        ("a: [unterminated\n", "t:"),
    ],
)
def test_rejected_inputs(text: str, fragment: str) -> None:
    with pytest.raises(StrictYAMLError, match="t:") as info:
        load_mapping(text, source="t")
    assert fragment in str(info.value)


def test_alias_expansion_bomb_rejected_before_expansion() -> None:
    bomb = "a: &a [x, x]\nb: &b [*a, *a]\nc: &c [*b, *b]\nd: [*c, *c]\n"
    with pytest.raises(StrictYAMLError, match="anchors are not allowed"):
        load_mapping(bomb, source="bomb")


def test_oversized_document_rejected() -> None:
    big = "a: " + "x" * MAX_DOCUMENT_BYTES + "\n"
    with pytest.raises(StrictYAMLError, match="limit"):
        load_mapping(big, source="big")


def test_invalid_utf8_rejected() -> None:
    with pytest.raises(StrictYAMLError, match="UTF-8"):
        load_mapping(b"a: \xff\xfe\n", source="bin")
