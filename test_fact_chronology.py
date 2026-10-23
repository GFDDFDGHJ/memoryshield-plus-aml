"""Invented source-utterance chronology regressions; SQLite memory only.

Message timestamps order assertions, never inferred dates of the described
events. No provider, credentials, retained evaluation content or deployed DB.
"""
from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import patch

import facts as F


class FactChronologyTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("""CREATE TABLE items (
            rid INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT,session_id TEXT,
            role TEXT,content TEXT,created_at REAL,timestamp_origin TEXT,
            seq INTEGER,status TEXT DEFAULT 'active')""")
        F.ensure_schema(self.conn)
        self.seq = 0

    def tearDown(self):
        self.conn.close()

    def add(self, text, timestamp, *, role="user", user="target", origin="message.timestamp"):
        self.seq += 1
        rid = self.conn.execute(
            "INSERT INTO items(user_id,session_id,role,content,created_at,timestamp_origin,seq)"
            " VALUES(?,?,?,?,?,?,?)",
            (user, "history", role, text, timestamp, origin, self.seq),
        ).lastrowid
        F.store_facts(self.conn, user, "history", rid, text, timestamp, self.seq, role)
        return rid

    def rows(self, predicate="name", *, role="user", user="target"):
        return [dict(row) for row in self.conn.execute(
            "SELECT * FROM facts WHERE user_id=? AND subject='self' AND predicate=?"
            " AND source_role=? ORDER BY seq,fid", (user, predicate, role))]

    def current(self, predicate="name", *, role="user", user="target"):
        return [row for row in self.rows(predicate, role=role, user=user) if row["status"] == "active"]

    def test_late_older_assertion_cannot_replace_newer_message(self):
        new = self.add("My name is Sable.", 3000)
        old = self.add("My name is Rowan.", 1000)
        self.assertEqual([(r["value"], r["source_rid"], r["previous_value"])
                          for r in self.current()], [("Sable", new, "Rowan")])
        self.assertEqual({r["source_rid"] for r in self.rows()}, {new, old})

    def test_reordered_delivery_has_same_chronological_state_and_previous(self):
        for user, order in (("ascending", (1000, 2000, 3000)),
                            ("mixed", (3000, 1000, 2000))):
            for timestamp in order:
                self.add("My name is " + {1000:"Rowan", 2000:"Mica", 3000:"Sable"}[timestamp] + ".",
                         timestamp, user=user)
            self.assertEqual([(r["value"], r["previous_value"]) for r in self.current(user=user)],
                             [("Sable", "Mica")])

    def test_same_value_reassertion_keeps_source_and_protects_latest_utterance(self):
        first = self.add("My name is Rowan.", 1000)
        reaffirmed = self.add("My name is Rowan.", 3000)
        middle = self.add("My name is Sable.", 2000)
        self.assertEqual([(r["value"], r["source_rid"], r["previous_value"])
                          for r in self.current()], [("Rowan", reaffirmed, "Sable")])
        self.assertEqual({r["source_rid"] for r in self.rows()}, {first, reaffirmed, middle})

    def test_late_old_retraction_cannot_erase_later_reassertion(self):
        self.add("I live in Bergen.", 3000)
        self.add("I no longer live in Bergen.", 2000)
        self.assertEqual([r["value"] for r in self.current("location")], ["Bergen"])

    def test_newer_retraction_prevents_late_older_assertion_from_reviving(self):
        self.add("I no longer live in Bergen.", 3000)
        self.add("I live in Bergen.", 1000)
        self.assertEqual(self.current("location"), [])
        self.assertEqual([r["status"] for r in self.rows("location")], ["stale"])

    def test_retraction_of_other_value_does_not_erase_current_state(self):
        self.add("I live in Bergen.", 3000)
        self.add("I no longer live in Oslo.", 4000)
        self.assertEqual([r["value"] for r in self.current("location")], ["Bergen"])

    def test_equal_timestamps_keep_deterministic_source_order(self):
        self.add("My name is Rowan.", 1000)
        last = self.add("My name is Sable.", 1000)
        self.assertEqual([(r["value"], r["source_rid"]) for r in self.current()], [("Sable", last)])

    def test_timestamp_origins_are_not_compared_when_mixed(self):
        for origin in ("ingestion-clock", "legacy-unknown"):
            user = origin
            self.add("My name is Sable.", 3000, user=user)
            self.add("My name is Rowan.", 900000, origin=origin, user=user)
            self.add("My name is Mica.", 1000, user=user)
            self.assertEqual([r["value"] for r in self.current(user=user)], ["Mica"])

    def test_chronology_isolated_by_role_user_and_attribute(self):
        self.add("My name is Sable.", 3000)
        self.add("My name is Rowan.", 1000)
        self.add("My name is Finch.", 9000, role="assistant")
        self.add("I live in Oslo.", 5000)
        self.add("My name is Quartz.", 9000, user="another")
        self.assertEqual([r["value"] for r in self.current()], ["Sable"])
        self.assertEqual([r["value"] for r in self.current(role="assistant")], ["Finch"])
        self.assertEqual([r["value"] for r in self.current("location")], ["Oslo"])
        self.assertEqual([r["value"] for r in self.current(user="another")], ["Quartz"])

    def test_same_message_assertion_order_is_preserved(self):
        self.add("I live in Oslo. I moved to Bergen. I live in Oslo.", 3000)
        self.assertEqual([(r["value"], r["previous_value"]) for r in self.current("location")],
                         [("Oslo", "Bergen")])

    def test_relative_event_words_do_not_override_message_timestamp(self):
        self.add("My name is Sable. Yesterday I visited Bergen.", 3000)
        self.add("My name is Rowan. Next year I may visit Oslo.", 1000)
        self.assertEqual([r["value"] for r in self.current()], ["Sable"])

    def test_chinese_late_assertion_respects_utterance_time(self):
        self.add("我住在杭州。", 3000)
        self.add("我住在南京。", 1000)
        self.assertEqual([(r["value"], r["previous_value"]) for r in self.current("location")],
                         [("杭州", "南京")])

    def test_touching_one_attribute_does_not_replay_other_attributes(self):
        self.add("I live in Bergen.", 3000)
        self.add("My name is Sable.", 3000)
        with patch.object(F, "_replay_fact_chronology", wraps=F._replay_fact_chronology) as replay:
            self.add("My name is Rowan.", 1000)
        self.assertEqual([(call.args[2],call.args[3],call.args[4]) for call in replay.call_args_list],
                         [("self", "name", "user")])
        self.assertEqual([r["value"] for r in self.current("location")], ["Bergen"])


class ProcedureSelectorIntegrationTests(unittest.TestCase):
    def test_shared_selector_still_filters_source_and_keeps_contiguous_qualifier(self):
        units = [
            {"source_rid":10,"segment":0,"text":"Process each rule once in ascending order."},
            {"source_rid":10,"segment":1,"text":"Unless an override explicitly stops execution."},
            {"source_rid":10,"segment":4,"text":"Otherwise a remote qualifier is unrelated."},
            {"source_rid":20,"segment":0,"text":"Process each rule once in descending order."},
        ]
        with patch.object(F.dependency_layer, "execution_control_indexes", return_value={0,3}) as selector:
            selected = F._procedure_dependency_indexes(units,{0})
        selector.assert_called_once_with([u["text"] for u in units])
        self.assertEqual(selected,{0,1})
        self.assertTrue(units[0]["context_dependency"] and units[1]["context_dependency"])
        self.assertNotIn("context_dependency", units[2])
        self.assertNotIn("context_dependency", units[3])

    def test_procedure_topic_mention_does_not_become_execution_authority(self):
        units = [{"source_rid":10,"segment":0,
                  "text":"The appendix mentions rule skipping and execution order in a fictional example."}]
        self.assertEqual(F._procedure_dependency_indexes(units,{0}),set())


if __name__ == "__main__":
    unittest.main()
