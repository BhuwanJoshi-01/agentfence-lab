# SPDX-License-Identifier: Apache-2.0
"""Strict Tier 0 sandbox policy (AF-0203; docs/03 sections 3-4, ADR-011).

:class:`IsolationPolicy` is the complete description of how one attempt's container may
run. Every mandatory control from docs/03 section 4 is a *locked* field: it exists so the
policy states it explicitly, and no value other than the safe one validates. There is no
field for a host bind mount, a socket, a device, extra capabilities, a host namespace or
privileged mode, so a policy cannot even express them.

What the policy fixes:

* image pinned by digest (``name@sha256:<64 hex>``), never pulled at run time;
* non-root user and group (UID/GID 0 rejected);
* read-only root filesystem, all capabilities dropped, ``no-new-privileges``, the
  engine's default seccomp profile (it can only be weakened by an option the policy has
  no field for);
* ``network = none`` (Tier 0);
* the only writable places: an engine-managed per-attempt volume at ``/workspace`` (the
  fixture is copied in, never bind-mounted from the host) and a size-limited ``/tmp``;
* memory (swap disabled), PID and CPU limits from :class:`ResourceLimits`;
* labels that tie every container to its run and attempt (stale cleanup, AF-0207);
* an explicit environment: names must look like ordinary variables and anything shaped
  like a credential, a socket or an engine endpoint is refused (docs/03 section 11).

Building the engine command line and auditing it is :mod:`agentfence.runtime.containers`.
That a running container actually has these properties is verified separately, with a
rootless engine (AF-0205, AF-0206); this module makes no claim about enforcement.
"""

from __future__ import annotations

import re
from typing import Annotated, Final, Literal

from pydantic import Field, field_validator

from agentfence.core.config import ResourceLimits
from agentfence.core.types import LockedFalse, LockedTrue, StrictModel

SANDBOX_IMAGE: Final = (
    "docker.io/library/python"
    "@sha256:1e1f6334529d1fb26a2ef619d7cffb30a7c7d7cc1cc7338ebee283a58d1aeaca"
)
"""Official ``python:3.13-alpine`` (Python 3.13.18), pinned 2026-11-07 by its OCI image-index
digest. It is never pulled at run time (``--pull=never``); ``doctor`` names the one-time
``podman pull`` command when it is missing. AgentFence code reaches the container as
arguments, so no locally built image (whose digest would differ per machine) is needed."""
WORKSPACE_PATH: Final = "/workspace"
TMP_PATH: Final = "/tmp"  # noqa: S108 - the container's own /tmp, a size-limited tmpfs
LABEL_PREFIX: Final = "io.agentfence"
RUN_LABEL: Final = f"{LABEL_PREFIX}.run"
ATTEMPT_LABEL: Final = f"{LABEL_PREFIX}.attempt"
VOLUME_PREFIX: Final = "agentfence-"

_IMAGE = re.compile(r"^[a-z0-9][a-z0-9._/:-]{0,254}@sha256:[0-9a-f]{64}$")
_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
_LABEL_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_VOLUME = re.compile(rf"^{VOLUME_PREFIX}[a-z0-9][a-z0-9-]{{0,94}}$")
_FORBIDDEN_ENV: Final = re.compile(
    r"(TOKEN|SECRET|PASSW|API_?KEY|ACCESS_?KEY|PRIVATE_?KEY|CREDENTIAL|SESSION|COOKIE|AUTH"
    r"|^AWS_|^AZURE_|^GOOGLE_|^GCLOUD|^OPENAI_|^ANTHROPIC_|^GITHUB_|^GH_|^HF_"
    r"|^SSH_|^GPG_|^DOCKER_|^CONTAINER_HOST$|^PODMAN_|^KUBECONFIG$|^DBUS_|^DISPLAY$"
    r"|^WAYLAND_|^XDG_RUNTIME_DIR$|PROXY$|^LD_|^PYTHONPATH$|^PYTHONSTARTUP$)"
)
MAX_ENV: Final = 32


def is_forbidden_env_name(name: str) -> bool:
    """Credential, socket, engine, display, proxy and loader variables never enter."""
    return bool(_FORBIDDEN_ENV.search(name.upper()))


class IsolationPolicy(StrictModel):
    """Everything a Tier 0 container launch may vary, and the controls it may not."""

    run_id: Annotated[str, Field(pattern=_LABEL_VALUE.pattern)]
    attempt_id: Annotated[str, Field(pattern=_LABEL_VALUE.pattern)]
    image: Annotated[str, Field(pattern=_IMAGE.pattern)]
    workspace_volume: Annotated[str, Field(pattern=_VOLUME.pattern)]
    uid: Annotated[int, Field(ge=1, le=2**31 - 1)] = 65532
    gid: Annotated[int, Field(ge=1, le=2**31 - 1)] = 65532
    limits: ResourceLimits = ResourceLimits()
    tmp_size_mb: Annotated[int, Field(ge=1, le=1024)] = 64
    env: dict[str, str] = Field(default_factory=dict)

    # Locked controls (docs/03 section 4). Present so the policy is explicit and audited.
    read_only_root: LockedTrue = True
    drop_all_capabilities: LockedTrue = True
    no_new_privileges: LockedTrue = True
    network: Literal["none"] = "none"
    pull: Literal["never"] = "never"
    privileged: LockedFalse = False
    host_mounts: LockedFalse = False

    @field_validator("env")
    @classmethod
    def _env_is_explicit_and_harmless(cls, env: dict[str, str]) -> dict[str, str]:
        if len(env) > MAX_ENV:
            raise ValueError(f"at most {MAX_ENV} environment variables")
        for name, value in env.items():
            if not _ENV_NAME.match(name):
                raise ValueError(f"invalid environment variable name {name!r}")
            if is_forbidden_env_name(name):
                raise ValueError(f"environment variable {name!r} is not allowed in a sandbox")
            if any(ch in value for ch in "\x00\n\r") or len(value) > 1024:
                raise ValueError(f"environment variable {name!r} has an unsafe value")
        return env

    @property
    def labels(self) -> dict[str, str]:
        return {RUN_LABEL: self.run_id, ATTEMPT_LABEL: self.attempt_id}
