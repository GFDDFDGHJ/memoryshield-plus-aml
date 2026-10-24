# Shared Textual / Coding transport

Gateway v1.0.0 exposes the same original URLs for both tracks:

- `https://hlq5070.tail50f4d4.ts.net/add`
- `https://hlq5070.tail50f4d4.ts.net/search`
- `https://hlq5070.tail50f4d4.ts.net/health`

The Memory System Key selects the backend. Textual keeps its existing key and
port 8080; Coding uses its separate key and port 8181. Funnel 443 forwards to
this loopback-only gateway on 8282. Track headers, user IDs, query strings and
payload content cannot select a backend. Unknown or conflicting credentials
are rejected before an upstream request. There are no databases or model calls
in this directory, and requests and responses are not logged as payloads.

The request body and decoded backend response entity/status are relayed unchanged.
Forwarding uses only fixed loopback targets, no environment proxy, no redirects
and bounded network timeouts. Content-Type and Retry-After are preserved;
transport headers are rebuilt to avoid stale length/encoding metadata.

Without a key, public `/health` returns the existing Textual health response for
compatibility. With the Coding key it returns Coding health. The gateway's own
readiness is `/__gateway_health`, independent of backend model/network calls.

Run `python -m unittest discover -p "test_*.py"` here for isolated fake-upstream
checks. This directory reuses the project's existing FastAPI, httpx and uvicorn
dependencies. At deployment, the gateway reads only the two dedicated auth files
at `../.secrets/aml_auth_token.txt` and `../coding/.secrets/auth_token.txt`.

Run `watcher.ps1` hidden to supervise only the gateway process. It checks the
exact Funnel 443 destination every 20 seconds, restoring 8282 after accidental
configuration drift or the legacy Textual supervisor's historical 8080 recovery
fallback. The Textual source, running process and database remain unchanged.
The gateway is a shared transport dependency; both backends remain independent.

Coding v1.0.1 includes this URL routing adjustment. The memory algorithms are
identical to Coding v1.0.0. Both directories are published at one fixed Git
commit. Separate source archives cover the backend and this gateway.
