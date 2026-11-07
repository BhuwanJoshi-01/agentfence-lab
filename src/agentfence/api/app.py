# SPDX-License-Identifier: Apache-2.0
"""Read-only loopback API (AF-0601; docs/06 section 7, docs/08 section 11, ADR-015).

Every request passes three gates before any route runs, in this order:

1. **Host.** The ``Host`` header must be exactly the bound loopback address with its
   port (``127.0.0.1:<port>`` by default) or ``localhost:<port>``. A page on another
   site that resolves its own name to 127.0.0.1 (DNS rebinding) sends its own host name
   and is refused.
2. **Origin.** A request that carries an ``Origin`` header must come from one of those two
   origins. Cross-origin browser requests are refused, and no CORS headers are ever sent.
3. **Session token.** Under ``/api/``, ``Authorization: Bearer <token>`` must match the
   random token ``agentfence ui`` printed when it started (constant-time comparison). The
   token lives only in that process's memory. The dashboard's own static files (``/`` and
   ``/assets/``) carry no data and need no token; ``agentfence ui`` hands the token to the
   page in the URL fragment, which browsers never send to a server or in ``Referer``.

Only ``GET`` routes exist. Nothing launches a scenario, invokes an agent or reads a path
from a request. Run data comes from the verified report (``load_verified_report``: the
report hash, the evidence hash chain and every cited evidence ID are checked) and is
scanned for secrets before it is returned, exactly as ``agentfence report`` does. Events
are alias-only by construction. Responses are ``no-store`` and carry ``nosniff`` and
``no-referrer``. API responses get a deny-all Content-Security-Policy; the dashboard page
gets a strict one (scripts, styles and connections from this origin only, nothing
inline). FastAPI's interactive docs are disabled because they load scripts from a CDN.
"""

from __future__ import annotations

import contextlib
import hmac
import json
import re
from collections.abc import Awaitable, Callable, Iterator
from importlib import resources
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from agentfence import __version__
from agentfence.core.attestation import IsolationAttestation
from agentfence.core.comparisons import compare_runs
from agentfence.core.vocabulary import SCHEMA_VERSIONS
from agentfence.reporting.export import (
    ExportError,
    VerifiedReport,
    load_verified_report,
    preflight_scan,
)
from agentfence.runtime.host_probe import assess_host, gather_host_facts
from agentfence.scenarios import bundled_scenarios
from agentfence.storage.db import RunStore, StoreError, as_dict
from agentfence.storage.paths import AppPaths

MAX_LIMIT: Final = 200
DEFAULT_LIMIT: Final = 50
_RUN_ID: Final = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_ATTEMPT_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_CURSOR: Final = re.compile(r"^([0-9T:.+\-]{10,40})~([0-9a-f-]{36})$")
SECURITY_HEADERS: Final = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "X-Frame-Options": "DENY",
}
PAGE_CSP: Final = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
STATIC: Final = resources.files("agentfence.api").joinpath("static")
_ASSET: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.(js|css)$")
_MEDIA: Final = {"js": "text/javascript; charset=utf-8", "css": "text/css; charset=utf-8"}
MAX_ATTESTATIONS: Final = 200


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message}}, status_code=status, headers=SECURITY_HEADERS
    )


def _paths(request: Request) -> AppPaths:
    paths: AppPaths = request.app.state.paths
    return paths


@contextlib.contextmanager
def _opened(paths: AppPaths) -> Iterator[RunStore | None]:
    """Open the store for one request, inside the endpoint.

    Not a yield dependency: FastAPI may run a sync dependency's setup and teardown on
    different threadpool threads, and an SQLite connection must be closed on the thread
    that opened it (found by the dashboard's end-to-end tests).
    """
    if not paths.database_path.exists():
        yield None
        return
    try:
        opened = RunStore(paths.database_path)
    except (StoreError, PermissionError) as exc:
        raise HTTPException(503, "the run store is unavailable") from exc
    with opened:
        yield opened


Paths = Annotated[AppPaths, Depends(_paths)]
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]
router = APIRouter(prefix="/api/v1")


def _verified(paths: AppPaths, db: RunStore | None, run_id: str) -> VerifiedReport:
    if db is None or not _RUN_ID.match(run_id):
        raise HTTPException(404, "no such run")
    try:
        report = load_verified_report(paths, db, run_id)
        preflight_scan(report.text)
    except ExportError as exc:
        if exc.not_found:
            raise HTTPException(404, "no such run") from exc
        raise HTTPException(409, f"run failed verification: {exc}") from exc
    return report


def _payload(paths: AppPaths, db: RunStore | None, run_id: str) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(_verified(paths, db, run_id).text)
    return payload


def _attempt(paths: AppPaths, db: RunStore | None, attempt_id: str) -> dict[str, Any]:
    run_id = db.run_for_attempt(attempt_id) if db and _ATTEMPT_ID.match(attempt_id) else None
    if run_id is None:
        raise HTTPException(404, "no such attempt")
    for attempt in _payload(paths, db, run_id)["results"]:
        if attempt["attempt_id"] == attempt_id:
            return {"run_id": run_id, **attempt}
    raise HTTPException(409, "attempt is missing from its verified report")


def _without_events(attempt: dict[str, Any]) -> dict[str, Any]:
    summary = {k: v for k, v in attempt.items() if k != "events"}
    summary["event_count"] = len(attempt.get("events", ()))
    return summary


@router.get("/health")
def health(paths: Paths) -> dict[str, Any]:
    with _opened(paths) as db:
        return {"version": __version__, "ready": True, "store": db is not None}


@router.get("/runs")
def runs(paths: Paths, limit: Limit = DEFAULT_LIMIT, cursor: str | None = None) -> dict[str, Any]:
    before = None
    if cursor is not None:
        match = _CURSOR.match(cursor)
        if not match:
            raise HTTPException(400, "malformed cursor")
        before = (match.group(1), match.group(2))
    with _opened(paths) as db:
        rows = db.list_runs(limit, before=before) if db else []
    last = rows[-1] if len(rows) == limit else None
    return {
        "runs": [as_dict(r) for r in rows],
        "next_cursor": f"{last.started_at}~{last.run_id}" if last else None,
    }


@router.get("/runs/{run_id}")
def run(run_id: str, paths: Paths) -> dict[str, Any]:
    with _opened(paths) as db:
        payload = _payload(paths, db, run_id)
    payload.pop("results", None)
    return payload


@router.get("/runs/{run_id}/attempts")
def run_attempts(run_id: str, paths: Paths) -> dict[str, Any]:
    with _opened(paths) as db:
        attempts = _payload(paths, db, run_id)["results"]
    return {"run_id": run_id, "attempts": [_without_events(a) for a in attempts]}


@router.get("/attempts/{attempt_id}")
def attempt(attempt_id: str, paths: Paths) -> dict[str, Any]:
    with _opened(paths) as db:
        return _without_events(_attempt(paths, db, attempt_id))


@router.get("/attempts/{attempt_id}/events")
def attempt_events(
    attempt_id: str,
    paths: Paths,
    limit: Limit = DEFAULT_LIMIT,
    after: Annotated[int, Query(ge=-1)] = -1,
) -> dict[str, Any]:
    with _opened(paths) as db:
        found = _attempt(paths, db, attempt_id)["events"]
    events = [e for e in found if e["sequence"] > after]
    page = events[:limit]
    return {
        "attempt_id": attempt_id,
        "events": page,
        "next_after": page[-1]["sequence"] if len(events) > limit else None,
    }


@router.get("/scenarios")
def scenarios() -> dict[str, Any]:
    rows = []
    for entry in bundled_scenarios("mvp"):
        m = entry.manifest
        rows.append(
            {
                "id": m.id,
                "version": m.version,
                "variant": m.variant.value,
                "category": m.category.value,
                "title": m.title,
                "risk_tags": list(m.risk_tags),
                "legitimate_task": m.legitimate_task.description,
                "attack_surface": (
                    {"kind": m.attack_surface.kind.value, "path": m.attack_surface.path}
                    if m.attack_surface
                    else None
                ),
                "boundary": {
                    "kind": m.boundary.kind.value,
                    "allow": list(m.boundary.allow),
                    "deny": list(m.boundary.deny),
                },
                "observation_channels": [c.value for c in m.requires.observation_channels],
                "failure_rules": [r.kind.value for r in m.verdict.failure_if],
                "benign_pair": m.benign_pair,
                "sha256": entry.sha256,
            }
        )
    return {"schema_version": SCHEMA_VERSIONS["scenario"], "profile": "mvp", "scenarios": rows}


def _latest_attestation(paths: AppPaths) -> tuple[IsolationAttestation | None, bool]:
    """Newest well-formed attestation under the state directory, and whether it verifies."""
    folder = paths.state_dir / "attestations"
    if not folder.is_dir():
        return None, False
    found: list[IsolationAttestation] = []
    for item in sorted(folder.glob("*.json"))[-MAX_ATTESTATIONS:]:
        if item.is_symlink() or not item.is_file() or item.stat().st_size > 256 * 1024:
            continue
        try:
            found.append(IsolationAttestation.model_validate_json(item.read_bytes()))
        except ValueError:
            continue
    if not found:
        return None, False
    latest = max(found, key=lambda a: a.created_at)
    return latest, latest.verify_id()


@router.get("/environment")
def environment(paths: Paths) -> dict[str, Any]:
    """The read-only doctor checks plus the newest self-test attestation, if any."""
    caps = assess_host(gather_host_facts())
    attestation, verified = _latest_attestation(paths)
    return {
        "isolation_state": caps.isolation_state.value,
        "checks": [c.model_dump(mode="json") for c in caps.checks],
        "blocking_checks": [c.id for c in caps.blocking_checks],
        "attestation": attestation.model_dump(mode="json") if attestation else None,
        "attestation_verified": verified,
        "attestation_passed": bool(attestation and verified and attestation.passed),
    }


@router.get("/compare")
def compare(left: str, right: str, paths: Paths) -> dict[str, Any]:
    with _opened(paths) as db:
        baseline = _verified(paths, db, left).report
        candidate = _verified(paths, db, right).report
    return compare_runs(baseline, candidate).as_dict()


pages = APIRouter()


def _static(name: str) -> bytes | None:
    resource = STATIC.joinpath(name)
    return resource.read_bytes() if resource.is_file() else None


@pages.get("/")
def index() -> Response:
    body = _static("index.html")
    if body is None:
        raise HTTPException(404, "the dashboard is not built; the API is still available")
    return Response(body, media_type="text/html; charset=utf-8")


@pages.get("/assets/{name}")
def asset(name: str) -> Response:
    match = _ASSET.match(name)
    body = _static(f"assets/{name}") if match else None
    if match is None or body is None:
        raise HTTPException(404, "no such asset")
    return Response(body, media_type=_MEDIA[match.group(1)])


_CODES: Final = {
    400: "bad_request",
    404: "not_found",
    409: "verification_failed",
    422: "invalid_parameter",
    503: "unavailable",
}


def create_app(paths: AppPaths, *, token: str, port: int, listen: str = "127.0.0.1") -> FastAPI:
    """The API for one ``agentfence ui`` session. ``token`` must be long and random.

    ``listen`` is the loopback address the server is bound to; requests must name it, or
    ``localhost``, in their ``Host`` header.
    """
    if len(token) < 32:
        raise ValueError("session token is too short")
    bound = f"[{listen}]" if ":" in listen else listen
    allowed_hosts = {f"{bound}:{port}", f"localhost:{port}"}
    allowed_origins = {f"http://{host}" for host in allowed_hosts}
    expected = f"Bearer {token}".encode()
    app = FastAPI(
        title="AgentFence Lab local API",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.paths = paths
    app.include_router(router)
    app.include_router(pages)

    @app.middleware("http")
    async def gatekeeper(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.headers.get("host") not in allowed_hosts:
            return _error(403, "host_not_allowed", "requests must address the loopback host")
        origin = request.headers.get("origin")
        if origin is not None and origin not in allowed_origins:
            return _error(403, "origin_not_allowed", "cross-origin requests are refused")
        is_api = request.url.path.startswith("/api/")
        presented = request.headers.get("authorization", "").encode()
        if is_api and not hmac.compare_digest(presented, expected):
            return _error(401, "unauthorized", "a valid session token is required")
        if request.method not in {"GET", "HEAD"}:
            return _error(405, "read_only", "the local API is read-only")
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        if not is_api and response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Content-Security-Policy"] = PAGE_CSP
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _error(exc.status_code, _CODES.get(exc.status_code, "error"), str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def invalid_parameter(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = sorted({".".join(str(p) for p in e.get("loc", ())) for e in exc.errors()})
        return _error(422, "invalid_parameter", f"invalid parameter: {', '.join(fields)}")

    return app
