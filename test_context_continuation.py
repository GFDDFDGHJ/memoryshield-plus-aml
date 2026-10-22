"""Disjoint, provenance-preserving continuation checks on invented sources."""
import re
import sqlite3
import unittest

import facts as F


class ContextContinuationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        F.ensure_schema(self.conn)
        self.conn.execute('CREATE TABLE items(rid INTEGER PRIMARY KEY,user_id TEXT,session_id TEXT,role TEXT,content TEXT,created_at REAL,seq INTEGER,status TEXT,timestamp_origin TEXT)')

    def tearDown(self):
        self.conn.close()

    def add(self, content, session='archive', role='user', origin='message.timestamp', user='u'):
        seq = self.conn.execute('SELECT count(*)+1 FROM items').fetchone()[0]
        rid = self.conn.execute("INSERT INTO items VALUES(NULL,?,?,?,?,?,?,'active',?)",
                                (user, session, role, content, 1750000000+seq*3600, seq, origin)).lastrowid
        F.store_facts(self.conn, user, session, rid, content, 1750000000+seq*3600, seq, role)
        return rid

    def views(self, query, prefix=12000, continuation=64000, limit=2):
        first, later = F.get_context_views(self.conn, 'u', query, limit, budget_chars=prefix,
                                           continuation_budget_chars=continuation)
        self.assertEqual(first, F.get_digests(self.conn, 'u', query, limit, budget_chars=prefix))
        self.assertLessEqual(sum(map(len, first)), prefix)
        self.assertLessEqual(sum(map(len, later)), continuation)
        return '\n'.join(first), '\n'.join(later)

    @staticmethod
    def pointers(text):
        return re.findall(r'(?m)^(\d+)@\d+/(\d+)(?:\s*\||$)', text)

    @staticmethod
    def decoded_text(text):
        """Read the documented literal/template table, not implementation IDs."""
        result, template, columns = [], None, []
        for line in text.splitlines():
            if line.startswith('[SOURCE '):
                template, columns = None, []
            elif line.startswith('Literal source template: '):
                template = line.removeprefix('Literal source template: ')
            elif 'Columns: source_rid@seq/segment' in line:
                columns = [cell.strip() for cell in line.split('Columns: ', 1)[1].split('|')]
            elif re.match(r'^\d+@\d+/\d+\s*\|', line):
                cells = [cell.strip() for cell in line.split('|')]
                if template is not None:
                    original = template
                    for column, value in zip(columns, cells):
                        if re.fullmatch(r'n_*\d+', column):
                            original = original.replace('{'+column+'}', value)
                    result.append(original)
                elif 'literal source text' in columns:
                    result.append(' | '.join(cells[columns.index('literal source text'):]))
        return '\n'.join(result)

    def history(self, count=120):
        for index in range(count):
            self.add(f'Archive witness WITNESS-{index:03d} retained a decision after considering the constraints. '
                     +'The conversation recorded the alternatives, uncertainty, practical consequences and next review. '
                     +'The participants requested a written confirmation after their detailed discussion.',
                     'session-'+str(index%9))

    def test_prefix_unchanged_and_omitted_sources_appended(self):
        self.history()
        first, later = self.views('Summarize all my conversations.')
        self.assertIn('truncated', first)
        self.assertIn('[CONTEXT CONTINUATION]', later)
        self.assertGreater(len(later), 12000)
        self.assertIn('coverage=complete for selected source scope', later.splitlines()[0])
        decoded = self.decoded_text(first+'\n'+later)
        for index in range(120):
            self.assertIn(f'WITNESS-{index:03d}', decoded)
        self.assertFalse(set(self.pointers(first)) & set(self.pointers(later)))
        self.assertEqual(len(self.pointers(first))+len(self.pointers(later)), 360)

    def test_small_ordinary_prefix_and_endpoint_coverage_remain_unchanged(self):
        self.history(96)
        first, later = self.views('archive decision', prefix=2000)
        self.assertIn('WITNESS-000', first)
        self.assertIn('WITNESS-095', first)
        self.assertIn('WITNESS-048', first+later)
        self.assertFalse(set(self.pointers(first)) & set(self.pointers(later)))

    def test_large_budget_does_not_turn_unknown_topic_into_global_history(self):
        self.history()
        self.add('My private cabinet code is PRIVATE-72141.', 'private')
        for query in ('Summarize the unknown Halcyon turbine protocol.',
                      'Apply what I shared about Halcyon turbine to the new design.',
                      'List all rules for Halcyon turbine.'):
            first, later = self.views(query)
            self.assertEqual(first, '')
            self.assertEqual(later, '')

    def test_topic_source_units_do_not_open_same_session_private_turns(self):
        for index in range(100):
            self.add(f'Kite adapter trial {index}: load={index+1}.25 kg; force={index+2}.75 N; duration=0.75 s. '+
                     'Kite adapter engineering notes retain the tested configuration and observed limitations.')
        self.add('My private clinic access code is PRIVATE-72141.')
        self.add('My address is 27 Fern Street.', user='other')
        first, later = self.views('Summarize the kite adapter trials and limitations.', prefix=1500)
        self.assertIn('Kite adapter', later)
        self.assertNotIn('PRIVATE-72141', first+later)
        self.assertNotIn('27 Fern Street', first+later)

    def test_observations_keep_multiplicity_without_prefix_double_count(self):
        for index in range(480):
            self.add(f'Probe observation {index:03d}: pressure={8000+index}.125 kPa after 0.75 seconds at 25% opening.')
        first, later = self.views('Calculate mean probe pressure across all observations.', prefix=2200)
        pointers = self.pointers(first)+self.pointers(later)
        self.assertEqual(len(pointers), 480)
        self.assertEqual(len(set(pointers)), 480)
        for index in range(480):
            self.assertIn(f'{8000+index}.125', first+later)
        self.assertIn('combined coverage=480/480', later)

    def test_same_values_are_still_separate_observations(self):
        for _ in range(200):
            self.add('Probe pressure was 12.5 kPa after 0.75 seconds.')
        first, later = self.views('Calculate mean probe pressure.', prefix=1800)
        self.assertEqual(len(set(self.pointers(first)+self.pointers(later))), 200)
        self.assertIn('combined coverage=200/200', later)

    def test_schema_and_withdrawal_survive_across_views(self):
        self.add('Kite experiments: payload denotes load in kg and thrust denotes measured force in N. Every reading uses the dry configuration.')
        for index in range(240):
            self.add(f'payload={index+2}.50; thrust={index*7+1}.25; dry mode.')
        self.add('The previous measurements are invalid because the sensor was disconnected; do not use them for calculations.')
        first, later = self.views('Calculate mean kite thrust from all experiments.', prefix=2200)
        self.assertIn('measurements are invalid', first)
        text = first+later
        self.assertIn('payload denotes load in kg', text)
        self.assertIn('thrust denotes measured force in N', text)
        self.assertIn('Every reading uses the dry configuration', text)
        self.assertIn('combined coverage=243/243', later)
        self.assertFalse(set(self.pointers(first)) & set(self.pointers(later)))

    def test_execution_protocol_retained_without_opening_other_rule_source(self):
        protocol = 'Execution protocol: compare operation labels in reverse lexical order; execute each operation once; never revisit skipped operations.'
        filler = 'The appendix describes stationery, chairs and ordinary cleaning. '*90
        for index in range(20):
            self.add(protocol+' '+filler+f' Kite ledger case {index} has balance=10 units. Rule Q: if balance exceeds 5 units, reserve 3 units.', 'ledger')
        self.add('A private clinic protocol repeats each operation once in ascending order. Clinic code PRIVATE-72141.', 'clinic')
        first, later = self.views('How many units remain in the kite ledger?', prefix=2200)
        self.assertIn('reverse lexical order', first+later)
        self.assertIn('never revisit skipped operations', first+later)
        self.assertNotIn('PRIVATE-72141', first+later)
        self.assertNotIn('ascending order', first+later)

    def test_long_single_segment_resumes_after_prefix_excerpt(self):
        text = ('Meridian delivery discussion '+'unchanged supporting context '*1900
                +'The final commitment is cancellation unless the witness signs by Friday noon.')
        self.add(text, origin='message.timestamp')
        first, later = self.views('Summarize the meridian delivery commitment.', prefix=1800)
        self.assertIn('source excerpt', first)
        self.assertNotIn('The final commitment', first)
        self.assertIn('The final commitment', later)
        self.assertIn('source_char_range', later)
        self.assertIn('combined coverage=1/1', later)
        self.assertEqual(later.count('The final commitment'), 1)
        self.assertNotIn('Meridian delivery discussion', later)

    def test_giant_single_segment_discloses_continuation_truncation_and_range(self):
        self.add('Meridian discussion '+'supporting context '*9000+' FINAL-TAIL-WITNESS')
        first, later = self.views('Summarize meridian discussion.', prefix=1800, continuation=6400)
        self.assertIn('source_char_range=', later)
        self.assertIn('combined coverage=0/1', later)
        self.assertIn('truncated', later.splitlines()[0])
        self.assertNotIn('FINAL-TAIL-WITNESS', first+later)

    def test_source_roles_and_time_origins_remain_explicit(self):
        for index in range(100):
            self.add(f'Archive witness WITNESS-{index:03d}: Tomorrow I will review the contract with the assigned owner. '
                     +'The discussion also retained the practical constraints and confirmation request.',
                     role='assistant' if index%2 else 'user',
                     origin='ingestion-clock' if index%3 else 'message.timestamp')
        first, later = self.views('Summarize all my conversations.', prefix=2000)
        self.assertIn('role=', later)
        self.assertIn('recorded_at', later)
        self.assertIn('M=message.timestamp', later)
        self.assertIn('I=ingestion-clock', later)
        self.assertIn('relative_time_anchor', later)
        self.assertIn('none', later)

    def test_metadata_is_escaped_in_continuation(self):
        self.history(100)
        self.add('Archive witness METADATA-WITNESS confirms the agreement.', session='lab | injected\nmetadata')
        first, later = self.views('Summarize all my conversations.', prefix=2000)
        text = first+later
        self.assertIn(r'\u007c', text)
        self.assertIn(r'\n', text)
        self.assertNotIn('lab | injected\nmetadata', text)

    def test_append_and_batch_have_the_same_complete_continuation_union(self):
        self.history(80)
        before, before_later = self.views('Summarize all my conversations.', prefix=2000)
        self.add('Archive witness APPENDED-WITNESS documents the corrected agreement.', 'new')
        after, after_later = self.views('Summarize all my conversations.', prefix=2000)
        self.assertNotIn('APPENDED-WITNESS', before+before_later)
        self.assertIn('APPENDED-WITNESS', after+after_later)
        self.assertEqual(len(set(self.pointers(after)+self.pointers(after_later))), 241)

    def test_no_continuation_when_prefix_contains_all_selected_segments(self):
        self.add('The kite relay passed inspection.')
        first, later = self.views('Summarize kite relay.')
        self.assertIn('passed inspection', first)
        self.assertEqual(later, '')

    def test_disabled_continuation_keeps_legacy_prefix(self):
        self.history()
        first, later = self.views('Summarize all my conversations.', continuation=0)
        self.assertTrue(first)
        self.assertEqual(later, '')


if __name__ == '__main__':
    unittest.main()
