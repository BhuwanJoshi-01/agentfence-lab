# SPDX-License-Identifier: Apache-2.0
"""Read-only local API (AF-0601): gates first, then every route (docs/06 section 7)."""

from __future__ import annotations

import importlib.util
import json
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from agentfence import cli
from agentfence.api import app as api
from agentfence.api.app import SECURITY_HEADERS, create_app
from agentfence.storage.paths import resolve_paths

TOKEN = "t" * 43
AUTH = {"Authorization": f"Bearer {TOKEN}"}
BASE = "http://127.0.0.1:8484"


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state = tmp_path / "state"
    for var in ("XDG_STATE_HOME", "XDG_CONFIG_HOME", "AGENTFENCE_CONFIG"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGENTFENCE_STATE_DIR", str(state))
    monkeypatch.chdir(tmp_path)
    return state


@pytest.fixture
def client(state: Path) -> Iterator[TestClient]:
    with TestClient(create_app(resolve_paths(), token=TOKEN, port=8484), base_url=BASE) as c:
        yield c


def new_run(profile: str) -> str:
    result = CliRunner().invoke(cli.app, ["test", "--agent-profile", profile, "--json"])
    return str(json.loads(result.stdout)["run_id"])


def get(client: TestClient, path: str, **kw: Any) -> httpx.Response:
    headers = {**AUTH, **kw.pop("headers", {})}
    return client.get(f"/api/v1{path}", headers=headers, **kw)


# --- gates -------------------------------------------------------------------------------


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8484", "127.0.0.1:9999", ""])
def test_foreign_host_headers_are_refused(client: TestClient, host: str) -> None:
    response = get(client, "/health", headers={"Host": host})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "host_not_allowed"


def test_localhost_is_accepted(client: TestClient) -> None:
    assert get(client, "/health", headers={"Host": "localhost:8484"}).status_code == 200


@pytest.mark.parametrize("origin", ["http://evil.example", "null", "http://127.0.0.1:9999"])
def test_cross_origin_requests_are_refused(client: TestClient, origin: str) -> None:
    response = get(client, "/health", headers={"Origin": origin})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "origin_not_allowed"
    assert "access-control-allow-origin" not in response.headers


def test_same_origin_is_accepted_and_no_cors_is_offered(client: TestClient) -> None:
    assert get(client, "/health", headers={"Origin": BASE}).status_code == 200
    preflight = client.options(
        "/api/v1/health",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert preflight.status_code == 403
    assert not any(h.startswith("access-control-") for h in preflight.headers)


@pytest.mark.parametrize(
    "authorization",
    [None, "", "Bearer", f"Bearer {TOKEN}x", f"bearer {TOKEN}", f"Basic {TOKEN}", TOKEN],
)
def test_session_token_is_required_exactly(client: TestClient, authorization: str | None) -> None:
    headers = {} if authorization is None else {"Authorization": authorization}
    response = client.get("/api/v1/health", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_the_api_is_read_only(client: TestClient, method: str) -> None:
    response = client.request(method, "/api/v1/runs", headers=AUTH)
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "read_only"


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"])
def test_cdn_backed_docs_are_disabled(client: TestClient, path: str) -> None:
    assert client.get(path, headers=AUTH).status_code == 404


def test_security_headers_on_success_and_errors(client: TestClient) -> None:
    for response in (
        get(client, "/health"),
        get(client, "/runs/not-a-run"),
        client.get("/api/v1/health"),
        get(client, "/runs", params={"limit": 0}),
    ):
        for name, value in SECURITY_HEADERS.items():
            assert response.headers[name] == value
        assert "server" not in response.headers


def test_short_tokens_are_rejected(state: Path) -> None:
    with pytest.raises(ValueError, match="too short"):
        create_app(resolve_paths(), token="short", port=8484)  # noqa: S106 - deliberately weak


# --- routes ------------------------------------------------------------------------------


def test_health_before_and_after_the_first_run(client: TestClient) -> None:
    assert get(client, "/health").json()["store"] is False
    assert get(client, "/runs").json() == {"runs": [], "next_cursor": None}
    new_run("safe")
    body = get(client, "/health").json()
    assert body["ready"] is True
    assert body["store"] is True


def test_runs_are_paged_with_an_opaque_cursor(client: TestClient) -> None:
    created = {new_run("safe") for _ in range(3)}
    first = get(client, "/runs", params={"limit": 2}).json()
    assert len(first["runs"]) == 2
    second = get(client, "/runs", params={"limit": 2, "cursor": first["next_cursor"]}).json()
    assert len(second["runs"]) == 1
    assert second["next_cursor"] is None
    assert {r["run_id"] for r in first["runs"] + second["runs"]} == created


@pytest.mark.parametrize(
    ("params", "status"),
    [({"limit": 0}, 422), ({"limit": 201}, 422), ({"cursor": "x"}, 400), ({"cursor": "a~b"}, 400)],
)
def test_bad_paging_parameters(client: TestClient, params: dict[str, Any], status: int) -> None:
    assert get(client, "/runs", params=params).status_code == status


def test_run_detail_is_the_verified_report_without_attempts(client: TestClient) -> None:
    run = new_run("unsafe")
    body = get(client, f"/runs/{run}").json()
    assert body["run_id"] == run
    assert "results" not in body
    assert body["summary"]["boundary"]["FAIL"] == 5


@pytest.mark.parametrize(
    "run_id",
    ["00000000-0000-4000-8000-000000000000", "not-a-uuid", "..%2F..%2Fetc%2Fpasswd", "x" * 300],
)
def test_unknown_or_malformed_runs_are_404(client: TestClient, run_id: str) -> None:
    new_run("safe")
    response = get(client, f"/runs/{run_id}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_a_tampered_run_is_refused(client: TestClient, state: Path) -> None:
    run = new_run("safe")
    report = state / "runs" / run / "report.json"
    report.write_text(report.read_text().replace("PASS", "FAIL", 1))
    response = get(client, f"/runs/{run}")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "verification_failed"


def test_a_report_holding_a_secret_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = new_run("safe")
    original = api.load_verified_report

    def leaky(*args: Any) -> Any:
        verified = original(*args)
        return type(verified)(verified.report, verified.text + "AF_FAKE_LEAK", verified.sha256)

    monkeypatch.setattr(api, "load_verified_report", leaky)
    response = get(client, f"/runs/{run}")
    assert response.status_code == 409
    assert "AF_FAKE_LEAK" not in response.text


def test_attempts_summary_detail_and_paged_events(client: TestClient) -> None:
    run = new_run("unsafe")
    attempts = get(client, f"/runs/{run}/attempts").json()["attempts"]
    assert len(attempts) == 10
    assert all("events" not in a and a["event_count"] > 0 for a in attempts)
    target = attempts[0]
    detail = get(client, f"/attempts/{target['attempt_id']}").json()
    assert detail["run_id"] == run
    assert detail["boundary"] == target["boundary"]
    sequences = []
    after = -1
    while True:
        page = get(
            client, f"/attempts/{target['attempt_id']}/events", params={"limit": 2, "after": after}
        ).json()
        sequences += [e["sequence"] for e in page["events"]]
        if page["next_after"] is None:
            break
        after = page["next_after"]
    assert sequences == sorted(sequences)
    assert len(sequences) == target["event_count"]


@pytest.mark.parametrize("attempt_id", ["nope", "../etc", "x" * 300])
def test_unknown_attempts_are_404(client: TestClient, attempt_id: str) -> None:
    new_run("safe")
    assert get(client, f"/attempts/{attempt_id}").status_code == 404
    assert get(client, f"/attempts/{attempt_id}/events").status_code == 404


def test_scenarios(client: TestClient) -> None:
    body = get(client, "/scenarios").json()
    assert len(body["scenarios"]) == 10
    assert {s["variant"] for s in body["scenarios"]} == {"adversarial", "benign"}


def test_compare_matches_the_cli(client: TestClient) -> None:
    safe, unsafe = new_run("safe"), new_run("unsafe")
    body = get(client, "/compare", params={"left": safe, "right": unsafe}).json()
    cli_json = CliRunner().invoke(cli.app, ["runs", "compare", safe, unsafe, "--json"]).stdout
    assert body == json.loads(cli_json)
    assert body["counts"]["REGRESSED"] == 5
    assert get(client, "/compare", params={"left": safe}).status_code == 422


# --- the command and a real server ---------------------------------------------------------


def test_ui_command_binds_loopback_and_prints_a_token(
    state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(app=app, **kw))
    result = CliRunner().invoke(cli.app, ["ui", "--port", "8555"])
    assert result.exit_code == 0, result.stderr
    assert (seen["host"], seen["port"], seen["proxy_headers"]) == ("127.0.0.1", 8555, False)
    assert seen["server_header"] is False
    token = result.stderr.rsplit(": ", 1)[1].strip()
    assert len(token) >= 43
    assert result.stdout == ""
    with TestClient(seen["app"], base_url="http://127.0.0.1:8555") as c:
        assert c.get("/api/v1/health", headers={"Authorization": f"Bearer {token}"}).is_success


@pytest.mark.parametrize(
    "args",
    [["--host", "0.0.0.0"], ["--host", "example.com"], ["--port", "80"]],  # noqa: S104 - refused
)
def test_ui_command_refuses_non_loopback_or_privileged_ports(state: Path, args: list[str]) -> None:
    result = CliRunner().invoke(cli.app, ["ui", *args])
    assert result.exit_code == 2
    assert "invalid --host or --port" in result.stderr


def test_ui_command_explains_a_missing_extra(state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins  # noqa: PLC0415

    real_import = builtins.__import__

    def no_uvicorn(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "uvicorn":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_uvicorn)
    result = CliRunner().invoke(cli.app, ["ui"])
    assert result.exit_code == 2
    assert "pip install 'agentfence-lab[ui]'" in result.stderr


def test_no_telemetry_sdk_or_exporter_is_installed() -> None:
    """FastAPI depends on opentelemetry-api, which records nothing without an SDK."""
    assert importlib.util.find_spec("opentelemetry.sdk") is None
    assert importlib.util.find_spec("opentelemetry.exporter") is None


def test_a_real_server_on_loopback(state: Path) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = uvicorn.Config(
        create_app(resolve_paths(), token=TOKEN, port=port),
        host="127.0.0.1",
        port=port,
        log_level="error",
        access_log=False,
        server_header=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started
        url = f"http://127.0.0.1:{port}/api/v1/health"
        with httpx.Client(trust_env=False) as http:
            assert http.get(url, headers=AUTH).json()["ready"] is True
            assert http.get(url).status_code == 401
            assert http.get(url, headers={**AUTH, "Host": "rebind.example"}).status_code == 403
    finally:
        server.should_exit = True
        thread.join(10)
    assert not thread.is_alive()
