# Sandbox policy (Tier 0)

What a Tier 0 container launch must look like, and how the arguments are checked
(docs/03 section 4, AF-0203). The policy is `core/sandbox_policy.py` (`IsolationPolicy`).
The launch arguments come from `runtime/containers.py` (`build_run_argv`) and pass an
independent allowlist auditor (`audit_run_options`) before anything can use them.

**Status:** the arguments are audited, and `agentfence doctor --self-test` checks the
result from inside a running container on rootless Podman. It records an attestation (see
[Self-test](#self-test)). On the maintainer host (rootless Podman 5.7.0, crun 1.21), all
16 checks pass. The mock agent itself still runs in-process: no scenario is executed in a
container yet.

## Image

`SANDBOX_IMAGE` in `core/sandbox_policy.py` pins the official `python:3.13-alpine`
(Python 3.13.18) by its OCI image-index digest, as of 2026-11-07:

```text
docker.io/library/python@sha256:1e1f6334529d1fb26a2ef619d7cffb30a7c7d7cc1cc7338ebee283a58d1aeaca
```

The image is never pulled during a run (`--pull=never`). `agentfence doctor` checks for it
with `podman image exists`, which is read-only, and when it is missing names the one-time
pull command (`podman pull` followed by the reference above).

No image is built locally. A local build's digest differs from machine to machine, so it
could not be pinned in code. AgentFence's own code reaches the container as arguments to
`python3 -I`, which keeps everything that runs inside fixed by the image digest plus the
installed package. To move to a newer base, change the digest in one commit after
re-running the isolation tests.

## Controls

| docs/03 section 4 control | Policy | `run` option | Checked by |
|---|---|---|---|
| 1. Rootless runtime, verified from the server | not part of the launch; `doctor` refuses rootful engines | none | `runtime/engine_probe.py`, `test_engine_probe.py::test_rootful_engine_is_a_fail_with_reason` |
| 2. Non-root UID/GID | `uid`, `gid` (default 65532, 0 rejected) | `--user=UID:GID` | `test_sandbox_policy.py::test_no_weakening_validates` |
| 2. No `--privileged`, capabilities dropped | `privileged` locked false, `drop_all_capabilities` locked true | `--cap-drop=ALL`; no `--cap-add`, no `--privileged` | `test_sandbox_policy.py::test_auditor_refuses_every_weakening` |
| 2. No host PID, IPC, network or user namespace | no field for them | none emitted; `--pid`, `--ipc`, `--userns` are refused | `test_sandbox_policy.py::test_auditor_refuses_every_weakening` |
| 3. Read-only root, ephemeral size-limited `/tmp` and work dir | `read_only_root` locked true, `tmp_size_mb`, `workspace_volume` | `--read-only`, `--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=Nm`, `--mount=type=volume,...,target=/workspace`; Podman also `--read-only-tmpfs=false` | `test_sandbox_policy.py::test_every_mandatory_control_is_on_the_command_line` |
| 4, 5. No host sockets, home, devices or bind mounts | `host_mounts` locked false; the workspace is an engine-managed volume named `agentfence-...`, never a host path | no `-v`/`--volume`, no `type=bind`, no `--device` | `test_sandbox_policy.py::test_no_host_path_socket_or_privilege_appears_anywhere` |
| Host identity stays out (found by AF-0208) | Podman only | `--no-hosts`: without it, Podman copies the host's `/etc/hosts` in (host name, private entries, host LAN address as `host.containers.internal`) | `test_sandbox_policy.py::test_podman_specific_defaults_are_turned_off`, `tests/isolation/test_sandbox_negative.py` |
| 6. Seccomp, `no-new-privileges` | `no_new_privileges` locked true; the engine's default seccomp profile is kept | `--security-opt=no-new-privileges`; every other `--security-opt` value is refused | `test_sandbox_policy.py::test_auditor_refuses_every_weakening` |
| 7. Memory, PID and CPU caps | `limits` (`ResourceLimits`) | `--memory`, `--memory-swap` (equal: no swap), `--pids-limit`, `--cpus` | `test_sandbox_policy.py::test_argv_shape_and_limits_follow_the_policy` |
| 8. No external network in Tier 0 | `network` is `"none"` only | `--network=none` | `test_sandbox_policy.py::test_no_weakening_validates` |
| 9. Pinned image, nothing fetched at run time | `image` must be `name@sha256:<64 hex>`; `pull` is `"never"` only | `--pull=never`, image by digest | `test_sandbox_policy.py::test_no_weakening_validates` |
| 10. Label artifacts by run | `labels` from `run_id`, `attempt_id` | `--label=io.agentfence.run=...`, `--label=io.agentfence.attempt=...` | `test_sandbox_policy.py::test_every_mandatory_control_is_on_the_command_line` |
| Explicit environment, no credentials (docs/03 section 11) | `env`: at most 32 plain names; credential, socket, engine, display, proxy and loader variables refused | `--env=NAME=value` only; no `--env-file`, no `--env-host`; Podman also `--http-proxy=false` | `test_sandbox_policy.py::test_credential_socket_and_loader_variables_are_refused` |

## The auditor

`audit_run_options` does not trust the builder. It accepts an option only if:

- the name is on a fixed allowlist, in `--name=value` form (`--rm` and `--read-only` take no value);
- the value is one the policy can produce (for example `--network=none`, `--cap-drop=ALL`,
  a non-root `--user`, a `type=volume` mount of an `agentfence-` volume at `/workspace`);
- every mandatory option is present, none is repeated except `--label` and `--env`, and
  `--memory-swap` equals `--memory`.

`build_run_argv` raises `PolicyViolationError` when the audit finds anything. A builder bug
therefore stops the launch rather than dropping a control. The test that proves this is
`test_sandbox_policy.py::test_a_builder_bug_becomes_a_refusal`.

## Labels and stale resources

Every container and workspace volume carries `io.agentfence.run` and
`io.agentfence.attempt` labels: `build_run_argv` adds them to `run`, and
`build_volume_create_argv` to `volume create`. A `finally` block cannot run after
`SIGKILL` or a crash, so `runtime/reconcile.py` finds leftovers by label (AF-0207):

- `list_labelled` asks the engine to filter by the run label, then checks every record
  again. The run label must be well formed, a container ID hexadecimal and a volume name
  `agentfence-` prefixed. Anything else is ignored, never removed.
- `plan_stale` keeps the active run's resources. `remove_stale` force-removes containers
  first, then volumes, through the process supervisor, and reports failures instead of
  raising.

The Docker listing commands were checked against Docker 29.9.2. Podman's output shape is
taken from its documentation and both shapes are accepted, until a rootless runner
confirms it. Nothing calls reconciliation yet: no container is created until the runner
exists (AF-0205), which is where it will run before each suite and from
`agentfence cleanup`. Tests: `test_reconcile.py::test_records_that_are_not_clearly_ours_are_ignored`,
`test_reconcile.py::test_the_active_run_is_never_stale_and_containers_go_first`,
`test_reconcile.py::test_a_forged_resource_is_never_passed_to_the_engine`.

## Self-test

`agentfence doctor --self-test` runs only when every required doctor check passes and the
pinned image is present. It never falls back to a weaker mode. It creates the labelled
workspace volume and starts one container with exactly the arguments above (`run_container`
in `runtime/sandbox.py`), under the process supervisor with a 120-second deadline. Every
container and volume labelled with that run is removed afterwards, whatever happened.

Inside, `runtime/probes/self_test.py` (standard library only) reports what it observes.
The host judges it (`judge`): a missing or malformed observation is `UNKNOWN`, never a
pass. Limits are checked as *applied* (cgroup files and a fork test), not as requested
(AF-0206).

| Check | Passes when |
|---|---|
| `process.non_root` | the process runs as the policy's UID (65532) |
| `userns.mapped` | the UID map is not the identity map, and neither the sandbox user nor container root maps to host root |
| `capabilities.none` | `CapEff` and `CapPrm` are zero |
| `no_new_privileges` | `NoNewPrivs` is 1 |
| `seccomp.filter` | `Seccomp` is 2 (filter mode) |
| `rootfs.read_only` | writing to `/etc` fails with `EROFS` |
| `tmp.size_limited` | `/tmp` is writable and no larger than the policy's size |
| `workspace.writable` | `/workspace` is writable by the sandbox user |
| `memory.limit` | `memory.max` equals the policy and `memory.swap.max` is 0 |
| `pids.limit` | `pids.max` equals the policy and forking is refused (`EAGAIN`) below it |
| `cpu.limit` | `cpu.max` matches the CPU quota |
| `network.none` | the only interface is `lo`; a public IP, the metadata address and the host gateway are unreachable |
| `dns.blocked` | name resolution fails |
| `hosts.private` | `/etc/hosts` holds only loopback entries and `host.containers.internal` does not resolve |
| `mounts.allowlisted` | every mount is engine-managed (`/dev`, `/proc`, `/sys`, `/etc/hosts`, ...), `/tmp` or `/workspace` |
| `sockets.absent` | no socket file is reachable, including the Docker, Podman, D-Bus and X11 paths |

The result is an `IsolationAttestation` (`core/attestation.py`): engine, image, policy and
the checks with expected and observed values. Its ID is the SHA-256 of its content, so an
edited file no longer verifies. It is saved to `<state>/attestations/<id>.json` (`0600`).
`doctor` exits 0 only when every check passes, 3 when one fails or the self-test could not
start, and 4 when the container could not be set up.

`tests/isolation/test_sandbox_negative.py` (AF-0208) attacks the same container from the
other side with 13 bounded attempts: host files, home, sockets, egress, host loopback, DNS
and host aliases, a fork bomb, memory over-allocation, privilege escalation, host
environment, host identity, and cleanup after a deadline and after a SIGKILLed client.

Tests: `test_sandbox_selftest.py::test_each_weakening_fails_its_check`,
`test_sandbox_selftest.py::test_missing_evidence_is_unknown_not_pass`,
`test_sandbox_selftest.py::test_cleanup_runs_even_when_the_run_raises`; with a real engine,
`tests/isolation/test_self_test_real.py`.

## Open points

- **Running scenarios in the container.** The mock's protected reads are observed by the
  trusted broker, which mediates every access. Inside a container the agent would read
  files directly, and nothing trusted would see the read. Container runs need a broker
  across the boundary (docs/12 Spike B) before they can produce verdicts. Until then they
  would only be `INCONCLUSIVE`.
