# ADR-015: Read-only local API on FastAPI, as an optional `ui` extra

- **Status:** accepted
- **Date:** 2026-11-07
- **Task:** AF-0601
- **Related:** docs/06 section 7, docs/08 sections 11-12, ADR-013 (small dependency tree),
  ADR-014 (license compatibility), TB-24 in `docs/reference/trust-boundaries.md`

## Context

The dashboard (M6) needs a local HTTP API. docs/06 section 7 fixes the contract: read-only,
loopback only, random session authorization, no cross-origin requests, no route that
launches anything or reads a host path. docs/08 section 12 plans to generate the
dashboard's TypeScript types from FastAPI schemas.

The CLI does not need a web server. ADR-013 kept the core dependency tree small for the
same reason: every dependency has to be pinned, audited and license-checked.

## Decision

1. Use FastAPI on uvicorn, as the blueprint proposes. A hand-rolled `http.server` would save
   dependencies, but the standard library documents it as not meant for production use.
   We would then have to get request parsing, header handling and concurrency right
   ourselves, for a component a browser talks to.
2. Ship them as an **optional extra**: `pip install 'agentfence-lab[ui]'`. The core install
   does not change. `agentfence ui` imports them lazily and explains how to install the
   extra when they are missing.
3. Gate every request before any route runs, in this order: exact `Host` (the bound
   loopback address or `localhost`, which defeats DNS rebinding), `Origin` (same origin
   only, and no CORS headers ever), then a constant-time check of a random bearer token
   that exists only in the server process's memory. Methods other than `GET`/`HEAD` are
   refused.
4. Serve run data only from the verified report, the same path `agentfence report` uses,
   and run the preflight secret scan on it. FastAPI's `/docs`, `/redoc` and
   `/openapi.json` are disabled, because the docs pages load scripts from a CDN.
5. Bind with `proxy_headers=False` and no `Server` header. Every response is `no-store`,
   `nosniff`, `no-referrer` and `frame-ancestors 'none'`.

## Consequences

- The `ui` extra adds FastAPI, Starlette, uvicorn, anyio, h11, click, idna,
  typing-inspection, annotated-doc and `opentelemetry-api` (FastAPI 0.162 requires it). All
  of them are MIT, BSD-3-Clause or Apache-2.0. The license test and the CI audit and SBOM
  now cover the extra.
- `opentelemetry-api` without an SDK and exporter is a no-op: it records and sends nothing.
  AgentFence installs neither, and `test_local_api.py::test_no_telemetry_sdk_or_exporter_is_installed`
  checks that. If a future FastAPI release pulls in an SDK or exporter, that test fails.
- The token is printed once to stderr. Anyone who can read that terminal can use the API.
  That is acceptable on a single-user machine, and it is the reason the API is read-only.
- The browser dashboard gets the token in the URL **fragment** (`/#token=…`). Fragments are
  sent neither to servers nor in `Referer`. The page moves the token into the tab's
  `sessionStorage` and replaces the history entry, so the token doesn't stay in the address
  bar or the back history. Static files need no token because they carry no data. Every
  data request goes to `/api/v1` with the bearer header (AF-0602, AF-0607).
- Store connections are opened and closed inside each endpoint. A FastAPI yield dependency
  may tear down on a different threadpool thread, and SQLite connections must be closed on
  the thread that opened them. The dashboard's end-to-end tests found this as 500s under
  parallel requests.
