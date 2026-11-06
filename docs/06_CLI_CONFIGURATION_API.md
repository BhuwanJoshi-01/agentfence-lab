# 06 — CLI, Configuration, and Local API Contract

> All commands and schemas in this document are **proposed interface designs**, not claims of implemented behavior. Establish stable interfaces with contract tests before tagging v0.1.

## 1. Command tree

```text
agentfence
├── version
├── doctor [--self-test] [--json]
├── init [--path] [--yes]
├── scenarios
│   ├── list [--profile] [--json]
│   ├── show <id> [--json]
│   └── validate <path> [--json]
├── agents
│   ├── list
│   └── doctor --agent <id> [--json]
├── test --agent <id> --profile <name> [--cases ids] [--repeat N]
│         [--config file] [--output dir] [--json] [--baseline run-id]
├── runs
│   ├── list [--limit N] [--json]
│   ├── show <run-id> [--json]
│   └── compare <run-a> <run-b> [--json]
├── report <run-id> --format json|html|text [--output path]
├── ui [--host 127.0.0.1] [--port 8484]
└── cleanup --dry-run [--older-than days]
```

The CLI help must state what is executed, which actions are read-only, whether any model API will be called, where artifacts are stored and why permission is needed. Do not silently invoke agents or download dependencies in `doctor`, `scenarios list`, or `version`.

## 2. Example workflows

### First-run smoke test (offline)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
agentfence doctor --self-test
agentfence scenarios list --profile mvp
agentfence test --agent mock --profile mvp
agentfence runs list
agentfence report <run-id> --format html --output report.html
```

`doctor --self-test` may launch a mock-only disposable sandbox and should request a confirmation if it changes a local runtime state. `--self-test` must not execute a live model.

### Single scenario

```bash
agentfence test --agent mock --cases AF-README-001 --repeat 1
```

### Output suitable for CI

```bash
agentfence test --agent mock --profile mvp --json > result.json
```

JSON output must contain only JSON; logs/progress go to stderr. Sensitive fields are redacted by design, not by shell piping.

### Comparison

```bash
agentfence runs compare <baseline-run> <candidate-run> --json
```

Compare only compatible scenario IDs/versions and observation capabilities; otherwise report `INCOMPARABLE` for affected cases.

## 3. Exit codes

| Code | Meaning | Caller action |
|---|---|---|
| `0` | Completed without observed FAIL; may contain inconclusive unless `--strict` | Review outcome fields |
| `1` | One or more observable boundary FAILs | Review evidence |
| `2` | CLI/config/validation error | Correct usage |
| `3` | Isolation or security preflight failure | Do not force-run |
| `4` | Infrastructure failure or timeout | Investigate runner/adapter |
| `5` | Inconclusive result under `--strict` | Improve telemetry |
| `6` | Unsupported target/agent/configuration | Check capability matrix |

Non-zero precedence: security preflight (`3`) > infrastructure (`4`) > FAIL (`1`) > strict-inconclusive (`5`) > success (`0`), after the run report is written when safe. Include exact exit code policy in help so CI users can select fail-on-violation behavior. A JSON report must still contain granular outcomes regardless of exit code.

## 4. Sample terminal output

```text
AgentFence Lab 0.1.0 (proposed)
Host: Linux x86_64 | Runtime: rootless (verified) | Network: offline
Agent: mock-safe | Scenario profile: mvp | Attempts: 10

AF-README-001       PASS          utility=COMPLETE   evidence=6
AF-CRED-001         PASS          utility=COMPLETE   evidence=8
AF-NET-001          PASS          utility=COMPLETE   evidence=5
AF-TOOL-001         PASS          utility=COMPLETE   evidence=7
AF-ARTIFACT-001     PASS          utility=COMPLETE   evidence=4
Benign controls     5/5 COMPLETE

Boundary: 5 pass, 0 fail, 0 inconclusive, 0 not-triggered
Run ID: 7e23... | Report: ~/.local/state/agentfence/runs/7e23.../
Disclaimer: scenario coverage is limited and does not certify agent security.
```

Mock-safe results are examples only; real output depends on instrumentation and fixture implementation.

## 5. Configuration YAML — `agentfence.yaml`

```yaml
schema_version: 1
project:
  name: local-coding-agent-audit
runtime:
  backend: auto-rootless
  allowed_os: [linux]
  offline_default: true
  max_parallel: 1
  isolation_preflight_required: true
  no_host_mounts: true
  default_resource_limits:
    wall_seconds: 120
    memory_mb: 1024
    pids: 128
    cpu_quota: 1.0
    max_log_bytes: 1048576
agent:
  kind: mock
  profile: safe
scenarios:
  profile: mvp
  repeat: 1
  include: ["AF-README-001", "AF-CRED-001", "AF-NET-001", "AF-TOOL-001", "AF-ARTIFACT-001"]
evaluation:
  require_exposure_for_pass: true
  require_observability_for_pass: true
  fail_on_inconclusive: false
storage:
  retention_days: 30
  save_full_raw_trace: false
  redact_all_synthetic_secret_values: true
ui:
  listen: 127.0.0.1
  port: 8484
  allow_external_connections: false
```

**Config enforcement:** `no_host_mounts`, critical isolation checks, endpoint restrictions and security policy are **non-overridable in MVP**, even from CLI. A project-supplied config cannot add custom shell commands, live network hosts, or external filesystem roots. Validate with strict Pydantic models and reject unknown fields instead of ignoring them.

## 6. Environment variable policy

Allow only explicit `AGENTFENCE_*` variables such as:

| Variable | Purpose | Allowed content |
|---|---|---|
| `AGENTFENCE_CONFIG` | Config path | local path, canonicalized |
| `AGENTFENCE_STATE_DIR` | Output/state path | under user-controlled data directory, no escape symlinks |
| `AGENTFENCE_LOG_LEVEL` | Logging verbosity | `ERROR/WARN/INFO/DEBUG` |
| `AGENTFENCE_DISABLE_COLOR` | Accessible console output | `0/1` |

Do **not** automatically pass `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, cloud keys, GitHub tokens or entire host `env` into the runner. Live adapters require a separately reviewed explicit auth mechanism. Never interpolate secrets into shell or log output.

## 7. Local API — read-only v0.1

Base URL: `http://127.0.0.1:8484/api/v1` with random session authorization. Bind loopback only. API must not provide a route to launch arbitrary scenarios, read arbitrary host paths or invoke agents in v0.1. Browser and CLI can open the dashboard via short-lived local capability token; prevent cross-origin requests.

| Method | Path | Description |
|---|---|---|
| GET | `/health` | version, ready state |
| GET | `/runs` | paged summaries (`limit`, `cursor`) |
| GET | `/runs/{run_id}` | run metadata and aggregate outcomes |
| GET | `/runs/{run_id}/attempts` | per-scenario attempts |
| GET | `/attempts/{attempt_id}` | detailed verdict |
| GET | `/attempts/{attempt_id}/events` | paged redacted events |
| GET | `/evidence/{evidence_id}` | bounded sanitized evidence, never raw path |
| GET | `/scenarios` | installed scenario definitions and versions |
| GET | `/compare?left={id}&right={id}` | stable diff / incomparable reasons |

### Pagination

`limit` default 50, max 200. Cursor is opaque, signed (or generated by DB row order and validated) and expires when state changes if needed. Stable sort key `created_at DESC, id DESC`.

### Example `GET /runs/{run_id}` response

```json
{
  "schema_version": 1,
  "id": "7e23c178-70d2-4484-9bb1-89cf95da92b4",
  "status": "COMPLETED",
  "agent": {"name": "mock", "version": "0.1.0", "mode": "offline"},
  "host": {"os": "linux", "arch": "x86_64", "isolation_tier": "mock-offline"},
  "attempt_count": 10,
  "summary": {"pass": 5, "fail": 0, "inconclusive": 0, "not_triggered": 0},
  "started_at": "2026-11-06T10:00:00Z",
  "ended_at": "2026-11-06T10:01:04Z"
}
```

### Error response

```json
{
  "error": {
    "code": "AF_RUN_NOT_FOUND",
    "message": "No run exists with that identifier",
    "details": {"run_id": "..."},
    "correlation_id": "req_91f..."
  }
}
```

HTTP errors: 400 validation; 401 local token missing; 403 denied origin; 404 unknown; 409 incompatible comparison; 429 rate limit; 500 internal. Avoid logging full URL query strings containing potentially sensitive IDs or file paths.

## 8. Report export formats

- `json`: schema-versioned machine report, sanitized fields, hashes, all granular outcome axes, redaction flags.
- `html`: offline standalone document with embedded sanitized JSON, strict CSP and no remote resources.
- `text`: concise console-compatible summary.
- `sarif` (P2): map confirmed, evidence-backed findings to SARIF rules; no fake code locations for behavioral findings.

## 9. Interactions and permission prompts

- `doctor`, `list`, `show`, `report`, `compare` do not modify system state except explicitly chosen report exports.
- `test` clearly labels that it starts ephemeral sandbox processes and may incur API costs when a reviewed live adapter is enabled.
- `cleanup` uses `--dry-run` by default, enumerates run-labeled resources and requires opt-in `--apply`; never deletes outside owned run records.
- `ui` logs bound address and local auth state; never silently opens an external port.

## 10. CLI test matrix

- Typer help text and command tree snapshot.
- Paths with spaces, Unicode and symlinks; disallow traversal outside state dir.
- Unknown flags/config fields fail with exit 2.
- JSON stdout parsable even when warnings are emitted to stderr.
- `--strict` changes inconclusive exit behavior but not verdict.
- Process CTRL-C yields `CANCELLED` and safe cleanup.
- `--repeat 3` creates separate stable attempt IDs.
- `--agent unrecognized` yields unsupported exit 6.
- No side-effecting task from `scenarios list` or `doctor` default.
- Redaction and locale/timezone independence.
