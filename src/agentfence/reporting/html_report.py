"""Self-contained, offline HTML report (AF-0503; docs/03 section 8, docs/07 section 10).

* No scripts, no remote fonts, images, links or stylesheets: the page works offline and
  sends nothing anywhere.
* A Content-Security-Policy ``<meta>`` forbids every source except the single inline
  stylesheet, which is pinned by its SHA-256 hash.
* Every dynamic string is HTML-escaped. The machine-readable report is embedded as an
  inert ``application/json`` block with ``<``, ``>`` and ``&`` escaped as ``\\u003c`` etc.,
  so no report content can close the block or form markup.
* Accessible by construction: ``lang``, captions, ``scope`` on header cells, and every
  status is written as text (color is only a secondary cue).
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Iterable, Sequence
from html import escape
from typing import Final

from agentfence.reporting.json_report import AttemptReport, RatioReport, SuiteReport

STYLE: Final = """
:root{color-scheme:light dark;--fg:#1b1f24;--bg:#fff;--muted:#57606a;--line:#d0d7de;
--fail:#a40e26;--pass:#116329;--warn:#7d4e00}
@media (prefers-color-scheme:dark){:root{--fg:#e6edf3;--bg:#0d1117;--muted:#9da7b3;
--line:#30363d;--fail:#ff8182;--pass:#56d364;--warn:#e3b341}}
body{font:15px/1.5 system-ui,sans-serif;color:var(--fg);background:var(--bg);
margin:0 auto;max-width:72rem;padding:1rem}
h1{font-size:1.4rem}h2{font-size:1.15rem;margin-top:2rem}h3{font-size:1rem}
table{border-collapse:collapse;width:100%;margin:.5rem 0 1rem}
caption{text-align:left;font-weight:600;padding:.25rem 0}
th,td{border:1px solid var(--line);padding:.3rem .5rem;text-align:left;vertical-align:top}
code{font-family:ui-monospace,monospace;font-size:.85em;word-break:break-all}
.muted{color:var(--muted)}.FAIL{color:var(--fail);font-weight:700}
.PASS{color:var(--pass);font-weight:700}.INCONCLUSIVE,.TIMEOUT,.ERROR{color:var(--warn);
font-weight:700}.notice{border-left:4px solid var(--warn);padding:.25rem .75rem}
""".strip()
STYLE_HASH: Final = base64.b64encode(hashlib.sha256(STYLE.encode()).digest()).decode()
CSP: Final = (
    "default-src 'none'; "
    f"style-src 'sha256-{STYLE_HASH}'; "
    "img-src 'none'; script-src 'none'; connect-src 'none'; font-src 'none'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


def _e(value: object) -> str:
    return escape(str(value), quote=True)


def _ratio(r: RatioReport) -> str:
    if r.denominator == 0:
        return "n/a"
    text = f"{r.numerator}/{r.denominator}"
    if r.wilson_95 is not None:
        text += f" (95% CI {r.wilson_95[0]:.2f} to {r.wilson_95[1]:.2f})"
    return text


def _table(caption: str, headers: Sequence[str], rows: Iterable[Sequence[str]]) -> str:
    head = "".join(f'<th scope="col">{_e(h)}</th>' for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return (
        f"<table><caption>{_e(caption)}</caption><thead><tr>{head}</tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )


def _status(value: str) -> str:
    return f'<span class="{_e(value)}">{_e(value)}</span>'


def _attempt(result: AttemptReport) -> str:
    title = f"{result.scenario_id} {result.scenario_version} ({result.variant}"
    title += f", repetition {result.repetition})"
    facts = [
        ("Boundary", _status(result.boundary)),
        ("Utility", _status(result.utility)),
        ("Infrastructure", _status(result.infrastructure)),
        ("Finding code", f"<code>{_e(result.finding_code)}</code>"),
        ("Confidence", _e(result.confidence)),
        ("Evaluator", _e(result.evaluator_version)),
        ("Final status", _e(result.final_status)),
        ("Fixture digest", f"<code>{_e(result.fixture_digest)}</code>"),
        ("Impact scope", _e(result.impact_scope)),
    ]
    rows = "".join(f'<tr><th scope="row">{_e(k)}</th><td>{v}</td></tr>' for k, v in facts)
    parts = [
        f'<section aria-labelledby="{_e(result.attempt_id)}">',
        f'<h3 id="{_e(result.attempt_id)}">{_e(title)}</h3>',
        f"<table><caption>Verdict</caption><tbody>{rows}</tbody></table>",
    ]
    if result.limitations:
        items = "".join(f"<li>{_e(note)}</li>" for note in result.limitations)
        parts.append(f'<div class="notice"><strong>Limitations</strong><ul>{items}</ul></div>')
    roles = {
        **dict.fromkeys(result.completed_effects, "completed effect"),
        **{i: "attempt" for i in result.prohibited_attempts if i not in result.completed_effects},
    }
    supporting = set(result.evidence_ids) | set(result.utility_evidence)
    events = [
        (
            f"<code>{_e(e.event_id)}</code>",
            _e(e.sequence),
            f"<code>{_e(e.kind)}</code>",
            _e(e.source_trust),
            f"<code>{_e(e.target)}</code>",
            _e(e.decision),
            f"<code>{_e(json.dumps(e.details, sort_keys=True))}</code>",
            _e(roles.get(e.event_id, "cited" if e.event_id in supporting else "")),
        )
        for e in result.events
    ]
    parts.append(
        _table(
            "Observed events (alias-only; no file contents)",
            ("Event", "Seq", "Kind", "Trust", "Target", "Decision", "Details", "Role"),
            events,
        )
    )
    parts.append("</section>")
    return "".join(parts)


def _json_block(report: SuiteReport) -> str:
    raw = json.dumps(report.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    inert = raw.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    return f'<script type="application/json" id="agentfence-report">{inert}</script>'


def render_html(report: SuiteReport, *, notes: Sequence[str] = ()) -> str:
    s = report.summary
    counts = s.boundary
    summary_rows = [
        (
            "Boundary",
            f"{counts['PASS']} pass, {counts['FAIL']} fail, "
            f"{counts['INCONCLUSIVE']} inconclusive, "
            f"{counts['NOT_TRIGGERED']} not-triggered",
        ),
        ("Exposure coverage (adversarial)", _ratio(s.exposure_coverage)),
        ("Violations among exposed", _ratio(s.violation_rate)),
        ("Utility complete", _ratio(s.utility_rate)),
        ("Benign controls incomplete", _ratio(s.benign_incomplete_rate)),
        ("Inconclusive", _ratio(s.inconclusive_rate)),
        ("Not exposed (adversarial)", _ratio(s.no_exposure_rate)),
    ]
    overview = [
        (
            f'<a href="#{_e(r.attempt_id)}">{_e(r.scenario_id)}</a>',
            _e(r.variant),
            _e(r.repetition),
            _status(r.boundary),
            _status(r.utility),
            _status(r.infrastructure),
            f"<code>{_e(r.finding_code)}</code>",
            _e(len(r.evidence_ids)),
        )
        for r in report.results
    ]
    body = [
        "<!DOCTYPE html>",
        '<html lang="en"><head><meta charset="utf-8">',
        f'<meta http-equiv="Content-Security-Policy" content="{_e(CSP)}">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="referrer" content="no-referrer">',
        f"<title>AgentFence report {_e(report.run_id)}</title>",
        f"<style>{STYLE}</style></head><body><main>",
        f"<h1>AgentFence Lab report <code>{_e(report.run_id)}</code></h1>",
        f'<p class="notice">{_e(report.disclaimer)}</p>',
        f"<p><strong>Isolation:</strong> {_e(report.isolation.tier)}. "
        f"{_e(report.isolation.claim)}</p>",
        f'<p class="muted">Tool {_e(report.tool_version)} · agent '
        f"{_e(report.agent.name)}-{_e(report.agent.profile)} · scenario profile "
        f"{_e(report.scenario_profile)} · seed {_e(report.seed)} · repeat "
        f"{_e(report.repeat)} · started {_e(report.started_at.isoformat())}</p>",
        "<h2>Summary</h2>",
        _table(
            "Suite metrics (counts with 95% Wilson intervals; no single score)",
            ("Metric", "Value"),
            [(_e(k), _e(v)) for k, v in summary_rows],
        ),
        _table(
            "Attempts",
            ("Scenario", "Variant", "Rep", "Boundary", "Utility", "Infra", "Finding", "Evidence"),
            overview,
        ),
        "<h2>Evidence</h2>",
        *(_attempt(r) for r in report.results),
    ]
    if notes:
        items = "".join(f"<li><code>{_e(n)}</code></li>" for n in notes)
        body.append(f"<h2>Reproduction</h2><ul>{items}</ul>")
    body += [
        '<p class="muted">Redaction: enabled; no transcripts or file contents are included. '
        "The machine-readable report is embedded below as inert JSON.</p>",
        _json_block(report),
        "</main></body></html>",
    ]
    return "\n".join(body) + "\n"
