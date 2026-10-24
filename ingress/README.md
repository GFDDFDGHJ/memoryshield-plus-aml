# Shared Textual / Coding transport

Gateway **v1.1.0** exposes the same original URLs for both tracks:

- `https://hlq5070.tail50f4d4.ts.net/add`
- `https://hlq5070.tail50f4d4.ts.net/search`
- `https://hlq5070.tail50f4d4.ts.net/health`

The original Memory System Key follows an explicit operator-owned local mode:
`textual` selects Textual on port 8080; `coding` selects Coding on port 8181.
This release's Coding evaluation mode is **`coding`**. The original Key and the
public URLs do not change, and the organizer need not replace the Key. The
gateway supplies the selected backend's separate internal credential. The
dedicated Coding Key always selects Coding, regardless of the original Key's
mode. Funnel 443 forwards to this loopback-only gateway on 8282.

Shared-Key compatibility is for **one track at a time**. It does not support
simultaneous Textual and Coding evaluations under the original Key. Request
content, track fields, user IDs, query strings and forwarding headers cannot
select or change the backend. Requests are never sent to both memory systems.
Unknown or conflicting credentials are rejected before any upstream request.
No memory database, model or content-based routing is used in this directory.
Request bodies, queries and memories are not logged as payloads.

## Select the local mode

Only switch after **all Smoke and Full evaluations have completed**. An idle
HTTP request counter does not prove that an evaluation has finished: an
evaluation may be waiting between Add and Search requests. The `--evaluation-idle`
flag is the operator's explicit confirmation of this condition.

From this directory, use:

```powershell
python set_legacy_target.py coding --evaluation-idle
```

Before the next Textual evaluation, wait for every evaluation to finish, then use:

```powershell
python set_legacy_target.py textual --evaluation-idle
```

The CLI writes `legacy_target.json` atomically through a temporary file in the
same directory and uses `legacy_target.switch.lock` to serialize local changes.
These are private deployment settings, excluded from the source archive. The
CLI checks the gateway before the change and verifies the resulting live mode.
If verification fails after the atomic write, it reports failure without
silently rolling the configuration back; inspect `/__gateway_health` before
starting any evaluation. The
gateway must run with **one worker**, because active request leases are process
local; `watcher.ps1` explicitly starts uvicorn with `--workers 1`. Do not add
workers or override this through `WEB_CONCURRENCY`.

Initialize the local configuration before using the original Key. Missing,
unreadable or invalid configuration makes original-Key requests fail closed
with **503**; the dedicated Coding Key and anonymous Textual health remain
independent. Each original-Key POST retains its selected mode through the whole
request, including error paths. If a different mode is requested while such a
lease is active, new original-Key POSTs receive **503** with `Retry-After: 1`;
they do not fall back to another backend. After the leases finish, the valid
new local mode takes effect.

## Transport and health

The request body and decoded backend response entity/status are relayed unchanged.
Forwarding uses fixed loopback targets, no environment proxy, no redirects and
bounded network timeouts. Content-Type and Retry-After are preserved; transport
headers are rebuilt to avoid stale length/encoding metadata.

Anonymous `/health` returns the existing Textual health response. Original-Key
health reflects the current local mode; dedicated Coding-Key health returns
Coding health. `/__gateway_health` reports the gateway version, current/requested
legacy mode, configuration validity and active request counts without calling
models or exposing credentials. Confirm its live mode before every evaluation.

Run `python -m unittest discover -p "test_*.py"` here for isolated fake-upstream
and temporary-file checks. This directory reuses the project's existing FastAPI,
httpx and uvicorn dependencies. Deployment credentials remain in
`../.secrets/aml_auth_token.txt` and `../coding/.secrets/auth_token.txt`.

Run `watcher.ps1` hidden to supervise only the gateway. It checks the exact
Funnel 443 destination every 20 seconds, restoring 8282 after configuration
drift or the legacy Textual supervisor's historical 8080 recovery fallback.
Textual and Coding source, processes and memory databases remain independent.
The gateway is their shared public transport dependency.

Coding **v4.1.1** adds this original-Key compatibility adjustment. Its memory
algorithms are unchanged from v4.1.0. Both directories are published at one
fixed Git commit; separate source archives cover the backend and gateway.
