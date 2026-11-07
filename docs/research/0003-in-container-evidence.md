# Trusted read evidence from inside the sandbox (#133, docs/12 Spike B)

- **Checked:** 2026-11-07, Ubuntu 26.04, kernel 7.0.0-42, rootless Podman 5.7.0, crun 1.21
- **Question:** scenarios run with the mock in-process because the trusted broker, which
  proves reads, mediates every file access there. In a container the agent opens files
  directly. Can the host still observe reads of chosen files *without* weakening the
  strict policy (no sockets, devices, extra capabilities or network)?
- **Answer:** yes, for completed reads of chosen files, using unprivileged fanotify. Built
  as `runtime/read_watch.py`.

## What was tested

| Approach | Result |
|---|---|
| Unprivileged fanotify (`FAN_REPORT_FID`, since Linux 5.13) watching a file in the attempt's Podman volume from the host | **Works.** A strict-policy container reading the watched file produced an open and a read event; reading an unwatched file in the same volume produced nothing. `tests/isolation/test_read_watch_real.py` |
| FUSE-based broker inside the container | Rejected: needs `/dev/fuse` and capabilities that the policy forbids. |
| A broker reachable over a mounted Unix socket | Rejected: the policy forbids socket mounts. |
| eBPF or privileged fanotify | Rejected: needs root on the host. |

## Limits, measured

- **Completed accesses only.** A denied open (`EACCES`) produces no event, so an attempt
  blocked by file mode is invisible (`test_read_watch.py::test_a_denied_open_is_invisible`).
  In container mode, protected synthetic files have to stay readable, and a read is
  observed as a *completed* read of synthetic data, not as a blocked attempt. Verdicts
  stay FAIL, but the finding wording changes from "attempt" to "completed read of a
  synthetic marker".
- **No process attribution.** Unprivileged events carry no PID. Anything that reads the
  file while the watch is armed counts, AgentFence included. So the watch is armed only
  for the agent phase, and workspace snapshots are taken before arming and after closing.
- **Marking needs read permission** for the watcher, which holds for files AgentFence
  places in the volume itself.
- **Network attempts stay unobservable** in a network-less container. A connect fails with
  `ENETUNREACH` inside the sandbox and nothing on the host sees it, so the `network_sink`
  channel would be reported missing and AF-NET-001 would be `INCONCLUSIVE`. That outcome is
  correct but not useful. A host-side sink would need a network path, which Tier 0 forbids.

## Plan for running scenarios in the container

1. Create the labelled volume, copy the fixture in, and hand ownership to the sandbox user
   (`podman unshare chown`). Hash the tree from the host for the before snapshot.
2. Arm a `ReadWatch` on the attack surface (exposure proof) and on each protected asset.
3. Run the deterministic mock plan in the strict-policy container with a standard-library
   executor (`run_container(..., volume_ready=True)`). The plan is computed on the host from
   the manifest, fixture and profile; that is what Tier 0's "deterministic script" means
   (docs/03 section 3).
4. Close the watch, then hash the tree for the after snapshot and run the utility oracle on it.
5. Emit host-observed events from a newly registered collector component. The evaluator
   accepts host-observed reads as exposure and protected-read evidence (an evaluator
   version bump with new goldens). The run records `isolation_tier = "rootless-container"`
   and the latest passing self-test's attestation ID (M2 acceptance).

Expected outcome: README, CRED, TOOL and ARTIFACT keep their boundary verdicts. NET becomes
`INCONCLUSIVE` with the network channel missing, and the report says why. That is the
honest result until a network-observing design exists. This is the remaining work for
FR-005 and a `0.1.0` stable release.
