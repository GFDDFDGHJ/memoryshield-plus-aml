"""Invented query-length recovery controls: no sockets, keys or SQLite access."""
from __future__ import annotations

import builtins
from dataclasses import replace
import math
import os
import socket
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

import httpx
from openai import APIConnectionError, BadRequestError, RateLimitError


_original_open = builtins.open


def _without_secrets(file, *args, **kwargs):
    if ".secrets" in str(file).replace("\\", "/").split("/"):
        raise FileNotFoundError("Secret files are forbidden in offline tests")
    return _original_open(file, *args, **kwargs)


_ENV = {
    "EMBEDDING_API_KEY": "offline", "DASHSCOPE_API_KEY": "offline",
    "OPENAI_API_KEY": "offline", "AML_AUTH_TOKEN": "offline",
    "AML_RERANK_API_KEY": "offline", "AML_RERANK": "false",
    "EMBEDDING_MODE": "standard", "EMBEDDING_MODEL": "text-embedding-v4",
    "EMBEDDING_DIM": "3", "EMBEDDING_BATCH_SIZE": "10",
}
with patch.dict(os.environ, _ENV), patch("builtins.open", _without_secrets), \
        patch.object(socket.socket, "connect", side_effect=AssertionError("No network")), \
        patch.object(sqlite3, "connect", side_effect=AssertionError("No databases")):
    import store as S


def bad_request(body=None, *, status=400, message="Invented provider rejection"):
    request = httpx.Request("POST", "https://offline.invalid/embeddings")
    return BadRequestError(message, response=httpx.Response(status, request=request), body=body)


def length_error():
    # Exact observed template: it contains spaces, not the word input_length.
    return bad_request({"code": "InvalidParameter",
                        "message": "Range of input length should be [1, 33000]"})


class FakeProvider:
    def __init__(self, *, first_error=None, vector=None, mutate=None, clock=None, duration=0):
        self.first_error = first_error
        self.vector = vector if vector is not None else lambda text: [1.0, 2.0, 0.0]
        self.mutate = mutate
        self.clock = clock
        self.duration = duration
        self.calls = []
        self.embeddings = SimpleNamespace(create=self.create)

    def create(self, **kwargs):
        self.calls.append({**kwargs, "input": list(kwargs["input"])})
        number = len(self.calls)
        if number == 1 and self.first_error is not None:
            raise self.first_error
        data = [SimpleNamespace(index=index, embedding=self.vector(text))
                for index, text in enumerate(kwargs["input"])]
        if self.mutate is not None:
            data = self.mutate(number, data)
        if self.clock is not None:
            self.clock[0] += self.duration
        # Provider order differs from the request order, as its index contract permits.
        return SimpleNamespace(data=list(reversed(data)))


class QueryEmbeddingLengthTests(unittest.TestCase):
    def setUp(self):
        self.config = replace(S.CONFIG, embedding_mode="standard", embedding_model="text-embedding-v4",
                              embedding_dim=3, embedding_batch_size=10, embedding_timeout=45)
        for active in (
            patch.object(S, "CONFIG", self.config),
            patch("builtins.open", _without_secrets),
            patch.object(socket.socket, "connect", side_effect=AssertionError("No network")),
            patch.object(sqlite3, "connect", side_effect=AssertionError("No databases")),
        ):
            active.start()
            self.addCleanup(active.stop)

    def memory(self, provider):
        memory = S.MemoryStore.__new__(S.MemoryStore)
        memory._client = provider
        memory._dim = 3
        memory._connect = Mock(side_effect=AssertionError("No real database connection"))
        return memory

    def assert_unit(self, vector):
        self.assertTrue(all(math.isfinite(value) for value in vector))
        self.assertAlmostEqual(math.hypot(*vector), 1.0)

    def test_short_success_keeps_original_vector_and_request_parameters(self):
        vector = [2.0, 3.0, 4.0]
        provider = FakeProvider(vector=lambda text: vector)
        memory = self.memory(provider)
        self.assertIs(memory._embed_query("Invented short question?"), vector)
        self.assertEqual(provider.calls, [{"model": "text-embedding-v4",
                                          "input": ["Invented short question?"], "dimensions": 3}])

    def test_long_success_does_not_preemptively_chunk_or_normalize(self):
        query = "Invented accepted text. " * S.QUERY_EMBED_CHUNK_BYTES
        vector = [2.0, 0.0, 0.0]
        provider = FakeProvider(vector=lambda text: vector)
        self.assertIs(self.memory(provider)._embed_query(query), vector)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(provider.calls[0]["input"], [query])
        self.assertNotIn("timeout", provider.calls[0])

    def test_observed_spaced_range_template_and_nested_error_are_recognized(self):
        cases = [length_error(), bad_request({"error": {"code": "InvalidParameter",
                 "message": "Range of input length should be [1, 33000]"}}),
                 bad_request({"code": "InvalidParameter", "param": "input_length"})]
        for error in cases:
            with self.subTest(case=cases.index(error)):
                provider = FakeProvider(first_error=error)
                self.assert_unit(self.memory(provider)._embed_query("a" * (S.QUERY_EMBED_CHUNK_BYTES + 1)))
                self.assertEqual(len(provider.calls), 2)

    def test_unrelated_bad_requests_and_unknown_body_shapes_are_not_retried(self):
        cases = [
            bad_request(None), bad_request("InvalidParameter input_length"), bad_request([]),
            bad_request({"code": "OtherCode", "message": "input_length"}),
            bad_request({"code": "InvalidParameter", "message": "Invalid dimensions"}),
            bad_request({"code": "InvalidParameter", "message": None}),
            bad_request({"code": "InvalidParameter", "message": "inputlength is invalid"}),
            bad_request({"code": "InvalidParameter", "message": "invalid input_length"}),
            bad_request({"code": "InvalidParameter", "details": "input_length"}),
            bad_request({"code": "InvalidParameter", "param": "dimensions"}),
            bad_request({"code": "InvalidParameter", "message": "input_length"}, status=422),
            bad_request({"code": "InvalidParameter"}, message="input_length only in exception text"),
        ]
        for index, error in enumerate(cases):
            with self.subTest(index=index):
                provider = FakeProvider(first_error=error)
                with self.assertRaises(BadRequestError) as caught:
                    self.memory(provider)._embed_query("a" * (S.QUERY_EMBED_CHUNK_BYTES + 1))
                self.assertIs(caught.exception, error)
                self.assertEqual(len(provider.calls), 1)

    def test_non_bad_request_errors_propagate_as_the_same_object(self):
        request = httpx.Request("POST", "https://offline.invalid/embeddings")
        errors = [RuntimeError("Invented input_length error"), TimeoutError("Invented timeout"),
                  APIConnectionError(request=request),
                  RateLimitError("Invented quota", response=httpx.Response(429, request=request), body={})]
        for error in errors:
            with self.subTest(error_type=type(error).__name__):
                provider = FakeProvider(first_error=error)
                with self.assertRaises(type(error)) as caught:
                    self.memory(provider)._embed_query("a" * (S.QUERY_EMBED_CHUNK_BYTES + 1))
                self.assertIs(caught.exception, error)
                self.assertEqual(len(provider.calls), 1)

    def test_other_embedding_mode_or_model_cannot_enable_fallback(self):
        for changes in ({"embedding_mode": "multimodal"}, {"embedding_model": "invented-model"}):
            with self.subTest(changes=changes), patch.object(S, "CONFIG", replace(self.config, **changes)):
                memory = self.memory(FakeProvider())
                error = length_error()
                memory.embed = Mock(side_effect=error)
                with self.assertRaises(BadRequestError) as caught:
                    memory._embed_query("a" * (S.QUERY_EMBED_CHUNK_BYTES + 1))
                self.assertIs(caught.exception, error)
                memory.embed.assert_called_once()

    def test_length_rejection_of_a_single_chunk_is_not_retried(self):
        error = length_error()
        provider = FakeProvider(first_error=error)
        with self.assertRaises(BadRequestError) as caught:
            self.memory(provider)._embed_query("Invented short query")
        self.assertIs(caught.exception, error)
        self.assertEqual(len(provider.calls), 1)

    def test_unicode_chunks_preserve_every_character_whitespace_and_tail(self):
        query = ("A中🙂e\u0301\r\n \t\u00a0" * 1200) + " UNIQUE-TAIL-終🙂 "
        chunks = S._query_embedding_chunks(query)
        self.assertGreater(len(chunks), 1)
        self.assertEqual("".join(chunks), query)
        self.assertEqual(b"".join(chunk.encode("utf-8") for chunk in chunks), query.encode("utf-8"))
        self.assertTrue(all(chunk and len(chunk.encode("utf-8")) <= S.QUERY_EMBED_CHUNK_BYTES
                            for chunk in chunks))
        self.assertTrue(chunks[-1].endswith(" UNIQUE-TAIL-終🙂 "))
        provider = FakeProvider(first_error=length_error())
        self.memory(provider)._embed_query(query)
        self.assertEqual([text for call in provider.calls[1:] for text in call["input"]], chunks)

    def test_utf8_exact_cap_and_crossing_four_byte_character(self):
        cap = S.QUERY_EMBED_CHUNK_BYTES
        for query in ("a" * cap, "a" * (cap + 1), "a" * (cap - 1) + "🙂tail",
                      "中" * (cap // 3) + "🙂", "e\u0301" * cap):
            with self.subTest(byte_count=len(query.encode("utf-8"))):
                chunks = S._query_embedding_chunks(query)
                self.assertEqual("".join(chunks), query)
                self.assertTrue(all(0 < len(chunk.encode("utf-8")) <= cap for chunk in chunks))

    def test_byte_weights_differ_from_character_weights_and_normalize(self):
        # Two equal-length strings in characters, but a 1:3 UTF-8 byte ratio.
        merged = S._merge_query_embeddings([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
                                            [len("ab".encode()), len("中文".encode())], 3)
        self.assertAlmostEqual(merged[0], 1 / math.sqrt(10))
        self.assertAlmostEqual(merged[1], 3 / math.sqrt(10))
        self.assert_unit(merged)

    def test_more_than_ten_chunks_restore_provider_indices_and_keep_tail_weight(self):
        cap = S.QUERY_EMBED_CHUNK_BYTES
        full = [chr(65 + index) * cap for index in range(11)]
        query = "".join(full) + "TAIL"
        vector = lambda text: [float(ord(text[0]) - 64), 1.0, 0.0]
        provider = FakeProvider(first_error=length_error(), vector=vector)
        result = self.memory(provider)._embed_query(query)
        self.assertEqual([len(call["input"]) for call in provider.calls], [1, 10, 2])
        self.assertEqual([text for call in provider.calls[1:] for text in call["input"]], full + ["TAIL"])
        x = sum(range(1, 12)) * cap + 20 * 4
        y = 11 * cap + 4
        self.assertAlmostEqual(result[0], x / math.hypot(x, y))
        self.assertAlmostEqual(result[1], y / math.hypot(x, y))
        self.assert_unit(result)

    def test_malformed_provider_counts_indices_and_dimensions_fail(self):
        def missing(number, data):
            return data[:-1]
        def duplicate(number, data):
            data[-1].index = 0
            return data
        def out_of_range(number, data):
            data[-1].index = len(data)
            return data
        def wrong_dim(number, data):
            data[-1].embedding = [1.0, 0.0]
            return data
        for change in (missing, duplicate, out_of_range, wrong_dim):
            with self.subTest(change=change.__name__):
                provider = FakeProvider(first_error=length_error(), mutate=change)
                with self.assertRaises(ValueError):
                    self.memory(provider)._embed_query("a" * (S.QUERY_EMBED_CHUNK_BYTES + 1))
                self.assertEqual(len(provider.calls), 2)

    def test_invalid_chunk_vectors_and_zero_direction_never_return_hits(self):
        for vector in ([math.nan, 0, 0], [math.inf, 0, 0], [-math.inf, 0, 0],
                       [True, 0, 0], ["1", 0, 0], [0.0, 0.0, 0.0]):
            with self.subTest(vector_type=type(vector[0]).__name__):
                provider = FakeProvider(first_error=length_error(), vector=lambda text: vector)
                with self.assertRaises(ValueError):
                    self.memory(provider)._embed_query("a" * (S.QUERY_EMBED_CHUNK_BYTES + 1))
                self.assertEqual(len(provider.calls), 2)

    def test_opposite_vectors_and_bad_aggregate_counts_fail(self):
        cases = [([[1, 0, 0], [-1, 0, 0]], [1, 1], 3),
                 ([], [], 3), ([[1, 0, 0]], [], 3),
                 ([[1, 0, 0]], [0], 3), ([[1, 0]], [1], 3)]
        for vectors, weights, dimension in cases:
            with self.subTest(case=len(vectors)):
                with self.assertRaises(ValueError):
                    S._merge_query_embeddings(vectors, weights, dimension)

    def test_tiny_and_large_finite_values_normalize_without_overflow_or_underflow(self):
        for magnitude in (1e-300, 1e308):
            result = S._merge_query_embeddings([[magnitude, magnitude, 0.0]] * 2, [1, 1], 3)
            self.assert_unit(result)
            self.assertAlmostEqual(result[0], 1 / math.sqrt(2))
            self.assertAlmostEqual(result[1], 1 / math.sqrt(2))

    def test_chunk_budget_exact_boundary_is_complete_and_excess_never_dispatches(self):
        cap = S.QUERY_EMBED_CHUNK_BYTES
        maximum = S.QUERY_EMBED_MAX_CHUNKS
        query = "a" * (cap * maximum)
        chunks = S._query_embedding_chunks(query)
        self.assertEqual(len(chunks), maximum)
        self.assertEqual("".join(chunks), query)
        provider = FakeProvider(first_error=length_error())
        with self.assertRaises(ValueError):
            self.memory(provider)._embed_query(query + "TAIL")
        self.assertEqual(len(provider.calls), 1)

    def test_elapsed_dispatch_budget_stops_before_another_batch(self):
        clock = [0.0]
        provider = FakeProvider(first_error=length_error(), clock=clock,
                                duration=S.QUERY_EMBED_FALLBACK_SECONDS + 1)
        query = "a" * (S.QUERY_EMBED_CHUNK_BYTES * 11)
        with patch.object(S.time, "monotonic", side_effect=lambda: clock[0]):
            with self.assertRaises(TimeoutError):
                self.memory(provider)._embed_query(query)
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(len(provider.calls[1]["input"]), 10)

    def test_exhausted_budget_before_first_chunk_does_not_dispatch(self):
        provider = FakeProvider(first_error=length_error())
        with patch.object(S.time, "monotonic", side_effect=[0.0, S.QUERY_EMBED_FALLBACK_SECONDS]):
            with self.assertRaises(TimeoutError):
                self.memory(provider)._embed_query("a" * (S.QUERY_EMBED_CHUNK_BYTES + 1))
        self.assertEqual(len(provider.calls), 1)

    def test_each_fallback_batch_receives_only_remaining_timeout(self):
        provider = FakeProvider(first_error=length_error())
        with patch.object(S.time, "monotonic", side_effect=[0.0, 20.0, 59.0]):
            self.memory(provider)._embed_query("a" * (S.QUERY_EMBED_CHUNK_BYTES * 11))
        self.assertNotIn("timeout", provider.calls[0])
        self.assertEqual([call["timeout"] for call in provider.calls[1:]], [40.0, 1.0])

    def test_rejected_chunk_does_not_recursively_recover_or_blindly_retry(self):
        for chunk_error in (length_error(), RuntimeError("Invented failure")):
            provider = FakeProvider(first_error=length_error())
            original_create = provider.embeddings.create
            def fail_chunk(**kwargs):
                if provider.calls:
                    provider.calls.append({**kwargs, "input": list(kwargs["input"])})
                    raise chunk_error
                return original_create(**kwargs)
            provider.embeddings.create = fail_chunk
            with self.subTest(chunk_error_type=type(chunk_error).__name__):
                with self.assertRaises(type(chunk_error)) as caught:
                    self.memory(provider)._embed_query("a" * (S.QUERY_EMBED_CHUNK_BYTES * 11))
                self.assertIs(caught.exception, chunk_error)
                self.assertEqual(len(provider.calls), 2)

    def test_search_keeps_original_query_for_all_downstream_planning(self):
        query = ("  Invented 中🙂e\u0301\n" * 700) + " FINAL-TAIL "
        memory = self.memory(FakeProvider(first_error=length_error()))
        connection = object()
        context = MagicMock()
        context.__enter__.return_value = connection
        memory._connect = Mock(return_value=context)
        expected_results = [{"id": "invented-result", "content": "Invented evidence"}]
        memory._layered = Mock(return_value=expected_results)
        with patch.object(S.fact_layer, "ensure_schema") as schema:
            self.assertIs(memory.search("invented-user", query, 7), expected_results)
        schema.assert_called_once_with(connection)
        args = memory._layered.call_args.args
        self.assertIs(args[0], connection)
        self.assertEqual(args[1], "invented-user")
        self.assertIs(args[2], query)
        self.assert_unit(args[3])
        self.assertEqual(args[4], 7)

    def test_add_still_sends_original_text_and_does_not_use_search_recovery(self):
        error = length_error()
        provider = FakeProvider(first_error=error)
        memory = self.memory(provider)
        memory._add_extractor = Mock()
        query = "Invented Add text " * S.QUERY_EMBED_CHUNK_BYTES
        messages = [{"role": "user", "content": query}]
        with patch.object(memory, "_embed_query", side_effect=AssertionError("Search helper used by Add")):
            with self.assertRaises(BadRequestError) as caught:
                memory._add_messages("invented-user", "invented-session", messages, request_id=None)
        self.assertIs(caught.exception, error)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(provider.calls[0]["input"], [query])
        self.assertNotIn("timeout", provider.calls[0])
        memory._add_extractor.extract.assert_not_called()
        memory._connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
