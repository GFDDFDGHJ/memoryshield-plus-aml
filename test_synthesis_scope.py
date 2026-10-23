"""Invented complete-object summary contrasts; no DB or provider calls."""
import unittest

import facts as F
import query_plan as Q


POSITIVE = (
    'Summarize my history.',
    'Summarise our past conversations.',
    'Please recap my experiences so far.',
    'Can you summarize my memories?',
    'Could you please summarize myself?',
    'Give me an overview of my history.',
    'Provide a summary of our shared memories to date.',
    'Offer us a recap of our experiences as a whole.',
    'Summarize what you remember about me.',
    'Summarize everything you know about me.',
    'Give me a summary of what you have learned about myself.',
    'Summarize who I am.',
    '总结我的经历。',
    '请回顾一下我的过往。',
    '概述我们是怎样的人。',
    '总结我自己。',
)

NEGATIVE = (
    'Summarize my history of the pottery guild.',
    'Summarize my memories of the kiln.',
    'Summarize my memories from the island.',
    'Summarize my history with a supplier.',
    'Summarize my history concerning the breeding ponds.',
    'Summarize my memories for the kiln commission.',
    'Summarize everything you know about my kiln.',
    'Summarize my kiln memories.',
    'Give me an overview of the hatchery history.',
    'Give me an overview of my history about the hatchery.',
    'Provide a summary of what you know about my side business.',
    'Summarize my experiences in the reed marsh.',
    'Summarize my memories of 2011.',
    'Summarize my history "of the kiln".',
    'Summarize my Zephyr memories.',
    '总结我的经历中关于青云项目的部分。',
    '回顾我的过往里关于人工孵化的事。',
    '总结我的陶艺记忆。',
    '总结我的经历，不包含私人信息，并且只讲工作。',
    'Summarize my memories and only discuss work.',
    'Did I ask you to summarize my history?',
    'Did you summarize my memories?',
    'Do not summarize my history.',
    'Do not provide a summary of my history.',
    'Explain why I asked you to summarize my history.',
    'If I ask later, summarize my history.',
    'Would you explain what "summarize my history" means?',
    'Did I say "summarize myself" during our exchange?',
    'Never summarize my memories.',
    'Please do not summarize myself.',
    'Explain why the command summarize my history was rejected.',
    'Does “summarize my memories” refer to the kiln?',
    'What do you remember about me?',
    'Tell me about myself.',
    'Which history did I summarize?',
    'I need a protocol for summarizing memories.',
)


class SynthesisScopeTests(unittest.TestCase):
    def test_complete_personal_summary_object_supplies_global_scope(self):
        for query in POSITIVE:
            with self.subTest(query=query):
                self.assertEqual(Q.plan(query)['task'], 'global_synthesis')

    def test_complements_topics_quotations_and_noncommands_do_not_expand(self):
        for query in NEGATIVE:
            with self.subTest(query=query):
                self.assertNotEqual(Q.plan(query)['task'], 'global_synthesis')

    def test_named_typed_numeric_and_quoted_guards_are_still_applied(self):
        for query in (
            'Summarize my history about the Neris wheel.',
            'Summarize my history concerning protocol changes.',
            'Summarize my memories of 2021.',
            'Summarize my history "all conversations".',
            'Summarize all my conversations about an unknown hatchery.',
            'Give me an overview of my history with an unknown hatchery.',
        ):
            with self.subTest(query=query):
                self.assertNotEqual(Q.plan(query)['task'], 'global_synthesis')

    def test_bare_exclusions_do_not_supply_a_complete_summary_object(self):
        for query in (
            'Summarize key points I shared without personal details.',
            'Connect the things I mentioned without sensitive information.',
            '汇总这些聊天的要点，不包含私人信息。',
            'Summarize without private details.',
        ):
            with self.subTest(query=query):
                self.assertNotEqual(Q.plan(query)['task'], 'global_synthesis')

    def test_explicit_complete_scope_is_retained_after_field_exclusions(self):
        for query, categories, expected_task in (
            ('Summarize my history, omitting my home address.', {'address'}, 'global_synthesis'),
            ('Summarize what you remember about me without personal details.', {'private'}, 'global_synthesis'),
            ('总结我的经历，不要透露我的姓名和电话好吗？', {'name'}, 'topic_synthesis'),
        ):
            with self.subTest(query=query):
                # A retained unparsed positive tail remains conservative.
                self.assertEqual(Q.plan(query)['task'], expected_task)
                self.assertTrue(categories <= set(Q.plan(query)['excluded_categories']))

    def test_source_selection_honours_privacy_and_does_not_expand_a_new_topic(self):
        rows = [dict(rid=index, source_rid=index, seq=index+1,
                     user_id='invented', session_id='session-'+str(index),
                     role='user', created_at=1750000000+index*60,
                     content=text)
                for index, text in enumerate((
                    'I left the night class because the ferry stopped running.',
                    'I now make quiet clay bowls instead of carving timber.',
                    'My home address is 41 Invented Marsh Lane.',
                    'My storage access code is INVENTED-5092.',
                ))]
        query = 'Summarize my history, omitting my home address and access code.'
        units = F.context_units_for_query(rows, query)
        text = '\n'.join(unit['text'] for unit in units)
        self.assertIn('ferry stopped running', text)
        self.assertIn('quiet clay bowls', text)
        self.assertNotIn('41 Invented Marsh Lane', text)
        self.assertNotIn('INVENTED-5092', text)
        for query in ('Summarize my history about an unknown hatchery.',
                      'Summarize my memories of an unknown hatchery.'):
            with self.subTest(query=query):
                self.assertEqual(F.context_units_for_query(rows, query), [])


if __name__ == '__main__':
    unittest.main()
