# SPDX-License-Identifier: Apache-2.0
"""Find and remove containers and volumes left behind by runs that never finished (AF-0207).

Every container and workspace volume AgentFence creates carries ``io.agentfence.run`` and
``io.agentfence.attempt`` labels (:mod:`agentfence.runtime.containers`). A ``finally``
block cannot run after ``SIGKILL``, a power cut or an interpreter crash (docs/02 section
5), so leftovers are found by label and removed explicitly.

Safety rules:

* Listing asks the engine to filter by the run label, and every record is checked again
  here: it must carry a well-formed run label, a container ID must be hexadecimal and a
  volume name must have the ``agentfence-`` prefix. Anything else is ignored, never
  removed, so an engine that returns unrelated objects cannot make us delete them.
* Resources of the active run are never stale.
* Containers are removed before volumes (a volume in use cannot be removed).
* With a clock, any resource of a run created within the grace period keeps the whole
  run: a concurrent ``doctor --self-test`` or run is never torn down. Creation times come
  from the engine (Podman: ``Created`` seconds, volume ``CreatedAt``; Docker: container
  ``CreatedAt``); a resource without one counts as old.
* Podman removes with ``--time 0``: a container whose PID 1 ignores ``SIGTERM`` would
  otherwise hold removal for ten seconds.
* Every engine call goes through the bounded process supervisor with a minimal
  environment (:func:`agentfence.runtime.engine_probe.run_bounded`).

Output formats: Podman's ``--format json`` is one JSON array with ``Id`` and a ``Labels``
object; Docker's ``--format '{{json .}}'`` is one JSON object per line with ``ID`` and
``Labels`` as a ``key=value,...`` string. Both shapes are accepted for either engine.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final, Literal

from agentfence.core.sandbox_policy import RUN_LABEL, VOLUME_PREFIX
from agentfence.runtime.containers import Engine
from agentfence.runtime.engine_probe import QueryResult, Runner, run_bounded

Kind = Literal["container", "volume"]
_CONTAINER_ID: Final = re.compile(r"^[0-9a-f]{12,64}$")
_VOLUME_NAME: Final = re.compile(rf"^{VOLUME_PREFIX}[a-z0-9][a-z0-9-]{{0,94}}$")
_RUN_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_LIST: Final[Mapping[Engine, Mapping[Kind, tuple[str, ...]]]] = {
    "podman": {
        "container": ("ps", "--all", "--filter", f"label={RUN_LABEL}", "--format", "json"),
        "volume": ("volume", "ls", "--filter", f"label={RUN_LABEL}", "--format", "json"),
    },
    "docker": {
        "container": (
            "ps",
            "--all",
            "--no-trunc",
            "--filter",
            f"label={RUN_LABEL}",
            "--format",
            "{{json .}}",
        ),
        "volume": ("volume", "ls", "--filter", f"label={RUN_LABEL}", "--format", "{{json .}}"),
    },
}
_REMOVE: Final[Mapping[Engine, Mapping[Kind, tuple[str, ...]]]] = {
    "podman": {
        "container": ("rm", "--force", "--time", "0"),
        "volume": ("volume", "rm", "--force"),
    },
    "docker": {"container": ("rm", "--force"), "volume": ("volume", "rm", "--force")},
}
STALE_GRACE_SECONDS: Final = 600.0


class ReconcileError(RuntimeError):
    """The engine could not be listed or its output could not be parsed."""


@dataclass(frozen=True, slots=True, order=True)
class LabelledResource:
    kind: Kind
    ref: str
    """Container ID or volume name."""
    run_id: str
    created: float | None = field(default=None, compare=False)
    """Unix time the engine reports for the resource, if any."""


@dataclass(frozen=True, slots=True)
class Removal:
    removed: tuple[LabelledResource, ...]
    failed: tuple[LabelledResource, ...]


def _records(stdout: bytes) -> list[Mapping[str, Any]]:
    text = stdout.decode("utf-8").strip()
    if not text:
        return []
    if text.startswith("["):
        data = json.loads(text)
        items = data if isinstance(data, list) else []
    else:
        items = [json.loads(line) for line in text.splitlines() if line.strip()]
    return [item for item in items if isinstance(item, Mapping)]


def _labels(raw: object) -> dict[str, str]:
    if isinstance(raw, Mapping):
        return {str(k): str(v) for k, v in raw.items()}
    if isinstance(raw, str):
        pairs = (part.split("=", 1) for part in raw.split(",") if "=" in part)
        return {pair[0]: pair[1] for pair in pairs}
    return {}


def _ref(kind: Kind, record: Mapping[str, Any]) -> str | None:
    if kind == "container":
        ref = record.get("Id", record.get("ID"))
        return ref if isinstance(ref, str) and _CONTAINER_ID.match(ref) else None
    name = record.get("Name")
    return name if isinstance(name, str) and _VOLUME_NAME.match(name) else None


def _created(record: Mapping[str, Any]) -> float | None:
    """Podman ``Created`` (Unix seconds) or an RFC 3339 / Docker ``CreatedAt`` string."""
    raw = record.get("Created")
    if isinstance(raw, int | float) and not isinstance(raw, bool):
        return float(raw)
    text = record.get("CreatedAt")
    if not isinstance(text, str):
        return None
    parts = text.split()
    candidates = [text, " ".join(parts[:3])] if len(parts) >= 3 else [text]
    for candidate in candidates:
        trimmed = re.sub(r"(\.\d{6})\d+", r"\1", candidate)
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S %z"):
            try:
                return datetime.strptime(trimmed, fmt).timestamp()
            except ValueError:
                continue
    return None


def list_labelled(
    engine: Engine, executable: str, run: Runner = run_bounded
) -> tuple[LabelledResource, ...]:
    """Containers and volumes carrying a well-formed AgentFence run label."""
    found: list[LabelledResource] = []
    for kind in ("container", "volume"):
        result = run([executable, *_LIST[engine][kind]])
        if result.returncode != 0:
            raise ReconcileError(f"could not list {kind}s with {engine}")
        try:
            records = _records(result.stdout)
        except (UnicodeDecodeError, ValueError) as exc:
            raise ReconcileError(f"unparseable {kind} list from {engine}") from exc
        for record in records:
            run_id = _labels(record.get("Labels")).get(RUN_LABEL)
            ref = _ref(kind, record)
            if run_id and _RUN_ID.match(run_id) and ref:
                found.append(LabelledResource(kind, ref, run_id, _created(record)))
    return tuple(sorted(found))


def plan_stale(
    resources: Sequence[LabelledResource],
    *,
    active_run: str | None = None,
    now: float | None = None,
    grace: float = STALE_GRACE_SECONDS,
) -> tuple[LabelledResource, ...]:
    """Everything not belonging to ``active_run`` or to a run with a recent resource;
    containers first, then volumes. Without ``now`` no run counts as recent."""
    busy = {active_run}
    if now is not None:
        busy |= {r.run_id for r in resources if r.created is not None and r.created > now - grace}
    stale = [r for r in resources if r.run_id not in busy]
    return tuple(sorted(stale, key=lambda r: (r.kind != "container", r.ref)))


def remove_stale(
    executable: str,
    stale: Sequence[LabelledResource],
    run: Runner = run_bounded,
    *,
    engine: Engine = "podman",
) -> Removal:
    """Force-remove each stale resource. Failures are reported, never raised."""
    removed: list[LabelledResource] = []
    failed: list[LabelledResource] = []
    for resource in sorted(stale, key=lambda r: (r.kind != "container", r.ref)):
        valid = (_CONTAINER_ID if resource.kind == "container" else _VOLUME_NAME).match(
            resource.ref
        )
        result = (
            run([executable, *_REMOVE[engine][resource.kind], resource.ref])
            if valid
            else QueryResult(returncode=-1, stdout=b"")
        )
        (removed if result.returncode == 0 else failed).append(resource)
    return Removal(removed=tuple(removed), failed=tuple(failed))
