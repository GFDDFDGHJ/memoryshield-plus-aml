# MemoryShield+ Coding

Coding release: **v4.1.1**, aligned with the submitted Coding version sequence.
This small release adds gateway compatibility for the original Memory System
Key; the storage, retrieval, window and model algorithms remain those of v4.1.0.
This is a separate service from
the frozen Textual v3.15.0 submission in the repository root.

## Isolation and endpoints

| Component | Textual | Coding |
|---|---|---|
| Public HTTPS port | 443, shared gateway | 443, shared gateway |
| Local API | existing 8080 | 127.0.0.1:8181 |
| Source | repository root | this `coding/` directory |
| Database | existing Textual database | `aml_coding_v4.db` |
| Public original Key | Textual when the gateway's local legacy mode is `textual` | Coding when the local legacy mode is `coding` |
| Dedicated Coding Key | never selects Textual | always selects Coding; backend key at `.secrets/auth_token.txt` |
| Supervisor/logs | existing Textual supervisor | this folder's watcher and logs |

Coding URLs after deployment:

- Add: `https://hlq5070.tail50f4d4.ts.net/add`
- Search: `https://hlq5070.tail50f4d4.ts.net/search`
- Health: `https://hlq5070.tail50f4d4.ts.net/health`

The existing Textual source files, database and running processes are
not replaced. The same public Add/Search URLs accept the original Memory System
Key through the shared `../ingress/` gateway. Its explicit local mode selects
one isolated backend at a time; this release's Coding evaluation mode is
**`coding`**. The gateway substitutes the selected backend's internal credential.
The dedicated Coding Key always selects Coding, independently of that mode.
Both services share the physical host and paid provider account; their memory
databases and backend credentials remain separate. Coding bounds searches
to two in flight and provider calls to two embeddings/two reranks per process.
Additional requests wait; a requested evaluation concurrency is an upper bound.

## Method

Raw original messages are persisted unchanged, with role, session, source
sequence and optional message timestamp. Each `user_id` is queried separately.
No cross-user candidate pool, fact extraction, answer generation or code
execution is used. Request replay/conflict checks precede model calls; SQLite
transactions allocate per-user sequences and persist the receipt atomically.

Retrieval fuses exact cosine similarity with identifier/path/error-token
matching. Original snake_case, qualified names and path tokens remain matching
keys. Optional `qwen3-rerank` orders a bounded leading recall pool; a provider
failure retains local ranking. Disposable ranking copies prioritize literal query
matches, preserve a long query's introduction and final constraints, and share
the provider's fixed input budget across the recall pool. Original source is
never replaced by these model-only copies.

Long returned records use exact character/line windows. Indentation, tabs,
CRLF and original fence lines are preserved; excerpt gaps and source ranges
are labelled. Supported Python segments use static AST ranges; conservative
brace-language segments use quote/comment-aware function boundaries. Matching
functions can retain their full body, referenced imports and direct local
callers/tests when they fit. No source is executed and no missing implementation
is inferred. Ambiguous, oversized or unsupported segments retain literal
windows. Static analysis is capped at 200,000 characters and 10,000 lines per
segment, 256 functions and 30,000 AST nodes, with an overall 2,000,000-character
and 30,000-line index guard.

Packing preserves independently ranked seeds, then adds a bounded single hop of
implementation, correction and validation evidence with explicit code anchors.
Same-session proximity alone is insufficient. Explicit conflicting file paths
or unrelated named definitions cannot widen a specific requested function.
Cross-session links require shared explicit path and symbol. Repeated identical
logs from the same role/session are deferred, not treated as newer authority.

The returned context is bounded to 48,000 Python characters including source
labels and inter-record spacing, with at most 9,000 characters per record and
never more than `top_k`/100 records. This is this system's context allocation,
not an asserted platform limit. Stored originals remain complete; omitted
ranges are explicit and original character/line coordinates remain verifiable.

Models stay `text-embedding-v4`, 1024 dimensions, Beijing standard endpoint,
and `qwen3-rerank`. There are no generative LLM components. Embedding-only
copies split at 6000 UTF-8 bytes, at most ten parts per request; all chunk
vectors contribute to the message's mean vector. Persisted and returned source
code is never replaced by those copies. Very large heterogeneous messages may
have diluted dense similarity; exact code-token recall provides another path.

## Run locally

Install the pinned `requirements.txt` in Python 3.10 or later. Set
`CODING_EMBEDDING_API_KEY` (or a persisted `DASHSCOPE_API_KEY`) and create a
dedicated `.secrets/auth_token.txt`. The watcher refreshes the persisted provider
key and pins its own database path before each launch. Keep credentials out of
source control. Start this folder's watcher hidden; it supervises only the
Coding API. Funnel 443 forwards to the shared gateway on 8282; it routes to
this backend on 8181 in local `coding` mode for the original Key, or whenever
the dedicated Coding Key is supplied. See `../ingress/README.md`.

Clients may continue using `Authorization: Bearer <original Memory System Key>`
and the original URLs. Before a Coding evaluation, select local gateway mode
`coding`; before returning to Textual, select `textual`. Only switch after all
Smoke and Full evaluations have completed. Shared-Key compatibility supports
one track at a time. No request content, `track` field or header changes the mode.
The dedicated Coding Key always selects Coding. Health is public: anonymous
health reports Textual, original-Key health follows the local mode, and dedicated
Coding-Key health reports Coding. Verify the actual mode using
`/__gateway_health` before launching an evaluation.
Add/Search follow the official text/Coding request envelope and echo
all required Add identifiers. Search returns evidence strings in `data` and
never more than `top_k` (capped at 100). No arbitrary 20-message Add cap is
imposed on Coding trajectories. Optional multiple-choice options are not answers.

Run `python -m unittest discover -p "test_*.py"` from this directory for offline
invented-fixture checks. No official evaluation source messages or answers are
used for development. Local tests measure contracts and source fidelity, not
the downstream task-solver's benchmark score. Full-scale capacity requires
separate measurement; no official Smoke or Full is launched by these scripts.

## Submission

Register a separate Coding system/version, such as **MemoryShield+ Coding /
v4.1.1**, with the shared URLs and the existing original Memory System Key.
Select the Coding version/track in the evaluation platform and set the local
gateway mode to `coding` before running. The organizer does not need to replace
the Memory System Key for this compatibility mode. An existing Eval Key may
still need the new version and Coding track approved/bound by AML.
Do not replace the Textual version's endpoint registration.

The storage/ranking/window implementation is new for this exploratory service.
The bounded reranker client is reused unchanged from this project's reviewed
Textual v3.15.0 source; see the parent repository for its development history.
The existing project and reused third-party dependencies remain attributed.

The initial backend used internal versions v1.0.0/v1.0.1; the website run label
v4.0 is not proof that it used that backend. v4.1.0 aligned the code release name
with the participant's website sequence; v4.1.1 adds original-Key gateway
compatibility without changing the memory algorithms. The original Key now
follows the operator's explicit local mode, independently of a website
track/version label. Both public endpoint URLs and both credentials remain
unchanged. Run Textual again only after all evaluations finish and the local
mode has been switched back to `textual`.

The shared gateway source remains in `../ingress/` at the same repository
commit, with its own source archive and manifest. Timestamped access logging
records method/path/status only, never request bodies, queries or memories.
Smoke aggregates motivate general checks, but no benchmark task source or
answer is used for tuning, and no synthetic check is a predicted official score.
