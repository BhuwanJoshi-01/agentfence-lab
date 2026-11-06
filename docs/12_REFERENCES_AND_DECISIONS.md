# 12 — Verified References, Design Decisions, and Open Questions

**Research check:** 2026-11-06. The web pages below are living resources; before coding an agent adapter, **recheck vendor CLI syntax and permission semantics against the pinned version installed in your environment.**

## 1. Primary references

### Threat and security guidance

1. [OWASP LLM01:2025 Prompt Injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/) — instruction/data confusion, trust boundaries and mitigation approaches.
2. [AWS — Agentic AI / OWASP Top 10 mapping](https://docs.aws.amazon.com/prescriptive-guidance/latest/agentic-ai-security/owasp-top-ten.html) — structured control categories.
3. [GitHub Actions secure use reference](https://docs.github.com/en/actions/reference/security/secure-use) — untrusted workflow context, permission limitations and secret safety.
4. [Docker rootless mode](https://docs.docker.com/engine/security/rootless/) and [rootless troubleshooting](https://docs.docker.com/engine/security/rootless/troubleshoot/) — real-world limitations and effective capability checks.
5. [Docker rootless resource limit tips](https://docs.docker.com/engine/security/rootless/tips/) — cgroup v2 delegation requirements; flags can be ignored when unsupported.

### Vendor coding-agent CLI documentation

6. [Codex CLI reference](https://learn.chatgpt.com/docs/cli/reference) — `codex exec`, JSONL and documented sandbox/working-directory controls.
7. [Claude Code CLI reference](https://docs.anthropic.com/en/docs/claude-code/cli-usage) — `claude -p` noninteractive invocation; confirm current docs.
8. [Claude Code power-user tips](https://support.claude.com/en/articles/14554000-claude-code-power-user-tips) — structured output and `--bare` concerns.
9. [OpenCode CLI commands](https://dev.opencode.ai/v2/docs/cli/commands/) — `opencode run`, `--format json` and current invocation modes.

### Existing open-source projects

10. [AgentTrap](https://github.com/zhmzm/AgentTrap) — dynamic runtime benchmark and utility/safety outcome separation.
11. [Agent3σ-Canary](https://github.com/antgroup/Agent3Sigma-Canary) — extensive sandboxed agent-security evaluation and trace analysis.
12. [Pipelock](https://github.com/pipelab-org/pipelock) — runtime-oriented agent security project; verify current API/features when positioning.

### Data and interface references

13. [SARIF 2.1.0 standard](https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html) — optional later machine-readable findings.
14. [WCAG 2.2 specification](https://www.w3.org/TR/WCAG22/) — UI accessibility target.
15. [GitHub workflow artifacts](https://docs.github.com/en/actions/concepts/workflows-and-actions/workflow-artifacts) — artifact retention and workflow report transport.

## 2. Architecture Decision Records (ADRs)

### ADR-001 — Offline mock adapter before real agents

- **Decision:** ship the full pipeline against a deterministic mock before any real model connectivity.
- **Rationale:** audit the scoring and containment without exposing provider auth or relying on non-deterministic model behavior.
- **Tradeoff:** the first release tests the harness, not security of a real commercial agent; communicate that candidly.
- **Status:** accepted for MVP.

### ADR-002 — Security and utility are independent

- **Decision:** maintain boundary (`PASS/FAIL/INCONCLUSIVE/NOT_TRIGGERED`) and utility (`COMPLETE/INCOMPLETE/UNKNOWN`) axes.
- **Rationale:** unconditional refusal can look safe but is not useful, while an agent can complete a task and still violate a boundary.
- **Status:** accepted.

### ADR-003 — No global safety percentage

- **Decision:** report counts, exposure coverage and per-case evidence rather than marketing a single "safe score".
- **Rationale:** scenario coverage and telemetry are limited; aggregate rates require appropriate denominators and uncertainty.
- **Status:** accepted.

### ADR-004 — Rootless OCI with strict preflight; VM later

- **Decision:** Linux-rootless runtime plus verified network/filesystem/resource constraints for mock MVP. Treat stronger VM isolation as future improvement.
- **Rationale:** accessible for developer users, but not a formal security boundary against kernel/runtime compromise.
- **Status:** conditional on passing empirical isolation tests.

### ADR-005 — Deterministic evaluator, not LLM judge of truth

- **Decision:** all security outcomes backed by trusted telemetry and explicit rules; optional LLM explanation layer never determines primary verdict.
- **Rationale:** hallucinated claims and incomplete traces must not become false evidence.
- **Status:** accepted.

### ADR-006 — Declarative scenario manifests only

- **Decision:** no arbitrary executable hooks in community scenarios for MVP.
- **Rationale:** scenario submission is an untrusted software supply-chain entry point.
- **Status:** accepted.

### ADR-007 — Dedicated auth design for live adapters

- **Decision:** never mount personal agent login directories or pass unrestricted long-lived secrets to a stock CLI in the sandbox. A supported live adapter must prove constrained credential and network handling.
- **Rationale:** agent under test can attempt to read any accessible runtime credential; container isolation protects host, not the token inside the container.
- **Status:** blocking unknown, revisit during M8.

### ADR-008 — Local-only dashboard

- **Decision:** read-only loopback server with origin checks and session authorization; no public multi-user service in MVP.
- **Rationale:** reduce attack surface and complexity.
- **Status:** accepted.

### ADR-009 — Synthetic-only tests

- **Decision:** all secrets, remote destinations, project artifacts and marker data are fake and disposable; no public endpoint for exfil simulation.
- **Rationale:** defensive research without exposing real users or accounts.
- **Status:** accepted.

## 3. Open engineering questions

1. Which Linux distro/kernel/container runtime pair will be the first *officially supported* and continuously tested host?
2. Which broker or file-access interception approach gives reliable `read_requested` evidence without requiring elevated privileges?
3. Should Tier 1 live adapters use a network proxy with temporary constrained tokens or an SDK-based host model broker? What provider auth options really support this?
4. What resource-limit controllers are consistently delegated on target distros, and what is our hard failure rule if not available?
5. What are the user-facing false-positive costs of strict agent permissions across different real agents?
6. How should a report be signed or attested for cross-machine comparison later?
7. What data retention model is acceptable for institutional users who may have sensitive workspace names or logs?
8. Can we make scenario exposure provable with lightweight event collection without changing agent behavior significantly?
9. What is the minimum viable feature improvement over AgentTrap/AgentCanary that independent users will adopt?
10. Should v0.2 emphasize one mature adapter and five scenarios or more mock scenarios and better instrumentation?

## 4. Proposed research spikes (timeboxed)

- **Spike A (2 days):** test rootless Podman vs rootless Docker prerequisites on two supported Linux versions; record limits and evidence.
- **Spike B (2 days):** prototype synthetic file-access broker read denials and event IDs; confirm limitations.
- **Spike C (2 days):** compare two agent CLIs' output stream formats on **benign** synthetic tasks without exposing real secrets; use a dedicated test account/environment.
- **Spike D (3 days):** prototype one-route model proxy in an isolated lab and prove all other outbound routes blocked. If not possible, keep live mode unsupported.

A research spike is not product implementation until the observed behavior is reproduced in automated tests and an ADR is updated.

## 5. Terminology glossary

- **Agent:** interactive or noninteractive coding assistant with project tools and model access.
- **Boundary:** declared limit on files, network or actions required by task.
- **Lower-trust content:** data the agent may read that is not an authorized high-priority instruction.
- **Exposure:** trusted evidence that lower-trust content was actually delivered/read.
- **Attempt:** observed action request, even if denied.
- **Completed effect:** actual read/write/request delivered or artifact modified with evidence.
- **Collector:** mechanism recording observable behavior from outside agent text.
- **Fixture:** deterministic, synthetic workspace resources for a scenario.
- **Utility:** whether legitimate engineering task succeeded according to independent assertions.
- **Inconclusive:** inadequate data or infrastructure conditions for a trustworthy verdict.
- **Offline-safe:** test mode requiring no real model, real credentials or internet connection; it is not a guarantee against all container vulnerabilities.
