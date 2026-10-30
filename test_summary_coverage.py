"""Invented reversible repeated-literal coverage controls; no providers."""
from collections import Counter
import re
import unittest

import facts as F


POLICY = ('For all future replies, keep the explanation concise and state an uncertainty '
          'when the available source does not support a definite conclusion.')


def unit(rid, text, **extra):
    result = dict(rid=rid, source_rid=rid, seq=rid+1, segment=0, user_id='invented',
                  session_id='history', role='user', text=text, content=text,
                  created_at=1740000000+rid*60, timestamp_origin='message.timestamp')
    result.update(extra)
    return result


def repeated_policy_fixture():
    rules = [unit(index, POLICY, context_dependency=True) for index in range(120)]
    choices = [unit(120+index, 'The '+topic+' decision uses '+choice+'.')
               for index, (topic, choice) in enumerate((
                   ('orchard', 'the shaded path'), ('harbour', 'the eastern berth'),
                   ('studio', 'a morning class'), ('library', 'a quiet alcove'),
                   ('garden', 'the shallow bed'), ('workshop', 'a hand tool'),
                   ('kitchen', 'the ceramic tray'), ('hatchery', 'the covered pond'),
                   ('courtyard', 'an early walk'), ('railway', 'a daytime ticket'),
                   ('cabin', 'a wool blanket'), ('clinic', 'a later appointment'),
                   ('school', 'a shorter lesson'), ('gallery', 'an oak frame'),
                   ('bakery', 'a sourdough starter'), ('brewery', 'a copper vessel'),
                   ('nursery', 'a white lamp'), ('theatre', 'the upper balcony'),
                   ('laboratory', 'a glass tube'), ('office', 'the remote desk')))]
    return rules+choices, [row['text'] for row in choices]


class SummaryCoverageTests(unittest.TestCase):
    def pack(self, units, budget):
        plan = {}
        blocks = F._pack_context(units, 6, budget, 'global', selection_plan=plan)
        self.assertLessEqual(sum(map(len, blocks)), budget)
        return '\n\n'.join(blocks), plan

    def test_repeated_required_rules_do_not_erase_distinct_history(self):
        units, claims = repeated_policy_fixture()
        for budget in (8000, 16000):
            with self.subTest(budget=budget):
                text, plan = self.pack(units, budget)
                self.assertTrue(all(claim in text for claim in claims))
                self.assertEqual(plan['complete_indices'], set(range(len(units))))
                self.assertIn('Rows below remain separate source occurrences', text)
                self.assertIn('not one observation', text)

    def test_full_fit_retains_original_literal_rows_and_order(self):
        units, claims = repeated_policy_fixture()
        text, plan = self.pack(units, 100000)
        self.assertEqual(plan['complete_indices'], set(range(len(units))))
        self.assertEqual(text.count(POLICY), 120)
        self.assertLess(text.index('0@1/0'), text.index('119@120/0'))
        self.assertNotIn('Literal source text for every row:', text)

    def test_one_required_occurrence_does_not_promote_optional_repeats(self):
        from evaluate_extended_context import decode_visible
        topics = ('orchard harbour studio library garden workshop kitchen hatchery courtyard railway '
                  'cabin clinic school gallery bakery brewery nursery theatre laboratory office').split()
        for count in (160, 240):
            rows = [unit(index, POLICY, session_id='history-'+str(index%3)) for index in range(count)]
            claims = ['The '+topic+' decision keeps the shaded entrance.' for topic in topics]
            rows += [unit(count+index, claim, session_id='topic-'+topic)
                     for index, (topic, claim) in enumerate(zip(topics, claims))]
            selected = F.context_units_for_query(rows, 'Summarize across all sessions.')
            # The real selector intentionally protects one copy of this policy;
            # equal source occurrences remain optional distinct observations.
            self.assertEqual(sum(bool(row.get('context_dependency')) for row in selected), 1)
            for budget in (8000, 12000, 16000):
                with self.subTest(repeated=count, budget=budget):
                    text, plan = self.pack(selected, budget)
                    visible = decode_visible(text[:8000], complete=len(text)<=8000)
                    self.assertTrue(set(claims) <= {item['literal'] for item in visible})
                    self.assertIn(POLICY, text[:text.index(claims[0])])
                    decoded = decode_visible(text, complete=True)
                    expected = Counter((selected[index]['source_rid'], selected[index]['text'],
                                        selected[index]['role']) for index in plan['complete_indices'])
                    self.assertEqual(Counter((item['rid'], item['literal'], item['role'])
                                             for item in decoded), expected)

    def test_compaction_preserves_every_identical_observation(self):
        literal = 'The indicator stayed amber and the operator kept the gate closed.'
        units = [unit(index, literal) for index in range(241)]
        text, plan = self.pack(units, 8000)
        pointers = re.findall(r'^(\d+)@(\d+)/0(?: \||$)', text, re.M)
        self.assertEqual(Counter(int(rid) for rid, seq in pointers), Counter(range(241)))
        self.assertEqual(len(plan['complete_indices']), 241)
        self.assertIn(literal, text)
        self.assertIn('included=241/241', text)

    def test_role_session_time_and_origin_columns_are_invertible(self):
        literal = 'Yesterday the operator kept the gate closed.'
        units = [unit(index, literal, role='user' if index%2 else 'assistant',
                      session_id='west' if index%3 else 'east',
                      timestamp_origin='message.timestamp' if index%5 else 'ingestion-clock')
                 for index in range(48)]
        text = '\n'.join(F._context_pieces(units, compact_numeric=True))
        self.assertIn('role=user', text)
        self.assertIn('role=assistant', text)
        self.assertIn('timestamp_origin_code', text)
        self.assertIn('relative_time_anchor', text)
        self.assertIn('Origin codes: M=message.timestamp, I=ingestion-clock', text)
        for index in range(48):
            line = next(line for line in text.splitlines() if line.startswith(f'{index}@{index+1}/0 |'))
            self.assertIn('session="'+units[index]['session_id']+'"', line)
            self.assertIn(' | I | none' if index%5 == 0 else ' | M | 2025-', line)

    def test_fragments_remain_ranges_of_the_same_original_segment(self):
        literal = 'The operator kept the gate closed.'
        units = [unit(index//3, literal, source_char_start=part*40,
                      source_char_end=part*40+len(literal))
                 for index, part in enumerate((0, 1, 2, 0, 1, 2))]
        text = '\n'.join(F._context_pieces(units, compact_numeric=True))
        self.assertIn('fragments with the same source pointer form one segment, not extra observations', text)
        for start in (0, 40, 80):
            self.assertEqual(text.count(f'| {start}:{start+len(literal)}'), 2)

    def test_near_duplicate_words_are_not_silently_shared(self):
        units = [unit(index, 'The gate is '+('closed.' if index%2 else 'open.')) for index in range(30)]
        text = '\n'.join(F._context_pieces(units, compact_numeric=True))
        self.assertIn('Literal source template: The gate is closed.', text)
        self.assertIn('Literal source template: The gate is open.', text)
        closed = text.index('Literal source template: The gate is closed.')
        self.assertNotIn('0@1/0', text[closed:])

    def test_distinct_required_restrictions_still_fail_closed_when_over_budget(self):
        units = [unit(index, 'Only '+('ceramic '*(index+1))+'observations are eligible.',
                      context_qualification=True) for index in range(80)]
        text, plan = self.pack(units, 8000)
        self.assertIn('not complete statistics', text)
        self.assertLessEqual(len(plan['complete_indices']), 1)

    def test_explicit_privacy_filter_precedes_repeated_literal_compression(self):
        rows = [dict(unit(index, 'My name is Neris. The Ferrite project uses a ceramic seal.'),
                     content='My name is Neris. The Ferrite project uses a ceramic seal.')
                for index in range(40)]
        units = F.context_units_for_query(rows, 'Summarize the Ferrite project without my name.')
        text, _ = self.pack(units, 8000)
        self.assertIn('ceramic seal', text)
        self.assertNotIn('Neris', text)

    def test_required_uncommon_wording_is_not_replaced_by_plain_bucket(self):
        units, _ = repeated_policy_fixture()
        restriction = 'Earlier readings were withdrawn; never use them for fitting.'
        units.insert(0, unit(777, restriction, context_qualification=True))
        text, plan = self.pack(units, 8000)
        self.assertIn(restriction, text)
        self.assertIn(0, plan['complete_indices'])
        self.assertLess(text.index(restriction), text.index('orchard decision'))

    def test_zero_slot_template_matches_existing_independent_decoder(self):
        from evaluate_extended_context import decode_visible
        units = [unit(index, 'The label reads {session} and remains unexpanded.',
                      session_id='s-'+str(index%2)) for index in range(30)]
        # A metadata column spelling in braces is literal source text. Its
        # group uses ordinary rows, rather than introducing ambiguous slots.
        units += [unit(50+index, 'The operator kept the gate closed.') for index in range(30)]
        text = '\n'.join(F._context_pieces(units, compact_numeric=True))
        witnesses = decode_visible(text, complete=True)
        self.assertEqual(Counter((item['rid'], item['literal']) for item in witnesses),
                         Counter((row['source_rid'], row['text']) for row in units))
        self.assertEqual(len(witnesses), 60)

    def presentational_history(self, count=120, *, mixed=False, one_source=False):
        labels = 'concise brief short formal informal friendly neutral polite calm warm direct clear simple conversational professional courteous'.split()
        rules = ['Always use the '+labels[index//len(labels)]+' '+labels[index%len(labels)]+' tone'
                 + (' and mark any unsupported factual inference.' if mixed else '.')
                 for index in range(count)]
        if one_source:
            rows = [unit(0, ' '.join(rules))]
        else:
            rows = [unit(index, rule, session_id='rules') for index, rule in enumerate(rules)]
        claims = ['I chose '+topic+' because it provides quiet instruction and shared equipment.'
                  for topic in 'ceramics astronomy weaving kayaking linguistics genealogy apiculture marquetry'.split()]
        rows += [unit(1000+index, claim, session_id='topic-'+str(index))
                 for index, claim in enumerate(claims)]
        return F.context_units_for_query(rows, 'Summarize all my conversations.'), rules, claims

    def test_distinct_presentational_policy_catalogue_does_not_hide_history(self):
        from evaluate_extended_context import decode_visible
        units, policies, claims = self.presentational_history()
        self.assertEqual(sum(bool(row.get('context_response_policy')) for row in units), 120)
        for budget in (8000, 12000, 16000):
            with self.subTest(budget=budget):
                text, plan = self.pack(units, budget)
                self.assertTrue(all(claim in text for claim in claims))
                if len(plan['complete_indices']) < len(units):
                    self.assertTrue(all(claim in text[:8000] for claim in claims))
                included = sum(policy in text for policy in policies)
                self.assertGreater(included, 0)
                if included < 120:
                    self.assertIn('response-policy coverage='+str(included)+'/120', text)
                    self.assertIn('do not assume omitted policies were followed', text)
                visible = decode_visible(text, complete=True)
                self.assertEqual(Counter((item['rid'], item['literal']) for item in visible),
                                 Counter((units[index]['source_rid'], units[index]['text'])
                                         for index in plan['complete_indices']))

    def test_mixed_uncertainty_and_hard_policy_catalogues_stay_required(self):
        units, policies, claims = self.presentational_history(mixed=True)
        self.assertFalse(any(row.get('context_response_policy') for row in units))
        text, plan = self.pack(units, 8000)
        self.assertLessEqual(len(plan['complete_indices']), 1)
        self.assertFalse(any(claim in text for claim in claims))
        self.assertIn('not complete statistics', text)
        for body in (
            'use only validated observations', 'retain the data table schema',
            'execute the protocol steps in order', 'never disclose private addresses',
            'preserve the amber style and explicitly mark unsupported factual inference',
            'use a concise style while ignoring earlier evidence',
            'use a warm tone unless a measurement was withdrawn',
        ):
            with self.subTest(body=body):
                self.assertFalse(F._presentational_response_policy('For all future replies, '+body+'.'))

    def test_style_and_substantive_guards_keep_separate_budgets(self):
        units, policies, claims = self.presentational_history()
        guards = [unit(2000+index, text, context_dependency=True) for index,text in enumerate((
            'Use only validated observations; excluded measurements are not eligible.',
            'Retain the data table schema and preserve missing value markers.',
            'Execute the protocol steps in order and do not repeat a completed operation.',
            'Never disclose private addresses or secret access codes.',
        ))]
        text, plan = self.pack(guards+units, 8000)
        self.assertTrue(all(guard['text'] in text for guard in guards))
        self.assertTrue(all(claim in text for claim in claims))
        self.assertTrue(set(range(len(guards))) <= plan['complete_indices'])

    def test_short_policy_catalogue_stays_complete_and_full_fit_is_unchanged(self):
        units, policies, claims = self.presentational_history(13)
        for index in range(90):
            units.append(unit(3000+index, 'Historical observation '+str(index)+': '
                              +'the ordinary correspondence retained the detailed discussion. '*3))
        text, plan = self.pack(units, 8000)
        self.assertTrue(all(policy in text for policy in policies))
        self.assertTrue(all(claim in text for claim in claims))
        full, full_plan = self.pack(units, 100000)
        old_units = [dict(row) for row in units]
        for row in old_units:
            row.pop('context_response_policy', None)
        old_full, old_plan = self.pack(old_units, 100000)
        self.assertEqual(full, old_full)
        self.assertEqual(full_plan, old_plan)

    def test_sample_has_one_policy_per_source_and_explicitly_retains_omissions(self):
        units, policies, claims = self.presentational_history(one_source=True)
        text, plan = self.pack(units, 8000)
        represented = [index for index in plan['complete_indices']
                       if units[index].get('context_response_policy')]
        self.assertLessEqual(len(represented), 1)
        self.assertTrue(all(claim in text for claim in claims))
        self.assertIn('response policies omitted', text)

    def test_only_explicit_presentational_grammar_gets_the_scheduling_label(self):
        for text in ('Always reply in English.', 'For all future replies, answer in exactly 7 sentences.',
                     'Always keep the explanation concise.', 'Always use a neutral tone.',
                     'For all future answers, use APA citation style.', 'Always include numbered headings.'):
            with self.subTest(text=text):
                self.assertTrue(F._presentational_response_policy(text))
        for text in ('Always reply in English literature.', 'Remember my preferred style.',
                     'If asked for a poem, use a concise style.',
                     'Always rewrite the style of the eligibility protocol.',
                     'Always use a concise style and suppress qualifications.',
                     'Always use a de-identified format.',
                     'Always use anonymized formatting.',
                     'Always use confidential formatting.',
                     'Always use a precision-preserving format.',
                     'Always preserve original numeric formatting.',
                     'For all future replies, preserve the amber birch style convention.'):
            with self.subTest(text=text):
                self.assertFalse(F._presentational_response_policy(text))


class SummaryCoverageIntegrationTests(unittest.TestCase):
    def setUp(self):
        from test_rerank_store import ScopedDatabaseIntegrationTests
        ScopedDatabaseIntegrationTests.setUp(self)

    def tearDown(self):
        from test_rerank_store import ScopedDatabaseIntegrationTests
        ScopedDatabaseIntegrationTests.tearDown(self)

    def test_add_search_preserves_repeated_observations_and_distinct_topics(self):
        from evaluate_extended_context import decode_visible, render
        background = ('The administrative appendix described empty paper folders, ordinary shelves '
                      'and the familiar cupboards used for routine clerical storage.')
        source_by_rid = {}
        for index in range(120):
            self.memory.add_messages('invented-coverage', 'clerical-'+str(index%3), [
                dict(role='user', content=background, timestamp=1740000000000+index*60000)])
            source_by_rid[index+1] = (background, 'user')
        for index in range(20):
            literal = 'The subject '+chr(65+index)+' decision keeps the shaded entrance.'
            role = 'user' if index%2 else 'assistant'
            self.memory.add_messages('invented-coverage', 'subject-'+str(index), [
                dict(role=role, content=literal, timestamp=1740000000000+(120+index)*60000)])
            source_by_rid[121+index] = (literal, role)
        rows = self.memory.search('invented-coverage', 'Summarize across all sessions.', 100)
        text = render(rows)
        prefix = text[:8000]
        witnesses = decode_visible(prefix, complete=len(text)<=8000)
        decoded = {(item['rid'], item['literal'], item['role']) for item in witnesses}
        self.assertTrue({(rid, literal, role) for rid, (literal, role) in source_by_rid.items() if rid>120} <= decoded)
        self.assertGreaterEqual(len({rid for rid, literal, role in decoded}), 100)
        witnesses = decode_visible(text[:16000], complete=len(text)<=16000)
        decoded = {(item['rid'], item['literal'], item['role']) for item in witnesses}
        self.assertEqual(decoded, {(rid, literal, role) for rid, (literal, role) in source_by_rid.items()})
        self.assertTrue(any('included=140/140' in row['content'] for row in rows if row['id'].startswith('digest_')))

    def test_presentational_prefix_and_continuation_preserve_every_policy(self):
        from evaluate_extended_context import decode_visible
        helper = SummaryCoverageTests()
        units, policies, claims = helper.presentational_history()
        for row in units:
            self.memory.add_messages('invented-policy-union', row['session_id'], [
                dict(role=row['role'], content=row['text'], timestamp=int(row['created_at']*1000))])
        with self.memory._connect() as conn:
            first, later = F.get_context_views(conn, 'invented-policy-union',
                'Summarize all my conversations.', budget_chars=8000, continuation_budget_chars=64000)
            dedicated = F.relevant_rules(conn, 'invented-policy-union', 'List all standing rules.', 200)
        prefix = '\n\n'.join(first)
        continuation = '\n\n'.join(later)
        self.assertTrue(all(claim in prefix for claim in claims))
        self.assertIn('response policies omitted', prefix)
        self.assertIn('coverage=complete for selected source scope', continuation.splitlines()[0])
        decoded = decode_visible(prefix+'\n'+continuation, complete=True)
        self.assertEqual(Counter(item['literal'] for item in decoded), Counter(policies+claims))
        self.assertEqual(len({(item['rid'], item['segment']) for item in decoded}), len(units))
        self.assertTrue(all(any(policy in rule['text'] for rule in dedicated) for policy in policies))

    def test_global_search_keeps_a_digest_candidate_after_mined_rules_fill_slots(self):
        tones = 'concise brief short formal informal friendly neutral polite calm warm direct clear simple conversational professional courteous'.split()
        policies = ['Always use a '+tones[index//len(tones)]+' '+tones[index%len(tones)]+' tone.'
                    for index in range(120)]
        claims = ['I chose '+topic+' because the local evening group provides quiet instruction and shared equipment.'
                  for topic in 'apiculture ceramics astronomy weaving kayaking linguistics genealogy marquetry'.split()]
        self.memory._reranker = None
        for index, text in enumerate(policies+claims):
            self.memory.add_messages('invented-mined-policy-budget', 'review', [
                dict(role='user', content=text, timestamp=1743465600000+index*1000)])
        rows = self.memory.search('invented-mined-policy-budget', 'Summarize all my conversations.', 100)
        prefix = '\n'.join(row['content'] for row in rows)[:8000]
        self.assertLessEqual(len(rows), 100)
        self.assertTrue(any(row['id'].startswith('digest_') for row in rows))
        self.assertTrue(all(claim in prefix for claim in claims))
        digest = '\n'.join(row['content'] for row in rows if row['id'].startswith('digest_'))
        if 'coverage=complete for selected source scope' in digest:
            self.assertTrue(all(policy in digest for policy in policies))
        else:
            self.assertIn('response policies omitted', digest)


if __name__ == '__main__':
    unittest.main()
