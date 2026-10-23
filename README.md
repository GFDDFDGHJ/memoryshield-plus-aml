# MemoryShield+ — AML Textual Memory

Candidate version: **v3.11.1**. Repository:
<https://github.com/GFDDFDGHJ/memoryshield-plus-aml>.

Self-hosted synchronous Add/Search service for the Agent Memory Leaderboard
Textual track / Open-source Methods division. Search returns stored evidence;
the platform handles answering and scoring.

This is a release candidate. The public repository and running API must be
updated to the same version, then pass the official Smoke before Full.

Versions use `vMAJOR.MINOR.PATCH`: large changes increment MAJOR, medium
changes increment MINOR, and small changes increment PATCH. Incrementing a
higher field resets lower fields to zero. The previous `v3.8` is `v3.8.0`.

## Method and models

- SQLite stores raw messages, source sessions, facts, and extractive digests.
- `text-embedding-v4`, 1024 dimensions, via Alibaba Cloud Model Studio's
  OpenAI-compatible API. Batches contain at most ten texts.
- Deterministic regex extraction creates facts, preferences, and standing rules.
  This candidate makes **no generative LLM calls** during Add or Search.
- Retrieval combines applicable rules, ordered state history, query-scoped
  source context, relevant facts, personal background when requested, and exact
  cosine ranking inside the requesting `user_id`.
  NumPy is optional; the standard-library fallback scores the same user scope.
- Optional `qwen3-rerank` ranks a bounded union of dense and rare-term source
  candidates. Set `AML_RERANK=true`; the checked-in watcher enables it using
  the embedding key. Direct launches can provide a separate `AML_RERANK_API_KEY`.
  `AML_RERANK_BASE_URL`, `AML_RERANK_MODEL` and `AML_RERANK_TIMEOUT` configure
  the native DashScope client. Defaults are Beijing, qwen3-rerank and 6 seconds.
  Provider errors, exhausted concurrency or invalid ranks retain deterministic
  dense/sparse interleaving, keeping rare older definitions near observations.
  Disabled ranking and explicit state/chronology guards retain their original
  paths. Only disposable ranking copies are truncated;
  returned evidence retains original wording, role and source provenance.
- Query planning separates explicit private-field exclusions from retrieval
  topics. Personal-history synthesis uses operation and scope grammar; named
  projects and single-domain summaries retain their topic bounds. A bounded
  prefix prioritizes explicit choices, changes and reasons; continuations carry
  the remaining selected original history.
- Complete direct requests such as "Summarize my history" or "Summarize what
  you remember about me" supply explicit whole-history scope. Specific
  complements, quoted commands, conditions and negated requests do not gain
  that scope. Explicit private-field exclusions remain separate constraints.
- Topic synthesis uses the same query plan in selection and budgeting. Global
  context schedules less-repeated literal sentences before repeated background,
  without merging observations. One original instance of each applicable global
  response policy is protected before optional context; every source RID remains.
- Bounded global summaries visit less-repeated sentences within each source
  before round-robin sampling across sources. Complete short views keep their
  original order; continuations still preserve all selected source segments.
- A response policy qualified by a topic or a request condition remains local,
  including when that qualifier follows the response noun. Output language,
  format and length alone do not create a topic restriction.
- Rule evidence retains an immediately preceding explicit scope heading and
  consecutive imperative follow-ups or an explicit same-rule withdrawal.
  Blank lines, unrelated narration and new headings delimit those blocks.
- Source-local qualification links preserve explicit approval outcomes,
  withdrawals, replacements and partial exemptions alongside their anchors.
  Links select original sentences and never execute rules or assert a new state.
- Long ranking copies use query-focused literal windows within the same byte
  ceiling, including selected exceptions and schemas. They no longer depend
  solely on the beginning of a long turn; raw source storage is unchanged.
- Exact lookups prioritize relevant source evidence. A temporal restriction
  such as "before" does not globally reorder all results by insertion time.
  Explicit sequence requests include bounded neighboring turns in source order.
  A large embedding-score gap does not permanently discard the remaining
  evidence: an independent cause or intermediate source can use different words.
- Each raw result identifies its source row, session, sequence, role, and storage
  timestamp. De-duplication merges the same source row across retrieval layers;
  distinct source rows remain distinct even when text and timestamps match.
- Fact updates preserve history, prohibitions, and conditional triggers. User
  and assistant assertions update their own role's history. Existing facts with
  unknown roles are repaired lazily from their original sources for the current
  user's affected attribute; genuine retractions remain retracted.
- Single-valued facts with comparable provided message timestamps replay in
  source-time order when a new assertion arrives late. Mixed/unknown timestamp
  origins keep source order. Reassertions keep their own source pointers.
  Later explicit self-attribute qualifications label applicability unverified
  and quote the later source; the service does not infer a replacement value.
- Unstructured negated complements such as "not Eira" are not asserted as
  replacement values. A later exact present-tense denial of the same self
  field/value is quoted as unverified applicability; historical, conditional,
  quoted-statement, other-role and different-value controls remain separate.
- Explicit delimited schemas can precede or follow a contiguous observation
  block. Declared missing cells retain their literal spelling. A new session
  inherits a schema only through an explicit same-dataset reference to one
  unambiguous earlier declaration. Roles, hidden gaps and topic boundaries stop
  expansion; schemas and validity conditions are scheduled before data rows.
- Summary evidence is assembled from original source messages at query time.
  Topic requests select source spans while retaining connected evidence;
  global requests can cover all sessions. Repeated numeric observations are
  retained with provenance. When needed, a reversible literal template compacts
  repeated wording without estimating statistics or dropping numeric precision.
  Coverage and any truncation are explicitly disclosed.
- Summary, comparison, aggregate, and sequence requests share an intent decision
  across retrieval layers. Matched schemas and contiguous numeric observations
  retain their relationship across message boundaries; a new topic, speaker,
  or session ends that data block. Table excerpts keep their column meanings.
- Raw messages remain complete in storage. Search uses query-centered excerpts
  for long messages, retains adjacent conditions/exceptions, and labels partial
  evidence. Ordinary results retain a 32,000-character budget. Large selected
  histories can append omitted source segments up to 160,000 content characters,
  with a 128,000-character continuation ceiling. A continuation preserves the
  compact first 16k characters; exact identifier background can also accompany
  its semantic anchor. Character counts are not token counts: the platform
  independently truncates its Answer context in ranked order. `top_k` remains
  an upper bound on the complete response array.
- Explicit dataset, project and protocol identifiers can link matching source
  background across sessions. These bounded, single-hop links retain source
  role, units, conditions and revocations, with literal quote ranges. They do
  not infer applicability, resolve versions or merge people by a shared name.
  An excerpted source already near the head can receive missing identifier
  background in the same result, retaining its score and source identity.
  Such supplementary spans do not represent additional observations.
- Explicit exclusions recognize labelled personal names and nicknames in
  addition to the existing sensitive fields. Technical file/column/project
  names stay eligible, and questions about earlier omissions remain questions.
  This field grammar does not perform free-text person-name anonymization.
- Applicable global execution constraints accompany rule requests; topic-specific
  business rules stay scoped. Explicit single-property personal queries use
  positive source assertions to limit context; unknown formulations retain
  semantic fallback. This is not a blanket privacy guarantee.
- Caller-provided record timestamps may anchor relative language. Ingestion-clock
  and unknown legacy timestamps are explicitly distinguished and do not become
  event-date anchors. Source tables preserve each record's time without inventing
  event chronology. Current-state assertions carry explicit history labels.
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
| `AML_VERSION` | `2026.10.23-rc8` |
| `AML_RERANK` | `false`; watcher enables `true` |
| `AML_RERANK_MODEL` | `qwen3-rerank` |
| `AML_RERANK_API_KEY` | Embedding key |
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
python test_context_facts.py
python test_prefix_retrieval.py
python test_temporal_context.py
python test_scope_consistency.py
python test_schema_context_blocks.py
python test_capability_generalization.py
python test_context_planning.py
python test_context_continuation.py
python test_evidence_links.py
python test_search_continuation.py
python evaluate_quality.py --source-root . --output tmp/quality-candidate.json
python evaluate_context_quality.py --source-root . --output tmp/context-candidate.json
python evaluate_capabilities.py --source-root . --output tmp/capabilities.json
python evaluate_extended_context.py --source-root . --output tmp/extended-context.json
```

Tests cover isolation, retry/conflict/concurrent writes, transaction rollback,
small Top K, long-message retention, exact scoring without NumPy, embedding
batching/order/shape, rule semantics, fact history, authentication, and request
auditing without content. They do not measure leaderboard quality or replace
the official Smoke.

Smoke covers a limited subset and is not the optimization target. The new fixed
capability evaluator contains 47 invented scenarios across nine families, checks
8k/16k visible evidence with Top K 100, and reports coverage, quotation/source
consistency, context contamination, ranking, and incremental/batch equivalence.
These budgets are stress checks, not asserted AML platform limits. Strict
scenario counts are diagnostic proxies, not official capability scores.

Read [EVALUATION.md](EVALUATION.md) for comparison procedures and limitations.
The same fixtures can be compared using a shared real embedding cache:

```powershell
python evaluate_capabilities.py --export-fixtures tmp/capability-fixtures.json
# Set EMBEDDING_API_KEY or DASHSCOPE_API_KEY in the environment first.
python prepare_capability_vectors.py --fixtures tmp/capability-fixtures.json --output tmp/capability-vectors.json --prepare
python evaluate_capabilities.py --source-root . --vectors-cache tmp/capability-vectors.json --output tmp/capabilities-real.json
```

Only the explicit `--prepare` step contacts the provider, using the exported
invented texts. Cached comparisons disable network access. No comparison uses
official conversations or answers. Preserve the earlier evaluators as regression
controls and record disagreements; do not optimize a single combined pass count.
Source-driven context does not depend on rebuilding cached session digests.

## Deployment and submission

Use a continuously available host, stable public HTTPS URL, dedicated
authentication, and documented capacity/concurrency/timeouts. `start.ps1` runs
in the foreground; configure a host-managed supervisor for unattended service.
Historical watcher/tunnel scripts are deployment-specific. Cloudflare quick
tunnel names change after restarts and require updating the AML API binding.

This Windows deployment keeps the existing public origin
`https://hlq5070.tail50f4d4.ts.net` and the same `/add` and `/search` paths.
`watcher.ps1` supervises the rc8 API. It pins Beijing `text-embedding-v4`, 1024
dimensions, and `aml_text_v4.db`; health probes verify version and model. A
lifetime file lock prevents duplicate supervisors. Launch the watcher outside
the interactive tool process; the current host uses `Win32_Process.Create`.
The existing participant authentication key remains unchanged. Keep the host
awake during Smoke. A separate Cloudflare fallback is deployment-specific and
does not replace the fixed participant origin.

The supervisor also checks the fixed origin through public IPv4 ingress every
120 seconds. It obtains public addresses using DNS over HTTPS and pins each
connection with `curl --resolve`, retaining hostname and certificate checks.
This bypasses local Tailscale MagicDNS, which can otherwise make an internal
success look like a public availability check. State changes are recorded in
the service log. Public-check failures do not restart a healthy local API.
Deploy before starting an evaluation and keep code fixed until it completes.
Request audit records include the runtime version without query/message content.

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
code before the formal submission. The name “MemoryShield+” alone does not establish
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
