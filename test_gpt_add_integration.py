"""Offline GPT Add transaction/API checks with invented data and temporary DBs.

No key files are read and neither model client can contact a network endpoint.
Run this script in its own process, as with the other offline store suites.
"""
from __future__ import annotations

import builtins
import copy
import gc
import hashlib
import io
import json
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


TEST_ROOT = tempfile.TemporaryDirectory(prefix="aml-gpt-integration-")
_original_open = builtins.open


def _without_secret_files(file, *args, **kwargs):
    # Config's fallback expressions can open secret files even when an
    # environment override is present. Prevent those reads at import time.
    try:
        is_secret = ".secrets" in Path(file).parts
    except TypeError:
        is_secret = False
    if is_secret:
        return io.StringIO("")
    return _original_open(file, *args, **kwargs)


_ENV = {
    "AML_DB_PATH": str(Path(TEST_ROOT.name) / "import-only.db"),
    "AML_AUTH_TOKEN": "offline-gpt-contract-token",
    "AML_GOVERNANCE": "false", "AML_RERANK": "false",
    "OPENAI_API_KEY": "offline-not-a-real-key",
    "EMBEDDING_API_KEY": "offline-not-a-real-key",
    "EMBEDDING_DIM": "8",
}
with patch.dict(os.environ, _ENV), patch("builtins.open", _without_secret_files):
    import store as store_module
    from add_memory import AddMemoryError, ExtractionResult, MemorySpan


class NoNetworkEmbeddingClient:
    def __init__(self):
        self.embeddings = SimpleNamespace(create=self.forbidden)

    @staticmethod
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline tests must not invoke an embedding API")

    def close(self):
        pass


class FakeExtractor:
    def __init__(self):
        self.calls = []
        self.error = None
        self.result = None
        self.delay = 0
        self._lock = threading.Lock()

    def extract(self, messages):
        with self._lock:
            self.calls.append(copy.deepcopy(messages))
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise self.error
        if self.result is not None:
            return self.result
        spans = [MemorySpan(i, 0, min(len(message["content"]), 240), "fact")
                 for i, message in enumerate(messages) if message["content"]]
        return ExtractionResult(spans, 101, 19, "gpt-4o-mini-2024-07-18", 1)


# Importing app creates one store. Ensure even that store is isolated and has
# fake dependencies before the module is allowed to initialize it.
_import_config = replace(store_module.CONFIG, db_path=str(Path(TEST_ROOT.name) / "import-only.db"),
                         embedding_dim=8, embedding_api_key="offline-not-a-real-key",
                         add_llm_api_key="offline-not-a-real-key", rerank_enabled=False,
                         auth_token="offline-gpt-contract-token", governance_enabled=False)
with patch.object(store_module, "CONFIG", _import_config), \
        patch.object(store_module, "OpenAI", return_value=NoNetworkEmbeddingClient()), \
        patch.object(store_module, "AddMemoryExtractor", return_value=FakeExtractor()):
    with patch.dict(os.environ, _ENV), patch("builtins.open", _without_secret_files):
        import app as app_module
from fastapi.testclient import TestClient

_import_store = app_module.store
AUTH = {"Authorization": "Bearer offline-gpt-contract-token"}


class GptAddIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="case-", dir=TEST_ROOT.name)
        self.config = replace(_import_config, db_path=str(Path(self.directory.name) / "memory.db"))
        self.config_patch = patch.object(store_module, "CONFIG", self.config)
        self.config_patch.start()
        self.extractor = FakeExtractor()
        with patch.object(store_module, "OpenAI", return_value=NoNetworkEmbeddingClient()):
            self.store = store_module.MemoryStore(add_extractor=self.extractor)
        self.embed_calls = []
        self.embed_error = None
        self.embed_delay = 0
        self.embed_lock = threading.Lock()

        def local_embed(texts):
            with self.embed_lock:
                self.embed_calls.append(list(texts))
            if self.embed_delay:
                time.sleep(self.embed_delay)
            if self.embed_error:
                raise self.embed_error
            return [[(byte - 128) / 128 for byte in hashlib.sha256(text.encode("utf-8")).digest()[:8]]
                    for text in texts]

        self.store.embed = local_embed
        self.app_patches = [patch.object(app_module, "store", self.store),
                            patch.object(app_module, "CONFIG", self.config),
                            patch.object(app_module, "AUDIT_LOG", str(Path(self.directory.name) / "audit.jsonl"))]
        for active_patch in self.app_patches:
            active_patch.start()
        self.client = TestClient(app_module.app, raise_server_exceptions=False)

    def tearDown(self):
        self.client.close()
        for active_patch in reversed(self.app_patches):
            active_patch.stop()
        self.store._pool.shutdown(wait=True)
        self.store._client.close()
        self.config_patch.stop()
        gc.collect()
        self.directory.cleanup()

    @staticmethod
    def messages():
        return [{"role": "user", "content": "The fictional observatory opens on Thursday."}]

    def body(self):
        return {"request_id": "request-1", "user_id": "invented-user", "session_id": "session-1",
                "messages": self.messages()}

    def counts(self):
        # Every query is restricted to this test's newly created disposable DB.
        self.assertEqual(Path(store_module.CONFIG.db_path).parent, Path(self.directory.name))
        tables = ("items", "vec_items", "facts", "digests", "add_requests",
                  "llm_memory_spans", "add_llm_usage")
        with self.store._connect() as conn:
            return {table: conn.execute("SELECT count(*) FROM " + table).fetchone()[0] for table in tables}

    def assert_empty(self):
        self.assertEqual(self.counts(), dict.fromkeys(self.counts(), 0))

    def test_sixteen_concurrent_retries_make_one_model_call_and_one_embedding(self):
        self.embed_delay = self.extractor.delay = 0.025
        barrier = threading.Barrier(16)

        def add_once(_):
            barrier.wait(timeout=10)
            return self.store.add_messages("user-1", "session-1", self.messages(), request_id="same-request")

        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(add_once, range(16)))
        self.assertEqual(results, [1] * 16)
        self.assertEqual(len(self.embed_calls), 1)
        self.assertEqual(len(self.extractor.calls), 1)
        counts = self.counts()
        for table in ("items", "vec_items", "add_requests", "llm_memory_spans", "add_llm_usage"):
            self.assertEqual(counts[table], 1, table)

    def test_sequential_receipt_replay_uses_no_additional_models(self):
        for _ in range(3):
            self.assertEqual(self.store.add_messages("user-1", "session-1", self.messages(), request_id="request-1"), 1)
        self.assertEqual(len(self.embed_calls), 1)
        self.assertEqual(len(self.extractor.calls), 1)
        with self.store._connect() as conn:
            usage = conn.execute("SELECT model,prompt_tokens,completion_tokens,call_count FROM add_llm_usage").fetchone()
        self.assertEqual(tuple(usage), ("gpt-4o-mini-2024-07-18", 101, 19, 1))

    def test_stripe_collision_returns_busy_before_models_and_releases_after_completion(self):
        # Force two DISTINCT request IDs onto the same admission stripe while
        # the first fake model call is still processing. No hash luck required.
        self.store._add_locks = [threading.Lock()]
        entered = threading.Event()
        release = threading.Event()
        original_extract = self.extractor.extract

        def blocked_extract(messages):
            result = original_extract(messages)
            entered.set()
            if not release.wait(timeout=5):
                raise AssertionError("Test did not release its invented model call")
            return result

        self.extractor.extract = blocked_extract
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(self.store.add_messages, "user-1", "session-1", self.messages(),
                                request_id="different-request-a")
            try:
                self.assertTrue(entered.wait(timeout=3))
                started = time.monotonic()
                with self.assertRaisesRegex(AddMemoryError, "already processing"):
                    self.store.add_messages("user-1", "session-1", self.messages(),
                                            request_id="different-request-b")
                self.assertLess(time.monotonic() - started, 2.5)
                self.assertEqual(len(self.embed_calls), 1)
                self.assertEqual(len(self.extractor.calls), 1)
            finally:
                release.set()
            self.assertEqual(first.result(timeout=3), 1)
        self.extractor.extract = original_extract
        # The timed-out request did not consume/release somebody else's permit;
        # once the first finishes its own release, the second can commit once.
        self.assertEqual(self.store.add_messages("user-1", "session-1", self.messages(),
                                                request_id="different-request-b"), 1)
        self.assertEqual(len(self.embed_calls), 2)
        self.assertEqual(len(self.extractor.calls), 2)
        self.assertEqual(self.counts()["add_requests"], 2)
        self.assertEqual(self.counts()["add_llm_usage"], 2)

    def test_changed_payload_and_session_are_409_without_new_model_calls(self):
        body = self.body()
        self.assertEqual(self.client.post("/add", json=body, headers=AUTH).status_code, 200)
        changed_payload = copy.deepcopy(body)
        changed_payload["messages"][0]["content"] = "A different fictional observatory opens Monday."
        changed_session = {**body, "session_id": "different-session"}
        for invalid in (changed_payload, changed_session):
            result = self.client.post("/add", json=invalid, headers=AUTH)
            self.assertEqual(result.status_code, 409)
            self.assertEqual(result.json(), {"detail": {"reason": "request_id_conflict"}})
        self.assertEqual(len(self.embed_calls), 1)
        self.assertEqual(len(self.extractor.calls), 1)

    def test_identical_request_id_for_different_users_remains_isolated(self):
        for user in ("user-a", "user-b"):
            self.store.add_messages(user, "shared-session", self.messages(), request_id="same-id")
        self.assertEqual(len(self.embed_calls), 2)
        self.assertEqual(len(self.extractor.calls), 2)
        with self.store._connect() as conn:
            owners = conn.execute("SELECT s.user_id,i.user_id FROM llm_memory_spans s JOIN items i ON i.rid=s.source_rid ORDER BY s.user_id").fetchall()
            receipts = conn.execute("SELECT user_id,request_id FROM add_requests ORDER BY user_id").fetchall()
        self.assertEqual([tuple(row) for row in owners], [("user-a", "user-a"), ("user-b", "user-b")])
        self.assertEqual([tuple(row) for row in receipts], [("user-a", "same-id"), ("user-b", "same-id")])
        self.assertEqual(self.store.search("unrelated-user", "observatory", 100), [])

    def test_gpt_failure_leaves_no_raw_facts_vectors_usage_or_receipt(self):
        self.extractor.error = AddMemoryError("Invented upstream failure")
        with self.assertRaises(AddMemoryError):
            self.store.add_messages("user-1", "session-1", self.messages(), request_id="request-1")
        self.assertEqual(len(self.embed_calls), 1)
        self.assertEqual(len(self.extractor.calls), 1)
        self.assert_empty()

    def test_retry_after_gpt_failure_can_commit_once(self):
        self.extractor.error = AddMemoryError("Invented unavailable response")
        with self.assertRaises(AddMemoryError):
            self.store.add_messages("user-1", "session-1", self.messages(), request_id="request-1")
        self.extractor.error = None
        self.assertEqual(self.store.add_messages("user-1", "session-1", self.messages(), request_id="request-1"), 1)
        self.assertEqual(self.counts()["add_requests"], 1)
        self.assertEqual(self.counts()["add_llm_usage"], 1)

    def test_invalid_extraction_reference_rolls_back_every_write(self):
        self.extractor.result = ExtractionResult([MemorySpan(0, 0, 999999, "fact")], 101, 19,
                                                  "gpt-4o-mini-2024-07-18", 1)
        with self.assertRaises(ValueError):
            self.store.add_messages("user-1", "session-1", self.messages(), request_id="request-1")
        self.assert_empty()

    def test_invalid_model_and_missing_real_call_roll_back_every_write(self):
        for model, calls in (("other-model", 1), ("gpt-4o-mini", 0)):
            self.extractor.result = ExtractionResult([], 101, 19, model, calls)
            with self.subTest(model=model, calls=calls), self.assertRaises(ValueError):
                self.store.add_messages("user-1", "session-1", self.messages(), request_id="request-1")
            self.assert_empty()

    def test_embedding_failure_never_spends_a_gpt_call(self):
        self.embed_error = RuntimeError("Invented embedding outage")
        with self.assertRaises(RuntimeError):
            self.store.add_messages("user-1", "session-1", self.messages(), request_id="request-1")
        self.assertEqual(len(self.embed_calls), 1)
        self.assertEqual(self.extractor.calls, [])
        self.assert_empty()

    def test_content_parts_use_exact_flattened_unicode_offsets(self):
        body = self.body()
        body["messages"] = [{"role": "assistant", "content": [
            {"type": "text", "text": "🙂 第一条原文。"},
            {"type": "text", "text": "第二条：周五开放。"}]}]
        expected = "🙂 第一条原文。\n第二条：周五开放。"
        result = self.client.post("/add", json=body, headers=AUTH)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.embed_calls, [[expected]])
        self.assertEqual(self.extractor.calls[0][0]["content"], expected)
        self.assertEqual(self.extractor.calls[0][0]["role"], "assistant")
        with self.store._connect() as conn:
            source = conn.execute("SELECT content,role FROM items").fetchone()
            span = conn.execute("SELECT start_offset,end_offset,source_role FROM llm_memory_spans").fetchone()
        self.assertEqual(tuple(source), (expected, "assistant"))
        self.assertEqual(tuple(span), (0, len(expected), "assistant"))
        self.assertEqual(expected[span[0]:span[1]], source[0])

    def test_api_model_failure_is_sanitized_503_and_audited_without_source(self):
        private = "INVENTED_PRIVATE_UPSTREAM_MARKER"
        self.extractor.error = AddMemoryError(private)
        body = self.body()
        body["messages"][0]["content"] = "INVENTED_PRIVATE_SOURCE_MARKER"
        result = self.client.post("/add", json=body, headers=AUTH)
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json(), {"detail": {"reason": "add_memory_processing_failed"}})
        self.assertEqual(result.headers["retry-after"], "5")
        self.assertNotIn(private, result.text)
        audit = Path(app_module.AUDIT_LOG).read_text(encoding="utf-8")
        self.assertNotIn(private, audit)
        self.assertNotIn(body["messages"][0]["content"], audit)
        self.assertTrue(any(row["status"] == 503 for row in map(json.loads, audit.splitlines())))
        self.assert_empty()

    def test_health_without_required_key_is_not_ready_and_exposes_no_key(self):
        with patch.object(app_module, "CONFIG", replace(self.config, add_llm_api_key="")):
            result = self.client.get("/health")
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json()["status"], "not-ready")
        self.assertTrue(result.json()["add_model_required"])
        self.assertFalse(result.json()["add_model_configured"])
        self.assertNotIn("offline-not-a-real-key", result.text)
        self.assertEqual(len(self.extractor.calls), 0)

    def test_health_with_configured_key_is_ready_without_paid_probe(self):
        result = self.client.get("/health")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["status"], "ok")
        self.assertEqual(result.json()["add_model"], "gpt-4o-mini")
        self.assertTrue(result.json()["add_model_configured"])
        self.assertNotIn("offline-not-a-real-key", result.text)
        self.assertEqual(len(self.extractor.calls), 0)


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        _import_store._pool.shutdown(wait=True)
        _import_store._client.close()
        gc.collect()
        TEST_ROOT.cleanup()
