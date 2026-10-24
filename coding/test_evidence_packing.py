"""Invented Coding-history packing controls; no storage, providers or secrets."""
import copy
import unittest

try:
    from .evidence_packing import pack_evidence
except ImportError:
    from evidence_packing import pack_evidence


def row(rid,content,*,session='finch-task',role='assistant',user='invented'):
    return dict(rid=rid,source_rid=rid,user_id=user,session_id=session,role=role,
                seq=rid,created_at=1740000000+rid,timestamp_origin='message.timestamp',content=content)


def render(source,query,budget):
    header=f"[SOURCE rid={source['rid']} role={source['role']} session={source['session_id']} seq={source['seq']}]\n"
    content=source['content']
    # Fixture records are atomic literal lines. A renderer cannot manufacture
    # partial code just because this caller requests a smaller budget.
    if len(header)+len(content)>budget:return None
    return dict(source_rid=source['rid'],role=source['role'],session_id=source['session_id'],seq=source['seq'],
                content=header+content,source_char_ranges=[[0,len(content)]])


class EvidencePackingTests(unittest.TestCase):
    def history(self):
        return [
            row(1,'File: src/finch/cache.py\ndef refresh_finch(key):\n    return cache.pop(key, None)\n'),
            row(2,'Correction for src/finch/cache.py refresh_finch: the first attempt failed for absent keys; preserve None without raising.'),
            row(3,'Validation for src/finch/cache.py refresh_finch: pytest tests/test_finch_cache.py -q; 12 passed, 0 failed.'),
            row(4,'Final implementation src/finch/cache.py refresh_finch uses an explicit sentinel; verified the missing-key case.'),
            row(5,'File: src/garden/soil.py\ndef measure_soil():\n    return 8\n'),
            row(6,'Validation for src/finch/cache.py refresh_finch: pytest tests/test_finch_cache.py -q; 12 passed, 0 failed.'),
            row(7,'Correction carried across sessions: src/finch/cache.py refresh_finch retains the explicit sentinel.',session='follow-up'),
            row(8,'File: other_project/cache.py\ndef open_other_cache():\n    return 4\n',session='other-task'),
        ]

    def test_seed_brings_complementary_implementation_correction_and_validation(self):
        rows=self.history();original=copy.deepcopy(rows)
        packed=pack_evidence(rows,[1],'How was refresh_finch in src/finch/cache.py repaired?',render,top_k=8,char_budget=5000)
        ids=[item['source_rid'] for item in packed]
        self.assertEqual(ids[0],1)
        self.assertTrue({2,3,4}<=set(ids))
        self.assertNotIn(5,ids)
        self.assertNotIn(8,ids)
        self.assertEqual(rows,original)
        self.assertTrue(all(item['role']=='assistant' and item['seq']==item['source_rid'] for item in packed))

    def test_cross_session_link_requires_both_explicit_path_and_symbol(self):
        rows=[self.history()[0],
              row(2,'Verified src/finch/cache.py only, no named symbol.',session='other'),
              row(3,'Verified refresh_finch only, no code path.',session='other'),
              row(4,'Verified src/finch/cache.py refresh_finch has no stale entry.',session='other')]
        ids=[item['source_rid'] for item in pack_evidence(rows,[1],'repair refresh_finch',render)]
        self.assertEqual(set(ids),{1,4})

    def test_nearby_session_tail_is_not_a_relationship(self):
        rows=[self.history()[0],row(2,'All tests passed. The final unrelated garden plan is complete.'),
              row(3,'The change in src/garden/soil.py has been verified.')]
        packed=pack_evidence(rows,[1],'refresh_finch',render)
        self.assertEqual([item['source_rid'] for item in packed],[1])

    def test_generic_main_get_and_readme_cannot_widen_context(self):
        rows=[row(1,'File: README.md\ndef main():\n    return get()\n'),
              row(2,'Final README.md main get implementation verified; all tests passed.')]
        self.assertEqual([item['source_rid'] for item in pack_evidence(rows,[1],'main',render)],[1])

    def test_shared_basename_is_not_shared_full_path(self):
        rows=[self.history()[0],row(2,'Final implementation other_project/cache.py open_other_cache has been verified.')]
        self.assertEqual([item['source_rid'] for item in pack_evidence(rows,[1],'refresh_finch',render)],[1])

    def test_same_named_definition_in_different_explicit_file_is_not_joined(self):
        rows=[self.history()[0],row(2,'File: other_project/cache.py\ndef refresh_finch(key):\n    return 9  # tested passed\n')]
        self.assertEqual([item['source_rid'] for item in pack_evidence(rows,[1],'How was the cache repaired?',render)],[1])

    def test_same_file_different_definition_needs_requested_symbol_reference(self):
        seed=self.history()[0]
        unrelated=row(2,'File: src/finch/cache.py\ndef other_func():\n    return 2  # final tested passed\n')
        caller=row(3,'File: src/finch/cache.py\ndef test_refresh():\n    assert refresh_finch("absent") is None\n')
        correction=row(4,'Correction src/finch/cache.py: preserve the sentinel without removing an existing entry.')
        packed=pack_evidence([seed,unrelated,caller,correction],[1],'Repair refresh_finch.',render)
        self.assertEqual({item['source_rid'] for item in packed},{1,3,4})

    def test_cross_file_evidence_must_explicitly_name_the_seed_path(self):
        seed=self.history()[0]
        explicit=row(2,'File: tests/test_cache.py\nValidation for src/finch/cache.py refresh_finch: 12 passed.')
        self.assertEqual({item['source_rid'] for item in pack_evidence([seed,explicit],[1],'refresh_finch',render)},{1,2})

    def test_missing_sessions_and_generic_log_levels_are_not_identity_anchors(self):
        rows=[row(1,'KnownSpecialError occurred.',session=None),
              row(2,'Verified KnownSpecialError correction.',session=None)]
        self.assertEqual([item['source_rid'] for item in pack_evidence(rows,[1],'repair',render)],[1])
        generic=[row(1,'ERROR in diagnostic output.'),row(2,'ERROR tests passed; final implementation complete.')]
        self.assertEqual([item['source_rid'] for item in pack_evidence(generic,[1],'repair',render)],[1])

    def test_links_do_not_expand_transitively(self):
        rows=[self.history()[0],row(2,'Correction: src/finch/cache.py refresh_finch uses src/sql/reader.py open_record.'),
              row(3,'Verified src/sql/reader.py open_record: 20 tests passed.')]
        ids=[item['source_rid'] for item in pack_evidence(rows,[1],'refresh_finch',render)]
        self.assertIn(2,ids);self.assertNotIn(3,ids)

    def test_repeated_literal_is_deferred_without_calling_latest_authoritative(self):
        rows=self.history();packed=pack_evidence(rows,[3,6,1,5],'refresh_finch src/finch/cache.py',render,char_budget=4000)
        ids=[item['source_rid'] for item in packed]
        self.assertEqual(ids[0],3)
        self.assertLess(ids.index(1),ids.index(6))
        self.assertLess(ids.index(5),ids.index(6))
        self.assertTrue(all('LATEST VERSION' not in item['content'] for item in packed))

    def test_different_source_roles_are_not_collapsed_as_duplicate_occurrences(self):
        text='Validation src/finch/cache.py refresh_finch: 12 passed.'
        rows=[row(1,text,role='user'),row(2,text,role='assistant')]
        packed=pack_evidence(rows,[1,2],'refresh_finch',render)
        self.assertEqual([(item['source_rid'],item['role']) for item in packed],[(1,'user'),(2,'assistant')])

    def test_short_independent_ranked_sources_keep_order_and_full_top_hundred(self):
        rows=[row(index,f'Independent notebook record {index}.',session=f'session-{index}') for index in range(1,106)]
        ranked=list(reversed(range(1,65)))+list(range(65,106))
        packed=pack_evidence(rows,ranked,'unmatched-symbol',render,top_k=100)
        self.assertEqual([item['source_rid'] for item in packed],ranked[:100])

    def test_actual_rendered_characters_and_top_k_are_hard_bounds(self):
        rows=[row(index,'File: src/finch/cache.py\n# refresh_finch verified\n'+str(index)*200) for index in range(1,20)]
        for top_k,budget in ((1,600),(5,1200),(100,2000),(100,0)):
            packed=pack_evidence(rows,list(range(1,20)),'refresh_finch',render,top_k=top_k,char_budget=budget,max_source_chars=9000)
            self.assertLessEqual(len(packed),min(top_k,100))
            self.assertLessEqual(sum(len(item['content']) for item in packed),budget)
            if packed:self.assertEqual(packed[0]['source_rid'],1)

    def test_mixed_users_and_ambiguous_source_ids_fail_closed(self):
        with self.assertRaises(ValueError):pack_evidence([row(1,'a',user='one'),row(2,'b',user='two')],[1],'a',render)
        with self.assertRaises(ValueError):pack_evidence([row(1,'a'),row(1,'different')],[1],'a',render)

    def test_renderer_receives_copy_and_oversized_result_is_never_admitted(self):
        rows=[row(1,'literal short source'),row(2,'another literal short source')];original=copy.deepcopy(rows)
        def bad(source,query,budget):
            source['role']='MUTATED'
            return dict(source_rid=source['rid'],content='x'*(budget+1))
        self.assertEqual(pack_evidence(rows,[1,2],'literal',bad,char_budget=100),[])
        self.assertEqual(rows,original)
        self.assertEqual(pack_evidence(rows,[1,2],'literal',lambda *args:dict(content='')),[])


if __name__=='__main__':unittest.main()
