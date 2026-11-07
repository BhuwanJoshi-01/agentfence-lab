"""Filesystem manifest loader and scenario-pack validation (AF-0301, FR-003).

Single manifests are validated by the strict schema. A *pack* (a directory of manifests,
or the bundled catalog) gets cross-file checks the schema cannot do on its own:

* scenario IDs are unique;
* every adversarial scenario's ``benign_pair`` exists in the pack;
* every benign control is claimed by exactly one adversarial scenario;
* a pair shares the same legitimate task, profile, version, fixture generator and
  adapter capability requirements, so the control is a like-for-like workload
  (docs/04 section 9) and over-refusal stays visible;
* ``fixture.generator`` names a built-in generator. Packs cannot bring their own code
  (ADR-006), so an unknown generator is an error rather than a runtime surprise.

Files are read with ``O_NOFOLLOW`` semantics: symlinked manifests are refused, the size
is capped before reading, and each manifest's SHA-256 is recorded for provenance
(``scenarios.content_sha256`` in docs/07 section 3).
"""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pydantic import ValidationError

import agentfence.scenarios.generators  # noqa: F401 - registers built-in fixture generators
from agentfence.core.fixtures import registered_generators
from agentfence.core.scenario_manifest import ScenarioManifest
from agentfence.core.strict_yaml import MAX_DOCUMENT_BYTES, StrictYAMLError, load_mapping
from agentfence.core.vocabulary import Variant

MANIFEST_SUFFIXES: Final = (".yaml", ".yml")
MAX_PACK_FILES: Final = 500


@dataclass(frozen=True, slots=True)
class LoadedManifest:
    source: str
    """Display name: path relative to the pack root, or the file name."""
    sha256: str
    manifest: ScenarioManifest


@dataclass(frozen=True, slots=True)
class ManifestProblem:
    source: str
    message: str


class PackError(ValueError):
    """One or more manifests in a pack are invalid. ``problems`` lists every issue."""

    def __init__(self, problems: Sequence[ManifestProblem]) -> None:
        self.problems = tuple(problems)
        super().__init__("; ".join(f"{p.source}: {p.message}" for p in self.problems))


def _format_validation(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors():
        where = ".".join(str(p) for p in error["loc"]) or "<root>"
        message = error["msg"].removeprefix("Value error, ")
        if error["type"] == "extra_forbidden":
            message = "unknown field (manifests are declarative; no hooks or commands)"
        parts.append(f"{where}: {message}")
    return "; ".join(parts)


def parse_manifest(data: bytes, *, source: str) -> LoadedManifest:
    """Validate manifest bytes. Raises :class:`PackError` with a single problem."""
    try:
        manifest = ScenarioManifest.model_validate(load_mapping(data, source=source))
    except StrictYAMLError as exc:
        raise PackError([ManifestProblem(source, str(exc).removeprefix(f"{source}: "))]) from exc
    except ValidationError as exc:
        raise PackError([ManifestProblem(source, _format_validation(exc))]) from exc
    return LoadedManifest(source, hashlib.sha256(data).hexdigest(), manifest)


def read_manifest_file(path: Path, *, display: str | None = None) -> LoadedManifest:
    """Read one manifest from disk without following symlinks."""
    name = display or path.name
    try:
        # O_NONBLOCK: opening a FIFO named *.yaml must not hang waiting for a writer.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except OSError as exc:
        reason = "is a symlink" if path.is_symlink() else (exc.strerror or "cannot be opened")
        raise PackError([ManifestProblem(name, reason)]) from exc
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise PackError([ManifestProblem(name, "is not a regular file")])
        if info.st_size > MAX_DOCUMENT_BYTES:
            raise PackError([ManifestProblem(name, f"exceeds {MAX_DOCUMENT_BYTES} bytes")])
        data = handle.read(MAX_DOCUMENT_BYTES + 1)
    return parse_manifest(data, source=name)


def _pair_mismatches(adversarial: ScenarioManifest, benign: ScenarioManifest) -> list[str]:
    checks = {
        "legitimate_task": (adversarial.legitimate_task, benign.legitimate_task),
        "profile": (adversarial.profile, benign.profile),
        "version": (adversarial.version, benign.version),
        "fixture.generator": (adversarial.fixture.generator, benign.fixture.generator),
        "requires.adapter_capabilities": (
            adversarial.requires.adapter_capabilities,
            benign.requires.adapter_capabilities,
        ),
    }
    return [name for name, (a, b) in checks.items() if a != b]


def validate_pack(entries: Iterable[LoadedManifest]) -> tuple[LoadedManifest, ...]:
    """Cross-file checks. Returns entries sorted by scenario ID or raises PackError."""
    items = sorted(entries, key=lambda e: e.manifest.id)
    problems: list[ManifestProblem] = []
    by_id: dict[str, LoadedManifest] = {}
    for entry in items:
        sid = entry.manifest.id
        if sid in by_id:
            problems.append(
                ManifestProblem(entry.source, f"duplicate id {sid} (also in {by_id[sid].source})")
            )
            continue
        by_id[sid] = entry
    known_generators = registered_generators()
    for entry in by_id.values():
        generator = entry.manifest.fixture.generator
        if generator not in known_generators:
            problems.append(
                ManifestProblem(
                    entry.source,
                    f"unknown fixture generator {generator!r}; scenarios may only use built-in "
                    f"generators ({', '.join(known_generators) or 'none registered'})",
                )
            )
    claimed: dict[str, str] = {}
    for entry in by_id.values():
        manifest = entry.manifest
        if manifest.variant is not Variant.ADVERSARIAL or manifest.benign_pair is None:
            continue
        pair = by_id.get(manifest.benign_pair)
        if pair is None:
            problems.append(
                ManifestProblem(entry.source, f"benign pair {manifest.benign_pair} not found")
            )
            continue
        claimed[pair.manifest.id] = manifest.id
        mismatched = _pair_mismatches(manifest, pair.manifest)
        if mismatched:
            problems.append(
                ManifestProblem(
                    entry.source,
                    f"benign pair {pair.manifest.id} differs in: {', '.join(mismatched)}",
                )
            )
    for entry in by_id.values():
        if entry.manifest.variant is Variant.BENIGN and entry.manifest.id not in claimed:
            problems.append(
                ManifestProblem(entry.source, "benign control is not paired with any scenario")
            )
    if problems:
        raise PackError(problems)
    return tuple(by_id.values())


def load_pack(root: Path) -> tuple[LoadedManifest, ...]:
    """Load ``*.yaml``/``*.yml`` from a file or a directory tree and validate the pack.

    Collects *every* problem before raising, so contributors see all issues at once.
    """
    if root.is_symlink():
        raise PackError([ManifestProblem(root.name, "is a symlink")])
    if root.is_file():
        return validate_pack([read_manifest_file(root)])
    if not root.is_dir():
        raise PackError([ManifestProblem(str(root), "no such file or directory")])
    files = sorted(p for p in root.rglob("*") if p.suffix in MANIFEST_SUFFIXES and not p.is_dir())
    if len(files) > MAX_PACK_FILES:
        raise PackError([ManifestProblem(str(root), f"more than {MAX_PACK_FILES} manifests")])
    loaded: list[LoadedManifest] = []
    problems: list[ManifestProblem] = []
    for path in files:
        display = path.relative_to(root).as_posix()
        try:
            loaded.append(read_manifest_file(path, display=display))
        except PackError as exc:
            problems.extend(exc.problems)
    try:
        validated = validate_pack(loaded)
    except PackError as exc:
        problems.extend(exc.problems)
        validated = ()
    if problems:
        raise PackError(problems)
    return validated
