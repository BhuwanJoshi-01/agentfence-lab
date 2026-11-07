# Releasing

Releases come from a version tag. `.github/workflows/release.yml` then:

1. builds the sdist and wheel once, checks that the tag matches `agentfence.__version__`,
   and runs the wheel's mock pipeline in a fresh virtual environment;
2. computes `SHA256SUMS`, a CycloneDX SBOM and the release notes (the version's
   `CHANGELOG.md` section);
3. creates the GitHub release with the built files, checksums and SBOM (a pre-release for
   `a`, `b` and `rc` versions);
4. publishes the **same** files to PyPI with trusted publishing. No API token exists
   anywhere.

## One-time PyPI setup (maintainer)

On <https://pypi.org/manage/account/publishing/>, add a **pending publisher** (or, once the
project exists, a trusted publisher under the project's settings):

| Field | Value |
|---|---|
| PyPI project name | `agentfence-lab` |
| Owner | `BhuwanJoshi-01` |
| Repository name | `agentfence-lab` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

GitHub creates the `pypi` environment the first time the workflow uses it. You can require
a manual approval on it under the repository's Settings, Environments.

## Cutting a release

1. Pick the version. docs/10 section 10 sets the gates: a beta (`0.1.0bN`) collects external
   reproducibility feedback, and `0.1.0` needs every P0 requirement. That includes running
   scenarios in an isolated workspace (#133).
2. In a pull request: set `__version__` in `src/agentfence/__init__.py`, regenerate the
   report golden (`AGENTFENCE_UPDATE_GOLDEN=1 pytest tests/unit/test_report.py`), move the
   `CHANGELOG.md` entries under `## [<version>] - <date>` with a Known issues section, and
   update the version mentions in `README.md`, `SECURITY.md` and
   `docs/reference/local-api.md`.
3. Merge it when CI is green, then tag the merge commit and push the tag:

   ```bash
   git tag -a v0.1.0b1 -m "AgentFence Lab 0.1.0b1"
   git push origin v0.1.0b1
   ```

4. Check the release page and <https://pypi.org/project/agentfence-lab/>. Verify a download:

   ```bash
   sha256sum -c SHA256SUMS
   ```

If the PyPI job fails because trusted publishing is not set up yet, set it up as above and
re-run only that job. The GitHub release is created independently.
