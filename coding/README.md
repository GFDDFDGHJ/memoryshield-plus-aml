# MemoryShield+ Coding

Initial exploratory Coding release: **v1.0.1**. This is a separate service from
the frozen Textual v3.15.0 submission in the repository root.

## Isolation and endpoints

| Component | Textual | Coding |
|---|---|---|
| Public HTTPS port | 443, shared gateway | 443, shared gateway |
| Local API | existing 8080 | 127.0.0.1:8181 |
| Source | repository root | this `coding/` directory |
| Database | existing Textual database | `aml_coding_v4.db` |
| Auth | existing Textual credential | `.secrets/auth_token.txt` in this folder |
| Supervisor/logs | existing Textual supervisor | this folder's watcher and logs |

Coding URLs after deployment:

- Add: `https://hlq5070.tail50f4d4.ts.net/add`
- Search: `https://hlq5070.tail50f4d4.ts.net/search`
- Health: `https://hlq5070.tail50f4d4.ts.net/health`

The existing Textual source files, database and running processes are
not replaced. The same public Add/Search URLs use separate Memory System Keys
to select fixed isolated backends through the shared `../ingress/` gateway. Both services share the physical host and paid provider account;
they do not share memories or authentication tokens. Coding bounds searches
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
failure retains local ranking and never deletes source candidates.

Long returned records use exact character/line windows. Indentation, tabs,
CRLF and original fence lines are preserved; excerpt gaps and source ranges
are labelled. Lexical function/class/file headers can accompany a matching
line within conservative local boundaries. This is not an AST parser or an
import/dependency graph. Unsupported syntax remains literal source evidence.

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
this backend on 8181 using the Coding key. See `../ingress/README.md`.

Clients use `Authorization: Bearer <Coding Memory System Key>`. Health is
public. Add/Search follow the official text/Coding request envelope and echo
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
v1.0.1**, with the shared URLs and its dedicated Memory System Key. An existing
Eval Key may need the new version and Coding track approved/bound by AML.
Do not replace the Textual version's endpoint registration.

The storage/ranking/window implementation is new for this exploratory service.
The bounded reranker client is reused unchanged from this project's reviewed
Textual v3.15.0 source; see the parent repository for its development history.
The existing project and reused third-party dependencies remain attributed.

The v1.0.1 patch changes shared-URL deployment metadata only; the memory
algorithms remain those of v1.0.0. The shared gateway source is in `../ingress/`
at the same repository commit, with its own source archive and manifest.
