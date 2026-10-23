"""Invented technical-reference/else boundaries; no provider or database."""
import unittest

import evidence_links as L


class DataLinkBoundaryTests(unittest.TestCase):
    def linked(self, body, *, role="assistant"):
        rows = [dict(rid=10, user_id="synthetic", session_id="calibration", role=role,
                     seq=1, created_at=1735689600, content=body),
                dict(rid=11, user_id="synthetic", session_id="reading", role="user", seq=2,
                     content="Dataset DS-IBIS-62 measurement: mass=4; elapsed=8.")]
        result = L.select_linked_sources(rows, "Apply dataset DS-IBIS-62.", {11})
        return result, rows

    def test_technical_their_units_and_literal_else_are_kept(self):
        body = ("Dataset ID DS-IBIS-62. Columns are mass_g and elapsed_ms; their units are g and ms. "
                "Only the sealed configuration is eligible. Otherwise, return constant 23.")
        result, originals = self.linked(body)
        self.assertEqual(len(result), 1)
        quote = result[0]["quote"]
        for literal in ("their units are g and ms", "Only the sealed configuration is eligible", "Otherwise, return constant 23"):
            self.assertIn(literal, quote)
        self.assertEqual(result[0]["role"], "assistant")
        self.assertEqual(result[0]["session_id"], "calibration")
        self.assertEqual(result[0]["content"], body)
        self.assertEqual(originals[0]["content"], body)
        self.assertEqual(quote, "\n".join(body[start:end] for start, end in result[0]["quote_spans"]))

    def test_otherwise_does_not_need_repeated_topic_words(self):
        result, _ = self.linked("Dataset ID DS-IBIS-62. Schema: input is mass. Only cold samples qualify. Otherwise, write UNAVAILABLE.")
        self.assertIn("Otherwise, write UNAVAILABLE", result[0]["quote"])

    def test_chinese_else_stays_with_original_condition(self):
        result, _ = self.linked("Dataset ID DS-IBIS-62. Schema: input is mass. Only cold samples qualify. 否则，输出不可用。")
        self.assertIn("否则，输出不可用", result[0]["quote"])

    def test_their_personal_address_is_a_scope_boundary(self):
        result, _ = self.linked("Dataset ID DS-IBIS-62. Columns are mass_g and elapsed_ms. Their personal address is PRIVATE-ADDRESS-543. Only cold samples qualify.")
        self.assertNotIn("PRIVATE-ADDRESS-543", result[0]["quote"])
        self.assertNotIn("Only cold", result[0]["quote"])

    def test_their_phone_is_a_scope_boundary(self):
        result, _ = self.linked("Dataset ID DS-IBIS-62. Schema: inputs are mass and duration. Their phone is PRIVATE-PHONE-543. Otherwise, return constant 23.")
        self.assertNotIn("PRIVATE-PHONE-543", result[0]["quote"])
        self.assertNotIn("constant 23", result[0]["quote"])

    def test_technical_prefix_cannot_smuggle_embedded_contact_value(self):
        result, _ = self.linked("Dataset ID DS-IBIS-62. Columns are mass_g and elapsed_ms. Their units are g and ms, and their phone is PRIVATE-PHONE-543.")
        self.assertNotIn("PRIVATE-PHONE-543", result[0]["quote"])

    def test_else_cannot_smuggle_personal_assignment(self):
        result, _ = self.linked("Dataset ID DS-IBIS-62. Schema: input is mass. Only cold samples qualify. Otherwise, their personal address is PRIVATE-ADDRESS-543.")
        self.assertNotIn("PRIVATE-ADDRESS-543", result[0]["quote"])

    def test_bare_possessive_units_without_prior_schema_are_not_linked(self):
        result, _ = self.linked("Dataset ID DS-IBIS-62. Their units are PRIVATE-UNIT-543.")
        self.assertEqual(result, [])

    def test_new_identifier_stops_else_chain(self):
        result, _ = self.linked("Dataset ID DS-IBIS-62. Schema: input is mass. Only cold samples qualify. Dataset ID DS-IBIS-63. Otherwise, return PRIVATE-OTHER-ID-543.")
        self.assertNotIn("PRIVATE-OTHER-ID-543", result[0]["quote"])

    def test_unrelated_prose_stops_before_a_later_else(self):
        result, _ = self.linked("Dataset ID DS-IBIS-62. Schema: input is mass. Only cold samples qualify. The garden has a stone path. Otherwise, return constant 23.")
        self.assertNotIn("constant 23", result[0]["quote"])


if __name__ == "__main__":
    unittest.main()
