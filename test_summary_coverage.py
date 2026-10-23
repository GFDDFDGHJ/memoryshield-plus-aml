"""Invented reversible repeated-literal coverage controls; no providers."""
from collections import Counter
import re
import unittest

import facts as F


POLICY = ('For all future replies, keep the explanation concise and state an uncertainty '
          'when the available source does not support a definite conclusion.')


def unit(rid, text, **extra):
    result = dict(rid=rid, source_rid=rid, seq=rid+1, segment=0, user_id='invented',
                  session_id='history', role='user', text=text, content=text,
                  created_at=1740000000+rid*60, timestamp_origin='message.timestamp')
    result.update(extra)
    return result


def repeated_policy_fixture():
    rules = [unit(index, POLICY, context_dependency=True) for index in range(120)]
    choices = [unit(120+index, 'The '+topic+' decision uses '+choice+'.')
               for index, (topic, choice) in enumerate((
                   ('orchard', 'the shaded path'), ('harbour', 'the eastern berth'),
                   ('studio', 'a morning class'), ('library', 'a quiet alcove'),
                   ('garden', 'the shallow bed'), ('workshop', 'a hand tool'),
                   ('kitchen', 'the ceramic tray'), ('hatchery', 'the covered pond'),
                   ('courtyard', 'an early walk'), ('railway', 'a daytime ticket'),
                   ('cabin', 'a wool blanket'), ('clinic', 'a later appointment'),
                   ('school', 'a shorter lesson'), ('gallery', 'an oak frame'),
                   ('bakery', 'a sourdough starter'), ('brewery', 'a copper vessel'),
                   ('nursery', 'a white lamp'), ('theatre', 'the upper balcony'),
                   ('laboratory', 'a glass tube'), ('office', 'the remote desk')))]
    return rules+choices, [row['text'] for row in choices]


class SummaryCoverageTests(unittest.TestCase):
    def pack(self, units, budget):
        plan = {}
        blocks = F._pack_context(units, 6, budget, 'global', selection_plan=plan)
        self.assertLessEqual(sum(map(len, blocks)), budget)
        return '\n\n'.join(blocks), plan

    def test_repeated_required_rules_do_not_erase_distinct_history(self):
        units, claims = repeated_policy_fixture()
        for budget in (8000, 16000):
            with self.subTest(budget=budget):
                text, plan = self.pack(units, budget)
                self.assertTrue(all(claim in text for claim in claims))
                self.assertEqual(plan['complete_indices'], set(range(len(units))))
                self.assertIn('Rows below remain separate source occurrences', text)
                self.assertIn('not one observation', text)

    def test_full_fit_retains_original_literal_rows_and_order(self):
        units, claims = repeated_policy_fixture()
        text, plan = self.pack(units, 100000)
        self.assertEqual(plan['complete_indices'], set(range(len(units))))
        self.assertEqual(text.count(POLICY), 120)
        self.assertLess(text.index('0@1/0'), text.index('119@120/0'))
        self.assertNotIn('Literal source text for every row:', text)

    def test_compaction_preserves_every_identical_observation(self):
        literal = 'The indicator stayed amber and the operator kept the gate closed.'
        units = [unit(index, literal) for index in range(241)]
        text, plan = self.pack(units, 8000)
        pointers = re.findall(r'^(\d+)@(\d+)/0(?: \||$)', text, re.M)
        self.assertEqual(Counter(int(rid) for rid, seq in pointers), Counter(range(241)))
        self.assertEqual(len(plan['complete_indices']), 241)
        self.assertIn(literal, text)
        self.assertIn('included=241/241', text)

    def test_role_session_time_and_origin_columns_are_invertible(self):
        literal = 'Yesterday the operator kept the gate closed.'
        units = [unit(index, literal, role='user' if index%2 else 'assistant',
                      session_id='west' if index%3 else 'east',
                      timestamp_origin='message.timestamp' if index%5 else 'ingestion-clock')
                 for index in range(48)]
        text = '\n'.join(F._context_pieces(units, compact_numeric=True))
        self.assertIn('role=user', text)
        self.assertIn('role=assistant', text)
        self.assertIn('timestamp_origin_code', text)
        self.assertIn('relative_time_anchor', text)
        self.assertIn('Origin codes: M=message.timestamp, I=ingestion-clock', text)
        for index in range(48):
            line = next(line for line in text.splitlines() if line.startswith(f'{index}@{index+1}/0 |'))
            self.assertIn('session="'+units[index]['session_id']+'"', line)
            self.assertIn(' | I | none' if index%5 == 0 else ' | M | 2025-', line)

    def test_fragments_remain_ranges_of_the_same_original_segment(self):
        literal = 'The operator kept the gate closed.'
        units = [unit(index//3, literal, source_char_start=part*40,
                      source_char_end=part*40+len(literal))
                 for index, part in enumerate((0, 1, 2, 0, 1, 2))]
        text = '\n'.join(F._context_pieces(units, compact_numeric=True))
        self.assertIn('fragments with the same source pointer form one segment, not extra observations', text)
        for start in (0, 40, 80):
            self.assertEqual(text.count(f'| {start}:{start+len(literal)}'), 2)

    def test_near_duplicate_words_are_not_silently_shared(self):
        units = [unit(index, 'The gate is '+('closed.' if index%2 else 'open.')) for index in range(30)]
        text = '\n'.join(F._context_pieces(units, compact_numeric=True))
        self.assertIn('Literal source template: The gate is closed.', text)
        self.assertIn('Literal source template: The gate is open.', text)
        closed = text.index('Literal source template: The gate is closed.')
        self.assertNotIn('0@1/0', text[closed:])

    def test_distinct_required_restrictions_still_fail_closed_when_over_budget(self):
        units = [unit(index, 'Only '+('ceramic '*(index+1))+'observations are eligible.',
                      context_qualification=True) for index in range(80)]
        text, plan = self.pack(units, 8000)
        self.assertIn('not complete statistics', text)
        self.assertLessEqual(len(plan['complete_indices']), 1)

    def test_explicit_privacy_filter_precedes_repeated_literal_compression(self):
        rows = [dict(unit(index, 'My name is Neris. The Ferrite project uses a ceramic seal.'),
                     content='My name is Neris. The Ferrite project uses a ceramic seal.')
                for index in range(40)]
        units = F.context_units_for_query(rows, 'Summarize the Ferrite project without my name.')
        text, _ = self.pack(units, 8000)
        self.assertIn('ceramic seal', text)
        self.assertNotIn('Neris', text)

    def test_required_uncommon_wording_is_not_replaced_by_plain_bucket(self):
        units, _ = repeated_policy_fixture()
        restriction = 'Earlier readings were withdrawn; never use them for fitting.'
        units.insert(0, unit(777, restriction, context_qualification=True))
        text, plan = self.pack(units, 8000)
        self.assertIn(restriction, text)
        self.assertIn(0, plan['complete_indices'])
        self.assertLess(text.index(restriction), text.index('orchard decision'))

    def test_zero_slot_template_matches_existing_independent_decoder(self):
        from evaluate_extended_context import decode_visible
        units = [unit(index, 'The label reads {session} and remains unexpanded.',
                      session_id='s-'+str(index%2)) for index in range(30)]
        # A metadata column spelling in braces is literal source text. Its
        # group uses ordinary rows, rather than introducing ambiguous slots.
        units += [unit(50+index, 'The operator kept the gate closed.') for index in range(30)]
        text = '\n'.join(F._context_pieces(units, compact_numeric=True))
        witnesses = decode_visible(text, complete=True)
        self.assertEqual(Counter((item['rid'], item['literal']) for item in witnesses),
                         Counter((row['source_rid'], row['text']) for row in units))
        self.assertEqual(len(witnesses), 60)


class SummaryCoverageIntegrationTests(unittest.TestCase):
    def setUp(self):
        from test_rerank_store import ScopedDatabaseIntegrationTests
        ScopedDatabaseIntegrationTests.setUp(self)

    def tearDown(self):
        from test_rerank_store import ScopedDatabaseIntegrationTests
        ScopedDatabaseIntegrationTests.tearDown(self)

    def test_add_search_preserves_repeated_observations_and_distinct_topics(self):
        from evaluate_extended_context import decode_visible, render
        background = ('The administrative appendix described empty paper folders, ordinary shelves '
                      'and the familiar cupboards used for routine clerical storage.')
        source_by_rid = {}
        for index in range(120):
            self.memory.add_messages('invented-coverage', 'clerical-'+str(index%3), [
                dict(role='user', content=background, timestamp=1740000000000+index*60000)])
            source_by_rid[index+1] = (background, 'user')
        for index in range(20):
            literal = 'The subject '+chr(65+index)+' decision keeps the shaded entrance.'
            role = 'user' if index%2 else 'assistant'
            self.memory.add_messages('invented-coverage', 'subject-'+str(index), [
                dict(role=role, content=literal, timestamp=1740000000000+(120+index)*60000)])
            source_by_rid[121+index] = (literal, role)
        rows = self.memory.search('invented-coverage', 'Summarize across all sessions.', 100)
        text = render(rows)
        prefix = text[:8000]
        witnesses = decode_visible(prefix, complete=len(text)<=8000)
        decoded = {(item['rid'], item['literal'], item['role']) for item in witnesses}
        self.assertTrue({(rid, literal, role) for rid, (literal, role) in source_by_rid.items() if rid>120} <= decoded)
        self.assertGreaterEqual(len({rid for rid, literal, role in decoded}), 100)
        witnesses = decode_visible(text[:16000], complete=len(text)<=16000)
        decoded = {(item['rid'], item['literal'], item['role']) for item in witnesses}
        self.assertEqual(decoded, {(rid, literal, role) for rid, (literal, role) in source_by_rid.items()})
        self.assertTrue(any('included=140/140' in row['content'] for row in rows if row['id'].startswith('digest_')))


if __name__ == '__main__':
    unittest.main()
