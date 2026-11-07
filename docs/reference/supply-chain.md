# Supply chain

How AgentFence Lab keeps its own dependencies reproducible and reviewable (docs/10 section 9,
AF-0706).

| Control | Where | Enforced by |
|---|---|---|
| Locked dependency set | `uv.lock` | CI `lint` job runs `uv lock --check`; every job installs with `UV_FROZEN=1` |
| Known-vulnerability audit | runtime dependencies exported from the lockfile | CI `audit` job: `pip-audit --strict` |
| Software bill of materials | CycloneDX 1.5 from the lockfile, runtime dependencies only | CI `package` job: `uv export --no-dev --format cyclonedx1.5`, uploaded with the wheel as the `wheel-and-sbom` artifact (14 days) |
| License compatibility | every runtime dependency must be Apache-2.0-compatible (ADR-014) | `tests/unit/test_dependency_licenses.py` |
| Pinned CI actions | every third-party action pinned to a full commit SHA, tag in a comment | review |
| Wheel contents | no tests, VCS data, local state or databases; scenarios and `NOTICE` present | CI `package` job |

## Runtime dependencies (as of AF-0706)

`typer`, `rich`, `pydantic`, `pydantic-core`, `pyyaml` and their dependencies (`annotated-types`,
`annotated-doc`, `typing-extensions`, `typing-inspection`, `markdown-it-py`, `mdurl`,
`pygments`, `shellingham`). They're all under MIT, BSD-2-Clause, ISC or PSF-2.0. The standard
library provides SQLite (ADR-013).

## Generating the SBOM locally

```bash
uv export --frozen --no-dev --format cyclonedx1.5 -o sbom.cdx.json
```

## Not covered yet

There are no container images. The pinned minimal mock image and its digest come with
AF-0202, along with signed release artifacts and checksums (AF-0707).
