# Local API and dashboard (read-only)

`agentfence ui` serves the dashboard and its API on a loopback address (AF-0601 to AF-0607,
ADR-015). It needs the `ui` extra:

```bash
python -m pip install -e '.[ui]'
agentfence ui --port 8484
```

The command prints the dashboard URL to stderr, for example
`http://127.0.0.1:8484/#token=…`. The token sits in the URL fragment, which browsers send
neither to the server nor in `Referer`. The page (`dashboard/src/api.ts`) moves it into
the tab's `sessionStorage`, removes it from the address bar and history, and sends it only
as `Authorization: Bearer` to `/api/v1`, with `credentials: "omit"` and no referrer. API
clients use the same token:

```console
$ curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8484/api/v1/health
{"version":"0.1.0b1","ready":true,"store":true}
```

## Gates

Every request passes these before any route runs. The first one that fails decides the
response.

| Order | Check | Failure |
|---|---|---|
| 1 | `Host` is exactly the bound address with its port (`127.0.0.1:8484` by default) or `localhost:8484` | `403 host_not_allowed` |
| 2 | If `Origin` is present, it is one of those origins over `http://`. No CORS headers are ever sent | `403 origin_not_allowed` |
| 3 | Under `/api/`: `Authorization: Bearer <token>` matches exactly (constant-time). The dashboard's static files carry no data and need no token | `401 unauthorized` |
| 4 | The method is `GET` or `HEAD` | `405 read_only` |

`--host` must be a loopback address and `--port` must be 1024 or higher. Both go through the
same validation as `ui.listen` and `ui.port` in `agentfence.yaml`.

## Routes

All routes are under `/api/v1`.

| Route | Returns |
|---|---|
| `GET /health` | version, `ready`, whether a run store exists |
| `GET /runs?limit=&cursor=` | run summaries, newest first; `next_cursor` continues the list (`limit` 1 to 200, default 50) |
| `GET /runs/{run_id}` | the verified report without its per-attempt results |
| `GET /runs/{run_id}/attempts` | per-attempt verdicts with `event_count`, without events |
| `GET /attempts/{attempt_id}` | one attempt's verdict and its `run_id` |
| `GET /attempts/{attempt_id}/events?limit=&after=` | alias-only events in sequence order; `next_after` continues |
| `GET /scenarios` | bundled scenarios with versions and manifest hashes |
| `GET /compare?left=&right=` | the same payload as `agentfence runs compare --json` |
| `GET /environment` | the read-only doctor checks, plus the newest self-test attestation and whether it verifies |

## Dashboard

`GET /` serves `index.html` and `GET /assets/<name>.js|css` serves the build, both from
`agentfence/api/static/` inside the installed package. Nothing else is served (no
directory listing, no other file types). The page gets
`Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self';
connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none';
frame-ancestors 'none'`: no inline scripts or styles, nothing from another origin.

The sources are in `dashboard/` (React 19, TypeScript, Vite). The build is committed, and CI
rebuilds it and fails if the result differs from the committed files. Only `react`,
`react-dom` and `scheduler` ship in it, all MIT (`npm run licenses`). The screens follow
docs/08: runs, run detail (scenario matrix with outcome and text filters), attempt
(timeline with trust tags, attempted vs completed), compare (with `INCOMPARABLE`),
scenario catalog, environment, and about/limitations.

Tests: `dashboard/tests/unit.test.tsx` covers the token handoff, routes and inert rendering
of hostile text. `dashboard/e2e/dashboard.spec.ts` runs Playwright against a real
`agentfence ui` on desktop and mobile: every screen, keyboard navigation, no sideways
scrolling, no console errors or CSP violations, and axe with WCAG 2.2 AA and best-practice
rules on every page. axe is a baseline, not proof of accessibility (docs/08 section 10).


Run data is served only after the same checks `agentfence report` makes: the report hash,
the evidence hash chain and every cited evidence ID, followed by the preflight secret scan.
A run that fails any of them returns `409 verification_failed`. An unknown or malformed ID
returns `404`.

Errors have one shape: `{"error": {"code": "...", "message": "..."}}`. Every response
carries `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`,
`Referrer-Policy: no-referrer`, `X-Frame-Options: DENY` and
`Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`.

## Differences from the docs/06 sketch

- There is no `/evidence/{evidence_id}` route. Events already carry what evidence is
  shown, and a separate route would add a lookup by ID without adding information.
- Run paging uses a keyset cursor (`started_at~run_id`), validated on input, not a signed
  token. It is stable when start times tie.

Tests: `test_local_api.py::test_foreign_host_headers_are_refused`,
`test_local_api.py::test_cross_origin_requests_are_refused`,
`test_local_api.py::test_session_token_is_required_exactly`,
`test_local_api.py::test_the_api_is_read_only`,
`test_local_api.py::test_a_tampered_run_is_refused`,
`test_local_api.py::test_a_real_server_on_loopback`.
