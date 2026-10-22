"""Offline API checks with a disposable database and synthetic embeddings."""
from __future__ import annotations

import gc
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TEST_DIR = tempfile.TemporaryDirectory(prefix="aml-api-contract-")
os.environ.update({
    "AML_DB_PATH": str(Path(TEST_DIR.name) / "test.db"),
    "AML_AUTH_TOKEN": "offline-contract-token",
    "AML_GOVERNANCE": "false",
    "EMBEDDING_API_KEY": "offline",
    "EMBEDDING_DIM": "8",
})

import store as store_module


def fake_embed(self, texts):
    return [[(b - 128) / 128 for b in hashlib.sha256(t.encode()).digest()[:8]]
            for t in texts]


store_module.MemoryStore.embed = fake_embed

import app as app_module
from fastapi.testclient import TestClient

app_module.AUDIT_LOG = str(Path(TEST_DIR.name) / "audit.jsonl")
AUTH = {"Authorization": "Bearer offline-contract-token"}


class ApiContractTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app_module.app, raise_server_exceptions=False)

    def tearDown(self):
        self.client.close()

    def body(self, suffix=""):
        return {"request_id": "chunk-1", "user_id": self.id() + suffix,
                "session_id": "session-1", "messages": [
                    {"role": "user", "content": "My favorite color is green."}]}

    def test_health_is_public(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["embedding_model"], "text-embedding-v4")

    def test_auth_is_required_for_both_routes(self):
        self.assertEqual(self.client.post("/add", json=self.body()).status_code, 401)
        self.assertEqual(self.client.post("/search", json={
            "query": "color", "user_id": "u", "top_k": 5}).status_code, 401)

    def test_supported_auth_schemes(self):
        for headers in (AUTH, {"Authorization": "Token offline-contract-token"},
                        {"X-Api-Key": "offline-contract-token"}):
            self.assertEqual(self.client.post("/add", json=self.body(),
                                             headers=headers).status_code, 200)

    def test_add_replay_does_not_duplicate_memory(self):
        body = self.body()
        with patch.object(app_module.store, "embed", wraps=app_module.store.embed) as embed:
            first = self.client.post("/add", json=body, headers=AUTH)
            replay = self.client.post("/add", json=body, headers=AUTH)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), replay.json())
        self.assertEqual(embed.call_count, 1)
        for name in ("request_id", "user_id", "session_id"):
            self.assertEqual(first.json()[name], body[name])
        with app_module.store._connect() as conn:
            n = conn.execute("SELECT count(*) FROM items WHERE user_id=?",
                             (body["user_id"],)).fetchone()[0]
        self.assertEqual(n, 1)

    def test_conflicting_request_id_is_409(self):
        body = self.body()
        self.assertEqual(self.client.post("/add", json=body, headers=AUTH).status_code, 200)
        body["messages"][0]["content"] = "A different request body."
        r = self.client.post("/add", json=body, headers=AUTH)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["detail"]["reason"], "request_id_conflict")

    def test_invalid_content_is_rejected_before_embedding(self):
        with patch.object(app_module.store, "embed") as embed:
            for content in (None, "", "  ", [], 123, {"text": "unsupported shape"}):
                body = self.body()
                body["messages"][0]["content"] = content
                self.assertEqual(self.client.post("/add", json=body, headers=AUTH).status_code, 422)
            embed.assert_not_called()

    def test_top_k_is_required_and_positive(self):
        for k in (None, 0, -1):
            body = {"query": "color", "user_id": "u"}
            if k is not None:
                body["top_k"] = k
            self.assertEqual(self.client.post("/search", json=body, headers=AUTH).status_code, 422)

    def test_search_contract_and_scope(self):
        body = self.body()
        self.client.post("/add", json=body, headers=AUTH)
        r = self.client.post("/search", json={"query": "favorite color", "options": ["red", "green"],
            "user_id": body["user_id"], "top_k": 1}, headers=AUTH)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()["data"]), 1)
        self.assertTrue(r.json()["data"][0]["id"])
        self.assertTrue(r.json()["data"][0]["content"])
        r = self.client.post("/search", json={"query": "favorite color",
            "user_id": body["user_id"] + "-other", "top_k": 100}, headers=AUTH)
        self.assertEqual(r.json(), {"data": []})

    def test_query_payload_is_not_audited(self):
        marker = "SYNTHETIC_PRIVATE_QUERY_MARKER"
        self.client.post("/search", json={"query": marker,
            "user_id": self.id(), "top_k": 1}, headers=AUTH)
        self.assertNotIn(marker, Path(app_module.AUDIT_LOG).read_text(encoding="utf-8"))

    def test_failed_add_has_status_audit_without_payload(self):
        with patch.object(app_module.store, "embed", side_effect=RuntimeError("synthetic failure")):
            r = self.client.post("/add", json=self.body(), headers=AUTH)
        self.assertEqual(r.status_code, 500)
        records = [json.loads(s) for s in Path(app_module.AUDIT_LOG).read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(r.get("path") == "/add" and r.get("status") == 500 for r in records))


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        app_module.store._pool.shutdown()
        gc.collect()
        TEST_DIR.cleanup()
