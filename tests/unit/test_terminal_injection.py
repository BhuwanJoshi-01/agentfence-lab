# SPDX-License-Identifier: Apache-2.0
"""Untrusted text must never reach the terminal raw (issue #86).

Escape sequences are built at runtime so no raw control bytes live in the repository.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from agentfence import cli
from agentfence.core.redaction import scrub_text
from agentfence.core.scenario_manifest import ScenarioManifest
from agentfence.scenarios.loader import LoadedManifest

DATA = Path(__file__).parent / "data"
ESC = chr(0x1B)
HOSTILE = [
    ESC + "[2J",  # clear screen
    ESC + "]0;pwned" + chr(0x07),  # set window title (OSC ... BEL)
    ESC + "]8;;http://x" + ESC + "\\link",  # OSC 8 hyperlink
    chr(0x9B) + "31m",  # C1 CSI
    chr(0x7F),  # DEL
    chr(0x07),  # BEL
    chr(0x202E),  # right-to-left override
    chr(0x200B),  # zero-width space
    chr(0x2066),  # left-to-right isolate
    chr(0xFEFF),  # byte order mark
]
FORBIDDEN_OUTPUT = (ESC, chr(0x9B), chr(0x7F), chr(0x07), chr(0x202E), chr(0x200B), chr(0x2066))


def adversarial() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load((DATA / "readme_adversarial.yaml").read_text())
    return loaded


def benign() -> dict[str, Any]:
    data = copy.deepcopy(adversarial())
    data["id"] = "AF-README-001-B"
    data["fixture"]["variant"] = "benign"
    data.pop("benign_pair")
    data.pop("attack_surface")
    data["verdict"]["failure_if"] = []
    return data


@pytest.mark.parametrize("payload", HOSTILE)
@pytest.mark.parametrize("field", ["title", "description"])
def test_manifest_rejects_control_and_format_characters(payload: str, field: str) -> None:
    data = adversarial()
    if field == "title":
        data["title"] = f"Readme {payload} title"
    else:
        data["legitimate_task"]["description"] = f"Fix the test {payload} please"
    with pytest.raises(ValidationError, match="not allowed"):
        ScenarioManifest.model_validate(data)


@pytest.mark.parametrize("text", ["Ünïcode títle — with dashes", "数学 calculator task"])
def test_ordinary_unicode_text_is_allowed(text: str) -> None:
    data = adversarial()
    data["title"] = text
    assert ScenarioManifest.model_validate(data).title == text


@pytest.mark.parametrize("payload", HOSTILE)
def test_scrub_text_removes_every_hostile_sequence(payload: str) -> None:
    out = scrub_text(f"a{payload}b")
    assert not any(ch in out for ch in FORBIDDEN_OUTPUT)
    assert out.startswith("a")
    assert out.endswith("b")


def test_scenarios_list_scrubs_titles_even_if_schema_is_bypassed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid = ScenarioManifest.model_validate(adversarial())
    hostile = valid.model_copy(update={"title": "Readme " + "".join(HOSTILE) + " title"})
    monkeypatch.setattr(
        cli, "bundled_scenarios", lambda _p: [LoadedManifest("mvp/x.yaml", "0" * 64, hostile)]
    )
    out = CliRunner().invoke(cli.app, ["scenarios", "list"]).stdout
    assert "Readme" in out
    assert not any(ch in out for ch in FORBIDDEN_OUTPUT)


def test_scenarios_validate_scrubs_hostile_file_names(tmp_path: Path) -> None:
    hostile_name = "a" + ESC + "]0;pwned" + chr(0x07) + chr(0x202E) + ".yaml"
    (tmp_path / hostile_name).write_text(yaml.safe_dump(adversarial()), encoding="utf-8")
    (tmp_path / "b.yaml").write_text(yaml.safe_dump(benign()), encoding="utf-8")
    result = CliRunner().invoke(cli.app, ["scenarios", "validate", str(tmp_path)])
    assert result.exit_code == 0, result.stdout
    assert not any(ch in result.stdout for ch in FORBIDDEN_OUTPUT)
    json_result = CliRunner().invoke(cli.app, ["scenarios", "validate", str(tmp_path), "--json"])
    assert not any(ch in json_result.stdout for ch in FORBIDDEN_OUTPUT)


def test_scenarios_validate_scrubs_hostile_names_in_problems(tmp_path: Path) -> None:
    hostile_name = "z" + ESC + "[2J.yaml"
    (tmp_path / hostile_name).write_text("a: &x 1\n", encoding="utf-8")
    result = CliRunner().invoke(cli.app, ["scenarios", "validate", str(tmp_path)])
    assert result.exit_code == 2
    assert ESC not in result.stdout
