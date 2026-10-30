"""Search-level continuation bounds, prefix stability and rollback controls."""
import builtins
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

if __name__=='__main__':
    unittest.main()
