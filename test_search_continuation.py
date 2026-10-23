"""Search-level continuation bounds, prefix stability and rollback controls."""
import builtins
from collections import Counter
from dataclasses import replace
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('EMBEDDING_API_KEY','offline-placeholder')
os.environ.setdefault('AML_AUTH_TOKEN','offline-placeholder')
_open = builtins.open
def safe_open(file,*args,**kwargs):
    if '.secrets' in str(file):
        raise OSError('No credentials in synthetic tests')
    return _open(file,*args,**kwargs)
with patch('builtins.open',safe_open):
    import store
    from test_store_regressions import OfflineMemoryStore

class ContinuationSearchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='aml-search-continuation-')
        self.config = patch.object(store,'CONFIG',replace(store.CONFIG,
            db_path=str(Path(self.directory.name)/'memory.db'),embedding_dim=4,
            extended_context_chars=160000,context_continuation_chars=128000))
        self.config.start()
        self.network = patch('socket.create_connection',side_effect=AssertionError('No network'))
        self.network.start()
        self.memory = OfflineMemoryStore()
        self.messages = []
        for index in range(88):
            code = chr(65+index//26)+chr(65+index%26)
            text = (f'Archive entry {index:03d} records the decision DECISION-{code}. '
                + ('An independently documented engineering consideration remains available for inspection. '*5)
                + f'The accompanying rationale is RATIONALE-{code}.')
            self.messages.append({'role':'user','content':text,'timestamp':1735689600000+index*1000})
        self.memory.add_messages('target','archive',self.messages)

    def tearDown(self):
        self.memory.close()
        self.network.stop()
        self.config.stop()
        self.directory.cleanup()

    def query(self,k=100):
        return self.memory.search('target','Summarize all my conversations.',k)

    @staticmethod
    def rendered(rows):
        return '\n\n'.join(row['content'] for row in rows)

    def test_complete_long_history_and_literal_rationales_survive(self):
        rows = self.query()
        text = self.rendered(rows)
        for index in range(88):
            code = chr(65+index//26)+chr(65+index%26)
            self.assertTrue(f'DECISION-{code}' in text, 'Missing a literal decision')
            self.assertTrue(f'RATIONALE-{code}' in text, 'Missing a literal rationale')
        self.assertTrue(any(row['id'].startswith('continuation_') for row in rows))
        self.assertLessEqual(len(rows),100)
        self.assertLessEqual(sum(len(row['content']) for row in rows),160000)

    def test_expansion_preserves_the_existing_reading_prefix(self):
        expanded = self.rendered(self.query())
        with patch.object(store,'CONFIG',replace(store.CONFIG,context_continuation_chars=0)):
            compact = self.rendered(self.query())
        self.assertEqual(expanded[:16000],compact[:16000])

    def test_continuation_can_be_disabled_without_data_migration(self):
        with patch.object(store,'CONFIG',replace(store.CONFIG,context_continuation_chars=0)):
            rows = self.query()
        self.assertFalse(any(row['id'].startswith('continuation_') for row in rows))
        self.assertLessEqual(sum(len(row['content']) for row in rows),32000)

    def test_small_top_k_remains_an_upper_bound(self):
        for limit in (1,2,8,10):
            with self.subTest(limit=limit):
                self.assertLessEqual(len(self.query(limit)),limit)

    def test_other_user_sources_cannot_enter_a_global_continuation(self):
        self.memory.add_messages('foreign','archive',[{'role':'user',
            'content':'FOREIGN-CONTINUATION-SECRET','timestamp':1735689600000}])
        self.assertNotIn('FOREIGN-CONTINUATION-SECRET',self.rendered(self.query()))

    def literal_catalogue(self, user, *, mixed_roles=False):
        with self.memory._connect() as connection:
            connection.execute("INSERT INTO items(rid,user_id,session_id,role,content,created_at,timestamp_origin,status,seq)"
                " VALUES(?,?,?,?,?,?,?,'active',?)", (1000000000 if not mixed_roles else 2000000000,
                'invented-unrelated', 'other', 'user', 'Unrelated invented marker.',
                1743465599, 'message.timestamp', 1))
            connection.commit()
        tones = 'concise brief short formal informal friendly neutral polite calm warm direct clear simple conversational professional courteous'.split()
        policies = ['Always use a '+tones[index//len(tones)]+' '+tones[index%len(tones)]+' tone.'
                    for index in range(120)]
        claims = ['I chose '+topic+' because the local evening group provides quiet instruction and shared equipment.'
                  for topic in 'apiculture ceramics astronomy weaving kayaking linguistics genealogy marquetry'.split()]
        texts = policies+[claim for claim in claims for _ in range(20)]
        self.memory.add_messages(user, 'invented-policy-overview', [dict(
            role='assistant' if mixed_roles and index>=120 and index%2 else 'user', content=text,
            timestamp=1743465600000+index*1000) for index,text in enumerate(texts)])
        return claims

    def test_global_budget_counts_the_protected_raw_prefix_before_continuation(self):
        from evaluate_extended_context import decode_visible
        for mixed in (False, True):
            with self.subTest(mixed_roles=mixed):
                user = 'invented-catalogue-'+str(mixed)
                claims = self.literal_catalogue(user, mixed_roles=mixed)
                rows = self.memory.search(user, 'Summarize all my conversations.', 100)
                context = [row['content'] for row in rows if row['id'].startswith(('digest_', 'continuation_'))]
                decoded = decode_visible('\n'.join(context), complete=True)
                with self.memory._connect() as connection:
                    originals = list(connection.execute('SELECT rid,seq,role,content FROM items WHERE user_id=?', (user,)))
                expected = Counter((row['rid'], row['seq'], 0, row['role'], row['content']) for row in originals)
                self.assertEqual(Counter((row['rid'], row['seq'], row['segment'], row['role'], row['literal'])
                                         for row in decoded), expected)
                self.assertEqual(len(decoded), 280)
                self.assertEqual(len({(row['rid'], row['segment']) for row in decoded}), 280)
                self.assertTrue(all(claim in self.rendered(rows)[:8000] for claim in claims))
                self.assertLessEqual(len(rows), 100)
                self.assertLessEqual(sum(len(row['content']) for row in rows), 160000)

    def test_global_continuation_still_obeys_a_configured_32k_ceiling(self):
        user = 'invented-capped-catalogue'
        self.literal_catalogue(user)
        with patch.object(store, 'CONFIG', replace(store.CONFIG, extended_context_chars=32000)):
            rows = self.memory.search(user, 'Summarize all my conversations.', 100)
        self.assertLessEqual(sum(len(row['content']) for row in rows), 32000)
        self.assertLessEqual(len(rows), 100)
        prefix = [row['content'] for row in rows if row['id'].startswith('digest_')]
        self.assertTrue(prefix)
        self.assertTrue(all('coverage=truncated' in text for text in prefix))

if __name__=='__main__':
    unittest.main()
