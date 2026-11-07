# SPDX-License-Identifier: Apache-2.0
"""Bundled scenario manifests, one sub-directory per profile (e.g. ``mvp/``).

Manifests are *data* shipped inside the wheel and validated against the strict schema
every time they are listed. Nothing in this package is executed. The five MVP
scenarios and their benign controls arrive in milestone M3 (AF-0303..AF-0307).
"""

from __future__ import annotations

import re
from importlib import resources
from importlib.resources.abc import Traversable
from typing import Final

from agentfence.core.strict_yaml import MAX_DOCUMENT_BYTES
from agentfence.scenarios.loader import LoadedManifest, PackError, parse_manifest, validate_pack

_PROFILE: Final = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
MAX_MANIFESTS_PER_PROFILE: Final = 200

CatalogEntry = LoadedManifest
"""Kept as an alias: catalog entries are loaded manifests with provenance."""


class CatalogError(ValueError):
    """A bundled manifest is invalid or the profile name is unusable."""


def bundled_scenarios(profile: str, *, base: Traversable | None = None) -> list[LoadedManifest]:
    """Load and validate every ``*.yaml`` manifest bundled for ``profile``.

    ``base`` overrides the package root (tests only). Returns an empty list when the
    profile has no directory. Raises :class:`CatalogError` if any manifest or the pack
    as a whole is invalid: a broken bundle must not be partially listed as complete.
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
    try:
        loaded = []
        for entry in files:
            data = entry.read_bytes()
            if len(data) > MAX_DOCUMENT_BYTES:
                raise CatalogError(f"bundled manifest {profile}/{entry.name} is too large")
            loaded.append(parse_manifest(data, source=f"{profile}/{entry.name}"))
        return list(validate_pack(loaded))
    except PackError as exc:
        raise CatalogError(f"bundled scenarios for {profile!r} are invalid: {exc}") from exc
