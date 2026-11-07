# SPDX-License-Identifier: Apache-2.0
"""Strict declarative scenario manifest schema (AF-0103, FR-003, ADR-006).

A manifest *describes* a scenario; it never contains executable content. There is no
field for shell commands, hooks, URLs or absolute paths, and unknown keys are rejected,
so ``setup: "curl ... | sh"`` fails validation instead of being ignored.

Cross-file checks (benign pair exists, fixture generator is registered, globs resolve
inside the materialized workspace) belong to the loader (AF-0301), not this schema.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, Field, model_validator

from agentfence.core.types import (
    DisplayText,
    RelativePattern,
    ScenarioId,
    SemVer,
    Slug,
    StrictModel,
)
from agentfence.core.vocabulary import Variant


class ScenarioCategory(StrEnum):
    PROMPT_INJECTION = "prompt-injection"
    SECRET_SCOPE = "secret-scope"  # noqa: S105 - category label
    NETWORK_EGRESS = "network-egress"
    TOOL_OUTPUT_SPOOFING = "tool-output-spoofing"
    ARTIFACT_INTEGRITY = "artifact-integrity"


class AdapterCapabilityName(StrEnum):
    REPO_READ = "repo_read"
    FILE_WRITE = "file_write"
    TOOL_EXECUTION = "tool_execution"


class ObservationChannel(StrEnum):
    """Telemetry a verdict may depend on. Missing required channel => INCONCLUSIVE."""

    WORKSPACE_DIFF = "workspace_diff"
    TRACE_ENTRY = "trace_entry"
    ACCESS_BROKER = "access_broker"
    NETWORK_SINK = "network_sink"
    TOOL_WRAPPER = "tool_wrapper"
    UTILITY_RUNNER = "utility_runner"


class UtilityAssertionKind(StrEnum):
    TEST_PASSES = "test_passes"
    FILE_MATCHES_REFERENCE = "file_matches_reference"
    CSV_MATCHES_REFERENCE = "csv_matches_reference"


class SurfaceKind(StrEnum):
    REPOSITORY_DOCUMENT = "repository_document"
    TASK_NOTE = "task_note"
    PROJECT_TEXT = "project_text"
    TOOL_OUTPUT = "tool_output"
    DATA_FILE = "data_file"


class ExposureProofKind(StrEnum):
    BROKER_READ_OBSERVED = "broker_read_observed"
    READ_OBSERVED_OR_CONTENT_QUOTED_IN_TRACE = "read_observed_or_content_quoted_in_trace"
    TOOL_OUTPUT_DELIVERED = "tool_output_delivered"
    MOCK_INJECTION_HANDSHAKE = "mock_injection_handshake"


class BoundaryKind(StrEnum):
    WRITE_SCOPE = "write_scope"
    READ_SCOPE = "read_scope"
    NETWORK_SCOPE = "network_scope"
    ARTIFACT_INTEGRITY = "artifact_integrity"


class FailureRuleKind(StrEnum):
    FORBIDDEN_PATH_MODIFIED = "forbidden_path_modified"
    PROTECTED_READ_GRANTED = "protected_read_granted"
    PROTECTED_READ_ATTEMPTED = "protected_read_attempted"
    UNAUTHORIZED_CONNECT_ATTEMPTED = "unauthorized_connect_attempted"
    OUTPUT_MARKER_PRESENT = "output_marker_present"


class PassRuleKind(StrEnum):
    TRUSTED_EXPOSURE_AND_NO_FORBIDDEN_SIDE_EFFECT = "trusted_exposure_and_no_forbidden_side_effect"


_lax = Field(strict=False)
Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")]


def _unique[T](values: tuple[T, ...]) -> tuple[T, ...]:
    if len(set(values)) != len(values):
        raise ValueError("entries must be unique")
    return values


class Requirements(StrictModel):
    adapter_capabilities: Annotated[
        tuple[Annotated[AdapterCapabilityName, _lax], ...], _lax, AfterValidator(_unique)
    ]
    observation_channels: Annotated[
        tuple[Annotated[ObservationChannel, _lax], ...],
        _lax,
        Field(min_length=1),
        AfterValidator(_unique),
    ]


class UtilityAssertion(StrictModel):
    kind: Annotated[UtilityAssertionKind, _lax]
    fixture_id: Identifier


class LegitimateTask(StrictModel):
    description: Annotated[DisplayText, Field(min_length=10, max_length=500)]
    assertions: Annotated[tuple[UtilityAssertion, ...], _lax, Field(min_length=1)]


class ExposureProof(StrictModel):
    kind: Annotated[ExposureProofKind, _lax]


class AttackSurface(StrictModel):
    kind: Annotated[SurfaceKind, _lax]
    path: RelativePattern
    exposure_proof: ExposureProof


class Boundary(StrictModel):
    kind: Annotated[BoundaryKind, _lax]
    allow: Annotated[tuple[RelativePattern, ...], _lax] = ()
    deny: Annotated[tuple[RelativePattern, ...], _lax] = ()

    @model_validator(mode="after")
    def _no_overlap(self) -> Self:
        overlap = set(self.allow) & set(self.deny)
        if overlap:
            raise ValueError(f"patterns cannot be both allowed and denied: {sorted(overlap)}")
        return self


class FixtureSpec(StrictModel):
    generator: Identifier
    variant: Annotated[Variant, _lax]


class FailureRule(StrictModel):
    kind: Annotated[FailureRuleKind, _lax]
    path: RelativePattern | None = None


class PassRule(StrictModel):
    kind: Annotated[PassRuleKind, _lax]


class VerdictRules(StrictModel):
    failure_if: Annotated[tuple[FailureRule, ...], _lax] = ()
    pass_if: Annotated[tuple[PassRule, ...], _lax, Field(min_length=1)]


class ScenarioLimits(StrictModel):
    wall_seconds: Annotated[int, Field(ge=1, le=600)] = 90
    memory_mb: Annotated[int, Field(ge=64, le=4096)] = 1024
    max_events: Annotated[int, Field(ge=1, le=100_000)] = 5000


class ScenarioManifest(StrictModel):
    schema_version: Literal[1]
    id: ScenarioId
    version: SemVer
    title: Annotated[DisplayText, Field(min_length=5, max_length=120)]
    category: Annotated[ScenarioCategory, _lax]
    risk_tags: Annotated[
        tuple[Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9-]{1,31}$")], ...],
        _lax,
        AfterValidator(_unique),
    ] = ()
    profile: Slug
    requires: Requirements
    legitimate_task: LegitimateTask
    attack_surface: AttackSurface | None = None
    boundary: Boundary
    fixture: FixtureSpec
    verdict: VerdictRules
    benign_pair: ScenarioId | None = None
    limits: ScenarioLimits = ScenarioLimits()

    @property
    def variant(self) -> Variant:
        return self.fixture.variant

    @model_validator(mode="after")
    def _pairing_and_variant_rules(self) -> Self:
        if self.variant is Variant.ADVERSARIAL:
            if self.id.endswith("-B"):
                raise ValueError("adversarial scenario IDs must not end with '-B'")
            if self.attack_surface is None:
                raise ValueError("adversarial scenarios must declare attack_surface")
            if not self.verdict.failure_if:
                raise ValueError("adversarial scenarios must declare at least one failure_if rule")
            if self.benign_pair != f"{self.id}-B":
                raise ValueError(
                    f"adversarial scenario must pair with its benign control '{self.id}-B'"
                )
        else:
            if not self.id.endswith("-B"):
                raise ValueError("benign control IDs must end with '-B'")
            if self.benign_pair is not None:
                raise ValueError("benign controls must not declare benign_pair")
        return self
