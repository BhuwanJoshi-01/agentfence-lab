"""Strict ``agentfence.yaml`` configuration model and layered resolution (AF-0103).

Precedence (docs/02 section 10): built-in defaults -> user config -> project config ->
explicit CLI flags. Later layers override earlier ones key by key; lists are replaced,
not merged.

Deny-first invariants (no host mounts, mandatory preflight, offline default, exposure and
observability required for PASS, redaction, loopback-only UI) are typed as locked
literals, so *no* layer, including CLI flags, can weaken them in v0.1. Unknown keys are
rejected rather than ignored, which also means a project config cannot introduce shell
commands, network hosts or filesystem roots: the schema has no place for them.
"""

from __future__ import annotations

import ipaddress
import itertools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Final, Literal

from pydantic import AfterValidator, Field, ValidationError

from agentfence.core.strict_yaml import StrictYAMLError, load_mapping
from agentfence.core.types import LockedFalse, LockedTrue, ScenarioId, Slug, StrictModel


class RuntimeBackend(StrEnum):
    AUTO_ROOTLESS = "auto-rootless"
    PODMAN_ROOTLESS = "podman-rootless"
    DOCKER_ROOTLESS = "docker-rootless"


class ResourceLimits(StrictModel):
    wall_seconds: Annotated[int, Field(ge=1, le=3600)] = 120
    memory_mb: Annotated[int, Field(ge=64, le=16384)] = 1024
    pids: Annotated[int, Field(ge=16, le=4096)] = 128
    cpu_quota: Annotated[float, Field(gt=0, le=16)] = 1.0
    max_log_bytes: Annotated[int, Field(ge=1024, le=64 * 1024 * 1024)] = 1024 * 1024


class RuntimeConfig(StrictModel):
    backend: Annotated[RuntimeBackend, Field(strict=False)] = RuntimeBackend.AUTO_ROOTLESS
    allowed_os: tuple[Literal["linux"], ...] = ("linux",)
    offline_default: LockedTrue = True
    max_parallel: Annotated[int, Field(ge=1, le=4)] = 1
    isolation_preflight_required: LockedTrue = True
    no_host_mounts: LockedTrue = True
    default_resource_limits: ResourceLimits = ResourceLimits()


class AgentConfig(StrictModel):
    kind: Slug = "mock"
    profile: Slug = "safe"


def _unique_ids(ids: tuple[str, ...]) -> tuple[str, ...]:
    if len(set(ids)) != len(ids):
        raise ValueError("contains duplicate scenario IDs")
    return ids


class ScenariosConfig(StrictModel):
    profile: Slug = "mvp"
    repeat: Annotated[int, Field(ge=1, le=100)] = 1
    include: Annotated[tuple[ScenarioId, ...], AfterValidator(_unique_ids)] = ()


class EvaluationConfig(StrictModel):
    require_exposure_for_pass: LockedTrue = True
    require_observability_for_pass: LockedTrue = True
    fail_on_inconclusive: bool = False


class StorageConfig(StrictModel):
    retention_days: Annotated[int, Field(ge=1, le=3650)] = 30
    save_full_raw_trace: bool = False
    redact_all_synthetic_secret_values: LockedTrue = True


def _loopback_only(value: str) -> str:
    if value == "localhost":
        return value
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError("ui.listen must be a loopback IP address or 'localhost'") from exc
    if not address.is_loopback:
        raise ValueError(
            f"ui.listen={value} is not loopback; the dashboard never binds a remote interface"
        )
    return value


class UIConfig(StrictModel):
    listen: Annotated[str, AfterValidator(_loopback_only)] = "127.0.0.1"
    port: Annotated[int, Field(ge=1024, le=65535)] = 8484
    allow_external_connections: LockedFalse = False


class ProjectConfig(StrictModel):
    name: Slug = "agentfence-project"


class AgentFenceConfig(StrictModel):
    """Root of ``agentfence.yaml``. ``AgentFenceConfig()`` is the safe default."""

    schema_version: Literal[1] = 1
    project: ProjectConfig = ProjectConfig()
    runtime: RuntimeConfig = RuntimeConfig()
    agent: AgentConfig = AgentConfig()
    scenarios: ScenariosConfig = ScenariosConfig()
    evaluation: EvaluationConfig = EvaluationConfig()
    storage: StorageConfig = StorageConfig()
    ui: UIConfig = UIConfig()


class ConfigSource(StrEnum):
    DEFAULTS = "defaults"
    USER = "user"
    PROJECT = "project"
    CLI = "cli"


_LAYER_ORDER: Final = (
    ConfigSource.DEFAULTS,
    ConfigSource.USER,
    ConfigSource.PROJECT,
    ConfigSource.CLI,
)


@dataclass(frozen=True, slots=True)
class ConfigLayer:
    source: ConfigSource
    origin: str
    """Human-readable origin, e.g. a canonical file path or ``--repeat``."""
    data: Mapping[str, Any]


class ConfigError(ValueError):
    """Invalid configuration. ``str()`` is a user-facing message that proposes a fix."""


@dataclass(frozen=True, slots=True)
class ResolvedConfig:
    config: AgentFenceConfig
    provenance: Mapping[str, str]
    """Dotted key -> ``"<source>:<origin>"`` for every leaf a non-default layer set."""


MAX_CONFIG_BYTES: Final = 64 * 1024


def layer_from_file(path: Path, source: ConfigSource) -> ConfigLayer:
    """Read one config file through the hardened YAML loader.

    The file must be a regular file (symlinks are resolved first and the
    canonical path is what gets recorded in provenance).
    """
    canonical = path.expanduser().resolve(strict=False)
    if not canonical.is_file():
        raise ConfigError(f"config file not found or not a regular file: {canonical}")
    size = canonical.stat().st_size
    if size > MAX_CONFIG_BYTES:
        raise ConfigError(f"{canonical}: {size} bytes exceeds the {MAX_CONFIG_BYTES}-byte limit")
    try:
        data = load_mapping(canonical.read_bytes(), source=str(canonical))
    except StrictYAMLError as exc:
        raise ConfigError(str(exc)) from exc
    return ConfigLayer(source=source, origin=str(canonical), data=data)


def resolve_config(layers: Sequence[ConfigLayer]) -> ResolvedConfig:
    """Merge layers in precedence order and validate the result strictly."""
    ordered = sorted(layers, key=lambda layer: _LAYER_ORDER.index(layer.source))
    for earlier, later in itertools.pairwise(ordered):
        if earlier.source == later.source:
            raise ConfigError(f"more than one {earlier.source.value} config layer supplied")
    merged: dict[str, Any] = {}
    provenance: dict[str, str] = {}
    for layer in ordered:
        if not isinstance(layer.data, Mapping):
            raise ConfigError(f"{layer.origin}: config must be a mapping")
        _deep_merge(merged, layer.data, f"{layer.source.value}:{layer.origin}", provenance, ())
    try:
        config = AgentFenceConfig.model_validate(merged)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(exc, provenance)) from exc
    return ResolvedConfig(config=config, provenance=dict(sorted(provenance.items())))


def _deep_merge(
    target: dict[str, Any],
    incoming: Mapping[str, Any],
    origin: str,
    provenance: dict[str, str],
    prefix: tuple[str, ...],
) -> None:
    for key, value in incoming.items():
        path = (*prefix, str(key))
        if isinstance(value, Mapping) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value, origin, provenance, path)
            continue
        if isinstance(value, Mapping):
            target[key] = {}
            _deep_merge(target[key], value, origin, provenance, path)
            continue
        # YAML sequences arrive as lists; models use immutable tuples.
        target[key] = tuple(value) if isinstance(value, list | tuple) else value
        provenance[".".join(path)] = origin


def _format_validation_error(exc: ValidationError, provenance: Mapping[str, str]) -> str:
    lines = ["invalid configuration:"]
    for error in exc.errors():
        parts = [str(part) for part in error["loc"]]
        dotted = ".".join(parts)
        where = next(
            (
                provenance[".".join(parts[:n])]
                for n in range(len(parts), 0, -1)
                if ".".join(parts[:n]) in provenance
            ),
            None,
        )
        source = f" (from {where})" if where else ""
        message = error["msg"].removeprefix("Value error, ")
        if error["type"] == "extra_forbidden":
            message = "unknown key; check spelling against docs/06_CLI_CONFIGURATION_API.md"
        elif error["type"] == "tuple_type":
            message = "expected a list"
        lines.append(f"  {dotted or '<root>'}: {message}{source}")
    return "\n".join(lines)
