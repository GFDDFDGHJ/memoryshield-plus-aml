"""Invented table associations and hard source boundaries; no database/model."""
import copy
import unittest

import context_data as D


class ContextDataTests(unittest.TestCase):
    def schema(self, *, missing=True, identifier="DS-HERON-41", force="force_N"):
        parts = [f"Dataset ID {identifier}.", "Heron experiment columns:",
                 f"sample | {force} | duration_ms | mode", "Force units are N.",
                 "Only dry observations are usable.", "Otherwise, discard the row."]
        if missing:
            parts.append("Missing force is written NA.")
        return parts

    def row(self, index, *, missing=False):
        return f"Q{index:03d} | {'NA' if missing else str(100+index)+'.5'} | 800 | dry"

    def make(self, records):
        units, indexes = [], []
        for ordinal, record in enumerate(records, 1):
            if isinstance(record, list):
                record = {"parts": record}
            parts = record["parts"]
            content = "\n".join(parts)
            group = []
            for segment, text in enumerate(parts):
                group.append(len(units))
                units.append(dict(source_rid=ordinal, rid=ordinal, seq=ordinal, segment=segment,
                    user_id=record.get("user", "u"), session_id=record.get("session", "bench"),
                    role=record.get("role", "user"), content=content, text=text))
            indexes.append(group)
        return units, indexes

    def test_front_schema_keeps_original_rows_and_units(self):
        units, groups = self.make([self.schema()] + [[self.row(index)] for index in range(7)])
        original = copy.deepcopy(units)
        extra, schema = D.expand(units, {groups[0][0]})
        self.assertEqual(extra, set(range(len(units))) - {0})
        self.assertEqual(schema, set(groups[0]))
        self.assertEqual(units, original)

    def test_footer_schema_in_a_separate_record(self):
        units, groups = self.make([[self.row(index)] for index in range(7)] + [self.schema()])
        extra, schema = D.expand(units, {groups[-1][0]})
        self.assertTrue(set(range(7)) <= extra)
        self.assertEqual(schema, set(groups[-1]))

    def test_footer_schema_in_the_same_original_source(self):
        units, groups = self.make([[self.row(index) for index in range(7)] + self.schema()])
        extra, schema = D.expand(units, {7})
        self.assertTrue(set(range(7)) <= extra)
        self.assertEqual(schema, set(range(7, len(units))))

    def test_middle_declared_NA_does_not_terminate_later_numeric_rows(self):
        units, groups = self.make([self.schema()] + [[self.row(index, missing=index == 3)] for index in range(9)])
        extra, _ = D.expand(units, {0})
        self.assertTrue({group[0] for group in groups[1:]} <= extra)
        self.assertIn("NA", units[groups[4][0]]["text"])

    def test_undeclared_NA_is_a_boundary(self):
        units, groups = self.make([self.schema(missing=False)] + [[self.row(index, missing=index == 3)] for index in range(9)])
        extra, _ = D.expand(units, {0})
        self.assertTrue({groups[index][0] for index in (1, 2, 3)} <= extra)
        self.assertFalse({group[0] for group in groups[4:]} & extra)

    def test_NA_does_not_retype_a_categorical_column_as_numeric(self):
        rows = [[self.row(index)] for index in range(7)]
        rows[3] = [self.row(3).replace("dry", "NA")]
        units, groups = self.make([self.schema()] + rows)
        extra, _ = D.expand(units, {0})
        self.assertNotIn(groups[-1][0], extra)

    def test_all_missing_rows_do_not_invent_a_numeric_mask(self):
        units, _ = self.make([self.schema()] + [[self.row(index, missing=True)] for index in range(4)])
        self.assertEqual(D.expand(units, {0}), (set(), set()))

    def test_new_prose_topic_stops_before_later_similar_numbers(self):
        units, groups = self.make([self.schema()] + [[self.row(index)] for index in range(3)] +
            [["The unrelated gardening plan uses gravel and seedlings."]] + [[self.row(index)] for index in range(4, 8)])
        extra, _ = D.expand(units, {0})
        self.assertTrue({groups[index][0] for index in (1, 2, 3)} <= extra)
        self.assertFalse({group[0] for group in groups[4:]} & extra)

    def test_role_switch_stops_and_does_not_resume(self):
        records = [self.schema()] + [[self.row(index)] for index in range(3)] + [
            {"parts": [self.row(3)], "role": "assistant"}] + [[self.row(index)] for index in range(4, 8)]
        units, groups = self.make(records)
        extra, _ = D.expand(units, {0})
        self.assertFalse({group[0] for group in groups[4:]} & extra)

    def test_session_switch_without_explicit_reference_stops(self):
        records = [self.schema()] + [[self.row(index)] for index in range(3)] + [
            {"parts": [self.row(index)], "session": "other"} for index in range(3, 8)]
        units, groups = self.make(records)
        extra, _ = D.expand(units, {0})
        self.assertFalse({group[0] for group in groups[4:]} & extra)

    def test_another_dataset_header_is_a_boundary(self):
        units, groups = self.make([self.schema()] + [[self.row(index)] for index in range(3)] + [
            ["Dataset ID DS-HERON-42."]] + [[self.row(index)] for index in range(4, 8)])
        extra, _ = D.expand(units, {0})
        self.assertFalse({group[0] for group in groups[4:]} & extra)

    def test_removed_whole_private_record_leaves_a_seq_boundary(self):
        units, groups = self.make([self.schema()] + [[self.row(index)] for index in range(3)] + [
            ["My private address is PRIVATE-ADDRESS-931."]] + [[self.row(index)] for index in range(4, 8)])
        hidden = set(groups[4])
        filtered = [unit for index, unit in enumerate(units) if index not in hidden]
        extra, _ = D.expand(filtered, {0})
        selected_rids = {filtered[index]["source_rid"] for index in extra}
        self.assertFalse(set(range(6, 10)) & selected_rids)

    def test_removed_private_segment_inside_one_source_remains_a_boundary(self):
        parts = self.schema() + [self.row(index) for index in range(3)] + [
            "My private address is PRIVATE-ADDRESS-931."] + [self.row(index) for index in range(4, 8)]
        units, _ = self.make([parts])
        filtered = [unit for unit in units if "PRIVATE-ADDRESS" not in unit["text"]]
        extra, _ = D.expand(filtered, {0})
        self.assertFalse(any("Q004" in filtered[index]["text"] for index in extra))

    def test_hidden_source_tail_blocks_transition_to_a_later_record(self):
        records = [self.schema()] + [[self.row(index)] for index in range(3)] + [
            [self.row(3), "My private address is PRIVATE-ADDRESS-931."]] + [[self.row(index)] for index in range(4, 8)]
        units, _ = self.make(records)
        filtered = [unit for unit in units if "PRIVATE-ADDRESS" not in unit["text"]]
        extra, _ = D.expand(filtered, {0})
        self.assertFalse(any("Q004" in filtered[index]["text"] for index in extra))

    def test_explicit_same_dataset_reference_inherits_original_schema(self):
        records = [{"parts": self.schema(), "session": "definitions"},
                   {"parts": ["Dataset ID DS-HERON-41.", "This batch uses the earlier shared schema."], "session": "batch"}] + [
                       {"parts": [self.row(index)], "session": "batch"} for index in range(7)]
        units, groups = self.make(records)
        extra, schema = D.expand(units, {groups[1][0]})
        self.assertTrue({group[0] for group in groups[2:]} <= extra)
        self.assertTrue(set(groups[0]) <= schema)
        self.assertTrue(set(groups[1]) <= schema)

    def test_same_ID_without_schema_reference_does_not_cross_sessions(self):
        records = [{"parts": self.schema(), "session": "definitions"},
                   {"parts": ["Dataset ID DS-HERON-41.", "A new batch starts here."], "session": "batch"}] + [
                       {"parts": [self.row(index)], "session": "batch"} for index in range(7)]
        units, groups = self.make(records)
        extra, _ = D.expand(units, {groups[1][0]})
        self.assertFalse({group[0] for group in groups[2:]} & extra)

    def test_different_ID_reference_does_not_borrow_schema(self):
        records = [{"parts": self.schema(), "session": "definitions"},
                   {"parts": ["Dataset ID DS-HERON-42.", "This batch uses the earlier shared schema."], "session": "batch"}] + [
                       {"parts": [self.row(index)], "session": "batch"} for index in range(7)]
        units, groups = self.make(records)
        self.assertEqual(D.expand(units, {groups[1][0]}), (set(), set()))

    def test_other_role_schema_is_not_inferred_as_authority(self):
        records = [{"parts": self.schema(), "session": "definitions", "role": "assistant"},
                   {"parts": ["Dataset ID DS-HERON-41.", "This batch uses the earlier shared schema."], "session": "batch"}] + [
                       {"parts": [self.row(index)], "session": "batch"} for index in range(7)]
        units, groups = self.make(records)
        self.assertEqual(D.expand(units, {groups[1][0]}), (set(), set()))

    def test_conflicting_old_schemas_do_not_choose_a_version(self):
        records = [{"parts": self.schema(), "session": "definitions"},
                   {"parts": self.schema(force="force_kN"), "session": "revision"},
                   {"parts": ["Dataset ID DS-HERON-41.", "This batch uses the earlier shared schema."], "session": "batch"}] + [
                       {"parts": [self.row(index)], "session": "batch"} for index in range(7)]
        units, groups = self.make(records)
        extra, _ = D.expand(units, {groups[2][0]})
        self.assertFalse({group[0] for group in groups[3:]} & extra)

    def test_reference_source_cannot_cross_its_own_private_field(self):
        records = [{"parts": self.schema(), "session": "definitions"},
                   {"parts": ["Dataset ID DS-HERON-41.", "This batch uses the earlier shared schema.",
                              "My bank account is PRIVATE-BANK-931."], "session": "batch"}] + [
                       {"parts": [self.row(index)], "session": "batch"} for index in range(7)]
        units, groups = self.make(records)
        extra, _ = D.expand(units, {groups[1][0]})
        self.assertFalse({group[0] for group in groups[2:]} & extra)

    def test_reference_source_cannot_seed_a_private_first_sentence(self):
        records = [{"parts": self.schema(), "session": "definitions"},
                   {"parts": ["My bank account is PRIVATE-BANK-931.", "Dataset ID DS-HERON-41.",
                              "This batch uses the earlier shared schema."], "session": "batch"}] + [
                       {"parts": [self.row(index)], "session": "batch"} for index in range(7)]
        units, groups = self.make(records)
        extra, schema = D.expand(units, {groups[1][1]})
        self.assertNotIn(groups[1][0], extra | schema)
        self.assertFalse({group[0] for group in groups[2:]} & extra)

    def test_hidden_personal_sentence_cannot_supply_the_schema_reference(self):
        records = [{"parts": self.schema(), "session": "definitions"},
                   {"parts": ["My bank record says use the earlier shared schema.", "Dataset ID DS-HERON-41."], "session": "batch"}] + [
                       {"parts": [self.row(index)], "session": "batch"} for index in range(7)]
        units, _ = self.make(records)
        filtered = [unit for unit in units if not unit["text"].startswith("My bank")]
        reference = next(index for index, unit in enumerate(filtered) if unit["session_id"] == "batch")
        extra, _ = D.expand(filtered, {reference})
        self.assertFalse(any("Q000" in filtered[index]["text"] for index in extra))

    def test_numeric_rows_without_declared_columns_are_not_associated(self):
        units, _ = self.make([["The laboratory was inspected."], ["sample | force_N | duration_ms | mode"]] + [
            [self.row(index)] for index in range(7)])
        self.assertEqual(D.expand(units, {0}), (set(), set()))

    def test_remote_missing_declaration_beyond_private_boundary_does_not_license_NA(self):
        parts = self.schema(missing=False) + [self.row(index, missing=index == 3) for index in range(7)] + [
            "My private address is PRIVATE-ADDRESS-931.", "Missing force is written NA."]
        units, _ = self.make([parts])
        filtered = [unit for unit in units if "PRIVATE-ADDRESS" not in unit["text"]]
        extra, _ = D.expand(filtered, {0})
        self.assertFalse(any("Q004" in filtered[index]["text"] for index in extra))

    def test_remote_dataset_ID_does_not_name_an_unscoped_schema(self):
        parts = self.schema()[1:] + ["My bank account is PRIVATE-BANK-931.", "Dataset ID DS-HERON-41."]
        records = [{"parts": parts, "session": "definitions"},
                   {"parts": ["Dataset ID DS-HERON-41.", "This batch uses the earlier shared schema."], "session": "batch"}] + [
                       {"parts": [self.row(index)], "session": "batch"} for index in range(7)]
        units, groups = self.make(records)
        extra, _ = D.expand(units, {groups[1][0]})
        self.assertFalse({group[0] for group in groups[2:]} & extra)

    def test_mixed_user_units_raise_before_association(self):
        units, _ = self.make([self.schema()] + [[self.row(index)] for index in range(3)])
        units[-1]["user_id"] = "other"
        with self.assertRaises(ValueError):
            D.expand(units, {0})


if __name__ == "__main__":
    unittest.main()
