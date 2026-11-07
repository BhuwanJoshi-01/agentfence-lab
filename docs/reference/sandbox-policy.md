# Sandbox policy (Tier 0)

What a Tier 0 container launch must look like, and how the arguments are checked
(docs/03 section 4, AF-0203). The policy is `core/sandbox_policy.py` (`IsolationPolicy`).
The launch arguments come from `runtime/containers.py` (`build_run_argv`) and pass an
independent allowlist auditor (`audit_run_options`) before anything can use them.

**Status:** these are argument-level guarantees. Nothing in AgentFence starts a container
yet. Whether a running container really has each property is verified against a rootless
engine in AF-0205 (network) and AF-0206 (applied limits). The maintainer host runs a
rootful engine, so it cannot verify them (ADR-011).

## Controls

| docs/03 section 4 control | Policy | `run` option | Checked by |
|---|---|---|---|
| 1. Rootless runtime, verified from the server | not part of the launch; `doctor` refuses rootful engines | none | `runtime/engine_probe.py`, `test_engine_probe.py::test_rootful_engine_is_a_fail_with_reason` |
| 2. Non-root UID/GID | `uid`, `gid` (default 65532, 0 rejected) | `--user=UID:GID` | `test_sandbox_policy.py::test_no_weakening_validates` |
| 2. No `--privileged`, capabilities dropped | `privileged` locked false, `drop_all_capabilities` locked true | `--cap-drop=ALL`; no `--cap-add`, no `--privileged` | `test_sandbox_policy.py::test_auditor_refuses_every_weakening` |
| 2. No host PID, IPC, network or user namespace | no field for them | none emitted; `--pid`, `--ipc`, `--userns` are refused | `test_sandbox_policy.py::test_auditor_refuses_every_weakening` |
| 3. Read-only root, ephemeral size-limited `/tmp` and work dir | `read_only_root` locked true, `tmp_size_mb`, `workspace_volume` | `--read-only`, `--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=Nm`, `--mount=type=volume,...,target=/workspace`; Podman also `--read-only-tmpfs=false` | `test_sandbox_policy.py::test_every_mandatory_control_is_on_the_command_line` |
| 4, 5. No host sockets, home, devices or bind mounts | `host_mounts` locked false; the workspace is an engine-managed volume named `agentfence-...`, never a host path | no `-v`/`--volume`, no `type=bind`, no `--device` | `test_sandbox_policy.py::test_no_host_path_socket_or_privilege_appears_anywhere` |
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

## Open points for AF-0205 and AF-0206

- **Workspace volume.** The fixture is copied into an engine-managed volume, never
  bind-mounted. How the volume is populated, and how it becomes writable for the non-root
  user, has to be settled on a rootless engine.
- **Applied limits.** Rootless engines can ignore `--memory`, `--pids-limit` and `--cpus`
  without cgroup v2 controller delegation. `doctor` checks delegation, but only a running
  container shows whether the limits took effect (AF-0206).
- **Image.** The digest-pinned mock image is AF-0202. Until it exists, there is nothing to
  launch.
