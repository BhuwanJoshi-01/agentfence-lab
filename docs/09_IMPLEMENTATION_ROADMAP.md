# 09 — Milestone-by-Milestone Implementation Plan and Task Backlog

## 1. Delivery strategy

Build credibility in layers. **Version 0.1 is an offline, evidence-correct mock sandbox lab**, not a universal multi-agent pentesting system. Extend to real-agent CLI integrations only after hard isolation tests and a reviewed model connectivity story. Estimated solo developer effort: 8–12 focused weeks for robust v0.1 including docs and basic web viewer; a live adapter may add several weeks or remain unsupported pending provider auth constraints. Estimates are planning placeholders.

**Progress columns:** `NOT_STARTED`, `READY`, `IN_PROGRESS`, `IN_REVIEW`, `BLOCKED`, `DONE`. A task is DONE only after tests, docs and evidence artifact links are recorded.

**Work-in-progress limit:** at most 2 active tasks. Commit small, testable slices. Do not let a coding agent "complete" the project by creating placeholder methods with TODOs.

## 2. Milestone overview

| ID | Timebox | Goal | Exit gate |
|---|---|---|---|
| M0 | 3–4 days | Discovery, testbed, schema freeze | threat model and project skeleton reviewed |
| M1 | week 1 | Python package, CLI, strict config | automated CLI contract tests |
| M2 | weeks 2–3 | Rootless sandbox and isolation preflight | negative isolation tests pass |
| M3 | weeks 3–4 | Scenario engine, mock adapter, fixtures | 5 scenarios + controls reproducible |
| M4 | weeks 4–5 | Evidence, evaluator and storage | fail/pass/inconclusive truth table tests |
| M5 | weeks 5–6 | JSON/HTML reporting and baseline diff | schema tests + reproducibility pass |
| M6 | weeks 6–8 | Dashboard and usability | Playwright/keyboard/privacy checks |
| M7 | weeks 8–10 | QA, community documentation, pre-release | v0.1 release checklist |
| M8 | post-v0.1 | Live adapter security review + spike | feasibility and gate decision |
| M9 | after gate | First live adapter, CI integration | reproducible contained real tests |

Timelines overlap only when tasks are not on each other's critical path.

## 3. M0 — Research, planning, threat validation

- [ ] `AF-0001` Verify naming availability and existing open-source project positioning.
- [ ] `AF-0002` Review OWASP LLM01 and LLM06, existing AgentTrap/AgentCanary methods and known limitations.
- [ ] `AF-0003` Pin supported Linux distro, Python version and rootless backend used for initial development.
- [ ] `AF-0004` Write ADR: why deterministic mock first; identify next live-agent candidate.
- [ ] `AF-0005` Create architecture diagram and complete trust-boundary inventory.
- [ ] `AF-0006` Define schema versions and immutable outcome vocabulary.
- [ ] `AF-0007` Interview at least 3 developer users and collect one concrete task they want tested.

**Acceptance:** user journeys and no-go conditions are documented; none of the core requirements depend on an unspecified security mechanism.

## 4. M1 — Foundation

- [ ] `AF-0101` Create package with Python 3.12+, `pyproject.toml`, console-script entry point.
- [ ] `AF-0102` Add Typer `version`, `doctor`, `scenarios list` stub with real behavior.
- [ ] `AF-0103` Create strict Pydantic models for config, capabilities and scenario manifest.
- [ ] `AF-0104` Add Ruff, type checker, pytest, coverage, pre-commit and dependency audit.
- [ ] `AF-0105` Set XDG state/config directories, restrictive file permissions and atomic report writing.
- [ ] `AF-0106` Add structured logger (stderr), log-size cap, redaction by allowlist.
- [ ] `AF-0107` Write CLI help/snapshot tests and validation failure tests.

**Acceptance:** `pip install -e .` exposes working `agentfence` command; `doctor` reports missing dependencies rather than crashing; tests pass on clean Linux environment.

## 5. M2 — Sandbox and containment

- [ ] `AF-0201` Implement runtime capability probe (rootless, cgroup, seccomp, namespaces, LSM availability).
- [ ] `AF-0202` Build pinned minimal mock image with no dependencies fetched at run time.
- [ ] `AF-0203` Implement strict sandbox policy config with no host-home/sockets, immutable root, ephemeral work dir.
- [ ] `AF-0204` Implement bounded process supervisor with timeout and clean shutdown.
- [ ] `AF-0205` Implement network-disabled Tier 0 and prove no host/internet egress.
- [ ] `AF-0206` Verify applied resource limits rather than only requested command arguments.
- [ ] `AF-0207` Implement run-specific resource labels and stale cleanup reconciliation.
- [ ] `AF-0208` Write minimum 10 sandbox negative tests including path, socket, egress and abnormal cleanup.

**Acceptance:** controlled attempted access to prohibited synthetic resources fails; preflight catches deliberately misconfigured rootless/cgroup environments; no fallback with lower security; results include attestation ID.

## 6. M3 — Scenario and mock engine

- [ ] `AF-0301` Strict manifest loader, schema and canonical path validation.
- [ ] `AF-0302` Deterministic seed-based fixture generator (all synthetic assets).
- [ ] `AF-0303` Implement paired README scenario and benign case.
- [ ] `AF-0304` Implement brokered synthetic protected-file test.
- [ ] `AF-0305` Implement local-only network attempt test.
- [ ] `AF-0306` Implement tool-output authority spoofing test.
- [ ] `AF-0307` Implement artifact-contamination test with CSV oracle.
- [ ] `AF-0308` Mock adapter profiles: safe, unsafe, refusal, no-exposure, missing-telemetry, timeout.
- [ ] `AF-0309` Fixture integrity/hashing tests and reproducible generation across clean runs.

**Acceptance:** every scenario can generate benign/adversarial fixtures, exercise test harness, emit necessary observations and verify task utility without a network model.

## 7. M4 — Evidence and evaluation

- [ ] `AF-0401` Create structured event taxonomy with trusted and agent-reported provenance.
- [ ] `AF-0402` Build collector coverage reports and bounded event stream.
- [ ] `AF-0403` Add file before/after digest, mock network sink, protected file broker.
- [ ] `AF-0404` Implement deterministic boundary evaluator with test truth tables.
- [ ] `AF-0405` Implement independent utility and infrastructure outcomes.
- [ ] `AF-0406` Add SQLite migrations, FK enforcement, append-only event rows, per-artifact digest.
- [ ] `AF-0407` Implement interruption/retry recovery and stop-safe cleanup.
- [ ] `AF-0408` Build golden scenarios for PASS, FAIL, INCONCLUSIVE, NOT_TRIGGERED and partial evidence.

**Acceptance:** no `PASS` with required event loss; no file-read claim from a diff; no external network data transfer; 100% test coverage of evaluator decision branches and explicit negative tests.

## 8. M5 — Reports and baseline comparisons

- [ ] `AF-0501` Machine JSON schema and schema-version acceptance tests.
- [ ] `AF-0502` Human CLI summary with explicit limitations and evidence counts.
- [ ] `AF-0503` Self-contained static HTML report with CSP, escaped strings and no remote assets.
- [ ] `AF-0504` Baseline comparison across compatible scenario versions and policies.
- [ ] `AF-0505` Tests for redaction, data retention, large events, XSS and bad input paths.
- [ ] `AF-0506` Export verified evidence links and reproduction metadata.

**Acceptance:** review of a report identifies cause and evidence in under 2 minutes; exported file opens offline and does not expose synthetic secret values.

## 9. M6 — Local web UI

- [ ] `AF-0601` FastAPI read-only loopback API with session auth and origin check.
- [ ] `AF-0602` React overview and recent-runs table.
- [ ] `AF-0603` Attempt detail with timeline, provenance, confidence and utility.
- [ ] `AF-0604` Scenario catalog and isolation diagnostics.
- [ ] `AF-0605` Comparison screen and incomparable warnings.
- [ ] `AF-0606` Keyboard, screen reader, axe + Playwright, mobile responsiveness.
- [ ] `AF-0607` Test request-origin handling and HTML injection defenses.

**Acceptance:** all mandatory UI paths work without internet; no remote binding; all statuses explained without relying on color; accessibility issues triaged.

## 10. M7 — Release and community

- [ ] `AF-0701` Clean install on a fresh, supported Linux machine and documented distro image.
- [ ] `AF-0702` Write 90-second example video or GIF based on actual results (not mock output passed off as live).
- [ ] `AF-0703` `README`, comparison with existing tools, screenshots, limitations, quickstart.
- [ ] `AF-0704` Add MIT/Apache license decision, SPDX headers, copyright policy.
- [ ] `AF-0705` Publish CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, issue forms and release changelog.
- [ ] `AF-0706` Pin dependencies and images, generate SBOM, verify clean dependency scan.
- [ ] `AF-0707` Tag `v0.1.0` with installable wheel/sdist, checksums, documented known issues.
- [ ] `AF-0708` Invite 5 independent testers; incorporate at least 2 reproducible bug reports.

**Acceptance:** strangers can complete mock smoke test with README alone, errors are honest, and contributors can add a benign fixture safely.

## 11. M8/M9 — Live agent, only after safety decision

- [ ] `AF-0801` Identify safe auth options per agent; no host login state mounted.
- [ ] `AF-0802` Design and prototype outbound provider gateway or host broker with explicit proof of confinement.
- [ ] `AF-0803` Perform adversarial egress, DNS, local-network and credential exposure tests.
- [ ] `AF-0804` Pin CLI version and normalize structured tool events.
- [ ] `AF-0805` Run one toy benign task and one adversarial case on dedicated constrained credentials.
- [ ] `AF-0806` Document unobservable actions and mark affected cases inconclusive.
- [ ] `AF-0807` Cost caps, model rate limits, no accidental recurring API use.
- [ ] `AF-0808` Independent security review; decide launch / defer / redesign.

**Stop rule:** if there is no safe provider connection and credential story, release the mock runner and research report without enabling real adapters. Do not compromise isolation for shipping dates.

## 12. Task card format

```markdown
### AF-0404 — Deterministic Boundary Evaluator
**Status:** NOT_STARTED
**Owner:** unassigned
**Dependencies:** AF-0301, AF-0401, AF-0402
**Goal:** Classify all scenario effects from authoritative events.
**Scope:** pure functions and typed result models; no network or agent calls.
**Implementation steps:** implement verdict truth table; add evidence ref validation; add guard for missing required collectors.
**Tests:** 4 outcomes x 5 scenarios + telemetry-loss and inconsistent-event tests.
**Definition of done:** passing tests, API docs, updated examples and reviewed change.
**Evidence:** CI test run link, sample JSON output.
```

Use `.github/ISSUE_TEMPLATE` and a GitHub Project board, or a `/Progress` folder if you prefer a local tracker. Do not duplicate live status in five places; GitHub Issues should be source of truth unless you deliberately build a sync layer.

## 13. Four launch-time hard questions

1. Do reviewers agree that evidence actually supports each verdict?
2. Does an unknown host configuration fail safely rather than fall back silently?
3. Can someone reproduce a finding with the exact same fixture and pinned adapter?
4. Can a new user understand the distinction between **attempted**, **blocked** and **completed** impact?

If any answer is no, delay the security claims and continue validation.
