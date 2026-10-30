# MemoryShield+ — Textual candidate v3.16.0

**Candidate only: not deployed, no matching official Smoke, no Full started.**
The existing service remains v3.14.0. Its passed Smoke does not qualify this
changed implementation. The Chinese Full checklist explicitly requires
gpt-4o-mini during Add; the old LLM-free service does not meet that condition.

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

Intended endpoints and original Memory System Key remain unchanged:

- https://hlq5070.tail50f4d4.ts.net/add
- https://hlq5070.tail50f4d4.ts.net/search
- https://hlq5070.tail50f4d4.ts.net/health

Shared ingress 443 -> loopback 8282 -> Textual 8080 uses
legacy_key_target=textual. Coding remains paused with separate storage.
A website version or payload cannot switch this local mode. Use one API
worker and one gateway worker. Candidate watcher is a deployment template:
do not start it on active port 8080 during isolated tests. It launches hidden
and leaves public-route ownership to ingress/watcher.ps1. Login Startup
supervision requires that user to log in after a reboot; it is not a Windows
service. Freeze code/models/mode throughout an evaluation.

Suggested initial platform concurrency is Add=2 / Search=2, Top K=100.
GPT admits two batches, waiting at most one second on overload, then returns
retryable 503. Per-operation model timeout is 60 seconds; the extraction
deadline is 180 seconds, checked before/after calls. Embedding timeout is
45 seconds with no SDK retries; SQLite lock wait is 30 seconds; current gateway
I/O timeout is 300 seconds. These are operation/validation limits, not hard
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
