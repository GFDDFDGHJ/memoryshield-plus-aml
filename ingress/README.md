# Shared Textual / Coding transport

Gateway **v1.1.3** exposes the same original URLs for both tracks:

- `https://hlq5070.tail50f4d4.ts.net/add`
- `https://hlq5070.tail50f4d4.ts.net/search`
- `https://hlq5070.tail50f4d4.ts.net/health`

The original Memory System Key follows an explicit operator-owned local mode:
`textual` selects the locally configured Textual release; `coding` selects Coding on port 8181.
The prepared Textual submission uses **`textual`**. Select `coding` only for a separate Coding evaluation after all evaluations have ended. The original Key and the
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
Forwarding uses only the three named Textual destinations below and the fixed
Coding loopback target, no environment proxy, no redirects and
bounded network timeouts. Content-Type and Retry-After are preserved; transport
headers are rebuilt to avoid stale length/encoding metadata.

Anonymous `/health` returns the existing Textual health response. Original-Key
health reflects the current local mode; dedicated Coding-Key health returns
Coding health. `/__gateway_health` reports the gateway version, current/requested
legacy mode, configuration validity and active request counts without calling
models or exposing credentials. Confirm its live mode before every evaluation.

## Select the Textual release locally

The optional operator-owned `textual_backend.json` is read once at gateway
startup. When absent, Textual continues to use `legacy_v3140`, the exact
`http://127.0.0.1:8080` destination. To select the existing isolated GPT backend,
the complete file must be:

```json
{"textual_backend":"gpt_v3160"}
```

`gpt_v3160` maps only to `http://100.93.38.9:8380` over this machine's Tailscale
interface. No arbitrary URL, environment variable or request field can alter
that mapping. A malformed, unreadable or unknown selection prevents startup;
it never falls back to the old service after an upstream failure. The backend
still receives the original Textual Memory System Key. Coding's fixed target
and credential, and the original Key's local Textual/Coding mode, are unchanged.

Change this private file only after all evaluations have completed and active
gateway requests have drained, then restart only the gateway. Verify
`/__gateway_health` reports `version=v1.1.3`, `textual_backend=gpt_v3160` and
`textual_version=v3.16.0`; verify `/health` and invented Add/Search end to end
before starting an evaluation. These safe health fields expose no credentials.
Editing the file does not change an already running process. To roll back,
select `legacy_v3140` (or remove only this optional file), restart the gateway
while idle, and verify the resulting release. Neither action stops, migrates,
copies, or modifies either Textual backend or its memory database. Backend
version names in gateway health are the configured release, not a live probe.

Run `python -m unittest discover -p "test_*.py"` here for isolated fake-upstream
and temporary-file checks. This directory reuses the project's existing FastAPI,
httpx and uvicorn dependencies. Deployment credentials remain in
`../.secrets/aml_auth_token.txt` and `../coding/.secrets/auth_token.txt`.

Run `watcher.ps1` hidden to supervise only the gateway. It checks the exact
Funnel 443 destination every 20 seconds, restoring 8282 after configuration
drift or the legacy Textual supervisor's historical 8080 recovery fallback.
Textual and Coding source, processes and memory databases remain independent.
The gateway is their shared public transport dependency.

## Textual Add admission

After authentication and explicit track selection, only Textual `/add` requests
enter a FIFO transport queue. At most two are forwarded at once, matching the
unchanged v3.16.0 GPT extractor's concurrency limit. At most 64 requests wait;
each can wait up to 600 seconds before receiving a sanitized 503 with
`Retry-After: 5`. This reduces overload retries without increasing model
concurrency or changing memory extraction, retrieval, credentials, routing or
database contents. Search, Coding, health and authentication failures do not
enter this queue. Queues and limits are local to the single gateway worker.

Original body bytes and request IDs pass through unchanged. The gateway does
not parse request contents, acknowledge before the backend commits, persist
queued bodies, retry requests or coalesce duplicates. Existing backend receipts
remain responsible for request-ID replay and conflict detection. Queue length
is bounded by request count; admitted HTTP bodies still occupy memory.

A disconnected queued client is removed before forwarding. Cancellation of an
active gateway handler keeps its admission slot and legacy-mode lease until
the outstanding upstream HTTP operation completes; the backend's synchronous
model thread cannot be reliably cancelled by a client disconnect. Ordinary
upstream exceptions release the slot. Existing HTTPX limits remain unchanged:
connect 3 seconds, write 60 seconds, pool wait 10 seconds and read 300 seconds.
The 300 seconds is an I/O timeout, not proof that backend model work has stopped.
An upstream timeout can therefore still temporarily leave model work running;
provider errors, store lock collisions and direct backend callers can also
still cause 503. Admission is a capacity repair, not a throughput or completion
guarantee. A queue wait of 600 seconds plus the normal buffered upstream HTTP
exchange fits under the documented 1800-second evaluator I/O timeout.

The safe operator health response adds `text_add_active`, `text_add_waiting`,
`text_add_concurrency`, `text_add_max_waiting` and
`text_add_wait_timeout_seconds`. Existing fields are preserved. Stop or restart
the gateway only when the evaluation has stopped and all active/queued counts
have drained; queued requests are not durable across process restarts.

Coding **v4.1.1** adds this original-Key compatibility adjustment. Its memory
algorithms are unchanged from v4.1.0. Both directories are published at one
fixed Git commit; separate source archives cover the backend and gateway.

## Prepared v3.17.0 switch (not executed by this source candidate)

Gateway v1.1.3 adds one fixed named mapping:

| Local selector | Fixed Textual destination | Configured method version |
| --- | --- | --- |
| `legacy_v3140` | `http://127.0.0.1:8080` | `v3.14.0` |
| `gpt_v3160` | `http://100.93.38.9:8380` | `v3.16.0` |
| `gpt_v3170` | `http://100.93.38.9:8480` | `v3.17.0` |

`/__gateway_health` retains `textual_version` and adds `method_version` as an
explicit alias for the configured Textual method. It is a local configuration
declaration, not a live probe or a claim about the current legacy Coding mode.
Confirm the independent backend `/health` separately. Authentication, Coding
isolation, FIFO two-active/64-waiting Add admission and all timeouts are unchanged.
The watcher and local legacy-mode CLI are both pinned to gateway v1.1.3.

Minimal cutover procedure for the operator, only after active requests and
queue counters have drained and the evaluation is no longer writing:

1. Verify the new independent backend at `100.93.38.9:8480` reports v3.17.0 and
   `add_selection_failure_policy=raw_on_unmatched`; retain the existing original
   Memory System Key. Do not copy or migrate the v3.16.0 evaluation database.
   The v3.17.0 backend uses its own new `aml_text_gpt_v317.db`; changing this
   gateway mapping does not migrate any data or platform checkpoint and does
   not establish compatibility with a previous evaluation checkpoint.
2. Back up the current gateway source, watcher, local CLI and private selector
   together outside any public source archive. Stop only the gateway supervisor
   and gateway, leaving both Textual backend processes/databases untouched.
3. Install this gateway source and matching v1.1.3 watcher/CLI. Atomically replace
   the private `textual_backend.json` with `{"textual_backend":"gpt_v3170"}`.
   Keep the original `legacy_target.json` in Textual mode. Restart only the
   gateway supervisor hidden. Funnel 443 remains pointed at loopback port 8282;
   public URLs and credentials do not change.
4. Verify internal health shows gateway v1.1.3, `gpt_v3170`, method v3.17.0 and
   zero queue/active counters. Verify original public `/health` independently
   shows the actual backend v3.17.0. Any synthetic Add/Search check must use its
   own invented namespace. This procedure never starts or resumes a platform
   evaluation; the participant controls that separately.

Rollback while drained: keep gateway v1.1.3 and atomically restore selector
`{"textual_backend":"gpt_v3160"}`, then restart only the gateway. Confirm
method v3.16.0 and the old backend health. The v3.16.0 mapping is deliberately
retained; no automatic fallback occurs on a v3.17.0 error. If restoring the
older gateway binaries instead, restore their matching watcher and CLI as a set.

The local staged selector is private runtime configuration and is not included
by `prepare_submission.py`. This directory has made no production changes.
