"""Invented Coding HTTP contracts, isolated SQLite and no provider calls."""
from __future__ import annotations

from dataclasses import replace
import importlib.util
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
IMPORT_DIRECTORY = tempfile.TemporaryDirectory(prefix='coding-api-import-')
os.environ.update(CODING_DB_PATH=str(Path(IMPORT_DIRECTORY.name)/'import.db'),
    CODING_AUTH_TOKEN='offline-coding-auth', CODING_EMBEDDING_API_KEY='offline-coding-embedding',
    CODING_RERANK='false', AML_DB_PATH=str(Path(IMPORT_DIRECTORY.name)/'forbidden-text.db'),
    AML_AUTH_TOKEN='forbidden-text-auth')

_read_text = Path.read_text
def guarded_read(path, *args, **kwargs):
    if '.secrets' in str(path):
        raise AssertionError('Coding API tests cannot read credentials')
    return _read_text(path, *args, **kwargs)

with patch.object(Path, 'read_text', guarded_read), patch('socket.create_connection', side_effect=AssertionError('No network')):
    spec = importlib.util.spec_from_file_location('coding_contract_application', ROOT/'app.py')
    api_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api_module)

from fastapi.testclient import TestClient

AUTH = {'Authorization':'Bearer offline-coding-auth'}

class CodingApiContractTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='coding-api-contract-')
        self.addCleanup(self.directory.cleanup)
        self.network = patch('socket.create_connection', side_effect=AssertionError('No network'))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.embed = Mock(side_effect=lambda texts:[[1.0,0.0,0.0,0.0] for _ in texts])
        self.settings = replace(api_module.CONFIG, db_path=str(Path(self.directory.name)/'coding.db'),
            auth_token='offline-coding-auth', dim=4, rerank_enabled=False, source_revision='invented-test-revision')
        self.memory = api_module.MemoryStore(self.settings.db_path, dim=4, embed=self.embed)
        self.client = TestClient(api_module.create_app(self.memory, self.settings), raise_server_exceptions=False)
        self.addCleanup(self.client.close)

    def body(self, *, user='invented-user', request='invented-write', session='invented-session', content=None):
        return dict(request_id=request,user_id=user,session_id=session,messages=[dict(role='user',
            content='def repair_fern_lock():\n    return "archived engineering evidence"\n' if content is None else content)])

    def add(self, body):
        return self.client.post('/add',json=body,headers=AUTH)

    def search(self, *, user='invented-user', query='repair_fern_lock', top_k=100, **extra):
        return self.client.post('/search',json=dict(user_id=user,query=query,top_k=top_k,**extra),headers=AUTH)

    def test_public_health_identifies_only_coding_version_and_models(self):
        response = self.client.get('/health')
        self.assertEqual(response.status_code,200)
        health = response.json()
        self.assertEqual(health['track'],'coding')
        self.assertEqual(health['version'],'v4.1.1')
        self.assertEqual(health['embedding_model'],'text-embedding-v4')
        self.assertEqual(health['source_revision'],'invented-test-revision')
        self.assertNotIn('offline-coding-auth',response.text)
        self.assertEqual(self.embed.call_count,0)

    def test_add_and_search_require_coding_auth_before_embedding(self):
        for headers in ({},{'Authorization':'Bearer forbidden-text-auth'},{'X-Api-Key':'incorrect'}):
            with self.subTest(headers=headers):
                self.assertEqual(self.client.post('/add',json=self.body(),headers=headers).status_code,401)
                self.assertEqual(self.client.post('/search',json=dict(user_id='invented-user',query='lock',top_k=1),headers=headers).status_code,401)
        self.assertEqual(self.embed.call_count,0)

    def test_supported_authorization_schemes_share_the_same_contract(self):
        for index,headers in enumerate((AUTH,{'Authorization':'Token offline-coding-auth'},
                                      {'X-Api-Key':'offline-coding-auth'})):
            body = self.body(request='authorization-'+str(index))
            response = self.client.post('/add',json=body,headers=headers)
            self.assertEqual(response.status_code,200)
            self.assertIs(response.json()['success'],True)
            for key in ('request_id','user_id','session_id'):
                self.assertEqual(response.json()[key],body[key])

    def test_add_is_immediately_searchable_and_replay_avoids_duplicate_vectors(self):
        body = self.body()
        first = self.add(body)
        second = self.add(body)
        self.assertEqual(first.status_code,200)
        self.assertEqual(first.json(),second.json())
        self.assertEqual(self.embed.call_count,1)
        rows = self.search().json()['data']
        self.assertEqual(len(rows),1)
        self.assertIn('repair_fern_lock',rows[0]['content'])
        with self.memory._connect() as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM coding_items').fetchone()[0],1)

    def test_conflicting_replay_is_409_and_does_not_overwrite_source(self):
        body = self.body()
        self.assertEqual(self.add(body).status_code,200)
        modified = self.body(content='This is an unrelated invented source.')
        response = self.add(modified)
        self.assertEqual(response.status_code,409)
        self.assertEqual(response.json()['detail']['reason'],'request_id_conflict')
        self.assertEqual(self.embed.call_count,1)
        with self.memory._connect() as connection:
            self.assertEqual(connection.execute('SELECT content FROM coding_items').fetchone()[0],body['messages'][0]['content'])

    def test_same_request_id_is_independent_between_users(self):
        self.assertEqual(self.add(self.body()).status_code,200)
        self.assertEqual(self.add(self.body(user='another-invented-user',content='def other_identifier(): pass')).status_code,200)
        own = self.search().json()['data']
        self.assertEqual(len(own),1)
        self.assertNotIn('other_identifier',own[0]['content'])

    def test_user_scope_includes_prior_sessions_and_excludes_foreign_memory(self):
        self.add(self.body(session='older-repository-task'))
        self.add(self.body(session='newer-repository-task',request='later',content='def prepare_new_repository_task(): pass'))
        self.add(self.body(user='foreign-invented-user',request='foreign',content='repair_fern_lock FOREIGN-CODING-SECRET'))
        rows = self.search().json()['data']
        self.assertTrue(any(row['session_id']=='older-repository-task' and 'repair_fern_lock' in row['content'] for row in rows))
        self.assertNotIn('FOREIGN-CODING-SECRET',str(rows))

    def test_empty_user_scope_returns_an_empty_data_array_without_model_work(self):
        response = self.search(user='never-written-invented-user')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json(),{'data':[]})
        self.assertEqual(self.embed.call_count,0)

    def test_search_preserves_query_options_do_not_become_generated_answers(self):
        self.add(self.body())
        query = 'Which archived repair mentions repair_fern_lock?'
        with patch.object(self.memory,'search',wraps=self.memory.search) as search:
            response = self.search(query=query,options=['A. invent a solution','B. execute untrusted code'])
        self.assertEqual(response.status_code,200)
        search.assert_called_once_with('invented-user',query,100)
        text = str(response.json()['data'])
        self.assertNotIn('A. invent a solution',text)
        self.assertNotIn('B. execute untrusted code',text)

    def test_code_payload_over_twenty_messages_preserves_order_roles_and_timestamps(self):
        body = self.body()
        body['messages']=[dict(role='assistant' if index%2 else 'user',
            content='def invented_step_'+str(index)+'():\n\treturn "literal whitespace"\n',
            timestamp=1743465600000+index) for index in range(25)]
        self.assertEqual(self.add(body).status_code,200)
        with self.memory._connect() as connection:
            stored = list(connection.execute('SELECT role,content,timestamp_ms,seq FROM coding_items ORDER BY seq'))
        self.assertEqual([(row['role'],row['content'],row['timestamp_ms'],row['seq']) for row in stored],
                         [(message['role'],message['content'],message['timestamp'],index+1)
                          for index,message in enumerate(body['messages'])])

    def test_long_code_stays_complete_in_storage_and_returned_windows_are_literal(self):
        source = '\r\n'.join('ordinary_archive_'+str(index)+' = "keep exact source"' for index in range(220))
        source += '\r\ndef repair_fern_lock():\r\n\treturn "evidence-only"\r\n'
        self.assertEqual(self.add(self.body(content=source)).status_code,200)
        row = self.search(top_k=1).json()['data'][0]
        self.assertIn('repair_fern_lock',row['content'])
        with self.memory._connect() as connection:
            self.assertEqual(connection.execute('SELECT content FROM coding_items').fetchone()[0],source)
        for start,end in row['source_char_ranges']:
            self.assertIn(source[start:end],row['content'])

    def test_result_schema_ids_and_order_are_stable_and_bounded(self):
        body = self.body()
        body['messages']=[dict(role='user',content='def invented_identifier_'+str(index)+'(): pass') for index in range(6)]
        self.add(body)
        first = self.search(query='invented_identifier_4',top_k=3)
        second = self.search(query='invented_identifier_4',top_k=3)
        self.assertEqual(first.status_code,200)
        rows = first.json()['data']
        self.assertLessEqual(len(rows),3)
        self.assertEqual([row['id'] for row in rows],[row['id'] for row in second.json()['data']])
        for row in rows:
            self.assertIsInstance(row['id'],str)
            self.assertTrue(row['id'])
            self.assertIsInstance(row['content'],str)
            self.assertTrue(row['content'].strip())
            self.assertIsInstance(row['score'],(int,float))
            self.assertTrue(math.isfinite(row['score']))
        self.assertEqual([row['score'] for row in rows],sorted((row['score'] for row in rows),reverse=True))

    def test_invalid_coding_content_and_whitespace_identifiers_are_rejected(self):
        for content in (None,'','  ',[],[{'type':'text','text':'code'}],{'text':'unsupported'},123):
            with self.subTest(content=content):
                body = self.body()
                body['messages'][0]['content']=content
                self.assertEqual(self.add(body).status_code,422)
        for field in ('request_id','user_id','session_id'):
            body = self.body()
            body[field]='  '
            self.assertEqual(self.add(body).status_code,422)
        self.assertEqual(self.embed.call_count,0)

    def test_contract_roles_and_numeric_fields_are_not_coerced_from_invalid_types(self):
        for role in ('system','tool','invented-role'):
            with self.subTest(role=role):
                body = self.body(request='invalid-role-'+role)
                body['messages'][0]['role']=role
                self.assertEqual(self.add(body).status_code,422)
        with self.subTest(field='timestamp'):
            body = self.body(request='invalid-timestamp-boolean')
            body['messages'][0]['timestamp']=True
            self.assertEqual(self.add(body).status_code,422)
        with self.subTest(field='top_k'):
            self.assertEqual(self.search(top_k=True).status_code,422)
        self.assertEqual(self.embed.call_count,0)

    def test_missing_coding_auth_configuration_fails_closed(self):
        with TestClient(api_module.create_app(self.memory,replace(self.settings,auth_token='')),
                        raise_server_exceptions=False) as client:
            self.assertEqual(client.get('/health').status_code,200)
            response = client.post('/add',json=self.body(),headers=AUTH)
            self.assertEqual(response.status_code,503)
            self.assertEqual(response.json()['detail']['reason'],'coding_auth_not_configured')
        self.assertEqual(self.embed.call_count,0)

    def test_store_identifier_capacity_errors_are_client_errors_without_model_work(self):
        for field in ('request_id','user_id','session_id'):
            with self.subTest(field=field):
                body = self.body()
                body[field]='x'*1025
                self.assertEqual(self.add(body).status_code,422)
        self.assertEqual(self.search(user='x'*1025).status_code,422)
        self.assertEqual(self.embed.call_count,0)

    def test_provider_failure_is_retryable_and_never_echoes_source_or_credentials(self):
        body = self.body()
        with patch.object(self.memory,'embed',side_effect=RuntimeError('INVENTED-SECRET-KEY SOURCE-CONTENT')):
            response = self.add(body)
        self.assertEqual(response.status_code,503)
        self.assertNotIn('INVENTED-SECRET-KEY',response.text)
        self.assertNotIn('SOURCE-CONTENT',response.text)
        self.assertEqual(self.add(body).status_code,200)
        with patch.object(self.memory,'embed',side_effect=RuntimeError('INVENTED-SECRET-KEY')):
            response = self.search()
        self.assertEqual(response.status_code,503)
        self.assertNotIn('INVENTED-SECRET-KEY',response.text)

if __name__ == '__main__':
    unittest.main()
