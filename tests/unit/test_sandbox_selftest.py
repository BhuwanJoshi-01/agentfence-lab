# SPDX-License-Identifier: Apache-2.0
"""Sandbox runner, self-test judge and attestation (AF-0205, AF-0206), without an engine.

``data/selftest_observation_podman.json`` is what the probe reported inside a real
strict-policy container on rootless Podman 5.7.0 (maintainer host, 2026-11-07). The judge
must PASS it, FAIL each weakened variant and say UNKNOWN when evidence is missing. The
same probe against a real engine runs in ``tests/isolation``.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agentfence import cli
from agentfence.core.attestation import IsolationAttestation
from agentfence.core.sandbox_policy import SANDBOX_IMAGE, IsolationPolicy
from agentfence.core.vocabulary import CheckStatus
from agentfence.runtime import sandbox
from agentfence.runtime.engine_probe import EngineFacts, QueryResult
from agentfence.runtime.host_probe import HostFacts
from agentfence.runtime.sandbox import SandboxError, judge, run_container, self_test
from agentfence.runtime.supervisor import (
    CapturedStream,
    ProcessOutcome,
    ProcessResult,
    ProcessSpec,
)

OBSERVED: dict[str, Any] = json.loads(
    (Path(__file__).parent / "data" / "selftest_observation_podman.json").read_text()
)
POLICY = IsolationPolicy(
    run_id="selftest-test", attempt_id="probe", image=SANDBOX_IMAGE, workspace_volume="agentfence-t"
)
NOW = datetime(2026, 11, 7, tzinfo=UTC)
PODMAN = "/usr/bin/podman"


def statuses(observed: dict[str, Any]) -> dict[str, CheckStatus]:
    return {c.id: c.status for c in judge(observed, POLICY)}


# --- judge ---------------------------------------------------------------------------------


def test_the_real_observation_passes_every_check() -> None:
    result = statuses(OBSERVED)
    assert len(result) == 16
    assert set(result.values()) == {CheckStatus.PASS}


def mutated(path: str, value: object) -> dict[str, Any]:
    observed = copy.deepcopy(OBSERVED)
    target: Any = observed
    *parents, leaf = path.split(".")
    for key in parents:
        target = target[key]
    target[leaf] = value
    return observed


@pytest.mark.parametrize(
    ("path", "value", "check"),
    [
        ("uid", 0, "process.non_root"),
        ("uid_map", ["0 0 4294967295"], "userns.mapped"),
        ("uid_map", ["0 0 1", "1 100000 65536"], "userns.mapped"),
        ("cap_eff", "00000000a80425fb", "capabilities.none"),
        ("cap_prm", "0000000000000400", "capabilities.none"),
        ("no_new_privs", "0", "no_new_privileges"),
        ("seccomp", "0", "seccomp.filter"),
        ("rootfs_write", "OK", "rootfs.read_only"),
        ("tmp_bytes", 8 * 1024**3, "tmp.size_limited"),
        ("tmp_write", "EROFS", "tmp.size_limited"),
        ("workspace_write", "EACCES", "workspace.writable"),
        ("memory_max", "max", "memory.limit"),
        ("memory_swap_max", "max", "memory.limit"),
        ("pids_max", "max", "pids.limit"),
        ("fork.refused", "", "pids.limit"),
        ("fork.forked", 4000, "pids.limit"),
        ("cpu_max", "max 100000", "cpu.limit"),
        ("interfaces", ["eth0", "lo"], "network.none"),
        ("connect_public", "CONNECTED", "network.none"),
        ("connect_metadata", "TimeoutError", "network.none"),
        ("connect_host_gateway", "ECONNREFUSED", "network.none"),
        ("dns", "RESOLVED", "dns.blocked"),
        ("resolve_host_alias", "RESOLVED", "hosts.private"),
        ("hosts_entries", [*OBSERVED["hosts_entries"], "127.0.1.1 build-host"], "hosts.private"),
        (
            "hosts_entries",
            [*OBSERVED["hosts_entries"], "192.168.1.74 host.containers.internal"],
            "hosts.private",
        ),
        ("mount_targets", [*OBSERVED["mount_targets"], "/home/user"], "mounts.allowlisted"),
        ("mount_targets", [*OBSERVED["mount_targets"], "/run/podman"], "mounts.allowlisted"),
        ("sockets", ["/run/docker.sock"], "sockets.absent"),
    ],
)
def test_each_weakening_fails_its_check(path: str, value: object, check: str) -> None:
    result = statuses(mutated(path, value))
    assert result[check] is CheckStatus.FAIL
    assert sum(s is not CheckStatus.PASS for s in result.values()) == 1


@pytest.mark.parametrize(
    ("key", "check"),
    [
        ("uid", "process.non_root"),
        ("uid_map", "userns.mapped"),
        ("seccomp", "seccomp.filter"),
        ("memory_max", "memory.limit"),
        ("fork", "pids.limit"),
        ("interfaces", "network.none"),
        ("hosts_entries", "hosts.private"),
        ("mount_targets", "mounts.allowlisted"),
        ("sockets", "sockets.absent"),
    ],
)
def test_missing_evidence_is_unknown_not_pass(key: str, check: str) -> None:
    observed = {k: v for k, v in OBSERVED.items() if k != key}
    assert statuses(observed)[check] is CheckStatus.UNKNOWN


def test_nothing_observed_is_all_unknown() -> None:
    assert set(statuses({}).values()) == {CheckStatus.UNKNOWN}


# --- attestation -------------------------------------------------------------------------


def attestation(checks: tuple[Any, ...] | None = None) -> IsolationAttestation:
    return IsolationAttestation.create(
        created_at=NOW,
        tool_version="0.1.0.dev0",
        engine="podman",
        engine_version="5.7.0",
        image=SANDBOX_IMAGE,
        policy={"uid": 65532, "network": "none"},
        checks=checks if checks is not None else judge(OBSERVED, POLICY),
    )


def test_attestation_id_is_content_addressed() -> None:
    first, second = attestation(), attestation()
    assert first.attestation_id == second.attestation_id
    assert first.verify_id()
    assert first.passed
    tampered = first.model_copy(update={"engine_version": "9.9.9"})
    assert not tampered.verify_id()
    roundtrip = IsolationAttestation.model_validate_json(first.model_dump_json())
    assert roundtrip == first
    assert roundtrip.verify_id()


def test_any_unknown_means_not_passed() -> None:
    checks = judge({k: v for k, v in OBSERVED.items() if k != "dns"}, POLICY)
    assert not attestation(checks).passed
    assert not attestation(()).passed


# --- runner and self-test with a fake engine ------------------------------------------------


class FakeEngine:
    def __init__(self, leftovers: Sequence[dict[str, str]] = (), *, create_ok: bool = True):
        self.calls: list[list[str]] = []
        self.leftovers = list(leftovers)
        self.create_ok = create_ok
        self.remove_ok = True

    def __call__(self, argv: Sequence[str]) -> QueryResult:
        self.calls.append(list(argv))
        if argv[1:3] == ["volume", "create"]:
            return QueryResult(0 if self.create_ok else 125, b"")
        if argv[1] == "ps":
            records = [r for r in self.leftovers if "ID" in r]
            return QueryResult(0, "".join(json.dumps(r) + "\n" for r in records).encode())
        if argv[1:3] == ["volume", "ls"]:
            records = [r for r in self.leftovers if "Name" in r]
            return QueryResult(0, "".join(json.dumps(r) + "\n" for r in records).encode())
        if "rm" in argv:
            return QueryResult(0 if self.remove_ok else 1, b"")
        return QueryResult(1, b"")


def finished(stdout: bytes, outcome: ProcessOutcome = ProcessOutcome.EXITED) -> ProcessResult:
    return ProcessResult(
        outcome,
        exit_code=0 if outcome is ProcessOutcome.EXITED else None,
        signal=None,
        stdout=CapturedStream(stdout, 0, complete=True),
        stderr=CapturedStream(b"", 0, complete=True),
        duration_seconds=1.0,
    )


def labelled(run_id: str) -> list[dict[str, str]]:
    labels = f"io.agentfence.run={run_id},io.agentfence.attempt=probe"
    return [{"ID": "c" * 64, "Labels": labels}, {"Name": "agentfence-t", "Labels": labels}]


def test_run_container_argv_env_and_cleanup() -> None:
    other = {"ID": "d" * 64, "Labels": "io.agentfence.run=other"}
    engine = FakeEngine([*labelled(POLICY.run_id), other])
    seen: list[ProcessSpec] = []

    def fake_supervise(spec: ProcessSpec) -> ProcessResult:
        seen.append(spec)
        return finished(b"done\n")

    result = run_container(
        PODMAN, POLICY, ["true"], timeout=30, run=engine, supervise_fn=fake_supervise
    )
    assert result.stdout.data == b"done\n"
    spec = seen[0]
    assert spec.argv[:3] == (PODMAN, "run", "--rm")
    assert "--network=none" in spec.argv
    assert spec.timeout_seconds == 30
    assert "PATH" in spec.env or spec.env.keys() <= {"HOME", "XDG_RUNTIME_DIR", "LC_ALL"}
    assert engine.calls[0][1:3] == ["volume", "create"]
    removed = [c for c in engine.calls if "rm" in c]
    assert removed == [
        [PODMAN, "rm", "--force", "c" * 64],
        [PODMAN, "volume", "rm", "--force", "agentfence-t"],
    ]


def test_cleanup_runs_even_when_the_run_raises() -> None:
    engine = FakeEngine(labelled(POLICY.run_id))

    def boom(spec: ProcessSpec) -> ProcessResult:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_container(PODMAN, POLICY, ["true"], timeout=30, run=engine, supervise_fn=boom)
    assert [PODMAN, "rm", "--force", "c" * 64] in engine.calls


def test_volume_creation_failure_and_cleanup_failure_refuse() -> None:
    with pytest.raises(SandboxError, match="could not create"):
        run_container(PODMAN, POLICY, ["true"], timeout=30, run=FakeEngine(create_ok=False))
    engine = FakeEngine(labelled(POLICY.run_id))
    engine.remove_ok = False
    with pytest.raises(SandboxError, match="could not be removed"):
        run_container(
            PODMAN, POLICY, ["true"], timeout=30, run=engine, supervise_fn=lambda s: finished(b"")
        )


def test_self_test_passes_on_the_real_observation() -> None:
    line = json.dumps(OBSERVED).encode() + b"\n"
    result = self_test(
        PODMAN, "5.7.0", now=NOW, run=FakeEngine(), supervise_fn=lambda s: finished(line)
    )
    assert result.passed
    assert result.verify_id()
    assert result.image == SANDBOX_IMAGE
    assert result.policy["pids"] == 128


@pytest.mark.parametrize(
    "outcome",
    [finished(b"", ProcessOutcome.TIMED_OUT), finished(b"not json"), finished(b"[1, 2]\n")],
)
def test_a_probe_that_does_not_report_is_all_unknown(outcome: ProcessResult) -> None:
    result = self_test(PODMAN, "5.7.0", now=NOW, run=FakeEngine(), supervise_fn=lambda s: outcome)
    assert not result.passed
    assert {c.status for c in result.checks} == {CheckStatus.UNKNOWN}
    assert all("probe did not report" in c.observed for c in result.checks)


def test_the_probe_is_shipped_and_standard_library_only() -> None:
    imports = {
        line.split()[1].split(".")[0]
        for line in sandbox.PROBE_SOURCE.splitlines()
        if line.startswith(("import ", "from ")) and "__future__" not in line
    }
    assert imports <= {
        "contextlib",
        "errno",
        "json",
        "os",
        "signal",
        "socket",
        "stat",
        "sys",
        "time",
    }


# --- doctor --self-test ---------------------------------------------------------------------


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for var in ("XDG_STATE_HOME", "XDG_CONFIG_HOME", "AGENTFENCE_CONFIG"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGENTFENCE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)
    return tmp_path / "state"


def safe_facts(**overrides: Any) -> HostFacts:
    fields: dict[str, Any] = {
        "system": "linux",
        "machine": "x86_64",
        "python_version": (3, 13, 0),
        "euid": 1000,
        "executables": {"podman": PODMAN, "docker": None, "git": "/usr/bin/git"},
        "cgroup2": True,
        "home": "/home/u",
        "engines": (EngineFacts("podman", True, rootless=True, seccomp=True, version="5.7.0"),),
        "delegated_controllers": frozenset({"cpu", "memory", "pids"}),
        "userns_max": 1000,
        "apparmor_userns_restricted": False,
        "sandbox_image_present": True,
    }
    fields.update(overrides)
    return HostFacts(**fields)


def doctor(monkeypatch: pytest.MonkeyPatch, facts: HostFacts, outcome: Any, *args: str) -> Any:
    monkeypatch.setattr(cli, "gather_host_facts", lambda: facts)

    def fake(*_a: Any, **_k: Any) -> IsolationAttestation:
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, IsolationAttestation)
        return outcome

    monkeypatch.setattr(cli, "run_self_test", fake)
    return CliRunner().invoke(cli.app, ["doctor", "--self-test", *args])


def test_doctor_self_test_passes_and_saves_a_private_attestation(
    state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    good = attestation()
    result = doctor(monkeypatch, safe_facts(), good)
    assert result.exit_code == 0, result.output
    assert "Self-test PASSED" in result.stdout
    saved = state / "attestations" / f"{good.attestation_id}.json"
    assert saved.stat().st_mode & 0o777 == 0o600
    assert IsolationAttestation.model_validate_json(saved.read_text()).verify_id()


def test_doctor_self_test_json(state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result = doctor(monkeypatch, safe_facts(), attestation(), "--json")
    body = json.loads(result.stdout)
    assert body["self_test"]["attestation_id"] == attestation().attestation_id
    assert body["attestation_path"].startswith("$HOME/")


@pytest.mark.parametrize(
    ("facts", "outcome", "code", "message"),
    [
        (safe_facts(), attestation(judge({}, POLICY)), 3, "Self-test FAILED"),
        (safe_facts(sandbox_image_present=False), None, 3, f"podman pull {SANDBOX_IMAGE}"),
        (safe_facts(engines=()), None, 3, "every required check must pass first"),
        (safe_facts(), SandboxError("volume trouble"), 4, "could not run the self-test"),
    ],
)
def test_doctor_self_test_refusals(
    state: Path,
    monkeypatch: pytest.MonkeyPatch,
    facts: HostFacts,
    outcome: Any,
    code: int,
    message: str,
) -> None:
    result = doctor(monkeypatch, facts, outcome)
    assert result.exit_code == code, result.output
    assert message in result.stdout
