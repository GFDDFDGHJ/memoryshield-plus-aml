# MemoryShield+ — AML Textual Memory

Candidate version: **2026.10.22-rc2**. Repository:
<https://github.com/GFDDFDGHJ/memoryshield-plus-aml>.

Self-hosted synchronous Add/Search service for the Agent Memory Leaderboard
Textual track / Open-source Methods division. Search returns stored evidence;
the platform handles answering and scoring.

This is a release candidate. The public repository and running API must be
updated to the same version, then pass the official Smoke before Full.

## Method and models

- SQLite stores raw messages, source sessions, facts, and extractive digests.
- `text-embedding-v4`, 1024 dimensions, via Alibaba Cloud Model Studio's
  OpenAI-compatible API. Batches contain at most ten texts.
- Deterministic regex extraction creates facts, preferences, and standing rules.
  This candidate makes **no generative LLM calls** during Add or Search.
- Retrieval combines related rules, ordered state history, session digests,
  relevant facts, personal background when requested, and exact cosine ranking
  inside the requesting `user_id`.
  NumPy is optional; the standard-library fallback scores the same user scope.
- Fact updates preserve history, prohibitions, and conditional triggers.
  Raw de-duplication preserves semantic punctuation and repeated observations
  from different sessions or dates.
- Extractive digests retain Chinese text, decimals, units, repeated observations,
  and early/middle/late sources. Summary retrieval covers all stored sessions.
- Historical retrieval includes neighboring turns in source order. Source
  timestamps are labeled as recording metadata, not inferred event dates.
  Current-state assertions carry explicit current/historical labels.
- Identical `(user_id, request_id)` retries are idempotent across restarts.
  Different content or session with that key returns HTTP 409.
- The legacy substring-based governance feature is disabled by default.

The current [AML documentation](https://agentmemories.ai/docs) specifies
`text-embedding-v4` for Open-source Methods embeddings and `gpt-4o-mini` for
LLM components. Whether an entirely LLM-free method must introduce one is
unresolved in [official issue #19](https://github.com/AML-memory/agent-memory-leaderboard/issues/19).
Disclose this regex-only implementation and obtain clarification before Full.

## Setup

Use Python 3.12 or 3.13 with SQLite extension loading available:

```powershell
python -m venv .venv
./.venv/Scripts/python -m pip install -r requirements.txt
New-Item -ItemType Directory -Force .secrets
# Save a Model Studio API key in .secrets/dashscope_api_key.txt.
# Save a dedicated participant API token in .secrets/aml_auth_token.txt.
./start.ps1
```

On Linux/macOS, activate the virtual environment and run
`python -m uvicorn app:app --host 0.0.0.0 --port 8080`.
For another Python installation, set `AML_PYTHON` before running `start.ps1`.

Key priority: `EMBEDDING_API_KEY`, then the project secret file, then
`DASHSCOPE_API_KEY`. The default endpoint is Beijing:
`https://dashscope.aliyuncs.com/compatible-mode/v1`. For Singapore, set
`EMBEDDING_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1`, or use
a region-specific workspace endpoint. Keys are region-specific.

The Windows startup scripts refresh saved User/Machine `DASHSCOPE_*`
variables before launching Python; User values take priority. Explicit
`EMBEDDING_*` overrides remain unchanged. Restart an already running server
after changing a key so it receives the updated environment.

The existing Ark key in `.secrets/ark_api_key.txt` belongs to Volcengine,
not Alibaba Cloud Model Studio. It is not read by this candidate and cannot
authenticate `text-embedding-v4`. The supplied Ark deployment
`ep-20261015115102-j7n5s` / `doubao-embedding-vision-251215` uses a different
model; it does not meet the current Open-source Methods embedding requirement.

`AllocationQuota.FreeTierOnly` means the model's “Free Quota Only /
免费额度用完即停” setting is blocking paid use; account balance alone does not
override it. See [Alibaba quota documentation](https://help.aliyun.com/zh/model-studio/new-free-quota).

## Configuration

| Variable | Default |
|---|---|
| `EMBEDDING_MODEL` | `text-embedding-v4` |
| `EMBEDDING_MODE` | `standard` |
| `EMBEDDING_DIM` | `1024` |
| `EMBEDDING_BATCH_SIZE` | `10`, constrained to 1–10 |
| `EMBEDDING_BASE_URL` | Beijing endpoint above |
| `EMBEDDING_API_KEY` | Project secret or `DASHSCOPE_API_KEY` |
| `AML_DB_PATH` | Project-local `aml_text_v4.db` |
| `AML_AUTH_TOKEN` | `.secrets/aml_auth_token.txt` |
| `AML_VERSION` | `2026.10.22-rc2` |
| `AML_RECENCY_WEIGHT` | `0.10` |
| `AML_GOVERNANCE` | `false` |
| `AML_HOST` / `AML_PORT` | `0.0.0.0` / `8080` |

The new database separates these vectors from the old Ark `aml_memory.db`.
Do not reuse the old vector rows with the new model. The original database is
preserved; a new platform run supplies new user scopes.

## API

Public `GET /health` returns local status, API version, and embedding model.
It does not verify upstream quota or public ingress. Add/Search support
`Authorization: Bearer <token>`, `Authorization: Token <token>`, and
`X-Api-Key: <token>`.

```json
POST /add
{
  "request_id": "synthetic:chunk:1",
  "user_id": "synthetic:user:1",
  "session_id": "synthetic:session:1",
  "messages": [{"role": "user", "content": "My favorite color is green."}]
}
```

After persistence, HTTP 200 returns `success: true`, the exact three request
identifiers, and `written`. Empty text returns 422; conflicting retries return 409.

```json
POST /search
{
  "query": "What is my favorite color?",
  "user_id": "synthetic:user:1",
  "top_k": 100
}
```

Returns `{"data":[{"id":"mem_1","content":"...","score":0.8,"created_at":"..."}]}`.
Results never exceed `top_k`. Optional choice `options` is accepted at the top
level. This candidate targets text; image ingestion is not implemented.
`session_id` organizes sources and does not narrow Search.

## Verification

These commands use synthetic data and temporary databases, with no real
embedding calls or formal evaluation quota use:

```powershell
python smoke_test.py
python test_facts.py
python test_store_regressions.py
python test_api_contract.py
python test_fact_quality.py
python test_store_quality.py
python evaluate_quality.py --source-root . --output tmp/quality-candidate.json
```

Tests cover isolation, retry/conflict/concurrent writes, transaction rollback,
small Top K, long-message retention, exact scoring without NumPy, embedding
batching/order/shape, rule semantics, fact history, authentication, and request
auditing without content. They do not measure leaderboard quality or replace
the official Smoke.

The fixed independent synthetic evidence evaluation improved from 21/37 checks
on the pre-optimization snapshot to 37/37 on this candidate. A second comparison
reusing the same real `text-embedding-v4` vectors improved from 4/7 to 7/7
synthetic retrieval cases. These results measure evidence coverage, fidelity,
and ranking; they are not official capability scores and do not predict a new
Smoke score. Neither comparison uses official evaluation conversations.

Previously stored digests use the extraction logic active when their session
was last written. New platform runs create fresh user scopes and receive the
new extraction behavior; no evaluation-data backfill is required.

## Deployment and submission

Use a continuously available host, stable public HTTPS URL, dedicated
authentication, and documented capacity/concurrency/timeouts. `start.ps1` runs
in the foreground; configure a host-managed supervisor for unattended service.
Historical watcher/tunnel scripts are deployment-specific. Cloudflare quick
tunnel names change after restarts and require updating the AML API binding.

This Windows deployment keeps the existing public origin
`https://hlq5070.tail50f4d4.ts.net` and the same `/add` and `/search` paths.
`watcher.ps1` supervises the rc2 API. It pins Beijing `text-embedding-v4`, 1024
dimensions, and `aml_text_v4.db`; health probes verify version and model. A
lifetime file lock prevents duplicate supervisors. Launch the watcher outside
the interactive tool process; the current host uses `Win32_Process.Create`.
The existing participant authentication key remains unchanged. Keep the host
awake during Smoke. A separate Cloudflare fallback is deployment-specific and
does not replace the fixed participant origin.

Before Full:

1. Verify real synthetic Add → Search using the approved embedding model.
2. Publish the complete candidate source and record its immutable commit.
3. Complete license, source attribution, and capacity declarations.
4. Update the submitted API version/binding and pass official Smoke.
5. Freeze the deployed code and configuration for the formal run.

Official [participation guide](https://agentmemories.ai/competition/) and
[API guide](https://agentmemories.ai/api-guide): materials close October 31,
2026 at 23:59 UTC+8; evaluation closes November 4 at 23:59 UTC+8.

## Publication and provenance

Inspected public base commit: `aed97db67b7dbd6d4d35678b4c0c609cf2050014`.
That base has no license or complete attribution statement. The author must
select an open-source license and disclose reused papers, repositories, and
code before publication. The name “MemoryShield+” alone does not establish
that this implementation reproduces a particular paper.

Relative to that public base, this candidate adds the fact/digest layer and
repairs retrieval, rule/state semantics, ingestion retries, model settings,
and content logging. The declared public commit must include the full change.

## Evaluation data

New audit records contain request metadata/status/timing and no query/message
payloads. Existing logs/databases remain private runtime artifacts. Keep all
evaluation data and derived copies out of Git and source archives; remove
them within the organizer's retention deadline unless written permission
extends retention. This code does not schedule automatic deletion.

`python prepare_submission.py` creates an allowlisted source archive and
SHA256 manifest. It does not publish a repository, choose a license, update
the deployed service, or start an evaluation.
