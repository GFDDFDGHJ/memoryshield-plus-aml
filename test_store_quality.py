"""Synthetic retrieval quality checks using temporary databases and local vectors."""
from __future__ import annotations

import math
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch

import store as store_module
from test_store_regressions import DIM, OfflineMemoryStore, raw_literal


class StoreQualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="aml-quality-")
        self.config_patch = patch.object(
            store_module, "CONFIG",
            replace(store_module.CONFIG, db_path=self.temp_dir.name + "/quality.db",
                    embedding_dim=DIM, recency_weight=0.1),
        )
        self.config_patch.start()
        self.memory = OfflineMemoryStore()

    def tearDown(self) -> None:
        self.memory.close()
        self.config_patch.stop()
        self.temp_dir.cleanup()

    def add(self, texts, *, user="target", session="session", timestamp=1700000000000):
        self.memory.add_messages(user, session, [
            {"role": "user", "content": text, "timestamp": timestamp} for text in texts
        ])

    def vector(self, text: str, similarity: float) -> None:
        self.memory.vectors[text] = [similarity, math.sqrt(1 - similarity ** 2), 0, 0]

    def raw(self, query="lookup record", limit=10, user="target"):
        with self.memory._connect() as conn:
            return self.memory._search_raw(conn, user, [1, 0, 0, 0], limit, query=query)

    def test_semantic_symbols_survive_duplicate_normalization(self):
        records = ["Language: C++.", "Language: C.", "Offset: +5.", "Offset: -5.",
                   "Version: 1.5.", "Version: 15."]
        self.add(records)
        self.assertEqual({raw_literal(row) for row in self.raw()}, set(records))

    def test_unicode_case_and_whitespace_variants_preserve_distinct_sources(self):
        records = ["Café  opening", "CAFE\u0301\topening"]
        self.add(records)
        results = self.raw()
        self.assertEqual(len(results), 2)
        self.assertEqual([raw_literal(row) for row in results], records)
        self.assertEqual(len({row["source_rid"] for row in results}), 2)

    def test_identical_events_in_distinct_sessions_survive(self):
        self.add(["I ran 5 km today."], session="morning")
        self.add(["I ran 5 km today."], session="evening")
        self.assertEqual(len(self.raw()), 2)

    def test_identical_events_on_distinct_dates_survive(self):
        self.add(["I ran 5 km today."])
        self.add(["I ran 5 km today."], timestamp=1700086400000)
        results = self.raw("On which days did I run 5 km?")
        self.assertEqual(len(results), 2)
        self.assertEqual(len({row["created_at"] for row in results}), 2)

    def test_ordinary_lookup_has_no_global_age_bias(self):
        old, new = "Access code: NX-731.", "Replacement code: QZ-206."
        self.vector(old, .88)
        self.vector(new, .82)
        self.add([old, new])
        self.assertEqual(raw_literal(self.raw(limit=1)[0]), old)

    def test_original_state_is_not_replaced_by_latest(self):
        old, new = "Original code was NX-731.", "Later code became QZ-206."
        self.vector(old, .88)
        self.vector(new, .82)
        self.add([old, new])
        result = self.raw("What was the original access code?", 1)[0]
        self.assertEqual(raw_literal(result), old)

    def test_original_query_selects_first_within_the_same_topic(self):
        unrelated = "Garden access code before launch: FERN-111."
        old = "Studio access code before launch: ALPHA-9."
        new = "Studio access code after launch: BETA-7."
        for text, similarity in [(unrelated, .82), (old, .80), (new, .84)]:
            self.vector(text, similarity)
        self.add([unrelated, old, new])
        original = self.raw("What was the original studio access code?", 1)[0]
        self.assertEqual(raw_literal(original), old)
        self.assertEqual(raw_literal(self.raw("What was the studio access code?", 1)[0]), new)

    def test_original_query_does_not_promote_a_semantically_distant_record(self):
        old, new = "Studio access code before launch: ALPHA-9.", "Studio access code after launch: BETA-7."
        self.vector(old, .50)
        self.vector(new, .84)
        self.add([old, new])
        self.assertEqual(raw_literal(self.raw("What was the original studio access code?", 1)[0]), new)

    def test_single_slot_current_query_uses_explicit_updated_state(self):
        old, new = "I live in Lisbon.", "I moved to Tallinn."
        self.vector(old, .90)
        self.vector(new, .40)
        self.add([old, new])
        result = self.memory.search("target", "Where do I currently live?", 1)[0]
        self.assertTrue(result["id"].startswith("change_"))
        self.assertIn("[CURRENT]", result["content"])
        self.assertIn("Tallinn", result["content"])

    def test_current_query_can_break_only_semantic_near_ties(self):
        old, new = "Code was NX-731.", "Code is now QZ-206."
        self.vector(old, .89)
        self.vector(new, .885)
        self.add([old, new])
        self.assertEqual(raw_literal(self.raw("What is the current code?", 1)[0]), new)
        self.assertEqual(raw_literal(self.raw("What was the original code, compared with now?", 1)[0]), old)

    def test_current_query_does_not_cross_a_semantic_gap(self):
        old, new = "Code was NX-731.", "Code is now QZ-206."
        self.vector(old, .88)
        self.vector(new, .82)
        self.add([old, new])
        self.assertEqual(raw_literal(self.raw("What is the current code?", 1)[0]), old)

    def test_rule_cue_does_not_boost_an_unrelated_raw_message(self):
        relevant, unrelated = "The launch access code is NX-731.", "Never forget the lunch box."
        self.vector(relevant, .80)
        self.vector(unrelated, .79)
        self.add([relevant, unrelated])
        self.assertEqual(raw_literal(self.raw("What is the launch access code?", 1)[0]), relevant)

    def test_chronology_adds_same_session_neighbors_in_source_order(self):
        records = ["Travel planning begins.", "The flight was booked on Monday.",
                   "The hotel was confirmed on Tuesday.", "The museum was booked on Wednesday.",
                   "An unrelated lunch discussion follows."]
        for record in records:
            self.vector(record, .1)
        self.vector(records[2], .99)
        self.add(records)
        self.add(["Another user's Wednesday event."], user="other")
        results = self.raw("What happened before and after the hotel confirmation?", 3)
        self.assertEqual([raw_literal(row) for row in results], records[1:4])
        self.assertEqual([row["seq"] for row in results], [2, 3, 4])
        for row in results:
            self.assertEqual(row["session_id"], "session")
            self.assertIn("recorded_at=", row["content"])
            self.assertIn("timestamp_origin=message.timestamp", row["content"])
            self.assertIn("not event occurrence time", row["content"])

    def test_neighbor_expansion_does_not_cross_sessions(self):
        foreign = "Unrelated previous session."
        records = ["The first important task was completed.", "A follow-up happened.",
                   "Another related task was planned."]
        self.vector(foreign, .1)
        for text, similarity in zip(records, [.99, .3, .2]):
            self.vector(text, similarity)
        self.add([foreign], session="other-session")
        self.add(records, session="target-session")
        self.add(["Private other-user event."], user="other")
        results = self.raw("What came first?", 3)
        self.assertEqual([raw_literal(row) for row in results], records)
        self.assertTrue(all(row["session_id"] == "target-session" for row in results))

    def test_summary_raw_evidence_exposes_source_without_truncating(self):
        text = "A long record " * 100 + "The final conclusion is preserved."
        self.add([text])
        result = self.raw("Summarize the records", 1)[0]
        self.assertEqual(raw_literal(result), text)
        self.assertEqual(result["stored_at"], result["created_at"])

    def test_ordinary_raw_source_label_preserves_literal_content(self):
        text = "Launch key: NX-731."
        self.add([text])
        result = self.raw("What is the launch key?", 1)[0]
        self.assertEqual(raw_literal(result), text)
        self.assertEqual(result["role"], "user")
        self.assertEqual(result["session_id"], "session")

    def test_short_exact_lookup_keeps_the_raw_record_first(self):
        relevant, other = "The launch access code is NX-731.", "The lunch room is upstairs."
        self.vector(relevant, .90)
        self.vector(other, .20)
        self.add([relevant, other])
        results = self.memory.search("target", "What is the launch access code?", 2)
        # A precise lookup must lead with the exact source. Do not require
        # permanent removal of lower-ranked records: that earlier behavior
        # deleted independent causes under paraphrased multi-evidence queries.
        self.assertEqual(raw_literal(results[0]), relevant)
        self.assertTrue(all(raw_literal(row) in {relevant, other} for row in results))
        self.assertLessEqual(len(results), 2)

    def test_order_number_lookup_is_not_a_chronology_query(self):
        records = ["The restaurant menu includes soup.",
                   "The telescope order number is ZIRCON-824.",
                   "The garden beds need watering."]
        for text, similarity in zip(records, [.1, .99, .2]):
            self.vector(text, similarity)
        self.add(records)
        result = self.raw("What is the order number for the telescope?", 3)[0]
        self.assertEqual(raw_literal(result), records[1])
        self.assertEqual(result["seq"], 2)
        self.assertEqual(result["session_id"], "session")

    def test_unrelated_background_does_not_precede_a_lookup(self):
        self.add(["My name is Mira.", "I live in Oslo.", "I moved to Bergen.",
                  "Launch key: NX-731."])
        with self.memory._connect() as conn:
            self.assertTrue(store_module.fact_layer.build_profile(conn, "target"))
        results = self.memory.search("target", "What is the launch key?", 10)
        self.assertFalse(any(row["id"] == "profile" for row in results))
        self.assertFalse(any(row["id"].startswith("change_") for row in results))

    def test_small_summary_budget_keeps_all_session_groups(self):
        markers = [f"MARK_{index:02d}" for index in range(13)]
        for index, marker in enumerate(markers):
            self.add([f"Milestone {marker} was confirmed."], session=f"session-{index:02d}")
        results = self.memory.search("target", "Summarize all recorded milestones", 5)
        digest_text = "\n".join(row["content"] for row in results if row["id"].startswith("digest_"))
        self.assertTrue(digest_text)
        for marker in markers:
            self.assertIn(marker, digest_text)

    def test_limited_history_retains_first_and_current_stages(self):
        cities = ["Oslo", "Bergen", "Tromso", "Paris", "London", "Rome", "Madrid", "Lisbon"]
        self.add([f"I live in {city}." for city in cities])
        results = self.memory.search("target", "Trace the history of my location", 10)
        history = [row["content"] for row in results if row["id"].startswith("change_")]
        self.assertGreaterEqual(len(history), 2)
        self.assertIn("Oslo", history[0])
        self.assertIn("Lisbon", history[-1])
        self.assertIn("[CURRENT]", history[-1])

    def test_complete_relevant_rule_can_fill_a_single_slot(self):
        text = "If the user requests a changelog, include compatibility notes and migration steps."
        self.add([text])
        rule = {"text": text, "score": 1., "source_rid": 1, "session_id": "session",
                "seq": 1, "created_at": 1700000000., "kind": "rule"}
        with (patch.object(store_module.fact_layer, "relevant_rules", return_value=[rule]),
              patch.object(store_module.fact_layer, "change_history", return_value=[])):
            result = self.memory.search("target", "Write a changelog", 1)[0]
        self.assertTrue(result["id"].startswith("rule_"))
        self.assertIn(text, result["content"])
        self.assertEqual(result["source_rid"], 1)

    def test_partial_rule_does_not_hide_other_sentences_from_its_source(self):
        text = "Always keep report headings short. The launch code for the report is LANTERN-88."
        self.add([text])
        results = self.memory.search("target", "What is the launch code for the report?", 10)
        self.assertTrue(any(row["id"].startswith("rule_") for row in results))
        raw = [row for row in results if row["id"].startswith("mem_")]
        self.assertTrue(raw)
        self.assertIn(text, [raw_literal(row) for row in raw])

    def test_single_slot_fact_query_is_not_replaced_by_a_weakly_related_rule(self):
        rule = "Always use uppercase for garden labels."
        fact = "The uppercase protocol launch code is LANTERN-88."
        self.vector(rule, .70)
        self.vector(fact, .90)
        self.add([rule, fact])
        result = self.memory.search("target", "What is the uppercase protocol launch code?", 1)[0]
        self.assertEqual(raw_literal(result), fact)

    def test_single_slot_explicit_rule_query_requires_strong_topic_overlap(self):
        rule = "Always use uppercase for garden labels."
        fact = "The uppercase protocol launch code is LANTERN-88."
        self.vector(rule, .70)
        self.vector(fact, .90)
        self.add([rule, fact])
        result = self.memory.search("target", "Which rule governs the uppercase protocol launch code?", 1)[0]
        self.assertEqual(raw_literal(result), fact)

    def test_direct_identity_lookup_does_not_disclose_the_full_profile(self):
        self.add(["My name is Mira.", "I live in Oslo.", "I am 32 years old."])
        results = self.memory.search("target", "What is my name?", 10)
        self.assertNotIn("profile", {row["id"] for row in results})
        self.assertTrue(any("Mira" in row["content"] for row in results))

    def test_broad_background_query_keeps_the_profile_available(self):
        self.add(["My name is Mira.", "I live in Oslo.", "I am 32 years old."])
        results = self.memory.search("target", "Summarize my personal background", 10)
        self.assertIn("profile", {row["id"] for row in results})

    def test_selected_structured_source_is_not_repeated_as_raw(self):
        text = "If asked for a report, include the decision date."
        self.add([text, "The project began in October."])
        rule = {"text": text, "score": 1., "source_rid": 1, "session_id": "session",
                "seq": 1, "created_at": 1700000000., "kind": "rule"}
        with (patch.object(store_module.fact_layer, "relevant_rules", return_value=[rule]),
              patch.object(store_module.fact_layer, "change_history", return_value=[]),
              patch.object(store_module.fact_layer, "match_facts", return_value=[])):
            results = self.memory.search("target", "Write a report", 10)
        self.assertTrue(results[0]["id"].startswith("rule_"))
        self.assertNotIn("mem_1", {row["id"] for row in results})
        self.assertIn("mem_2", {row["id"] for row in results})

    def test_personal_excerpt_keeps_example_and_report_attribution_headers(self):
        for index, header in enumerate(("Example:", "Fictional example:", "The visitor said:")):
            with self.subTest(header=header):
                user = f"header-user-{index}"
                source = header + "\nMy name is Inara.\nActually, my name is Selene."
                self.add([source], user=user, session="quoted-source")
                rows = self.raw("What is my name now?", user=user)
                self.assertEqual(len(rows), 1)
                literal = raw_literal(rows[0])
                self.assertIn(header, literal)
                self.assertIn("My name is Inara.", literal)
                self.assertIn("Actually, my name is Selene.", literal)
                self.assertEqual(rows[0]["session_id"], "quoted-source")
                self.assertEqual(rows[0]["role"], "user")

    def test_personal_excerpt_retains_header_without_intervening_medical_field(self):
        source = ("My medical record is PRIVATE-OUTSIDE-HEADER.\nThe visitor said:\n"
                  "My medical record is PRIVATE-INSIDE-HEADER.\nMy name is Inara.")
        self.add([source])
        literal = raw_literal(self.raw("What is my name now?")[0])
        self.assertIn("The visitor said:", literal)
        self.assertIn("My name is Inara.", literal)
        self.assertNotIn("PRIVATE-", literal)

    def test_personal_excerpt_keeps_multiline_quote_scope_without_private_fields(self):
        source = '"\nMy medical record is PRIVATE-QUOTED-DETAIL.\nMy name is Inara.\nMy work is PRIVATE-WORK-DETAIL.\n"'
        self.add([source])
        literal = raw_literal(self.raw("What is my name now?")[0])
        self.assertIn('"\nMy name is Inara.\n"', literal)
        self.assertNotIn("PRIVATE-", literal)

    def test_personal_excerpt_does_not_attach_header_across_a_blank_line(self):
        self.add(["Example:\nThe unused example concerns supplies.\n\nMy name is Selene."])
        literal = raw_literal(self.raw("What is my name now?")[0])
        self.assertIn("My name is Selene.", literal)
        self.assertNotIn("Example:", literal)

    def test_personal_excerpt_closes_omitted_quote_before_actual_correction(self):
        source = ('The visitor said:\n"My name is Inara.\n'
                  'My medical record is PRIVATE-QUOTED-DETAIL."\n'
                  'Actually, my name is Selene.')
        self.add([source])
        result = self.raw("What is my name now?")[0]
        literal = raw_literal(result)
        self.assertIn('"My name is Inara.\n"\nActually, my name is Selene.', literal)
        self.assertIn("QUERY-FOCUSED SOURCE EXCERPTS", result["content"])
        self.assertNotIn("PRIVATE-", literal)

    def test_explicit_private_table_columns_are_withheld_before_raw_ranking(self):
        for field, header, query in (
            ('name', 'name | condition | outcome', 'Estimate Rivel success probability; omit names.'),
            ('passport', 'passport_number | condition | outcome', 'Estimate Rivel success probability; omit passport numbers.'),
            ('old-numeric', 'name | pressure | duration', 'Estimate Rivel mean pressure; omit names.'),
            ('phone', 'phone_number | condition | outcome', 'Estimate Rivel success probability; omit phone numbers.'),
            ('address', 'postal_address | condition | outcome', 'Estimate Rivel success probability; omit addresses.'),
        ):
            with self.subTest(field=field):
                schema = 'Rivel observation columns:\n' + header
                records = [f'PRIVATE-IDENTIFIER-{i:03d} | ' + (f'{10+i} | {100+i}' if field == 'old-numeric' else 'cold | pass') for i in range(121)]
                self.add([schema] + records, user=field)
                actual = self.raw(query, 100, user=field)
                self.assertNotIn('PRIVATE-IDENTIFIER', '\n'.join(row['content'] for row in actual))
                result = self.memory.search(field, query, 100)
                self.assertNotIn('PRIVATE-IDENTIFIER', '\n'.join(row['content'] for row in result))

    def test_private_table_filter_retains_safe_sentences_in_compact_source_context(self):
        source = ('Rivel valves passed the final leak inspection.\nRivel observation columns:\n'
                  'name | condition | outcome\nPRIVATE-PERSON | cold | pass\n'
                  'PRIVATE-OTHER | cold | fail\nPRIVATE-THIRD | warm | pass')
        self.add([source])
        query = 'Summarize the Rivel observations without names.'
        results = self.memory.search('target', query, 100)
        text = '\n'.join(row['content'] for row in results)
        self.assertIn('Rivel valves passed the final leak inspection.', text)
        self.assertNotIn('PRIVATE-', text)

    def test_private_table_filter_also_blocks_linked_and_structured_source_rescue(self):
        private = ('Dataset ID DS-RIVEL-91. Rivel observation columns:\nname | pressure | duration\n'
                   'PRIVATE-PERSON | 14 | 100\nPRIVATE-OTHER | 16 | 110\nPRIVATE-THIRD | 18 | 120')
        safe = 'Dataset DS-RIVEL-91 procedure: estimate pressure using the observations.'
        self.add([private, safe])
        results = self.memory.search('target', 'Estimate dataset DS-RIVEL-91 mean pressure; omit names.', 100)
        self.assertNotIn('PRIVATE-', '\n'.join(row['content'] for row in results))

    def test_private_period_cells_and_following_rows_cannot_escape_raw_or_compact_filtering(self):
        for index, (header, record, later) in enumerate((
            ('name,condition,outcome', '"Dr. Vera Mallow",cold,pass', '"Prof. Other Person",warm,fail'),
            ('name | condition | outcome', 'Dr. Vera Mallow | cold | pass', 'Prof. Other Person | warm | fail'),
        )):
            with self.subTest(header=header):
                user = f'period-private-{index}'
                self.add(['Mallow observation columns:\n'+header, record, later], user=user)
                query = 'Estimate Mallow success probability; omit names.'
                for results in (self.raw(query, 100, user=user), self.memory.search(user, query, 100)):
                    text = '\n'.join(row['content'] for row in results)
                    self.assertNotIn('Vera Mallow', text)
                    self.assertNotIn('Other Person', text)

    def test_private_period_table_keeps_safe_sentence_on_another_physical_line(self):
        source = ('Mallow valves passed final leak inspection.\nMallow observation columns:\n'
                  'name,condition,outcome\n"Dr. Vera Mallow",cold,pass\n"Prof. Other Person",warm,fail')
        self.add([source])
        results = self.memory.search('target', 'Summarize Mallow observations without names.', 100)
        text = '\n'.join(row['content'] for row in results)
        self.assertIn('Mallow valves passed final leak inspection.', text)
        self.assertNotIn('Vera Mallow', text)
        self.assertNotIn('Other Person', text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
