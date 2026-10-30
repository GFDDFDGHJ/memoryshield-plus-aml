"""Transactional index of literal source spans selected during GPT Add.

The model never supplies searchable claims.  It selects offsets in original
messages; this index may only give those same source rows a bounded relevance
boost.  The caller owns the transaction containing raw rows and Add receipts.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import time
import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from add_memory import ExtractionResult


MAX_SOURCE_BOOST = 0.03
_MODEL = re.compile(r"gpt-4o-mini(?:-\d{4}-\d{2}-\d{2})?\Z")
_KINDS = frozenset({"fact", "event", "rule", "preference", "procedure"})
_STOP = frozenset(
    "a an the and or of to for in on at by with from as is are was were be been "
    "being do does did can could would should will shall may might please what "
    "which who whom whose when where why how tell give show list recall remember "
    "about me my i we our you your they their it its this that these those".split()
)


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create companion tables without committing the caller's transaction."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS llm_memory_spans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            source_rid INTEGER NOT NULL,
            start_offset INTEGER NOT NULL CHECK(start_offset >= 0),
            end_offset INTEGER NOT NULL CHECK(end_offset > start_offset),
            kind TEXT NOT NULL,
            source_role TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            UNIQUE(user_id, source_rid, start_offset, end_offset, kind),
            FOREIGN KEY(source_rid) REFERENCES items(rid) ON DELETE CASCADE
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_spans_user_source "
                 "ON llm_memory_spans(user_id, source_rid)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS add_llm_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            request_id TEXT,
            model TEXT NOT NULL,
            prompt_tokens INTEGER NOT NULL CHECK(prompt_tokens >= 0),
            completion_tokens INTEGER NOT NULL CHECK(completion_tokens >= 0),
            call_count INTEGER NOT NULL CHECK(call_count >= 1),
            created_at REAL NOT NULL
        )
    """)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_add_llm_usage_request "
                 "ON add_llm_usage(user_id, request_id) WHERE request_id IS NOT NULL")


def _integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _plain_content(content: object) -> str:
    # Matches the store's text-track normalization, including retained image
    # placeholders. No new content is generated or interpreted here.
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    out.append(str(part.get("text", "")))
                elif part.get("type") == "image_url":
                    out.append("[image]")
            else:
                out.append(str(part))
        return "\n".join(out)
    return str(content)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def store_extraction(
    conn: sqlite3.Connection,
    user_id: str,
    session_id: str,
    source_rids: list[int],
    messages: list[dict],
    result: ExtractionResult,
    *,
    request_id: str | None = None,
) -> None:
    """Validate and persist source selections and usage in an existing Add.

    Validation completes before writes. Any error must propagate to the Add
    transaction so raw rows, selections, usage and replay receipt roll back
    together. An empty selection is valid; an absent model call is not.
    """
    if not conn.in_transaction:
        raise ValueError("GPT source index requires the Add transaction")
    if (not isinstance(user_id, str) or not user_id
            or not isinstance(session_id, str) or not session_id):
        raise ValueError("invalid GPT source owner")
    if request_id is not None and (not isinstance(request_id, str) or not request_id):
        raise ValueError("invalid GPT Add request ID")
    if (len(source_rids) != len(messages) or not messages
            or any(not _integer(rid) or rid <= 0 for rid in source_rids)
            or len(set(source_rids)) != len(source_rids)):
        raise ValueError("invalid GPT source references")
    model = result.model
    if not isinstance(model, str) or not _MODEL.fullmatch(model):
        raise ValueError("invalid GPT Add model")
    usage = (result.prompt_tokens, result.completion_tokens, result.call_count)
    if (any(not _integer(value) for value in usage)
            or usage[0] < 0 or usage[1] < 0 or usage[2] < 1):
        raise ValueError("invalid GPT Add token usage")

    sources: list[tuple[int, str, str, str]] = []
    for rid, message in zip(source_rids, messages):
        if not isinstance(message, dict):
            raise ValueError("invalid GPT source message")
        content = _plain_content(message.get("content", ""))
        role = message.get("role", "user")
        row = conn.execute(
            "SELECT user_id, session_id, role, content, status FROM items WHERE rid=?",
            (rid,),
        ).fetchone()
        if (row is None or tuple(row) != (user_id, session_id, role, content, "active")):
            raise ValueError("GPT source ownership or content mismatch")
        sources.append((rid, content, role, _digest(content)))

    selections: list[tuple] = []
    for span in result.spans:
        index, start, end, kind = span.message_index, span.start, span.end, span.kind
        if (not _integer(index) or not 0 <= index < len(sources)
                or not _integer(start) or not _integer(end)):
            raise ValueError("invalid GPT source span reference")
        rid, content, role, source_hash = sources[index]
        if (not 0 <= start < end <= len(content) or not content[start:end].strip()
                or not isinstance(kind, str) or kind not in _KINDS):
            raise ValueError("invalid GPT source span")
        selections.append((user_id, session_id, rid, start, end, kind, role, source_hash))

    conn.executemany(
        "INSERT OR IGNORE INTO llm_memory_spans "
        "(user_id,session_id,source_rid,start_offset,end_offset,kind,source_role,source_sha256) "
        "VALUES(?,?,?,?,?,?,?,?)", selections,
    )
    conn.execute(
        "INSERT INTO add_llm_usage "
        "(user_id,session_id,request_id,model,prompt_tokens,completion_tokens,call_count,created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (user_id, session_id, request_id, model, *usage, time.time()),
    )


def _tokens(text: str) -> set[str]:
    text = unicodedata.normalize("NFKC", text).casefold()
    tokens = set(re.findall(r"[a-z0-9]+(?:['_-][a-z0-9]+)*", text)) - _STOP
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        tokens.update(run[index:index + 2] for index in range(len(run) - 1))
    return tokens


def rank_source_boosts(
    conn: sqlite3.Connection,
    user_id: str,
    query: str,
    source_ids: list[int],
) -> dict[int, float]:
    """Return at most 0.03 per eligible source, never new evidence rows.

    The caller must pass only source IDs retained by its existing privacy,
    scope and exclusion rules. The exact-user/active join is checked again;
    a changed source or malformed index row receives no boost.
    """
    query_tokens = _tokens(query)
    if not query_tokens or not source_ids:
        return {}
    if any(not _integer(rid) or rid <= 0 for rid in source_ids):
        raise ValueError("invalid GPT retrieval source reference")
    ids = sorted(set(source_ids))
    boosts: dict[int, float] = {}
    for start in range(0, len(ids), 500):
        batch = ids[start:start + 500]
        placeholders = ",".join("?" for _ in batch)
        rows = conn.execute(
            "SELECT s.source_rid,s.start_offset,s.end_offset,s.source_role,s.source_sha256,"
            "s.session_id,i.role,i.content,i.session_id "
            "FROM llm_memory_spans s JOIN items i ON i.rid=s.source_rid "
            "WHERE s.user_id=? AND i.user_id=? AND i.status='active' "
            f"AND s.source_rid IN ({placeholders})",
            (user_id, user_id, *batch),
        )
        for row in rows:
            rid, left, right, role, source_hash, session, live_role, content, live_session = row
            if (role != live_role or session != live_session
                    or not isinstance(content, str) or source_hash != _digest(content)
                    or not _integer(left) or not _integer(right)
                    or not 0 <= left < right <= len(content)):
                continue
            overlap = query_tokens & _tokens(content[left:right])
            if overlap:
                boost = MAX_SOURCE_BOOST * len(overlap) / len(query_tokens)
                boosts[rid] = max(boosts.get(rid, 0.0), boost)
    return boosts
