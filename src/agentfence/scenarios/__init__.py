"""Bundled scenario manifests, one sub-directory per profile (e.g. ``mvp/``).

Manifests are *data* shipped inside the wheel and validated against the strict schema
every time they are listed. Nothing in this package is executed. The five MVP
scenarios and their benign controls arrive in milestone M3 (AF-0303..AF-0307).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from typing import Final

from pydantic import ValidationError

from agentfence.core.scenario_manifest import ScenarioManifest
from agentfence.core.strict_yaml import StrictYAMLError, load_mapping

_PROFILE: Final = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
MAX_MANIFESTS_PER_PROFILE: Final = 200


class CatalogError(ValueError):
    """A bundled manifest is invalid or the profile name is unusable."""


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    resource: str
    manifest: ScenarioManifest


def bundled_scenarios(profile: str, *, base: Traversable | None = None) -> list[CatalogEntry]:
    """Load and validate every ``*.yaml`` manifest bundled for ``profile``.

    ``base`` overrides the package root (tests only). Returns an empty list when the
    profile has no directory. Raises
    :class:`CatalogError` on the first invalid manifest: a broken bundle must not be
    partially listed as if it were complete.
    """
    if not _PROFILE.fullmatch(profile):
        raise CatalogError(f"invalid profile name {profile!r}; use lowercase letters, digits, '-'")
    root = (base if base is not None else resources.files(__name__)).joinpath(profile)
    if not root.is_dir():
        return []
    files = sorted(
        (entry for entry in root.iterdir() if entry.name.endswith(".yaml") and entry.is_file()),
        key=lambda entry: entry.name,
    )
    if len(files) > MAX_MANIFESTS_PER_PROFILE:
        raise CatalogError(f"profile {profile!r} bundles more than {MAX_MANIFESTS_PER_PROFILE}")
    entries: list[CatalogEntry] = []
    seen: dict[str, str] = {}
    for entry in files:
        name = f"{profile}/{entry.name}"
        try:
            manifest = ScenarioManifest.model_validate(
                load_mapping(entry.read_bytes(), source=name)
            )
        except (StrictYAMLError, ValidationError) as exc:
            raise CatalogError(f"bundled manifest {name} is invalid: {exc}") from exc
        if manifest.id in seen:
            raise CatalogError(
                f"duplicate scenario id {manifest.id} in {seen[manifest.id]} and {name}"
            )
        seen[manifest.id] = name
        entries.append(CatalogEntry(resource=name, manifest=manifest))
    return entries
