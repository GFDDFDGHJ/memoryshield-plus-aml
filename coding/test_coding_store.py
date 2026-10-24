"""Isolated synthetic Coding-store tests; no API keys, models or real DBs."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest

try:
    from .coding_store import MemoryStore, AddRequestConflict, InvalidAddInput
except ImportError:
    from coding_store import MemoryStore, AddRequestConflict, InvalidAddInput


class Embeddings:
    def __init__(self):
        self.calls=[]
        self.vectors={}
        self.guard=threading.Lock()

    def __call__(self,texts):
        with self.guard:self.calls.append(list(texts))
        return [self.vectors.get(text,[1.,0.,0.,0.]) for text in texts]


class CodingStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='isolated-coding-store-')
        self.path=Path(self.temporary.name)/'coding-only.sqlite'
        self.embedding=Embeddings()
        self.memory=MemoryStore(self.path,embed=self.embedding,dim=4)

    def tearDown(self):self.temporary.cleanup()

    def add(self,text,*,user='target',session='work',role='user',request=None,timestamp=1735689600000):
        return self.memory.add_messages(user,session,[dict(content=text,role=role,timestamp=timestamp)],request)

    def query(self,sql,args=()):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.row_factory=sqlite3.Row
            return [dict(row) for row in connection.execute(sql,args)]

    def test_raw_source_unicode_whitespace_newlines_and_metadata_survive_reopen(self):
        source='src/worker.py\r\n```python\r\n\tdef RunTask(x: str):\r\n\t\treturn "甲\\n  β"  # KEEP  spaces\r\n```\r\n'
        self.add(source,session='phase-two',role='assistant',timestamp=1740000000123)
        self.assertEqual(self.embedding.calls,[[source]])
        row=self.query('SELECT * FROM coding_items')[0]
        self.assertEqual(row['content'],source)
        self.assertEqual(json.loads(row['message_json'])['content'],source)
        reopened=MemoryStore(self.path,embed=self.embedding,dim=4)
        found=reopened.search('target','Find RunTask in src/worker.py.',1)
        self.assertEqual(len(found),1)
        item=found[0]
        self.assertEqual((item['role'],item['session_id'],item['seq']),('assistant','phase-two',1))
        self.assertEqual(item['timestamp_origin'],'message.timestamp')
        self.assertEqual(item['created_at'],'2025-02-19T21:20:00.123000Z')
        for start,end in item['source_char_ranges']:
            self.assertIn(source[start:end],item['content'])

    def test_idempotent_retry_and_conflict_are_checked_before_embedding(self):
        message=[dict(role='user',content='def CacheEntry():\n    return 3\n',timestamp=1735689600000)]
        self.assertEqual(self.memory.add_messages('target','s',message,'chunk-1'),1)
        self.assertEqual(self.memory.add_messages('target','s',message,'chunk-1'),1)
        with self.assertRaises(AddRequestConflict):
            self.memory.add_messages('target','different',message,'chunk-1')
        with self.assertRaises(AddRequestConflict):
            self.memory.add_messages('target','s',[dict(content='different')],'chunk-1')
        self.assertEqual(len(self.embedding.calls),1)
        self.assertEqual(len(self.query('SELECT * FROM coding_items')),1)
        self.assertEqual(self.memory.add_messages('another','s',message,'chunk-1'),1)
        self.assertEqual(len(self.embedding.calls),2)

    def test_concurrent_same_request_across_instances_embeds_and_persists_once(self):
        start=threading.Barrier(8)
        second=MemoryStore(self.path,embed=self.embedding,dim=4)
        def add(index):
            start.wait()
            return (self.memory if index%2 else second).add_messages('target','s',[dict(content='retry-source')],'same')
        with ThreadPoolExecutor(max_workers=8) as executor:
            written=list(executor.map(add,range(8)))
        self.assertEqual(written,[1]*8)
        self.assertEqual(self.embedding.calls,[['retry-source']])
        self.assertEqual(len(self.query('SELECT * FROM coding_items')),1)

    def test_concurrent_distinct_chunks_get_unique_contiguous_per_user_sequences(self):
        def add(index):
            return self.memory.add_messages('target','s'+str(index),[
                dict(content=f'chunk {index} line one'),dict(content=f'chunk {index} line two')],f'chunk-{index}')
        with ThreadPoolExecutor(max_workers=12) as executor:
            self.assertEqual(list(executor.map(add,range(24))),[2]*24)
        rows=self.query('SELECT seq,content FROM coding_items WHERE user_id=? ORDER BY seq',('target',))
        self.assertEqual([row['seq'] for row in rows],list(range(1,49)))
        for left,right in zip(rows[::2],rows[1::2]):
            self.assertEqual(left['content'].rsplit(' ',2)[0],right['content'].rsplit(' ',2)[0])

    def test_mid_write_failure_rolls_back_messages_vectors_receipt_and_sequence(self):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("CREATE TRIGGER reject_boom BEFORE INSERT ON coding_items WHEN new.content='boom' BEGIN SELECT RAISE(ABORT,'synthetic rejection'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.memory.add_messages('target','s',[dict(content='good'),dict(content='boom')],'failed')
        self.assertEqual(self.query('SELECT * FROM coding_items'),[])
        self.assertEqual(self.query('SELECT * FROM coding_add_requests'),[])
        self.add('new')
        self.assertEqual(self.query('SELECT seq FROM coding_items'),[{'seq':1}])

    def test_bad_provider_vectors_never_commit_partial_messages(self):
        for response in ([[1.,0.,0.,0.]], [[1.,0.],[1.,0.]], [[float('nan')]*4]*2):
            broken=MemoryStore(self.path,embed=lambda texts:response,dim=4)
            with self.assertRaises(ValueError):
                broken.add_messages('target','s',[dict(content='a'),dict(content='b')],'broken')
            self.assertEqual(self.query('SELECT * FROM coding_items'),[])
            self.assertEqual(self.query('SELECT * FROM coding_add_requests'),[])

    def test_bad_input_is_rejected_without_providers_or_silent_conversion(self):
        for messages in ([dict(content={'text':'changed shape'})],[dict(content='x',timestamp=float('inf'))],
                         [dict(content='x',timestamp=True)],[dict(content='x',role='')]):
            with self.assertRaises(InvalidAddInput):self.memory.add_messages('target','s',messages)
        self.assertEqual(self.embedding.calls,[])
        self.assertEqual(self.query('SELECT * FROM coding_items'),[])

    def test_empty_chunk_and_blank_messages_preserve_request_semantics_without_provider(self):
        self.assertEqual(self.memory.add_messages('target','s',[],'empty'),0)
        self.assertEqual(self.memory.add_messages('target','s',[],'empty'),0)
        self.add(' \t\r\n',request='blank')
        self.assertEqual(self.embedding.calls,[])
        self.assertEqual(self.query('SELECT content FROM coding_items'),[{'content':' \t\r\n'}])

    def test_literal_identifier_and_path_recall_overrides_many_dense_decoys(self):
        for index in range(85):self.add(f'Ordinary unrelated cabinet notebook {index}.')
        source='File: src/cache_layer.py\ndef dispatch_cache_entry(key):\n    raise CacheMissError(key)\n'
        self.embedding.vectors[source]=[-1.,0.,0.,0.]
        self.add(source,session='archived-code',role='assistant')
        results=self.memory.search('target','Where is dispatch_cache_entry in src/cache_layer.py and CacheMissError?',5)
        self.assertEqual(results[0]['source_rid'],86)
        self.assertIn('dispatch_cache_entry',results[0]['content'])
        self.assertIn('CacheMissError',results[0]['content'])
        self.assertEqual(results[0]['session_id'],'archived-code')

    def test_cosine_is_exact_and_scales_out_vector_magnitude(self):
        self.embedding.vectors.update({'source-a':[2.,0.,0.,0.],'source-b':[0.,4.,0.,0.],
                                       'unmatched query':[1.,0.,0.,0.]})
        self.add('source-a');self.add('source-b')
        results=self.memory.search('target','unmatched query',2)
        self.assertEqual([r['cosine_similarity'] for r in results],[1.,0.])

    def test_foreign_sources_never_reach_search_reranker_or_evidence(self):
        seen=[]
        self.memory.rerank=lambda query,documents:seen.extend(documents) or list(range(len(documents)))
        self.add('def TargetKernel():\n    return 1\n',session='one')
        self.add('def TargetHelper():\n    return 2\n',session='two',role='assistant')
        self.add('FOREIGN-USER-SECRET def TargetKernel(): return 999',user='foreign')
        results=self.memory.search('target','Find TargetKernel and TargetHelper.',100)
        self.assertEqual({r['session_id'] for r in results},{'one','two'})
        self.assertNotIn('FOREIGN-USER-SECRET','\n'.join(seen+[r['content'] for r in results]))
        before=len(self.embedding.calls)
        self.assertEqual(self.memory.search('missing-user','TargetKernel',100),[])
        self.assertEqual(len(self.embedding.calls),before)

    def test_optional_rerank_pool_is_bounded_and_invalid_results_fall_back(self):
        for index in range(105):self.add(f'source number {index}')
        baseline=self.memory.search('target','unknown-symbol',100)
        recorded=[]
        def reverse(query,documents):
            recorded.append(documents)
            return list(reversed(range(len(documents))))
        self.memory.rerank=reverse
        reordered=self.memory.search('target','unknown-symbol',500)
        self.assertEqual(len(reordered),100)
        self.assertEqual(len(recorded[0]),64)
        self.assertEqual(reordered[0]['source_rid'],baseline[63]['source_rid'])
        for invalid in ([0],None,[0]*64,[False]*64):
            self.memory.rerank=lambda query,documents:invalid
            self.assertEqual([r['id'] for r in self.memory.search('target','unknown-symbol',100)],
                             [r['id'] for r in baseline])
        def fail(*_):raise RuntimeError('synthetic rank failure')
        self.memory.rerank=fail
        self.assertEqual([r['id'] for r in self.memory.search('target','unknown-symbol',100)],[r['id'] for r in baseline])

    def test_unknown_and_ingestion_timestamps_are_distinct_without_precedence_inference(self):
        self.add('def NewerIngestedButOlderSource(): pass',timestamp=1735689600000)
        self.add('def UndatedSource(): pass',timestamp=None)
        results=self.memory.search('target','Source',100)
        self.assertEqual({r['timestamp_origin'] for r in results},{'message.timestamp','ingestion-clock'})
        self.assertTrue(all('not a code-version precedence claim' in r['content'] for r in results))

    def test_dimension_changes_and_unrelated_database_are_rejected(self):
        with self.assertRaises(ValueError):MemoryStore(self.path,embed=self.embedding,dim=8)
        other=Path(self.temporary.name)/'unrelated.sqlite'
        with closing(sqlite3.connect(other)) as connection:connection.execute('CREATE TABLE important(value)')
        with self.assertRaises(ValueError):MemoryStore(other,embed=self.embedding,dim=4)
        with closing(sqlite3.connect(other)) as connection:
            self.assertEqual(connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(),[('important',)])


if __name__=='__main__':unittest.main()
