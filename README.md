# MemoryShield+ — AML Add/Search Submission Server

Self-hosted memory system implementing the Agent Memory Leaderboard (AML)
Add/Search contract for the **Textual track / Open-source Methods** division.

## Live endpoints (this deployment)

| Method | URL |
|---|---|
| GET  | `https://hlq-1.tail50f4d4.ts.net/health` |
| POST | `https://hlq-1.tail50f4d4.ts.net/add` |
| POST | `https://hlq-1.tail50f4d4.ts.net/search` |

Auth: send the Memory System Key as `Authorization: Bearer <token>` (also
accepted as `X-Api-Key`). `/health` is unauthenticated. The token lives in
`.secrets/aml_auth_token.txt` (not committed).

## What it is

- **Storage**: SQLite + `sqlite-vec` (file-based, zero external DB).
- **Embeddings**: Volcengine Ark multimodal endpoint
  `ep-20261015115102-j7n5s` via `POST /embeddings/multimodal` (text-only
  input, 2048-dim vectors; one text per request, fanned out concurrently).
- **Retrieval**: global ANN candidate pool, then a strict `user_id` filter,
  then a light recency re-rank. Returns up to `top_k` evidence; never answers.
- **Governance**: MemoryShield+ supersession hook (a later statement supersedes
  an earlier one on an updated fact). Aggressive keyword quarantine is **off**
  — AML data is clean, so it would only suppress correct recall.

## API contract

```
POST /add
{ "request_id": "...", "user_id": "...", "session_id": "...",
  "messages": [ {"role":"user","timestamp":1700000000000,"content":"..."} ] }
-> 200 { "success": true, "request_id": "...", "user_id":"...", "session_id":"...", "written": n }

POST /search
{ "query": "...", "user_id": "...", "top_k": 100 }
-> 200 { "data": [ {"id":"mem_1","content":"...","score":-0.5,"created_at":"..."} ] }
```

## Run

```powershell
pip install -r requirements.txt
# keys are auto-read from .secrets/ (ark_api_key.txt, aml_auth_token.txt)
./start.ps1        # == python -m uvicorn app:app --host 0.0.0.0 --port 8080
```

Public exposure uses Tailscale Funnel: `tailscale funnel --bg --yes 8080`.

## Key environment variables

| Var | Default | Note |
|---|---|---|
| `EMBEDDING_BASE_URL` | `https://ark.cn-beijing.volces.com/api/v3` | |
| `EMBEDDING_API_KEY` | `.secrets/ark_api_key.txt` | Ark key |
| `EMBEDDING_MODEL` | `ep-20261015115102-j7n5s` | multimodal vision endpoint |
| `EMBEDDING_MODE` | `multimodal` | `standard` = OpenAI `/embeddings` |
| `EMBEDDING_DIM` | `2048` | must match model output |
| `EMBEDDING_CONCURRENCY` | `8` | parallel single-text embedding calls |
| `AML_DB_PATH` | `aml_memory.db` | SQLite file |
| `AML_AUTH_TOKEN` | `.secrets/aml_auth_token.txt` | Bearer / X-Api-Key |
| `AML_CANDIDATE_POOL` | `4000` | ANN pool (sqlite-vec hard cap 4096) |
| `AML_RECENCY_WEIGHT` | `0.15` | recency boost in re-rank |
| `AML_GOVERNANCE` | `true` | supersession on update cues |
| `AML_QUARANTINE` | `false` | off on clean benchmark data |

## Verify locally (no API key needed)

```powershell
python smoke_test.py
```

Uses a deterministic fake embedder to exercise Add→Search, schema, `top_k`,
and cross-user isolation.

## Notes & limits

- Per-user isolation is enforced in SQL after the ANN pool; no record from
  another `user_id` can be returned.
- `Add` is synchronous: it embeds and persists before returning 200, so the
  platform can Search immediately.
- This is a **recall-first** baseline. MemoryShield+'s poisoning-defense value
  (quarantine / provenance gate) is deliberately not exercised because the
  benchmark contains no poisoning attacks; re-enable via `AML_QUARANTINE=true`.
