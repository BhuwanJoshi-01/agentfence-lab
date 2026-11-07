# ADR-014: Apache-2.0 for code and synthetic data

- **Status:** accepted
- **Date:** 2026-11-07
- **Task:** AF-0704
- **Related:** README ("Licensing" recommendation), ADR-009 (synthetic-only tests)

## Context

The blueprint recommended Apache-2.0 for code and asked for an explicit decision on test
corpora and third-party fixtures. The repository has shipped under Apache-2.0 since its
first commit, but the decision and the policy for data were never written down.

## Decision

1. **Code: Apache-2.0.** It includes an explicit patent grant, which matters for security
   tooling that organizations want to adopt, and it's compatible with the project's
   dependencies (typer, pydantic, PyYAML).
2. **Scenario data: also Apache-2.0.** Every fixture, marker and example is synthetic and
   written for the project (ADR-009), so it needs no separate license. Third-party text,
   code or datasets aren't accepted into fixtures. If that ever changes, the data gets its
   own license file and a separate decision.
3. **Inbound equals outbound.** Contributions are accepted under Apache-2.0 section 5. There's
   no contributor licence agreement, and authors keep their copyright.
4. **Machine-readable marking.** Every source, test, manifest and migration file starts with
   `SPDX-License-Identifier: Apache-2.0`, enforced by `tests/unit/test_licensing.py`.
   `NOTICE` ships in the wheel and sdist.

## Consequences

- Dependency additions need an Apache-compatible license (checked in review; SBOM work is
  AF-0706).
- Relicensing later would need consent from every contributor, which is unlikely to be
  feasible. Treat this decision as permanent.
