# Installing AgentFence Lab

## Requirements

- Linux on x86_64. Other platforms are out of scope until a VM-based backend exists
  (ADR-011).
- Python 3.12, 3.13 or 3.14. All three are tested in CI.
- Nothing else for the mock pipeline, reports and dashboard. For the container self-test
  (`agentfence doctor --self-test`) and the isolation tests: rootless Podman, delegated cgroup
  v2 controllers and the pinned sandbox image (see below).

## Install

From PyPI. The first releases are pre-releases, so ask for them explicitly:

```bash
python3 -m venv ~/.venvs/agentfence && . ~/.venvs/agentfence/bin/activate
pip install --pre 'agentfence-lab[ui]'      # drop [ui] if you do not want the dashboard
```

From a clone, for development: see the [README quickstart](../../README.md#quickstart-offline-no-api-keys)
and [CONTRIBUTING.md](../../CONTRIBUTING.md).

## Verified clean installs

`scripts/clean-install-check.sh` installs a built wheel into a fresh distribution container
and runs the README quickstart as an ordinary user. It checks the exit codes along the way
(`doctor` 3, `test` unsafe 1 and safe 0, `report` written with mode 600). Results on
2026-11-07, for the wheel built from `main`:

| Distribution | Image (digest) | Python | Result |
|---|---|---|---|
| Ubuntu 26.04.1 LTS | `docker.io/library/ubuntu@sha256:157dc3d6e4a3e4f2c68d12df9df9ed6e808b59af6d4c3757b432b4f519916725` | 3.14.4 | passed |
| Debian 13 (trixie) | `docker.io/library/debian@sha256:9c6e381421d0f48d1b8133e573f33c185ee16cbf505eda9c4a8a4791b25ea8a6` | 3.13.5 | passed |
| Fedora 43 | `registry.fedoraproject.org/fedora@sha256:c39145dc40b34883a6129af7fd98e1e59035f7002395f58d86c7a233b370792d` | 3.14.8 | passed |

Reproduce:

```bash
uv build --wheel
scripts/clean-install-check.sh docker.io/library/ubuntu:26.04 dist/agentfence_lab-*.whl
```

Inside those containers `doctor` exits 3, as it should: there is no container engine
there. The mock pipeline does not need one.

## Enabling the container self-test (optional)

On Ubuntu 26.04, as verified on the maintainer's machine (ADR-011 update):

```bash
sudo apt install podman uidmap passt crun fuse-overlayfs
agentfence doctor                       # every required check should now pass
podman pull docker.io/library/python@sha256:1e1f6334529d1fb26a2ef619d7cffb30a7c7d7cc1cc7338ebee283a58d1aeaca
agentfence doctor --self-test           # 16 checks from inside one disposable container
```

AgentFence never installs or configures a container engine itself. Leave the AppArmor
user-namespace restriction on: the distribution's Podman profile is enough. A rootful
Docker daemon is always reported as `FAIL` and is never used.

## Removing it

```bash
agentfence cleanup --older-than 0 --apply --yes   # every run, plus leftover labelled containers and volumes
pip uninstall agentfence-lab
rm -rf ~/.local/state/agentfence                  # attestations and anything else left in the state directory
```

Run `cleanup` first, while AgentFence is still installed, because it is what finds
leftover sandbox resources by label. Copies in backups or snapshots are outside
AgentFence's reach.
