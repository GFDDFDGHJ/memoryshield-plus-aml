# MemoryShield+ — Textual candidate v3.16.0

**Deploying on another Windows computer? Start with [DEPLOYMENT.txt](DEPLOYMENT.txt).**
Use `ops/run-portable.ps1` for that installation: localhost port 18080,
an independent `data/` database, private credentials and pinned Textual models.
The historical deployment scripts below target the existing machines.

**Candidate v3.16.0 is deployed at the original hlq5070 URL and at hlq-1.
Independent public health and invented Add/Search checks verify connectivity;
no matching official Smoke or completed Full result is available.**
The original v3.14.0 backend remains on local port 8080 for rollback, while
gateway v1.1.2 routes the original public URL to the separate GPT backend.
Historical Smoke results describe the earlier implementation, not this GPT candidate.
The Chinese Full checklist explicitly requires
gpt-4o-mini during Add; the old LLM-free service does not meet that condition.

The independent observability patch **v3.16.1** adds only fixed, allowlisted
failure-category counters and audit events. The API/memory-method version
remains v3.16.0; health exposes the diagnostic revision separately as
`observability_version`. No source content, IDs, credentials or provider error
bodies are included in the new failure events. The diagnostic patch preserves
the original success and 503 responses, model selection, extraction rules,
database and routing. It does not claim to repair the unresolved Full failure.

## Method

Every new valid Add invokes official **gpt-4o-mini** for durable memory selection.
Complete source inputs are split into batches of at most 12,000 characters,
with overlap for long messages. Each call selects at most four literal quotes,
each at most 240 characters. Exact substring matching supplies original offsets;
source owner, role, session, content and hash are validated again before storage.
Generated replacement facts are never stored. All original messages remain.

Raw records, text-embedding-v4 vectors, deterministic facts, GPT-selected spans,
model/token metadata and request receipt commit in one SQLite transaction.
Failed/refused/truncated or invalid GPT responses cannot acknowledge Add success.
Committed request replays avoid further GPT/embedding calls; conflicting payload
or session IDs return 409. Bounded per-process locks coalesce concurrent retries.

Search returns evidence without generative calls or final answers. It retains
v3.14.0 rules, state history, source-context and extractive-summary retrieval.
Query overlap with GPT-selected spans adds at most 0.03 to ordinary raw-source
ranking. Current/original-state and chronology queries retain their ordering
guards. Existing privacy/exclusion filters apply first; evidence wording and
provenance remain original. Optional bounded qwen3-rerank is retained.

Models: official gpt-4o-mini during Add; DashScope text-embedding-v4, standard,
1024 dimensions, batches of at most ten; optional qwen3-rerank. No DeepSeek/Ark
substitution is used. Original Textual algorithm baseline:
`db43d67ea1728497ab7b5418a2fd9c62a5c29b71`; publication baseline:
`d63976daff1f8533c883a9ef25e5861a0ba709e3`.

## Configuration and deployment

Install requirements.txt. Configure OPENAI_API_KEY, DASHSCOPE_API_KEY (or
EMBEDDING_API_KEY) and AML_AUTH_TOKEN, or the private .secrets files supported
in config.py. The GPT endpoint/model are fixed to https://api.openai.com/v1
and gpt-4o-mini. Never put credentials in Git or command arguments. Windows
launchers refresh User keys, then Machine keys, before starting children.
Missing GPT credentials make health return 503/not-ready. Health does not
call the model or verify quota. The new database is **aml_text_gpt_v316.db**;
old Textual and Coding stores are neither migrated nor deleted.

The website-bound endpoints and original Memory System Key remain unchanged:

- https://hlq5070.tail50f4d4.ts.net/add
- https://hlq5070.tail50f4d4.ts.net/search
- https://hlq5070.tail50f4d4.ts.net/health

Gateway v1.1.2 on loopback 8282 uses the local startup-only selector
`{"textual_backend":"gpt_v3160"}` to reach the fixed GPT backend at
100.93.38.9:8380. Only two named, fixed destinations are accepted; callers
cannot select a version or URL. Invalid configuration prevents startup, and
upstream failure never falls back to the old Textual or Coding store.
The existing legacy-key mode remains `textual`, with Coding credentials and
storage isolated. The original Key is forwarded unchanged to the GPT backend.
Gateway source, tests and rollback instructions are in `ingress/` in the
fixed GitHub revision. The portable Textual ZIP does not need this existing
machine's shared gateway; its launcher provides a standalone local service.

The October 31 transport recovery adds a bounded FIFO for authenticated
Textual Add requests: two active forwards, at most 64 waiting requests,
and a 600-second admission deadline followed by the existing upstream I/O
timeout. It preserves the original request bytes, IDs, credentials, backend,
model calls and database. Search, Coding and health bypass this queue.
Successful Add is still returned only after the backend commits. Full
recovery reuses the existing task and compatible checkpoint; the gateway
does not create, resume or cancel evaluations. This is a transport-capacity
repair, not a new memory method or a completed Full capacity certification.

The additional hlq-1 entry is a separate relay: hlq-1 Funnel 443 -> its loopback 8380
portproxy -> WireGuard tailnet -> hlq5070 100.93.38.9:8380, bound only on the
Tailscale interface. ops/configure-hlq1-ingress.ps1 runs on the owning hlq-1
Windows node with Administrator PowerShell, backs up scoped configuration,
refuses conflicting routes by default and supports -Rollback. For the exact
existing hlq-1 root proxy to 127.0.0.1:8080, -MigrateLegacy8080 backs it up and
replaces only that root; rollback restores it. Other ports/routes are preserved.
It never reads API keys.
ops/watch-isolated-textual.ps1 supervises the independent backend hidden with
persisted provider keys and the original Memory System Key. Its runtime is
deployments/textual-v3.16.0 with its own database and logs. Keep both machines
online and awake. Public Add/Search, replay/conflict handling, authentication,
Top K and user isolation passed on both currently published Funnel IPv4
addresses on October 31, 2026. Independent SG/CN/US health probes also passed
with valid TLS. These connectivity checks are not official Smoke results.

The original hlq5070 entry now uses shared ingress 443 -> loopback 8282 ->
the separate GPT Textual backend on 8380, with legacy_key_target=textual.
The old local 8080 backend and database are preserved. Coding remains paused.
A website version or payload cannot switch this local mode. Use one API
worker and one gateway worker. Candidate watcher is a deployment template:
do not start it on active port 8080 during isolated tests. It launches hidden
and leaves public-route ownership to ingress/watcher.ps1. Login Startup
supervision requires that user to log in after a reboot; it is not a Windows
service. Freeze code/models/mode throughout an evaluation.

The [official Textual contract](https://agentmemories.ai/api/v1/evaluation-tracks/textual/contract),
checked October 31, 2026, accepts Add concurrency ceilings of 16–64, Search
ceilings of 16–256, and Top K of 1–100. The earlier 2/2 recommendation was
incorrect for this range. Selecting 16/16 meets the minimum accepted ceilings;
it is not evidence that this service can safely sustain a Full run. GPT admits
two Add operations, waiting at most one second on overload, then returns
retryable 503. The original public gateway now queues Textual Adds before
they reach that semaphore; direct access through the separate relay does not.
Before Full, verify at least 16 independent new Adds, concurrent
Search and long inputs with invented data. Existing duplicate-request tests
and small public connectivity checks do not establish that capacity.
Per-operation model timeout is 60 seconds; the extraction
deadline is 180 seconds, checked before/after calls. Embedding timeout is
45 seconds with no SDK retries; SQLite lock wait is 30 seconds. The unchanged
legacy gateway has a 300-second I/O timeout; the new Funnel relay does not pass
through that gateway and its complete timeout envelope is not measured.
These are operation/validation limits, not hard
wall-clock cancellation or measured Full-scale throughput. Failed provider
calls can still cost money; committed usage is not a billing ledger.

## Verification and budget

`python run_offline_tests.py report.json` runs retrieval regressions and new GPT
tests with invented temporary data and fake providers, with no paid calls.
`python probe_gpt_add.py --allow-small-network-test --report report.json`
makes a few paid calls against invented fixtures in a disposable database.
It does not contact deployed endpoints and is **not official Smoke**.

The real two-message Add/Search fixture passed, including replay without a
second GPT call and user isolation: 385 input / 98 output tokens, approximately
$0.00011655 at standard $0.15/M input + $0.60/M output rates. This tiny fixture
cannot predict Full costs. Budget actual input/output, repeated prompts,
failed calls, provider limits and embedding separately.
[Official model/pricing](https://developers.openai.com/api/docs/models/gpt-4o-mini).

## Submission readiness

Publish this changed candidate under its own immutable source commit/version.
It requires passed **matching version + Textual Smoke** before Full. Public
rules allow one Smoke/hour and at most 30 per track/edition. No new-version
quota reset or inherited Smoke approval is claimed. If exhausted, obtain an
explicit official verification/admission path. Do not relabel this as v3.14.0.

Materials close **2026-10-31 23:59 UTC+8**; evaluation stops
**2026-11-04 23:59 UTC+8**. Capacity, account eligibility and deployment remain
to be confirmed before all eight Full declarations can truthfully be checked.
[Official rules](https://agentmemories.ai/docs),
[competition](https://agentmemories.ai/competition/).

MIT licensing was approved by the project author; see LICENSE, NOTICE.txt and
SUBMISSION.txt. Third-party software and provider terms retain their licenses.
No official evaluation memories/questions/answers were inspected for this
change. Keep evaluation data, derived copies, runtime databases and credentials
out of source packages, and comply with the organizer's retention deadline.
`python prepare_submission.py` creates an allowlisted source ZIP/manifest;
it neither deploys nor starts an official evaluation.
