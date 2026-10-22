"""Independent offline ranking integration controls on invented source rows.

No provider calls, deployment SQLite, real credentials or official data.
"""
from __future__ import annotations

import builtins
import copy
from dataclasses import replace
import math
import os
from pathlib import Path
import socket
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

_original_open = builtins.open


def _safe_open(file, *args, **kwargs):
    if ".secrets" in str(file):
        raise FileNotFoundError("credential files disabled in synthetic tests")
    return _original_open(file, *args, **kwargs)


with patch.dict(os.environ, {"EMBEDDING_API_KEY": "offline", "DASHSCOPE_API_KEY": "offline", "AML_AUTH_TOKEN": "offline", "AML_RERANK": "false"}), \
     patch("builtins.open", _safe_open), patch.object(socket.socket, "connect", side_effect=AssertionError("No network")):
    import store as S


def row(rid, content, role="user", score=.9):
    return score, {"rid": rid, "user_id": "synthetic", "session_id": "notes", "role": role,
                   "content": content, "seq": rid, "created_at": 1735689600 + rid,
                   "timestamp_origin": "message.timestamp", "status": "active"}


class RankingIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.memory = S.MemoryStore.__new__(S.MemoryStore)
        self.client = Mock()
        self.memory._reranker = self.client

    def test_sparse_old_method_enters_pool_below_dense_top_sixty_four(self):
        ranked = [row(index, "The ordinary cabinet contains spare clamps and bolts.", score=1-index*.004)
                  for index in range(1, 111)]
        old = row(111, "Torque normalization procedure: subtract 1.25 from the hatch reading, then multiply by 0.72.", score=.2)
        ranked.append(old)
        original = copy.deepcopy(ranked)
        def select_method(query, documents):
            selected = next(index for index, document in enumerate(documents) if "Torque normalization procedure" in document)
            return [selected] + [index for index in range(len(documents)) if index != selected]
        self.client.rerank.side_effect = select_method
        result = self.memory._rerank_rows("Apply the archived torque normalization procedure to the hatch reading.", ranked)
        self.assertEqual(result[0][1]["rid"], 111)
        self.assertEqual(len(self.client.rerank.call_args.args[1]), 64)
        self.assertEqual(len(result), len(ranked))
        self.assertEqual({item[1]["rid"] for item in result}, {item[1]["rid"] for item in ranked})
        self.assertEqual(ranked, original)
        self.assertEqual({id(item[1]) for item in result}, {id(item[1]) for item in ranked})

    def test_repeated_source_wording_is_not_deduplicated(self):
        ranked = [row(1, "Valve limit is 7.25 kPa."), row(2, "Valve limit is 7.25 kPa.")]
        self.client.rerank.return_value = [1, 0]
        result = self.memory._rerank_rows("Which valve limit applies?", ranked)
        self.assertEqual([item[1]["rid"] for item in result], [2, 1])
        self.assertIs(result[0][1], ranked[1][1])
        self.assertIs(result[1][1], ranked[0][1])

    def test_role_is_sent_as_metadata_and_remains_on_original_rows(self):
        ranked = [row(1, "I maintain the amber adapter.", role="user"),
                  row(2, "I maintain the cobalt adapter.", role="assistant")]
        self.client.rerank.return_value = [1, 0]
        result = self.memory._rerank_rows("Who maintains each adapter?", ranked)
        documents = self.client.rerank.call_args.args[1]
        self.assertTrue(documents[0].startswith("[speaker role=user]\n"))
        self.assertTrue(documents[1].startswith("[speaker role=assistant]\n"))
        self.assertEqual([item[1]["role"] for item in result], ["assistant", "user"])
        self.assertEqual(result[0][1]["content"], "I maintain the cobalt adapter.")

    def test_failure_or_invalid_permutation_preserves_exact_original_order(self):
        ranked = [row(1, "First candidate."), row(2, "Second candidate.")]
        for invalid in (None, [], [0], [0, 0], [0, 2], [False, 1], [0.0, 1], (1, 0)):
            with self.subTest(invalid=invalid):
                self.client.rerank.return_value = invalid
                self.assertIs(self.memory._rerank_rows("Which candidate applies?", ranked), ranked)
        self.client.rerank.side_effect = TimeoutError("offline timeout")
        self.assertIs(self.memory._rerank_rows("Which candidate applies?", ranked), ranked)

    def test_disabled_or_too_small_pool_never_calls_client(self):
        ranked = [row(1, "First candidate."), row(2, "Second candidate.")]
        self.memory._reranker = None
        self.assertIs(self.memory._rerank_rows("Which candidate applies?", ranked), ranked)
        self.memory._reranker = self.client
        one = ranked[:1]
        self.assertIs(self.memory._rerank_rows("Which candidate applies?", one), one)
        self.assertIs(self.memory._rerank_rows(" ", ranked), ranked)
        self.client.rerank.assert_not_called()

    def test_current_original_temporal_summary_and_personal_guards_bypass_model(self):
        ranked = [row(1, "The pressure ceiling is 180.25 kPa."), row(2, "The earlier pressure ceiling was 240.5 kPa.")]
        self.client.rerank.return_value = [1, 0]
        for query in ("What is the current pressure ceiling?", "What was the original pressure setting?",
                      "In which order did the events occur?",
                      "Summarize the operating constraints.", "What is my name?", "Where do I live?"):
            with self.subTest(query=query):
                self.assertIs(self.memory._rerank_rows(query, ranked), ranked)
        self.client.rerank.assert_not_called()

    def test_first_event_with_noun_preserves_already_correct_time_order(self):
        ranked = [row(1, "The amber review occurred on 2025-06-05.", score=.95),
                  row(2, "The bronze review occurred on 2025-06-12.", score=.94)]
        self.client.rerank.return_value = [1, 0]
        self.assertIs(self.memory._rerank_rows("Which event happened first?", ranked), ranked)
        self.client.rerank.assert_not_called()

    def test_earlier_method_reference_does_not_falsely_mean_event_order(self):
        ranked = [row(1, "The earlier normalization procedure subtracts 1.25 before scaling."),
                  row(2, "The hatch reading is 12.5 N.")]
        self.client.rerank.return_value = [1, 0]
        result = self.memory._rerank_rows("Apply the earlier normalization procedure to this hatch reading.", ranked)
        self.assertEqual([item[1]["rid"] for item in result], [2, 1])
        self.client.rerank.assert_called_once()

    def test_already_prioritized_explicit_correction_is_not_moved(self):
        ranked = [row(2, "Correction: the pressure ceiling is 180.25 kPa; do not use the old ceiling."),
                  row(1, "The pressure ceiling is 240.5 kPa.")]
        self.assertIs(self.memory._rerank_rows("Which pressure ceiling applies?", ranked), ranked)
        self.client.rerank.assert_not_called()


class OfflineStore(S.MemoryStore):
    def __init__(self, client):
        self._dim, self.vectors, self.connections, self._reranker = 4, {}, [], client
        self._ensure_schema()

    def _connect(self):
        connection = super()._connect()
        self.connections.append(connection)
        return connection

    def embed(self, texts):
        return [self.vectors.get(text, [1., 0., 0., 0.]) for text in texts]


class ScopedDatabaseIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="aml-rerank-store-")
        self.root = Path(self.temporary.name).resolve()
        original_connect = sqlite3.connect
        def safe_connect(database, *args, **kwargs):
            if str(database) != ":memory:" and not Path(database).resolve().is_relative_to(self.root):
                raise RuntimeError("only synthetic temporary databases are allowed")
            return original_connect(database, *args, **kwargs)
        self.guards = [patch.object(S, "CONFIG", replace(S.CONFIG, db_path=str(self.root / "test.db"), embedding_dim=4)),
                       patch("sqlite3.connect", safe_connect), patch("builtins.open", _safe_open),
                       patch("socket.create_connection", side_effect=AssertionError("No network")),
                       patch.object(socket.socket, "connect", side_effect=AssertionError("No network"))]
        for guard in self.guards:
            guard.start()
        self.client = Mock()
        self.client.rerank.side_effect = lambda query, documents: list(reversed(range(len(documents))))
        self.memory = OfflineStore(self.client)

    def tearDown(self):
        for connection in self.memory.connections:
            connection.close()
        for guard in reversed(self.guards):
            guard.stop()
        self.temporary.cleanup()

    def add(self, text, score=.95, user="target", role="user"):
        self.memory.vectors[text] = [score, math.sqrt(1-score*score), 0., 0.]
        self.memory.add_messages(user, "notes", [{"role": role, "content": text, "timestamp": 1735689600000}])

    def raw(self, query):
        with self.memory._connect() as connection:
            return self.memory._search_raw(connection, "target", [1., 0., 0., 0.], 100, query=query)

    def test_other_user_sources_never_reach_ranking_provider_or_results(self):
        self.add("The amber valve operating procedure is to subtract 1.25 before scaling.", .94)
        self.add("The amber valve installation uses two washers.", .95)
        self.add("The private operating procedure is FOREIGN-USER-SENTINEL.", .999, user="other")
        rows = self.raw("Which amber valve operating procedure applies?")
        self.assertTrue(self.client.rerank.called)
        sent = "\n".join(self.client.rerank.call_args.args[1])
        self.assertNotIn("FOREIGN-USER-SENTINEL", sent)
        self.assertNotIn("FOREIGN-USER-SENTINEL", "\n".join(item["content"] for item in rows))
        with self.memory._connect() as connection:
            actual = {item[0] for item in connection.execute("SELECT rid FROM items WHERE user_id='target'")}
        self.assertTrue(all(item["source_rid"] in actual for item in rows))

    def test_different_explicit_protocol_scope_is_removed_before_ranking(self):
        self.add("Protocol ID PR-HERON-AX. The force unit is N; divide the timing input by 1000 to obtain seconds.", .94)
        self.add("Protocol PR-HERON-AX new case: force=15; duration=40ms.", .95)
        self.add("Protocol ID PR-HERON-AY. Private override SCOPE-PRIVATE-SENTINEL.", .999)
        rows = self.raw("Apply protocol PR-HERON-AX to this reading.")
        self.assertTrue(self.client.rerank.called)
        self.assertNotIn("SCOPE-PRIVATE-SENTINEL", "\n".join(self.client.rerank.call_args.args[1]))
        self.assertNotIn("SCOPE-PRIVATE-SENTINEL", "\n".join(item["content"] for item in rows))

    def test_personal_name_scope_and_speaker_survive_adversarial_ranker(self):
        self.add("My name is Rowan.", .95)
        self.add("My name is Copper.", .999, role="assistant")
        self.add("My confidential medical code is PRIVATE-CARE-SENTINEL.", .98)
        rows = self.raw("What is my name?")
        self.client.rerank.assert_not_called()
        text = "\n".join(item["content"] for item in rows)
        self.assertIn("Rowan", text)
        self.assertNotIn("Copper", text)
        self.assertNotIn("PRIVATE-CARE-SENTINEL", text)
        self.assertTrue(all(item["role"] == "user" for item in rows))

    def test_query_time_correction_priority_prevents_late_reordering(self):
        self.add("The operating pressure ceiling is 240.5 kPa.", .95)
        correction = "Correction: the operating pressure ceiling is 180.25 kPa; do not use the old ceiling."
        self.add(correction, .94)
        rows = self.raw("Which operating pressure ceiling applies?")
        self.client.rerank.assert_not_called()
        self.assertTrue(rows[0].get("explicit_correction"))
        self.assertIn(correction, rows[0]["content"])

    def test_named_technical_summary_excludes_private_fields_without_erasing_topic(self):
        technical = "Zephyr turbine project: the team chose a ceramic seal because it withstands 340 C."
        self.add(technical, .96)
        self.add("Zephyr turbine project: final bench validation passed on 2025-06-12.", .95)
        self.add("My home address is 47 Invented Fern Lane.", .99)
        results = self.memory.search("target", "Summarize the Zephyr turbine project without personal details.", 100)
        text = "\n".join(item["content"] for item in results)
        self.assertIn(technical, text)
        self.assertIn("2025-06-12", text)
        self.assertNotIn("47 Invented Fern Lane", text)
        witnessed = [item for item in results if technical in item["content"] and item.get("source_rid") is not None]
        self.assertTrue(witnessed)
        self.assertTrue(all(item["role"] == "user" and item["session_id"] == "notes" for item in witnessed))

    def test_explicit_all_history_with_private_exclusion_retains_safe_facts(self):
        technical = "The Zephyr turbine uses ceramic seals to withstand 340 C."
        botanical = "The herbarium uses photographs to reduce handling of fragile specimens."
        self.add(technical, .96)
        self.add(botanical, .95)
        self.add("My home address is 47 Invented Fern Lane.", .99)
        results = self.memory.search("target", "Summarize all my conversations without personal details.", 100)
        text = "\n".join(item["content"] for item in results)
        self.assertIn(technical, text)
        self.assertIn(botanical, text)
        self.assertNotIn("47 Invented Fern Lane", text)

    def test_vague_private_exclusion_does_not_open_arbitrary_history(self):
        self.add("The Zephyr turbine uses ceramic seals to withstand 340 C.", .96)
        self.add("The herbarium uses photographs to reduce handling of fragile specimens.", .95)
        self.add("My home address is 47 Invented Fern Lane.", .99)
        results = self.memory.search("target", "Summarize key points I shared without personal details.", 100)
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
