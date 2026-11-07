# ADR-011: Initial supported environment

- **Status:** proposed
- **Date:** 2026-11-06
- **Task:** AF-0003
- **Related:** ADR-004 (rootless OCI with strict preflight; docs/12 section 2), docs/12 section 3
  open questions 1 and 4, docs/12 Spike A, AF-0201, AF-0203, AF-0205, AF-0206, AF-0701

## Context

The README promises "Linux x86_64, rootless OCI runtime with verified cgroup controls" for the
MVP. It does not name a distro, a runtime or any versions, and docs/12 lists that as open
question 1. AF-0201 (runtime capability probe) needs a concrete first target.

Three environments matter today. None of them can prove rootless containment.

| Environment | What it has | What it can prove |
|---|---|---|
| CI (`.github/workflows/ci.yml`) | GitHub-hosted `ubuntu-latest`. The `unit` job runs Python 3.12 and 3.13; `lint`, `package` and `audit` run 3.12. The runner's Docker is rootful. | Pure logic, the CLI contract, packaging and the dependency audit. Tests marked `isolation` are deselected with `-m "not isolation"`. |
| Maintainer dev box | Ubuntu 26.04, x86_64, Linux 7.0 kernel, cgroup v2 with the systemd cgroup driver. Docker Engine 29.9.2, **rootful**: `docker info` lists the apparmor, seccomp and cgroupns security options and no rootless entry. Podman is not installed. `kernel.apparmor_restrict_unprivileged_userns = 1`; Ubuntu ships AppArmor profiles that grant `userns` to `/usr/bin/podman`, `crun`, `runc` and `buildah`. Python 3.13. | The same as CI. |
| Dedicated isolation runner | Does not exist yet. docs/10 section 5 requires one for isolation tests. | Nothing yet. |

On the dev box (2026-11-06, `main` after #80), `agentfence doctor` passes every implemented
required check, reports `runtime.rootless` as `UNKNOWN`, sets the isolation state to
`UNSUPPORTED` and exits 3. That outcome is correct, but the reason matters. `runtime.available`
passes because a `docker` binary is on `PATH`. `runtime.rootless` is `UNKNOWN` on every host
until AF-0201 exists. Doctor does not yet detect that this particular Docker is rootful.

## Decision

1. **Python.** Keep `requires-python = ">=3.12"`. CPython 3.12 and 3.13 are supported because
   they are the versions CI tests. 3.12 is the floor used for lint, type checks, packaging and
   the audit. A newer minor version counts as supported only after it is added to the CI matrix.
   This is already enforced by `pyproject.toml`, CI and the doctor check `python.version`.

2. **Platform.** Linux on x86_64 only, as doctor already enforces (`platform.os`,
   `platform.arch`). Other architectures and operating systems stay out of scope until a backend
   is verified on them.

3. **First backend to verify: rootless Podman.** AF-0201 builds and tests its probe against
   rootless Podman first. Rootless Docker is the second backend, compared against Podman in
   Spike A as docs/12 proposes. Reasons:
   - Podman runs rootless containers without a root daemon. Docker's rootless mode is a separate
     per-user daemon, and the same `docker` CLI can talk to the rootful or the rootless daemon
     depending on its context. docs/03 section 4 already says rootless must be verified from the
     server's capabilities, not the CLI context name. The dev box shows the risk: `docker` is on
     `PATH` and the daemon behind it is rootful.
   - Doctor's existing remedy text already recommends rootless Podman.
   - Neither backend removes the cgroup v2 delegation question (docs/03 section 4, docs/12
     question 4). Both need verified resource limits.

   This sets the order of verification. It is not a claim that Podman is safer in general. If
   Podman fails a required check on the candidate distro, record the failure in this ADR and
   evaluate rootless Docker. Do not relax the check.

4. **Rootful runtimes are never supported for sandbox runs.** That covers the rootful Docker on
   the dev box and on hosted CI, and rootful Podman. There is no fallback from rootless to
   rootful (docs/10 section 5). Pure-logic development continues on those hosts. No isolation
   result from them is claimed.

5. **Distro and runtime versions are TO BE CONFIRMED by Spike A** on a dedicated runner. This
   ADR does not pin version numbers it cannot verify.

| Item | Pin | Status |
|---|---|---|
| Python | `>=3.12`; tested on 3.12 and 3.13 | pinned, enforced |
| OS and architecture | Linux, x86_64 | pinned, enforced |
| cgroup | v2 unified hierarchy | pinned; doctor only checks that `/sys/fs/cgroup/cgroup.controllers` exists |
| Distro and release | TO BE CONFIRMED (Spike A) | candidate: Ubuntu 26.04 x86_64, to match the dev box |
| Second distro or release | TO BE CONFIRMED (Spike A) | docs/12 asks for two Linux versions |
| Kernel | TO BE CONFIRMED (Spike A) | record what the runner ships |
| Rootless Podman version | TO BE CONFIRMED (Spike A) | first backend |
| Rootless Docker version | TO BE CONFIRMED (Spike A) | second backend |
| Low-level OCI runtime (for example crun or runc) and version | TO BE CONFIRMED (Spike A) | record it; it affects cgroup handling |

6. **What doctor must verify before a pin counts.** A distro and runtime pair is supported only
   when every required check below is `PASS` on the dedicated runner and the result is stored as
   an `IsolationAttestation` with tool version, runtime version and timestamp (docs/03 section
   5). `UNKNOWN` never counts as `PASS`. `HostCapabilities.isolation_state` already works this
   way.

Read-only checks (`agentfence doctor`):

| Check | What it must establish | State on `main` today |
|---|---|---|
| `platform.os`, `platform.arch` | Linux on x86_64 | implemented |
| `python.version` | Python 3.12 or newer | implemented |
| `process.not_root` | AgentFence runs as an unprivileged user | implemented |
| `runtime.available` | a supported runtime binary is on `PATH` | implemented; presence only, says nothing about rootless |
| `cgroup.v2` | unified hierarchy mounted | implemented; marker file only, no delegation check |
| `runtime.rootless` | the runtime's engine reports rootless operation, read from server-side information and not the CLI context name | hard-coded `UNKNOWN`; AF-0201 |
| user namespaces allowed | the runtime can create user namespaces with the host's restrictions left on (on Ubuntu, `kernel.apparmor_restrict_unprivileged_userns` stays `1` and the runtime is covered by an AppArmor profile that grants `userns`) | not implemented; AF-0201 |
| user namespace mapping | UID 0 in the container maps to an unprivileged host UID | not implemented; AF-0201 |
| cgroup delegation | the controllers needed for memory, pids and CPU limits are delegated to the user's cgroup subtree | not implemented; AF-0201 |
| seccomp and `no-new-privileges` | the runtime supports and applies both | not implemented; AF-0201 |
| LSM | AppArmor or SELinux availability is recorded (docs/03 asks for a profile "if available") | not implemented; AF-0201 |

Self-test checks (`agentfence doctor --self-test`, one disposable mock-only container, docs/03
section 5). None of these are implemented:

| Check | Expected result | Task |
|---|---|---|
| applied resource limits | pids and memory caps take effect, shown with a bounded test rather than a stress test | AF-0206 |
| no egress | DNS, public internet, host loopback and metadata addresses are unreachable | AF-0205 |
| filesystem | root filesystem is read-only; host files outside the fixture are unreachable; writes outside the work dir and `/tmp` are denied | AF-0203 |
| mounts | no host home, engine socket, SSH agent or other host socket in the mount table | AF-0203 |
| privileges | capabilities dropped; seccomp and `no-new-privileges` effective inside the container | AF-0201, AF-0203 |
| identity | UID 0 inside the container is not host root | AF-0201 |

## Consequences

- No host is supported for sandbox runs yet, the dev box included. Doctor exits 3 everywhere
  until AF-0201 lands and the checks above pass. That is the intended fail-closed state.
- The README's "rootless OCI runtime with verified cgroup controls" stays a target until Spike A
  and AF-0201 are done.
- AF-0201 starts with Podman. When `auto-rootless` is implemented it should try Podman before
  Docker, which matches the order of `CONTAINER_RUNTIMES` in `runtime/host_probe.py`.
- Once AF-0201 exists, the dev box should report `runtime.rootless` as `FAIL` (rootful Docker)
  instead of `UNKNOWN`. A unit test should pin that case.
- Developing AF-0201 locally needs rootless Podman on the dev box. Installing it is a manual
  host change made by the maintainer. AgentFence never installs or configures a runtime.
- Ubuntu restricts unprivileged user namespaces through AppArmor, and rootless containers need
  them. The shipped profiles are tied to paths such as `/usr/bin/podman`, so a distro-packaged
  Podman should be allowed, while a Podman installed somewhere else would not match. Spike A must
  show rootless Podman working with the restriction left on. Turning the restriction off to make
  a runtime work weakens the host and is not acceptable (AGENTS.md guardrails 2 and 9).
- The dedicated runner must not run jobs triggered by pull requests from forks. docs/10 section
  8 lists isolation jobs as manual or scheduled only.
- After Spike A: fill in the version table with observed values and links to evidence, set the
  status to accepted, and use the pinned distro image for AF-0701 (clean install on a documented
  distro).
- Hosted CI never counts as a supported isolation host, whatever its runtime reports.

## Update 2026-11-07: rootless Podman on the dev box

The maintainer installed the distro-packaged Podman on the dev box (`apt install podman
uidmap passt crun fuse-overlayfs`). Observed with AppArmor's user-namespace restriction left
**on**: Ubuntu 26.04, kernel 7.0.0-42, Podman 5.7.0, crun 1.21, netavark with pasta, the
overlay storage driver, cgroup v2 with the systemd manager, and `cpu`, `memory` and `pids`
delegated. `podman info` reports `rootless: true` and seccomp enabled. `agentfence doctor`
now passes every required check (isolation state `OFFLINE_SAFE`).

This is one machine, not Spike A's dedicated runner, so the version table above stays
"to be confirmed". It does mean the container work in AF-0205, AF-0206 and AF-0208 can be
verified against a real rootless engine.

