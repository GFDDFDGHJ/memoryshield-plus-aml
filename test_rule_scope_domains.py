"""Response-domain scope controls on synthetic low-cosine original sources."""
import unittest

import test_rerank_store as T


class RuleScopeDomainTests(unittest.TestCase):
    def setUp(self):
        T.ScopedDatabaseIntegrationTests.setUp(self)
        self.memory._reranker = None
        self.memory.add_messages('target', 'identity', [dict(role='user', content='My name is Nalani.', timestamp=1750000000000)])

    def tearDown(self):
        T.ScopedDatabaseIntegrationTests.tearDown(self)

    def add_rule(self, text, index=1):
        # A zero cosine to the identity query makes dedicated rule admission
        # observable independently from dense ranking or a provider's behavior.
        self.memory.vectors[text] = [0., 1., 0., 0.]
        self.memory.add_messages('target', f'rules-{index}', [dict(role='user', content=text, timestamp=1750000000000+index*60000)])

    def rule_rows(self, query):
        with self.memory._connect() as conn:
            return T.S.fact_layer.relevant_rules(conn, 'target', query, 100)

    def search_rules(self, query='What is my name?'):
        return [row for row in self.memory.search('target', query, 100) if row['id'].startswith('rule_')]

    def test_response_noun_topic_adjuncts_are_local_in_complete_search(self):
        for index, text in enumerate((
            'Always format every response about ceramic-glaze invoices as a compact table.',
            'Always write every answer to ceramic-glaze questions in Spanish.',
            'Always keep every response for ceramic-glaze requests under 30 words.',
            'Always format every response concerning ceramic-glaze invoices as YAML.',
        ), 1):
            self.add_rule(text, index)
        self.assertEqual(self.rule_rows('What is my name?'), [])
        self.assertEqual(self.search_rules(), [])

    def test_unpunctuated_request_conditions_still_bound_the_rule(self):
        for index, text in enumerate((
            'Always keep all replies under 30 words when I ask about ceramic-glaze invoices.',
            'Always format every response as YAML whenever the user requests ceramic-glaze advice.',
            'Always write every answer in Spanish when answering ceramic-glaze questions.',
        ), 1):
            self.add_rule(text, index)
        self.assertEqual(self.rule_rows('What is my name?'), [])
        self.assertEqual(self.search_rules(), [])

    def test_local_rules_remain_available_for_the_matching_topic(self):
        rule = 'Always format every response about ceramic-glaze invoices as a compact table.'
        self.add_rule(rule)
        selected = self.rule_rows('Explain the ceramic-glaze invoice requirements.')
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]['scope'], 'local')
        results = self.search_rules('Explain the ceramic-glaze invoice requirements.')
        self.assertTrue(any(rule in row['content'] for row in results))
        self.assertTrue(all(row['role'] == 'user' for row in results))

    def test_global_language_format_and_length_modifiers_stay_global(self):
        rules = (
            'Always write every future response in Spanish.',
            'Always format every response in a compact table.',
            'Always keep every response under 30 words.',
            'Always write every answer to be concise.',
            'Always keep every answer to the point.',
            'Always write every response on one line.',
            'Always write every response for me in Spanish.',
        )
        for index, text in enumerate(rules, 1):
            self.add_rule(text, index)
        selected = self.rule_rows('What is my name?')
        self.assertEqual(len(selected), len(rules))
        self.assertTrue(all(row['scope'] == 'global' for row in selected))
        text = '\n'.join(row['content'] for row in self.search_rules())
        for literal in rules:
            self.assertIn(literal, text)

    def test_quoted_domain_and_conditional_text_remain_literal_output(self):
        rules = (
            'Always format every response with the exact heading "answers about ceramic-glaze invoices".',
            'Always format every response with the literal label "when I ask about ceramic-glaze invoices".',
        )
        for index, text in enumerate(rules, 1):
            self.add_rule(text, index)
        selected = self.rule_rows('What is my name?')
        self.assertEqual(len(selected), len(rules))
        self.assertTrue(all(row['scope'] == 'global' for row in selected))
        rendered = '\n'.join(row['content'] for row in self.search_rules())
        for literal in rules:
            self.assertIn(literal, rendered)

    def test_existing_local_prefix_and_object_domains_are_preserved(self):
        for index, text in enumerate((
            'For ceramic-glaze invoices, always format every response as a compact table.',
            'Always use the blue archive folder for invoices.',
        ), 1):
            self.add_rule(text, index)
        self.assertEqual(self.rule_rows('What is my name?'), [])
        self.assertEqual(self.search_rules(), [])

    def test_identical_actions_keep_each_heading_in_one_original(self):
        action = 'Always format every response as YAML.'
        alder = 'For Alder invoice processing only:'
        cedar = 'For Cedar inventory only:'
        self.add_rule(alder+'\n'+action+'\n'+cedar+'\n'+action)
        self.assertEqual(self.rule_rows('What is my name?'), [])
        self.assertEqual(self.search_rules(), [])
        for query, wanted, other in (
            ('Explain Alder invoice processing.', alder, cedar),
            ('Explain Cedar inventory.', cedar, alder),
        ):
            selected = self.rule_rows(query)
            self.assertEqual(len(selected), 1)
            self.assertEqual(selected[0]['scope'], 'local')
            self.assertIn(wanted, selected[0]['text'])
            self.assertNotIn(other, selected[0]['text'])
            self.assertIn(action, selected[0]['text'])
            self.assertEqual(selected[0]['role'], 'user')
            rows = self.search_rules(query)
            self.assertTrue(any(wanted in row['content'] for row in rows))

    def test_identical_actions_in_separate_sources_keep_local_scope(self):
        action = 'Always format every response as YAML.'
        alder = 'For Alder invoice processing only:'
        cedar = 'For Cedar inventory only:'
        self.add_rule(alder+'\n'+action, 1)
        self.add_rule(cedar+'\n'+action, 2)
        selected = self.rule_rows('Explain Cedar inventory.')
        self.assertEqual(len(selected), 1)
        self.assertIn(cedar, selected[0]['text'])
        self.assertNotIn(alder, selected[0]['text'])
        self.assertEqual(selected[0]['session_id'], 'rules-2')
        self.assertEqual(selected[0]['scope'], 'local')
        self.assertEqual(self.search_rules(), [])

    def test_decimal_revision_heading_does_not_create_a_global_rule(self):
        heading = 'For the Juniper calibration workflow only (revision 2.1):'
        action = 'Always include every output in JSON.'
        self.add_rule(heading+'\n'+action)
        self.assertEqual(self.rule_rows('What is my name?'), [])
        self.assertEqual(self.search_rules(), [])
        selected = self.rule_rows('Explain Juniper calibration.')
        self.assertEqual(len(selected), 1)
        self.assertIn(heading, selected[0]['text'])
        self.assertEqual(selected[0]['scope'], 'local')

    def test_shared_action_keeps_occurrence_specific_exception(self):
        action = 'Always use the padded crate.'
        alder = 'For Alder dispatch only:'
        cedar = 'For Cedar inventory only:'
        self.add_rule(alder+'\n'+action+' Exception: use rigid cases for glass.\n'+
                      cedar+'\n'+action+' Exception: use dry cartons for powder.')
        selected = self.rule_rows('Explain Alder dispatch.')
        self.assertEqual(len(selected), 1)
        self.assertIn('Exception: use rigid cases for glass.', selected[0]['text'])
        self.assertNotIn('dry cartons', selected[0]['text'])
        selected = self.rule_rows('Explain Cedar inventory.')
        self.assertEqual(len(selected), 1)
        self.assertIn('Exception: use dry cartons for powder.', selected[0]['text'])
        self.assertNotIn('rigid cases', selected[0]['text'])

    def test_repeated_scoped_policy_does_not_displace_distinct_constraints(self):
        action = 'Always format every response as YAML.'
        alder = 'For Alder invoice processing only:\n'+action
        cedar = 'For Cedar inventory only:\n'+action
        self.add_rule(alder, 1)
        for index in range(2, 102):
            self.add_rule(cedar, index)
        constraints = ('Always include the review date in every response.',
                       'Always cite the source identifier in every response.',
                       'Always keep every response under forty words.')
        for index, policy in enumerate(constraints, 102):
            self.add_rule(policy, index)
        with self.memory._connect() as conn:
            count = conn.execute("SELECT count(*) FROM facts WHERE user_id='target' AND kind='rule' AND status='active'").fetchone()[0]
        self.assertEqual(count, 5)
        selected = self.rule_rows('List all standing rules.')
        self.assertEqual(len(selected), 5)
        rendered = '\n'.join(row['content'] for row in self.search_rules('List all standing rules.'))
        for policy in constraints:
            self.assertIn(policy, rendered[:8000])
        self.assertEqual(rendered.count('For Alder invoice processing only:'), 1)
        self.assertEqual(rendered.count('For Cedar inventory only:'), 1)

    def test_global_and_local_occurrences_are_not_conjoined(self):
        action = 'Always cite a source for every response.'
        heading = 'For Alder invoice processing only:'
        self.add_rule(action+'\n'+heading+'\n'+action)
        selected = self.rule_rows('What is my name?')
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]['scope'], 'global')
        self.assertIn(action.rstrip('.'), selected[0]['text'])
        self.assertNotIn(heading, selected[0]['text'])

    def test_equal_rule_context_keeps_distinct_original_speakers(self):
        text = 'For Alder invoice processing only:\nAlways format every response as YAML.'
        for index, role in enumerate(('user', 'system'), 1):
            self.memory.add_messages('target', f'role-{role}', [dict(
                role=role, content=text, timestamp=1750000000000+index*60000)])
        selected = self.rule_rows('Explain Alder invoice processing.')
        self.assertEqual(len(selected), 2)
        self.assertEqual({row['role'] for row in selected}, {'user', 'system'})
        self.assertEqual(len({row['source_rid'] for row in selected}), 2)
        for row in selected:
            self.assertIn('role='+row['role'], row['text'])
            self.assertEqual(row['session_id'], 'role-'+row['role'])
            self.assertEqual(row['scope'], 'local')


if __name__ == '__main__':
    unittest.main()
