"""Independent literal execution-evidence checks; no model or stored memories."""
import copy
import unittest
from unittest.mock import Mock

from test_rerank_store import S, row


class ExecutionContextTests(unittest.TestCase):
    def test_repeated_mentions_do_not_hide_executable_policy_and_steps(self):
        background = ('The iris latch counter skip rules notebook catalogues ordinary shelves, '
                      'route labels and empty drawers, and states no operating decision. ')*65
        rules = ('Iris controller procedure: execute the instructions once in their written order. '
                 'Any instruction with a false precondition is skipped permanently; never return to it. '
                 'Rule A: if latch is closed, add 10 to counter. '
                 'Rule B: if counter is zero, close the latch. '
                 'Rule C: if latch is closed, add 1 to counter.')
        source = row(1, background + rules)[1]
        before = copy.deepcopy(source)
        query = 'Apply the iris latch counter instructions starting at latch=open and counter=0.'
        preview = S._query_preview(source['content'], query)
        wire = S._ranking_document(query, source)
        for evidence in ('once in their written order', 'false precondition is skipped permanently',
                         'never return to it', 'add 10 to counter', 'if counter is zero, close the latch',
                         'add 1 to counter'):
            self.assertIn(evidence, preview)
            self.assertIn(evidence, wire)
        self.assertLessEqual(len(wire.encode('utf-8')), S.MAX_DOCUMENT_BYTES)
        self.assertEqual(source, before)

    def test_failed_semantic_rank_keeps_sparse_method_near_dense_observation(self):
        ranked = [row(index, f'The Cobalt sensor reading {index} is listed in the cabinet log.')
                  for index in range(1, 25)]
        method = row(25, 'Cobalt calibration and normalization: subtract 0.35 from the sensor reading before scaling.')
        ranked.append(method)
        before = copy.deepcopy(ranked)
        memory = S.MemoryStore.__new__(S.MemoryStore)
        memory._reranker = Mock()
        query = 'Apply the earlier Cobalt calibration and normalization to this sensor reading.'
        for response in (None, [], [0, 0]):
            memory._reranker.rerank.return_value = response
            result = memory._rerank_rows(query, ranked)
            self.assertLessEqual(next(index for index, item in enumerate(result) if item[1]['rid']==25), 2)
            self.assertEqual({item[1]['rid'] for item in result}, set(range(1, 26)))
        self.assertEqual(ranked, before)

    def test_numeric_repetitions_are_not_silently_called_unique_observations(self):
        # Prioritizing literal evidence does not authorize rewriting repeated
        # measurements or attributing one source to another speaker.
        body = ('The sample reading was 2.50 mL. '*110
                + 'Kestrel sample calibration: subtract 0.25 mL before conversion. '
                  'Only clean vials are eligible. Otherwise discard the observation.')
        source = row(3, body, role='assistant')[1]
        before = copy.deepcopy(source)
        preview = S._ranking_document('What Kestrel calibration and eligibility govern a sample reading?', source)
        for literal in ('0.25 mL', 'Only clean vials are eligible', 'Otherwise discard'):
            self.assertIn(literal, preview)
        self.assertIn('[speaker role=assistant]', preview)
        self.assertEqual(source, before)


if __name__ == '__main__':
    unittest.main()
