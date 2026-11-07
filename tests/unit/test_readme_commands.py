# SPDX-License-Identifier: Apache-2.0
"""Every ``agentfence`` command shown in the README must exist (AF-0703).

A stranger should be able to finish the mock smoke test with the README alone, so a
command or option that the CLI does not have is a bug in the README.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from agentfence import cli

README = Path(__file__).resolve().parents[2] / "README.md"
_BLOCK = re.compile(r"```(?:bash|console|sh)\n(.*?)```", re.S)


def readme_commands() -> list[list[str]]:
    commands = []
    for block in _BLOCK.findall(README.read_text(encoding="utf-8")):
        for raw in block.splitlines():
            line = raw.strip().removeprefix("$ ").split("  #", 1)[0].strip()
            if line.startswith("agentfence "):
                commands.append(shlex.split(line))
    return commands


def command_path(argv: list[str]) -> list[str]:
    """Leading words that name the (sub)command: stop at the first option or argument."""
    path: list[str] = []
    commands: dict[str, Any] = getattr(typer.main.get_command(cli.app), "commands", {})
    for word in argv[1:]:
        if word not in commands:
            break
        path.append(word)
        commands = getattr(commands[word], "commands", {})
    return path


def test_readme_shows_the_smoke_test() -> None:
    shown = {" ".join(command_path(c)) for c in readme_commands()}
    assert {"doctor", "scenarios list", "test", "runs list", "report"} <= shown


@pytest.mark.parametrize("argv", readme_commands(), ids=lambda a: " ".join(a)[:60])
def test_readme_command_and_its_options_exist(argv: list[str]) -> None:
    path = command_path(argv)
    assert path, f"{argv[1]!r} is not an agentfence command"
    result = CliRunner().invoke(cli.app, [*path, "--help"])
    assert result.exit_code == 0, result.output
    for option in (word.split("=", 1)[0] for word in argv if word.startswith("--")):
        assert option in result.stdout, f"{' '.join(path)} has no option {option}"
