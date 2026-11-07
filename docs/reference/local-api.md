# Local API (read-only)

`agentfence ui` serves the dashboard API on a loopback address (AF-0601, ADR-015). It needs
the `ui` extra:

```bash
python -m pip install -e '.[ui]'
agentfence ui --port 8484
```

The command prints a session token to stderr. Send it on every request:

```console
$ curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8484/api/v1/health
{"version":"0.1.0.dev0","ready":true,"store":true}
```

## Gates

Every request passes these before any route runs. The first one that fails decides the
response.

| Order | Check | Failure |
|---|---|---|
| 1 | `Host` is exactly the bound address with its port (`127.0.0.1:8484` by default) or `localhost:8484` | `403 host_not_allowed` |
| 2 | If `Origin` is present, it is one of those origins over `http://`. No CORS headers are ever sent | `403 origin_not_allowed` |
| 3 | `Authorization: Bearer <token>` matches exactly (constant-time) | `401 unauthorized` |
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
