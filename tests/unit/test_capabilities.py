"""Capability model tests: isolation state is derived fail-closed."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentfence.core.capabilities import AdapterCapabilities, CapabilityCheck, HostCapabilities
from agentfence.core.vocabulary import CheckStatus, IsolationState


def check(cid: str, status: CheckStatus, *, required: bool = True) -> CapabilityCheck:
    return CapabilityCheck(id=cid, status=status, required=required, summary=f"{cid} check")


def host(*checks: CapabilityCheck) -> HostCapabilities:
    return HostCapabilities(os="linux", arch="x86_64", python="3.12.0", checks=checks)


def test_all_required_pass_is_offline_safe() -> None:
    caps = host(check("runtime.rootless", CheckStatus.PASS), check("cgroup.v2", CheckStatus.PASS))
    assert caps.isolation_state is IsolationState.OFFLINE_SAFE
    assert caps.blocking_checks == ()


@pytest.mark.parametrize("bad", [CheckStatus.FAIL, CheckStatus.UNKNOWN])
def test_any_required_non_pass_is_unsupported(bad: CheckStatus) -> None:
    caps = host(check("runtime.rootless", CheckStatus.PASS), check("cgroup.v2", bad))
    assert caps.isolation_state is IsolationState.UNSUPPORTED
    assert [c.id for c in caps.blocking_checks] == ["cgroup.v2"]


def test_optional_failures_do_not_block() -> None:
    caps = host(
        check("runtime.rootless", CheckStatus.PASS),
        check("lsm.apparmor", CheckStatus.UNKNOWN, required=False),
    )
    assert caps.isolation_state is IsolationState.OFFLINE_SAFE


def test_no_checks_at_all_is_unsupported() -> None:
    assert host().isolation_state is IsolationState.UNSUPPORTED


def test_check_status_parsed_from_string() -> None:
    parsed = CapabilityCheck.model_validate(
        {"id": "python.version", "status": "PASS", "required": True, "summary": "ok"}
    )
    assert parsed.status is CheckStatus.PASS


@pytest.mark.parametrize("cid", ["Runtime", "1abc", "has space", "x" * 80])
def test_check_ids_are_constrained(cid: str) -> None:
    with pytest.raises(ValidationError):
        check(cid, CheckStatus.PASS)


def test_adapter_capabilities_require_every_flag() -> None:
    with pytest.raises(ValidationError, match="Field required"):
        AdapterCapabilities.model_validate({"name": "mock", "binary_version": "0.1.0"})


def test_adapter_capabilities_strict_booleans() -> None:
    base = {
        "name": "mock",
        "binary_version": "0.1.0",
        "noninteractive": True,
        "structured_events": True,
        "supports_repo_read": True,
        "supports_file_write": True,
        "supports_tool_execution": False,
        "supports_model_proxy": False,
        "supports_isolated_config_root": True,
        "requires_network": False,
    }
    assert AdapterCapabilities.model_validate(base).requires_network is False
    with pytest.raises(ValidationError):
        AdapterCapabilities.model_validate({**base, "supports_model_proxy": "yes"})
