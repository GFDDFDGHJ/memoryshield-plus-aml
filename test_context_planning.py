"""Fresh metamorphic source-planning checks; memory SQLite, no provider calls."""
import re
import sqlite3
import unittest

import facts as F


class ContextPlanningTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        F.ensure_schema(self.conn)
        self.conn.execute('CREATE TABLE items(rid INTEGER PRIMARY KEY,user_id TEXT,session_id TEXT,role TEXT,content TEXT,created_at REAL,seq INTEGER,status TEXT,timestamp_origin TEXT)')

    def tearDown(self):
        self.conn.close()

    def add(self, content, session='session', role='user'):
        seq = self.conn.execute('SELECT count(*)+1 FROM items').fetchone()[0]
        rid = self.conn.execute("INSERT INTO items VALUES(NULL,'u',?,?,?,?,?,'active','message.timestamp')",
                                (session, role, content, 1750000000+seq*3600, seq)).lastrowid
        F.store_facts(self.conn, 'u', session, rid, content, 1750000000+seq*3600, seq, role)
        return rid

    def rows(self):
        return [dict(row) for row in self.conn.execute('SELECT * FROM items ORDER BY seq')]

    def context(self, query, budget=12000):
        result = F.get_digests(self.conn, 'u', query, 6, budget_chars=budget)
        self.assertLessEqual(sum(map(len, result)), budget)
        return '\n'.join(result)

    def personal_history(self):
        self.add('I rejected the transfer because the ferry commute was unreliable.', 'career')
        self.add('I postponed the climbing trip after injuring my shoulder.', 'health')
        self.add('I care for my aunt every Wednesday afternoon.', 'family')

    def test_personal_overview_paraphrases_share_the_source_plan(self):
        self.personal_history()
        expected = {row['rid'] for row in self.rows()}
        for query in ('Summarize the key points I previously shared with you.',
                      'What common themes arise from our earlier discussions?',
                      'Recap important things I mentioned.',
                      'What major lessons stand out in my past experiences?'):
            with self.subTest(query=query):
                self.assertNotEqual(F.context_scope(query)[0], 'ordinary')
                scope, topics, reason = F.source_context_scope(self.rows(), query)
                self.assertEqual((scope, topics), ('global', set()))
                self.assertEqual(reason, 'source-unanchored-memory-overview')
                self.assertEqual(F.context_source_rids(self.rows(), query), expected)
                text = self.context(query)
                for row in self.rows():
                    self.assertIn(row['content'], text)

    def test_concrete_or_excluded_topics_never_trigger_overview_fallback(self):
        self.personal_history()
        self.add('My cabinet passcode is PRIVATE-60491.', 'private')
        for query in ('Summarize the important medical things I told you.',
                      'What broad themes emerge from my experiences about the turbine?',
                      'Summarize key points I told you regarding Anhinga.',
                      'Summarize key points I shared without personal details.',
                      'Summarize important things I mentioned about test 421.',
                      'Summarize the "river monitoring" experiences I shared.'):
            with self.subTest(query=query):
                self.assertEqual(F.source_context_scope(self.rows(), query)[0], 'topic-scoped')
                self.assertNotIn('PRIVATE-60491', self.context(query))

    def test_abstract_domain_without_evidence_stays_topic_constrained(self):
        self.personal_history()
        query = 'Summarize my professional compromises and family-care decisions.'
        self.assertEqual(F.source_context_scope(self.rows(), query)[0], 'topic-scoped')
        self.assertNotIn('ferry commute', self.context(query))
        self.assertNotIn('climbing trip', self.context(query))

    def test_ordinary_memory_question_is_not_promoted_to_global(self):
        self.personal_history()
        query = 'What did I mention about the ferry?'
        self.assertEqual(F.context_scope(query)[0], 'ordinary')
        self.assertEqual(F.source_context_scope(self.rows(), query)[0], 'ordinary')

    def test_supported_topic_keeps_other_sources_out(self):
        self.add('The heron relay now runs at the eastern site.', 'relay')
        self.add('My cabinet passcode is PRIVATE-60491.', 'private')
        query = 'Summarize what I mentioned about the heron relay.'
        self.assertEqual(F.source_context_scope(self.rows(), query)[0], 'topic-scoped')
        text = self.context(query)
        self.assertIn('eastern site', text)
        self.assertNotIn('PRIVATE-60491', text)

    def test_selector_rejects_cross_user_inputs(self):
        self.personal_history()
        rows = self.rows()
        rows[-1]['user_id'] = 'someone-else'
        with self.assertRaises(ValueError):
            F.source_context_scope(rows, 'Summarize the important things I told you.')

    def test_existing_everything_intent_remains_requested(self):
        self.assertNotEqual(F.context_scope('Tell me everything we discussed.')[0], 'ordinary')

    def test_budget_backfill_preserves_more_independent_sources(self):
        for topic in ('employment', 'hobbies', 'caregiving'):
            for index in range(15):
                marker = topic.upper()+'-'+chr(65+index)+'-WITNESS'
                self.add(marker+' The revised arrangement reflected practical limitations explained in a difficult discussion. '
                         +'Participants considered alternatives carefully and retained the chosen approach for a documented reason. '
                         +'An administrative review will check the final wording and seek written confirmation.', topic+'-'+str(index))
        text = self.context('Summarize all my conversations.')
        self.assertGreater(len(text), 11000)
        self.assertIn('truncated', text)
        self.assertEqual(len(set(re.findall(r'(?m)^(\d+)@\d+/\d+', text))), 45)
        for topic in ('EMPLOYMENT', 'HOBBIES', 'CAREGIVING'):
            self.assertIn(topic+'-', text[:8000])

    def test_budget_backfill_is_bounded_at_several_sizes(self):
        for index in range(73):
            self.add('Decision '+str(index)+' retained the handwritten agreement after considering the practical constraints. '
                     +'The supporting discussion documented both the reason and the remaining uncertainty.', 'topic-'+str(index%4))
        for budget in (1500, 3000, 6000, 12000):
            text = self.context('Summarize all my conversations.', budget)
            self.assertGreater(len(text), budget*.82)
            self.assertIn('truncated', text)

    def test_small_context_keeps_history_endpoints_before_session_first_round(self):
        for session in range(12):
            for index in range(10):
                label = 'OPENING-WITNESS' if session == index == 0 else f'note-{session}-{index}'
                self.add('Archive '+label+' records a review and the practical reason for retaining the agreement.', 'archive-'+str(session))
        self.add('Archive TERMINAL-WITNESS confirms the final agreement. However, the final approval is conditional on witness verification.', 'archive-11')
        text = self.context('archive', 2000)
        self.assertIn('OPENING-WITNESS', text)
        self.assertIn('TERMINAL-WITNESS', text)
        self.assertIn('conditional on witness verification', text)
        self.assertIn('truncated', text)

    def test_full_numeric_encoding_retains_qualification_and_every_observation(self):
        self.add('The preceding readings are invalid; do not use those readings for calculations.')
        for index in range(240):
            self.add(f'Probe reading {index:03d}: pressure={4000+index}.125 kPa; duration=0.75 s; humidity=25%.')
        # Explicitly required validity qualifiers remain prior to usable-looking data.
        units = F.context_units_for_query(self.rows(), 'Calculate mean probe pressure from all readings.')
        for unit in units:
            if unit['source_rid'] == 1:
                unit['context_qualification'] = True
        pieces = F._pack_context(units, 6, 12000, 'topic-scoped')
        text = '\n'.join(pieces)
        self.assertLessEqual(sum(map(len, pieces)), 12000)
        self.assertIn('included=241/241', text)
        self.assertIn('preceding readings are invalid', text[:1000])
        for index in range(240):
            self.assertIn(f'{4000+index}.125', text)

    def test_declaration_and_validity_survive_tight_numeric_context(self):
        self.add('Finch experiments: mass indicates applied load in kg; thrust indicates measured force in N.')
        for index in range(120):
            self.add(f'mass={index+1}; thrust={index*5+3}; dry mode.')
        self.add('These measurements are invalid because the sensor was unplugged; do not use them.')
        text = self.context('Calculate average finch thrust for all experiments.', 2300)
        self.assertIn('applied load in kg', text)
        self.assertIn('measured force in N', text)
        self.assertIn('measurements are invalid', text)
        self.assertIn('truncated', text)
        self.assertNotIn('coverage=complete', text)

    def test_global_response_policy_applies_to_fact_questions(self):
        for value in ('Always use metric units.', 'Always cite sources for every response.', 'Never disclose private addresses.'):
            self.add(value)
        rules = F.relevant_rules(self.conn, 'u', 'Who owns the heron relay?', 50)
        self.assertEqual(len(rules), 3)
        self.assertEqual({rule['scope'] for rule in rules}, {'global'})

    def test_local_output_policy_and_safety_gear_do_not_become_global(self):
        for value in ('For audit ledger, all outputs must cite the ledger row.',
                      'Always wear a helmet.', 'Always use the violet folder for invoices.'):
            self.add(value, role='system')
        self.assertEqual(F.relevant_rules(self.conn, 'u', 'Who owns the heron relay?', 50), [])

    def test_explicit_universal_output_manner_stays_global(self):
        self.add('Always write every future response in Spanish.')
        self.add('Always format every future response in a compact table.')
        self.assertEqual(len(F.relevant_rules(self.conn, 'u', 'Who owns the heron relay?', 50)), 2)

    def test_execution_controls_remain_attached_only_to_matched_source(self):
        protocol = 'Execution protocol: compare operation labels in reverse lexical order; evaluate each operation at most one time; never return to a skipped operation.'
        filler = 'The appendix describes stationery, chairs and ordinary room cleaning. '*100
        self.add(protocol+' '+filler+' Initial balance for starling ledger is 10 units. Rule Q: if balance exceeds 5, reserve 3 units.')
        self.add('Unrelated protocol: repeat each operation once, in ascending order. My private clinic code is PRIVATE-60491.', 'private')
        text = self.context('How many units remain in the starling ledger?')
        self.assertIn(protocol, text)
        self.assertNotIn('ascending order', text)
        self.assertNotIn('PRIVATE-60491', text)

    def test_one_message_can_keep_more_than_twelve_explicit_rules(self):
        text = '\n'.join('Always include source tag TAG'+str(index)+' in every response.' for index in range(25))
        self.add(text, role='system')
        rules = F.relevant_rules(self.conn, 'u', 'Who owns the heron relay?', 50)
        self.assertEqual(len(rules), 25)
        for index in range(25):
            self.assertTrue(any('TAG'+str(index)+' ' in rule['text'] for rule in rules))

    def test_many_authoritative_bullets_keep_all_but_assistant_has_no_authority(self):
        text = '\n'.join('- Always include output code CODE'+str(index)+'.' for index in range(18))
        self.add(text, role='system')
        self.assertEqual(len(F.relevant_rules(self.conn, 'u', 'Who owns the heron relay?', 50)), 18)
        self.conn.execute('DELETE FROM items'); self.conn.execute('DELETE FROM facts')
        self.add(text, role='assistant')
        self.assertEqual(F.relevant_rules(self.conn, 'u', 'Who owns the heron relay?', 50), [])

    def test_complete_exception_chain_does_not_stop_after_three(self):
        self.add('Always use metric units. '+ ' '.join('Exception: when flag FLAG'+str(index)+' is set, cite its unit definition.' for index in range(9)))
        rules = F.relevant_rules(self.conn, 'u', 'Who owns the heron relay?', 50)
        target = next(rule for rule in rules if 'Always use metric units' in rule['text'])
        for index in range(9):
            self.assertIn('FLAG'+str(index), target['text'])


if __name__ == '__main__':
    unittest.main()
