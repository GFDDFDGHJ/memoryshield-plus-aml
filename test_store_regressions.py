"""Offline retrieval regressions; no API calls or deployment database access.

Run: python test_store_regressions.py
"""
from __future__ import annotations

import tempfile
import threading
import unittest
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

import store as store_module


DIM = 4


class OfflineMemoryStore(store_module.MemoryStore):
    """Use the real SQLite schema with controllable, local embeddings."""

    def __init__(self) -> None:
        self._dim = DIM
        self.vectors: dict[str, list[float]] = {}
        self.connections = []
        self._ensure_schema()

    def _connect(self):
        conn = super()._connect()
        self.connections.append(conn)
        return conn

    def embed(self, texts):
        return [self.vectors.get(text, [1.0, 0.0, 0.0, 0.0]) for text in texts]

    def close(self) -> None:
        for conn in self.connections:
            conn.close()


class _OfflineStoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="aml-store-regression-")
        self.config_patch = patch.object(
            store_module,
            "CONFIG",
            replace(
                store_module.CONFIG,
                db_path=self.temp_dir.name + "/memory.db",
                embedding_dim=DIM,
                recency_weight=0.1,
            ),
        )
        self.config_patch.start()
        self.memory = OfflineMemoryStore()

    def tearDown(self) -> None:
        self.memory.close()
        self.config_patch.stop()
        self.temp_dir.cleanup()

    def add(self, user_id: str, texts: list[str]) -> None:
        self.memory.add_messages(
            user_id,
            "session",
            [{"role": "user", "content": text} for text in texts],
        )


class StoreRetrievalRegressions(_OfflineStoreTestCase):
    def test_connection_context_closes_after_commit_and_rollback(self) -> None:
        committed = self.memory._connect()
        with committed:
            committed.execute("CREATE TABLE connection_probe(value TEXT)")
            committed.execute("INSERT INTO connection_probe VALUES('committed')")
        with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed"):
            committed.execute("SELECT 1")

        rolled_back = self.memory._connect()
        with self.assertRaisesRegex(RuntimeError, "rollback probe"):
            with rolled_back:
                rolled_back.execute("INSERT INTO connection_probe VALUES('rolled back')")
                raise RuntimeError("rollback probe")
        with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed"):
            rolled_back.execute("SELECT 1")
        with self.memory._connect() as conn:
            self.assertEqual(
                [r["value"] for r in conn.execute("SELECT value FROM connection_probe")],
                ["committed"],
            )

    def test_small_top_k_keeps_raw_evidence_after_structured_results(self) -> None:
        self.add("target", [
            "My name is Ada. I am 30 years old.",
            "I live in Singapore.",
            "I work as an engineer.",
            "I prefer tea over coffee every morning.",
            "Every morning I walk 20 minutes before work.",
            "My next appointment is on Friday at 10.",
        ])
        query = "summarize my background and name"
        with self.memory._connect() as conn:
            self.assertTrue(store_module.fact_layer.build_profile(conn, "target", query))
            self.assertTrue(store_module.fact_layer.get_digests(conn, "target", query))

        for top_k in (1, 2, 5):
            with self.subTest(top_k=top_k):
                results = self.memory.search("target", query, top_k)
                self.assertEqual(len(results), top_k)
                raw_count = sum(r["id"].startswith("mem_") for r in results)
                self.assertGreaterEqual(raw_count, min(8, (top_k + 1) // 2))

    def test_same_long_prefix_keeps_different_tails_and_collapses_duplicates(self) -> None:
        prefix = "shared conversation introduction " * 20
        first = prefix + "Destination chosen is Kyoto."
        second = prefix + "Destination chosen is Bergen."
        self.add("target", [first, second, second])

        with self.memory._connect() as conn:
            results = self.memory._search_raw(conn, "target", [1.0, 0.0, 0.0, 0.0], 10)

        self.assertEqual(len(results), 2)
        self.assertEqual({r["content"] for r in results}, {first, second})

    def test_no_numpy_ranking_is_unchanged_by_other_users(self) -> None:
        relevant = "Target record alpha with the relevant information."
        unrelated = "Target record beta with unrelated information."
        self.memory.vectors[relevant] = [0.8, 0.6, 0.0, 0.0]
        self.memory.vectors[unrelated] = [-1.0, 0.0, 0.0, 0.0]
        self.add("target", [relevant, unrelated])

        with patch.object(store_module, "np", None):
            before = self.memory.search("target", "lookup alpha", 2)
            self.add("other", [f"Distractor record {i}." for i in range(600)])
            after = self.memory.search("target", "lookup alpha", 2)

        self.assertEqual(before, after)
        self.assertEqual(after[0]["content"], relevant)
        self.assertEqual({r["content"] for r in after}, {relevant, unrelated})

    def test_no_numpy_uses_cosine_with_nonunit_and_zero_vectors(self) -> None:
        aligned = "Aligned target record."
        diagonal = "Diagonal target record."
        zero = "Zero target record."
        self.memory.vectors.update({
            aligned: [100.0, 0.0, 0.0, 0.0],
            diagonal: [2.0, 2.0, 0.0, 0.0],
            zero: [0.0, 0.0, 0.0, 0.0],
            "lookup alignment": [3.0, 0.0, 0.0, 0.0],
        })
        self.add("target", [aligned, diagonal, zero])
        with patch.object(store_module, "np", None):
            results = self.memory.search("target", "lookup alignment", 3)

        self.assertEqual([r["content"] for r in results], [aligned, diagonal, zero])
        self.assertAlmostEqual(results[0]["score"], 0.9, places=5)


    def test_structured_ids_follow_evidence_when_query_changes_rank(self) -> None:
        # Keep actual _layered output construction while changing the structured
        # evidence order, as query-dependent fact/digest ranking does in service.
        facts = [{"text": "first fact"}, {"text": "second fact"}]
        digests = ["[DIGEST] first session", "[DIGEST] second session"]

        def layer(user_id, reverse):
            with (
                patch.object(store_module.fact_layer, "build_profile", return_value=""),
                patch.object(store_module.fact_layer, "change_history", return_value=[{"text": "changed value"}]),
                patch.object(store_module.fact_layer, "relevant_rules", return_value=[]),
                patch.object(store_module.fact_layer, "get_digests", return_value=digests[::-1] if reverse else digests),
                patch.object(store_module.fact_layer, "match_facts", return_value=facts[::-1] if reverse else facts),
                self.memory._connect() as conn,
            ):
                return self.memory._layered(conn, user_id, "summarize second" if reverse else "summarize first", [1.0, 0.0, 0.0, 0.0], 20)

        first = {r["content"]: r["id"] for r in layer("target", False)}
        second = {r["content"]: r["id"] for r in layer("target", True)}
        other_user = {r["content"]: r["id"] for r in layer("other", False)}
        self.assertEqual(len(first), 5)
        self.assertEqual(first, second)
        self.assertEqual(len(set(first.values())), 5)
        self.assertTrue(all(first[content] != other_user[content] for content in first))


class AddIdempotencyRegressions(_OfflineStoreTestCase):
    def test_retries_are_persistent_and_skip_embedding(self) -> None:
        messages = [{"role": "user", "content": "My name is Ada."}]
        self.assertEqual(self.memory.add_messages("target", "session", messages, request_id="request"), 1)
        reopened = OfflineMemoryStore()
        try:
            with patch.object(reopened, "embed", side_effect=AssertionError("retry must not embed")):
                # Changing JSON object key order preserves the canonical payload.
                for _ in range(32):
                    self.assertEqual(reopened.add_messages(
                        "target", "session", [{"content": "My name is Ada.", "role": "user"}],
                        request_id="request",
                    ), 1)
            with reopened._connect() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM items").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM vec_items").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM add_requests").fetchone()[0], 1)
        finally:
            reopened.close()

    def test_conflicting_payload_or_session_is_rejected_before_embedding(self) -> None:
        messages = [{"role": "user", "content": "Original record."}]
        self.memory.add_messages("target", "session", messages, request_id="request")
        with patch.object(self.memory, "embed", side_effect=AssertionError("conflict must not embed")):
            for session_id, body in [
                ("different-session", messages),
                ("session", [{"role": "user", "content": "Changed record."}]),
            ]:
                with self.subTest(session_id=session_id, body=body):
                    with self.assertRaises(store_module.AddRequestConflict):
                        self.memory.add_messages("target", session_id, body, request_id="request")
        with self.memory._connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM items").fetchone()[0], 1)

    def test_same_request_id_is_independent_between_users(self) -> None:
        for user_id in ("first", "second"):
            self.assertEqual(self.memory.add_messages(
                user_id, "session", [{"role": "user", "content": user_id + " record."}],
                request_id="shared-request",
            ), 1)
        with self.memory._connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM items").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM add_requests").fetchone()[0], 2)

    def concurrent_adds(self, request_ids):
        barrier = threading.Barrier(len(request_ids))

        def add_from_worker(request_id):
            memory = OfflineMemoryStore()
            original_embed = memory.embed

            def embed_together(texts):
                barrier.wait(timeout=10)
                return original_embed(texts)

            memory.embed = embed_together
            try:
                return memory.add_messages(
                    "target", "session", [{"role": "user", "content": "Concurrent record."}],
                    request_id=request_id,
                )
            finally:
                memory.close()

        with ThreadPoolExecutor(max_workers=len(request_ids)) as pool:
            return list(pool.map(add_from_worker, request_ids))

    def test_concurrent_replay_writes_once(self) -> None:
        self.assertEqual(self.concurrent_adds(["request", "request"]), [1, 1])
        with self.memory._connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM items").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM vec_items").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM add_requests").fetchone()[0], 1)

    def test_concurrent_distinct_requests_allocate_unique_sequences(self) -> None:
        self.assertEqual(self.concurrent_adds(["first", "second"]), [1, 1])
        with self.memory._connect() as conn:
            seqs = conn.execute("SELECT seq FROM items ORDER BY seq").fetchall()
            self.assertEqual([r["seq"] for r in seqs], [1, 2])

    def test_failed_write_does_not_register_request_or_partial_messages(self) -> None:
        messages = [{"role": "user", "content": "A record to retry."}]
        with patch.object(store_module.fact_layer, "update_digest", side_effect=RuntimeError("simulated failure")):
            with self.assertRaisesRegex(RuntimeError, "simulated failure"):
                self.memory.add_messages("target", "session", messages, request_id="request")
        with self.memory._connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM items").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM vec_items").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM add_requests").fetchone()[0], 0)
        self.assertEqual(self.memory.add_messages("target", "session", messages, request_id="request"), 1)

    def test_missing_request_id_keeps_direct_add_compatibility(self) -> None:
        for request_id in (None, ""):
            for _ in range(2):
                self.memory.add_messages(
                    "target", "session", [{"role": "user", "content": "Direct record."}],
                    request_id=request_id,
                )
        with self.memory._connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM items").fetchone()[0], 4)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM add_requests").fetchone()[0], 0)


class StandardEmbeddingRegressions(unittest.TestCase):
    def setUp(self) -> None:
        self.config_patch = patch.object(
            store_module,
            "CONFIG",
            replace(store_module.CONFIG, embedding_mode="standard", embedding_dim=DIM, embedding_batch_size=10),
        )
        self.config_patch.start()
        self.memory = store_module.MemoryStore.__new__(store_module.MemoryStore)
        self.memory._dim = DIM
        self.create = Mock()
        self.memory._client = SimpleNamespace(embeddings=SimpleNamespace(create=self.create))

    def tearDown(self) -> None:
        self.config_patch.stop()

    def test_batches_ten_and_preserves_response_index_order(self) -> None:
        def respond(**kwargs):
            return SimpleNamespace(data=[
                SimpleNamespace(index=i, embedding=[float(text), 0.0, 0.0, 0.0])
                for i, text in reversed(list(enumerate(kwargs["input"])))
            ])

        self.create.side_effect = respond
        for count in (20, 21):
            with self.subTest(count=count):
                self.create.reset_mock()
                vectors = self.memory.embed([str(i) for i in range(count)])
                self.assertEqual([v[0] for v in vectors], list(range(count)))
                self.assertEqual(
                    [len(call.kwargs["input"]) for call in self.create.call_args_list],
                    [10, 10] + ([1] if count == 21 else []),
                )
                self.assertTrue(all(call.kwargs["dimensions"] == DIM for call in self.create.call_args_list))

    def test_incomplete_duplicate_or_out_of_range_indices_fail(self) -> None:
        for indices in ([0], [0, 0], [0, 2]):
            with self.subTest(indices=indices):
                self.create.return_value = SimpleNamespace(data=[
                    SimpleNamespace(index=i, embedding=[1.0, 0.0, 0.0, 0.0])
                    for i in indices
                ])
                with self.assertRaisesRegex(ValueError, "count or indices"):
                    self.memory.embed(["first", "second"])

    def test_configured_batch_size_is_limited_to_one_through_ten(self) -> None:
        self.create.side_effect = lambda **kwargs: SimpleNamespace(data=[
            SimpleNamespace(index=i, embedding=[1.0, 0.0, 0.0, 0.0])
            for i in range(len(kwargs["input"]))
        ])
        for configured, expected in ((0, [1] * 11), (3, [3, 3, 3, 2]), (99, [10, 1])):
            with self.subTest(configured=configured):
                self.create.reset_mock()
                with patch.object(store_module, "CONFIG", replace(store_module.CONFIG, embedding_batch_size=configured)):
                    self.memory.embed(["text"] * 11)
                self.assertEqual(
                    [len(call.kwargs["input"]) for call in self.create.call_args_list],
                    expected,
                )

    def test_wrong_dimensions_fail(self) -> None:
        self.create.return_value = SimpleNamespace(data=[
            SimpleNamespace(index=0, embedding=[1.0, 0.0]),
        ])
        with self.assertRaisesRegex(ValueError, "dimensions"):
            self.memory.embed(["first"])

    def test_empty_input_does_not_call_client(self) -> None:
        self.assertEqual(self.memory.embed([]), [])
        self.create.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
