# Security Policy — AgentFence Lab

## Project status

AgentFence Lab is **pre-alpha**. The offline mock pipeline works end to end (`agentfence test`), but the rootless sandbox, live-agent adapters and the dashboard don't exist yet. The mock runs in-process and makes **no containment claim**. Every report says so. This project doesn't guarantee model or system safety.

## Supported versions

| Version | Supported | Notes |
|---|---|---|
| `main` (0.1.0.dev0) | Best effort | Pre-release. Fixes land on `main` only. |
| Released versions | None yet | A release is supported only after the isolation and regression gates in docs/10 pass. |

## Reporting vulnerabilities

**Do not publish exploit details in a public issue.** Report privately through GitHub's private vulnerability reporting:

> https://github.com/BhuwanJoshi-01/agentfence-lab/security/advisories/new

Include the affected commit or version, sanitized steps to reproduce, the affected trust boundary (docs/reference/trust-boundaries.md), evidence (run IDs, event IDs), and possible remediation if you have one. This is a volunteer project without a formal response-time commitment. Reports are acknowledged as soon as the maintainer can, and you'll be told if a fix will take a while.

Especially welcome: a verdict that can be produced without trusted evidence, a way for scenario content to escape the workspace or execute, ways to make an export pass verification after tampering, and redaction gaps.

Please do not include real keys, private data or third-party victim details. If you discover a vulnerability in a vendor agent, coordinate with that vendor through its published security disclosure channel.

## Responsible testing policy

AgentFence Lab is intended for **authorized defensive evaluation** of synthetic fixture repositories. Do not use it to probe third-party environments or access real credentials. Never direct a synthetic exfiltration case to a real internet recipient. Keep tests isolated and resource bounded; do not weaken controls to produce a result.

## Hard isolation guarantees — not claimed

Rootless containers reduce attack surface but cannot be treated as an absolute security guarantee against runtime/kernel flaws. A passed scenario does not certify that an agent is universally secure. Missing telemetry must yield `INCONCLUSIVE`, not an optimistic PASS.

## Confidentiality of reports

Reports should be redacted; even redacted logs may include names, model identifiers, paths and other metadata. Review before sharing. Never paste raw provider API logs or real credentials in GitHub issues. Automated logs and CI artifacts may outlive local files according to hosting provider retention policies.

## Dependency and provenance policy

All container image digests, agent versions and package dependencies should be pinned and audited. Use minimal permissions for CI and protect release tags. Changes to sandbox, collector, proxy or credential-handling code require a security-focused review.

## Release-blocking conditions

Do not ship a feature that silently runs with excessive privileges, unrestricted host network or arbitrary mounts; cannot distinguish requested vs applied limits; leaks credentials to agent process; exports real secret bytes; or presents unobserved effects as facts. A fail-closed refusal is preferable to insecure partial support.
