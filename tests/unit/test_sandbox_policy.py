# SPDX-License-Identifier: Apache-2.0
"""Strict Tier 0 sandbox policy and its audited launch arguments (AF-0203; docs/03 s4).

These tests prove what the *arguments* say. Whether a running container honours them is
verified against a rootless engine in AF-0205/AF-0206; nothing here starts a container.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from agentfence.core.config import ResourceLimits
from agentfence.core.sandbox_policy import IsolationPolicy, is_forbidden_env_name
from agentfence.runtime.containers import (
    Engine,
    PolicyViolationError,
    audit_run_options,
    build_run_argv,
)

DIGEST = "sha256:" + "ab" * 32
IMAGE = f"localhost/agentfence-mock@{DIGEST}"
ENGINES: list[Engine] = ["podman", "docker"]


def policy(**overrides: Any) -> IsolationPolicy:
    fields: dict[str, Any] = {
        "run_id": "5b0c9a1e-2f7d-4c3a-9d8e-1a2b3c4d5e6f",
        "attempt_id": "att-0001",
        "image": IMAGE,
        "workspace_volume": "agentfence-5b0c9a1e-att-0001",
    }
    fields.update(overrides)
    return IsolationPolicy(**fields)


def options(engine: Engine, p: IsolationPolicy | None = None) -> list[str]:
    argv = build_run_argv(engine, p or policy(), ["true"])
    image_at = argv.index(IMAGE)
    return list(argv[1:image_at])


# --- the policy model -------------------------------------------------------------------


def test_defaults_are_the_strict_policy() -> None:
    p = policy()
    assert (p.uid, p.gid, p.network, p.pull) == (65532, 65532, "none", "never")
    assert p.read_only_root and p.drop_all_capabilities and p.no_new_privileges
    assert not p.privileged and not p.host_mounts
    assert p.labels == {
        "io.agentfence.run": "5b0c9a1e-2f7d-4c3a-9d8e-1a2b3c4d5e6f",
        "io.agentfence.attempt": "att-0001",
    }


@pytest.mark.parametrize(
    "weakening",
    [
        {"privileged": True},
        {"host_mounts": True},
        {"read_only_root": False},
        {"drop_all_capabilities": False},
        {"no_new_privileges": False},
        {"network": "host"},
        {"network": "bridge"},
        {"pull": "always"},
        {"uid": 0},
        {"gid": 0},
        {"image": "docker.io/library/alpine:latest"},
        {"image": "alpine"},
        {"image": f"-v/home:/h@{DIGEST}"},
        {"workspace_volume": "/home/user/project"},
        {"workspace_volume": "agentfence-../../etc"},
        {"workspace_volume": "other-volume"},
        {"tmp_size_mb": 0},
        {"run_id": "run id with spaces"},
        {"cap_add": ["SYS_ADMIN"]},
        {"mounts": ["/var/run/docker.sock:/var/run/docker.sock"]},
        {"devices": ["/dev/kvm"]},
        {"pid": "host"},
    ],
    ids=lambda w: "-".join(f"{k}={v}" for k, v in w.items())[:40],
)
def test_no_weakening_validates(weakening: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        policy(**weakening)


def test_locked_fields_reject_look_alikes() -> None:
    with pytest.raises(ValidationError):
        policy(privileged=0)
    with pytest.raises(ValidationError):
        policy(read_only_root=1)


@pytest.mark.parametrize(
    "name",
    [
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_PROFILE",
        "SSH_AUTH_SOCK",
        "DOCKER_HOST",
        "CONTAINER_HOST",
        "KUBECONFIG",
        "DBUS_SESSION_BUS_ADDRESS",
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XDG_RUNTIME_DIR",
        "HTTPS_PROXY",
        "LD_PRELOAD",
        "PYTHONPATH",
        "MY_PASSWORD",
        "SERVICE_CREDENTIALS",
        "session_cookie",
    ],
)
def test_credential_socket_and_loader_variables_are_refused(name: str) -> None:
    assert is_forbidden_env_name(name)
    with pytest.raises(ValidationError):
        policy(env={name.upper(): "x"})


@pytest.mark.parametrize("name", ["HOME", "LANG", "TZ", "PYTHONDONTWRITEBYTECODE", "CI"])
def test_ordinary_variables_are_allowed(name: str) -> None:
    assert not is_forbidden_env_name(name)
    assert policy(env={name: "1"}).env == {name: "1"}


@pytest.mark.parametrize(
    "env",
    [
        {"lower": "x"},
        {"A-B": "x"},
        {"X": "a\nb"},
        {"X": "a\x00"},
        {f"V{i}": "x" for i in range(33)},
    ],
)
def test_malformed_environment_is_refused(env: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        policy(env=env)


# --- the built arguments ----------------------------------------------------------------


@pytest.mark.parametrize("engine", ENGINES)
def test_every_mandatory_control_is_on_the_command_line(engine: Engine) -> None:
    opts = options(engine)
    for required in (
        "--rm",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--network=none",
        "--pull=never",
        "--user=65532:65532",
        "--memory=1024m",
        "--memory-swap=1024m",
        "--pids-limit=128",
        "--cpus=1",
        "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=64m",
        "--mount=type=volume,source=agentfence-5b0c9a1e-att-0001,target=/workspace",
        "--label=io.agentfence.run=5b0c9a1e-2f7d-4c3a-9d8e-1a2b3c4d5e6f",
        "--label=io.agentfence.attempt=att-0001",
    ):
        assert required in opts
    assert audit_run_options(opts, engine) == []


def test_podman_specific_defaults_are_turned_off() -> None:
    assert {"--http-proxy=false", "--read-only-tmpfs=false"} <= set(options("podman"))
    assert not {o.split("=")[0] for o in options("docker")} & {"--http-proxy", "--read-only-tmpfs"}


@pytest.mark.parametrize("engine", ENGINES)
def test_no_host_path_socket_or_privilege_appears_anywhere(engine: Engine) -> None:
    argv = build_run_argv(engine, policy(env={"HOME": "/workspace"}), ["sh", "-c", "true"])
    joined = " ".join(argv)
    for forbidden in (
        "/home",
        "/root",
        "/var/run",
        "/run/user",
        ".sock",
        "type=bind",
        "--volume",
        "-v ",
        "--privileged",
        "--cap-add",
        "--device",
        "=host",
        "unconfined",
        "--env-file",
        "--env-host",
    ):
        assert forbidden not in joined


def test_argv_shape_and_limits_follow_the_policy() -> None:
    p = policy(limits=ResourceLimits(memory_mb=256, pids=64, cpu_quota=0.5), tmp_size_mb=16)
    argv = build_run_argv("podman", p, ["python3", "-c", "print('hi')"])
    assert argv[0] == "run"
    assert argv[-4:] == (IMAGE, "python3", "-c", "print('hi')")
    assert {"--memory=256m", "--memory-swap=256m", "--pids-limit=64", "--cpus=0.5"} <= set(argv)
    assert "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16m" in argv


def test_command_is_validated() -> None:
    with pytest.raises(ValueError, match="empty"):
        build_run_argv("podman", policy(), [])
    with pytest.raises(ValueError, match="NUL"):
        build_run_argv("podman", policy(), ["a\x00b"])


# --- the independent auditor ------------------------------------------------------------


def tampered(engine: Engine, old: str | None, new: str | None) -> list[str]:
    opts = options(engine)
    if old is not None:
        opts.remove(old)
    if new is not None:
        opts.append(new)
    return audit_run_options(opts, engine)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (None, "--privileged"),
        (None, "--volume=/home/user:/home/user"),
        (None, "-v=/var/run/docker.sock:/var/run/docker.sock"),
        (None, "--device=/dev/kvm"),
        (None, "--cap-add=SYS_ADMIN"),
        (None, "--pid=host"),
        (None, "--ipc=host"),
        (None, "--userns=host"),
        (None, "--env-file=/home/user/.env"),
        (None, "--security-opt=seccomp=unconfined"),
        (None, "--security-opt=apparmor=unconfined"),
        (None, "--env=OPENAI_API_KEY=sk-x"),
        (None, "--env=lower=x"),
        (None, "--label=io.agentfence.run=second"),
        (None, "--tmpfs=/tmp:rw,size=64m"),
        ("--network=none", "--network=host"),
        ("--network=none", "--network=slirp4netns"),
        ("--network=none", None),
        ("--read-only", None),
        ("--rm", "--rm=false"),
        ("--cap-drop=ALL", "--cap-drop=NET_RAW"),
        ("--security-opt=no-new-privileges", None),
        ("--pull=never", "--pull=always"),
        ("--user=65532:65532", "--user=0:0"),
        ("--user=65532:65532", "--user=root"),
        ("--memory-swap=1024m", "--memory-swap=-1"),
        ("--memory-swap=1024m", "--memory-swap=2048m"),
        ("--pids-limit=128", "--pids-limit=-1"),
        ("--cpus=1", "--cpus=0"),
        ("--workdir=/workspace", "--workdir=/"),
        (
            "--mount=type=volume,source=agentfence-5b0c9a1e-att-0001,target=/workspace",
            "--mount=type=bind,source=/home/user/project,target=/workspace",
        ),
        (
            "--mount=type=volume,source=agentfence-5b0c9a1e-att-0001,target=/workspace",
            "--mount=type=volume,source=agentfence-x,target=/",
        ),
        ("--label=io.agentfence.attempt=att-0001", None),
    ],
    ids=lambda v: str(v)[:32],
)
@pytest.mark.parametrize("engine", ENGINES)
def test_auditor_refuses_every_weakening(engine: Engine, old: str | None, new: str | None) -> None:
    assert tampered(engine, old, new), f"auditor accepted {old!r} -> {new!r}"


def test_auditor_refuses_podman_proxy_and_tmpfs_defaults() -> None:
    assert tampered("podman", "--http-proxy=false", None) == [
        "mandatory option --http-proxy is missing"
    ]
    assert tampered("podman", "--read-only-tmpfs=false", "--read-only-tmpfs=true")
    assert tampered("docker", None, "--http-proxy=false")


def test_auditor_refuses_space_separated_and_duplicate_options() -> None:
    assert tampered("docker", "--network=none", "--network") != []
    opts = [*options("docker"), "--memory=1024m"]
    assert "option --memory is given more than once" in audit_run_options(opts, "docker")


def test_a_builder_bug_becomes_a_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentfence.runtime import containers  # noqa: PLC0415

    original = containers._run_options
    monkeypatch.setattr(
        containers,
        "_run_options",
        lambda engine, p: [o for o in original(engine, p) if o != "--cap-drop=ALL"],
    )
    with pytest.raises(PolicyViolationError, match="mandatory option --cap-drop is missing"):
        build_run_argv("podman", policy(), ["true"])
