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
    'She said I used to live in Alder Bay, and now I live in Pine Bay.',
    'She said I used to live in Alder Bay, and I live in Pine Bay now.',
    'Imagine I used to live in Alder Bay, and now I live in Pine Bay.',
    'It is not true that I used to live in Alder Bay, and now I live in Pine Bay.',
    'I used to live in Alder Bay if the stage allowed, and now I live in Pine Bay.',
    'Previously I lived in Alder Bay, and now I live in Pine Bay if approved.',
    'Previously I lived in Alder Bay, and now I live in Pine Bay?',
    'I used to live in Alder Bay, and now I live in Pine Bay provided my permit is approved.',
    'I used to live in Alder Bay, and now I live in Pine Bay, hypothetically speaking.',
    'I used to live in Alder Bay, and now I live in Pine Bay in this fictional scenario.',
    'She said since release 1.5 I live in Alder Bay.',
    'Last year in 2024.05 I live in Alder Bay.',
    'Example:\nMy name is Brindle.\nI live in Alder Bay.',
    'The visitor said:\nMy name is Brindle.\nI live in Alder Bay.',
    'Example:\nMy name is Brindle.\nI live in Alder Bay.\nI work at Cedar Lab.',
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
    ('I used to live in Alder Bay, and now I live in Pine Bay.', 'location', 'Pine Bay'),
    ('Previously I lived in Alder Bay, and now I live in Pine Bay.', 'location', 'Pine Bay'),
    ('Last year I left Alder Bay, and now I live in Pine Bay.', 'location', 'Pine Bay'),
    ('I used to work at Cedar Lab, and currently I work at Pine Lab.', 'work', 'Pine Lab'),
    ('I previously worked at Cedar Lab, and I work at Pine Lab now.', 'work', 'Pine Lab now'),
    ('Previously I lived in Alder Bay, and I live in Pine Bay now.', 'location', 'Pine Bay now'),
    ('Previously I lived in Alder Bay, and now I live in Example Bay.', 'location', 'Example Bay'),
    ('I used to work at Cedar Lab, and now I work at Fictional Publishing.', 'work', 'Fictional Publishing'),
    ('Previously my name was Brindle, and now my name is Lumen.', 'name', 'Lumen'),
    ('As I said yesterday, I live in Pine Bay.', 'location', 'Pine Bay'),
    ('我以前住在青湾，现在我住在白湾。', 'location', '白湾'),
    ('以前我住在青湾，现在我住在白湾。', 'location', '白湾'),
]


class AssertionContextTests(unittest.TestCase):
    def test_new_first_person_clause_is_not_part_of_a_literal_fact_value(self):
        cases = [
            ('I live in Northmere, and I work at Finch Studio.',
             [('location', 'Northmere'), ('work', 'Finch Studio')]),
            ('I work at Finch Studio, and I live in Northmere.',
             [('work', 'Finch Studio'), ('location', 'Northmere')]),
            ('My name is Avens, and I live in Northmere.',
             [('name', 'Avens'), ('location', 'Northmere')]),
            ('I live in Northmere and I work at Finch Studio.',
             [('location', 'Northmere'), ('work', 'Finch Studio')]),
            ('My name is Avens, my favorite color is amber.',
             [('name', 'Avens'), ('color', 'amber')]),
        ]
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual([(f.predicate, f.value) for f in F.extract_facts(source)], expected)

    def test_literal_commas_quotes_structured_values_and_rule_text_are_preserved(self):
        controls = [
            ('I live in Northmere, New Haven.', 'Northmere, New Haven'),
            ('My name is Avens, Jr.', 'Avens, Jr'),
            ('My name is "Avens, and I am unusual".', 'Avens, and I am unusual'),
            ('{"subject":"Aster","relation":"label","object":"Avens, and I am unusual"}',
             'Avens, and I am unusual'),
            ('I prefer Northmere, and I love Cedar Quay.', 'northmere and i love cedar quay'),
            ('Always include the units, and I am responsible for validation.',
             'Always include the units, and I am responsible for validation'),
        ]
        for source, expected in controls:
            with self.subTest(source=source):
                self.assertIn(expected, [f.value for f in F.extract_facts(source)])

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

    def test_header_scope_ends_at_blank_line_new_heading_or_direct_correction(self):
        for text in (
            'Example:\nMy name is Brindle.\nI live in Alder Bay.\n\nI live in Pine Bay.',
            'Example:\nMy name is Brindle.\nI live in Alder Bay.\nActual details:\nI live in Pine Bay.',
            'The visitor said:\nMy name is Brindle.\nI live in Alder Bay.\nActually, I live in Pine Bay.',
        ):
            with self.subTest(text=text):
                self.assertEqual([(f.predicate, f.value) for f in F.extract_facts(text)
                                  if f.kind != 'rule'], [('location', 'Pine Bay')])

    def test_direct_correction_ends_header_scope_for_following_lines(self):
        text = ('Example:\nMy name is Brindle.\nI live in Alder Bay.\n'
                'Correction: my name is Lumen.\nI live in Pine Bay.')
        self.assertEqual([(f.predicate, f.value) for f in F.extract_facts(text)
                          if f.kind != 'rule'], [('name', 'Lumen'), ('location', 'Pine Bay')])


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

    def test_compound_assertion_can_be_retracted_by_its_exact_location(self):
        source = 'I live in Northmere, and I work at Finch Studio.'
        self.add(source)
        self.add('I no longer live in Northmere.')
        self.assertEqual(self.state(), [('Willow Bay', 'stale'), ('Northmere', 'stale')])
        with self.memory._connect() as connection:
            work = connection.execute("SELECT value FROM facts WHERE user_id='target'"
                " AND predicate='work' AND status='active'").fetchall()
            original = connection.execute('SELECT content FROM items WHERE content=?', (source,)).fetchone()[0]
        self.assertEqual([row[0] for row in work], ['Finch Studio'])
        self.assertEqual(original, source)

    def test_compound_employment_retraction_leaves_location_intact(self):
        self.add('I work at Finch Studio, and I live in Northmere.')
        self.add('I no longer work at Finch Studio.')
        self.assertEqual(self.state(), [('Willow Bay', 'stale'), ('Northmere', 'active')])
        with self.memory._connect() as connection:
            work = connection.execute("SELECT value FROM facts WHERE user_id='target'"
                " AND predicate='work' AND status='active'").fetchall()
        self.assertEqual(work, [])

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

    def test_explicit_current_clause_retires_prior_location_after_historical_narration(self):
        self.add('Previously I lived in Alder Bay, and now I live in Pine Bay.')
        self.assertEqual(self.state(), [('Willow Bay', 'stale'), ('Pine Bay', 'active')])

    def test_explicit_current_name_survives_historical_name_clause(self):
        self.add('My name is Brindle.')
        self.add('Previously my name was Brindle, and now my name is Lumen.')
        with self.memory._connect() as connection:
            values = [tuple(row) for row in connection.execute(
                "SELECT value,status FROM facts WHERE user_id='target' AND predicate='name' ORDER BY seq")]
        self.assertEqual(values, [('Brindle', 'stale'), ('Lumen', 'active')])


if __name__ == '__main__':
    unittest.main()
