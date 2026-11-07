# SPDX-License-Identifier: Apache-2.0
"""Isolation attestation: what a sandbox self-test observed (docs/03 section 5, AF-0205).

One attestation records the engine, the pinned image, the policy under test and one
PASS/FAIL/UNKNOWN check per isolation property, each with the expected and the observed
value. Its ID is the SHA-256 of its canonical JSON without the ID, so any edit to a stored
attestation is detectable. ``passed`` is true only when every check is PASS: UNKNOWN never
counts as a pass.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, Field

from agentfence.core.types import StrictModel
from agentfence.core.vocabulary import CheckStatus

_lax = Field(strict=False)


class AttestationCheck(StrictModel):
    id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.]{0,63}$")]
    status: Annotated[CheckStatus, _lax]
    expected: Annotated[str, Field(max_length=300)]
    observed: Annotated[str, Field(max_length=600)]


class IsolationAttestation(StrictModel):
    schema_version: Literal[1] = 1
    attestation_id: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    created_at: Annotated[AwareDatetime, _lax]
    tool_version: str
    engine: Literal["podman"]
    engine_version: str
    image: str
    policy: dict[str, int | float | str]
    checks: Annotated[tuple[AttestationCheck, ...], _lax]

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(c.status is CheckStatus.PASS for c in self.checks)

    @staticmethod
    def compute_id(fields: dict[str, Any]) -> str:
        body = {k: v for k, v in fields.items() if k != "attestation_id"}
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()

    @classmethod
    def create(cls, **fields: Any) -> IsolationAttestation:
        draft = cls.model_validate({**fields, "attestation_id": "0" * 64})
        dumped = draft.model_dump(mode="json")
        return cls.model_validate({**dumped, "attestation_id": cls.compute_id(dumped)})

    def verify_id(self) -> bool:
        return self.attestation_id == self.compute_id(self.model_dump(mode="json"))
