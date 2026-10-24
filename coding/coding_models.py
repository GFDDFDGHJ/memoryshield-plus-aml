"""Bounded embedding/ranking provider. Original code is never rewritten here."""
import math
import threading
import httpx

from coding_reranker import (RerankerClient, MAX_DOCUMENTS, MAX_QUERY_BYTES,
                            MAX_DOCUMENT_BYTES, MAX_WEIGHTED_INPUT_BYTES)

def embedding_parts(text, max_bytes=6000):
    """UTF-8 bounded copies for the provider; their mean covers the full text."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError('embedding requires non-empty text')
    encoded = text.encode('utf-8')
    chunks = []
    while encoded:
        part = encoded[:max_bytes]
        # Never split a Unicode codepoint. Every byte belongs to one chunk.
        value = part.decode('utf-8', errors='ignore')
        if not value:
            raise ValueError('embedding chunk bound too small')
        used = len(value.encode('utf-8'))
        chunks.append(value)
        encoded = encoded[used:]
    return chunks

class ModelProvider:
    def __init__(self, settings, *, transport=None):
        self.settings = settings
        self.client = httpx.Client(timeout=httpx.Timeout(60, connect=15), transport=transport, follow_redirects=False)
        self.embedding_slots = threading.BoundedSemaphore(2)
        self.rank_slots = threading.BoundedSemaphore(2)
        self.ranker = RerankerClient(settings.embedding_key, model=settings.rerank_model)

    def embed(self, texts):
        if not texts:
            return []
        if not self.settings.embedding_key:
            raise RuntimeError('Coding embedding credential is not configured')
        parts, owners = [], []
        for index, text in enumerate(texts):
            split = embedding_parts(text)
            parts.extend(split)
            owners.extend([index]*len(split))
        vectors = []
        for offset in range(0, len(parts), 10):
            batch = parts[offset:offset+10]
            with self.embedding_slots:
                try:
                    response = self.client.post(self.settings.embedding_url,
                        headers={'Authorization':'Bearer '+self.settings.embedding_key},
                        json={'model':self.settings.embedding_model,'input':batch,'dimensions':self.settings.dim})
                    if response.status_code != 200:
                        raise RuntimeError('Embedding provider did not accept the request')
                    rows = response.json().get('data')
                    if not isinstance(rows,list) or len(rows) != len(batch):
                        raise ValueError('Invalid embedding count')
                    if any(not isinstance(item,dict) or type(item.get('index')) is not int for item in rows):
                        raise ValueError('Invalid embedding indexes')
                    ordered = sorted(rows,key=lambda item:item['index'])
                    if [item['index'] for item in ordered] != list(range(len(batch))):
                        raise ValueError('Invalid embedding indexes')
                    for row in ordered:
                        vector = row['embedding']
                        if (not isinstance(vector,list) or len(vector)!=self.settings.dim
                            or any(isinstance(value,bool) or not isinstance(value,(float,int))
                                   or not math.isfinite(value) for value in vector)):
                            raise ValueError('Invalid embedding dimensions or values')
                        vectors.append(vector)
                except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                    raise RuntimeError('Embedding request failed validation') from None
        result = [[0.0]*self.settings.dim for _ in texts]
        counts = [0]*len(texts)
        for owner, vector in zip(owners,vectors):
            counts[owner] += 1
            for index,value in enumerate(vector):
                result[owner][index] += value
        for owner in range(len(result)):
            result[owner] = [value/counts[owner] for value in result[owner]]
        return result

    def rerank(self, query, documents):
        if not self.settings.rerank_enabled or not self.rank_slots.acquire(blocking=False):
            return None
        try:
            # Rank a bounded leading recall pool; return the untouched tail in
            # the full permutation so a provider-size limit drops no evidence.
            query_cost = min(MAX_QUERY_BYTES,len(query.encode('utf-8')))
            count,used = 0,0
            for document in documents[:MAX_DOCUMENTS]:
                cost = query_cost + min(MAX_DOCUMENT_BYTES,len(document.encode('utf-8')))
                if used+cost > MAX_WEIGHTED_INPUT_BYTES:
                    break
                used += cost
                count += 1
            if count < 2:
                return None
            order = self.ranker.rerank(query,documents[:count])
            if order is None:
                return None
            return list(order)+list(range(count,len(documents)))
        finally:
            self.rank_slots.release()
