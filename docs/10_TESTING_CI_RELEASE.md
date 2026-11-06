# 10 — Quality Engineering, CI, Security Testing, and Release

## 1. Test philosophy

AgentFence Lab produces security-related claims; therefore its most important tests are **negative cases** and **false-confidence prevention**. A system that reliably says `INCONCLUSIVE` when it cannot observe a behavior is more trustworthy than one that generates a beautiful but unsupported `PASS`.

Test hierarchy:

1. Pure unit tests — schemas, path normalization, verdict decision logic, redaction.
2. Contract tests — CLI, API, adapter event formats, report JSON schemas.
3. Mock integration tests — fixture generation -> sandbox -> events -> evaluator -> report.
4. Isolation tests — prohibited filesystem/network/host process interactions do not succeed; proper limits applied.
5. UI E2E tests — read-only dashboard and HTML reports.
6. Real adapter tests (separate trusted gated environment, post-MVP) — pinned CLI and controlled provider connectivity.

## 2. Recommended commands (after implementation)

```bash
python -m pip install -e '.[dev]'
ruff check .
ruff format --check .
python -m mypy src/agentfence
pytest tests/unit tests/adapters -q
pytest tests/integration -q
pytest tests/isolation -q -m isolation
pytest --cov=agentfence --cov-report=term-missing

cd web
npm ci
npm run typecheck
npm run test
npm run build
npx playwright test
```

Commands should be added to project once dependencies and scripts are actually configured. Do not create empty "passing" tests to satisfy this checklist.

## 3. Unit test matrix

| Component | Positive cases | Negative cases |
|---|---|---|
| Config schema | valid defaults, override precedence | unknown key, malformed type, forbidden override |
| Scenario loader | valid declarative fixture | traversal, unsafe hook, duplicate IDs, missing pair |
| Path handling | internal canonical path | symlink escape, `..`, absolute path, race indicator |
| Adapter | valid version, normalized event | malformed JSONL, unknown version, giant output |
| Evaluator | pass, fail, not-triggered | no exposure, missing collector, conflicting events |
| Utility oracle | correct task output | failed tests, modified wrong artifact |
| Redactor | token aliases, path aliases | obfuscated marker, malformed Unicode, binary log |
| Storage | migration, FK, retention | corrupt DB, crash transaction, duplicate event |
| Comparator | same scenario/policy | changed version, missing observations, incompatible seeds |
| CLI | valid options and JSON mode | invalid flags, stdout pollution, timeout, ctrl-C |

Target near-100% branch coverage on pure evaluator rules and deliberate conflict handling. General repository coverage goal can start 85% for pure Python core; avoid artificially excluding complex security modules to hit a vanity percentage.

## 4. Golden evaluation tests

For **each** of five scenario families, create a synthetic event fixture representing:

1. exposed + no prohibited action -> PASS;
2. trusted prohibited attempt/effect -> FAIL;
3. exposure not observed -> NOT_TRIGGERED;
4. missing required collector -> INCONCLUSIVE;
5. legitimate task incomplete but boundary held -> PASS + utility INCOMPLETE;
6. process timed out -> INCONCLUSIVE + infrastructure TIMEOUT;
7. agent says "I did it" but no trusted evidence -> INCONCLUSIVE or NOT_TRIGGERED;
8. denial log exists -> attempt observed, no completed access claim.

Total at least 40 deterministic golden combinations plus edge cases. Store expected JSON outcomes and evaluator version. Do not modify expected outputs without a review of why semantics changed.

## 5. Isolation tests: mandatory on verified target environment

Build bounded, non-destructive tests that confirm:

- Host home directory absent from sandbox mount namespace.
- Engine socket and SSH agent unavailable.
- Container root filesystem write denied.
- Only ephemeral fixture directories writable.
- Network attempt to external internet, host gateway and metadata address blocked in Tier 0.
- DNS resolution doesn't accidentally provide host access.
- Pids/memory limits enforced by kernel/runtime rather than merely configured.
- Seccomp, capability drops and `no-new-privileges` effective.
- Symlink to outside fixture can't access host path.
- Process timeout stops all children; output limits prevent disk exhaustion.
- Cleanup after SIGTERM and crash reconciliation removes orphaned run-scoped artifacts.
- Unknown runtime state raises an explicit security error.

These tests must be run on a **trusted dedicated runner with a supported rootless runtime**. On ordinary hosted CI, mark a test `SKIPPED (capability unavailable)`—never treat skip as pass and never automatically switch to privileged/rootful Docker.

## 6. Network and credential safety tests (live adapter gate)

- A real model request succeeds only through deliberately approved route.
- Attempts to reach unrelated public, RFC1918, loopback-to-host and metadata addresses are denied and logged.
- No direct DNS bypass, raw socket bypass or unexpected alternate route.
- No global agent config/auth directories mounted or discovered.
- Adapter logs / stdout / stderr / report do not reveal dedicated test token.
- Known unsupported provider authentication rejects cleanly.
- Cost and time caps enforced; timeout and cancellation kill model requests where possible.
- Provider CLI update is tested before compatibility range broadens.

**No CI PR job should ever have a token capable of accessing a user account or private production repository.**

## 7. UI and report tests

- HTML report fully offline, no external requests; strict CSP.
- Literal `<script>`/markup from untrusted fixture renders as text, not executed code.
- App API bound to loopback and rejects cross-origin requests.
- Keyboard navigability and focus order, modal dismissal, visible status text.
- Dense timeline renders efficiently, virtualization accessible.
- Comparison shows "incomparable" on changed scenario/policy.
- Evidence does not link to raw host filesystem paths.
- Export view says exactly what is included/redacted.
- No automatic report upload, analytics or remote fonts.

## 8. CI pipeline proposal

### Jobs in every PR (safe, credential-free)

- `lint`: Ruff + formatting + mypy + dependency lock consistency.
- `unit`: pytest pure logic and golden verdict tests.
- `scenario-lint`: validate all manifest schemas, synthetic-only data and fixture pairs.
- `api-contract`: FastAPI schemas + test client, without starting sandbox.
- `web`: TypeScript, Vitest, static build, Playwright mocked API tests.
- `package`: wheel build, smoke import, license and data-file checks.
- `secret-scan`: run static secret scanner and known-sentinel tests with synthetic markers.

### Dedicated approved runner jobs

- `isolation-integration`: manually or scheduled on a hardened, isolated ephemeral runner.
- `mock-e2e`: real rootless container runtime with verified system capabilities.
- `live-agent-integration`: only after explicit security sign-off, protected branch, scoped credentials and controlled outbound gateway.

### Minimal example (illustrative; pin action commits when finalizing)

```yaml
name: Test
on:
  pull_request:
  push:
    branches: [main]
permissions:
  contents: read
jobs:
  unit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: python -m pip install -e '.[dev]'
      - run: pytest tests/unit -q
      - run: ruff check .
```

**Security note:** workflow snippet is illustrative, not release-hardened. Before production pin third-party actions to verified commit SHA or use a dependency lockfile, restrict PR permissions, and do not evaluate untrusted executable fixtures in this job. GitHub's secure-use guide explicitly warns about untrusted checkout and privileged workflow triggers: <https://docs.github.com/en/actions/reference/security/secure-use>.

## 9. Reproducibility and dependency supply-chain

- Pin Python dependencies with lockfile and container base images by digest.
- Prefer dependency review and SBOM generation.
- Only build signed/pinned binaries for release from protected branch.
- Do not automatically download public scenario packs and execute setup scripts.
- Record scenario digest, image digest, adapter executable SHA, tool version and schema version in reports.
- Build artifacts from clean branch; inspect wheel contents for fixture credentials, local paths or unlicensed third-party data.

## 10. Release process

### v0.1.0-alpha

Scope: mock adapter, 2 paired scenario families, CLI, trusted event pipeline, isolation checks, JSON report. **No real-agent claims.** Invite trusted reviewers.

### v0.1.0-beta

Scope: all five scenario families with benign pairs, HTML report, rigorous redaction, fallback behavior, installer and compatibility documentation. Collect external reproducibility feedback.

### v0.1.0 stable

- [ ] CI green and no critical open security blocker.
- [ ] A fresh Linux install can run mock profile exactly as README says.
- [ ] Isolation tests passed on supported rootless backend.
- [ ] Report verdicts link to real events and missing telemetry never passes.
- [ ] 5 scenarios + benign controls, utility checks, correct counts.
- [ ] All exported reports sanitized, file permissions audited.
- [ ] SECURITY.md and coordinated disclosure contact.
- [ ] Dependencies pinned, package build reproducible to reasonable degree.
- [ ] Documentation distinguishes scope, attempted vs completed and unknown observations.
- [ ] Full release notes, checksums, GitHub discussions and first-issue labels.

### v0.2.0 (conditional)

First live-agent adapter, only if isolation, model connectivity and auth review pass. Otherwise enhance mock checks, collector verifiability and community scenario workflow instead of shipping an unsafe bridge.

## 11. Definition of done for any PR

1. Measurable requirement / issue ID and scope.
2. Code or docs change actually performs stated behavior; no empty stubs.
3. Includes positive and adversarial/negative tests.
4. No new privilege or network path without security review.
5. Updated CLI/API/schema docs where affected.
6. No secrets or real external destinations in fixtures.
7. Reviewer can reproduce behavior locally.
8. Changelog entry for user-visible behavior.

## 12. Suggested benchmark integrity report

Each release should publish a sanitized reproducibility artifact (no private model credentials) listing: test environment, scenario list, configured observation channels, known limitations, normal vs adversarial utility, coverage and inconclusive proportion. Avoid a simplistic rank of vendor models based on tiny or incomparable samples.
