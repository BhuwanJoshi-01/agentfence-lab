# SPDX-License-Identifier: Apache-2.0
"""Read-only loopback API (AF-0601; docs/06 section 7, docs/08 section 11, ADR-015).

Every request passes three gates before any route runs, in this order:

1. **Host.** The ``Host`` header must be exactly the bound loopback address with its
   port (``127.0.0.1:<port>`` by default) or ``localhost:<port>``. A page on another
   site that resolves its own name to 127.0.0.1 (DNS rebinding) sends its own host name
   and is refused.
2. **Origin.** A request that carries an ``Origin`` header must come from one of those two
   origins. Cross-origin browser requests are refused, and no CORS headers are ever sent.
3. **Session token.** ``Authorization: Bearer <token>`` must match the random token
   ``agentfence ui`` printed when it started (constant-time comparison). The token lives
   only in that process's memory.

Only ``GET`` routes exist. Nothing launches a scenario, invokes an agent or reads a path
from a request. Run data comes from the verified report (``load_verified_report``: the
report hash, the evidence hash chain and every cited evidence ID are checked) and is
scanned for secrets before it is returned, exactly as ``agentfence report`` does. Events
are alias-only by construction. Responses are ``no-store`` and carry ``nosniff``,
``no-referrer`` and a deny-all Content-Security-Policy. FastAPI's interactive docs are
disabled because they load scripts from a CDN.
"""

from __future__ import annotations

import hmac
import json
import re
from collections.abc import Awaitable, Callable, Iterator
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from agentfence import __version__
from agentfence.core.comparisons import compare_runs
from agentfence.core.vocabulary import SCHEMA_VERSIONS
from agentfence.reporting.export import (
    ExportError,
    VerifiedReport,
    load_verified_report,
    preflight_scan,
)
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


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message}}, status_code=status, headers=SECURITY_HEADERS
    )


def _paths(request: Request) -> AppPaths:
    paths: AppPaths = request.app.state.paths
    return paths


def _store(request: Request) -> Iterator[RunStore | None]:
    database = _paths(request).database_path
    if not database.exists():
        yield None
        return
    try:
        opened = RunStore(database)
    except (StoreError, PermissionError) as exc:
        raise HTTPException(503, "the run store is unavailable") from exc
    with opened:
        yield opened


Store = Annotated[RunStore | None, Depends(_store)]
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
def health(db: Store) -> dict[str, Any]:
    return {"version": __version__, "ready": True, "store": db is not None}


@router.get("/runs")
def runs(db: Store, limit: Limit = DEFAULT_LIMIT, cursor: str | None = None) -> dict[str, Any]:
    before = None
    if cursor is not None:
        match = _CURSOR.match(cursor)
        if not match:
            raise HTTPException(400, "malformed cursor")
        before = (match.group(1), match.group(2))
    rows = db.list_runs(limit, before=before) if db else []
    last = rows[-1] if len(rows) == limit else None
    return {
        "runs": [as_dict(r) for r in rows],
        "next_cursor": f"{last.started_at}~{last.run_id}" if last else None,
    }


@router.get("/runs/{run_id}")
def run(run_id: str, db: Store, paths: Paths) -> dict[str, Any]:
    payload = _payload(paths, db, run_id)
    payload.pop("results", None)
    return payload


@router.get("/runs/{run_id}/attempts")
def run_attempts(run_id: str, db: Store, paths: Paths) -> dict[str, Any]:
    attempts = _payload(paths, db, run_id)["results"]
    return {"run_id": run_id, "attempts": [_without_events(a) for a in attempts]}


@router.get("/attempts/{attempt_id}")
def attempt(attempt_id: str, db: Store, paths: Paths) -> dict[str, Any]:
    return _without_events(_attempt(paths, db, attempt_id))


@router.get("/attempts/{attempt_id}/events")
def attempt_events(
    attempt_id: str,
    db: Store,
    paths: Paths,
    limit: Limit = DEFAULT_LIMIT,
    after: Annotated[int, Query(ge=-1)] = -1,
) -> dict[str, Any]:
    events = [e for e in _attempt(paths, db, attempt_id)["events"] if e["sequence"] > after]
    page = events[:limit]
    return {
        "attempt_id": attempt_id,
        "events": page,
        "next_after": page[-1]["sequence"] if len(events) > limit else None,
    }


@router.get("/scenarios")
def scenarios() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSIONS["scenario"],
        "profile": "mvp",
        "scenarios": [
            {
                "id": e.manifest.id,
                "version": e.manifest.version,
                "variant": e.manifest.variant.value,
                "category": e.manifest.category.value,
                "title": e.manifest.title,
                "benign_pair": e.manifest.benign_pair,
                "sha256": e.sha256,
            }
            for e in bundled_scenarios("mvp")
        ],
    }


@router.get("/compare")
def compare(left: str, right: str, db: Store, paths: Paths) -> dict[str, Any]:
    baseline = _verified(paths, db, left).report
    candidate = _verified(paths, db, right).report
    return compare_runs(baseline, candidate).as_dict()


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

    @app.middleware("http")
    async def gatekeeper(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.headers.get("host") not in allowed_hosts:
            return _error(403, "host_not_allowed", "requests must address the loopback host")
        origin = request.headers.get("origin")
        if origin is not None and origin not in allowed_origins:
            return _error(403, "origin_not_allowed", "cross-origin requests are refused")
        presented = request.headers.get("authorization", "").encode()
        if not hmac.compare_digest(presented, expected):
            return _error(401, "unauthorized", "a valid session token is required")
        if request.method not in {"GET", "HEAD"}:
            return _error(405, "read_only", "the local API is read-only")
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _error(exc.status_code, _CODES.get(exc.status_code, "error"), str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def invalid_parameter(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = sorted({".".join(str(p) for p in e.get("loc", ())) for e in exc.errors()})
        return _error(422, "invalid_parameter", f"invalid parameter: {', '.join(fields)}")

    return app
