"""Synthetic source/prefix regressions; isolated SQLite, local vectors, no secrets."""
from __future__ import annotations

import builtins
import math
import os
from pathlib import Path
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch

os.environ.setdefault("EMBEDDING_API_KEY", "offline-placeholder")
os.environ.setdefault("AML_AUTH_TOKEN", "offline-placeholder")
_original_open = builtins.open
def _offline_open(file, *args, **kwargs):
    if ".secrets" in str(file):
        raise OSError("Synthetic tests never read credentials")
    return _original_open(file, *args, **kwargs)

with patch("builtins.open", _offline_open):
    import store
    from test_store_regressions import OfflineMemoryStore


class SourcePrefixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aml-prefix-regression-")
        self.config = patch.object(store, "CONFIG", replace(
            store.CONFIG, db_path=str(Path(self.temp.name) / "isolated.db"), embedding_dim=4))
        self.config.start()
        self.network = patch("socket.create_connection", side_effect=AssertionError("No network"))
        self.network.start()
        self.memory = OfflineMemoryStore()

    def tearDown(self):
        self.memory.close()
        self.network.stop()
        self.config.stop()
        self.temp.cleanup()

    def add(self, records, *, user="target", session="synthetic", request_id=None):
        messages = [dict(role=role, content=text, timestamp=1700000000000) for role, text in records]
        self.memory.add_messages(user, session, messages, request_id=request_id)

    def score(self, text, value):
        self.memory.vectors[text] = [value, math.sqrt(1-value*value), 0, 0]

    def search(self, query, k=100, user="target"):
        return self.memory.search(user, query, k)

    @staticmethod
    def raw(results):
        return [result for result in results if result["id"].startswith("mem_")]

    def test_every_raw_result_exposes_readable_and_machine_source(self):
        text = "The amber parcel is on the west shelf."
        self.add([("assistant", text)])
        result = self.raw(self.search("Where is the amber parcel?"))[0]
        self.assertIn("role=assistant", result["content"])
        self.assertIn('session="synthetic"', result["content"])
        self.assertEqual(result["role"], "assistant")
        self.assertEqual(result["session_id"], "synthetic")
        self.assertIsInstance(result["source_rid"], int)
        self.assertTrue(result["content"].endswith(text))

    def test_equal_text_different_roles_remains_two_sources(self):
        text = "I booked the cedar room."
        self.add([("user", text), ("assistant", text)])
        results = self.raw(self.search("Who booked the cedar room?"))
        self.assertEqual(len(results), 2)
        self.assertEqual({result["role"] for result in results}, {"user", "assistant"})
        self.assertEqual(len({result["source_rid"] for result in results}), 2)

    def test_equal_text_timestamp_and_role_preserves_observation_multiplicity(self):
        self.add([("user", "The gauge measured 2.5 kg.")] * 3)
        self.assertEqual(len(self.raw(self.search("What did the gauge measure?"))), 3)

    def test_presentation_equivalent_rows_are_not_erased(self):
        self.add([("user", "Café  opening"), ("user", "CAFE\u0301\topening")])
        self.assertEqual(len(self.raw(self.search("cafe opening"))), 2)

    def test_replayed_add_still_deduplicates_request_identity(self):
        records = [("user", "The gauge measured 2.5 kg.")]
        self.add(records, request_id="synthetic-request")
        self.add(records, request_id="synthetic-request")
        self.assertEqual(len(self.raw(self.search("What did the gauge measure?"))), 1)

    def _late_fact(self):
        exact = "Iris used access code ORBIT-8 before departure."
        records = [f"Unrelated kitchen inventory note {i:03d}." for i in range(99)] + [exact]
        for text in records:
            self.score(text, .05)
        self.score(exact, .99)
        self.add([("user", text) for text in records])
        return exact

    def test_before_fact_lookup_keeps_semantic_head_at_top100(self):
        exact = self._late_fact()
        results = self.search("Which access code did Iris use before departure?")
        self.assertEqual(results[0]["id"], self.raw(results)[0]["id"])
        self.assertTrue(results[0]["content"].endswith(exact))
        self.assertIn("ORBIT-8", "\n".join(result["content"] for result in results)[:8000])

    def test_after_fact_lookup_does_not_global_sequence_sort(self):
        exact = self._late_fact()
        self.assertTrue(self.search("Which code did Iris use after checking the desk?")[0]["content"].endswith(exact))

    def test_explicit_sequence_orders_only_local_related_group(self):
        records = [f"Unrelated inventory {i:03d}." for i in range(96)] + [
            "The crate arrived on Monday.", "The crate was inspected on Tuesday.",
            "The crate was sealed on Wednesday.", "An unrelated lunch followed."]
        for text in records:
            self.score(text, .05)
        self.score(records[97], .99)
        self.add([("user", text) for text in records])
        results = self.raw(self.search("What happened before and after the crate inspection?"))
        self.assertEqual([result["seq"] for result in results[:3]], [97, 98, 99])
        self.assertTrue(results[1]["content"].endswith(records[97]))

    def test_weak_rule_overlap_cannot_precede_exact_fact(self):
        exact = "The courier left the package inside the cedar cabinet."
        rules = ["Never leave metal tools beside the heater.",
                 "Always keep the package of seeds dry.",
                 "Always use a courier for the museum shipment.",
                 "Never leave the bicycle under a pine tree."]
        for text in rules:
            self.score(text, .05)
        self.score(exact, .99)
        self.add([("user", text) for text in rules + [exact]])
        self.assertTrue(self.search("Where did the courier leave the package?")[0]["content"].endswith(exact))

    def test_large_digests_cannot_hide_exact_lookup_prefix(self):
        exact = "Courier package identifier: QUARTZ-27."
        self.score(exact, .99)
        for session in ("inventory-a", "inventory-b"):
            texts = [f"Courier package inventory {i:03d}: " +
                     "unrelated wrapping material description " * 28 for i in range(60)]
            if session == "inventory-a":
                texts[17] = exact
            for text in texts:
                self.memory.vectors.setdefault(text, [.05, math.sqrt(1-.05**2), 0, 0])
            self.add([("user", text) for text in texts], session=session)
        results = self.search("What is the courier package identifier?")
        self.assertTrue(results[0]["content"].endswith(exact))
        self.assertIn("QUARTZ-27", "\n".join(result["content"] for result in results)[:8000])
        structured = [result for result in results if not result["id"].startswith("mem_")]
        self.assertLessEqual(sum(len(result["content"]) for result in structured), 2000)

    def test_long_turn_previews_complementary_attributes_without_losing_source(self):
        text = ("General workshop notes. " * 120 + "The parcel color is amber. " +
                "Unrelated workshop notes. " * 120 + "The parcel seal is violet. " +
                "Closing workshop notes. " * 120)
        self.add([("user", text)])
        result = self.search("What are the parcel color and parcel seal?", 1)[0]
        preview = result["content"]
        self.assertIn("amber", preview)
        self.assertIn("violet", preview)
        self.assertLess(len(result["content"]), 3400)
        self.assertIn("complete source retained", result["content"])
        with self.memory._connect() as conn:
            source = conn.execute("SELECT content FROM items WHERE rid=?", (result["source_rid"],)).fetchone()
        self.assertEqual(source["content"], text)

    def test_long_rule_excerpt_retains_decimals_negation_and_adjacent_exception(self):
        text = ("Ordinary handbook introduction. " * 130 +
                "For the amber valve, never exceed 37.5 kPa while the latch is open. "
                "Unless the emergency override is active, do not set duty above 0.75. " +
                "Ordinary handbook appendix. " * 130)
        self.add([("user", text)])
        result = self.raw(self.search("What pressure applies to the amber valve?"))[0]
        self.assertIn("never exceed 37.5 kPa while the latch is open", result["content"])
        self.assertIn("Unless the emergency override is active, do not set duty above 0.75", result["content"])

    def test_long_rule_excerpt_preserves_chained_exceptions(self):
        text = ("Workshop background notes. " * 150 +
                "Pressure limit is 238.4 kPa. "
                "However, this limit applies only under dry conditions. "
                "Otherwise, use 192.6 kPa. " + "Workshop appendix notes. " * 150)
        self.add([("user", text)])
        result = self.raw(self.search("What is the pressure limit?"))[0]
        for fragment in ("238.4 kPa", "only under dry conditions", "Otherwise, use 192.6 kPa"):
            self.assertIn(fragment, result["content"])

    def test_over_budget_exception_chain_is_disclosed_not_made_unconditional(self):
        text = ("Pressure limit is 238.4 kPa. " +
                "Otherwise, the applicable threshold depends on an additional sealed-casing condition. " * 70)
        preview = store._query_preview(text, "What is the pressure limit?")
        self.assertIn("INCOMPLETE EVIDENCE", preview)
        self.assertNotIn("Pressure limit is 238.4 kPa", preview)

    def test_narrow_summary_does_not_drop_semantically_close_multihop_bridge(self):
        records = ["Amira leads the Solstice integration program.",
                   "Solstice runs on the Kestrel platform.",
                   "Kestrel is operated by the Research Operations division."]
        for index, text in enumerate(records):
            self.score(text, .99 - index / 1000)
            self.add([("user", text)], session=f"chain-{index}")
        result = self.search("Summarize the operating division of the platform used by the program Amira leads.")
        self.assertTrue(any(records[1] in item["content"] for item in self.raw(result)))

    def test_related_explicit_correction_precedes_earlier_rule(self):
        old = "Always use brass sleeves for latex batches."
        new = "Correction for latex batches: use glass sleeves from now on. Brass sleeves are obsolete."
        self.score(old, .985)
        self.score(new, .98)
        self.add([("user", old)], session="earlier")
        self.add([("user", new)], session="later")
        result = self.search("What sleeves should I use for latex batches today?")[0]
        self.assertTrue(result["content"].endswith(new))
        self.assertTrue(result.get("explicit_correction"))

    def test_unrelated_correction_does_not_receive_recency_priority(self):
        exact = "The cedar parcel owner is Mira."
        other = "Correction: the garden latch is now blue."
        self.score(exact, .99)
        self.score(other, .985)
        self.add([("user", exact), ("user", other)])
        self.assertTrue(self.search("Who is the cedar parcel owner?")[0]["content"].endswith(exact))

    def test_current_state_single_slot_still_prefers_updated_assertion(self):
        self.score("I live in Lisbon.", .9)
        self.score("I moved to Tallinn.", .4)
        self.add([("user", "I live in Lisbon."), ("user", "I moved to Tallinn.")])
        result = self.search("Where do I currently live?", 1)[0]
        self.assertIn("[CURRENT]", result["content"])
        self.assertIn("Tallinn", result["content"])

    def test_governing_rule_single_slot_still_available(self):
        self.add([("user", "Never mix acidic and alkaline cleaning products in the same container.")])
        result = self.search("What rule applies to acidic and alkaline cleaning products?", 1)[0]
        self.assertTrue(result["id"].startswith("rule_"))
        self.assertIn("Never mix", result["content"])

    def test_global_directives_apply_to_execution_without_topic_overlap(self):
        self.add([("user", "Always include a short greeting."),
                  ("user", "Always use numbered paragraphs."),
                  ("user", "Always end with a question.")])
        results = self.search("Write an email about a train delay.")
        content = "\n".join(result["content"] for result in results[:4])
        self.assertIn("short greeting", content)
        self.assertIn("numbered paragraphs", content)
        self.assertIn("end with a question", content)

    def test_personal_profile_remains_first_for_advice(self):
        self.add([("user", "I live in Tallinn."), ("user", "I prefer quiet parks.")])
        self.assertEqual(self.search("What personal advice fits my background?")[0]["id"], "profile")

    def test_summary_digest_has_bounded_global_prefix(self):
        for session in range(19):
            self.add([("user", f"Survey note {session}: measured payload {session + 1}.5 kg.")],
                     session=f"survey-{session:02d}")
        results = self.search("Summarize all payload survey observations.")
        self.assertTrue(results[0]["id"].startswith("digest_"))
        self.assertLessEqual(sum(len(result["content"]) for result in results
                                 if result["id"].startswith("digest_")), 12000)

    def test_source_query_excerpt_never_reveals_other_user(self):
        self.add([("user", "Own parcel key: AMBER-5.")])
        self.add([("user", "Foreign parcel key: SECRET-9.")], user="other")
        self.assertNotIn("SECRET-9", str(self.search("What is the parcel key?")))

    def test_complete_structured_quote_deduplicates_its_own_raw_source(self):
        text = "If asked for a report, include the decision date."
        self.add([("user", text), ("user", "The project began in October.")])
        results = self.search("Write a report", 10)
        self.assertTrue(any(result["id"].startswith("rule_") for result in results))
        self.assertNotIn("mem_1", {result["id"] for result in results})
        self.assertIn("mem_2", {result["id"] for result in results})

    def test_partial_structured_quote_keeps_other_facts_in_raw_source(self):
        text = "Always keep report headings short. The report launch code is LANTERN-88."
        self.add([("user", text)])
        results = self.search("What is the report launch code?", 10)
        self.assertTrue(any(result["id"].startswith("rule_") for result in results))
        self.assertTrue(any(result["id"] == "mem_1" and result["content"].endswith(text)
                            for result in results))

    def test_complete_quote_does_not_erase_a_distinct_identical_source(self):
        text = "Always include the decision date in a report."
        self.add([("user", text), ("user", text)])
        results = self.search("Write a report", 10)
        represented = {result.get("source_rid") for result in results}
        self.assertEqual(represented, {1, 2})

    def test_total_context_budget_preserves_whole_result_boundaries(self):
        texts = [f"Parcel ledger entry {i:03d}: " + "ordinary packing details " * 125
                 for i in range(100)]
        self.assertTrue(all(len(text) < 3200 for text in texts))
        self.add([("user", text) for text in texts])
        results = self.search("lookup parcel ledger", 100)
        self.assertLess(len(results), 100)
        self.assertLessEqual(sum(len(result["content"]) for result in results), 32000)
        with self.memory._connect() as conn:
            for result in self.raw(results):
                original = conn.execute("SELECT content FROM items WHERE rid=?",
                                        (result["source_rid"],)).fetchone()["content"]
                self.assertTrue(result["content"].endswith(original))

    def test_summary_total_budget_keeps_all_selected_compact_digests(self):
        for index in range(100):
            text = (f"Parcel mapping milestone {index:03d}: the archive was verified. " +
                    "Ordinary context material. " * 150)
            self.add([("user", text)], session=f"mapping-{index:03d}")
        query = "Summarize all parcel mapping milestones."
        with self.memory._connect() as conn:
            expected = store.fact_layer.get_digests(conn, "target", query, 6, budget_chars=12000)
        results = self.search(query)
        actual = [result["content"] for result in results if result["id"].startswith("digest_")]
        self.assertEqual(actual, expected)
        self.assertLessEqual(sum(len(result["content"]) for result in results), 32000)


if __name__ == "__main__":
    unittest.main()
