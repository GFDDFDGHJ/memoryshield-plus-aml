"""Vector-backed memory store for the AML Add/Search contract.

One SQLite database holds all memories. The vec0 virtual table indexes
embeddings; the content table keeps metadata and the per-user_id isolation
boundary. Search pulls a global ANN candidate pool, then filters strictly to
the requested user_id so cross-user leakage is impossible.
"""
from __future__ import annotations

import array
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable

import httpx
import sqlite_vec
from openai import OpenAI

from config import CONFIG


def _pack(vec: list[float]) -> bytes:
    return array.array("f", vec).tobytes()


class MemoryStore:
    def __init__(self) -> None:
        self._client = OpenAI(
            base_url=CONFIG.embedding_base_url,
            api_key=CONFIG.embedding_api_key or "not-needed",
        )
        self._dim = CONFIG.embedding_dim
        self._headers = {
            "Authorization": f"Bearer {CONFIG.embedding_api_key}",
            "Content-Type": "application/json",
        }
        self._pool = ThreadPoolExecutor(max_workers=CONFIG.embedding_concurrency)
        self._ensure_schema()

    # ------------------------------------------------------------------ db
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(CONFIG.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS items (
                    rid INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    seq INTEGER NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_items_user ON items(user_id, status)")
            conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_items USING vec0(embedding float[{self._dim}])"
            )
            conn.commit()

    # ------------------------------------------------------------- embed
    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if CONFIG.embedding_mode == "multimodal":
            return self._embed_multimodal(texts)
        resp = self._client.embeddings.create(model=CONFIG.embedding_model, input=texts)
        return [d.embedding for d in resp.data]

    def _embed_one_multimodal(self, text: str) -> list[float]:
        url = CONFIG.embedding_base_url.rstrip("/") + "/embeddings/multimodal"
        payload = {
            "model": CONFIG.embedding_model,
            "input": [{"type": "text", "text": text}],
        }
        # One retry; the multimodal endpoint accepts one text item per request.
        for attempt in range(2):
            try:
                r = httpx.post(url, headers=self._headers, json=payload, timeout=60.0)
                if r.status_code == 200:
                    return r.json()["data"]["embedding"]
                last = f"HTTP {r.status_code}: {r.text[:200]}"
            except Exception as exc:  # noqa: BLE001
                last = repr(exc)
            time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"multimodal embedding failed: {last}")

    def _embed_multimodal(self, texts: list[str]) -> list[list[float]]:
        # Preserve order while fanning out concurrent single-text requests.
        results = list(self._pool.map(self._embed_one_multimodal, texts))
        return results

    # ---------------------------------------------------------------- add
    def add_messages(
        self,
        user_id: str,
        session_id: str,
        messages: list[dict],
    ) -> int:
        """Embed and persist a chunk of messages. Returns count written."""
        # Use the later of message timestamp or now; fallback to wall clock.
        now_ms = time.time() * 1000.0
        contents = [m.get("content", "") for m in messages]
        contents = [c if isinstance(c, str) else _flatten_content(c) for c in contents]
        embeddings = self.embed(contents)

        with self._connect() as conn:
            seq = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM items WHERE user_id=?",
                (user_id,),
            ).fetchone()[0]
            for i, (msg, emb) in enumerate(zip(messages, embeddings)):
                ts = msg.get("timestamp") or now_ms
                created_at = float(ts) / 1000.0
                role = msg.get("role", "user")
                content = contents[i]
                cur = conn.execute(
                    "INSERT INTO items(user_id,session_id,role,content,created_at,status,seq)"
                    " VALUES(?,?,?,?,?,'active',?)",
                    (user_id, session_id, role, content, created_at, seq + i),
                )
                rid = cur.lastrowid
                conn.execute(
                    "INSERT INTO vec_items(rowid, embedding) VALUES(?, ?)",
                    (rid, _pack(emb)),
                )
            conn.commit()
        return len(messages)

    # ------------------------------------------------------------ search
    def search(self, user_id: str, query: str, top_k: int) -> list[dict]:
        qvec = self.embed([query])[0]
        with self._connect() as conn:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            rows = conn.execute(
                """
                SELECT i.rid, i.user_id, i.content, i.created_at, i.status, i.seq, v.distance
                FROM vec_items v
                JOIN items i ON i.rid = v.rowid
                WHERE v.embedding MATCH ? AND k = ?
                """,
                (_pack(qvec), CONFIG.candidate_pool),
            ).fetchall()

        # Strict per-user_id filter: never leak across users.
        mine = [r for r in rows if r["user_id"] == user_id and r["status"] == "active"]

        # Recency boost: combine ANN distance with age.
        newest = max((r["created_at"] for r in mine), default=time.time())
        ranked = []
        for r in mine:
            dist = float(r["distance"])
            age_norm = 1.0 - min((newest - r["created_at"]) / (86400.0 * 30.0), 1.0)
            score = -dist + CONFIG.recency_weight * age_norm
            ranked.append((score, r))
        ranked.sort(key=lambda x: x[0], reverse=True)

        results = []
        for score, r in ranked[:top_k]:
            results.append(
                {
                    "id": f"mem_{r['rid']}",
                    "content": r["content"],
                    "score": round(score, 6),
                    "created_at": _iso(r["created_at"]),
                }
            )
        return results

    # ------------------------------------------------------- governance
    def supersede_old(self, user_id: str, keyword: str, except_seq: int) -> int:
        """Mark earlier active records mentioning a keyword as superseded.

        Used by the governance layer when a newer message restates an updated
        fact. Conservative: only triggers on explicit update cues.
        """
        if not keyword:
            return 0
        like = f"%{keyword.lower()}%"
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE items SET status='superseded' WHERE user_id=? AND status='active'"
                " AND seq < ? AND lower(content) LIKE ?",
                (user_id, except_seq, like),
            )
            conn.commit()
            return cur.rowcount


def _flatten_content(part) -> str:
    """Multimodal content parts -> plain text for the text track."""
    if isinstance(part, str):
        return part
    if isinstance(part, list):
        out = []
        for p in part:
            if isinstance(p, dict):
                if p.get("type") == "text":
                    out.append(str(p.get("text", "")))
                elif p.get("type") == "image_url":
                    out.append("[image]")
            else:
                out.append(str(p))
        return "\n".join(out)
    return str(part)


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
