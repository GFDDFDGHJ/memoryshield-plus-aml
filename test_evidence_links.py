"""Invented, offline tests for auditable cross-session identifier dependencies."""
from __future__ import annotations

import copy
import sqlite3
import unittest

from evidence_links import conflicting_source_ids, select_linked_sources


def source(rid, content, *, session=None, role="user", user="isolated"):
    return {"rid": rid, "user_id": user, "session_id": session or f"session-{rid}",
            "seq": rid, "role": role, "content": content, "created_at": 1748779200.125,
            "timestamp_origin": "message.timestamp", "status": "active"}


class EvidenceLinkTests(unittest.TestCase):
    def test_dataset_definition_units_and_calibration_cross_session(self):
        definition = ("Dataset ID: ds-q7m4. Schema: p is gauge pressure in kPa; t is elapsed seconds. "
                      "Valid readings require dry mode. Subtract 1.75 from p for calibration.")
        rows = [source(1, definition, session="definition"), source(2,
            "Dataset ds-q7m4 new case: p=19.25; t=8; mode=dry.", session="new-case")]
        got = select_linked_sources(rows,
            "For dataset ds-q7m4, estimate the corrected value using our earlier column convention.", {2})
        self.assertEqual([row["source_rid"] for row in got], [1])
        self.assertEqual(got[0]["content"], definition)
        self.assertIn("calibration", got[0]["dependency_types"])
        for text in ("gauge pressure in kPa", "elapsed seconds", "Valid readings require dry mode", "Subtract 1.75"):
            self.assertIn(text, got[0]["quote"])

    def test_protocol_schema_keeps_assistant_role_in_new_user_session(self):
        definition = ("Protocol ID: PR-482Q. The input pair is charge_mC,duration_ms. "
                      "Normalize duration to seconds before applying factor 0.80. Ignore trials labelled drift.")
        rows = [source(1, definition, role="assistant"), source(2,
            "Protocol PR-482Q next sample: (18.0,40.0), label=drift.", role="user")]
        got = select_linked_sources(rows,
            "Use protocol PR-482Q for the next sample with our previous interpretation and exclusions.", {2})
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["role"], "assistant")
        self.assertEqual(got[0]["content"], definition)
        self.assertEqual(got[0]["timestamp_origin"], "message.timestamp")
        self.assertEqual(got[0]["created_at"], 1748779200.125)
        for text in ("charge_mC,duration_ms", "Normalize duration to seconds", "Ignore trials labelled drift"):
            self.assertIn(text, got[0]["quote"])

    def test_revocation_is_visible_with_definition_without_overwriting_either(self):
        rows = [source(1, "Dataset ID: ds-z91. Schema: a is thermal power in watts; b is elapsed milliseconds. Divide b by 1000 before comparing rates."),
                source(2, "Dataset ds-z91: Previous readings are withdrawn due to probe saturation; do not use them for fitting."),
                source(3, "Dataset ds-z91 new case: a=76.4; b=2500.")]
        got = select_linked_sources(rows,
            "Dataset ds-z91: assess the new case with the interpretation and eligibility restrictions we established before.", {3})
        self.assertEqual([row["source_rid"] for row in got], [2, 1])
        self.assertEqual([row["content"] for row in got], [rows[1]["content"], rows[0]["content"]])
        self.assertEqual(got[0]["dependency_types"][0], "revocation")
        quote = "\n".join(row["quote"] for row in got)
        for text in ("thermal power in watts", "elapsed milliseconds", "Divide b by 1000", "Previous readings are withdrawn", "do not use them for fitting"):
            self.assertIn(text, quote)

    def test_link_reason_points_to_literal_query_seed_and_source_spans(self):
        rows = [source(1, "Dataset ID: DS-817. Units are kPa."), source(2, "Dataset DS-817 new reading 2.51.")]
        query = "Apply dataset DS-817 to the new reading."
        row = select_linked_sources(rows, query, {2})[0]
        link = row["evidence_links"][0]
        self.assertEqual(link["reason"], "same-explicit-identifier")
        self.assertEqual(link["identifier_kind"], "dataset")
        self.assertEqual(link["traversal"], "single-hop")
        self.assertEqual(link["seed_sources"][0]["source_rid"], 2)
        for start, end in link["source_spans"]:
            self.assertEqual(row["content"][start:end], "DS-817")
        for start, end in link["query_spans"]:
            self.assertEqual(query[start:end], "DS-817")
        for start, end in link["seed_sources"][0]["spans"]:
            self.assertEqual(rows[1]["content"][start:end], "DS-817")

    def test_chinese_id_label_and_conditions_are_linked(self):
        rows = [source(1, "数据集编号：CN-572。字段定义：u 的单位为毫秒；有效条件为干燥模式。"),
                source(2, "数据集CN-572新观测：u=271.5。")]
        got = select_linked_sources(rows, "请按数据集CN-572先前的约定处理本次观测。", {2})
        self.assertEqual([row["source_rid"] for row in got], [1])
        self.assertEqual(got[0]["content"], rows[0]["content"])

    def test_french_explicit_identifier_label(self):
        rows = [source(1, "Protocole ID: FR-381. Schema: v is in litres. Conversion divides v by 1000."),
                source(2, "Protocole FR-381 prochain cas: v=917.0.")]
        self.assertEqual(len(select_linked_sources(rows, "Appliquer le protocole FR-381 au prochain cas.", {2})), 1)

    def test_explicit_all_letter_structured_ids_do_not_require_digits(self):
        for value in ("ds-MALLOW-JF", "PR-L", "PR-WREN-TK"):
            with self.subTest(value=value):
                rows = [source(1, f"Protocol ID: {value}. Schema: v is in litres."),
                        source(2, f"Protocol {value} new case: v=7.3.")]
                self.assertEqual(len(select_linked_sources(rows, f"Apply protocol {value} to this case.", {2})), 1)

    def test_plain_acronym_is_only_linked_with_explicit_type_not_as_person_name(self):
        rows = [source(1, "Protocol BRAID. Schema: v is in litres."),
                source(2, "BRAID has a private health condition."),
                source(3, "Protocol BRAID new case: v=7.3.")]
        got = select_linked_sources(rows, "Apply protocol BRAID to this case.", {3})
        self.assertEqual([row["source_rid"] for row in got], [1])
        self.assertEqual(conflicting_source_ids([source(4, "For protocol OTHER, divide v by 9.")], "Use protocol BRAID."), {4})

    def test_explicit_underscore_id_field(self):
        rows = [source(1, "dataset_id='ab-713'. Schema: q is in seconds."),
                source(2, "dataset_id=ab-713; q=8.7")]
        self.assertEqual(len(select_linked_sources(rows, "Use dataset_id=ab-713 for this sample.", {2})), 1)

    def test_bare_known_complex_id_can_reference_a_typed_domain(self):
        rows = [source(1, "RX-181: calibration subtracts 0.72 from each reading."),
                source(2, "Dataset ID: RX-181. New sample 8.22.")]
        got = select_linked_sources(rows, "Apply RX-181 to the new sample.", {2})
        self.assertEqual([row["source_rid"] for row in got], [1])
        self.assertEqual(got[0]["evidence_links"][0]["source_match"], "known-literal-identifier")

    def test_bare_id_at_sentence_end_is_not_missed(self):
        rows = [source(1, "Calibration subtracts 0.72 for RX-181."),
                source(2, "Dataset ID: RX-181. New sample 8.22.")]
        self.assertEqual(len(select_linked_sources(rows, "Apply RX-181.", {2})), 1)

    def test_identifier_prefixes_and_substrings_never_link(self):
        rows = [source(1, "Dataset DS-470. Schema: pressure is in bar."),
                source(2, "Dataset DS-47-B. Schema: pressure is in torr."),
                source(3, "Dataset DS-47. New reading 8.22.")]
        self.assertEqual(select_linked_sources(rows, "Use dataset DS-47 for this sample.", {3}), [])

    def test_identifier_value_case_is_not_silently_normalized(self):
        rows = [source(1, "Dataset ID: DS-781. Schema: pressure is in bar."),
                source(2, "Dataset ID: ds-781. New reading 8.22.")]
        self.assertEqual(select_linked_sources(rows, "Use dataset ds-781 for this sample.", {2}), [])

    def test_same_literal_with_different_domain_type_does_not_merge(self):
        rows = [source(1, "Protocol ID: SH-731. Schema: pressure is in bar."),
                source(2, "Dataset ID: SH-731. New reading 8.22."),
                source(3, "SH-731: calibration subtracts 4.0."),
                source(4, "Dataset ID: SH-731. Schema: pressure is in pascals.")]
        got = select_linked_sources(rows, "Use dataset SH-731 for this sample.", {2})
        self.assertEqual([row["source_rid"] for row in got], [4])

    def test_same_person_name_cannot_become_a_rule_domain(self):
        rows = [source(1, "Rowan has a chronic health condition and private clinical code Q-878."),
                source(2, "My colleague Rowan sent the new experiment.")]
        self.assertEqual(select_linked_sources(rows, "What conditions apply to Rowan's experiment?", {2}), [])

    def test_plain_alphabetic_id_requires_explicit_typed_label_on_each_side(self):
        rows = [source(1, "Project ID: Rowan. Schema: field t is in seconds."),
                source(2, "Rowan's private medical condition is confidential."),
                source(3, "Project ID: Rowan. New sample t=7.2.")]
        got = select_linked_sources(rows, "Use Project ID: Rowan for this sample.", {3})
        self.assertEqual([row["source_rid"] for row in got], [1])

    def test_generic_shared_words_and_pronouns_do_not_link(self):
        rows = [source(1, "The protocol schema says only dry samples are valid."),
                source(2, "Use the protocol for this new sample. It has value 8.9.")]
        self.assertEqual(select_linked_sources(rows, "Apply it using the earlier protocol schema.", {2}), [])

    def test_no_query_identifier_or_no_seed_cannot_open_another_domain(self):
        rows = [source(1, "Dataset DS-872. Units are kPa."), source(2, "Dataset DS-872 new reading 4.5.")]
        self.assertEqual(select_linked_sources(rows, "Apply the earlier convention.", {2}), [])
        self.assertEqual(select_linked_sources(rows, "Apply dataset DS-872.", set()), [])
        self.assertEqual(select_linked_sources(rows, "Apply dataset DS-872.", {999}), [])

    def test_query_id_not_present_in_seed_is_not_followed(self):
        rows = [source(1, "Dataset DS-872. Units are kPa."), source(2, "Dataset DS-873 new reading 4.5.")]
        self.assertEqual(select_linked_sources(rows, "Apply dataset DS-872.", {2}), [])

    def test_newly_discovered_identifier_does_not_expand_transitively(self):
        rows = [source(1, "Project PR-882 uses dataset DS-993. Definition: reports use a common schema."),
                source(2, "Dataset DS-993. Calibration subtracts 8.0."),
                source(3, "Project PR-882 new report: 41.2.")]
        got = select_linked_sources(rows, "Use project PR-882 for the report.", {3})
        self.assertEqual([row["source_rid"] for row in got], [1])

    def test_same_id_without_background_evidence_does_not_copy_biography(self):
        rows = [source(1, "Dataset DS-882. Rowan lives at 92 Imaginary Cedar Lane."),
                source(2, "Dataset DS-882 new reading: 2.9.")]
        self.assertEqual(select_linked_sources(rows, "Use dataset DS-882 for the sample.", {2}), [])

    def test_same_source_mixed_private_body_is_not_part_of_safe_quote(self):
        background = ("Dataset DS-882. Schema: p is gauge pressure in kPa. Only dry readings are valid. "
                      "My home address is 92 Imaginary Second Street. My medical condition is confidential.")
        rows = [source(1, background), source(2, "Dataset DS-882 new reading: p=2.9.")]
        got = select_linked_sources(rows, "Use dataset DS-882 for the new reading.", {2})
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["content"], background)
        self.assertIn("gauge pressure in kPa", got[0]["quote"])
        self.assertIn("Only dry readings are valid", got[0]["quote"])
        self.assertNotIn("Second Street", got[0]["quote"])
        self.assertNotIn("medical condition", got[0]["quote"])
        self.assertEqual(got[0]["quote"], "\n".join(
            background[start:end] for start,end in got[0]["quote_spans"]))

    def test_another_explicit_id_ends_the_background_block(self):
        rows = [source(1, "Dataset DS-882. Units are kPa. Dataset DS-883. Units are bar. Calibration subtracts 99."),
                source(2, "Dataset DS-882 new reading: p=2.9.")]
        got = select_linked_sources(rows, "Use dataset DS-882 for the new reading.", {2})
        self.assertEqual(len(got), 1)
        self.assertIn("kPa", got[0]["quote"])
        self.assertNotIn("DS-883", got[0]["quote"])
        self.assertNotIn("subtracts 99", got[0]["quote"])

    def test_qualification_and_reversal_chain_remains_complete(self):
        rows = [source(1, "Dataset DS-882. Schema: p is in kPa. Only dry readings are valid. "
            "However, wet mode is allowed after operator approval. Approval has not been granted. "
            "Otherwise, exclude the reading."), source(2, "Dataset DS-882 new reading: p=2.9.")]
        quote = select_linked_sources(rows, "Use dataset DS-882 for the new reading.", {2})[0]["quote"]
        for text in ("Only dry readings are valid", "after operator approval", "Approval has not been granted", "exclude the reading"):
            self.assertIn(text, quote)

    def test_declarative_operating_conditions_and_prohibitions_stay_with_schema(self):
        for condition in ("The declared operating condition requires low-vibration mode.",
                          "An eligibility requirement excludes unsealed runs.",
                          "Validity criteria require a stable baseline.",
                          "The environmental precondition requires a sealed enclosure."):
            with self.subTest(condition=condition):
                prohibition = "Never invent extra observations from repeated values."
                rows = [source(1, "Dataset ID: ds-IRIS-RM. Field reading is raw pressure in kilopascals. "
                    + condition + " " + prohibition + " My private home address is 37 Imaginary Road. "
                    "My medical condition is confidential."),
                    source(2, "Dataset ds-IRIS-RM new reading: 6.25.")]
                quote = select_linked_sources(rows, "Apply dataset ds-IRIS-RM to this case.", {2})[0]["quote"]
                self.assertIn("Field reading is raw pressure in kilopascals", quote)
                self.assertIn(condition, quote)
                self.assertIn(prohibition, quote)
                self.assertNotIn("Imaginary Road", quote)
                self.assertNotIn("medical condition", quote)

    def test_chinese_prohibition_continues_an_explicit_schema_background(self):
        rows = [source(1, "数据集编号：CN-572。字段定义：u 的单位为毫秒。运行条件要求密封环境。禁止根据重复数值添加额外观测。我的住址是虚构道路37号。"),
                source(2, "数据集CN-572新观测：u=271.5。")]
        quote = select_linked_sources(rows, "请按数据集CN-572先前的约定处理本次观测。", {2})[0]["quote"]
        self.assertIn("运行条件要求密封环境", quote)
        self.assertIn("禁止根据重复数值添加额外观测", quote)
        self.assertNotIn("虚构道路", quote)

    def test_schema_and_contiguous_measurement_rows_keep_literal_precision(self):
        rows = [source(1, "Dataset DS-882. Schema: fields p,t are pressure and time.\np | t\n1.250 | 2.500\n-3.750 | 4.625\nMy name is Rowan."),
                source(2, "Dataset DS-882 new reading: p=2.9.")]
        quote = select_linked_sources(rows, "Use dataset DS-882 for the new reading.", {2})[0]["quote"]
        self.assertIn("p | t", quote)
        self.assertIn("1.250 | 2.500", quote)
        self.assertIn("-3.750 | 4.625", quote)
        self.assertNotIn("Rowan", quote)

    def test_input_metadata_and_original_text_are_not_modified(self):
        rows = [source(1, "Dataset DS-771. Units are kPa."), source(2, "Dataset DS-771 new reading: 2.9.")]
        original = copy.deepcopy(rows)
        got = select_linked_sources(rows, "Use dataset DS-771.", {2})
        self.assertEqual(rows, original)
        self.assertEqual(got[0]["content"], original[0]["content"])
        self.assertEqual(got[0]["session_id"], original[0]["session_id"])
        got[0]["role"] = "assistant"
        self.assertEqual(rows, original)

    def test_same_original_text_from_different_sources_and_roles_remains_distinct(self):
        text = "Dataset DS-771. Units are kPa."
        rows = [source(1, text, role="user"), source(2, text, role="assistant"),
                source(3, "Dataset DS-771 new reading: 2.9.")]
        got = select_linked_sources(rows, "Use dataset DS-771.", {3})
        self.assertEqual([row["source_rid"] for row in got], [1, 2])
        self.assertEqual([row["role"] for row in got], ["user", "assistant"])

    def test_whole_source_character_and_count_budgets(self):
        rows = [source(1, "Dataset DS-771. Schema: " + "a" * 300),
                source(2, "Dataset DS-771. Units are kPa."),
                source(3, "Dataset DS-771. Calibration subtracts 1.5."),
                source(4, "Dataset DS-771 new reading: 2.9.")]
        got = select_linked_sources(rows, "Use dataset DS-771.", {4}, max_sources=1, max_chars=100)
        self.assertEqual([row["source_rid"] for row in got], [2])
        self.assertLessEqual(sum(len(row["content"]) for row in got), 100)
        self.assertEqual(select_linked_sources(rows, "Use dataset DS-771.", {4}, max_chars=0), [])
        self.assertEqual(select_linked_sources(rows, "Use dataset DS-771.", {4}, max_sources=0), [])

    def test_inactive_sources_are_not_reintroduced(self):
        rows = [source(1, "Dataset DS-771. Units are kPa."), source(2, "Dataset DS-771 new reading: 2.9.")]
        rows[0]["status"] = "deleted"
        self.assertEqual(select_linked_sources(rows, "Use dataset DS-771.", {2}), [])

    def test_mixed_user_inputs_fail_closed(self):
        rows = [source(1, "Dataset DS-771. Units are kPa.", user="alice"),
                source(2, "Dataset DS-771 new reading: 2.9.", user="bob")]
        with self.assertRaises(ValueError):
            select_linked_sources(rows, "Use dataset DS-771.", {2})

    def test_sqlite_row_and_source_rid_input_compatibility(self):
        with sqlite3.connect(":memory:") as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("CREATE TABLE x(source_rid,session_id,role,seq,content)")
            conn.executemany("INSERT INTO x VALUES(?,?,?,?,?)", [(1,"a","assistant",1,"Dataset DS-771. Units are kPa."),
                (2,"b","user",2,"Dataset DS-771 new reading: 2.9.")])
            got = select_linked_sources(conn.execute("SELECT * FROM x").fetchall(), "Use dataset DS-771.", {2})
            self.assertEqual([row["source_rid"] for row in got], [1])
            self.assertEqual(got[0]["role"], "assistant")

    def test_conflict_excludes_only_clear_different_domain_headers(self):
        rows = [source(1, "Protocol PR-Q: Schema: p is in bar."),
                source(2, "For protocol PR-Q, multiply p by 9."),
                source(3, "Protocol PR-L: Schema: p is in kPa."),
                source(4, "The previous discussion mentioned protocol PR-Q as background."),
                source(5, "An unlabelled causal observation explains the result.")]
        self.assertEqual(conflicting_source_ids(rows, "Use protocol PR-L for the new case."), {1, 2})

    def test_conflict_keeps_negation_revocation_and_relation_bridges(self):
        rows = [source(1, "Do not use protocol PR-Q; instead follow the current protocol."),
                source(2, "Protocol PR-Q: Previous readings are withdrawn; do not use them."),
                source(3, "Protocol PR-Q references dataset DS-MALLOW-JF for calibration."),
                source(4, "Protocol PR-Q: Compare its units against PR-L."),
                source(5, "Protocol PR-Q is not applicable to this new case.")]
        self.assertEqual(conflicting_source_ids(rows, "Use protocol PR-L for the new case."), set())

    def test_conflict_comparison_can_request_multiple_ids(self):
        rows = [source(1, "Protocol PR-Q: Schema: p is in bar."),
                source(2, "Protocol PR-L: Schema: p is in kPa."),
                source(3, "Protocol PR-M: Schema: p is in torr.")]
        self.assertEqual(conflicting_source_ids(rows, "Compare protocol PR-L with protocol PR-Q."), {3})

    def test_conflict_never_excludes_missing_id_or_different_kind(self):
        rows = [source(1, "Project PR-Q: Schema: p is in bar."),
                source(2, "This evidence has no identifier but explains a causal mechanism."),
                source(3, "Protocol PR-Q: Schema: p is in torr.")]
        self.assertEqual(conflicting_source_ids(rows, "What happens for the new case?"), set())
        self.assertEqual(conflicting_source_ids(rows, "Use protocol PR-L."), {3})

    def test_conflict_guards_user_isolation(self):
        with self.assertRaises(ValueError):
            conflicting_source_ids([source(1, "Protocol PR-Q: Units are bar.", user="alice"),
                                    source(2, "Protocol PR-L: Units are kPa.", user="bob")], "Use protocol PR-L.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
