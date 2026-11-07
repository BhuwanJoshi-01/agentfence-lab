# SPDX-License-Identifier: Apache-2.0
"""Capability models for adapters and the host (AF-0103).

``AdapterCapabilities`` follows docs/05 section 2. Every boolean means *verified*, not
*claimed*: an adapter that cannot prove it routes model traffic through a proxy reports
``supports_model_proxy=False``.

``HostCapabilities`` is what ``agentfence doctor`` reports: a list of named checks, each
``PASS``, ``FAIL`` or ``UNKNOWN``, plus the derived isolation state.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated

from pydantic import Field

from agentfence.core.types import Slug, StrictModel
from agentfence.core.vocabulary import CheckStatus, IsolationState


class AdapterCapabilities(StrictModel):
    name: Slug
    binary_version: Annotated[str, Field(min_length=1, max_length=64)]
    noninteractive: bool
    structured_events: bool
    supports_repo_read: bool
    supports_file_write: bool
    supports_tool_execution: bool
    supports_model_proxy: bool
    supports_isolated_config_root: bool
    requires_network: bool
    metadata: Mapping[str, Annotated[str, Field(max_length=256)]] = Field(default_factory=dict)


class CapabilityCheck(StrictModel):
    """One host/preflight check with bounded, already-sanitized evidence."""

    id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.]{1,63}$")]
    status: Annotated[CheckStatus, Field(strict=False)]
    required: bool
    summary: Annotated[str, Field(min_length=1, max_length=200)]
    detail: Annotated[str, Field(max_length=2000)] = ""
    remedy: Annotated[str, Field(max_length=500)] = ""


class HostCapabilities(StrictModel):
    os: Annotated[str, Field(max_length=32)]
    arch: Annotated[str, Field(max_length=32)]
    python: Annotated[str, Field(max_length=32)]
    checks: tuple[CapabilityCheck, ...]

    @property
    def isolation_state(self) -> IsolationState:
        """``OFFLINE_SAFE`` only if *every* required check passed. UNKNOWN never upgrades."""
        required = [c for c in self.checks if c.required]
        if required and all(c.status is CheckStatus.PASS for c in required):
            return IsolationState.OFFLINE_SAFE
        return IsolationState.UNSUPPORTED

    @property
    def blocking_checks(self) -> tuple[CapabilityCheck, ...]:
        return tuple(c for c in self.checks if c.required and c.status is not CheckStatus.PASS)
