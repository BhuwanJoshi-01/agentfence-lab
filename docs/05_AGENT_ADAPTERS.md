# 05 — Agent Integration and Adapter Specification

## 1. Intent

An adapter should wrap an existing coding agent's **documented execution mode**, normalize observable events, and report effective capabilities. The adapter must not bypass provider safeguards, impersonate a user, silently reuse personal credentials, or claim permissions it cannot enforce. It is an integration bridge, not a model safety judge.

**MVP adapter:** `mock`, offline, deterministic.  
**First live adapter:** choose exactly one after security gates; Codex is a reasonable candidate because it has documented noninteractive JSONL mode. OpenCode is a second candidate. Claude Code follows after verifying its runtime/permission interactions.

## 2. Core interface

```python
@dataclass(frozen=True)
class AdapterCapabilities:
    name: str
    binary_version: str
    noninteractive: bool
    structured_events: bool
    supports_repo_read: bool
    supports_file_write: bool
    supports_tool_execution: bool
    supports_model_proxy: bool  # verified, not guessed
    supports_isolated_config_root: bool
    metadata: Mapping[str, str]

@dataclass(frozen=True)
class LaunchPlan:
    argv: tuple[str, ...]
    working_directory: str
    allowed_environment_names: tuple[str, ...]
    timeout_seconds: int
    stdout_limit_bytes: int
    stderr_limit_bytes: int

@dataclass(frozen=True)
class AgentEvent:
    event_type: str
    timestamp_monotonic_ns: int
    source_id: str
    actor: str
    payload_redacted: dict

class AgentAdapter(Protocol):
    def probe(self, sandbox: SandboxRef) -> AdapterCapabilities: ...
    def prepare(self, attempt: AttemptSpec) -> LaunchPlan: ...
    async def execute(self, plan: LaunchPlan, observer: Observer) -> ExitSummary: ...
    def normalize_event(self, raw: bytes) -> AgentEvent | None: ...
```

**Contract:** `prepare` builds an argument array and explicitly narrowed environment, not a shell string. Agent output is never trusted as a permission grant. Each adapter can declare unsupported modes and abort safely.

## 3. `mock` adapter — required first

Create a deterministic scenario runner for the five MVP cases. It reads an attempt manifest produced by the controller and emits structured fake action proposals, intentionally touching only synthetic paths and local sinks through instrumented interfaces.

**Profiles:**

- `mock-safe`: completes legitimate task, ignores traps.
- `mock-unsafe`: performs one forbidden synthetic action *within the sandbox* and still completes task.
- `mock-refusal`: declines benign and adversarial task, for overdefense tests.
- `mock-no-exposure`: does legitimate task without encountering trap.
- `mock-no-telemetry`: simulates collector failure.
- `mock-timeout`: deterministic bounded timeout.

This adapter's aim is to test pipeline correctness, not emulate LLM reasoning or provider protocols. It needs zero API keys and zero internet.

## 4. Codex adapter (candidate for first live support)

Official CLI documentation currently documents `codex exec` for noninteractive use, `--json` for JSONL events, `--cd` for workspace and `--sandbox` for model-generated command controls. The documented sandbox modes include `read-only`, `workspace-write`, and a dangerous full-access variant. **Do not** use the dangerous variant; do not rely on CLI sandbox flags as a substitute for outer OS isolation.

Reference: [Codex CLI reference](https://learn.chatgpt.com/docs/cli/reference).

**Illustrative command shape only** (verify `codex exec --help` on pinned version):

```bash
codex exec --json --cd /work --sandbox workspace-write \
  "Fix the toy calculator test without modifying unrelated files"
```

**Required integration checks:**

1. Agent binary/version inside isolated image is pinned and recorded.
2. Host Codex config/auth directories are not mounted; no default discovery of host state.
3. Effective permissions match scenario requirements; no unreviewed overrides.
4. JSONL event schema is mapped by adapter unit tests to stable internal events.
5. Model connectivity is only through reviewed allowlisted gateway/broker, not unrestricted network.
6. Evidence source for a file read/denied operation is from trusted collector, not JSONL alone.
7. Agent's final response and tool logs are captured separately with size limits and redaction.
8. All run data written into scenario workspace or trusted output channel, not global host state.

### Pinned compatibility matrix

Maintain `compatibility/codex.json` with: tested binary SHA/version, verified output event examples (sanitized), supported flags, known missing observations, network/auth mechanism, tested date, and test result hashes. Unknown versions should be warning/unsupported depending on policy; no silent unsafe fallback.

## 5. Claude Code adapter (post-MVP)

Anthropic documentation describes noninteractive `claude -p`, structured `--output-format=stream-json` and options such as `--bare` to control configuration loading. Use the **current installed CLI's** help and official docs for an exact compatibility profile; flags may change over time.

References: [Claude Code CLI reference](https://docs.anthropic.com/en/docs/claude-code/cli-usage) and [Claude Code power-user tips](https://support.claude.com/en/articles/14554000-claude-code-power-user-tips).

**Illustrative command shape only:**

```bash
claude -p "Fix the toy calculator test" \
  --output-format=stream-json --verbose --bare
```

**Special concerns:** home-directory config discovery, settings/MCP loading, approval/permissions, potential plugin hooks, authentication state and stdout formats. Verify exactly what the CLI reads in isolated mode. A `--bare` flag may reduce config loading but does not by itself prove credential isolation, filesystem confinement, or network restrictions. Never pass shared personal auth into untrusted testing workspace.

## 6. OpenCode adapter (post-MVP)

Documentation describes `opencode run` with `--format json` and model selection; note that current docs also describe shared background server behavior for some invocation paths. Never attach to a developer's existing global server. Require standalone or isolated equivalent when supported and verified by version. Avoid `--auto` if it expands approval scope beyond target scenario.

Reference: [OpenCode CLI commands](https://dev.opencode.ai/v2/docs/cli/commands/).

**Illustrative command shape only:**

```bash
opencode run --format json "Fix the toy calculator test"
```

**Special concerns:** global providers/credential resolution, background processes, shared sessions, plugin loading and runtime permissions. An adapter must force isolated config/state dirs and validate that no cross-project sessions are visible before supporting a real-agent test.

## 7. Network and authentication decision matrix

| Scenario | Supported? | Justification |
|---|---|---|
| Mock agent with no internet | Yes, v0.1 | Pure sandboxed, synthetic |
| Stock CLI using personal host login session | **No** | Host credential exposure |
| Stock CLI requiring unrestricted internet | **No** | Breaks network boundary |
| Live CLI with isolated short-lived constrained token and proven egress policy | Candidate post-MVP | Needs security review, cost/rate limits |
| Adapter using audited host-side model broker with no credential in guest | Candidate post-MVP | Requires SDK/protocol integration proof |
| Remote managed browser agent with no local action trace | Unsupported | Cannot prove local effects |

Be prepared for a provider integration to remain unsupported. That is better than relaxing containment.

## 8. Observation normalization

Adapter-provided messages include provenance:

```json
{
  "kind": "agent.tool_call",
  "adapter": "codex",
  "source_trust": "agent_reported",
  "timestamp_monotonic_ns": 91723599123,
  "redacted": {"tool": "shell", "argv_preview": "pytest -q"}
}
```

The same action may have a separate **trusted** supervisor event. Correlation is by run ID, process ID (if available), bounded time window and a normalized command or file path. Never discard raw evidence before storing sanitized digest.

## 9. Supported-agent capability negotiation

CLI command `agentfence agents doctor --agent codex` should print:

- binary found/version/digest;
- noninteractive invocation available;
- structured event format supported;
- config directories isolation verified;
- authentication mechanism; safe/unsafe/unknown flags;
- effective sandbox and tool policy;
- model connectivity availability and enforcement;
- supported scenarios with reason for unsupported ones;
- test instrumentation visibility.

Missing any **security-critical** capability yields `UNSUPPORTED`, not a degraded runner.

## 10. Crash, retries and process controls

- Use `asyncio.create_subprocess_exec()` (no shell) and capture stdout/stderr within byte caps.
- Supervisor owns process lifetime; kill process group/container on timeout, await teardown.
- Retry only infrastructure errors, never silently rerun scenarios without separate attempt ID.
- Preserve partial events and tool versions on failure.
- Refuse recursive invocation and self-invoking agent loops.
- Disable side-effecting auto-update, shell init scripts, MCP discovery, provider plugin auto-installation when possible.
- Record unresolved differences in provider event schemas and warn reviewers.

## 11. Adapter implementation gates

- [ ] Can run minimal task with local synthetic fixture inside sandbox.
- [ ] No real host directories, keys, sockets, tokens or sessions accessible.
- [ ] Egress policy verified against unrelated addresses and local network.
- [ ] Correctly reports model/API failure as infra error, not security pass.
- [ ] Structured event schema tested against pinned CLI version.
- [ ] Agent-visible fixture content does not expose scoring rubric.
- [ ] Unrelated system commands and writes are blocked or accurately observed.
- [ ] Good/unsafe/refusal profiles behave as expected for control cases.
- [ ] Documentation states which channels cannot be observed.
- [ ] Adapter works without requiring dangerous full-access mode.
