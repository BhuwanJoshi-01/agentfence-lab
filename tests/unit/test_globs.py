# SPDX-License-Identifier: Apache-2.0
"""Boundary glob semantics (used by the evaluator)."""

from __future__ import annotations

import pytest

from agentfence.core.globs import matches, matches_any


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("src/**", "src/a/b.py", True),
        ("src/**", "src", True),
        ("src/**", "src/a", True),
        ("src/**", "srcx/a", False),
        ("src/**", "other/src/a", False),
        ("**", "a/b/c", True),
        ("**", "x", True),
        ("**/*.py", "a.py", True),
        ("**/*.py", "x/y/a.py", True),
        ("**/*.py", "a.pyc", False),
        ("a/**/b", "a/b", True),
        ("a/**/b", "a/x/y/b", True),
        ("a/**/b", "a/xb", False),
        ("notes/hidden_marker.txt", "notes/hidden_marker.txt", True),
        ("notes/*", "notes/a", True),
        ("notes/*", "notes/a/b", False),
        ("*", "a/b", False),
        ("tests/*.py", "tests/test_x.py", True),
        ("[ab].txt", "a.txt", True),
        ("[ab].txt", "c.txt", False),
        ("[!ab].txt", "a.txt", False),
        ("[!ab].txt", "c.txt", True),
        ("[a-c].txt", "b.txt", True),
        ("[a-c].txt", "-.txt", False),
        ("?.md", "a.md", True),
        ("?.md", "ab.md", False),
        ("?", "/", False),
        ("README.md", "readme.md", False),
        ("a.b", "axb", False),
        ("a+b", "a+b", True),
        ("x[", "x[", True),
        ("[]", "[]", True),
    ],
)
def test_glob_semantics(pattern: str, path: str, *, expected: bool) -> None:
    assert matches(path, pattern) is expected


def test_matches_any() -> None:
    assert matches_any("tests/t.py", ["src/**", "tests/**"])
    assert not matches_any("notes/x", ["src/**", "tests/**"])
    assert not matches_any("anything", [])
