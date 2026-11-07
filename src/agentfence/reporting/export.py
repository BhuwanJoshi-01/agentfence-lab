"""Verified report export and reproduction metadata (AF-0506; docs/07 sections 6 and 12).

Before a stored report is shown or exported it must pass three checks:

1. ``report.json`` still hashes to the SHA-256 recorded when the run was saved;
2. the run's stored events pass :meth:`RunStore.verify_run` (payload hashes, hash chain,
   event validity);
3. every evidence ID the report cites exists among the stored events of that attempt.

Any failure refuses the export with the specific problems. A hash chain detects
corruption and casual tampering; it does not authenticate origin against someone who
controls the host (ADR-013).
"""

from __future__ import annotations

import hashlib
import shlex
from dataclasses import dataclass
from pathlib import Path

from agentfence.reporting.json_report import SuiteReport
from agentfence.runtime.collectors import BrokerError, read_workspace_file
from agentfence.storage.atomic import atomic_write_text
from agentfence.storage.db import RunStore
from agentfence.storage.paths import AppPaths


class ExportError(RuntimeError):
    """The run cannot be exported. ``not_found`` distinguishes a usage error."""

    def __init__(self, message: str, *, not_found: bool = False) -> None:
        super().__init__(message)
        self.not_found = not_found


@dataclass(frozen=True, slots=True)
class VerifiedReport:
    report: SuiteReport
    text: str
    sha256: str


def load_verified_report(paths: AppPaths, store: RunStore, run_id: str) -> VerifiedReport:
    run_dir = paths.run_dir(run_id)  # raises for non-canonical IDs
    summary = store.get_run(run_id)
    if summary is None:
        raise ExportError(f"no run with ID {run_id}", not_found=True)
    try:
        raw = read_workspace_file(run_dir, "report.json")
    except BrokerError as exc:
        raise ExportError(f"report for run {run_id} is missing or unreadable: {exc}") from exc
    digest = hashlib.sha256(raw).hexdigest()
    if summary.report_sha256 != digest:
        raise ExportError(
            f"report.json for run {run_id} does not match the hash recorded when it was saved; "
            "refusing to export a modified report"
        )
    problems = store.verify_run(run_id)
    if problems:
        raise ExportError("stored evidence failed verification: " + "; ".join(problems[:5]))
    text = raw.decode("utf-8")
    report = SuiteReport.model_validate_json(text)
    if report.run_id != run_id:
        raise ExportError("report.json belongs to a different run")
    for result in report.results:
        stored = {e.event_id for e in store.events_for(result.attempt_id)}
        cited = {
            *result.evidence_ids,
            *result.prohibited_attempts,
            *result.completed_effects,
            *result.utility_evidence,
        }
        missing = sorted(cited - stored)
        if missing:
            raise ExportError(
                f"{result.attempt_id} cites evidence that is not stored: {', '.join(missing[:3])}"
            )
    return VerifiedReport(report=report, text=text, sha256=digest)


def reproduce_command(report: SuiteReport) -> str:
    """The exact ``agentfence test`` invocation that reproduces this mock run."""
    ids = {r.scenario_id for r in report.results}
    # Selecting a scenario adds its benign pair, so list a control only if it ran alone.
    cases = sorted(sid for sid in ids if not (sid.endswith("-B") and sid[:-2] in ids))
    args = [
        "agentfence",
        "test",
        "--agent",
        report.agent.name,
        "--agent-profile",
        report.agent.profile,
        "--profile",
        report.scenario_profile,
        "--cases",
        ",".join(cases),
        "--repeat",
        str(report.repeat),
        "--seed",
        str(report.seed),
    ]
    return shlex.join(args)


def reproduction_notes(report: SuiteReport) -> list[str]:
    scenarios = sorted({(r.scenario_id, r.scenario_version) for r in report.results})
    digests = sorted({(r.scenario_id, r.fixture_digest[:16]) for r in report.results})
    return [
        f"Reproduce with: {reproduce_command(report)}",
        f"Tool version: {report.tool_version} | Evaluator: "
        + ", ".join(sorted({r.evaluator_version for r in report.results})),
        "Scenario versions: " + ", ".join(f"{sid} {ver}" for sid, ver in scenarios),
        "Fixture digests: " + ", ".join(f"{sid} {d}" for sid, d in digests),
    ]


def write_export(path: Path, text: str) -> Path:
    """Write an explicitly requested export; never overwrite an existing file."""
    if path.exists() or path.is_symlink():
        raise ExportError(f"{path} already exists; choose a new file name")
    return atomic_write_text(path, text)
