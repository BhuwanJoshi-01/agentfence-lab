# SPDX-License-Identifier: Apache-2.0
"""Utility oracles: did the legitimate task succeed? (AF-0405, ADR-002)

An oracle is registered per ``(fixture generator, assertion fixture_id)``. It receives a
reader for workspace files and the generated :class:`~agentfence.core.fixtures.Fixture`
(including controller-only markers) and returns a pass/fail result with a short reason
code. Oracles must be deterministic and must never execute workspace content: they
inspect bytes and, at most, parse them (e.g. with :mod:`ast`).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from agentfence.core.fixtures import Fixture

_REASON: Final = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

Reader = Callable[[str], bytes | None]
"""Returns file bytes, or ``None`` if the file is missing or unsafe to read."""


@dataclass(frozen=True, slots=True)
class OracleResult:
    passed: bool
    reason: str

    def __post_init__(self) -> None:
        if not _REASON.fullmatch(self.reason):
            raise ValueError("oracle reason must be a short snake_case code")


Oracle = Callable[[Reader, Fixture], OracleResult]

_ORACLES: dict[tuple[str, str], Oracle] = {}


def register_oracle(generator: str, fixture_id: str) -> Callable[[Oracle], Oracle]:
    def wrap(oracle: Oracle) -> Oracle:
        key = (generator, fixture_id)
        if key in _ORACLES:
            raise ValueError(f"oracle {key} registered twice")
        _ORACLES[key] = oracle
        return oracle

    return wrap


def get_oracle(generator: str, fixture_id: str) -> Oracle | None:
    return _ORACLES.get((generator, fixture_id))


def registered_oracles() -> Mapping[tuple[str, str], Oracle]:
    return MappingProxyType(dict(_ORACLES))


Solution = Callable[[Reader, Fixture], Mapping[str, bytes]]
"""Reference solution: the file writes that complete one assertion's task. Used only by
the deterministic mock adapter so its "safe" profile genuinely does the work."""

_SOLUTIONS: dict[tuple[str, str], Solution] = {}


def register_solution(generator: str, fixture_id: str) -> Callable[[Solution], Solution]:
    def wrap(solution: Solution) -> Solution:
        key = (generator, fixture_id)
        if key in _SOLUTIONS:
            raise ValueError(f"solution {key} registered twice")
        _SOLUTIONS[key] = solution
        return solution

    return wrap


def get_solution(generator: str, fixture_id: str) -> Solution | None:
    return _SOLUTIONS.get((generator, fixture_id))
