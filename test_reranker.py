"""Offline client/schema/budget/concurrency checks; never call a provider."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
import copy
import io
import json
import math
import threading
import unittest
from unittest.mock import Mock, patch

import reranker as R


def response(results):
    return 200, {"output": {"results": results}}


def scores(*pairs):
    return [{"index": index, "relevance_score": score} for index, score in pairs]


class RerankerTests(unittest.TestCase):
    def client(self, transport, **options):
        return R.RerankerClient("offline-test-key", transport=transport, **options)

    def test_native_schema_and_complete_descending_permutation(self):
        transport = Mock(return_value=response(scores((0, .05), (2, .95), (1, .5))))
        client = self.client(transport)
        self.assertEqual(client.rerank("Which constraint applies?", ["first", "second", "third"]), [2, 1, 0])
        url, payload, headers, timeout = transport.call_args.args
        self.assertEqual(url, R.DEFAULT_BASE_URL + "/services/rerank/text-rerank/text-rerank")
        self.assertEqual(payload, {"model": "qwen3-rerank", "input": {"query": "Which constraint applies?", "documents": ["first", "second", "third"]},
                                   "parameters": {"top_n": 3, "return_documents": False}})
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["Authorization"], "Bearer offline-test-key")
        self.assertEqual(timeout, 6.0)

    def test_zero_and_tied_scores_keep_all_documents(self):
        transport = Mock(return_value=response(scores((2, .5), (0, .5), (1, 0))))
        self.assertEqual(self.client(transport).rerank("q", ["a", "b", "c"]), [2, 0, 1])

    def test_base_host_api_root_and_full_endpoint(self):
        expected = "https://example.test/api/v1/services/rerank/text-rerank/text-rerank"
        for base in ("https://example.test", "https://example.test/api/v1/", expected):
            with self.subTest(base=base):
                transport = Mock(return_value=response(scores((0, .5))))
                self.assertEqual(self.client(transport, base_url=base).rerank("q", ["a"]), [0])
                self.assertEqual(transport.call_args.args[0], expected)

    def test_invalid_or_credential_bearing_base_never_calls_transport(self):
        for base in ("http://example.test", "file:///tmp/key", "https://u:p@example.test/api/v1", "https://example.test/api/v1?key=secret", "https://example.test/compatible-mode/v1"):
            with self.subTest(base=base):
                transport = Mock()
                self.assertIsNone(self.client(transport, base_url=base).rerank("q", ["a"]))
                transport.assert_not_called()

    def test_constructor_uses_only_explicit_values(self):
        transport = Mock(return_value=response(scores((0, 1))))
        with patch("builtins.open", side_effect=AssertionError("No files")), patch("os.getenv", side_effect=AssertionError("No environment")):
            client = R.RerankerClient("explicit", "https://example.test", "custom-rerank", 1.5, transport=transport)
            self.assertEqual(client.rerank("q", ["a"]), [0])
        self.assertEqual(transport.call_args.args[1]["model"], "custom-rerank")
        self.assertEqual(transport.call_args.args[3], 1.5)

    def test_empty_key_model_and_invalid_timeout_disable_calls(self):
        options = ({"api_key": ""}, {"model": ""}, {"timeout": 0}, {"timeout": -1},
                   {"timeout": math.nan}, {"timeout": math.inf}, {"timeout": "bad"})
        for option in options:
            with self.subTest(option=option):
                transport = Mock()
                kwargs = {"api_key": "offline", "transport": transport, **option}
                self.assertIsNone(R.RerankerClient(**kwargs).rerank("q", ["a"]))
                transport.assert_not_called()

    def test_timeout_is_capped_and_timeout_failure_falls_back(self):
        transport = Mock(side_effect=TimeoutError("provider detail must not be emitted"))
        self.assertIsNone(self.client(transport, timeout=30).rerank("q", ["a"]))
        self.assertEqual(transport.call_args.args[3], 6.0)

    def test_http_and_malformed_response_fail_safely(self):
        bad = [(403, {"message": "confidential provider body"}), (429, {}), (500, {}),
               (200, None), (200, {}), (200, {"output": []}), (200, {"output": {"results": "bad"}})]
        for item in bad:
            with self.subTest(item=item):
                self.assertIsNone(self.client(Mock(return_value=item)).rerank("q", ["a"]))
        self.assertIsNone(self.client(Mock(side_effect=OSError("private text"))).rerank("q", ["a"]))

    def test_indexes_must_be_unique_complete_and_original(self):
        bad = [scores((0, .5)), scores((0, .7), (0, .5)), scores((0, .7), (2, .5)),
               scores((-1, .7), (1, .5)), scores((False, .7), (1, .5)),
               scores((0.0, .7), (1, .5)), scores(("0", .7), (1, .5)), [None, {}]]
        for rows in bad:
            with self.subTest(rows=rows):
                self.assertIsNone(self.client(Mock(return_value=response(rows))).rerank("q", ["a", "b"]))

    def test_scores_must_be_finite_numeric_values_in_range(self):
        for score in (math.nan, math.inf, -math.inf, -.001, 1.001, True, None, "0.5"):
            with self.subTest(score=score):
                self.assertIsNone(self.client(Mock(return_value=response(scores((0, score))))).rerank("q", ["a"]))
        for score in (0, 1, .2):
            self.assertEqual(self.client(Mock(return_value=response(scores((0, score))))).rerank("q", ["a"]), [0])

    def test_invalid_input_never_calls_transport(self):
        for query, documents in ((None, ["a"]), (" ", ["a"]), ("q", []), ("q", "a"),
                                 ("q", [None]), ("q", [" "]), ("q", ["a"] * 65),
                                 ("\ud800", ["a"]), ("q", ["\ud800"])):
            with self.subTest(query=repr(query), count=len(documents) if documents is not None else None):
                transport = Mock()
                self.assertIsNone(self.client(transport).rerank(query, documents))
                transport.assert_not_called()

    def test_utf8_truncation_affects_ranking_copies_only(self):
        query, documents = "界" * 1001 + "尾", ["汉" * 2001 + "末", "😀" * 1501 + "end"]
        originals = copy.deepcopy(documents)
        transport = Mock(return_value=response(scores((0, .7), (1, .3))))
        self.assertEqual(self.client(transport).rerank(query, documents), [0, 1])
        sent = transport.call_args.args[1]["input"]
        self.assertEqual(sent["query"], "界" * 1000)
        self.assertEqual(sent["documents"], ["汉" * 1166, "😀" * 875])
        self.assertEqual(documents, originals)
        self.assertTrue(query.endswith("尾"))
        for document in sent["documents"]:
            self.assertLessEqual(len(document.encode("utf-8")), 3500)
            self.assertNotIn("\ufffd", document)

    def test_byte_guard_charges_query_once_per_document(self):
        transport = Mock()
        # Unique serialized input is below 100k, but the repeated-query cost
        # exceeds it. This must not reach the provider.
        self.assertIsNone(self.client(transport).rerank("q" * 3000, ["d"] * 34))
        transport.assert_not_called()

    def test_partial_multibyte_character_is_dropped_without_replacement(self):
        transport = Mock(return_value=response(scores((0, .5))))
        self.assertEqual(self.client(transport).rerank("x" + "界" * 1000, ["x" + "😀" * 1500]), [0])
        sent = transport.call_args.args[1]["input"]
        self.assertEqual(sent["query"], "x" + "界" * 999)
        self.assertEqual(sent["documents"], ["x" + "😀" * 874])
        self.assertLessEqual(len(sent["query"].encode()), R.MAX_QUERY_BYTES)
        self.assertLessEqual(len(sent["documents"][0].encode()), R.MAX_DOCUMENT_BYTES)

    def test_weighted_byte_boundary_and_max_documents(self):
        transport = Mock(return_value=response(scores(*((i, .5) for i in range(25)))))
        query, documents = "q" * 1000, ["d" * 3000] * 25
        self.assertEqual(self.client(transport).rerank(query, documents), list(range(25)))
        transport.reset_mock()
        documents[-1] += "d"
        self.assertIsNone(self.client(transport).rerank(query, documents))
        transport.assert_not_called()
        all_docs = ["d"] * 64
        transport.return_value = response(scores(*((i, .1) for i in range(64))))
        self.assertEqual(len(self.client(transport).rerank("q", all_docs)), 64)

    def test_errors_emit_no_documents_key_or_provider_body(self):
        transport = Mock(side_effect=ValueError("key=offline-test-key; source=PRIVATE-SOURCE"))
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            self.assertIsNone(self.client(transport).rerank("PRIVATE-QUERY", ["PRIVATE-SOURCE"]))
        self.assertEqual(output.getvalue(), "")

    def test_default_transport_is_offline_mocked_and_has_no_redirects(self):
        class Reply:
            status = 200
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, maximum):
                return json.dumps(response(scores((0, .7)))[1]).encode()
        opener = Mock()
        opener.open.return_value = Reply()
        with patch.object(R.urllib.request, "build_opener", return_value=opener) as build:
            self.assertEqual(R.RerankerClient("offline").rerank("q", ["a"]), [0])
        self.assertIsInstance(build.call_args.args[0], R._NoRedirect)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 6.0)
        self.assertIsNone(R._NoRedirect().redirect_request(None, None, 302, None, {}, "https://other.test"))

    def test_default_transport_rejects_invalid_json_and_oversized_body(self):
        class Reply:
            status = 200
            def __init__(self, body):
                self.body = body
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, maximum):
                return self.body
        for body in (b"not-json", b"x" * (R._MAX_RESPONSE_BYTES + 1)):
            opener = Mock()
            opener.open.return_value = Reply(body)
            with patch.object(R.urllib.request, "build_opener", return_value=opener):
                self.assertIsNone(R.RerankerClient("offline").rerank("q", ["a"]))

    def test_exceptions_release_concurrency_slots(self):
        client = self.client(Mock(side_effect=RuntimeError("failed")))
        for _ in range(12):
            self.assertIsNone(client.rerank("q", ["a"]))
        transport = Mock(return_value=response(scores((0, .5))))
        self.assertEqual(self.client(transport).rerank("q", ["a"]), [0])

    def test_concurrency_is_eight_globally_and_busy_calls_do_not_queue(self):
        lock, release = threading.Lock(), threading.Event()
        full, fallbacks_done = threading.Event(), threading.Event()
        gate = threading.Barrier(17)
        state = {"active": 0, "peak": 0, "calls": 0, "fallbacks": 0}

        def transport(url, payload, headers, timeout):
            with lock:
                state["active"] += 1
                state["calls"] += 1
                state["peak"] = max(state["peak"], state["active"])
                if state["active"] == 8:
                    full.set()
            try:
                if not release.wait(4):
                    raise TimeoutError("offline concurrency check timed out")
                return response(scores((0, .5)))
            finally:
                with lock:
                    state["active"] -= 1

        clients = [self.client(transport), self.client(transport)]
        def work(index):
            gate.wait(timeout=4)
            result = clients[index % 2].rerank("q", ["a"])
            if result is None:
                with lock:
                    state["fallbacks"] += 1
                    if state["fallbacks"] == 8:
                        fallbacks_done.set()
            return result

        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = [pool.submit(work, index) for index in range(16)]
            try:
                gate.wait(timeout=4)
                self.assertTrue(full.wait(3))
                self.assertTrue(fallbacks_done.wait(3))
            finally:
                release.set()
            results = [future.result() for future in futures]
        self.assertEqual(state["peak"], 8)
        self.assertEqual(state["calls"], 8)
        self.assertEqual(sum(result == [0] for result in results), 8)
        self.assertEqual(sum(result is None for result in results), 8)


if __name__ == "__main__":
    unittest.main()
