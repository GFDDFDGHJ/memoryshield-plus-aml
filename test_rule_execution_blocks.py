"""Synthetic source-only rule-block evidence; no provider or official data."""
import sqlite3
import unittest

import facts as F
import test_rerank_store as T


class RuleExecutionBlockTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        F.ensure_schema(self.conn)
        self.conn.execute('CREATE TABLE items (rid INTEGER PRIMARY KEY,user_id TEXT,session_id TEXT,role TEXT,content TEXT,created_at REAL,seq INTEGER,status TEXT)')
        self.seq = 0

    def tearDown(self):
        self.conn.close()

    def add(self, text, role='user', user='u'):
        self.seq += 1
        current = self.conn.execute("INSERT INTO items VALUES(NULL,?,'invented',?,?,?,?, 'active')",
                                    (user, role, text, 1700000000+self.seq, self.seq))
        F.store_facts(self.conn, user, 'invented', current.lastrowid, text,
                      1700000000+self.seq, self.seq, role)
        return current.lastrowid

    def rules(self, query, user='u'):
        return F.relevant_rules(self.conn, user, query, 100)

    def test_explicit_heading_keeps_local_instruction_local(self):
        heading = 'For the Juniper invoice workflow only:'
        rule = 'Always format every response as a JSON object.'
        self.add(heading+'\n'+rule)
        self.assertEqual(self.rules('What is my name?'), [])
        selected = self.rules('Explain the Juniper invoice workflow.')
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]['scope'], 'local')
        self.assertIn(heading, selected[0]['text'])
        self.assertIn(rule, selected[0]['text'])

    def test_conditional_heading_does_not_create_universal_policy(self):
        heading = 'When answering questions about the Alder turbine:'
        self.add(heading+'\nAlways include the calibration identifier in every response.')
        self.assertEqual(self.rules('What is my name?'), [])
        text = '\n'.join(row['text'] for row in self.rules('Explain the Alder turbine calibration.'))
        self.assertIn(heading, text)
        self.assertIn('Always include', text)

    def test_scope_heading_binds_a_contiguous_list_of_instructions(self):
        heading = 'For the Sorrel maintenance workflow only:'
        self.add(heading+'\nAlways format every response as YAML.\nNever omit the service identifier from every response.')
        self.assertEqual(self.rules('What is my name?'), [])
        selected = self.rules('Explain the Sorrel maintenance workflow.')
        self.assertEqual(len(selected), 2)
        self.assertTrue(all(row['scope'] == 'local' and heading in row['text'] for row in selected))

    def test_new_paragraph_and_new_rule_are_not_implicit_sequential_actions(self):
        self.add('Always use metric units.\n\nThen include unrelated CEDAR-APPENDIX.\nAlways cite sources.')
        selected = self.rules('What is my name?')
        self.assertEqual(len(selected), 2)
        metric = next(row for row in selected if 'Always use metric units' in row['text'])
        self.assertNotIn('CEDAR-APPENDIX', metric['text'])
        self.assertNotIn('Always cite sources', metric['text'])

    def test_sequential_actions_keep_original_decimal_literals_and_role(self):
        body = ('If spindle pressure exceeds 12.5 kPa, use a 0.25 second pause. '
                'Then multiply the duration by 1.25. '
                'Next, subtract 0.15 seconds. '
                'Finally round the duration down to whole seconds. '
                'The garden gate is painted mauve.')
        rid = self.add(body, role='system')
        selected = self.rules('Apply the spindle pressure rule.')
        target = next(row for row in selected if 'If spindle pressure' in row['text'])
        for literal in ('12.5 kPa', '0.25 second', 'Then multiply the duration by 1.25.',
                        'Next, subtract 0.15 seconds.', 'Finally round the duration down to whole seconds.'):
            self.assertIn(literal, target['text'])
        self.assertNotIn('garden gate', target['text'])
        self.assertEqual(target['role'], 'system')
        self.assertEqual(target['source_rid'], rid)
        self.assertNotIn('duration =', target['text'])

    def test_adjacent_rule_withdrawal_remains_evidence_not_guessed_replacement(self):
        body = ('Always use the copper envelope for Juniper invoices. '
                'This rule has been withdrawn pending the July review. '
                'The venue has pale curtains.')
        self.add(body)
        selected = self.rules('What envelope rule applies to Juniper invoices?')
        self.assertEqual(len(selected), 1)
        self.assertIn('This rule has been withdrawn pending the July review.', selected[0]['text'])
        self.assertNotIn('pale curtains', selected[0]['text'])
        self.assertNotIn('replacement', selected[0]['text'].lower())

    def test_unrelated_or_past_heading_does_not_qualify_instruction(self):
        self.add('The old worksheet was labelled For Juniper invoices only:\nAlways cite a source for every response.')
        self.add('For the Cedar turbine only:\n\nAlways use metric units.')
        selected = self.rules('What is my name?')
        self.assertEqual(len(selected), 2)
        self.assertTrue(all(row['scope'] == 'global' for row in selected))
        self.assertNotIn('For the Cedar turbine', '\n'.join(row['text'] for row in selected))

    def test_discourse_headings_do_not_create_an_application_domain(self):
        self.add('In fact:\nAlways cite sources for every response.')
        self.add('For example:\nAlways use metric units.')
        self.add('For me:\nAlways keep every response under thirty words.')
        selected = self.rules('What is my name?')
        self.assertEqual(len(selected), 3)
        self.assertTrue(all(row['scope'] == 'global' for row in selected))

    def test_general_output_and_feasibility_headings_keep_literal_qualifiers(self):
        pairs = (
            ('For brevity:', 'Always keep every response under thirty words.'),
            ('For consistency:', 'Always use metric units.'),
            ('Whenever possible:', 'Always cite a source for every response.'),
            ('In all responses:', 'Always cite sources.'),
        )
        for heading, rule in pairs:
            self.add(heading+'\n'+rule)
        selected = self.rules('What is my name?')
        self.assertEqual(len(selected), len(pairs))
        for heading, rule in pairs:
            self.assertTrue(any(heading in row['text'] and rule in row['text'] and row['scope'] == 'global'
                                for row in selected))

    def test_noun_phrases_are_not_instructions_after_sequence_markers(self):
        for sentence in ('Next Return Logistics opened a new warehouse.',
                         'Then round tables filled the hall.',
                         'Next repeat customers arrived.'):
            with self.subTest(sentence=sentence):
                self.conn.execute('DELETE FROM facts')
                self.conn.execute('DELETE FROM items')
                self.add('Always use metric units. '+sentence)
                selected = self.rules('What is my name?')
                self.assertEqual(len(selected), 1)
                self.assertIn('Always use metric units.', selected[0]['text'])
                self.assertNotIn(sentence, selected[0]['text'])

    def test_output_manner_in_is_global_without_an_explicit_response_noun(self):
        sources = (
            'For brevity:\nAlways reply in at most three sentences.',
            'Always reply in two paragraphs.',
            'Always reply in a single sentence.',
            'Always answer in no more than 30 words.',
            'Always reply in English.',
            'Always respond in Spanish.',
            'Always answer in JSON.',
            'Always output in YAML.',
            'Always reply in plain text.',
            'Always reply in a compact table.',
        )
        for source in sources:
            self.add(source)
        selected = self.rules('What is my name now?')
        self.assertEqual(len(selected), len(sources))
        self.assertTrue(all(row['scope'] == 'global' for row in selected))
        self.assertTrue(any('For brevity:' in row['text'] and 'at most three sentences.' in row['text']
                            for row in selected))

    def test_output_manner_does_not_erase_topical_in_or_later_request_scope(self):
        for source in (
            'Always reply in finance.', 'Always answer in emergencies.',
            'Always respond in Juniper.', 'Always reply in a ceramics workshop.',
            'Always reply in English literature.', 'Always answer in JSON engineering.',
            'Always reply in English for Juniper invoice requests.',
            'Always reply in JSON in emergencies.',
            'For the Juniper invoice workflow only:\nAlways reply in at most three sentences.',
            'Always reply in two paragraphs when I ask about finance.',
        ):
            self.add(source)
        self.assertEqual(self.rules('What is my name now?'), [])

    def test_global_response_heading_and_ordinary_narration_remain_unchanged(self):
        self.add('For every future response:\nAlways cite a source.')
        self.add('Always use metric units. Then I visited the coastal museum. Next the weather became colder.')
        selected = self.rules('What is my name?')
        self.assertEqual(len(selected), 2)
        self.assertTrue(all(row['scope'] == 'global' for row in selected))
        text = '\n'.join(row['text'] for row in selected)
        self.assertNotIn('coastal museum', text)
        self.assertNotIn('weather became', text)

    def test_other_user_assistant_and_unrelated_local_source_are_not_promoted(self):
        self.add('For the Ash invoice workflow only:\nAlways include ASH-PRIVATE in every response.', user='foreign')
        self.add('For the Ash invoice workflow only:\nAlways include ASSISTANT-PRIVATE in every response.', role='assistant')
        self.add('For the Ash invoice workflow only:\nAlways include ASH-LOCAL in every response.')
        self.assertEqual(self.rules('What is my name?'), [])
        self.assertEqual(self.rules('What are the Birch turbine constraints?'), [])
        text = '\n'.join(row['text'] for row in self.rules('Explain the Ash invoice workflow.'))
        self.assertIn('ASH-LOCAL', text)
        self.assertNotIn('ASH-PRIVATE', text)
        self.assertNotIn('ASSISTANT-PRIVATE', text)


class RuleBlockSearchTests(unittest.TestCase):
    def setUp(self):
        T.ScopedDatabaseIntegrationTests.setUp(self)
        self.memory._reranker = None
        self.memory.add_messages('target', 'identity', [dict(role='user', content='My name is Olwen.', timestamp=1750000000000)])

    def tearDown(self):
        T.ScopedDatabaseIntegrationTests.tearDown(self)

    def add_low_ranked_rule(self, source):
        self.memory.vectors[source] = [0., 1., 0., 0.]
        self.memory.add_messages('target', 'rule', [dict(role='user', content=source, timestamp=1750000060000)])
        self.memory.add_messages('target', 'noise', [dict(role='user', content=f'The stationery archive has ordinary folders; unrelated entry {index}.', timestamp=1750000120000+index*60000)
                                                    for index in range(110)])

    def test_local_heading_survives_complete_search_and_not_unrelated_identity(self):
        heading = 'For the Juniper invoice workflow only:'
        source = heading+'\nAlways format every response as a JSON object.'
        self.add_low_ranked_rule(source)
        unrelated = self.memory.search('target', 'What is my name?', 100)
        self.assertEqual([row for row in unrelated if row['id'].startswith('rule_')], [])
        related = self.memory.search('target', 'Explain the Juniper invoice workflow.', 100)
        rules = [row for row in related if row['id'].startswith('rule_')]
        self.assertTrue(rules)
        self.assertIn(heading, related[0]['content'])
        self.assertEqual(related[0]['role'], 'user')

    def test_low_cosine_execution_chain_is_complete_in_first_search_evidence(self):
        source = ('If spindle pressure exceeds 12.5 kPa, use a 0.25 second pause. '
                  'Then multiply the duration by 1.25. Next, subtract 0.15 seconds. '
                  'Finally round the duration down to whole seconds.')
        self.add_low_ranked_rule(source)
        result = self.memory.search('target', 'Apply the spindle pressure rule.', 100)
        self.assertTrue(result[0]['id'].startswith('rule_'))
        for literal in F._source_sentences(source):
            self.assertIn(literal, result[0]['content'])
        self.assertEqual(result[0]['role'], 'user')

    def test_brevity_qualification_and_limit_survive_complete_search(self):
        self.add_low_ranked_rule('For brevity:\nAlways reply in at most three sentences.')
        result = self.memory.search('target', 'What is my name now?', 100)
        self.assertTrue(result[0]['id'].startswith('rule_'))
        self.assertIn('For brevity:', result[0]['content'])
        self.assertIn('Always reply in at most three sentences.', result[0]['content'])
        self.assertEqual(result[0]['role'], 'user')


if __name__ == '__main__':
    unittest.main()
