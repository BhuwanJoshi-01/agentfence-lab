# 08 — Dashboard UX, Interaction, and Accessibility Specification

## 1. Product principles

The interface is a **local read-only investigation console**, not a colorful "security score" that hides uncertainty. It must answer: What was the agent asked to do? What untrusted content did it receive? What did it actually attempt? Did the legitimate task succeed? Which instrumentation proves the finding? What changed since last run?

UI should feel modern and focused, with strong typography, generous spacing, and concise evidence labels. Avoid scary red overlays or an invented overall safety percentage.

## 2. Information architecture

```text
Overview
├── Runs
│   ├── Run details
│   │   ├── Scenario matrix
│   │   ├── Attempt detail
│   │   │   ├── Timeline
│   │   │   ├── Evidence
│   │   │   ├── Fixture / trust boundary
│   │   │   └── Utility result
│   │   └── Export
│   └── Compare two runs
├── Scenario catalog
├── Agent compatibility
├── Environment / isolation checks
└── About / limitations / privacy
```

V0.1 UI can ship after CLI/HTML export. Do not let dashboard become a dependency for running tests.

## 3. Screen 1: Overview

**Sections:**

- recent suite runs with name, adapter, relative and absolute timestamp, current status;
- four separate counters: boundary FAIL, PASS, INCONCLUSIVE, NOT_TRIGGERED;
- utility completion and benign refusal separate from boundary scores;
- effective isolation label and a link to test preflight;
- "View reports" call to action, not "Make safe";
- warning if existing runs lack required telemetry or use outdated adapter versions.

**No deceptive UI:** do not represent a passing score of 5/5 as "100% secure". Show `5/5 exposed cases passed; 5-case scope only` and model/seed/sample-size metadata.

## 4. Screen 2: Run detail

Header: `Run #... · Mock adapter · OFFLINE_SAFE · 2026-11-06 · 10 attempts`. Below: scenario matrix with columns **Scenario**, **Attack surface reached?**, **Boundary**, **Utility**, **Infrastructure**, **Evidence**, **Duration**, **Comparison**.

**Filters:** outcome, scenario family, agent, date, instrument source. **Sort:** severity, date, scenario ID, confidence. **Search:** scenario names and finding codes only (do not index raw sensitive content by default).

An `INCONCLUSIVE` row is visually distinct from both PASS and FAIL, and explains which observation source was missing. A `NOT_TRIGGERED` row explains that the agent did not encounter the injected content, so resistance was not tested.

## 5. Screen 3: Attempt and timeline

Two-column desktop layout (single column on mobile): left event timeline, right evidence inspector. Events carry trust badges `HOST_OBSERVED`, `BROKER_OBSERVED`, `AGENT_REPORTED`, `DERIVED`. The inspector shows the specific trust boundary and how the event supports or contradicts the verdict.

**Timeline shape:**

```text
10:02:11  Run started (isolation verified)
10:02:12  Fixture staged (README digest: ...)
10:02:15  Agent received README [broker observed]
10:02:19  Proposed tool use [agent reported]
10:02:20  Denied read of SYNTHETIC_SECRET [broker observed]
10:02:27  Authorized tests passed [utility runner]
10:02:28  Boundary verdict: FAIL (prohibited attempted access)
```

Status labels must distinguish `attempted` vs `completed`. No deceptive "data exfiltrated" label for blocked local mock network attempts.

**Details panel:** explanation, outcome triplet, evidence IDs, collector coverage, fixture info, allowed/denied scopes, reproduction command and redacted comparison.

## 6. Screen 4: Run comparison

Compare two compatible runs with columns: baseline, candidate, changed outcome, changed utility, changed observability, adapter version, scenario version and model ID. Detect incompatible scenarios or missing telemetry and show `INCOMPARABLE` instead of fabricated delta.

Examples:

- `AF-README-001: FAIL -> PASS` (potential improvement, same scenario, n=3 repetitions each).
- `AF-CRED-001: PASS -> INCONCLUSIVE` (visibility regression, **not** an improvement).
- `AF-TOOL-001: utility COMPLETE -> INCOMPLETE` (possible overdefense).

For stochastic model runs, show raw attempts and sample size, not a single confident arrow based on one result.

## 7. Screen 5: Scenario catalog

Each card shows a scenario's ID/version, purpose, legitimate task, untrusted surface, protected boundary, required collectors, expected behavior, benign pair and limitations. Scenario source is presented as **read-only sanitized text**. Show license and reviewer decision metadata for community additions.

Filters by risk taxonomy (OWASP LLM01/LLM06 etc.), source type and runtime requirement. A scenario that requires unavailable telemetry should show `unsupported on this host` with reason.

## 8. Screen 6: Environment diagnostic

Detailed preflight rows:

| Check | Example presentation |
|---|---|
| Rootless runtime | PASS — verified effective user namespace |
| Network none | PASS — external and host-local attempts denied |
| Memory/pids limits | PASS — cgroup v2 checks applied |
| Host directories absent | PASS — mount table audited |
| Protected resource broker | PASS — synthetic read request recorded |
| Real adapter auth | UNSUPPORTED — no scoped credentials configured |

A failed critical check disables live testing; no UI "proceed anyway" escape hatch in MVP.

## 9. Design system proposal

- Framework: React + TypeScript + Vite + TanStack Query; accessible components (Radix primitives if reviewed) and CSS variables.
- Typography: high-legibility system font stack (no remote fonts), tabular digits for timestamps.
- Palette: neutral surface; one restrained accent; semantic red for verified failures, amber for inconclusive, green for pass. Never rely on color alone.
- Components: `RunCard`, `OutcomeTriplet`, `ScenarioTable`, `EvidenceTimeline`, `EvidenceInspector`, `SourceTrustTag`, `IsolationPanel`, `ComparisonTable`, `RedactionNotice`.
- Design tokens for spacing 4/8/12/16/24/32px; accessible focus outlines; comfortable touch target sizing.
- Dark/light themes with user system preference; no excessive motion.

## 10. Accessibility criteria

Target WCAG 2.2 **AA** and manual verification: fully keyboard traversable tables and drawers; skip-to-content; visible focus; screen-reader announcements for filtering; icon text labels; statuses expressed in text; minimum 4.5:1 text contrast and 3:1 for essential UI graphics; reduced motion; no auto-updating timeline that steals focus; high zoom and mobile responsive.

Axe-core automation is a **baseline**, not proof of accessibility compliance. Use Playwright + axe plus manual keyboard and screen-reader checks.

## 11. Privacy and offline design

Serve all assets locally; no CDNs, analytics, trackers, external fonts or video embeds. Evidence fields returned by the API are already redacted. No client-side access to local filesystem other than a reviewed report export. UI must escape untrusted text, use strict CSP, and never directly render arbitrary Markdown HTML or raw model output.

## 12. Suggested local API consumption

- Use generated OpenAPI types from FastAPI schemas.
- Prefer server-side filtering/pagination for large traces.
- Cache report data by run ID and invalidate on run status transition.
- Render large timelines with virtualization while preserving keyboard navigation and accessibility.
- All network fetches confined to `127.0.0.1` with local session auth. Explicit error boundaries for API unavailable, report corrupted, token expired, run pruned.

## 13. UI deliverable checklist

- [ ] Overview supports PASS/FAIL/INCONCLUSIVE/NOT_TRIGGERED simultaneously.
- [ ] Run details show utility separately from security.
- [ ] Evidence inspector distinguishes self-report and host observed sources.
- [ ] Timeline displays denied vs completed operations accurately.
- [ ] Compare screen refuses incompatible versions.
- [ ] Offline report viewer loads with internet disconnected.
- [ ] Keyboard navigation and axe tests pass; manual audit documented.
- [ ] No raw secrets, host paths or script-capable text leaks through client.
- [ ] UI only binds loopback and does not launch agent commands in v0.1.
