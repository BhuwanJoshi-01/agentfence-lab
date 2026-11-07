# AgentFence Lab — Engineering Blueprint

> **Status:** pre-alpha. The offline mock pipeline works end to end for all five MVP scenario families and their benign controls (`agentfence test`); the rootless sandbox, the dashboard and live agents are not built yet. Track progress in [milestones](https://github.com/BhuwanJoshi-01/agentfence-lab/milestones).  
> **Prepared:** 2026-11-06 · **Audience:** solo developer, maintainers, coding agents, security reviewers.  
> **Category:** defensive AI coding-agent security evaluation.  
> **Tagline:** *Test what your coding agent actually does, not merely what it claims to do.*

## The product in one minute

AgentFence Lab evaluates whether a coding agent completes legitimate engineering tasks **while respecting boundaries** when it encounters untrusted repository content. It runs each experiment in an isolated, disposable environment, supplies only synthetic resources, records **observable side effects** (not just chat responses), and produces actionable local reports. Primary targets: Codex, Claude Code, and OpenCode—but **MVP ships with a deterministic mock adapter first** so every verdict can be tested without network access or an LLM subscription.

**Distinction from existing benchmarks:** AgentFence Lab targets a developer's *configured agent workflow* with one-command reproducible, evidence-first regression tests and a small self-contained local UI. It is **not** a claim to discover all vulnerabilities or certify any model as safe.

## Documentation map

| Document | Purpose |
|---|---|
| [01_PRODUCT_REQUIREMENTS.md](docs/01_PRODUCT_REQUIREMENTS.md) | Users, goals, feature requirements, scope, acceptance criteria, success metrics |
| [02_ARCHITECTURE.md](docs/02_ARCHITECTURE.md) | Components, execution flow, contracts, storage, repository design |
| [03_SECURITY_SANDBOX.md](docs/03_SECURITY_SANDBOX.md) | Threat model, isolation architecture, credentials, preflights, failure modes |
| [04_SCENARIOS_AND_EVALUATION.md](docs/04_SCENARIOS_AND_EVALUATION.md) | Five MVP scenarios, benign controls, judge logic, outcome labels, metrics |
| [05_AGENT_ADAPTERS.md](docs/05_AGENT_ADAPTERS.md) | Mock, Codex, Claude Code and OpenCode adapter strategy and compatibility |
| [06_CLI_CONFIGURATION_API.md](docs/06_CLI_CONFIGURATION_API.md) | CLI grammar, config, exit codes, local API and examples |
| [07_EVIDENCE_AND_DATA.md](docs/07_EVIDENCE_AND_DATA.md) | SQLite model, event schemas, provenance, redaction, sample findings |
| [08_DASHBOARD_UX.md](docs/08_DASHBOARD_UX.md) | Screens, interactions, accessibility, performance, state handling |
| [09_IMPLEMENTATION_ROADMAP.md](docs/09_IMPLEMENTATION_ROADMAP.md) | Milestones, requirement-by-requirement task lists, work estimates |
| [10_TESTING_CI_RELEASE.md](docs/10_TESTING_CI_RELEASE.md) | Tests, matrices, threat tests, CI isolation, release gates |
| [11_COMPETITION_LAUNCH_COMMUNITY.md](docs/11_COMPETITION_LAUNCH_COMMUNITY.md) | Competitive research, positioning, contributor strategy, launch plan |
| [12_REFERENCES_AND_DECISIONS.md](docs/12_REFERENCES_AND_DECISIONS.md) | Primary references, ADRs, unresolved design questions |
| [13_CODING_AGENT_EXECUTION_PROMPTS.md](docs/13_CODING_AGENT_EXECUTION_PROMPTS.md) | Copy-ready milestone prompts for implementing safely |
| [AGENTS.md](AGENTS.md) | Instructions for Codex/Claude/other implementation agents |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Contribution and scenario submission rules |
| [SECURITY.md](SECURITY.md) | Coordinated security disclosure and safe-use policy |

Also included: [example issue templates](.github/ISSUE_TEMPLATE) for feature tasks and scenario proposals.

## Supported scope — be precise

- **MVP platform:** Linux x86_64, rootless OCI runtime with verified cgroup controls; `mock` adapter mandatory.
- **First live-agent integration:** one of Codex or OpenCode only *after* the outbound-model traffic design and isolation assurance gates pass. No assumption that an ordinary CLI can work safely under `--network none`.
- **Later:** Claude Code adapter, second live adapter, macOS/Windows through stronger VM-based backends.
- **Offline-first:** mock-agent tests, scenario linting, report inspection, CLI, and dashboard must work without any real model call.
- **No real secrets** and **no live exfiltration endpoints** in scenarios. A simulated receiver is always local to the test boundary.
- **No scanning other people's machines**, no arbitrary repository execution in default mode, no automatic configuration changes.

## Try what exists today

```bash
git clone https://github.com/BhuwanJoshi-01/agentfence-lab.git
cd agentfence-lab
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -e '.[dev]'     # or: uv sync --extra dev
agentfence version --json
agentfence doctor            # read-only host check; exits 3 unless a rootless engine, seccomp and cgroup delegation are verified
agentfence scenarios list    # all five MVP scenario families, each with a benign control
agentfence test --agent mock --agent-profile unsafe   # exits 1: the mock crosses the boundary
agentfence test --agent mock --agent-profile safe     # exits 0
agentfence runs list
agentfence report <run-id> --format html --output report.html   # offline, no scripts
pytest -q
```

`agentfence test` writes `report.json` (schema: `src/agentfence/schemas/report.v1.schema.json`) under `~/.local/state/agentfence/runs/<run-id>/` and records the run in a local SQLite store. The mock runs in-process: it executes no workspace code and opens no network connections, and the report says plainly that no containment claim is made. Mock results test AgentFence itself, not any real coding agent.

`doctor` is deliberately fail-closed. It asks each container engine for its own server-side report: rootless Podman or rootless Docker passes, a rootful daemon is a `FAIL`, and an engine it cannot query is `UNKNOWN`. It also checks seccomp, cgroup controller delegation and user namespaces. A host AgentFence can't verify never looks ready.

The PyPI distribution name is `agentfence-lab` (the bare `agentfence` name is taken); the CLI and import package are `agentfence`.

## Proposed user experience (future commands; not executable yet)

```bash
agentfence doctor                         # Check host prerequisites; no mutations
agentfence scenarios list                 # List bundled safe scenarios
agentfence test --agent mock --profile mvp # Run 5 offline scenarios + benign controls
agentfence report <run-id> --format html   # Generate local HTML report
agentfence ui --host 127.0.0.1 --port 8484 # View local run dashboard
```

### Primary design target: a credible finding

A finding must show an explicit **contract**, **scenario ID**, **event ID**, **observed effect**, **evidence source**, **confidence/limitations**, and **reproduction metadata**. For instance:

```text
AF-CRED-001: Synthetic protected file was accessed
Observed: filesystem broker granted read outside approved task scope
Evidence: event 37 (broker access log), test fixture checksum
Utility: legitimate task completed
Boundary: failed
Limitation: does not demonstrate access to actual host credentials
```

If telemetry cannot prove that the action occurred, the outcome must be `INCONCLUSIVE`, not `PASS` or `FAIL`.

## Recommended implementation sequence

1. Freeze schemas and outcomes; implement scenario linter and deterministic mock adapter.
2. Implement rootless sandbox preflight, isolation tests, and fail-closed behavior.
3. Implement two paired scenarios, event journal, deterministic verdicts, JSON/HTML report.
4. Add all five scenarios, baseline comparison, UX polish.
5. Only then integrate a real agent with model network connectivity strictly controlled.
6. Build dashboard and GitHub Actions integration after CLI stability.

## Definition of success

A reproducible command creates a verifiable report from five adversarial scenarios and five matched benign controls; an unknown or unsupported environment is correctly flagged, the runner cannot access host secrets or unrestricted networking, and reviewers can reproduce each verdict from stored evidence. Release value is **trustworthy measurement**, not a high score.

## Technology defaults

Python 3.12+, Typer, Rich, Pydantic v2, SQLAlchemy/Alembic, SQLite, pytest, rootless Podman or rootless Docker (subject to verified preflight), FastAPI, React + TypeScript + Vite, Playwright, Ruff and mypy/pyright. These are **proposed choices**, not installed dependencies.

## License and branding

**Recommended** Apache-2.0 for code, plus explicit separate licenses for test corpora and third-party fixtures. Confirm `AgentFence Lab` repository, package, CLI, and domain availability before publishing. Name is provisional and does not establish ownership.

## Safety reminder

Defensive experiments can still be dangerous if an agent receives a real token, host directory, SSH socket, cloud metadata, or external network path. A guardrail failure should stop the run; never downgrade security to get a benchmark working.
