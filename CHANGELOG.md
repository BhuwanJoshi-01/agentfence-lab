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
- Developer tooling: ruff (lint + format), mypy `--strict`, pytest branch coverage,
  pre-commit hooks and pip-audit. (AF-0104)
- Credential-free CI on GitHub Actions: lint, unit tests on Python 3.12/3.13, wheel
  build with content check and clean-install smoke test, dependency audit. (AF-0104)
