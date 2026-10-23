"""Invented literal list boundaries; no provider or production memories."""
import copy
import unittest

import source_dependencies as D


PROCEDURE = (
    'Mallow controller procedure: execute the steps once in written order.\n'
    '1. If latch is closed, add 10 to counter.\n'
    '2. If counter is zero, close the latch.\n'
    '3. If latch is closed, add 1 to counter.\n'
    'Do not return to a skipped step.\n'
)
LONG_PROCEDURE = (
    'Mallow release decision notebook records ordinary empty drawers. '*95 + '\n'
    'Mallow controller procedure: execute the steps once in written order.\n'
    '1. Set the hinge to open.\n'
    '2. If counter is zero, close the hinge.\n'
    '3. If hinge is closed, output the Mallow release decision.\n'
    'Do not return to a skipped step.\n'
)
LONG_LITERALS = (
    '1. Set the hinge to open.',
    '2. If counter is zero, close the hinge.',
    '3. If hinge is closed, output the Mallow release decision.',
    'Do not return to a skipped step.',
)


def units(text):
    return [text[start:end].strip() for start, end in D.source_sentence_spans(text)]


class NumberedRuleBlockTests(unittest.TestCase):
    def test_same_line_numbers_attach_without_changing_source_offsets(self):
        spans = D.source_sentence_spans(PROCEDURE)
        selected = units(PROCEDURE)
        self.assertEqual(len(selected), 5)
        for ordinal in range(1, 4):
            self.assertTrue(selected[ordinal].startswith(str(ordinal)+'. If '))
        for start, end in spans:
            self.assertEqual(PROCEDURE[start:end], PROCEDURE[slice(start, end)])

    def test_decimals_terminal_numbers_and_inline_ordinals_stay_separate(self):
        text = ('The reading is 12.5 kPa. Next reading is 4.\n'
                '1. If reading exceeds 3.5, subtract 0.25.\n'
                'The label is 2. The note says 3. ordinary prose.')
        self.assertEqual(units(text), [
            'The reading is 12.5 kPa.', 'Next reading is 4.',
            '1. If reading exceeds 3.5, subtract 0.25.',
            'The label is 2.', 'The note says 3.', 'ordinary prose.'])

    def test_an_orphan_number_does_not_jump_to_a_different_line(self):
        self.assertEqual(units('1.\nThe next line is ordinary prose.'),
                         ['1.', 'The next line is ordinary prose.'])
        self.assertEqual(units('21.   \n2. Keep the count.'), ['21.', '2. Keep the count.'])

    def test_matching_one_step_recovers_literal_heading_sequence_and_control(self):
        extra = D.numbered_rule_indexes(PROCEDURE, {2})
        self.assertEqual(extra, {0, 1, 3, 4})
        selected = '\n'.join(units(PROCEDURE)[index] for index in sorted(extra | {2}))
        for literal in ('1. If latch', '2. If counter', '3. If latch', 'once in written order',
                        'Do not return to a skipped step.'):
            self.assertIn(literal, selected)
        self.assertNotIn('counter =', selected)

    def test_character_spans_resolve_repeated_text_without_fuzzy_matching(self):
        text = ('Ordinary unrelated note.\n' + PROCEDURE + '\n'
                'Ordinary unrelated note.\n' + PROCEDURE)
        second = text.rfind('2. If counter')
        blocks = D.numbered_rule_spans(text, [(second, second+4)])
        self.assertEqual(len(blocks), 1)
        self.assertGreater(blocks[0][0], text.find(PROCEDURE))
        self.assertEqual(text[blocks[0][0]:blocks[0][1]], PROCEDURE)

    def test_parenthesized_list_and_indented_literal_continuation(self):
        text = ('Clover calculation rules:\n'
                '1) Set the amount to 4.5 credits.\n'
                '   Unless the invoice is cancelled, retain the amount.\n'
                '2) Multiply the amount by 0.75.\n'
                '3) Round the amount down to whole credits.\n')
        result = D.numbered_rule_indexes(text, {3})
        selected = '\n'.join(units(text)[index] for index in sorted(result | {3}))
        self.assertIn('1) Set the amount to 4.5 credits.', selected)
        self.assertIn('Unless the invoice is cancelled', selected)
        self.assertIn('3) Round the amount down', selected)

    def test_numbered_narrative_and_nonprocedural_heading_are_not_selected(self):
        controls = (
            'Mallow event sequence:\n1. I opened the latch.\n2. The counter was zero.\n',
            'Mallow procedure:\n1. I opened the latch.\n2. The counter was zero.\n',
            'Mallow procedure:\n1. Return Logistics opened a depot.\n2. Round tables filled the hall.\n',
            'Mallow procedure:\n1. Set of plates was blue.\n2. Add Ventures acquired a depot.\n',
            'Mallow cabinet labels:\n1. Set the amount to 4.\n2. Add 2 to amount.\n',
        )
        for text in controls:
            with self.subTest(text=text):
                self.assertEqual(D.numbered_rule_indexes(text, {1}), set())

    def test_gap_restart_and_unrelated_narration_break_the_block(self):
        controls = (
            'Clover procedure:\n1. Set the amount to 4.\n3. Add 2 to amount.\n',
            'Clover procedure:\n1. Set the amount to 4.\n1. Add 2 to amount.\n',
            'Clover procedure:\n1. Set the amount to 4.\nThe garden is green.\n2. Add 2 to amount.\n',
            'Clover procedure:\n1. Set the amount to 4.\n\n2. Add 2 to amount.\n',
        )
        for text in controls:
            with self.subTest(text=text):
                self.assertEqual(D.numbered_rule_indexes(text, {1}), set())

    def test_new_scope_is_never_joined_to_the_selected_procedure(self):
        other = ('Other procedure:\n1. Set the amount to 80.\n2. Add 10 to amount.\n')
        text = PROCEDURE + '\n' + other
        result = D.numbered_rule_indexes(text, {2})
        selected = '\n'.join(units(text)[index] for index in sorted(result | {2}))
        self.assertNotIn('Other procedure', selected)
        self.assertNotIn('80', selected)

    def test_no_anchor_no_local_source_and_invalid_intervals_select_nothing(self):
        original = copy.deepcopy(PROCEDURE)
        self.assertEqual(D.numbered_rule_indexes(PROCEDURE, set()), set())
        self.assertEqual(D.numbered_rule_indexes(PROCEDURE, {-1, 999, False}), set())
        self.assertEqual(D.numbered_rule_spans(PROCEDURE, [(True, 8), (-1, 8), (3, 99999)]), [])
        text = 'Ordinary unrelated sentence.\n' + PROCEDURE
        self.assertEqual(D.numbered_rule_spans(text, [(0, 8)]), [])
        self.assertEqual(PROCEDURE, original)

    def test_oversized_procedure_is_not_silently_made_complete(self):
        text = 'Clover procedure:\n' + ''.join(
            f'{index}. Add {index} to the counter.\n' for index in range(1, 30))
        self.assertEqual(D.numbered_rule_indexes(text, {1}), set())


class NumberedRuleIntegrationTests(unittest.TestCase):
    def test_long_preview_recovers_fixed_complete_sequence_once(self):
        from test_rerank_store import S
        preview = S._query_preview(LONG_PROCEDURE, 'Apply the Mallow release decision procedure.', 1200)
        for literal in LONG_LITERALS:
            self.assertEqual(preview.count(literal), 1)
        self.assertLess(preview.index(LONG_LITERALS[0]), preview.index(LONG_LITERALS[1]))
        self.assertLess(preview.index(LONG_LITERALS[1]), preview.index(LONG_LITERALS[2]))
        self.assertNotIn('counter =', preview)

    def test_too_small_preview_discloses_omission_instead_of_partial_new_sequence(self):
        from test_rerank_store import S
        preview = S._query_preview(LONG_PROCEDURE, 'Apply the Mallow release decision procedure.', 200)
        self.assertIn('INCOMPLETE EVIDENCE', preview)
        self.assertTrue(all(literal not in preview for literal in LONG_LITERALS[:3]))

    def test_privacy_gap_does_not_reconnect_numbered_procedure(self):
        import facts as F
        text = ('Clover procedure:\n'
                '1. Set the amount to 4.\n'
                '   My home address is 39 Invented Clover Lane.\n'
                '2. Add 2 to the amount.\n'
                '3. If amount exceeds 5, output the Clover release decision.\n')
        source_units = F._source_sentences(text)
        selected = []
        for index, part in enumerate(source_units):
            if 'My home address' not in part:
                selected.append(dict(source_rid=7, segment=index, text=part, content=text))
        anchor = next(index for index, unit in enumerate(selected) if unit['text'].startswith('3.'))
        restored = F._procedure_dependency_indexes(selected, {anchor})
        quotes = '\n'.join(selected[index]['text'] for index in restored)
        self.assertNotIn('1. Set', quotes)
        self.assertNotIn('2. Add', quotes)
        self.assertNotIn('39 Invented', quotes)

    def test_table_rows_and_decimal_values_keep_literal_schema(self):
        from test_rerank_store import S
        text = (LONG_PROCEDURE + '\n'
                'row_id | dose_mL | speed_mm_s\n'
                'T01 | 1.25 | 20.5\n'
                'T02 | 2.50 | 30.5\n')
        preview = S._query_preview(text, 'Apply the Mallow release decision procedure and show T01 dose_mL.', 1800)
        for literal in LONG_LITERALS + ('row_id | dose_mL | speed_mm_s', '1.25', '20.5'):
            self.assertIn(literal, preview)

    def test_full_search_preserves_assistant_role_rid_and_foreign_user_boundary(self):
        import test_rerank_store as T
        harness = T.ScopedDatabaseIntegrationTests()
        harness.setUp()
        try:
            harness.memory._reranker = None
            harness.add(LONG_PROCEDURE, .8, role='assistant')
            harness.add(LONG_PROCEDURE.replace('hinge', 'FOREIGN-HINGE'), .99, user='other')
            results = harness.memory.search('target', 'Apply the Mallow release decision procedure.', 1)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]['id'], 'mem_1')
            self.assertEqual(results[0]['source_rid'], 1)
            self.assertEqual(results[0]['role'], 'assistant')
            for literal in LONG_LITERALS:
                self.assertEqual(results[0]['content'].count(literal), 1)
            self.assertNotIn('FOREIGN-HINGE', results[0]['content'])
        finally:
            harness.tearDown()


if __name__ == '__main__':
    unittest.main()
