"""Invented provider responses; no real model calls or credentials."""
import json
from types import SimpleNamespace
import unittest
import httpx

from coding_models import ModelProvider, embedding_parts

def settings(**changes):
    values=dict(embedding_key='offline-placeholder',embedding_url='https://offline.invalid/embeddings',
        embedding_model='text-embedding-v4',dim=4,rerank_model='qwen3-rerank',rerank_enabled=False)
    values.update(changes)
    return SimpleNamespace(**values)

class EmbeddingProviderTests(unittest.TestCase):
    def test_unicode_chunks_reconstruct_every_byte_including_tail(self):
        text='def calculate_rate():\r\n    return "田野🌲"\r\n'*600
        chunks=embedding_parts(text)
        self.assertGreater(len(chunks),1)
        self.assertEqual(''.join(chunks),text)
        self.assertTrue(all(len(chunk.encode('utf-8'))<=6000 for chunk in chunks))

    def test_chunk_pooling_batch_bounds_and_response_index_order(self):
        calls=[]
        def transport(request):
            body=json.loads(request.content);calls.append(body)
            self.assertEqual(request.headers['Authorization'],'Bearer offline-placeholder')
            self.assertLessEqual(len(body['input']),10)
            return httpx.Response(200,json={'data':[
                {'index':index,'embedding':[len(text),1.,2.,3.]}
                for index,text in reversed(list(enumerate(body['input']))) ]})
        provider=ModelProvider(settings(),transport=httpx.MockTransport(transport))
        texts=['x'*13000]+['def f'+str(i)+'():\n    return 5\n' for i in range(11)]
        result=provider.embed(texts)
        self.assertEqual(len(calls),2)
        self.assertEqual(len(result),12)
        self.assertAlmostEqual(result[0][0],13000/3)
        self.assertEqual(result[1],[len(texts[1]),1.,2.,3.])
        provider.client.close()

    def test_invalid_provider_schemas_never_become_vectors(self):
        invalid=[{}, {'data':[]}, {'data':[{'index':False,'embedding':[1.,2.,3.,4.]}]},
                 {'data':[{'index':1,'embedding':[1.,2.,3.,4.]}]},
                 {'data':[{'index':0,'embedding':[1.,2.,3.]}]},
                 {'data':[{'index':0,'embedding':[True,2.,3.,4.]}]}]
        for payload in invalid:
            with self.subTest(payload=payload):
                provider=ModelProvider(settings(),transport=httpx.MockTransport(lambda request:httpx.Response(200,json=payload)))
                with self.assertRaises(RuntimeError):provider.embed(['def f(): pass'])
                provider.client.close()

    def test_empty_input_and_missing_credential_do_not_call_network(self):
        def denied(request):raise AssertionError('Unexpected provider call')
        provider=ModelProvider(settings(embedding_key=''),transport=httpx.MockTransport(denied))
        self.assertEqual(provider.embed([]),[])
        with self.assertRaises(RuntimeError):provider.embed(['text'])
        self.assertIsNone(provider.rerank('code',['source']))
        provider.client.close()

    def test_provider_error_body_is_not_exposed(self):
        provider=ModelProvider(settings(),transport=httpx.MockTransport(
            lambda request:httpx.Response(429,text='PRIVATE-REMOTE-BODY')))
        with self.assertRaises(RuntimeError) as caught:provider.embed(['example'])
        self.assertNotIn('PRIVATE-REMOTE-BODY',str(caught.exception))
        provider.client.close()

    def test_long_ranking_pool_preserves_the_unranked_tail(self):
        provider=ModelProvider(settings(rerank_enabled=True))
        class OfflineRanker:
            def rerank(self,query,documents):
                self.count=len(documents)
                return list(reversed(range(len(documents))))
        provider.ranker=OfflineRanker()
        original=['def example():\n    return "'+str(index)+'"\n'+'x'*5000 for index in range(64)]
        order=provider.rerank('why does the example fail? '+'long-query-context '*300,original)
        self.assertLess(provider.ranker.count,64)
        self.assertEqual(set(order),set(range(64)))
        self.assertEqual(order[provider.ranker.count:],list(range(provider.ranker.count,64)))
        self.assertEqual(len(original),64)
        provider.client.close()

if __name__=='__main__':unittest.main()
