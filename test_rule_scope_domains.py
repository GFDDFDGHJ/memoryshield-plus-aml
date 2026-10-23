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


if __name__ == '__main__':
    unittest.main()
