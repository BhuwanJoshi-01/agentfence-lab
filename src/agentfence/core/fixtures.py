"""Deterministic, synthetic fixture model and generator registry (AF-0302).

A :class:`Fixture` is an in-memory workspace: a sorted set of files plus markers that
only the trusted controller knows (canary nonces, synthetic secret values). Generators
are pure functions of ``(seed, variant)``; the same inputs always produce byte-identical
files and the same digest, so a finding can be reproduced exactly (NFR-004).

Markers are derived with HMAC-SHA256 keyed by the seed, never with :mod:`random`, so
they are stable across Python versions and platforms. Synthetic secret values always
start with ``AF_FAKE_`` (docs/03 section 7) and carry no authentication value.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final

from agentfence.core.vocabulary import Variant

MAX_FIXTURE_FILES: Final = 64
MAX_FIXTURE_BYTES: Final = 256 * 1024
SYNTHETIC_PREFIX: Final = "AF_FAKE_"
_PATH: Final = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")
_SEED_MAX: Final = 2**63 - 1


class FixtureError(ValueError):
    """A generator produced an invalid fixture or was asked for something unknown."""


def _check_path(path: str) -> str:
    if not _PATH.fullmatch(path) or any(seg in {".", ".."} for seg in path.split("/")):
        raise FixtureError(f"fixture path {path!r} must be a plain workspace-relative path")
    return path


@dataclass(frozen=True, slots=True)
class FixtureFile:
    path: str
    content: bytes
    protected: bool = False
    """Synthetic protected asset (e.g. a fake token). Never exported in reports."""

    def __post_init__(self) -> None:
        _check_path(self.path)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


@dataclass(frozen=True, slots=True)
class Fixture:
    generator: str
    generator_version: str
    variant: Variant
    seed: int
    files: tuple[FixtureFile, ...]
    markers: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    """Controller-side values (canary, synthetic secret). Not a file in the workspace."""

    def __post_init__(self) -> None:
        paths = [f.path for f in self.files]
        if list(paths) != sorted(paths):
            raise FixtureError("fixture files must be sorted by path")
        if len(set(paths)) != len(paths):
            raise FixtureError("duplicate fixture paths")
        if not self.files or len(self.files) > MAX_FIXTURE_FILES:
            raise FixtureError(f"a fixture needs 1..{MAX_FIXTURE_FILES} files")
        if sum(len(f.content) for f in self.files) > MAX_FIXTURE_BYTES:
            raise FixtureError(f"fixture exceeds {MAX_FIXTURE_BYTES} bytes")
        prefixes = {p + "/" for p in paths}
        if any(p.startswith(tuple(prefixes)) for p in paths):
            raise FixtureError("a fixture path cannot be both a file and a directory")
        for name, value in self.markers.items():
            if name.startswith("secret") and not value.startswith(SYNTHETIC_PREFIX):
                raise FixtureError(f"synthetic secret marker {name!r} must start with AF_FAKE_")

    def file(self, path: str) -> FixtureFile:
        for item in self.files:
            if item.path == path:
                return item
        raise KeyError(path)

    @property
    def digest(self) -> str:
        """SHA-256 over the sorted ``(path, file sha256, protected)`` list."""
        h = hashlib.sha256()
        for item in self.files:
            h.update(f"{item.path}\0{item.sha256}\0{int(item.protected)}\n".encode())
        return h.hexdigest()


def derive_marker(seed: int, label: str, *, length: int = 16) -> str:
    """Deterministic hex marker for ``label`` under ``seed`` (HMAC-SHA256)."""
    if not 0 <= seed <= _SEED_MAX:
        raise FixtureError("seed must be between 0 and 2**63-1")
    key = seed.to_bytes(8, "big")
    return hmac.new(key, label.encode("utf-8"), hashlib.sha256).hexdigest()[:length]


def derive_int(seed: int, label: str, low: int, high: int) -> int:
    """Deterministic integer in ``[low, high]`` for ``label`` under ``seed``."""
    if low > high:
        raise FixtureError("low must not exceed high")
    value = int(derive_marker(seed, label, length=16), 16)
    return low + value % (high - low + 1)


Generator = Callable[[int, Variant], Fixture]


@dataclass(frozen=True, slots=True)
class _Registered:
    version: str
    build: Generator


_REGISTRY: dict[str, _Registered] = {}


def register(name: str, version: str) -> Callable[[Generator], Generator]:
    """Decorator: register a generator under a manifest ``fixture.generator`` name."""

    def wrap(build: Generator) -> Generator:
        if name in _REGISTRY:
            raise FixtureError(f"generator {name!r} registered twice")
        _REGISTRY[name] = _Registered(version, build)
        return build

    return wrap


def registered_generators() -> Mapping[str, str]:
    """Name -> version for every registered generator."""
    return MappingProxyType({name: entry.version for name, entry in sorted(_REGISTRY.items())})


def generate(name: str, variant: Variant, seed: int) -> Fixture:
    """Build a fixture and check that the generator honored its contract."""
    entry = _REGISTRY.get(name)
    if entry is None:
        raise FixtureError(f"unknown fixture generator {name!r}")
    fixture = entry.build(seed, variant)
    if (fixture.generator, fixture.generator_version) != (name, entry.version):
        raise FixtureError(f"generator {name!r} returned a fixture labeled {fixture.generator!r}")
    if fixture.variant is not variant or fixture.seed != seed:
        raise FixtureError(f"generator {name!r} ignored the requested variant or seed")
    return fixture
