"""Prepare a reusable text-embedding-v4 cache for invented capability fixtures.

This command calls the provider only with --prepare. It reads the exported
synthetic fixtures, never a deployment database. Supply EMBEDDING_API_KEY or
DASHSCOPE_API_KEY through the environment. The cache contains no credentials.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path

import httpx


MODEL = "text-embedding-v4"
DIM = 1024
API = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"


def validate(document):
    if document.get("model") != MODEL or document.get("dimensions") != DIM:
        raise ValueError("cache model or dimensions do not match")
    vectors = document.get("vectors")
    if not isinstance(vectors, dict) or any(
        not isinstance(vector, list) or len(vector) != DIM
        or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in vector)
        for vector in vectors.values()
    ):
        raise ValueError("cache contains malformed vectors")
    return vectors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prepare", action="store_true", help="explicitly call the embedding provider")
    args = parser.parse_args()
    exported = json.loads(args.fixtures.read_text(encoding="utf-8"))
    cases, texts = exported["cases"], exported["texts"]
    fixture_hash = hashlib.sha256(json.dumps(cases, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    if exported.get("fixture_sha256") != fixture_hash:
        raise ValueError("fixture hash mismatch")
    if not isinstance(texts, list) or any(not isinstance(text, str) or not text.strip() for text in texts):
        raise ValueError("fixtures must export nonempty synthetic texts")
    texts = list(dict.fromkeys(texts))
    document = (json.loads(args.output.read_text(encoding="utf-8")) if args.output.exists()
                else {"model": MODEL, "dimensions": DIM, "api": API, "vectors": {}})
    vectors = validate(document)
    missing = [text for text in texts if text not in vectors]
    print(json.dumps({"fixture_sha256": fixture_hash, "unique_texts": len(texts),
                      "missing_vectors": len(missing), "provider_calls_enabled": args.prepare}), flush=True)
    if not args.prepare:
        return 0
    key = os.environ.get("EMBEDDING_API_KEY") or os.environ.get("DASHSCOPE_API_KEY")
    if not key and missing:
        raise ValueError("set an embedding credential in the environment")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    calls = 0
    try:
        with httpx.Client(timeout=httpx.Timeout(45., connect=10.)) as client:
            for offset in range(0, len(missing), 10):
                batch = missing[offset:offset + 10]
                response = client.post(API, headers={"Authorization": "Bearer " + key},
                    json={"model": MODEL, "input": batch, "dimensions": DIM, "encoding_format": "float"})
                calls += 1
                if response.status_code != 200:
                    raise RuntimeError("embedding preparation HTTP status=" + str(response.status_code))
                data = sorted(response.json()["data"], key=lambda item: item["index"])
                if len(data) != len(batch) or [item["index"] for item in data] != list(range(len(batch))):
                    raise ValueError("embedding response count or index mismatch")
                for text, item in zip(batch, data):
                    vectors[text] = item["embedding"]
                validate(document)
                document["latest_fixture_sha256"] = fixture_hash
                scratch = args.output.with_suffix(args.output.suffix + ".tmp")
                scratch.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
                scratch.replace(args.output)
                if calls % 10 == 0 or offset + 10 >= len(missing):
                    print(json.dumps({"vectors_prepared": min(offset + 10, len(missing)),
                                      "new_vectors_total": len(missing), "requests": calls}), flush=True)
    except httpx.HTTPError as error:
        raise RuntimeError("embedding transport failed: " + type(error).__name__) from None
    print(json.dumps({"new_vectors": len(missing), "requests": calls,
                      "cache_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest()}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
