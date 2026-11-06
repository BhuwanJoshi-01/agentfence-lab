# Security Policy — AgentFence Lab

## Project status

AgentFence Lab is currently represented by a **development blueprint**, not a deployed security tool. Any future code releases must state supported versions/platforms and their known limits. This project does not guarantee model or system safety.

## Supported versions

No code versions released yet. Once published, maintain a version support matrix in this file. Only designate a release supported after the isolation and regression test gates pass.

## Reporting vulnerabilities

**Do not publish exploit details in a public issue before maintainers have a chance to assess impact.** Use GitHub private vulnerability reporting for the eventual repository, if enabled, or a security contact published by repository maintainers. No email address is provided here because none has been designated. Include affected commit/release, sanitized steps to reproduce, affected boundary, evidence, and possible remediation.

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
