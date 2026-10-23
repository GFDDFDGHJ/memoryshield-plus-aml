"""Runtime configuration for the AML Add/Search submission server.

All settings come from environment variables so the same image runs locally,
on a server, or behind a tunnel without code changes.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _read_secret(filename: str) -> str:
    path = os.path.join(os.path.dirname(__file__), ".secrets", filename)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


@dataclass(frozen=True)
class Config:
    # --- Embedding endpoint ---
    # The default is the Beijing DashScope endpoint. Keys are region-specific;
    # set EMBEDDING_BASE_URL for another region or a workspace-specific domain.
    # EMBEDDING_API_KEY overrides the project secret file, which in turn
    # overrides the machine-wide DASHSCOPE_API_KEY.
    # Cycle 2 Open-source Methods requires text-embedding-v4. Keep its vectors
    # in a separate database from the previous Ark deployment.
    embedding_base_url: str = os.environ.get(
        "EMBEDDING_BASE_URL", os.environ.get(
            "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    )
    embedding_api_key: str = os.environ.get(
        "EMBEDDING_API_KEY",
        _read_secret("dashscope_api_key.txt") or os.environ.get("DASHSCOPE_API_KEY", ""),
    )
    embedding_model: str = os.environ.get("EMBEDDING_MODEL", "text-embedding-v4")
    embedding_mode: str = os.environ.get("EMBEDDING_MODE", "standard")
    embedding_dim: int = int(os.environ.get("EMBEDDING_DIM", "1024"))
    embedding_batch_size: int = int(os.environ.get("EMBEDDING_BATCH_SIZE", "10"))
    embedding_concurrency: int = int(os.environ.get("EMBEDDING_CONCURRENCY", "8"))

    # --- Storage ---
    db_path: str = os.environ.get(
        "AML_DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "aml_text_v4.db")
    )

    # --- Retrieval ---
    # Legacy setting retained for launch compatibility; exact per-user search
    # does not use a shared ANN candidate pool.
    candidate_pool: int = int(os.environ.get("AML_CANDIDATE_POOL", "4000"))
    # Only explicit current-state queries use a recency tie-break, capped at
    # a 0.01 cosine gap. Ordinary and historical queries have no age boost.
    recency_weight: float = float(os.environ.get("AML_RECENCY_WEIGHT", "0.10"))
    # Preserve the compact front of Search, then use a bounded continuation for
    # large selected histories. These are character ceilings, not token counts.
    extended_context_chars: int = int(os.environ.get("AML_EXTENDED_CONTEXT_CHARS", "160000"))
    context_continuation_chars: int = int(os.environ.get("AML_CONTEXT_CONTINUATION_CHARS", "128000"))
    # Optional semantic second-stage ranking; a timeout or invalid response
    # retains deterministic dense/sparse recall. This does not generate answers.
    rerank_enabled: bool = _bool("AML_RERANK", False)
    rerank_model: str = os.environ.get("AML_RERANK_MODEL", "qwen3-rerank")
    rerank_base_url: str = os.environ.get("AML_RERANK_BASE_URL", "https://dashscope.aliyuncs.com/api/v1")
    rerank_api_key: str = os.environ.get("AML_RERANK_API_KEY", embedding_api_key)
    rerank_timeout: float = float(os.environ.get("AML_RERANK_TIMEOUT", "6"))

    # --- Governance (MemoryShield+) ---
    # OFF by default as of 2026-10-21. Measured against the real AML DB, the
    # supersession rule in governance.py fired 132 times and hid 1480 of 3974
    # rows (37%), because _TOPIC_PATTERNS extracts pronouns and filler from
    # dialogue ("Yes, that is quite true." -> topic='that') and then does a
    # LIKE '%that%' bulk demotion. Killed rows sit at 31% depth of each user's
    # history, which is exactly the early history that long-range summary (F1)
    # and event-ordering (C2) questions need -- F1 scored 0.00.
    # Re-enable only after supersession is rewritten to match on extracted
    # (subject, predicate, value) triples with a per-event kill cap.
    governance_enabled: bool = _bool("AML_GOVERNANCE", False)
    quarantine_enabled: bool = _bool("AML_QUARANTINE", False)

    # --- Auth ---
    # If set, requests must present this value as Bearer token or X-Api-Key.
    # Falls back to .secrets/aml_auth_token.txt so the server can be launched
    # without exporting the token in the shell.
    auth_token: str = os.environ.get("AML_AUTH_TOKEN", _read_secret("aml_auth_token.txt"))

    # --- Server ---
    host: str = os.environ.get("AML_HOST", "0.0.0.0")
    port: int = int(os.environ.get("AML_PORT", "8080"))
    version: str = os.environ.get("AML_VERSION", "v3.13.0")


CONFIG = Config()
