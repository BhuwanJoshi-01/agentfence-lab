# Contributing to AgentFence Lab

Thanks for helping make AI coding-agent workflows easier to evaluate responsibly. AgentFence Lab values **reproducible evidence**, not dramatic demos.

## Before you start

Read [README](README.md), [Security policy](SECURITY.md), [Threat model](docs/03_SECURITY_SANDBOX.md) and [Scenario design](docs/04_SCENARIOS_AND_EVALUATION.md). Issues welcome for false positives, missing telemetry, compatibility problems, docs and accessibility.

## Development setup (after initial implementation)

```bash
python3.12 -m venv .venv            # 3.12 or newer
source .venv/bin/activate
python -m pip install -e '.[dev]'   # or, lockfile-pinned: uv sync --extra dev
pre-commit install                  # optional: run the lint hooks on commit

ruff check .
ruff format --check .
mypy                                # strict, over src/agentfence
pytest -m "not isolation" --cov
```

These are the same checks CI runs on every pull request (`.github/workflows/ci.yml`). Tests marked `isolation` need a verified rootless runtime on a trusted runner; they are skipped on hosted CI and on hosts without that capability, and a skip is never counted as a pass.

### Branches, commits and pull requests

- One roadmap task per pull request, on a branch named `feat/AF-XXXX-short-description` (or `fix/`, `docs/`, `ci/`).
- Small, self-contained commits using [Conventional Commits](https://www.conventionalcommits.org/) prefixes (`feat`, `fix`, `test`, `docs`, `build`, `ci`, `chore`, `refactor`).
- Reference the task issue in each commit (`Refs #N`) and close it from the PR (`Closes #N`).
- Add a `CHANGELOG.md` entry for any user-visible change.

## Contribution kinds

**Good first contributions:** documentation corrections, CLI/help improvements, test-fixture documentation, accessible UI fixes, schema validation tests, deterministic mock scenario controls.

**Requires explicit security review:** isolation backends, network gateways, credential handling, real agent adapters, access collectors, scenario payload design, arbitrary code execution, report exporting, CI workflows with privileges.

## Scenario PR template requirements

Include:

1. Scenario ID + semantic version, description and risk taxonomy.
2. Legitimate toy user task and deterministic success oracle.
3. Lower-trust input fixture and synthetic deviation only.
4. Matched benign counterpart, similar complexity.
5. Authorized and denied files/actions, observation channels needed.
6. At least 3 evaluator fixtures: PASS, FAIL, INCONCLUSIVE (plus NOT_TRIGGERED recommended).
7. Proof no actual secrets, live endpoints or destructive host commands.
8. Fixture hashes, rationale and known detection limitations.

**Prohibited contributions:** real credential harvesting, public exfil endpoints, targeting third-party live services, destructive/persistent host payloads, unreviewed download-and-execute scripts, exploits against real users, evasion of sandbox controls.

## Pull request checklist

- [ ] Narrow scope, linked issue/task ID.
- [ ] Code matches architecture contracts and zero-trust assumptions.
- [ ] Tests for normal behavior and unexpected/failing conditions.
- [ ] Docs/CLI examples updated.
- [ ] No real secrets, local usernames or private paths in fixtures/screenshots.
- [ ] New dependencies justified, locked and license-compatible.
- [ ] Any new privilege, socket, mount or egress path receives security approval.
- [ ] Existing mock suite still runs fully offline.

## Issue reports

Provide version, OS/distro, runtime, agent adapter/version (if any), scenario ID/version, sanitized run ID, exact CLI command without secrets, expected behavior, observed behavior, evidence IDs, and whether isolation preflight passed. Do not paste full unredacted traces or credential values.

## Code review standards

Review verdict correctness first, then isolation and privacy, then performance/UX. A change that makes more tests "pass" by dropping observation requirements is a regression. Maintainers may request synthetic reproduction for reports that affect a real vendor agent, and will not test unauthorized targets.

## Community behavior

Be respectful and specific. Critique evidence and reproducibility rather than vendors or individuals. Observe responsible disclosure for genuine vulnerabilities. See [SECURITY.md](SECURITY.md) for coordinated reporting.
