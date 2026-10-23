"""Literal long-source ranking controls, without any model or database calls."""
import copy
import unittest
from unittest.mock import Mock

from test_rerank_store import S, row


class RankingWindows(unittest.TestCase):
    def test_short_sources_keep_exact_wire_representation(self):
        source = row(1, 'The drying limit is 71.5 kPa.', role='user')[1]
        self.assertEqual(S._ranking_document('drying limit', source),
                         '[speaker role=user]\nThe drying limit is 71.5 kPa.')

    def test_tail_rule_and_exception_are_visible_without_editing_source(self):
        padding = 'The archive inventory lists cabinets, labels and ordinary shelves. ' * 90
        rule = ('The valve drying limit is 71.5 kPa. '
                'However, this applies only with the vent open. '
                'Otherwise, reject the observation instead of calculating a throughput.')
        source = row(1, padding + rule)[1]
        original = copy.deepcopy(source)
        rendered = S._ranking_document('Which valve drying limit applies?', source)
        for literal in ('71.5 kPa', 'only with the vent open', 'reject the observation'):
            self.assertIn(literal, rendered)
        self.assertLessEqual(len(rendered.encode('utf-8')), S.MAX_DOCUMENT_BYTES)
        self.assertEqual(source, original)

    def test_chinese_byte_bound_preserves_complete_qualification(self):
        source = row(1, '档案室里存放了普通柜子和标签。' * 220
                     + '灌注流量需要先减去0.75毫升。只有排气阀打开时才适用。否则拒绝这次测量。')[1]
        rendered = S._ranking_document('灌注流量怎样校准？', source)
        self.assertIn('0.75', rendered)
        self.assertIn('只有排气阀打开时才适用', rendered)
        self.assertIn('否则拒绝', rendered)
        self.assertLessEqual(len(rendered.encode('utf-8')), S.MAX_DOCUMENT_BYTES)

    def test_late_sources_are_distinguishable_on_wire_and_remain_whole(self):
        common = 'The archives contain routine reference material. ' * 100
        sources = [row(1, common + 'The dryer pressure rule permits 71.5 kPa.', role='user'),
                   row(2, common + 'The dryer pressure rule in this fictional example permits 900 kPa.', role='assistant')]
        before = copy.deepcopy(sources)
        memory = S.MemoryStore.__new__(S.MemoryStore)
        client = Mock()
        def rerank(query, documents):
            self.assertTrue(any('71.5 kPa' in text for text in documents))
            self.assertTrue(any('fictional example' in text for text in documents))
            self.assertTrue(all(len(text.encode('utf-8')) <= S.MAX_DOCUMENT_BYTES for text in documents))
            return [1, 0]
        client.rerank.side_effect = rerank
        memory._reranker = client
        result = memory._rerank_rows('Which dryer pressure rule was recorded?', sources)
        self.assertTrue(client.rerank.called)
        self.assertEqual(sources, before)
        self.assertEqual(result[0][1]['content'], sources[1][1]['content'])
        self.assertEqual(result[0][1]['role'], 'assistant')


if __name__ == '__main__':
    unittest.main()
