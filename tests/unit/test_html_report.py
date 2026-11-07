"""Self-contained HTML report (AF-0503; docs/03 section 8, docs/10 section 7)."""

from __future__ import annotations

import base64
import hashlib
import json
import stat
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentfence import cli
from agentfence.adapters.mock import MockProfile
from agentfence.reporting.html_report import CSP, STYLE, render_html
from agentfence.reporting.json_report import SuiteReport, build_report
from agentfence.runtime.attempt import stepping_clock
from agentfence.runtime.suite import run_suite
from agentfence.scenarios import bundled_scenarios

HOSTILE = "<script>alert(1)</script><img src=x onerror=alert(2)>\"'&</style>"


class Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.data: dict[str, list[str]] = {"style": [], "script": []}
        self._in: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))
        self._in = tag if tag in self.data else None

    def handle_endtag(self, tag: str) -> None:
        self._in = None

    def handle_data(self, data: str) -> None:
        if self._in:
            self.data[self._in].append(data)


def report(tmp_path: Path, profile: MockProfile = MockProfile.UNSAFE) -> SuiteReport:
    clock = stepping_clock(datetime(2026, 11, 7, tzinfo=UTC), timedelta(milliseconds=1))
    suite = run_suite(
        [e.manifest for e in bundled_scenarios("mvp")],
        profile,
        scenario_profile="mvp",
        seed=42,
        workdir=tmp_path,
        clock=clock,
        run_id="7e23c178-70d2-4484-9bb1-89cf95da92b4",
    )
    return build_report(suite)


def parse(html: str) -> Collector:
    parser = Collector()
    parser.feed(html)
    return parser


def test_strict_csp_with_no_scripts_and_hashed_style(tmp_path: Path) -> None:
    html = render_html(report(tmp_path))
    page = parse(html)
    metas = [a for t, a in page.tags if t == "meta" and a.get("http-equiv")]
    assert metas[0]["content"] == CSP
    for directive in (
        "default-src 'none'",
        "script-src 'none'",
        "connect-src 'none'",
        "img-src 'none'",
        "form-action 'none'",
        "base-uri 'none'",
    ):
        assert directive in CSP
    expected = base64.b64encode(hashlib.sha256(STYLE.encode()).digest()).decode()
    assert f"'sha256-{expected}'" in CSP
    assert "".join(page.data["style"]) == STYLE  # the hash covers exactly what is served


def test_no_remote_resources_or_executable_script(tmp_path: Path) -> None:
    html = render_html(report(tmp_path))
    page = parse(html)
    for tag, attrs in page.tags:
        assert "src" not in attrs, tag
        href = attrs.get("href")
        assert href is None or href.startswith("#"), href
        assert not any(k.startswith("on") for k in attrs), (tag, attrs)
    scripts = [a for t, a in page.tags if t == "script"]
    assert scripts == [{"type": "application/json", "id": "agentfence-report"}]
    for needle in ("http://", "https://", "@import", "url(", "javascript:"):
        assert needle not in html


def test_embedded_json_round_trips_and_is_inert(tmp_path: Path) -> None:
    original = report(tmp_path)
    page = parse(render_html(original))
    raw = "".join(page.data["script"])
    assert "<" not in raw
    assert ">" not in raw
    assert SuiteReport.model_validate(json.loads(raw)) == original


def test_hostile_strings_are_escaped_everywhere(tmp_path: Path) -> None:
    base = report(tmp_path)
    first = base.results[0]
    event = first.events[0].model_copy(update={"details": {"note": HOSTILE}})
    hostile = first.model_copy(
        update={"limitations": (HOSTILE,), "events": (event, *first.events[1:])}
    )
    tampered = base.model_copy(update={"results": (hostile, *base.results[1:])})
    html = render_html(tampered, notes=[HOSTILE])
    assert "<script>alert" not in html
    assert "<img" not in html
    assert "onerror" not in "".join(k for _, attrs in parse(html).tags for k in attrs)
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_statuses_are_text_not_only_color(tmp_path: Path) -> None:
    html = render_html(report(tmp_path))
    assert '<span class="FAIL">FAIL</span>' in html
    assert "AF-DATA-ALTER" in html
    assert "not exfiltration" in html
    assert 'lang="en"' in html
    assert "<caption>" in html


def test_reproduction_notes_section(tmp_path: Path) -> None:
    html = render_html(report(tmp_path), notes=["Reproduce with: agentfence test --seed 42"])
    assert "<h2>Reproduction</h2>" in html
    assert render_html(report(tmp_path / "b")).count("<h2>Reproduction</h2>") == 0


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    for var in (
        "XDG_STATE_HOME",
        "XDG_CONFIG_HOME",
        "AGENTFENCE_CONFIG",
        "AGENTFENCE_STATE_DIR",
        "AGENTFENCE_LOG_LEVEL",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    return home


def test_cli_html_export(home: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    out = runner.invoke(cli.app, ["test", "--agent-profile", "unsafe", "--json"]).stdout
    run_id = json.loads(out)["run_id"]
    target = tmp_path / "report.html"
    result = runner.invoke(cli.app, ["report", run_id, "--format", "html", "--output", str(target)])
    assert result.exit_code == 0
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    html = target.read_text()
    assert html.startswith("<!DOCTYPE html>")
    assert "Reproduce with: agentfence test" in html
    assert str(home) not in html


def test_empty_denominators_render_as_not_applicable(tmp_path: Path) -> None:
    html = render_html(report(tmp_path, MockProfile.NO_EXPOSURE))
    assert "<td>Violations among exposed</td><td>n/a</td>" in html
