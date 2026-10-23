"""Invented explicit-current-denial controls; no providers or production data."""
from __future__ import annotations

import unittest

import state_evidence as Q
import source_dependencies as D


POSITIVE_CASES = [
    ('name', 'Eira', 'My name is not Eira.'),
    ('name', 'Eira', 'My legal name is no longer Eira.'),
    ('name', 'Eira', "My registered name isn't Eira."),
    ('name', 'Eira', 'Correction: my official name is not "Eira".'),
    ('name', 'Eira', '我的姓名不是Eira。'),
    ('name', 'Eira', '我的当前名字不再是Eira。'),
    ('name', 'Eira', '更正：我的姓名不是“Eira”。'),
    ('nickname', 'Wren', 'My current nickname is no longer Wren.'),
    ('location', '17 Invented Linden Way', 'My current address is not 17 Invented Linden Way.'),
    ('work', 'Fern Ceramics', 'My current job is no longer Fern Ceramics.'),
    ('role', 'civic advisor', 'My current position is not civic advisor.'),
    ('condition', 'invented diagnosis', 'My recorded diagnosis is not invented diagnosis.'),
]

NEGATIVE_CASES = [
    ('My name was not Eira.', 'name', 'Eira'),
    ('Last year, my name was not Eira.', 'name', 'Eira'),
    ('My name is not Eira last year.', 'name', 'Eira'),
    ('My name will not be Eira.', 'name', 'Eira'),
    ('My name might not be Eira.', 'name', 'Eira'),
    ('If approved, my name is not Eira.', 'name', 'Eira'),
    ('My name is not Eira if the permit is approved.', 'name', 'Eira'),
    ('My name is not Eira?', 'name', 'Eira'),
    ('Is my name not Eira?', 'name', 'Eira'),
    ('"My name is not Eira."', 'name', 'Eira'),
    ('Quote:\nMy name is not Eira.', 'name', 'Eira'),
    ('My name is not Eira. This is a fictional example.', 'name', 'Eira'),
    ('I said my name is not Eira.', 'name', 'Eira'),
    ('My nickname is not Eira.', 'name', 'Eira'),
    ('My name is not Eira.', 'nickname', 'Eira'),
    ("My daughter's name is not Eira.", 'name', 'Eira'),
    ('My name is not Anna.', 'name', 'Ann'),
    ('My name is not Ann Marie.', 'name', 'Ann'),
    ('My name is not Eira Junior.', 'name', 'Eira'),
    ('My name is not Solin.', 'name', 'Eira'),
    ('Eira is not my legal name but my nickname.', 'nickname', 'Eira'),
    ('My nickname, not my legal name, is Eira.', 'name', 'Eira'),
    ('我的姓名曾经不是Eira。', 'name', 'Eira'),
    ('去年我的姓名不是Eira。', 'name', 'Eira'),
    ('我的姓名不是Eira吗？', 'name', 'Eira'),
    ('如果获批，我的姓名不是Eira。', 'name', 'Eira'),
    ('我的姓名不是Eira，除非申请通过。', 'name', 'Eira'),
    ('小禾的姓名不是Eira。', 'name', 'Eira'),
    ('我的昵称不是Eira。', 'name', 'Eira'),
    ('我的姓名不是Eira2。', 'name', 'Eira'),
]

DEPENDENCY_POSITIVE = [
    'The licensing panel approved the change.',
    'The licensing panel authorized the change.',
    'The licensing panel denied the change.',
    'The licensing panel declined the change.',
    'The licensing panel rejected the change.',
]
DEPENDENCY_NEGATIVE = [
    'The licensing panel approved the travel reimbursement.',
    "The licensing panel approved Ivo's change.",
    'The school panel approved the change.',
    'The licensing panel will approve the change.',
    'If the licensing panel approved the change, a new card would be issued.',
    'The licensing panel approved the change if the signature was present.',
    'The licensing panel approved the change?',
    '"The licensing panel approved the change."',
    'For example, the licensing panel approved the change.',
    'The licensing panel might have approved the change.',
    'The licensing panel did not approve the change.',
    'The licensing panel never approved the change.',
    'The licensing panel approved a different request.',
    'Did the licensing panel approve the change?',
    'The licensing panel approved the change to tax policy.',
    'The licensing panel denied the change for a different request.',
]


def dependency_case(module, outcome):
    sources = ['If the licensing panel approves the change, my name is Solin.'] + [
        f'Catalogue bin {index} holds cardboard.' for index in range(40)] + [outcome]
    return module.dependency_indexes(sources, {0})


def select(module, text, predicate='name', value='Eira'):
    return module.qualifications(dict(subject='self', predicate=predicate, value=value), [dict(
        source_rid=31, role='user', seq=4, created_at=123.0,
        timestamp_origin='message.timestamp', content=text)])


class ExplicitCurrentDenialTests(unittest.TestCase):
    def test_exact_current_self_field_value_denial_is_quoted_only(self):
        for predicate, value, text in POSITIVE_CASES:
            with self.subTest(text=text):
                results = select(Q, text, predicate, value)
                self.assertEqual(len(results), 1)
                self.assertEqual(results[0]['text'], text)
                self.assertEqual(results[0]['reason'], 'explicit-current-self-denial')
                self.assertEqual(results[0]['source_rid'], 31)
                self.assertNotIn('value', results[0])
                self.assertNotIn('status', results[0])

    def test_past_conditional_question_quote_other_value_or_field_is_unresolved(self):
        for text, predicate, value in NEGATIVE_CASES:
            with self.subTest(text=text):
                self.assertEqual(select(Q, text, predicate, value), [])

    def test_same_actor_object_simple_past_outcomes_survive_long_source(self):
        for text in DEPENDENCY_POSITIVE:
            with self.subTest(text=text):
                self.assertEqual(dependency_case(D, text), {41})

    def test_simple_past_outcome_does_not_join_other_scope_or_nonfactual_text(self):
        for text in DEPENDENCY_NEGATIVE:
            with self.subTest(text=text):
                self.assertEqual(dependency_case(D, text), set())

    def test_simple_past_outcome_respects_intervening_scope_and_private_barriers(self):
        condition = 'If the licensing panel approves the change, my name is Solin.'
        outcome = 'The licensing panel approved the change.'
        for barrier in ('Project Pelican has a separate identity selection.',
                        'My medical record was approved.'):
            with self.subTest(barrier=barrier):
                self.assertEqual(D.dependency_indexes([condition, barrier, outcome], {0}), set())


class ExplicitCurrentDenialIntegrationTests(unittest.TestCase):
    def setUp(self):
        from test_rerank_store import ScopedDatabaseIntegrationTests
        ScopedDatabaseIntegrationTests.setUp(self)
        self.add('I work at Fern Ceramics.', 1735689598000)

    def tearDown(self):
        from test_rerank_store import ScopedDatabaseIntegrationTests
        ScopedDatabaseIntegrationTests.tearDown(self)

    def add(self, text, timestamp, role='user', user='target'):
        self.memory.add_messages(user, 'invented-current-state', [dict(
            role=role, content=text, timestamp=timestamp)])

    def views(self):
        from test_rerank_store import S
        with self.memory._connect() as connection:
            return ['\n'.join(row['text'] for row in S.fact_layer.change_history(
                        connection, 'target', 'What is my name now?', 8)),
                    '\n'.join(row['text'] for row in S.fact_layer.match_facts(
                        connection, 'target', 'my name', 8)),
                    S.fact_layer.build_profile(connection, 'target', 'Who am I?')]

    def test_later_exact_denial_unverifies_three_views_without_replacement_assertion(self):
        self.add('My name is Vesper.', 1735689599000)
        self.add('My name is Eira.', 1735689600000)
        denial = 'My name is not Eira.'
        self.add(denial, 1735689603000)
        with self.memory._connect() as connection:
            values = [(row[0], row[1]) for row in connection.execute(
                "SELECT value,status FROM facts WHERE user_id='target' AND predicate='name'")]
            rid = connection.execute('SELECT rid FROM items WHERE content=?', (denial,)).fetchone()[0]
        self.assertEqual([value for value, status in values if status == 'active'], ['Eira'])
        self.assertFalse(any(value == 'not Eira' for value, _ in values))
        for text in self.views():
            self.assertIn('CURRENT UNVERIFIED', text)
            self.assertIn(denial, text)
            self.assertIn(f'source_rid={rid} ', text)
            self.assertNotIn('[CURRENT] self.name: Eira', text)
            self.assertNotIn('(current value: Eira)', text)

    def test_provided_older_denial_arriving_late_does_not_qualify_newer_assertion(self):
        self.add('My name is Vesper.', 1735689599000)
        self.add('My name is Eira.', 1735689603000)
        self.add('My legal name is not Eira.', 1735689601000)
        self.assertTrue(any('[CURRENT] self.name: Eira' in text for text in self.views()))
        self.assertTrue(all('CURRENT UNVERIFIED' not in text for text in self.views()))

    def test_other_role_or_user_does_not_qualify_users_assertion(self):
        self.add('My name is Vesper.', 1735689599000)
        self.add('My name is Eira.', 1735689600000)
        self.add('My legal name is not Eira.', 1735689603000, role='assistant')
        self.add('My legal name is not Eira.', 1735689604000, user='foreign')
        self.assertTrue(any('[CURRENT] self.name: Eira' in text for text in self.views()))
        self.assertTrue(all('CURRENT UNVERIFIED' not in text for text in self.views()))

    def test_past_denial_does_not_qualify_current_state(self):
        self.add('My name is Vesper.', 1735689599000)
        self.add('My name is Eira.', 1735689600000)
        self.add('Last year, my legal name was not Eira.', 1735689603000)
        self.assertTrue(any('[CURRENT] self.name: Eira' in text for text in self.views()))
        self.assertTrue(all('CURRENT UNVERIFIED' not in text for text in self.views()))


if __name__ == '__main__':
    unittest.main()
