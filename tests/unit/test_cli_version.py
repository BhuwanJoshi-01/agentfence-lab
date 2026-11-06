"""Contract tests for ``agentfence version`` (AF-0101, FR-001)."""

from __future__ import annotations

import json
import subprocess
import sys

from typer.testing import CliRunner

from agentfence import __version__
from agentfence.cli import app

runner = CliRunner()


def test_version_human_output_contains_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.startswith(f"AgentFence Lab {__version__} ")


def test_version_json_is_only_json_on_stdout() -> None:
    result = runner.invoke(app, ["version", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["name"] == "agentfence-lab"
    assert payload["version"] == __version__
    assert set(payload) == {"name", "version", "python", "platform"}


def test_no_arguments_shows_help_without_running_anything() -> None:
    result = runner.invoke(app, [])
    assert "version" in result.output
    assert result.exit_code in (0, 2)  # click >= 8.2 exits 2 for no_args_is_help


def test_unknown_command_is_usage_error_exit_2() -> None:
    result = runner.invoke(app, ["definitely-not-a-command"])
    assert result.exit_code == 2


def test_unknown_flag_is_usage_error_exit_2() -> None:
    result = runner.invoke(app, ["version", "--no-such-flag"])
    assert result.exit_code == 2


def test_python_dash_m_entry_point() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "agentfence", "version", "--json"],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["version"] == __version__
