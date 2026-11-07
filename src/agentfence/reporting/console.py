# SPDX-License-Identifier: Apache-2.0
"""Human-readable run summary (AF-0502; docs/06 section 4, NFR-007).

Rendered from a :class:`~agentfence.reporting.json_report.SuiteReport`, so a live run and
``agentfence report <run-id> --format text`` print the same thing. Plain text only: no
color, no terminal control sequences, readable in CI logs and screen readers.

Every row shows the boundary outcome *and* utility separately (ADR-002) with evidence
counts; limitations are printed under the row they qualify. Benign controls collapse into
one line unless something needs attention (a FAIL or INCONCLUSIVE).
"""

from __future__ import annotations

from agentfence.core.redaction import scrub_text
from agentfence.reporting.json_report import AttemptReport, RatioReport, SuiteReport


def _ratio(r: RatioReport) -> str:
    if r.denominator == 0:
        return "n/a"
    text = f"{r.numerator}/{r.denominator}"
    if r.wilson_95 is not None:
        low, high = r.wilson_95
        text += f" (95% CI {low:.2f}-{high:.2f})"
    return text


def _row(result: AttemptReport, *, show_repetition: bool) -> list[str]:
    label = result.scenario_id + (f" #{result.repetition}" if show_repetition else "")
    if result.variant == "benign":
        label += " (benign)"
    line = (
        f"{label:<26} {result.boundary:<14} utility={result.utility:<11} "
        f"evidence={len(result.evidence_ids):<3} {result.finding_code}"
    )
    if result.infrastructure != "OK":
        line += f"  infra={result.infrastructure}"
    lines = [line]
    lines.extend(f"{'':<26} limitation: {scrub_text(note)}" for note in result.limitations)
    return lines


def render_summary(report: SuiteReport, *, report_location: str | None = None) -> str:
    s = report.summary
    show_rep = report.repeat > 1
    lines = [
        f"AgentFence Lab {report.tool_version}",
        (
            f"Agent: {report.agent.name}-{report.agent.profile} | "
            f"Scenario profile: {report.scenario_profile} | Attempts: {s.attempts} | "
            f"Seed: {report.seed}" + (f" | Repeat: {report.repeat}" if show_rep else "")
        ),
        f"Isolation: {report.isolation.tier}. {report.isolation.claim}",
        "",
    ]
    adversarial = [r for r in report.results if r.variant == "adversarial"]
    benign = [r for r in report.results if r.variant == "benign"]
    for result in adversarial:
        lines.extend(_row(result, show_repetition=show_rep))
    flagged = [r for r in benign if r.boundary in {"FAIL", "INCONCLUSIVE"}]
    for result in flagged:
        lines.extend(_row(result, show_repetition=show_rep))
    if benign:
        complete = sum(r.utility == "COMPLETE" for r in benign)
        lines.append(f"{'Benign controls':<26} {complete}/{len(benign)} COMPLETE")
    counts = s.boundary
    lines += [
        "",
        (
            f"Boundary: {counts['PASS']} pass, {counts['FAIL']} fail, "
            f"{counts['INCONCLUSIVE']} inconclusive, {counts['NOT_TRIGGERED']} not-triggered"
        ),
        (
            f"Exposure coverage: {_ratio(s.exposure_coverage)} adversarial | "
            f"Violations among exposed: {_ratio(s.violation_rate)}"
        ),
        f"Run ID: {report.run_id}" + (f" | Report: {report_location}" if report_location else ""),
        f"Disclaimer: {report.disclaimer}",
    ]
    return "\n".join(lines) + "\n"
