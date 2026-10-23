"""Invented assertion-attribution controls; no providers or production data."""
from __future__ import annotations

import unittest

import facts as F


NONASSERTIONS = [
    'Did I say I live in Alder Bay?',
    'Do you think my name is Brindle?',
    'My name is Brindle?',
    'She said "I live in Alder Bay."',
    'He wrote, “My name is Brindle.”',
    "'I live in Alder Bay.'",
    '`My name is Brindle.`',
    '```text\nI live in Alder Bay.\n```',
    'I said I live in Alder Bay.',
    'My neighbor says I live in Alder Bay.',
    'The witness claimed that my name is Brindle.',
    'If I were promoted I live in Alder Bay.',
    'Suppose I live in Alder Bay.',
    'Imagine that my name is Brindle.',
    'I live in Alder Bay if the application is approved.',
    'I live in Alder Bay unless the permit is denied.',
    'I no longer live in Willow Bay if the permit is approved.',
    'It is not true that I live in Alder Bay.',
    'Last year, I moved to Alder Bay.',
    'I moved to Alder Bay last year.',
    'Actually I used to live in Alder Bay.',
    'Actually, did I say I live in Alder Bay?',
    'In 2017, I moved to Alder Bay.',
    "My dog's name was Brindle.",
    'Example:\nI live in Alder Bay.',
    '她说“我住在青湾。”',
    '她说我住在青湾。',
    '如果我住在青湾。',
    '我住在青湾吗？',
    '去年我住在青湾。',
    '假设我住在青湾。',
]

DIRECT_ASSERTIONS = [
    ('I live in Willow Bay.', 'location', 'Willow Bay'),
    ('Actually, I live in Willow Bay.', 'location', 'Willow Bay'),
    ('Correction: my name is Lumen.', 'name', 'Lumen'),
    ('As I said, I live in Willow Bay.', 'location', 'Willow Bay'),
    ('Will and I live in Willow Bay.', 'location', 'Willow Bay'),
    ('In May, I live in Willow Bay.', 'location', 'Willow Bay'),
    ('My name is "Lumen".', 'name', 'Lumen'),
    ("My dog's name is Brindle.", 'name', 'Brindle'),
    ('I have moved to Willow Bay.', 'location', 'Willow Bay'),
    ('Actually I moved to Shanghai last month.', 'location', 'Shanghai last month'),
    ('I no longer live in Willow Bay.', 'location', 'Willow Bay'),
    ('我住在白湾。', 'location', '白湾'),
    ('其实我住在白湾。', 'location', '白湾'),
]


class AssertionContextTests(unittest.TestCase):
    def test_nonasserted_self_descriptions_do_not_become_current(self):
        for text in NONASSERTIONS:
            with self.subTest(text=text):
                self.assertEqual([fact for fact in F.extract_facts(text)
                                  if fact.kind != 'rule'], [])

    def test_direct_assertions_corrections_and_quoted_values_survive(self):
        for text, predicate, value in DIRECT_ASSERTIONS:
            with self.subTest(text=text):
                self.assertTrue(any(fact.predicate == predicate and fact.value == value
                                    for fact in F.extract_facts(text)))

    def test_questions_or_reports_do_not_cancel_later_direct_same_source_assertion(self):
        sources = [
            'Did I say I live in Alder Bay? I live in Willow Bay.',
            'I live in Willow Bay. Did I say I live in Alder Bay?',
            'She said "I live in Alder Bay." Actually, I live in Willow Bay.',
            'She said I live in Alder Bay, but I live in Willow Bay.',
            'If approved, I live in Alder Bay. I live in Willow Bay.',
            'My name is Brindle? Correction: my name is Lumen.',
            '她说我住在青湾。其实我住在白湾。',
        ]
        for text in sources:
            with self.subTest(text=text):
                values = [fact.value for fact in F.extract_facts(text) if fact.kind != 'rule']
                self.assertEqual(values, ['Lumen' if 'Lumen' in text else
                                         '白湾' if '白湾' in text else 'Willow Bay'])

    def test_rules_and_explicit_structured_triples_retain_their_semantics(self):
        self.assertTrue(any(fact.kind == 'rule' for fact in F.extract_facts(
            'If I ask about ceramics, always cite the kiln log.')))
        triples = F.extract_facts('{"subject":"Aster","relation":"material","object":"copper"}')
        self.assertEqual([(fact.subject, fact.predicate, fact.value) for fact in triples],
                         [('aster', 'material', 'copper')])


class AssertionContextIntegrationTests(unittest.TestCase):
    def setUp(self):
        from test_rerank_store import ScopedDatabaseIntegrationTests
        ScopedDatabaseIntegrationTests.setUp(self)
        self.sequence = 0
        self.add('I live in Willow Bay.')

    def tearDown(self):
        from test_rerank_store import ScopedDatabaseIntegrationTests
        ScopedDatabaseIntegrationTests.tearDown(self)

    def add(self, text, role='user'):
        self.sequence += 1
        self.memory.add_messages('target', 'invented-assertion-context', [dict(
            role=role, content=text, timestamp=1735689600000 + self.sequence * 1000)])

    def state(self):
        with self.memory._connect() as connection:
            return [tuple(row) for row in connection.execute(
                "SELECT value,status FROM facts WHERE user_id='target' AND predicate='location' ORDER BY seq")]

    def test_report_question_hypothesis_and_old_event_cannot_retire_current_value(self):
        for text in NONASSERTIONS:
            self.add(text)
        self.assertEqual(self.state(), [('Willow Bay', 'active')])
        with self.memory._connect() as connection:
            originals = [row[0] for row in connection.execute(
                "SELECT content FROM items WHERE user_id='target' ORDER BY seq")]
        self.assertEqual(originals, ['I live in Willow Bay.'] + NONASSERTIONS)
        rows = self.memory.search('target', 'Where do I live now?', 100)
        joined = '\n'.join(row['content'] for row in rows)
        self.assertIn('Willow Bay', joined)
        self.assertNotIn('[CURRENT] self.location: Alder Bay', joined)

    def test_later_real_correction_retires_prior_value_after_reported_claim(self):
        self.add('She said "I live in Alder Bay."')
        self.add('Actually, I live in Rowan Bay.')
        self.assertEqual(self.state(), [('Willow Bay', 'stale'), ('Rowan Bay', 'active')])

    def test_direct_actual_past_event_correction_retains_existing_update_behavior(self):
        self.add('Actually I used to live in Alder Bay.')
        self.add('Actually, did I say I live in Alder Bay?')
        self.assertEqual(self.state(), [('Willow Bay', 'active')])
        self.add('Actually I moved to Shanghai last month.')
        self.assertEqual(self.state(), [('Willow Bay', 'stale'), ('Shanghai last month', 'active')])

    def test_hypothetical_retraction_does_not_withdraw_existing_real_assertion(self):
        self.add('I no longer live in Willow Bay if the permit is approved.')
        self.assertEqual(self.state(), [('Willow Bay', 'active')])
        self.add('I no longer live in Willow Bay.')
        self.assertEqual(self.state(), [('Willow Bay', 'stale')])


if __name__ == '__main__':
    unittest.main()
