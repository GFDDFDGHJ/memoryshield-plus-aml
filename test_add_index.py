"""Invented source-selection controls using temporary SQLite only."""
from __future__ import annotations

import array
import builtins
from contextlib import contextmanager
from dataclasses import dataclass, replace
import math
import os
import socket
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import add_index as I


@dataclass(frozen=True)
class Span:
    message_index: int
    start: int
    end: int
    kind: str = "fact"


def result(*spans, **changes):
    fields = dict(spans=list(spans), prompt_tokens=87, completion_tokens=23,
                  model="gpt-4o-mini-2024-07-18", call_count=1)
    fields.update(changes)
    return SimpleNamespace(**fields)


class AddIndexTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="aml-invented-gpt-index-")
        self.conn = sqlite3.connect(os.path.join(self.directory.name, "invented.sqlite"))
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("""CREATE TABLE items (
            rid INTEGER PRIMARY KEY, user_id TEXT, session_id TEXT, role TEXT,
            content TEXT, status TEXT, created_at REAL,
            timestamp_origin TEXT, seq INTEGER)""")
        self.conn.execute("CREATE TABLE vec_items(rowid INTEGER PRIMARY KEY, embedding BLOB)")
        I.ensure_schema(self.conn)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.directory.cleanup()

    def source(self, rid=1, text="The Larch alloy hardens after cooling.", *,
               user="invented-a", session="session-a", role="user", status="active"):
        self.conn.execute(
            "INSERT INTO items VALUES(?,?,?,?,?,?,?,?,?)",
            (rid, user, session, role, text, status, 1735689600 + rid,
             "message.timestamp", rid),
        )
        self.conn.execute("INSERT INTO vec_items VALUES(?,?)",
                          (rid, array.array("f", [1.0, 0.0]).tobytes()))
        return {"content": text, "role": role}

    def selected(self, messages, source_ids, extraction=None, *, user="invented-a",
                 session="session-a", request="invented-request"):
        extraction = extraction or result(Span(0, 4, 15))
        I.store_extraction(self.conn, user, session, source_ids, messages,
                           extraction, request_id=request)

    def test_literal_offsets_and_usage_committed_together(self):
        message = self.source()
        self.selected([message], [1])
        self.conn.commit()
        stored = self.conn.execute(
            "SELECT source_rid,start_offset,end_offset,kind,source_role FROM llm_memory_spans"
        ).fetchone()
        self.assertEqual(stored, (1, 4, 15, "fact", "user"))
        self.assertEqual(message["content"][stored[1]:stored[2]], "Larch alloy")
        self.assertEqual(self.conn.execute(
            "SELECT user_id,session_id,request_id,model,prompt_tokens,completion_tokens,call_count "
            "FROM add_llm_usage").fetchone(),
            ("invented-a", "session-a", "invented-request", "gpt-4o-mini-2024-07-18", 87, 23, 1))
        self.assertEqual(I.rank_source_boosts(self.conn, "invented-a", "Larch alloy", [1]), {1: .03})

    def test_caller_rollback_removes_raw_rows_spans_and_usage(self):
        self.conn.execute("BEGIN")
        message = self.source()
        self.selected([message], [1])
        self.conn.rollback()
        for table in ("items", "llm_memory_spans", "add_llm_usage"):
            self.assertEqual(self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)

    def test_requires_existing_transaction(self):
        message = self.source()
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, "transaction"):
            self.selected([message], [1])

    def test_schema_creation_does_not_commit_pending_add(self):
        self.conn.execute("BEGIN")
        self.source()
        I.ensure_schema(self.conn)
        self.assertTrue(self.conn.in_transaction)
        self.conn.rollback()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM items").fetchone()[0], 0)

    def test_rejects_invalid_offsets_indices_or_kinds_before_any_index_write(self):
        message = self.source()
        valid = Span(0, 4, 15)
        for bad in (replace(valid, message_index=-1), replace(valid, message_index=1),
                    replace(valid, message_index=True), replace(valid, start=-1),
                    replace(valid, end=10000), replace(valid, start=4, end=4),
                    replace(valid, start=True), replace(valid, kind="generated_answer")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.selected([message], [1], result(valid, bad))
                self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM llm_memory_spans").fetchone()[0], 0)
                self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM add_llm_usage").fetchone()[0], 0)

    def test_rejects_blank_selection(self):
        message = self.source(text="  A note.")
        with self.assertRaises(ValueError):
            self.selected([message], [1], result(Span(0, 0, 2)))

    def test_rejects_bad_usage_and_wrong_model(self):
        message = self.source()
        for changes in ({"model": "other-model"}, {"model": "gpt-4o-mini-evil"},
                        {"call_count": 0}, {"prompt_tokens": -1},
                        {"completion_tokens": True}, {"call_count": 1.0}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    self.selected([message], [1], result(Span(0, 4, 15), **changes))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM add_llm_usage").fetchone()[0], 0)

    def test_rejects_wrong_user_session_role_content_inactive_or_missing_source(self):
        message = self.source()
        for changes in ({"user": "invented-b"}, {"session": "session-b"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.selected([message], [1], **changes)
        for changed_message in ({**message, "role": "assistant"},
                                {**message, "content": "The Fir alloy is different."}):
            with self.subTest(message=changed_message), self.assertRaises(ValueError):
                self.selected([changed_message], [1])
        with self.assertRaises(ValueError):
            self.selected([message], [999])
        self.conn.execute("UPDATE items SET status='superseded' WHERE rid=1")
        with self.assertRaises(ValueError):
            self.selected([message], [1])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM llm_memory_spans").fetchone()[0], 0)

    def test_empty_selection_records_real_call_and_does_not_invent_evidence(self):
        message = self.source()
        self.selected([message], [1], result())
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM add_llm_usage").fetchone()[0], 1)
        self.assertEqual(I.rank_source_boosts(self.conn, "invented-a", "Larch alloy", [1]), {})

    def test_duplicate_spans_have_one_index_entry(self):
        message = self.source()
        span = Span(0, 4, 15)
        self.selected([message], [1], result(span, span))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM llm_memory_spans").fetchone()[0], 1)

    def test_request_usage_is_unique_and_caller_can_rollback_all_add_data(self):
        message = self.source()
        self.selected([message], [1])
        with self.assertRaises(sqlite3.IntegrityError):
            self.selected([message], [1])
        self.conn.rollback()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM add_llm_usage").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM items").fetchone()[0], 0)

    def test_does_not_retrieve_other_users_or_sources_excluded_by_caller(self):
        first = self.source()
        second = self.source(2, user="invented-b")
        self.selected([first], [1])
        self.selected([second], [2], user="invented-b")
        self.assertEqual(I.rank_source_boosts(self.conn, "invented-a", "Larch alloy", [2]), {})
        self.assertEqual(I.rank_source_boosts(self.conn, "invented-b", "Larch alloy", [1]), {})
        self.assertEqual(I.rank_source_boosts(self.conn, "invented-a", "Larch alloy", []), {})
        self.assertEqual(set(I.rank_source_boosts(self.conn, "invented-a", "Larch alloy", [1, 2])), {1})

    def test_source_changed_after_extraction_loses_all_boost(self):
        message = self.source()
        self.selected([message], [1])
        self.conn.commit()
        for field, changed in (("content", "The Larch alloy melts before cooling."),
                               ("role", "assistant"), ("session_id", "different"),
                               ("status", "superseded")):
            with self.subTest(field=field):
                self.conn.execute(f"UPDATE items SET {field}=? WHERE rid=1", (changed,))
                self.assertEqual(I.rank_source_boosts(self.conn, "invented-a", "Larch alloy", [1]), {})
                self.conn.rollback()

    def test_unselected_words_and_generic_question_words_do_not_receive_boost(self):
        message = self.source()
        self.selected([message], [1])
        for query in ("cooling", "the what which is", "", "unrelated nebula"):
            with self.subTest(query=query):
                self.assertEqual(I.rank_source_boosts(self.conn, "invented-a", query, [1]), {})

    def test_bounded_overlap_cannot_accumulate_over_many_duplicate_spans(self):
        message = self.source()
        self.selected([message], [1], result(Span(0, 4, 15), Span(0, 4, 9)))
        boosts = I.rank_source_boosts(self.conn, "invented-a", "Larch alloy pressure", [1])
        self.assertAlmostEqual(boosts[1], .02)
        self.assertLessEqual(boosts[1], I.MAX_SOURCE_BOOST)

    def test_unicode_offsets_and_text_part_normalization_match_originals(self):
        message = self.source(text="白鹭材料\n冷却后硬化。")
        message["content"] = [{"type": "text", "text": "白鹭材料"},
                              {"type": "text", "text": "冷却后硬化。"}]
        self.selected([message], [1], result(Span(0, 0, 4)))
        self.assertEqual(I.rank_source_boosts(self.conn, "invented-a", "白鹭材料", [1]), {1: .03})
        self.assertEqual(I.rank_source_boosts(self.conn, "invented-a", "冷却后硬化", [1]), {})

    def test_batched_source_id_selection(self):
        for rid in range(1, 1003):
            message = self.source(rid)
        self.selected([message], [1002])
        self.assertEqual(I.rank_source_boosts(self.conn, "invented-a", "Larch alloy",
                                            list(range(1, 1003))), {1002: .03})

    @contextmanager
    def _raw_search_store(self):
        original_open = builtins.open
        def safe_open(path, *args, **kwargs):
            if ".secrets" in str(path):
                raise FileNotFoundError("synthetic test blocks credential files")
            return original_open(path, *args, **kwargs)
        fake = {"EMBEDDING_API_KEY": "offline", "DASHSCOPE_API_KEY": "offline",
                "OPENAI_API_KEY": "offline", "AML_AUTH_TOKEN": "offline", "AML_RERANK": "false"}
        with patch.dict(os.environ, fake), patch("builtins.open", safe_open), \
             patch.object(socket.socket, "connect", side_effect=AssertionError("no network")):
            import store as S
            memory = S.MemoryStore.__new__(S.MemoryStore)
            memory._reranker = None
            self.conn.row_factory = sqlite3.Row
            yield memory

    def test_current_state_keeps_newest_equal_vector_source_despite_old_span_selection(self):
        with self._raw_search_store() as memory:
            old = self.source(1, "The pressure ceiling is 240.5 kPa.")
            new = self.source(2, "The pressure ceiling is 180.25 kPa.")
            query = "What is the current pressure ceiling?"
            before = memory._search_raw(self.conn, "invented-a", [1.0, 0.0], 1, query=query)
            self.assertEqual(before[0]["source_rid"], 2)
            self.selected([old, new], [1, 2], result(Span(0, 0, len(old["content"]))))
            self.assertGreater(I.rank_source_boosts(self.conn, "invented-a", query, [1, 2])[1], .01)
            after = memory._search_raw(self.conn, "invented-a", [1.0, 0.0], 1, query=query)
            self.assertEqual(after[0]["source_rid"], 2)
            self.assertTrue(after[0]["content"].endswith(new["content"]))

    def test_original_state_preserves_earliest_source_at_semantic_near_tie_boundary(self):
        with self._raw_search_store() as memory:
            old = self.source(1, "The pressure setting is 240.5 kPa.")
            new = self.source(2, "The pressure setting is 180.25 kPa.")
            # The earlier state is within the existing 0.10 semantic window.
            # A 0.02 boost on the later row must not push it outside that window.
            for rid, cosine in ((1, .81), (2, .90)):
                vector = array.array("f", [cosine, math.sqrt(1 - cosine * cosine)]).tobytes()
                self.conn.execute("UPDATE vec_items SET embedding=? WHERE rowid=?", (vector, rid))
            query = "What was the original pressure setting?"
            before = memory._search_raw(self.conn, "invented-a", [1.0, 0.0], 1, query=query)
            self.assertEqual(before[0]["source_rid"], 1)
            self.selected([old, new], [1, 2], result(Span(1, 0, len(new["content"]))))
            self.assertGreater(I.rank_source_boosts(self.conn, "invented-a", query, [1, 2])[2], .01)
            after = memory._search_raw(self.conn, "invented-a", [1.0, 0.0], 1, query=query)
            self.assertEqual(after[0]["source_rid"], 1)
            self.assertTrue(after[0]["content"].endswith(old["content"]))

    def test_sequence_context_keeps_original_anchor_and_neighbor_order(self):
        with self._raw_search_store() as memory:
            texts = ["The review opened the crate.", "The review checked the valve.",
                     "The review tested the bracket.", "The review approved the casing.",
                     "The review closed the file."]
            messages = [self.source(index, text) for index, text in enumerate(texts, 1)]
            query = "In what order did the review events occur?"
            before = memory._search_raw(self.conn, "invented-a", [1.0, 0.0], 3, query=query)
            self.assertEqual([row["source_rid"] for row in before], [1, 2, 3])
            self.selected(messages, [1, 2, 3, 4, 5], result(Span(3, 0, len(texts[3]))))
            self.assertGreater(I.rank_source_boosts(self.conn, "invented-a", query, [4])[4], 0)
            after = memory._search_raw(self.conn, "invented-a", [1.0, 0.0], 3, query=query)
            self.assertEqual([row["source_rid"] for row in after], [1, 2, 3])

    def test_selected_source_changes_actual_search_tie_without_new_claims(self):
        # Import only after replacing credentials and blocking network. The
        # SQLite connection above is a throwaway ordinary table fixture.
        original_open = builtins.open
        def safe_open(path, *args, **kwargs):
            if ".secrets" in str(path):
                raise FileNotFoundError("synthetic test blocks credential files")
            return original_open(path, *args, **kwargs)
        fake = {"EMBEDDING_API_KEY": "offline", "DASHSCOPE_API_KEY": "offline",
                "OPENAI_API_KEY": "offline", "AML_AUTH_TOKEN": "offline", "AML_RERANK": "false"}
        with patch.dict(os.environ, fake), patch("builtins.open", safe_open), \
             patch.object(socket.socket, "connect", side_effect=AssertionError("no network")):
            import store as S
            memory = S.MemoryStore.__new__(S.MemoryStore)
            memory._reranker = None
            self.conn.row_factory = sqlite3.Row
            first = self.source(1, "An ordinary cabinet holds a spare Larch alloy.")
            second = self.source(2, "The tested Larch alloy resists bending.")
            before = memory._search_raw(self.conn, "invented-a", [1.0, 0.0], 1,
                                        query="Larch alloy")
            self.assertEqual(before[0]["source_rid"], 1)
            start = second["content"].index("Larch alloy")
            self.selected([first, second], [1, 2], result(Span(1, start, start + 11)))
            after = memory._search_raw(self.conn, "invented-a", [1.0, 0.0], 1,
                                       query="Larch alloy")
            self.assertEqual(after[0]["source_rid"], 2)
            self.assertTrue(after[0]["content"].endswith(second["content"]))
            excluded = memory._search_raw(self.conn, "invented-a", [1.0, 0.0], 1,
                                          query="Larch alloy", exclude_source_rids={2})
            self.assertEqual(excluded[0]["source_rid"], 1)


if __name__ == "__main__":
    unittest.main()
