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

    def test_declared_markdown_alignment_is_literal_schema_not_an_observation(self):
        for header, alignment, record in (
                ('| sample | outcome | climate |', '| --- | --- | --- |', '| K001 | pass | dry |'),
                ('| sample | outcome | climate |', '| :--- | :---: | ---: |', '| K001 | pass | dry |'),
                ('sample | outcome | climate', '--- | :---: | ---:', 'K001 | pass | dry')):
            with self.subTest(alignment=alignment):
                units, groups = self.make([['Sorrel observation columns:', header, alignment]] + [[record] for _ in range(8)])
                original = copy.deepcopy(units)
                extra, schema = D.expand(units, {0})
                self.assertTrue({group[0] for group in groups[1:]} <= extra)
                self.assertEqual(schema, set(groups[0]))
                self.assertIn(alignment, [units[index]['text'] for index in schema])
                self.assertEqual(units, original)

    def test_markdown_alignment_requires_exact_width_and_three_hyphens_per_cell(self):
        for alignment in ('| --- | --- |', '| --- | note | --- |', '| --- | 3 | --- |',
                          '| -- | --- | --- |', '|| --- | --- | --- ||', '| --- | --- | --- | --- |'):
            with self.subTest(alignment=alignment):
                units, _ = self.make([['Sorrel observation columns:', '| sample | outcome | climate |', alignment]] +
                                    [['| K001 | pass | dry |'] for _ in range(5)])
                self.assertEqual(D.expand(units, {0}), (set(), set()))

    def test_markdown_alignment_requires_its_declared_header_on_previous_physical_line(self):
        for interposed in ('', 'Unrelated garden diary.', 'My private address is SECRET-ADDRESS.'):
            with self.subTest(interposed=interposed):
                units, _ = self.make([['Sorrel observation columns:', '| sample | outcome | climate |',
                    interposed, '| --- | --- | --- |']] + [['| K001 | pass | dry |'] for _ in range(5)])
                self.assertEqual(D.expand(units, {0}), (set(), set()))
        units, _ = self.make([['Sorrel observation columns:', '| sample | outcome | climate |'],
                            ['| --- | --- | --- |']] + [['| K001 | pass | dry |'] for _ in range(5)])
        self.assertEqual(D.expand(units, {0}), (set(), set()))

    def test_hidden_private_gap_before_alignment_is_not_reconnected(self):
        units, groups = self.make([['Sorrel observation columns:', '| sample | outcome | climate |',
            'My private address is SECRET-ADDRESS.', '| --- | --- | --- |']] +
            [['| K001 | pass | dry |'] for _ in range(5)])
        filtered = [unit for index, unit in enumerate(units) if index != groups[0][2]]
        self.assertEqual(D.expand(filtered, {0}), (set(), set()))

    def test_markdown_alignment_does_not_license_unheaded_or_non_pipe_tables(self):
        for header, alignment, row in (
                ('| sample | outcome | climate |', '| --- | --- | --- |', '| K001 | pass | dry |'),
                ('sample,outcome,climate', '---,---,---', 'K001,pass,dry')):
            title = 'Ordinary scratchpad:' if '|' in header else 'Sorrel observation columns:'
            units, _ = self.make([[title, header, alignment]] + [[row] for _ in range(5)])
            self.assertEqual(D.expand(units, {0}), (set(), set()))

    def test_markdown_data_still_stops_at_role_session_and_record_sequence_gaps(self):
        for boundary in ({'role':'assistant'}, {'session':'other'}, {'gap':True}):
            records = [['Sorrel observation columns:', '| sample | outcome | climate |', '| --- | --- | --- |']]
            records += [[f'| K{i:03d} | pass | dry |'] for i in range(3)]
            records += [dict(parts=['| K003 | pass | dry |'], **{k:v for k,v in boundary.items() if k!='gap'})]
            records += [[f'| K{i:03d} | pass | dry |'] for i in range(4,7)]
            units, groups = self.make(records)
            if boundary.get('gap'):
                for unit in units:
                    if unit['seq'] >= 5:unit['seq'] += 1
            extra, _ = D.expand(units, {0})
            self.assertTrue({groups[index][0] for index in (1,2,3)} <= extra)
            self.assertFalse({group[0] for group in groups[4:]} & extra)

    def test_declared_private_markdown_table_remains_withheld(self):
        for header, value, category in (
                ('name | outcome | climate', 'Invented Person | pass | dry', 'name'),
                ('phone | outcome | climate', '+1 (212) 555-0198 | pass | dry', 'phone')):
            units, _ = self.make([['Sorrel observation columns:', '| '+header+' |', '| --- | --- | --- |']] +
                                [['| '+value+' |'] for _ in range(5)])
            self.assertEqual(D.excluded_table_indexes(units, [category]), set(range(len(units))))
            if category == 'phone':self.assertEqual(D.expand(units, {0}), (set(), set()))

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

    def test_one_numeric_measurement_column_is_a_complete_declared_table(self):
        for columns, rows in (
            ('sample | outcome | exposure', [f'T{i:03d} | pass | {10+i}' for i in range(8)]),
            ('sample | pressure', [f'T{i:03d} | {10+i}' for i in range(8)]),
            ('sample | offset', [f'T{i:03d} | {-1.5+i*0.1:.2e}' for i in range(8)]),
        ):
            with self.subTest(columns=columns):
                units, groups = self.make([['Rivel experiment columns:', columns]] + [[row] for row in rows])
                original = copy.deepcopy(units)
                extra, schema = D.expand(units, {0})
                self.assertTrue({group[0] for group in groups[1:]} <= extra)
                self.assertEqual(schema, set(groups[0]))
                self.assertEqual(units, original)

    def test_categorical_outcomes_keep_each_original_observation(self):
        units, groups = self.make([['Rivel experiment columns:', 'sample | outcome | condition']] +
                                 [[f'T{i:03d} | {"pass" if i % 4 else "fail"} | cold'] for i in range(8)])
        extra, _ = D.expand(units, {0})
        self.assertTrue({group[0] for group in groups[1:]} <= extra)

    def test_named_observation_subject_attaches_to_immediate_column_declaration(self):
        for columns, rows in (
            ('sample | outcome | exposure', [f'T{i:03d} | pass | {10+i}' for i in range(8)]),
            ('sample | outcome | condition', [f'T{i:03d} | pass | cold' for i in range(8)]),
        ):
            with self.subTest(columns=columns):
                units, groups = self.make([['Rivel observations.', 'Columns:', columns]] + [[row] for row in rows])
                extra, schema = D.expand(units, {0})
                self.assertTrue({group[0] for group in groups[1:]} <= extra)
                self.assertEqual(schema, set(groups[0]))

    def test_unrelated_same_source_sentence_is_not_the_tables_subject(self):
        units, _ = self.make([['The garden plan uses gravel and seedlings.', 'Columns:',
                              'sample | outcome | condition']] +
                            [[f'T{i:03d} | pass | cold'] for i in range(8)])
        self.assertEqual(D.expand(units, {0}), (set(), set()))

    def test_hidden_gap_blocks_observation_subject_from_schema(self):
        units, _ = self.make([['Rivel observations.', 'My private address is PRIVATE-ADDRESS-382.',
                              'Columns:', 'sample | outcome | condition']] +
                            [[f'T{i:03d} | pass | cold'] for i in range(8)])
        filtered = [unit for unit in units if 'PRIVATE-ADDRESS' not in unit['text']]
        self.assertEqual(D.expand(filtered, {0}), (set(), set()))

    def test_identical_categorical_records_remain_distinct_original_rows(self):
        units, groups = self.make([['Rivel experiment columns:', 'outcome | condition']] +
                                 [['pass | cold'] for _ in range(8)])
        extra, _ = D.expand(units, {0})
        self.assertEqual(len({units[index]['source_rid'] for index in extra if index not in groups[0]}), 8)

    def test_categorical_footer_declaration_closes_preceding_rows(self):
        units, groups = self.make([[f'T{i:03d} | pass | cold'] for i in range(8)] +
                                 [['Rivel experiment columns:', 'sample | outcome | condition']])
        extra, _ = D.expand(units, {groups[-1][0]})
        self.assertTrue({group[0] for group in groups[:-1]} <= extra)

    def test_categorical_table_still_requires_explicit_column_declaration(self):
        units, _ = self.make([['Rivel experiment.'], ['sample | outcome | condition']] +
                            [[f'T{i:03d} | pass | cold'] for i in range(8)])
        self.assertEqual(D.expand(units, {0}), (set(), set()))

    def test_two_categorical_rows_cannot_create_a_table(self):
        units, _ = self.make([['Rivel experiment columns:', 'sample | outcome | condition']] +
                            [[f'T{i:03d} | pass | cold'] for i in range(2)])
        self.assertEqual(D.expand(units, {0}), (set(), set()))

    def test_categorical_rows_do_not_cross_role_or_new_topic_boundary(self):
        for boundary in ({'parts': ['T003 | pass | cold'], 'role': 'assistant'},
                         ['A new gardening topic begins here.']):
            with self.subTest(boundary=boundary):
                units, groups = self.make([['Rivel experiment columns:', 'sample | outcome | condition']] +
                    [[f'T{i:03d} | pass | cold'] for i in range(3)] + [boundary] +
                    [[f'T{i:03d} | pass | cold'] for i in range(4,8)])
                extra, _ = D.expand(units, {0})
                self.assertTrue({groups[i][0] for i in (1,2,3)} <= extra)
                self.assertFalse({group[0] for group in groups[4:]} & extra)

    def test_categorical_rows_do_not_cross_removed_private_record(self):
        units, groups = self.make([['Rivel experiment columns:', 'sample | outcome | condition']] +
            [[f'T{i:03d} | pass | cold'] for i in range(3)] + [['My private address is PRIVATE-ADDRESS-382.']] +
            [[f'T{i:03d} | pass | cold'] for i in range(4,8)])
        filtered = [unit for i, unit in enumerate(units) if i not in groups[4]]
        extra, _ = D.expand(filtered, {0})
        self.assertFalse(any('T004' in filtered[index]['text'] for index in extra))

    def test_categorical_table_does_not_associate_sensitive_field_schema(self):
        units, _ = self.make([['Rivel experiment columns:', 'sample | phone | outcome']] +
                            [[f'T{i:03d} | PRIVATE-PHONE-{i} | pass'] for i in range(8)])
        self.assertEqual(D.expand(units, {0}), (set(), set()))

    def test_single_measurement_missing_cell_keeps_declared_numeric_mask(self):
        units, groups = self.make([['Rivel experiment columns:', 'sample | pressure',
                                   'Missing pressure is written NA.']] +
            [[f'T{i:03d} | {"NA" if i == 4 else str(10+i)}'] for i in range(8)])
        extra, _ = D.expand(units, {0})
        self.assertTrue({group[0] for group in groups[1:]} <= extra)

    def test_categorical_missing_marker_does_not_invent_its_column_type(self):
        units, groups = self.make([['Rivel experiment columns:', 'sample | outcome | condition',
                                   'Missing outcome is written NA.']] +
            [[f'T{i:03d} | {"NA" if i == 4 else "pass"} | cold'] for i in range(8)])
        extra, _ = D.expand(units, {0})
        self.assertFalse({group[0] for group in groups[5:]} & extra)

    def test_explicit_private_column_exclusions_withhold_original_table_indexes(self):
        for header, category, row in (
            ('name | condition | outcome', 'name', 'Invented Person | cold | pass'),
            ('passport_number | condition | outcome', 'identity', 'ZZ-907 123 | cold | pass'),
            ('government-id | condition | outcome', 'identity', 'ZZ-907 123 | cold | pass'),
            ('phone_number | condition | outcome', 'phone', '+1 (212) 555-0198 | cold | pass'),
            ('postal_address | condition | outcome', 'address', '47 Invented Fern Lane | cold | pass'),
            ('patient_diagnosis | condition | outcome', 'medical', 'invented shoulder sprain | cold | pass'),
        ):
            with self.subTest(header=header):
                units, _ = self.make([['Rivel observation columns:', header]] + [[row] for _ in range(8)])
                original = copy.deepcopy(units)
                self.assertEqual(D.excluded_table_indexes(units, [category]), set(range(len(units))))
                self.assertEqual(units, original)

    def test_one_private_table_row_is_withheld_without_a_sample_minimum(self):
        units, _ = self.make([['Rivel observation columns:', 'name | outcome'], ['Invented Person | pass']])
        self.assertEqual(D.excluded_table_indexes(units, ['name']), set(range(len(units))))

    def test_private_value_shape_and_missing_cells_cannot_end_privacy_scope(self):
        units, _ = self.make([['Rivel observation columns:', 'name | condition | outcome'],
                            [' | cold | pass'], ['My | cold | pass'], ['A' * 700 + ' | cold | pass'],
                            ['Later Person | cold | pass']])
        self.assertEqual(D.excluded_table_indexes(units, ['name']), set(range(len(units))))

    def test_quoted_csv_private_values_are_withheld_without_rewriting(self):
        units, _ = self.make([['Rivel observation columns:', 'name,condition,outcome'],
                            ['"Invented, Person",cold,pass'], ['"Another, Person",warm,fail']])
        original = copy.deepcopy(units)
        self.assertEqual(D.excluded_table_indexes(units, ['name']), set(range(len(units))))
        self.assertEqual(units, original)

    def test_numbered_private_rows_share_the_callers_source_segment_boundaries(self):
        units, _ = self.make([['Rivel observation columns:', 'name | condition | outcome']] +
                            [[f'{index}. Invented Person | cold | pass'] for index in range(1, 5)])
        self.assertEqual(D.excluded_table_indexes(units, ['name']), set(range(len(units))))
        self.assertEqual(D._segments('1. Invented Person | cold | pass\n'),
                         ['1. Invented Person | cold | pass'])

    def test_privacy_physical_rows_keep_periods_inside_csv_and_pipe_values(self):
        for header, first, following in (
            ('name,condition,outcome', '"Dr. Vera Mallow",cold,pass', '"Prof. Invented Person",warm,fail'),
            ('name | condition | outcome', 'Dr. Vera Mallow | cold | pass', 'Prof. Invented Person | warm | fail'),
        ):
            with self.subTest(header=header):
                records = [dict(rid=1, seq=1, user_id='u', role='user', session_id='bench',
                                content='Mallow observation columns:\n' + header),
                           dict(rid=2, seq=2, user_id='u', role='user', session_id='bench', content=first),
                           dict(rid=3, seq=3, user_id='u', role='user', session_id='bench', content=following)]
                original = copy.deepcopy(records)
                spans = D.excluded_source_spans(records, ['name'])
                self.assertEqual(set(spans), {1,2,3})
                self.assertEqual(spans[2], [(0, len(first))])
                self.assertEqual(spans[3], [(0, len(following))])
                self.assertEqual(records, original)

    def test_privacy_physical_blank_line_stops_before_later_anonymous_rows(self):
        source = 'Mallow observation columns:\nname | condition | outcome\nDr. Vera Mallow | cold | pass\n\nLater Person | warm | fail'
        rows = [dict(rid=1, seq=1, user_id='u', role='user', session_id='bench', content=source)]
        selected = '\n'.join(source[start:end] for start,end in D.excluded_source_spans(rows,['name'])[1])
        self.assertIn('Dr. Vera Mallow', selected)
        self.assertNotIn('Later Person', selected)

    def test_private_row_terminal_period_does_not_withhold_the_next_safe_sentence(self):
        import facts as F
        source = ('Mallow observation columns:\nname | condition | outcome\n'
                  'Dr. Vera Mallow | cold | pass.\nMallow calibration uses sealed chambers.')
        rows = [dict(rid=1, seq=1, user_id='u', role='user', session_id='bench', content=source)]
        withheld = F.excluded_table_segments(rows, ['name'])
        sentences = F._source_sentences(source)
        self.assertTrue(all(index in withheld[1] for index in range(len(sentences)-1)))
        self.assertNotIn(len(sentences)-1, withheld[1])

    def test_private_table_scope_stops_at_role_session_and_dataset_boundaries(self):
        for boundary in (
            {'parts': ['Other Person | cold | pass'], 'role': 'assistant'},
            {'parts': ['Other Person | cold | pass'], 'session': 'other'},
            ['Dataset ID DS-OTHER-53.'],
            ['A new gardening topic starts here.'],
        ):
            with self.subTest(boundary=boundary):
                units, groups = self.make([['Rivel observation columns:', 'name | condition | outcome'],
                    ['Invented Person | cold | pass'], boundary, ['Later Person | cold | pass']])
                withheld = D.excluded_table_indexes(units, ['name'])
                self.assertTrue(set(groups[0] + groups[1]) <= withheld)
                self.assertFalse(set(groups[2] + groups[3]) & withheld)

    def test_normalized_sensitive_schema_does_not_qualify_for_ordinary_table_expansion(self):
        for field in ('phone_number', 'patient_diagnosis', 'postal_address', 'passport_number', 'national-id'):
            with self.subTest(field=field):
                units, _ = self.make([['Rivel observation columns:', field + ' | condition | outcome']] +
                                    [['PRIVATE-VALUE | cold | pass'] for _ in range(8)])
                self.assertEqual(D.expand(units, {0}), (set(), set()))

    def test_technical_sample_name_is_retained_without_name_exclusion(self):
        units, _ = self.make([['Rivel observation columns:', 'sample_name | condition | outcome']] +
                            [[f'Q{i:03d} | cold | pass'] for i in range(8)])
        self.assertTrue(D.expand(units, {0})[0])
        self.assertEqual(D.excluded_table_indexes(units, []), set())
        self.assertEqual(D.excluded_table_indexes(units, ['address']), set())


if __name__ == "__main__":
    unittest.main()
