"""Schema-versioned machine report (AF-0501; docs/06 section 8, docs/07 section 8).

The report is built only from data that is already safe to share: verdicts, metrics and
alias-only events (no file contents, no synthetic secret values, no host paths). It
carries every event an attempt produced so a reviewer can re-check each verdict from
the report alone (FR-009, NFR-008).

The JSON Schema for this model is checked in at
``src/agentfence/schemas/report.v1.schema.json``; a test keeps it in sync.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Final, Literal

from pydantic import AwareDatetime, Field

from agentfence.core.events import ObservedEvent
from agentfence.core.metrics import Ratio, compute_metrics
from agentfence.core.types import StrictModel
from agentfence.core.vocabulary import SCHEMA_VERSIONS
from agentfence.runtime.attempt import AttemptResult
from agentfence.runtime.suite import SuiteRunResult

REPORT_SCHEMA_VERSION: Final = SCHEMA_VERSIONS["report"]
DISCLAIMER: Final = (
    "Scenario coverage is limited and does not certify agent security. Mock results test "
    "the AgentFence pipeline, not any real coding agent."
)
ISOLATION_CLAIMS: Final = {
    "mock-inprocess": (
        "No sandbox. The deterministic mock ran in-process, executed no workspace code and "
        "opened no network connections. No containment claim is made."
    ),
}

_lax = Field(strict=False)


class RatioReport(StrictModel):
    numerator: Annotated[int, Field(ge=0)]
    denominator: Annotated[int, Field(ge=0)]
    value: float | None
    wilson_95: tuple[float, float] | None

    @classmethod
    def of(cls, ratio: Ratio) -> RatioReport:
        return cls(
            numerator=ratio.numerator,
            denominator=ratio.denominator,
            value=ratio.value,
            wilson_95=ratio.wilson_95,
        )


class SummaryReport(StrictModel):
    attempts: int
    adversarial: int
    benign: int
    boundary: dict[str, int]
    exposure_coverage: RatioReport
    violation_rate: RatioReport
    utility_rate: RatioReport
    benign_incomplete_rate: RatioReport
    inconclusive_rate: RatioReport
    no_exposure_rate: RatioReport


class EventReport(StrictModel):
    event_id: str
    sequence: int
    timestamp: Annotated[AwareDatetime, _lax]
    kind: str
    source_component: str
    source_trust: str
    target: str
    decision: str
    details: dict[str, str | int | bool | None]
    content_hash: str | None

    @classmethod
    def of(cls, event: ObservedEvent) -> EventReport:
        return cls(
            event_id=event.event_id,
            sequence=event.sequence,
            timestamp=event.timestamp,
            kind=event.kind.value,
            source_component=event.source.component,
            source_trust=event.source.trust.value,
            target=event.target.alias,
            decision=event.decision.value,
            details=dict(event.details),
            content_hash=event.content_hash,
        )


class AttemptReport(StrictModel):
    attempt_id: str
    scenario_id: str
    scenario_version: str
    category: str
    variant: Literal["adversarial", "benign"]
    repetition: int
    boundary: str
    utility: str
    infrastructure: str
    finding_code: str
    confidence: str
    evaluator_version: str
    evidence_ids: tuple[str, ...]
    prohibited_attempts: tuple[str, ...]
    completed_effects: tuple[str, ...]
    utility_evidence: tuple[str, ...]
    coverage_gaps: tuple[str, ...]
    limitations: tuple[str, ...]
    fixture_digest: str
    final_status: str
    impact_scope: Literal["synthetic laboratory fixture only"] = "synthetic laboratory fixture only"
    events: tuple[EventReport, ...]

    @classmethod
    def of(cls, attempt: AttemptResult) -> AttemptReport:
        v = attempt.verdict
        return cls(
            attempt_id=attempt.attempt_id,
            scenario_id=v.scenario_id,
            scenario_version=v.scenario_version,
            category=attempt.scenario.category.value,
            variant=v.variant.value,
            repetition=attempt.repetition,
            boundary=v.boundary.value,
            utility=v.utility.value,
            infrastructure=v.infrastructure.value,
            finding_code=v.reason_code,
            confidence=v.confidence.value,
            evaluator_version=v.evaluator_version,
            evidence_ids=v.evidence_ids,
            prohibited_attempts=v.prohibited_attempts,
            completed_effects=v.completed_effects,
            utility_evidence=v.utility_evidence,
            coverage_gaps=tuple(g.value for g in v.coverage_gaps),
            limitations=v.limitations,
            fixture_digest=attempt.fixture_digest,
            final_status=attempt.lifecycle[-1].target.value if attempt.lifecycle else "CREATED",
            events=tuple(EventReport.of(e) for e in attempt.events),
        )


class AgentReport(StrictModel):
    name: str
    version: str
    profile: str
    model: None = None


class IsolationReport(StrictModel):
    tier: str
    claim: str
    attestation: None = None


class RedactionReport(StrictModel):
    enabled: Literal[True] = True
    raw_transcript_included: Literal[False] = False
    file_contents_included: Literal[False] = False


class SuiteReport(StrictModel):
    """Root of ``report.json``."""

    schema_version: Literal[1] = 1
    run_id: str
    tool_version: str
    started_at: Annotated[AwareDatetime, _lax]
    ended_at: Annotated[AwareDatetime, _lax]
    scenario_profile: str
    seed: int
    repeat: int
    agent: AgentReport
    isolation: IsolationReport
    summary: SummaryReport
    results: tuple[AttemptReport, ...]
    redaction: RedactionReport = RedactionReport()
    disclaimer: str = DISCLAIMER


def build_report(suite: SuiteRunResult) -> SuiteReport:
    metrics = compute_metrics([a.verdict for a in suite.attempts])
    return SuiteReport(
        run_id=suite.run_id,
        tool_version=suite.tool_version,
        started_at=suite.started_at,
        ended_at=suite.ended_at,
        scenario_profile=suite.scenario_profile,
        seed=suite.seed,
        repeat=suite.repeat,
        agent=AgentReport(
            name=suite.agent, version=suite.tool_version, profile=suite.agent_profile.value
        ),
        isolation=IsolationReport(
            tier=suite.isolation_tier,
            claim=ISOLATION_CLAIMS.get(suite.isolation_tier, "No containment claim is made."),
        ),
        summary=SummaryReport(
            attempts=metrics.attempts,
            adversarial=metrics.adversarial,
            benign=metrics.benign,
            boundary=metrics.boundary_counts,
            exposure_coverage=RatioReport.of(metrics.exposure_coverage),
            violation_rate=RatioReport.of(metrics.violation_rate),
            utility_rate=RatioReport.of(metrics.utility_rate),
            benign_incomplete_rate=RatioReport.of(metrics.benign_incomplete_rate),
            inconclusive_rate=RatioReport.of(metrics.inconclusive_rate),
            no_exposure_rate=RatioReport.of(metrics.no_exposure_rate),
        ),
        results=tuple(AttemptReport.of(a) for a in suite.attempts),
    )


def report_json(report: SuiteReport) -> str:
    """Deterministic JSON text: sorted keys, UTF-8, trailing newline."""
    data: dict[str, Any] = report.model_dump(mode="json")
    return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def report_schema() -> dict[str, Any]:
    schema = SuiteReport.model_json_schema(mode="serialization")
    schema["$id"] = "https://github.com/BhuwanJoshi-01/agentfence-lab/schemas/report.v1.schema.json"
    schema["title"] = "AgentFence Lab suite report v1"
    return schema
