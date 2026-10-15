"""AML Add/Search submission server.

Endpoints (paths are configurable on the AML side; defaults below):
  GET  /health  -> any 2xx
  POST /add     -> synchronous write, echoes ids
  POST /search  -> ranked evidence, never answers

Run:  uvicorn app:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import time

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Any

from config import CONFIG
from governance import maybe_govern_add
from store import MemoryStore

app = FastAPI(title="MemoryShield+ AML Submission", version="1.0.0")
store = MemoryStore()


# ------------------------------------------------------------------ auth
def _check_auth(authorization: str | None, x_api_key: str | None) -> None:
    if not CONFIG.auth_token:
        return
    presented = ""
    if authorization:
        presented = authorization.removeprefix("Bearer ").strip()
    if not presented and x_api_key:
        presented = x_api_key.strip()
    if presented != CONFIG.auth_token:
        raise HTTPException(status_code=401, detail={"reason": "invalid_auth_token"})


# ---------------------------------------------------------------- models
class AddMessage(BaseModel):
    role: str = "user"
    timestamp: int | None = None
    content: Any


class AddRequest(BaseModel):
    request_id: str
    messages: list[AddMessage]
    user_id: str
    session_id: str


class SearchRequest(BaseModel):
    query: str | list[dict]
    options: list[str] | None = None
    user_id: str
    top_k: int = 100


# ---------------------------------------------------------------- routes
@app.get("/health")
def health() -> dict:
    return {"status": "ok", "ts": time.time()}


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
    n = store.add_messages(body.user_id, body.session_id, plain_messages)
    if CONFIG.governance_enabled:
        # seq of the last written message; governance demotes older topics.
        maybe_govern_add(store, body.user_id, 10**9, plain_messages)

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
    return JSONResponse({"data": results})


def _query_to_text(parts: list[dict]) -> str:
    out = []
    for p in parts:
        if isinstance(p, dict) and p.get("type") == "text":
            out.append(str(p.get("text", "")))
    return "\n".join(out)
