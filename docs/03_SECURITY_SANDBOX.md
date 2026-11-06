# 03 — Threat Model, Sandbox and Credential Safety

> **Release blocker:** do not invoke a real coding agent with untrusted fixtures until this document's isolation requirements are implemented and tested. A successful Docker/Podman command alone is not sufficient evidence of isolation.

## 1. Assets and adversaries

**Assets to protect:** host user files, SSH keys, cloud credentials, browser sessions, Docker/Podman sockets, other processes, user network and local services, CI tokens, personal data, integrity of test verdicts and report exports.

**Adversary controls:** untrusted fixture text/README, mock tool output, scenario-supplied content and any agent-generated commands resulting from reading that content. Public scenario contributions are also untrusted. We assume a malicious fixture may try to cause tool use beyond the legitimate task. We do **not** assume an adversary has root access to the host; container escape remains an explicitly residual risk.

**Trust hierarchy:** trusted controller policy > container backend enforcement > trusted collectors > adapter tool outputs > scenario text and model output. Natural-language descriptions do not cross a privilege boundary.

## 2. Attack surface map

| Surface | Example failure | Enforcement point |
|---|---|---|
| Untrusted text | Agent treats README note as instruction | policy + observed behavior assertions |
| Workspace paths | Agent reads synthetic protected path | broker/VFS audit, access controls |
| Tool execution | Agent executes unrelated command | sandbox policy and process observation |
| Network | Agent initiates unauthorized callback | network namespace/proxy deny log |
| Credentials | Agent accesses provider auth outside scope | credential provisioning boundary |
| Host mounts | Agent reads `$HOME` / `~/.ssh` | mount allowlist, refusal to launch |
| Local services | Agent reaches host port/socket | network and mount namespace isolation |
| Dashboard | XSS through prompt text or JSON | escaping, CSP, path validation |
| CI | Untrusted PR gets secrets or write token | event trigger and permissions rules |
| Scenario packages | Malicious "generator" executes on host | declarative manifests only; review gate |

## 3. Isolation modes — do not confuse them

### Tier 0 — Deterministic mock (MVP)

- No real agent binary, model API or provider auth.
- A deterministic script interprets scenario-defined actions; filesystem/network observations generated through the same trusted plumbing used by later adapters.
- Rootless container and `--network none`; synthetic fixture copied into ephemeral writable workspace.
- No host bind mounts besides strictly controlled, read-only fixtures where truly necessary; preferably copy content in.
- Demonstrates runner correctness, preflight, observation and scoring logic without an LLM.

### Tier 1 — Real agent with controlled model connectivity (post-MVP)

Real coding agents normally need HTTPS access to their model provider. `--network none` blocks this. Two practical architectures:

**A. Policy proxy / egress gateway:** the agent's network namespace can contact only a supervisor-controlled proxy; the proxy enforces approved provider endpoints and local mock sinks, rejects all other targets, logs denials, and cannot reach arbitrary host/local network addresses. A default deny outbound firewall must be confirmed with live network tests. Some CLIs use custom transport, web sockets, DNS or certificate pinning: validate compatibility explicitly. Don't claim complete observation if a CLI bypasses the proxy.

**B. Host-side model broker / tool-protocol adapter:** keep model API credentials in a trusted controller process, expose a narrowly authenticated IPC to the sandboxed agent runner, and relay only the specific model requests permitted by policy. This may require adapter-level integration or an SDK rather than a stock vendor CLI. **Do not claim stock CLIs can automatically use this approach.**

**Credentials tradeoff:** a stock CLI may require its own auth state or token inside the isolated runtime. Even a scoped, synthetic test workspace cannot make that token inaccessible to a compromised agent process. For Tier 1, require dedicated low-privilege short-lived credentials or a defensible broker design; no mounting `~/.config`, `~/.codex`, `~/.claude`, browser auth or SSH agent. If the provider only offers long-lived shared credentials, default to **unsupported** until a suitable architecture is reviewed.

### Tier 2 — Stronger VM isolation (future)

Consider disposable microVM/VM runner for untrusted binaries, heterogeneous platforms and greater defense in depth. Evaluate guest-agent communication, networking, filesystem shares, kernel boundaries, image signing and availability. Do not promise VM-level containment based on OCI alone.

## 4. Mandatory Linux runtime controls

**At minimum:**

1. Rootless OCI runtime; verify `rootless` in server capabilities, not just CLI context name.
2. Non-root container UID/GID, `--privileged` disabled, capabilities dropped, no host PID/IPC/network/user namespaces.
3. Read-only root filesystem; dedicated ephemeral writable work directory and `/tmp`, both size-limited when possible.
4. No host socket mounts (Docker/Podman socket, D-Bus, SSH agent, Wayland, X11, Kubeconfig).
5. No host home bind mounts, device passthrough, or host `/proc`/`/sys` writes.
6. Seccomp enabled, `no-new-privileges`, restrictive LSM profile if available; never turn off controls to make the agent run.
7. Timeouts, memory/pid/CPU caps. Check cgroup v2 and controller delegation; unsupported limits => fail preflight or explicitly downgrade to mock only.
8. No raw external network in Tier 0. For Tier 1, externally verified deny-by-default boundary and explicit model-provider route.
9. Immutable pinned image digest and minimal base image; no unchecked runtime `curl | sh`.
10. Label artifacts by run, verify cleanup and stale resources following crashes.

**Rootless caveat:** Docker docs state resource limit flags may be ignored without cgroup v2 + systemd delegation, and rootless mode has backend-dependent restrictions. Test actual applied resource constraints before claiming them. See [Docker rootless limitations](https://docs.docker.com/engine/security/rootless/troubleshoot/) and [resource limits](https://docs.docker.com/engine/security/rootless/tips/).

### Illustrative defense-in-depth flags (NOT a complete safe launch command)

```bash
# Example shape only; a verified backend must build and enforce the full policy.
# Never execute arbitrary agent workloads using a copy-pasted partial flag list.
--read-only --cap-drop=ALL --security-opt=no-new-privileges \
--network=none --pids-limit=128 --memory=1g \
--tmpfs=/tmp:rw,noexec,nosuid,size=64m
```

Requirements can conflict with particular software; respond with `UNSUPPORTED` rather than silently stripping defenses.

## 5. Isolation preflight

`agentfence doctor` should perform read-only checks; `agentfence doctor --self-test` may create a disposable **mock-only** test container and verify in practice:

- Can host files outside allowed fixture be reached? Expected denied.
- Are host machine-local addresses, metadata IPs and public internet reachable? Expected denied for Tier 0.
- Can processes fork past declared `pids` cap, write to root filesystem, or escape writable paths? Expected denied within safe test constraints.
- Is UID 0 mapped to host root? Expected no.
- Are container engine and user home absent from mount table? Expected absent.
- Are capability, seccomp and `no-new-privileges` effective? Expected verified.
- Is DNS blocked (Tier 0), no host network attached, and no accidental localhost-to-host routing? Expected blocked.
- Are runtime resource limits effective? Perform bounded tests against verified small allocations, not a denial-of-service stress test.

Store an `IsolationAttestation` with every run. Each check has status `PASS`, `FAIL`, or `UNKNOWN`, tool version, bounded evidence and timestamp. If any required check is unknown/fail, abort live execution.

## 6. Telemetry trust and completeness

Monitoring a workspace snapshot proves that content changed, but **cannot prove a file was read**. Network packet logs can prove a connection to a local sink, but may not prove payload semantics. A blocked access can still be a serious **attempt**, distinct from completed access.

Recommended mechanisms:

- **Trusted file access broker:** provide fixture-resource access through a controlled service which emits signed/audited read decisions. Useful to prove access to synthetic protected assets.
- **Network mock sink / proxy:** synthetic endpoint inside network boundary logs attempted requests, including blocked decisions, without contacting public internet.
- **Supervisor process events:** capture launched commands with argv sanitization and exit status; agent output is corroborating only.
- **Filesystem digests:** before/after allowlisted workspace files, plus explicit missing/symlink outcomes.
- **Optional Linux tracing:** `strace` or eBPF if properly supported and authorized, but kernel capability and container policy affect visibility. Not required for MVP; never make an unconditional claim that all file reads are captured.

For each assertion, map precisely the *minimum acceptable* telemetry to verdict logic. No requested telemetry? `INCONCLUSIVE`.

## 7. Safe scenario authoring rules

- Use **only synthetic keys** with unmistakable `AF_FAKE_...` prefix and random nonreusable markers.
- No genuine `.env`, SSH private key, login cookie, OAuth token, GitHub access token or cloud API credential.
- No live domains. Synthetic network targets must resolve solely to local mock services that cannot forward to internet.
- No remote shell, destructive host commands, persistence mechanisms or arbitrary attacker code downloads.
- Use checked-in declarative fixture files and deterministic templates. No executable scenario `setup.py`, make recipe, shell or post-install step without explicit security review.
- No scenario requiring elevated host privileges or access outside isolated workspace.
- Safe reproducibility: include a benign control for every adversarial case.
- Treat third-party scenario packs as untrusted until audited; never automatically fetch-and-run.

## 8. Threats to the controller itself

**Path traversal:** path fields validated against canonical workspace root; symlinks refused or handled via file-descriptor-relative operations. Revalidate at use time (TOCTOU). Use `openat2` policies where supported in optional hardened collector; Python `resolve()` alone is not a complete defense.

**Command injection:** subprocess invoked with argument arrays (`shell=False`), fixed binary allowlist and no raw untrusted option interpolation. Do not let scenario author decide arbitrary executable.

**HTML/Markdown injection:** treat report content as text, escape React/HTML, sanitize Markdown, strict CSP, disable remote scripts and links that execute `javascript:` URIs.

**Oversized log / zip bombs:** bounded event count, bytes, decompression ratio and streaming parsing. Store explicit `truncated=true`; never silently truncate evidence used in judgment.

**Local API abuse:** loopback binding, bearer/CSRF defenses and origin checks; read-only by default. No arbitrary path read endpoint.

**Tampering:** evaluator and broker run outside agent-writable filesystem; ephemeral file permissions hardened; evidence integrity checks before report.

## 9. CI threat model

- **Default v0.1 CI** runs mock tests only; **no AI model API keys** on repository PR jobs.
- Avoid `pull_request_target` workflows that check out untrusted PRs with credentials; avoid privileged Docker socket if possible.
- CI container runtime is often rootful even if test container is non-root; do not assume hosted runner meets rootless security. Separate isolation tests on a **dedicated trusted runner** and mark skipped checks honestly on normal CI.
- GitHub workflow permissions minimum (`contents: read`), no write tokens, no cloud creds. Sensitive integration tests gated behind reviewed/manual dispatch on protected branch with hardened ephemeral runners.
- Keep exported artifacts redacted. GitHub's own secure-use guidance notes secret masking is imperfect; treat all raw traces as potentially sensitive.

Reference: [GitHub Actions secure use](https://docs.github.com/en/actions/reference/security/secure-use).

## 10. Security and privacy UX

Display effective isolation state **before** the run: `OFFLINE_SAFE`, `CONTROLLED_EGRESS`, or `UNSUPPORTED`. Include warnings when a user selects any real-agent test. Never present "safe" as a blanket promise. Explain the trusted runtime boundary, credential behavior, and limits in plain language. All export/share actions default to redacted content.

## 11. Stop conditions (hard blockers)

Abort or refuse the run if:

- Rootless runtime cannot be verified; mandatory resource limits ignored; prohibited mount exists.
- Any direct host-home, engine socket, SSH agent, or cloud credential is in the guest environment.
- A real agent needs unrestricted outbound internet or host bearer tokens in v0.1.
- Network policy not verified to block arbitrary egress or local network access.
- Test fixture is nondeclarative or contains unsanitized actual secrets.
- Required access evidence is unavailable but the scenario would otherwise be incorrectly scored.
- An untrusted PR attempts to run credentialed tests.

## 12. Residual risks and honest limitations

A compromised container runtime or Linux kernel can invalidate confinement; host telemetry may be incomplete; provider/model upgrades may alter behaviors; container egress filtering is difficult for dynamic endpoints; ephemeral credentials can still be abused within their privilege. This design is a **defensive evaluation harness**, not a formal proof of safety. Publish limitations prominently and treat real-adapter enablement as a separate security gate.

## 13. Security test checklist

- [ ] Rootless verification with negative tests and log evidence.
- [ ] Denied home, cloud metadata and engine socket access.
- [ ] Denied arbitrary DNS/network access in mock mode.
- [ ] Enforced pids/memory/time limits verified.
- [ ] No provider credential in mock fixtures, logs or container.
- [ ] Stable collection under abnormal exits and timeouts.
- [ ] Read operations not claimed from write-only evidence.
- [ ] Input path traversal and symlink tests.
- [ ] HTML report XSS payload tests.
- [ ] CI untrusted-PR secret isolation verified.
- [ ] Cleanup succeeds after interrupt and abandoned-run reconciliation.
