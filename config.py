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
    # --- Embedding endpoint (OpenAI-compatible / Volcengine Ark) ---
    # base_url=https://ark.cn-beijing.volces.com/api/v3
    # The API key can also live in .secrets/ark_api_key.txt (one line, no quotes).
    #
    # EMBEDDING_MODE:
    #   "standard"   -> POST /embeddings, OpenAI-compatible, model=text endpoint
    #   "multimodal" -> POST /embeddings/multimodal, one text item per request,
    #                   used for the doubao-embedding-vision endpoint which embeds
    #                   text (and images) and returns 2048-dim vectors.
    embedding_base_url: str = os.environ.get("EMBEDDING_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
    embedding_api_key: str = os.environ.get("EMBEDDING_API_KEY", _read_secret("ark_api_key.txt"))
    embedding_model: str = os.environ.get("EMBEDDING_MODEL", "ep-20261015115102-j7n5s")
    embedding_mode: str = os.environ.get("EMBEDDING_MODE", "multimodal")
    embedding_dim: int = int(os.environ.get("EMBEDDING_DIM", "2048"))
    embedding_concurrency: int = int(os.environ.get("EMBEDDING_CONCURRENCY", "8"))

    # --- Storage ---
    db_path: str = os.environ.get("AML_DB_PATH", "aml_memory.db")

    # --- Retrieval ---
    # How many ANN candidates to pull globally before the per-user_id filter.
    # Must be large enough to contain all of the queried user's active records.
    candidate_pool: int = int(os.environ.get("AML_CANDIDATE_POOL", "4000"))
    recency_weight: float = float(os.environ.get("AML_RECENCY_WEIGHT", "0.15"))

    # --- Governance (MemoryShield+) ---
    # Disabled by default: the AML benchmark data is clean (no poisoning), so an
    # aggressive keyword-based quarantine would only hurt recall. Supersession
    # (prefer the later statement when a newer message restates a fact) stays on.
    governance_enabled: bool = _bool("AML_GOVERNANCE", True)
    quarantine_enabled: bool = _bool("AML_QUARANTINE", False)

    # --- Auth ---
    # If set, requests must present this value as Bearer token or X-Api-Key.
    # Falls back to .secrets/aml_auth_token.txt so the server can be launched
    # without exporting the token in the shell.
    auth_token: str = os.environ.get("AML_AUTH_TOKEN", _read_secret("aml_auth_token.txt"))

    # --- Server ---
    host: str = os.environ.get("AML_HOST", "0.0.0.0")
    port: int = int(os.environ.get("AML_PORT", "8080"))


CONFIG = Config()
