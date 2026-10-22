"""Bounded ranking-only HTTP client; no configuration, credentials or logs.

Long query/document copies are truncated at UTF-8 boundaries only for ranking.
Callers must keep the original documents as their returned evidence. A missing
key, exhausted concurrency slot, oversized request, network error or invalid
response returns None so the caller can preserve its original ranking.
"""
from __future__ import annotations

import json
import math
import threading
from typing import Callable
import urllib.parse
import urllib.request

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"
DEFAULT_MODEL = "qwen3-rerank"
MAX_QUERY_BYTES = 3000
MAX_DOCUMENT_BYTES = 3500
MAX_DOCUMENTS = 64
MAX_WEIGHTED_INPUT_BYTES = 100000
MAX_TIMEOUT_SECONDS = 6.0
MAX_CONCURRENCY = 8
_MAX_RESPONSE_BYTES = 262144
_SERVICE_PATH = "/services/rerank/text-rerank/text-rerank"
# Shared across instances so constructing a second client cannot double the
# provider concurrency. A full pool falls back immediately instead of queuing.
_PROVIDER_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENCY)

Transport = Callable[[str, dict, dict, float], tuple[int, dict]]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http_transport(url: str, payload: dict, headers: dict, timeout: float) -> tuple[int, dict]:
    request = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"),
        headers=headers, method="POST",
    )
    # Do not forward an Authorization header through a provider redirect.
    opener = urllib.request.build_opener(_NoRedirect())
    with opener.open(request, timeout=timeout) as response:
        status = response.status
        if status != 200:
            return status, {}
        body = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(body) > _MAX_RESPONSE_BYTES:
            raise ValueError("reranker response exceeds its bound")
        return status, json.loads(body.decode("utf-8"))


def _endpoint(base_url: str) -> str | None:
    if not isinstance(base_url, str):
        return None
    try:
        parsed = urllib.parse.urlsplit(base_url.strip())
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment):
            return None
        path = parsed.path.rstrip("/")
        if path.endswith(_SERVICE_PATH):
            pass
        elif path.endswith("/api/v1"):
            path += _SERVICE_PATH
        elif not path:
            path = "/api/v1" + _SERVICE_PATH
        else:
            return None
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    except (TypeError, ValueError):
        return None


def _utf8_prefix(text: str, limit: int) -> str:
    encoded = text.encode("utf-8")
    return text if len(encoded) <= limit else encoded[:limit].decode("utf-8", errors="ignore")


class RerankerClient:
    """Return a complete permutation of input indexes, or fail safely.

    ``transport(url, payload, headers, timeout) -> (HTTP status, JSON dict)``
    can be injected for offline checks. The default performs one HTTP request,
    with no retries. Credentials and application options are explicit; it
    never reads secret files, global application configuration or deployment
    data. Standard urllib proxy discovery still follows the host environment.
    """

    def __init__(
        self, api_key: str, base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL, timeout: float = MAX_TIMEOUT_SECONDS,
        *, transport: Transport | None = None,
    ):
        self._api_key = api_key.strip() if isinstance(api_key, str) else ""
        self._endpoint = _endpoint(base_url)
        self._model = model.strip() if isinstance(model, str) else ""
        try:
            value = float(timeout)
            self._timeout = min(value, MAX_TIMEOUT_SECONDS) if math.isfinite(value) and value > 0 else None
        except (TypeError, ValueError, OverflowError):
            self._timeout = None
        self._transport = _http_transport if transport is None else transport

    def rerank(self, query: str, documents: list[str]) -> list[int] | None:
        if not self._api_key or not self._endpoint or not self._model or self._timeout is None:
            return None
        if not isinstance(query, str) or not query.strip() or not isinstance(documents, (list, tuple)):
            return None
        if not 1 <= len(documents) <= MAX_DOCUMENTS:
            return None
        if any(not isinstance(document, str) or not document.strip() for document in documents):
            return None
        try:
            # These are disposable ranking copies. Never write them back to
            # the caller's source list or substitute them for original context.
            ranking_query = _utf8_prefix(query, MAX_QUERY_BYTES)
            ranking_documents = [_utf8_prefix(document, MAX_DOCUMENT_BYTES) for document in documents]
            if not ranking_query.strip() or any(not document.strip() for document in ranking_documents):
                return None
            weighted_size = len(ranking_query.encode("utf-8")) * len(ranking_documents)
            weighted_size += sum(len(document.encode("utf-8")) for document in ranking_documents)
            # The query is charged once per document, matching the provider's
            # request-limit formula. Bytes are a conservative input guard,
            # not a claim to implement the provider's tokenizer.
            if weighted_size > MAX_WEIGHTED_INPUT_BYTES:
                return None
            payload = {"model": self._model, "input": {"query": ranking_query, "documents": ranking_documents},
                       "parameters": {"top_n": len(ranking_documents), "return_documents": False}}
            headers = {"Authorization": "Bearer " + self._api_key, "Content-Type": "application/json"}
            if not _PROVIDER_SLOTS.acquire(blocking=False):
                return None
            try:
                status, response = self._transport(self._endpoint, payload, headers, self._timeout)
            finally:
                _PROVIDER_SLOTS.release()
            if status != 200 or not isinstance(response, dict):
                return None
            output = response.get("output")
            results = output.get("results") if isinstance(output, dict) else None
            if not isinstance(results, list) or len(results) != len(documents):
                return None
            scored, seen = [], set()
            for item in results:
                if not isinstance(item, dict):
                    return None
                index, score = item.get("index"), item.get("relevance_score")
                if type(index) is not int or not 0 <= index < len(documents) or index in seen:
                    return None
                if (isinstance(score, bool) or not isinstance(score, (int, float))
                        or not math.isfinite(score) or not 0 <= score <= 1):
                    return None
                seen.add(index)
                scored.append((index, score))
            # Keep every document, including zero scores; ties retain provider
            # order. A score never becomes a deletion threshold or confidence.
            return [index for index, _ in sorted(scored, key=lambda pair: -pair[1])]
        except Exception:
            # Do not emit provider error bodies, documents, URLs or credentials.
            return None
