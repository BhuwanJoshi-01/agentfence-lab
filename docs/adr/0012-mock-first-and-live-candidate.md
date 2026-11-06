# ADR-012: Mock first, and the first live-agent candidate

- **Status:** proposed
- **Date:** 2026-11-06
- **Task:** AF-0004
- **Related:** ADR-001 (offline mock adapter before real agents), ADR-005 (deterministic
  evaluator), ADR-007 (dedicated auth design for live adapters), ADR-011 (initial supported
  environment, proposed in #81), docs/05, docs/09 sections 6 and 11, docs/12 section 4 Spikes C
  and D

## Context

ADR-001 in docs/12 section 2 already decided to ship the full pipeline against a deterministic
mock before any real model connectivity, and recorded why. This ADR does not repeat that
reasoning. It records what mock-first means for the code that exists now, and answers the second
half of AF-0004: which live agent to evaluate first, and what has to be true before that choice
is final.

State of `main` at `61a0d9b`:

- The evaluator (`core/evaluator.py`), event model (`core/events.py`) and coverage
  (`core/coverage.py`) take events as input and never see an agent. The 40-case golden matrix
  (`tests/unit/test_evaluator_golden.py`) tests them without any adapter. This is ADR-001 doing
  its job: verdict logic is checked before an agent exists.
- `AdapterCapabilities` exists as a strict model (`core/capabilities.py`). No adapter
  implements it. The mock adapter (AF-0308), the sandbox (M2) and the collectors (AF-0403) do not
  exist yet.
- No code launches any agent. `agent.kind` in `core/config.py` defaults to `mock` and accepts
  any slug, but nothing reads it.

docs/05 section 1 names Codex as a reasonable first candidate, OpenCode second, and Claude Code
after its runtime and permission interactions are verified. The README says the same. Those
statements come from vendor documentation (docs/12 research check, 2026-11-06), not from running
a pinned binary.

## Decision

1. **Mock first stands as decided in ADR-001.** No vendor adapter code is merged before the M8
   gate in point 5 passes. Spikes C and D (docs/12 section 4) are M8 research. They run in an
   isolated lab with a dedicated test account, never with the maintainer's personal agent login,
   and they produce notes and evidence, not shipped code.

2. **Every vendor CLI flag named here or in docs/05 is illustrative.** docs/12 says to recheck
   vendor CLI syntax and permission semantics against the pinned version installed in the
   environment. AF-0804 pins the version. Flags are re-verified against that version's `--help`
   and documentation when it is pinned, and again whenever the pin changes. If a flag does not
   behave as documented, stop that subfeature and record the blocker (AGENTS.md rule 11).

3. **What must be verified before choosing.** The table records what docs/05 says each CLI
   documents and what must be checked on a pinned version. Nothing in it has been verified by
   this project. Whichever CLI is chosen, its own events stay `agent_reported` and never prove an
   access (ADR-005, `SourceTrust.authoritative_for_access`). Verdicts come from trusted
   collectors.

| Must be verified on a pinned version | Codex | OpenCode | Claude Code | Task |
|---|---|---|---|---|
| Noninteractive mode that exits when the task ends | `codex exec` documented | `opencode run` documented | `claude -p` documented | AF-0804 |
| Structured event stream with a stable schema for that version | `--json` (JSONL) documented | `--format json` documented | `--output-format=stream-json` documented | AF-0804 |
| Explicit workspace directory | `--cd` documented | not covered in docs/05 | not covered in docs/05 | AF-0804 |
| Works without any full-access or auto-approve mode | `--sandbox` documents `read-only` and `workspace-write`; the full-access variant must never be used, and inner sandbox flags never replace outer isolation | avoid `--auto` if it widens approval scope | approval and permission settings not yet mapped | AF-0804, AF-0805 |
| Config and state isolated from the host | host config and auth dirs must not be mounted or discovered | must force isolated config and state dirs and never attach to a developer's shared background server or sessions | docs/05 flags home-directory config discovery and settings and MCP loading; `--bare` reduces config loading but does not prove isolation | AF-0801, AF-0803 |
| Dedicated, constrained credential with no host login | unknown | unknown; global provider and credential resolution must be overridden | unknown | AF-0801 |
| Model traffic only through the reviewed gateway or broker, with no DNS, raw-socket or alternate-route bypass | unknown | unknown | unknown | AF-0802, AF-0803 |
| Side-effecting extras can be turned off: auto-update, plugins, hooks, MCP discovery, background processes | unknown | plugins and the background server are documented concerns | plugins, hooks and MCP loading are documented concerns | AF-0803, AF-0806 |
| Per-run cost and rate caps | unknown | unknown | unknown | AF-0807 |
| Binary pinned by version and digest inside the image, nothing fetched at run time | unknown | unknown | unknown | AF-0804 |

4. **Provisional pick: Codex.** The reasons all come from documentation, not testing:
   - docs/05 and the README already name it first.
   - Its documented CLI covers noninteractive mode, JSONL events, an explicit working directory
     and named sandbox modes in one reference. That maps directly onto the adapter contract in
     docs/05 section 2.
   - OpenCode's shared background server and global credential resolution are isolation problems
     that must be solved before it can run at all. Claude Code has a larger configuration surface
     to verify: home-directory config, settings, MCP servers and hooks.

   Fewer documented concerns may only mean fewer known concerns, so this pick can change.
   OpenCode is the fallback if Codex fails a gate, and Claude Code follows either of them. If no
   candidate passes, live mode stays disabled and v0.1 ships as the mock runner (docs/09 section
   11 stop rule).

5. **The M8 gate.** The pick becomes final only after every M8 task passes for that candidate, in
   an environment that meets ADR-011. Until then no code path launches it.

| Task | The gate passes when |
|---|---|
| AF-0801 Identify safe auth options per agent; no host login state mounted | A per-vendor auth matrix exists, and the candidate has a dedicated, low-privilege, short-lived or revocable credential, or a broker design keeps the credential out of the guest. If only long-lived shared credentials exist, the candidate is unsupported (docs/03 section 3, ADR-007). |
| AF-0802 Design and prototype outbound provider gateway or host broker with explicit proof of confinement | A prototype in an isolated lab (Spike D) lets the approved provider route through and blocks every other route. |
| AF-0803 Perform adversarial egress, DNS, local-network and credential exposure tests | The docs/10 section 6 tests pass: unrelated public, RFC1918, host-loopback and metadata addresses are denied and logged; there is no DNS or raw-socket bypass; no global config or auth is discovered; the test token never appears in logs, stdout, stderr or reports. |
| AF-0804 Pin CLI version and normalize structured tool events | `compatibility/<agent>.json` records version, digest, verified flags, sanitized event examples, known missing observations and test date (docs/05 section 4). Adapter unit tests map the pinned event schema to internal events. |
| AF-0805 Run one toy benign task and one adversarial case on dedicated constrained credentials | Both run inside a verified sandbox and get verdicts from trusted collectors. A model or API failure is reported as an infrastructure error, never as a pass. |
| AF-0806 Document unobservable actions and mark affected cases inconclusive | Every channel the adapter cannot observe is listed, and affected scenarios come out `INCONCLUSIVE`. |
| AF-0807 Cost caps, model rate limits, no accidental recurring API use | Caps are enforced and tested. Nothing schedules or repeats model calls without an explicit run. |
| AF-0808 Independent security review; decide launch, defer or redesign | Someone other than the implementer reviews the adapter, and the decision is recorded as an update to this ADR. |

The gate also assumes M2 (AF-0201..AF-0208) has passed on a host supported under ADR-011, and the
mock adapter (AF-0308) runs the five MVP scenarios end to end.

## Consequences

- v0.1 tests the harness, not a commercial agent. Reports and the README must keep saying so
  (the ADR-001 tradeoff).
- Adapter code for Codex, OpenCode or Claude Code is out of scope until AF-0808 records a launch
  decision.
- When adapters are added, an unknown or unverified `agent.kind` must be refused as unsupported
  (exit code 6) rather than passed through. Today the config accepts any slug because nothing
  uses it.
- The comparison table is updated as M8 tasks complete. A cell moves from "documented" or
  "unknown" to "verified on version X" only with linked evidence.
- If the auth story fails for all three CLIs, the options are the host-side model broker of
  docs/03 section 3 (Tier 1, option B) or keeping live mode unsupported. A weaker sandbox is not
  an option.
