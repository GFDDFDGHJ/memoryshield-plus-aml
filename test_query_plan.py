"""Invented positive/negative compositional query plans; no model calls."""
import unittest

import query_plan as Q


class QueryPlanTests(unittest.TestCase):
    def test_english_exclusion_is_not_a_positive_topic(self):
        result = Q.plan('Summarize work and travel choices, omitting home addresses and access codes.')
        self.assertEqual(result['excluded_categories'], ['address', 'access_code'])
        self.assertEqual(result['positive_query'], 'Summarize work and travel choices.')
        self.assertEqual(result['task'], 'topic_synthesis')

    def test_chinese_exclusion_keeps_the_requested_task(self):
        result = Q.plan('回顾我们以往的聊天，但是不要透露我的住址和门禁密码。')
        self.assertEqual(set(result['excluded_categories']), {'address', 'access_code'})
        self.assertNotIn('住址', result['positive_query'])
        self.assertNotIn('门禁', result['positive_query'])
        self.assertIn('回顾', result['positive_query'])

    def test_exclusion_list_stops_before_a_new_positive_clause(self):
        result = Q.plan('Give an overview without mentioning my email or phone number, and explain the job changes.')
        self.assertEqual(set(result['excluded_categories']), {'email', 'phone'})
        self.assertIn('explain the job changes', result['positive_query'])
        self.assertNotIn('email', result['positive_query'])

    def test_command_forms_and_sensitive_record_categories(self):
        for query, expected in (
            ('Never reveal my passport number; summarize the journey.', 'identity'),
            ("Don't include my salary, but compare the arrangements.", 'financial'),
            ('Exclude medical records from the overview.', 'medical'),
            ('省略我的健康信息，然后回顾对话。', 'medical'),
            ('别提供我的电子邮箱，说明新的约定。', 'email'),
            ('Summarize my experiences without sensitive information.', 'private'),
            ('Please omit my address and compare my career decisions.', 'address'),
            ('Do not disclose my diagnosis; summarize the journey.', 'medical'),
        ):
            with self.subTest(query=query):
                self.assertIn(expected, Q.plan(query)['excluded_categories'])

    def test_pure_privacy_command_has_an_empty_positive_query(self):
        result = Q.plan('不要透露我的地址。')
        self.assertEqual(result['positive_query'], '')
        self.assertEqual(result['task'], 'ordinary')
        self.assertEqual(result['excluded_categories'], ['address'])

    def test_factual_negation_is_preserved_verbatim(self):
        for query in ('Which permit was not renewed, and why?',
                      'Why was the phone number not updated?',
                      'What did I choose without support from my partner?',
                      '哪份合同没有续签？', '我的地址为什么没有更新？',
                      'What is the difference between not disclosing an address and not renewing a permit?'):
            with self.subTest(query=query):
                result = Q.plan(query)
                self.assertEqual(result['positive_query'], query)
                self.assertEqual(result['excluded_categories'], [])

    def test_quoted_commands_do_not_become_current_exclusions(self):
        for query in ('Did I say "do not disclose my address" in the earlier discussion?',
                      '我之前说过“不要透露我的地址”吗？'):
            self.assertEqual(Q.plan(query)['excluded_categories'], [])

    def test_integration_of_memory_choices_is_global(self):
        for query in ('Connect the reasons behind the decisions I have described.',
                      'Reflect on the combination of our discussions and my priorities.',
                      'What does the combination of our discussions reveal about my priorities?',
                      'Draw a unified portrait of my choices over our exchanges.',
                      'Summarize how practical constraints shaped my life choices.',
                      'Summarize the important things I have told you.',
                      'What broad themes emerge from my experience so far?',
                      '把这些聊天串起来，讲讲我一路以来的取舍和变化。',
                      '跨领域看，我这些选择背后最看重的是什么？'):
            with self.subTest(query=query):
                self.assertEqual(Q.plan(query)['task'], 'global_synthesis')

    def test_explicit_history_range_and_personal_facets(self):
        for query in ('Summarize all my conversations.', 'Give an overview across all sessions.',
                      'Integrate my work, health, learning and family choices.'):
            self.assertEqual(Q.plan(query)['task'], 'global_synthesis')

    def test_integrated_view_requires_operation_and_integrated_object(self):
        for query in ('Give me a consolidated picture of my work, travel, health, study, hobbies, family, housing, and volunteering discussions.',
                      'Provide an integrated account of my work, health and family choices.',
                      'Offer a holistic assessment of the decisions I have described.',
                      'Create one unified understanding of my life choices.'):
            with self.subTest(query=query):
                self.assertEqual(Q.plan(query)['task'], 'global_synthesis')
        self.assertEqual(Q.plan('Give me a picture of a consolidated turbine.' )['task'], 'ordinary')
        self.assertEqual(Q.plan('Provide an integrated view of Zephyr turbine experiments.')['task'], 'topic_synthesis')

    def test_nominal_integration_question_has_a_relational_predicate(self):
        for query in ('What does the combination of our discussions reveal about my priorities?',
                      'How can the synthesis of our earlier experiences indicate my values?',
                      'What can the integration of our exchanges suggest about my life choices?'):
            with self.subTest(query=query):
                self.assertEqual(Q.plan(query)['task'], 'global_synthesis')
        self.assertEqual(Q.plan('What does the combination of observations reveal about Zephyr turbine?')['task'], 'topic_synthesis')
        self.assertEqual(Q.plan('Did I mention the word combination in our discussions?')['task'], 'ordinary')

    def test_broad_private_exclusion_needs_an_independent_positive_scope(self):
        for query in ('Summarize key points I shared without personal details.',
                      'Connect the things I mentioned without sensitive information.',
                      '汇总这些聊天的要点，不包含私人信息。'):
            with self.subTest(query=query):
                self.assertNotEqual(Q.plan(query)['task'], 'global_synthesis')
                self.assertEqual(Q.plan(query)['excluded_categories'], ['private'])
        self.assertEqual(Q.plan('Summarize all my conversations without personal details.')['task'], 'global_synthesis')
        self.assertEqual(Q.plan('Reflect on my life choices without personal details.')['task'], 'global_synthesis')
        topic = Q.plan('Summarize the Zephyr turbine project without personal details.')
        self.assertEqual(topic['task'], 'topic_synthesis')
        self.assertEqual(topic['reason'], 'explicit-topic-bound')
        domain = Q.plan('Summarize my career choices without personal details.')
        self.assertEqual(domain['task'], 'topic_synthesis')
        self.assertEqual(domain['reason'], 'explicit-topic-bound')

    def test_single_life_domain_stays_topic_scoped(self):
        for query in ('Summarize my career decisions.', 'Compare my work and family choices.',
                      'Summarize the important medical things I told you.',
                      'Summarize the professional choices I described.',
                      'Connect the family-care decisions I shared.',
                      'Summarize my professional compromises and family-care decisions.',
                      'Reflect on the educational choices I shared.', '总结我的职业选择。'):
            self.assertEqual(Q.plan(query)['task'], 'topic_synthesis')

    def test_named_or_typed_topics_do_not_promote_to_global(self):
        for query in ('Summarize the Zephyr cryogenic turbine maintenance.',
                      'Connect the choices I described for the Zephyr turbine.',
                      'Summarize my life choices about the unknown project.',
                      'Summarize all my conversations about permit 432.',
                      '总结我的人生中关于青云计划的变化。',
                      '整合我在青云实验中的取舍。'):
            with self.subTest(query=query):
                self.assertEqual(Q.plan(query)['task'], 'topic_synthesis')

    def test_ordinary_factual_queries_do_not_become_synthesis(self):
        for query in ('Why does the herbarium use photographs?', 'Who owns the relay?',
                      'What did I say about the permit that was not renewed?', '我的职位是什么？'):
            self.assertEqual(Q.plan(query)['task'], 'ordinary')

    def test_exclusion_removal_happens_before_scope_guard(self):
        result = Q.plan('Connect the reasons behind my life choices without my API keys or passport number.')
        self.assertEqual(result['task'], 'global_synthesis')
        self.assertEqual(set(result['excluded_categories']), {'access_code', 'identity'})

    def test_excluded_text_requires_a_field_value_or_format(self):
        for text, category in (
            ('My home address is 47 Invented Fern Lane.', 'address'),
            ('Storage access code: INVENTED-48271.', 'access_code'),
            ('诊断结果为需要复查。', 'medical'),
            ('联系邮箱是 invented@example.invalid。', 'email'),
            ('Salary is 4200 credits monthly.', 'financial'),
            ('Passport number: INVENTED-ABC-871.', 'identity'),
            ('Call (212) 555-0198 after the inspection.', 'phone'),
            ('I now live at 47 Invented Fern Lane.', 'address'),
        ):
            with self.subTest(text=text):
                self.assertTrue(Q.excluded_text(text, [category]))

    def test_excluded_text_keeps_protective_rules_and_unrelated_units(self):
        for text in ('Never disclose private addresses.', 'Always protect personal information.',
                     'The address field is not required.', 'The phone number was not updated.',
                     'The medical record format has two columns.', 'Mass=4200 kg; delay=871 ms.',
                     '每次回答都不要透露私人地址。', '以后回复必须省略门禁密码。'):
            with self.subTest(text=text):
                self.assertFalse(Q.excluded_text(text, ['private']))
        self.assertFalse(Q.excluded_text('A password is required.', ['access_code']))
        self.assertTrue(Q.excluded_text('Salary is 4.5 units hourly.', ['financial']))

    def test_category_does_not_hide_other_requested_properties(self):
        self.assertFalse(Q.excluded_text('I work at Fern Ceramics.', ['address', 'access_code']))
        self.assertFalse(Q.excluded_text('My home address is 47 Invented Fern Lane.', ['medical']))
        self.assertFalse(Q.excluded_text('My diagnosis is a shoulder sprain.', ['address']))
        self.assertTrue(Q.excluded_text('My diagnosis is a shoulder sprain.', ['private']))
        self.assertFalse(Q.excluded_text('The server address is 10.0.0.8.', ['address']))
        self.assertTrue(Q.excluded_text('My home address is 47 Invented Fern Lane.', 'address'))


if __name__ == '__main__':
    unittest.main()
