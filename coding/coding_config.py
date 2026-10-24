"""Coding-only configuration. No imports or database paths from Textual."""
from dataclasses import dataclass
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def _secret(name):
    try:
        return (ROOT / '.secrets' / name).read_text(encoding='utf-8').strip()
    except OSError:
        return ''

@dataclass(frozen=True)
class CodingConfig:
    version: str = 'v1.0.0'
    db_path: str = os.environ.get('CODING_DB_PATH', str(ROOT / 'aml_coding_v4.db'))
    auth_token: str = os.environ.get('CODING_AUTH_TOKEN', '') or _secret('auth_token.txt')
    embedding_key: str = os.environ.get('CODING_EMBEDDING_API_KEY', '') or _secret('dashscope_api_key.txt') or os.environ.get('DASHSCOPE_API_KEY', '')
    embedding_url: str = 'https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings'
    embedding_model: str = 'text-embedding-v4'
    dim: int = 1024
    rerank_model: str = 'qwen3-rerank'
    rerank_enabled: bool = os.environ.get('CODING_RERANK', 'true').lower() in {'1','true','yes'}
    source_revision: str = os.environ.get('CODING_SOURCE_REVISION', '')

CONFIG = CodingConfig()
