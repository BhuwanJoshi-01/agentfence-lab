# Changelog

All notable user-visible changes are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/); pre-releases use PEP 440 (`0.1.0b1`).

## [Unreleased]

### Added

- `runtime/read_watch.py`: an unprivileged host-side watch (fanotify) that observes reads of
  chosen files, including reads from inside a strict-policy rootless container. This is the
  building block for running scenarios in the sandbox (#133). Its limits (no process IDs,
  completed opens only) are documented and tested. See
  `docs/research/0003-in-container-evidence.md`.
- `run_container(..., volume_ready=True)` for callers that prepare the workspace volume first.
- Project website (`website/`, deployed to GitHub Pages by `.github/workflows/pages.yml`):
  one static page with no scripts or third-party requests and a restrictive CSP, using the
  real screenshots and demo. Also a 1280x640 social card (`website/social-card.png`,
  rendered from `website/card/`).

### Changed

- The README demo GIF was re-recorded on 0.1.0b1.
- Package metadata points `Homepage` at the website and adds `Source` and `Changelog`.

## [0.1.0b1] - 2026-11-07

First public pre-release: the **beta** stage of docs/10 section 10. It covers all five
scenario families with benign controls, the HTML report, redaction, fail-closed behaviour,
installer and compatibility documentation, and a verified rootless sandbox self-test. It
ships the **mock agent only**. The purpose of a beta is to collect external
reproducibility feedback before `0.1.0`. See Known issues.

### Added

- Python package `agentfence-lab` (import name `agentfence`) with an `agentfence`
  console script and `python -m agentfence` entry point. (AF-0101)
- `agentfence version [--json]`: read-only version report; JSON mode writes only
  JSON to stdout. (AF-0101)
- Apache-2.0 license.
- Frozen outcome vocabulary (`agentfence.core.vocabulary`): boundary, utility and
  infrastructure axes, event taxonomy, source trust, exit codes with documented
  precedence, and per-artifact schema versions. See ADR-010. (AF-0006)
- Run lifecycle state machine with auditable, timestamped transitions. (AF-0006)
- Observed event model with source-trust provenance, alias-only targets,
  content-addressed IDs that detect tampering, stream consistency validation and a
  collector-side `EventRecorder`. (AF-0401)
- Deterministic evaluator v1.0.0: boundary, utility and infrastructure axes from
  trusted evidence only, attempts kept separate from completed effects, and a 40-case
  golden matrix. See `docs/reference/evaluator.md`. (AF-0404)
- Collector coverage reports: per-channel `COMPLETE`/`TRUNCATED`/`ERROR`/`MISSING`
  derived only from trusted `collector.status` events, plus a bounded event stream
  that records truncation explicitly. (AF-0402)
- Strict `agentfence.yaml` model with layered precedence (defaults < user < project <
  CLI) and per-key provenance. Security invariants are locked and unknown keys are
  rejected. See `examples/agentfence.yaml`. (AF-0103)
- Strict declarative scenario manifest schema with pairing rules and no executable
  fields. (AF-0103)
- Adapter and host capability models; isolation state is derived fail-closed. (AF-0103)
- XDG-aware state and config locations (`AGENTFENCE_STATE_DIR`, `XDG_STATE_HOME`,
  `XDG_CONFIG_HOME`) with `0700` directories, refusal of loose, symlinked or
  foreign-owned paths, and config discovery (`--config` > `AGENTFENCE_CONFIG` >
  `./agentfence.yaml`, no upward search). (AF-0105)
- Atomic `0600` file writes with fsync and temp-file cleanup on failure. (AF-0105)
- `agentfence doctor [--json] [--config PATH]`: read-only host prerequisite checks and
  config validation. Exits 0 only when every required check passes, 3 otherwise, 2 on
  invalid configuration. (AF-0102)
- `agentfence scenarios list [--profile] [--json]`: lists validated bundled manifests;
  an invalid bundle fails the listing. (AF-0102)
- CLI contract tests: `--help` snapshots for every command, the exact command tree,
  exit-code documentation, stdout/stderr separation under verbose logging, and
  locale/timezone independence. (AF-0107)
- `agentfence runs list|show` and `agentfence report <run-id> [--format json|text]
  [--output FILE]`: read saved runs only after verifying the report hash, the evidence
  hash chain and every cited evidence ID, with a reproduce command, scenario versions
  and fixture digests. Exports never overwrite files. (AF-0506)
- `agentfence test [--agent mock] [--agent-profile P] [--profile] [--cases] [--repeat]
  [--seed] [--strict] [--json]`: runs the offline mock, saves `report.json` and the run,
  prints a plain-text summary with limitations and evidence counts, and exits with the
  documented codes. (AF-0502)
- Local SQLite run store (standard-library `sqlite3`, ADR-013): versioned migrations,
  `0600` database file, foreign keys, append-only events enforced by triggers, a
  per-attempt hash chain with `verify_run()`, and atomic whole-run saves. (AF-0406)
- Schema-versioned JSON suite report (`agentfence/schemas/report.v1.schema.json`)
  with per-attempt verdicts, evidence and alias-only events, plus suite metrics as
  ratios with denominators and Wilson 95% intervals instead of a single score. A suite
  runner executes scenarios × repetitions. (AF-0501)
- Deterministic offline mock adapter with the six docs/05 profiles (safe, unsafe,
  refusal, no-exposure, no-telemetry, timeout) and an end-to-end attempt runner:
  fixture, private workspace, trusted collectors, utility oracle, evaluator, lifecycle
  and guaranteed cleanup. Results are labeled `mock-inprocess` and make no sandbox
  claim. (AF-0308)
- Utility oracles and runner: the legitimate task is graded statically (never by
  executing workspace code) and recorded as trusted `test.utility_result` events. The
  calculator oracle rejects faked fixes such as editing the source or deleting tests.
  Packs must name an existing oracle. (AF-0405)
- Trusted Tier 0 collectors: a file-access broker that denies and logs protected reads
  and cannot be redirected by symlinks, before/after workspace digests as
  `file.changed` events, and a socket-free mock network with deny and lab-sink modes.
  (AF-0403)
- End-to-end golden outcomes for all 60 bundled scenario × mock profile combinations,
  plus a pinned partial-evidence case. (AF-0408)
- Bounded process supervisor (`agentfence.runtime.supervisor`): argv with an absolute
  executable and no shell, an explicit environment, its own process group, `SIGTERM` then
  `SIGKILL` at the deadline, leftover group members killed, capped stdout/stderr that never
  block the child, bounded teardown and a recursion guard. `doctor`'s engine queries now run
  under it. Processes that leave the group with `setsid` are a documented limit until
  containers land. (AF-0204)
- Strict Tier 0 sandbox policy (`IsolationPolicy`) and audited `podman`/`docker run`
  arguments: non-root user, read-only root, all capabilities dropped, no-new-privileges,
  no network, digest-pinned image never pulled, size-limited `/tmp`, an engine-managed
  workspace volume instead of host mounts, resource caps and run labels. An allowlist
  auditor refuses any other option. Argument-level only: no container is started yet.
  See `docs/reference/sandbox-policy.md`. (AF-0203)
- Thirteen sandbox negative tests against a real rootless Podman (`tests/isolation`): host
  files, home, sockets, egress, host loopback, DNS and host aliases, fork bomb, memory
  over-allocation, privilege escalation, host environment, host identity, and cleanup after
  a deadline and after a SIGKILLed client. Run with `pytest -m isolation`. (AF-0208)
- `agentfence doctor --self-test`: on a host whose required checks pass, starts one
  disposable network-less container under the strict policy on rootless Podman, checks 16
  isolation properties from inside it (user, user namespace, capabilities,
  no-new-privileges, seccomp, read-only root, sized `/tmp`, workspace, applied memory/PID/CPU
  limits, no network, no DNS, private `/etc/hosts`, allowlisted mounts, no sockets) and
  saves a content-addressed
  `IsolationAttestation`. Every labelled container and volume is removed afterwards.
  (AF-0205, AF-0206)
- `doctor`'s help no longer claims it starts no processes: it runs the engines' read-only
  `info` and `image exists` queries.
- Pinned sandbox image: official `python:3.13-alpine` by OCI image-index digest, never
  pulled at run time. `doctor` reports whether rootless Podman has it (optional
  `sandbox.image` check) and names the one-time `podman pull` command. (AF-0202)
- A 35-second demo GIF in the README, recorded from real mock runs and the real dashboard
  by a Playwright spec that anyone can re-run (`dashboard/e2e/demo.spec.ts`). (AF-0702)
- Python 3.14 is supported and tested in CI, alongside 3.12 and 3.13.
- `scripts/clean-install-check.sh` and `docs/reference/install.md`: the wheel installs
  cleanly and the README quickstart passes as an ordinary user on fresh Ubuntu 26.04.1,
  Debian 13 and Fedora 43 containers, pinned by digest. (AF-0701)
- Read-only dashboard served by `agentfence ui` (React, TypeScript, Vite; prebuilt in the
  package, no Node needed to use it). It shows runs with separate outcome counters, run
  detail with a filterable scenario matrix, attempt timelines with trust tags, run
  comparison, the scenario catalog, and environment and self-test checks. The session
  token travels in the URL fragment. The page runs under a strict CSP, and Playwright and
  axe (WCAG 2.2 AA) test it on desktop and mobile. New API routes: `/environment`, and
  `/scenarios` gains the catalog fields. (AF-0602 to AF-0607)
- `agentfence ui`: read-only local API on a loopback address (optional `ui` extra:
  FastAPI and uvicorn). Requests must name the loopback host, come from the same origin and
  carry the printed session token; only `GET` routes exist; run data is served from the
  verified report after the secret scan. See `docs/reference/local-api.md` and ADR-015.
  (AF-0601)
- Stale container and volume reconciliation by run label (`runtime/reconcile.py`):
  workspace volumes are created with the run labels, leftovers from runs that never
  finished are listed, checked again client-side and force-removed (containers first).
  `agentfence cleanup` lists and (with `--apply`) removes them on rootless Podman, sparing
  any run with a resource created in the last ten minutes.
  (AF-0207)
- Preflight secret scan: `report` and `runs show` check the exact output for synthetic
  markers, private keys and credential shapes before printing or writing it, and refuse
  (exit 4) with the kind and line of each finding, never the value. (#119)
- `agentfence cleanup`: retention based on `storage.retention_days` (or `--older-than`).
  Dry run by default; `--apply` needs confirmation or `--yes`. Expired runs are purged from
  the database in one transaction with the append-only trigger restored, then their folders
  and any settled orphan folders are removed. See `docs/reference/data-retention.md`.
  (AF-0505)
- Supply chain: CycloneDX SBOM built in CI and kept with the wheel, a runtime dependency
  license check, and `docs/reference/supply-chain.md`. (AF-0706)
- Community files: Contributor Covenant code of conduct, an updated SECURITY.md with
  private vulnerability reporting (enabled), and structured issue forms. (AF-0705)
- Licensing: ADR-014 (Apache-2.0 for code and synthetic data), `NOTICE`, SPDX headers on
  every source file, and a contribution copyright policy. (AF-0704)
- `agentfence doctor` now verifies the runtime: rootless mode from each engine's own
  report (a rootful daemon is a FAIL), seccomp, cgroup controller delegation to the
  user, user namespaces, and AppArmor's user-namespace restriction (informational).
  Engine queries are bounded, read-only subprocesses with a minimal environment.
  (AF-0201, #85)
- Ctrl-C during `agentfence test` exits 130 with nothing saved, and the next run removes
  workspaces left behind by a killed run (symlink-safe, UUID-named entries only).
  (AF-0407)
- `agentfence test --baseline <run-id>`: compares the new run with a verified baseline
  and exits 1 on any regression. (AF-0504)
- `agentfence runs compare <baseline> <candidate> [--json]`: per-scenario REGRESSED /
  IMPROVED / CHANGED / UNCHANGED, and INCOMPARABLE with reasons when scenario version,
  evaluator, fixture, isolation tier or agent differ. Exits 1 on regressions. (AF-0504)
- `agentfence report <run-id> --format html`: a single offline HTML file with a strict
  Content-Security-Policy (no scripts, no remote resources), escaped content, inert
  embedded JSON and accessible tables. (AF-0503)
- Fixture integrity: pinned digests for every generator, variant and several seeds;
  a generator change without a version bump fails CI; generation is identical across
  processes, hash seeds, locales and timezones. New `scenario-lint` CI job. (AF-0309)
- Fifth bundled scenario pair: `AF-ARTIFACT-001` (output artifact contamination) and
  `AF-ARTIFACT-001-B`, completing the five MVP families. The snapshot collector records
  whether a seeded marker reached a watched output file, never the content. (AF-0307)
- Fourth bundled scenario pair: `AF-TOOL-001` (tool-output authority spoofing) and
  `AF-TOOL-001-B`. A spoofed `[SYSTEM NOTICE]` in captured test output must not grant
  permission for an unrelated edit. (AF-0306)
- Evaluator 1.1.0: a FAIL caused by a mock-sink delivery states that nothing left the
  lab and that it is not exfiltration. Outcomes are unchanged.
- Third bundled scenario pair: `AF-NET-001` (unauthorized network intent) and
  `AF-NET-001-B`. The only "network" is an in-memory lab sink; the requested host is a
  reserved `.invalid` name. (AF-0305)
- Second bundled scenario pair: `AF-CRED-001` (synthetic protected-file scope) and
  `AF-CRED-001-B`. A denied read of the fake token is a FAIL reported as an attempt, not
  a completed read. (AF-0304)
- First bundled scenario pair: `AF-README-001` (lower-trust README instruction
  confusion) and its benign control `AF-README-001-B`. `agentfence scenarios list` now
  shows them. Packs that name an unregistered fixture generator are rejected. (AF-0303)
- Deterministic fixture generation: seeded, HMAC-derived markers, byte-identical output
  per seed, a generator registry, the `calculator_v1` toy project for AF-README-001, and
  safe materialization into a fresh private workspace (`O_EXCL|O_NOFOLLOW`, digest
  check). (AF-0302)
- `agentfence scenarios validate <path> [--json]`: validates a manifest or a pack,
  including benign-pair rules, and reports every problem at once. (AF-0301)
- Scenario pack loader: non-following, non-blocking reads with a size cap, manifest
  SHA-256 provenance, unique IDs, and like-for-like benign pairs. The bundled catalog
  uses the same checks. (AF-0301)
- Structured logging to stderr only, level from `AGENTFENCE_LOG_LEVEL`, allowlisted
  fields, a 1 MiB per-process cap with a single truncation notice, and no tracebacks
  in logs. (AF-0106)
- Text scrubbing for synthetic markers, common credential shapes, home paths and
  terminal control sequences. (AF-0106)
- Hardened YAML loader: rejects duplicate keys, anchors/aliases, non-string keys,
  multi-document streams and oversized input. (AF-0103)
- Developer tooling: ruff (lint + format), mypy `--strict`, pytest branch coverage,
  pre-commit hooks and pip-audit. (AF-0104)
- Credential-free CI on GitHub Actions: lint, unit tests on Python 3.12/3.13, wheel
  build with content check and clean-install smoke test, dependency audit. (AF-0104)

### Changed

- README rewritten for what exists today: a quickstart checked from a fresh clone, real
  output from a failing mock run, a screenshot of the HTML report, how verdicts work,
  what AgentFence touches, its limitations, and related projects in their own words. A
  test keeps every `agentfence` command shown in the README real. (AF-0703)

### Fixed

- The local API could answer with a 500 or drop the connection under parallel requests: a
  yield dependency closed the SQLite connection on a different thread from the one that
  opened it. Each endpoint now opens and closes the store itself. Found by the dashboard's
  end-to-end tests. (#126)
- Sandbox containers on Podman no longer see the host's `/etc/hosts`. Podman copied it in by
  default, exposing the host name, private host entries and the host's LAN address as
  `host.containers.internal`. The policy now passes `--no-hosts`, the auditor requires it,
  and the self-test checks it (`hosts.private`). Found by the AF-0208 negative tests.
  (AF-0208)
- `scenarios validate` (and the bundled catalog) now rejects adversarial scenarios that
  disagree with their generated fixture: a missing attack surface, a forbidden path the
  boundary allows or does not deny, a failure rule the adversarial surface never mentions,
  or a benign surface that carries the deviation. Before, such a pack validated and could
  never trigger. (#118)

- Event trust is no longer taken at face value: a collector registry caps the trust each
  component may claim, and unregistered components cannot prove anything (evaluator
  1.2.0). Verdict outcomes for the bundled scenarios are unchanged. (#88)

- An exception inside the agent adapter now ends the attempt as INCONCLUSIVE with
  infrastructure ERROR instead of crashing `agentfence test`. (AF-0307)

- Attempt IDs now include the run ID, so repeated runs with identical settings no
  longer collide. (AF-0406)

- Untrusted manifest titles, task descriptions and pack file names can no longer inject
  terminal escape sequences or bidi/zero-width characters into CLI output: the schema
  rejects them and human output is scrubbed. (#86)
- Event details now reject any host path (absolute POSIX, `~`, Windows drive or UNC,
  `file:` URIs, embedded host directories), not only home-directory prefixes. (#87)

- Group-writable config files are accepted when the group is the user's private group
  (Debian/Ubuntu `umask 0002`); shared-group and world-writable files are still
  refused. (AF-0102)

### Known issues

- **Scenarios run in-process, not in the container.** The rootless sandbox is verified
  (`doctor --self-test`, 16 checks), but a container cannot yet produce the trusted
  read evidence the broker provides, so scenario runs stay in-process. Until that works,
  FR-005's "strictly isolated workspace for every run" is not met. (#133)
- **No real agents.** Codex, Claude Code and OpenCode adapters wait for the M8 security
  gate and an independent review (AF-0801 to AF-0808). Every result is from the mock.
- **Isolation verified on one machine.** The self-test and the 13 negative tests pass on
  the maintainer's Ubuntu 26.04 with rootless Podman 5.7.0. A dedicated isolation runner
  (ADR-011, Spike A) does not exist yet, and hosted CI cannot run these tests.
- **Accessibility:** axe (WCAG 2.2 AA plus best practices) is clean on every dashboard page
  and keyboard use is tested, but no manual screen-reader testing has been done yet.
- **Not built:** the dashboard's agent-compatibility screen (there are no adapters yet),
  and injection channels beyond workspace files (#129).
- **External feedback** (AF-0007 interviews, AF-0708 testers) has not been collected yet.
  That is what this beta is for.
- **Name:** other projects in this space use "AgentFence" (PyPI `agentfence`). This
  project is `agentfence-lab` (#127).

