"""Invented exact-ID background supplementation; local vectors and temp DBs."""
from __future__ import annotations

import copy
import math
import unittest

import evidence_links as L
from test_store_regressions import _OfflineStoreTestCase


QUERY = 'Use protocol PR-HERON-VC to convert the new spindle sample z=287500 with our earlier convention.'
BACKGROUND = ('Protocol ID: PR-HERON-VC. Field z denotes elapsed microseconds. '
              'Normalization divides z by 1000000 to obtain seconds. '
              'Valid readings require shielded mode. '
              'Never infer additional observations from repeated values.')
PRIVATE = 'My private address is 81 Secret Avenue.'
FILLER = 'The spindle sample notebook contains descriptive pages about the new spindle sample and a storage tray. '
LONG = ('Protocol ID: PR-HERON-VC. '
        + ' '.join(f'Notebook page {i}: '+FILLER for i in range(60))
        + ' '+BACKGROUND+' '+PRIVATE)
ANCHOR = 'Protocol PR-HERON-VC new spindle sample: z=287500; mode=shielded.'
REQUIRED = ('elapsed microseconds','divides z by 1000000','shielded mode',
            'Never infer additional observations')


def record(rid, text, role='user'):
    return {'rid':rid,'user_id':'owner','session_id':f'session-{rid}','seq':rid,
            'role':role,'content':text,'status':'active'}


class SeedBackgroundApiTests(unittest.TestCase):
    def test_default_excludes_seeds_but_explicit_excerpted_seed_can_return_background(self):
        rows=[record(1,LONG),record(2,ANCHOR)]
        original=copy.deepcopy(rows)
        self.assertEqual(L.select_linked_sources(rows,QUERY,{1,2}),[])
        got=L.select_linked_sources(rows,QUERY,{1,2},include_seed_rids={1})
        self.assertEqual([row['source_rid'] for row in got],[1])
        self.assertTrue(all(text in got[0]['quote'] for text in REQUIRED))
        self.assertNotIn(PRIVATE,got[0]['quote'])
        self.assertEqual(rows,original)
        for a,b in got[0]['quote_spans']:
            self.assertIn(LONG[a:b],got[0]['quote'])

    def test_seed_option_does_not_create_query_or_identifier_anchor(self):
        rows=[record(1,LONG),record(2,ANCHOR)]
        self.assertEqual(L.select_linked_sources(rows,'Convert the new sample.',{1,2},include_seed_rids={1}),[])
        other_query=QUERY.replace('PR-HERON-VC','PR-HERON-VD')
        self.assertEqual(L.select_linked_sources(rows,other_query,{1,2},include_seed_rids={1}),[])

    def test_same_literal_other_kind_cannot_add_background(self):
        rows=[record(1,BACKGROUND.replace('Protocol ID:','Dataset ID:')),record(2,ANCHOR)]
        self.assertEqual(L.select_linked_sources(rows,QUERY,{1,2},include_seed_rids={1}),[])


class SeedBackgroundStoreTests(_OfflineStoreTestCase):
    def populate(self, definition=LONG, *, role='user'):
        messages=[definition,ANCHOR]+[f'Cabinet entry {i}: spare washers and carton folders.' for i in range(5)]
        for index,text in enumerate(messages):
            cosine=1.0 if index==0 else .95 if index==1 else .7-index*.01
            self.memory.vectors[text]=[cosine,math.sqrt(1-cosine*cosine),0.,0.]
            self.memory.add_messages('owner',f'session-{index}',[
                {'role':role if index==0 else 'user','content':text,'timestamp':1735689600000+index*1000}])
        with self.memory._connect() as conn:
            return self.memory._search_raw(conn,'owner',[1.,0.,0.,0.],20,query=QUERY)[0]

    def test_excerpted_seed_background_is_in_same_source_without_score_or_role_change(self):
        before=self.populate()
        self.assertTrue(before['excerpt'])
        self.assertTrue(all(text not in before['content'] for text in REQUIRED))
        results=self.memory.search('owner',QUERY,20)
        matching=[r for r in results if r['id']==before['id']]
        self.assertEqual(len(matching),1)
        after=matching[0]
        self.assertTrue(all(text in after['content'] for text in REQUIRED))
        self.assertIn('not additional observations',after['content'])
        for key in ('id','source_rid','score','role','seq','session_id','recorded_at','timestamp_origin'):
            self.assertEqual(after[key],before[key],key)
        self.assertNotIn(PRIVATE,after['content'])
        self.assertEqual(len([r for r in results if r['id'].startswith('mem_')]),7)
        with self.memory._connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM items WHERE user_id='owner'").fetchone()[0],7)
        self.assertTrue(after['background_source_char_ranges'])

    def test_complete_short_seed_is_not_supplemented(self):
        before=self.populate(BACKGROUND)
        self.assertNotIn('excerpt',before)
        results=self.memory.search('owner',QUERY,20)
        after=next(r for r in results if r['id']==before['id'])
        self.assertEqual(after['content'],before['content'])
        self.assertNotIn('same_source_background',after)

    def test_top_one_does_not_open_background_linking(self):
        self.populate()
        results=self.memory.search('owner',QUERY,1)
        self.assertLessEqual(len(results),1)
        self.assertFalse(any(r.get('same_source_background') for r in results))

    def test_assistant_background_keeps_its_source_role(self):
        self.populate(role='assistant')
        results=self.memory.search('owner',QUERY,20)
        source=next(r for r in results if r['id']=='mem_1')
        self.assertEqual(source['role'],'assistant')
        self.assertTrue(all(text in source['content'] for text in REQUIRED))
        self.assertNotIn(PRIVATE,source['content'])

    def test_visible_observation_is_not_repeated_by_background_supplement(self):
        observation='z=287500; w=12.5'
        definition=('Protocol ID: PR-HERON-VC. Schema: z is a reading; w is a weight.\n'
                    +observation+'\n'+LONG)
        before=self.populate(definition)
        self.assertIn(observation,before['content'])
        source=next(r for r in self.memory.search('owner',QUERY,20) if r['id']==before['id'])
        self.assertTrue(all(text in source['content'] for text in REQUIRED))
        self.assertEqual(source['content'].count(observation),1)


if __name__=='__main__':
    unittest.main()
