"""Fresh pure applicability-selection contracts; no DB, model or keys."""
import unittest

import state_evidence as S


class StateEvidenceTests(unittest.TestCase):
    def select(self, text, **assertion):
        fact = dict(subject='self', predicate='name', value='Eira')
        fact.update(assertion)
        return S.qualifications(fact, [dict(source_rid=73, role='user', seq=9,
            timestamp_origin='message.timestamp', created_at=123.0, content=text)])

    def test_completed_withdrawal_quotes_source_without_inference(self):
        text = 'My registered name Eira has been revoked; no replacement name is on file.'
        result = self.select(text)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['text'], text)
        self.assertEqual(result[0]['source_rid'], 73)
        self.assertEqual(result[0]['timestamp_origin'], 'message.timestamp')
        self.assertEqual(result[0]['reason'], 'explicit-self-withdrawal')
        self.assertNotIn('value', result[0])
        self.assertNotIn('status', result[0])

    def test_unambiguous_field_can_omit_the_old_value(self):
        self.assertTrue(self.select('My legal name has been rescinded.'))

    def test_nickname_does_not_qualify_a_distinct_name_attribute(self):
        self.assertEqual(self.select('My nickname Eira has been revoked.'), [])
        self.assertEqual(self.select('我的昵称Eira已经撤销。'), [])
        self.assertTrue(self.select('My nickname Eira has been revoked.', predicate='nickname'))

    def test_questions_are_not_completed_state_qualifications(self):
        for text in ('My registered name Eira has been revoked?', '我的登记姓名Eira已经撤销吗？',
                     'If the county registry approves the change, my name is Solin. '
                     'The county registry has granted approval?'):
            self.assertEqual(self.select(text), [])

    def test_same_actor_completed_conditional_pair_is_only_quoted(self):
        text = ('If the county registry approves the change, my name is Solin. '
                'The county registry has granted approval.')
        result = self.select(text)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['sentence_indexes'], [0, 1])
        self.assertEqual(result[0]['text'], text.replace('. The', '.\nThe'))
        self.assertEqual(result[0]['reason'], 'later-same-source-condition-outcome')
        self.assertNotIn('value', result[0])

    def test_denial_or_uncompleted_condition_does_not_qualify(self):
        prefix = 'If the county registry approves the change, my name is Solin. '
        for outcome in ('The county registry has denied the application.',
                        'The county registry declined the change.',
                        'The county registry has not granted approval.',
                        'The county registry might grant approval.',
                        'The county registry will grant approval.', ''):
            with self.subTest(outcome=outcome):
                self.assertEqual(self.select(prefix+outcome), [])

    def test_different_actor_object_and_target_do_not_qualify(self):
        for text in (
            'If the county registry approves the change, my name is Solin. The school has granted approval.',
            'If the county registry approves the change, my name is Solin. The county registry has approved the travel application.',
            'If the county registry approves the change, Doran\'s name is Solin. The county registry has granted approval.',
            'If the county registry approves the change, my name is Eira. The county registry has granted approval.',
            'My registered name Solin has been revoked.',
            'Doran\'s registered name Eira has been revoked.',
            'My daughter\'s name Eira has been revoked.',
        ):
            with self.subTest(text=text):
                self.assertEqual(self.select(text), [])

    def test_hypothetical_quoted_and_negated_withdrawal_do_not_qualify(self):
        for text in (
            'If the registry revokes my name, my registered name Eira will become invalid.',
            'My registered name Eira may be revoked.',
            'My registered name Eira has not been revoked.',
            'My registered name Eira was never revoked.',
            'Imagine my registered name Eira has been revoked.',
            'For example, my registered name Eira has been revoked.',
            '"My registered name Eira has been revoked."',
            'The quoted sentence says my registered name Eira has been revoked.',
            'I said my registered name Eira has been revoked.',
        ):
            with self.subTest(text=text):
                self.assertEqual(self.select(text), [])

    def test_chinese_explicit_completed_name_withdrawal(self):
        text = '我的登记姓名Eira已经被撤销，目前没有替代姓名。'
        self.assertEqual(self.select(text)[0]['text'], text)
        for negative in ('如果我的登记姓名Eira被撤销，就要重新申请。',
                         '我的登记姓名Eira没有被撤销。', '小禾的登记姓名Eira已经被撤销。',
                         '我的登记姓名Solin已经被撤销。'):
            self.assertEqual(self.select(negative), [])

    def test_multiline_example_header_and_trailing_disclaimer_are_not_self_updates(self):
        for text in (
            'Fictional example:\nMy registered name Eira has been revoked.',
            'Quote:\nMy registered name Eira has been revoked.',
            'My registered name Eira has been revoked. This is an illustration only.',
            '示例：\n我的登记姓名Eira已经被撤销。',
            'If the county registry approves the change, my name is Solin. '
            'The county registry has granted approval. This is a hypothetical script.',
        ):
            with self.subTest(text=text):
                self.assertEqual(self.select(text), [])

    def test_supported_attribute_is_bound_to_the_matching_field(self):
        self.assertTrue(self.select('My current appointment has been revoked.', predicate='role', value='Civic advisor'))
        self.assertFalse(self.select('My current appointment has been revoked.'))
        self.assertFalse(self.select('My registered name Eira has been revoked.', subject='daughter'))
        self.assertFalse(self.select('My registered name Eira has been revoked.', predicate='unrecognized'))

    def test_no_cross_source_condition_execution(self):
        result = S.qualifications(dict(subject='self', predicate='name', value='Eira'), [
            dict(source_rid=1, content='If the county registry approves the change, my name is Solin.'),
            dict(source_rid=2, content='The county registry has granted approval.'),
        ])
        self.assertEqual(result, [])

    def test_presegmented_original_text_and_limit_are_preserved(self):
        sentences = ['Unrelated introductory line.', 'My name Eira has been revoked; no replacement is recorded.']
        sources = [dict(source_rid=rid, sentences=sentences) for rid in range(5)]
        result = S.qualifications(dict(subject='self', predicate='name', value='Eira'), sources, limit=2)
        self.assertEqual([row['source_rid'] for row in result], [0, 1])
        self.assertEqual(result[0]['sentence_indexes'], [1])
        self.assertEqual(result[0]['text'], sentences[1])
        self.assertEqual(S.qualifications({}, sources, limit=0), [])


class ApplicabilityIntegrationTests(unittest.TestCase):
    def setUp(self):
        from test_rerank_store import ScopedDatabaseIntegrationTests
        ScopedDatabaseIntegrationTests.setUp(self)

    def tearDown(self):
        from test_rerank_store import ScopedDatabaseIntegrationTests
        ScopedDatabaseIntegrationTests.tearDown(self)

    def add_source(self, text, timestamp, role='user'):
        self.memory.add_messages('target', 'identity', [dict(role=role, content=text, timestamp=timestamp)])

    def test_later_withdrawal_qualifies_all_three_views_with_real_source_pointer(self):
        from test_rerank_store import S as M
        self.add_source('My name is Vesper.', 1735689600000)
        self.add_source('My name is Eira.', 1735689601000)
        self.add_source('I work at Fern Ceramics.', 1735689602000)
        revoked = 'My registered name Eira has been revoked; no replacement name is on file.'
        self.add_source(revoked, 1735689603000)
        with self.memory._connect() as conn:
            current = conn.execute("SELECT value FROM facts WHERE user_id='target' AND predicate='name' AND status='active'").fetchone()[0]
            rid = conn.execute("SELECT rid FROM items WHERE user_id='target' AND content=?", (revoked,)).fetchone()[0]
            views = ['\n'.join(row['text'] for row in M.fact_layer.change_history(conn, 'target', 'What is my name now?', 8)),
                     '\n'.join(row['text'] for row in M.fact_layer.match_facts(conn, 'target', 'my name', 8)),
                     M.fact_layer.build_profile(conn, 'target', 'Who am I?')]
        self.assertEqual(current, 'Eira')  # Selection does not invent a new stored value.
        for view in views:
            self.assertIn('CURRENT UNVERIFIED', view)
            self.assertIn(revoked, view)
            self.assertIn(f'source_rid={rid} ', view)
            self.assertNotIn('[CURRENT] self.name: Eira', view)
            self.assertNotIn('(current value: Eira)', view)

    def test_older_provided_withdrawal_arriving_late_does_not_qualify_new_assertion(self):
        from test_rerank_store import S as M
        self.add_source('My name is Vesper.', 1735689600000)
        self.add_source('My name is Eira.', 1735689603000)
        self.add_source('My registered name Eira has been revoked.', 1735689601000)
        with self.memory._connect() as conn:
            view = '\n'.join(row['text'] for row in M.fact_layer.change_history(conn, 'target', 'What is my name now?', 8))
        self.assertIn('[CURRENT] self.name: Eira', view)
        self.assertNotIn('CURRENT UNVERIFIED', view)

    def test_other_role_withdrawal_does_not_qualify_users_current_assertion(self):
        from test_rerank_store import S as M
        self.add_source('My name is Vesper.', 1735689600000)
        self.add_source('My name is Eira.', 1735689601000)
        self.add_source('My registered name Eira has been revoked.', 1735689603000, role='assistant')
        with self.memory._connect() as conn:
            view = '\n'.join(row['text'] for row in M.fact_layer.change_history(conn, 'target', 'What is my name now?', 8))
        self.assertIn('[CURRENT] self.name: Eira', view)
        self.assertNotIn('CURRENT UNVERIFIED', view)


if __name__ == '__main__':
    unittest.main()
