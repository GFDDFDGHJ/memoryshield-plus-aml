"""AML Add/Search submission server.

Endpoints (paths are configurable on the AML side; defaults below):
  GET  /health  -> any 2xx
  POST /add     -> synchronous write, echoes ids
  POST /search  -> ranked evidence, never answers

Run:  uvicorn app:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import json
import hmac
import os
import time

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from typing import Any

from config import CONFIG
from governance import maybe_govern_add
from store import AddRequestConflict, MemoryStore

app = FastAPI(title="MemoryShield+ AML Submission", version=CONFIG.version)
store = MemoryStore()

# ------------------------------------------------------------------ audit log
# uvicorn's access log is truncated by every restart (the launcher redirects
# over it), so a failed Add leaves no trace and a whole 12-minute evaluation
# can produce zero diagnosable data. This appends one JSON line per request
# instead, which survives restarts and can be joined with the scores.
AUDIT_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "requests.jsonl")


def _audit(record: dict) -> None:
    try:
        with open(AUDIT_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({**record, "system_version": CONFIG.version}, ensure_ascii=False) + "\n")
        if os.path.getsize(AUDIT_LOG) > 8 * 1024 * 1024:
            with open(AUDIT_LOG, encoding="utf-8") as fh:
                tail = fh.readlines()[-5000:]
            with open(AUDIT_LOG, "w", encoding="utf-8") as fh:
                fh.writelines(tail)
    except Exception:  # noqa: BLE001 - never let logging break a request
        pass


@app.middleware("http")
async def audit_middleware(request: Request, call_next):
    started = time.time()
    try:
        response = await call_next(request)
    except Exception:
        # Keep failed writes diagnosable without recording source data,
        # credentials, or upstream response bodies.
        _audit({"ts": round(time.time(), 3), "path": request.url.path,
                "status": 500, "ms": round((time.time() - started) * 1000)})
        raise
    if request.url.path == "/health":
        return response  # the supervisor probes this every 30s; not worth a line
    # Status/latency only: reading the body here would consume the stream that
    # FastAPI needs for validation. Handlers log their own business fields.
    _audit(
        {
            "ts": round(time.time(), 3),
            "path": request.url.path,
            "status": response.status_code,
            "ms": round((time.time() - started) * 1000),
        }
    )
    return response


# ------------------------------------------------------------------ auth
def _check_auth(authorization: str | None, x_api_key: str | None) -> None:
    if not CONFIG.auth_token:
        return
    presented = ""
    if authorization:
        presented = authorization.strip()
        for scheme in ("Bearer ", "Token ", "token ", "bearer "):
            if presented.startswith(scheme):
                presented = presented[len(scheme):].strip()
                break
    if not presented and x_api_key:
        presented = x_api_key.strip()
    if not hmac.compare_digest(presented.encode(), CONFIG.auth_token.encode()):
        raise HTTPException(status_code=401, detail={"reason": "invalid_auth_token"})


# ---------------------------------------------------------------- models
class AddMessage(BaseModel):
    role: str = Field(default="user", min_length=1)
    timestamp: int | None = None
    content: Any

    @field_validator("content")
    @classmethod
    def nonempty_text(cls, value):
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, list) and value and all(
            isinstance(p, dict) and p.get("type") == "text"
            and isinstance(p.get("text"), str) and p["text"].strip()
            for p in value
        ):
            return value
        raise ValueError("Textual memory content must contain non-empty text")


class AddRequest(BaseModel):
    request_id: str = Field(min_length=1)
    messages: list[AddMessage] = Field(min_length=1)
    user_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)


class SearchRequest(BaseModel):
    query: str | list[dict]
    options: list[str] | None = None
    user_id: str
    top_k: int = Field(ge=1)


# ---------------------------------------------------------------- routes
@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": CONFIG.version,
            "embedding_model": CONFIG.embedding_model,
            "rerank_enabled": getattr(store, "_reranker", None) is not None,
            "rerank_model": CONFIG.rerank_model if CONFIG.rerank_enabled else None,
            "ts": time.time()}


@app.post("/add")
def add(
    body: AddRequest,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict:
    _check_auth(authorization, x_api_key)
    if not body.messages:
        raise HTTPException(status_code=422, detail={"reason": "empty_messages"})

    plain_messages = [m.model_dump() for m in body.messages]
    try:
        n = store.add_messages(body.user_id, body.session_id, plain_messages,
                               request_id=body.request_id)
    except AddRequestConflict:
        raise HTTPException(status_code=409, detail={"reason": "request_id_conflict"})
    if CONFIG.governance_enabled:
        # seq of the last written message; governance demotes older topics.
        maybe_govern_add(store, body.user_id, 10**9, plain_messages)

    _audit(
        {
            "ts": round(time.time(), 3),
            "path": "/add",
            "status": 200,
            "user_id": body.user_id,
            "session_id": body.session_id,
            "request_id": body.request_id,
            "n_msgs": len(plain_messages),
            "written": n,
        }
    )
    return {
        "success": True,
        "request_id": body.request_id,
        "user_id": body.user_id,
        "session_id": body.session_id,
        "written": n,
    }


@app.post("/search")
def search(
    body: SearchRequest,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> JSONResponse:
    _check_auth(authorization, x_api_key)
    query_text = body.query if isinstance(body.query, str) else _query_to_text(body.query)
    if not query_text.strip():
        return JSONResponse({"data": []})
    k = max(1, min(int(body.top_k), 100))
    results = store.search(body.user_id, query_text, k)
    _audit(
        {
            "ts": round(time.time(), 3),
            "path": "/search",
            "status": 200,
            "user_id": body.user_id,
            "top_k": k,
            "n_results": len(results),
            "n_pinned": sum(1 for r in results if r["id"].startswith(
                ("profile", "digest", "fact", "change"))),
        }
    )
    return JSONResponse({"data": results})


def _query_to_text(parts: list[dict]) -> str:
    out = []
    for p in parts:
        if isinstance(p, dict) and p.get("type") == "text":
            out.append(str(p.get("text", "")))
    return "\n".join(out)
