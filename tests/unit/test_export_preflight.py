# SPDX-License-Identifier: Apache-2.0
"""Preflight secret scan before a report is printed or exported (#119; docs/07 s10 item 8)."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agentfence import cli
from agentfence.adapters.mock import MockProfile
from agentfence.core.redaction import SecretFinding, find_secrets
from agentfence.reporting.export import ExportError, preflight_scan

GITHUB_TOKEN = "ghp_" + "Z" * 30
MARKER = "AF_FAKE_TOKEN_LEAKED123"


# --- find_secrets -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        (f"see {MARKER}", "synthetic secret marker"),
        (
            "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----",
            "private key block",
        ),
        ("key sk-ant-" + "a" * 24, "API key (sk-)"),
        (GITHUB_TOKEN, "GitHub token"),
        ("github_pat_" + "b" * 24, "GitHub token"),
        ("AKIA" + "C" * 16, "AWS access key ID"),
        ("xoxb-" + "1" * 12, "Slack token"),
        ("AIza" + "d" * 33, "Google API key"),
        ("eyJhbGciOiJI.eyJzdWIiOiIx.c2ln", "JWT"),
        ("Authorization: Bearer abcdefgh123", "bearer token"),
        ("password = hunter2hunter2", "credential assignment"),
    ],
)
def test_each_secret_shape_is_found_without_its_value(text: str, kind: str) -> None:
    findings = find_secrets(f"line one\n{text}\n")
    assert SecretFinding(line=2, kind=kind) in findings
    assert all(text.rsplit(maxsplit=1)[-1] not in repr(f) for f in findings)


def test_redacted_placeholders_and_ordinary_text_are_clean() -> None:
    clean = (
        "token=[CREDENTIAL] secret: [SYNTHETIC_SECRET]\n"
        '{"token": "short", "category": "credential-exposure"}\n'
        "Content-Security-Policy: default-src 'none'; style-src 'sha256-abc='\n"
    )
    assert find_secrets(clean) == ()


def test_findings_are_sorted_and_deduplicated() -> None:
    text = f"{MARKER} {MARKER}\n{GITHUB_TOKEN}\n"
    assert find_secrets(text) == (
        SecretFinding(1, "synthetic secret marker"),
        SecretFinding(2, "GitHub token"),
    )


def test_preflight_message_lists_kinds_and_lines_but_never_values() -> None:
    lines = [f"{GITHUB_TOKEN}"] * 7 + [MARKER]
    with pytest.raises(ExportError) as caught:
        preflight_scan("\n".join(lines))
    message = str(caught.value)
    assert "8 suspected secret(s): GitHub token on line 1," in message
    assert "and 3 more" in message
    assert GITHUB_TOKEN not in message
    assert MARKER not in message
    preflight_scan("nothing to see here\n")


# --- CLI ------------------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    for var in ("XDG_STATE_HOME", "XDG_CONFIG_HOME", "AGENTFENCE_CONFIG", "AGENTFENCE_STATE_DIR"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    return home


def af(*args: str) -> Any:
    return CliRunner().invoke(cli.app, list(args))


def new_run(profile: str = "unsafe") -> str:
    result = af("test", "--agent-profile", profile, "--json")
    return str(json.loads(result.stdout)["run_id"])


@pytest.mark.parametrize("profile", [p.value for p in MockProfile])
def test_real_reports_pass_the_scan_in_every_format(home: Path, profile: str) -> None:
    run = new_run(profile)
    for fmt in ("json", "text", "html"):
        result = af("report", run, "--format", fmt)
        assert result.exit_code == 0, (fmt, result.stderr)
    assert af("runs", "show", run).exit_code == 0
    assert af("runs", "show", run, "--json").exit_code == 0


@pytest.fixture
def leaky_notes(monkeypatch: pytest.MonkeyPatch) -> None:
    original = cli.reproduction_notes
    monkeypatch.setattr(
        cli, "reproduction_notes", lambda report: [*original(report), f"note {MARKER}"]
    )


@pytest.fixture
def leaky_json(monkeypatch: pytest.MonkeyPatch) -> None:
    original = cli._verified
    monkeypatch.setattr(
        cli,
        "_verified",
        lambda run_id: dataclasses.replace(
            original(run_id), text=original(run_id).text + GITHUB_TOKEN
        ),
    )


def assert_refused(result: Any, kind: str) -> None:
    assert result.exit_code == 4
    assert result.stdout == ""
    assert f"{kind} on line" in result.stderr
    assert MARKER not in result.stderr
    assert GITHUB_TOKEN not in result.stderr


@pytest.mark.usefixtures("leaky_notes")
@pytest.mark.parametrize("fmt", ["text", "html"])
def test_leak_in_rendered_report_is_refused_and_nothing_is_written(
    home: Path, tmp_path: Path, fmt: str
) -> None:
    run = new_run()
    target = tmp_path / f"out.{fmt}"
    assert_refused(af("report", run, "--format", fmt), "synthetic secret marker")
    assert_refused(
        af("report", run, "--format", fmt, "--output", str(target)), "synthetic secret marker"
    )
    assert not target.exists()


@pytest.mark.usefixtures("leaky_json")
def test_leak_in_json_report_is_refused(home: Path) -> None:
    run = new_run()
    assert_refused(af("report", run, "--format", "json"), "GitHub token")
    assert_refused(af("runs", "show", run, "--json"), "GitHub token")


@pytest.mark.usefixtures("leaky_notes")
def test_runs_show_is_scanned_too(home: Path) -> None:
    assert_refused(af("runs", "show", new_run()), "synthetic secret marker")
