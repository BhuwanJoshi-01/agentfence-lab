# Changelog

All notable user-visible changes are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/) once releases begin.

## [Unreleased]

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

### Fixed

- Untrusted manifest titles, task descriptions and pack file names can no longer inject
  terminal escape sequences or bidi/zero-width characters into CLI output: the schema
  rejects them and human output is scrubbed. (#86)
- Event details now reject any host path (absolute POSIX, `~`, Windows drive or UNC,
  `file:` URIs, embedded host directories), not only home-directory prefixes. (#87)

- Group-writable config files are accepted when the group is the user's private group
  (Debian/Ubuntu `umask 0002`); shared-group and world-writable files are still
  refused. (AF-0102)
