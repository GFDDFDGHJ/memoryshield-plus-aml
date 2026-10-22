"""General temporal and table-evidence regressions on invented local records."""
from __future__ import annotations
import builtins
from contextlib import closing
from dataclasses import replace
from datetime import datetime
import math
import os
from pathlib import Path
import socket
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("EMBEDDING_API_KEY", "offline-placeholder")
os.environ.setdefault("AML_AUTH_TOKEN", "offline-placeholder")
_original_open = builtins.open
def _safe_open(file, *args, **kwargs):
    if ".secrets" in str(file):
        raise OSError("No credential access in synthetic tests")
    return _original_open(file, *args, **kwargs)
with patch("builtins.open", _safe_open):
    import store
    from test_store_regressions import OfflineMemoryStore, raw_literal


class TemporalEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aml-temporal-context-")
        self.config = patch.object(store, "CONFIG", replace(
            store.CONFIG, db_path=str(Path(self.temp.name) / "isolated.db"), embedding_dim=4))
        self.config.start()
        self.no_network = patch.object(socket.socket, "connect", side_effect=AssertionError("No network"))
        self.no_network.start()
        self.memory = OfflineMemoryStore()

    def tearDown(self):
        self.memory.close()
        self.no_network.stop()
        self.config.stop()
        self.temp.cleanup()

    def add(self, text, *, timestamp=1735732800000, session="synthetic", user="target", score=.9):
        self.memory.vectors[text] = [score, math.sqrt(1-score*score), 0, 0]
        message = dict(role="user", content=text)
        if timestamp is not None:
            message["timestamp"] = timestamp
        self.memory.add_messages(user, session, [message])

    def raw(self, query, limit=100):
        with self.memory._connect() as conn:
            return self.memory._search_raw(conn, "target", [1, 0, 0, 0], limit, query=query)

    def test_provided_record_date_is_a_relative_expression_anchor(self):
        text = "Tomorrow I will deliver the bronze parcel."
        self.add(text)
        result = self.raw("When will the bronze parcel be delivered?")[0]
        self.assertEqual(result["timestamp_origin"], "message.timestamp")
        self.assertEqual(result["relative_time_anchor"], "2025-01-01T12:00:00Z")
        self.assertEqual(raw_literal(result), text)
        self.assertIn("not event occurrence time", result["content"])

    def test_missing_source_date_never_claims_ingestion_as_relative_anchor(self):
        text = "明天我要寄出青铜包裹。"
        with patch.object(store.time, "time", return_value=1735732800):
            self.add(text, timestamp=None)
        result = self.raw("包裹什么时候寄出？")[0]
        self.assertEqual(result["timestamp_origin"], "ingestion-clock")
        self.assertIsNone(result["relative_time_anchor"])
        self.assertIn("do not resolve relative dates from ingestion time", result["content"])
        self.assertEqual(raw_literal(result), text)

    def test_epoch_zero_is_an_explicit_timestamp_not_a_missing_value(self):
        self.add("The archive entry was recorded today.", timestamp=0)
        result = self.raw("When was the archive entry recorded?")[0]
        self.assertEqual(result["relative_time_anchor"], "1970-01-01T00:00:00Z")
        self.assertEqual(result["timestamp_origin"], "message.timestamp")

    def test_provided_millisecond_timestamp_roundtrips_without_losing_anchor_precision(self):
        text = "Tomorrow the bronze parcel will be delivered."
        timestamp = 1735732800123
        self.add(text, timestamp=timestamp)
        result = self.raw("When will the bronze parcel be delivered?")[0]
        self.assertEqual(result["recorded_at"], "2025-01-01T12:00:00.123Z")
        self.assertEqual(result["relative_time_anchor"], result["recorded_at"])
        self.assertEqual(round(datetime.fromisoformat(result["recorded_at"].replace("Z", "+00:00")).timestamp()*1000), timestamp)
        self.assertEqual(raw_literal(result), text)

    def test_schema_migration_preserves_legacy_unknown_time(self):
        # Use a separate temporary legacy schema; never inspect deployment data.
        legacy = str(Path(self.temp.name) / "legacy.db")
        with closing(sqlite3.connect(legacy)) as conn:
            conn.execute("CREATE TABLE items(rid INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT NOT NULL,session_id TEXT NOT NULL,role TEXT NOT NULL,content TEXT NOT NULL,created_at REAL NOT NULL,status TEXT NOT NULL DEFAULT 'active',seq INTEGER NOT NULL)")
            conn.execute("INSERT INTO items(user_id,session_id,role,content,created_at,status,seq) VALUES('target','legacy','user','Tomorrow the legacy package arrives.',1735732800,'active',1)")
            conn.commit()
        with patch.object(store, "CONFIG", replace(store.CONFIG, db_path=legacy)):
            migrated = OfflineMemoryStore()
            try:
                with migrated._connect() as conn:
                    conn.execute("INSERT INTO vec_items(rowid,embedding) VALUES(1,?)", (store._pack([1,0,0,0]),))
                result = migrated.search("target", "When does the legacy package arrive?", 1)[0]
                self.assertEqual(result["timestamp_origin"], "legacy-unknown")
                self.assertIsNone(result["relative_time_anchor"])
                self.assertEqual(raw_literal(result), "Tomorrow the legacy package arrives.")
            finally:
                migrated.close()

    def test_earliest_event_query_does_not_use_ingestion_order_as_event_order(self):
        later = "Archive note: Juniper sensor calibration happened on 2025-05-10."
        earlier = "Archive note: Juniper sensor calibration happened on 2025-04-02."
        self.add(later, score=.88)
        self.add(earlier, score=.92)
        for query in ("When was the earliest Juniper sensor calibration?", "Juniper 传感器最早在哪一天校准？",
                      "¿Cuándo fue la primera calibración del sensor Juniper?"):
            with self.subTest(query=query):
                self.assertEqual(raw_literal(self.raw(query, 1)[0]), earlier)

    def test_explicit_original_state_retains_its_existing_conservative_selection(self):
        old = "Studio access code before launch: AMBER-8."
        new = "Studio access code after launch: VIOLET-6."
        self.add(old, score=.80)
        self.add(new, score=.84)
        self.assertEqual(raw_literal(self.raw("What was the original studio access code?", 1)[0]), old)

    def test_dense_gap_does_not_erase_causal_evidence_that_omits_query_nouns(self):
        overview = "The Meridian generator delay was discussed at the meeting on June 12."
        cause = "A seized inlet bearing triggered the stoppage on June 9."
        self.add(overview, score=.99)
        self.add(cause, score=.60)
        for query in ("Which event caused the Meridian generator delay, and on which day?",
                      "哪个事件导致 Meridian 发电机延迟，发生在哪一天？"):
            with self.subTest(query=query):
                self.assertIn(cause, [raw_literal(result) for result in self.raw(query)])

    def test_numeric_table_row_keeps_column_schema_in_multiple_languages_and_formats(self):
        for delimiter, labels, query in (
            (" | ", ["ID", "elapsed time (ms)", "pressure (kPa)"], "For specimen R137, what values were observed?"),
            ("\t", ["编号", "延迟（毫秒）", "压力（千帕）"], "R137 的观测值是什么？"),
            (",", ["sample", "latency_ms", "pressure_kPa"], "What was measured for sample R137?"),
        ):
            header = delimiter.join(labels)
            text = header + "\n" + "\n".join(delimiter.join([f"R{i:03d}",str(100+i),f"{120+i/10:.1f}"]) for i in range(1,221))
            preview = store._query_preview(text, query)
            with self.subTest(delimiter=delimiter):
                self.assertIn(header, preview)
                self.assertIn(delimiter.join(["R137","237","133.7"]), preview)

    def test_long_excerpt_preserves_chained_numeric_exception_after_table_schema(self):
        text = ("General maintenance notes. " * 150 +
                "Pressure limit is 238.4 kPa. However, this limit applies only under dry conditions. "
                "Otherwise, use 192.6 kPa. " + "General appendix notes. " * 150)
        preview = store._query_preview(text, "What is the pressure limit?")
        for literal in ("238.4 kPa", "only under dry conditions", "192.6 kPa"):
            self.assertIn(literal, preview)

    def test_store_uses_shared_context_intent_for_summary_and_aggregate_budgets(self):
        queries = ["Estimate a distribution from the calibration runs.", "计算这些测量的平均值。",
                   "Give the event sequence for Cedar.", "Summarize the project.", "What is the parcel key?"]
        for query in queries:
            with self.subTest(query=query):
                self.assertEqual(store._summary_query(query), store.fact_layer.context_scope(query)[0] != "ordinary")

    def test_single_attribute_request_uses_positive_scope_without_disclosing_other_records(self):
        self.add("My name is Tamsin.", score=.995)
        self.add("My private street address is 28 Imaginary Cedar Lane.", score=.1)
        self.add("My confidential medical code is MED-8847-PRIVATE.", score=.08)
        rendered = str(self.memory.search("target", "What is my name?", 100))
        self.assertIn("Tamsin", rendered)
        self.assertNotIn("28 Imaginary Cedar Lane", rendered)
        self.assertNotIn("MED-8847-PRIVATE", rendered)

    def test_single_attribute_request_excerpts_only_matching_source_sentences(self):
        self.add("My name is Tamsin. My private street address is 28 Imaginary Cedar Lane.")
        rendered = str(self.memory.search("target", "What is my name?", 100))
        self.assertIn("Tamsin", rendered)
        self.assertNotIn("28 Imaginary Cedar Lane", rendered)

    def test_narrow_project_context_does_not_include_unrelated_same_session_rows(self):
        self.add("Cedar deployment began with a granite prototype.", score=.99)
        self.add("Cedar deployment adopted a basalt prototype.", score=.98)
        self.add("My private home address is 14 Synthesist Lane.", score=.08)
        self.add("My private cabinet code is CAB-6721-SECRET.", score=.07)
        rendered = str(self.memory.search("target", "Recap only the Cedar deployment choices.", 100))
        self.assertIn("granite prototype", rendered)
        self.assertIn("basalt prototype", rendered)
        self.assertNotIn("14 Synthesist Lane", rendered)
        self.assertNotIn("CAB-6721-SECRET", rendered)

    def _employment_history_with_other_speaker(self):
        self.add("I work at Bracken Analytics.", score=.99)
        assistant = "I work at Dune Robotics."
        self.memory.vectors[assistant] = [.8,.6,0,0]
        self.memory.add_messages("target", "synthetic", [dict(
            role="assistant", content=assistant, timestamp=1735732800000)])
        self.add("I changed my company to Cedar Analytics.", score=.7)
        self.add("My name is Tamsin.", score=.1)

    def test_single_attribute_scope_keeps_current_metadata_and_separates_speakers(self):
        self._employment_history_with_other_speaker()
        results = self.memory.search("target", "What is my current employer?", 100)
        self.assertTrue(results[0]["id"].startswith("change_"))
        self.assertIn("[CURRENT]", results[0]["content"])
        self.assertIn("Cedar Analytics", results[0]["content"])
        self.assertEqual(results[0]["subject"], "self")
        self.assertEqual(results[0]["predicate"], "work")
        self.assertEqual(results[0]["role"], "user")
        self.assertNotIn("Tamsin", str(results))
        for evidence in results:
            if evidence["id"].startswith(("change_", "fact_")):
                self.assertEqual(evidence["predicate"], "work")
                self.assertEqual(evidence["role"], "user")

    def test_current_and_historical_work_evidence_retains_user_role_after_correction(self):
        self._employment_history_with_other_speaker()
        results = self.memory.search("target", "What was my original employer compared with the current one?", 100)
        changes = [evidence for evidence in results if evidence["id"].startswith("change_")]
        self.assertTrue(any("[HISTORICAL]" in evidence["content"] and "Bracken Analytics" in evidence["content"] for evidence in changes))
        self.assertTrue(any("[CURRENT]" in evidence["content"] and "Cedar Analytics" in evidence["content"] for evidence in changes))
        self.assertTrue(all(evidence["role"] == "user" and evidence["predicate"] == "work" for evidence in changes))
        self.assertFalse(any("Dune Robotics" in evidence["content"] for evidence in changes))

    def _add_schema_and_numeric_block(self, count=8):
        header = "Vortex experiment columns: sample | load_kg | force_N | temperature_C"
        self.add(header, score=.99)
        texts = [f"S{index:03d} | {index + 1}.50 | {index * 3 + 2}.25 | 20.0" for index in range(count)]
        for text in texts:
            self.add(text, score=.4)
        return header, texts

    def test_schema_context_keeps_contiguous_numeric_sources_without_query_nouns(self):
        header, texts = self._add_schema_and_numeric_block(47)
        results = self.memory.search("target", "Infer the load-to-force relationship across Vortex experiments.", 100)
        raw_ids = {result["source_rid"] for result in results if result["id"].startswith("mem_")}
        with self.memory._connect() as conn:
            expected = {row["rid"] for row in conn.execute("SELECT rid FROM items WHERE user_id='target'")}
        self.assertEqual(raw_ids, expected)
        self.assertTrue(any(header in result["content"] for result in results))

    def test_schema_numeric_context_stops_at_non_data_topic_boundary(self):
        _, texts = self._add_schema_and_numeric_block()
        self.add("Unrelated personal ledger starts here.", score=.1)
        unrelated = "Z999 | 9000 | 8000 | 7000"
        self.add(unrelated, score=.1)
        results = self.memory.search("target", "Summarize the Vortex experiment measurements.", 100)
        self.assertFalse(any(unrelated in result["content"] for result in results))
        raw_ids = {result["source_rid"] for result in results if result["id"].startswith("mem_")}
        self.assertEqual(raw_ids, set(range(1, len(texts) + 2)))

    def test_schema_numeric_context_does_not_cross_speaker_boundary(self):
        _, texts = self._add_schema_and_numeric_block()
        other = "A999 | 9000 | 8000 | 7000"
        self.memory.vectors[other] = [.1,math.sqrt(.99),0,0]
        self.memory.add_messages("target", "synthetic", [dict(role="assistant", content=other, timestamp=1735732800000)])
        resumed = "S777 | 999 | 888 | 777"
        self.add(resumed, score=.1)
        results = self.memory.search("target", "Summarize the Vortex experiment measurements.", 100)
        self.assertFalse(any(other in result["content"] or resumed in result["content"] for result in results))
        raw_ids = {result["source_rid"] for result in results if result["id"].startswith("mem_")}
        self.assertEqual(raw_ids, set(range(1, len(texts) + 2)))

    def test_narrow_project_context_uses_selected_units_within_one_mixed_source(self):
        self.add("Cedar deployment adopted the basalt prototype. My private home address is 14 Synthesist Lane.", score=.99)
        results = self.memory.search("target", "Recap only the Cedar deployment choices.", 100)
        self.assertIn("basalt prototype", str(results))
        self.assertNotIn("14 Synthesist Lane", str(results))

    def test_unparsed_explicit_attribute_correction_is_retained_without_crossing_speakers(self):
        self.add("My name is Rowan.", score=.99)
        other = "Correction: I legally changed my name to Wrong-Speaker."
        self.memory.vectors[other] = [.995,math.sqrt(1-.995**2),0,0]
        self.memory.add_messages("target", "synthetic", [dict(role="assistant", content=other, timestamp=1735732800000)])
        correction = "Correction: I legally changed my name to Sable."
        self.add(correction, score=.4)
        results = self.memory.search("target", "What is my name?", 100)
        self.assertTrue(results[0].get("explicit_correction"))
        self.assertEqual(results[0]["role"], "user")
        self.assertTrue(results[0]["content"].endswith(correction))
        self.assertNotIn("Wrong-Speaker", str(results))
        earlier = [row for row in results if row["id"].startswith("fact_") and "Rowan" in row["content"]]
        self.assertTrue(earlier)
        self.assertTrue(all("later explicit correction" in row["content"] for row in earlier))

    def test_personal_possessive_does_not_turn_third_person_name_into_self_name(self):
        self.add("My name is Ada.", score=.2)
        third = "My doctor's name is Dr Rana."
        self.add(third, score=.99)
        query = "What's my doctor's name?"
        self.assertIsNone(store._single_personal_attribute(query))
        result = self.memory.search("target", query, 100)[0]
        self.assertTrue(result["content"].endswith(third))

    def test_personal_possessive_does_not_turn_relative_employer_into_self_work(self):
        self.add("I work at Bracken Analytics.", score=.2)
        third = "My sister works at Harbor Lab."
        self.add(third, score=.99)
        query = "What is my sister's employer?"
        self.assertIsNone(store._single_personal_attribute(query))
        result = self.memory.search("target", query, 100)[0]
        self.assertTrue(result["content"].endswith(third))


if __name__ == "__main__":
    unittest.main()
