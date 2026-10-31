"""Undeployed method policy tests: fictional inputs, fake client, in-memory DB."""
import array
import ast
from contextlib import closing
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
import time
import unittest
from unittest.mock import patch

import add_index
import add_memory
from add_memory import AddMemoryError, AddMemoryExtractor


SOURCE = Path(__file__).resolve().parent


def configuration():
    return SimpleNamespace(add_llm_api_key='', add_llm_base_url='https://api.openai.com/v1',
        add_llm_model='gpt-4o-mini', add_llm_timeout=60, add_llm_total_timeout=180,
        add_llm_concurrency=2, add_llm_chunk_chars=12000, add_llm_max_output_tokens=2048)


def response(selections=None, **overrides):
    result = {'model':'gpt-4o-mini-2024-07-18', 'choices':[{'finish_reason':'stop',
        'message':{'content':json.dumps({'selections':selections or []}), 'refusal':None}}],
        'usage':{'prompt_tokens':87, 'completion_tokens':23}}
    result.update(overrides)
    return result


def selection(quote, *, mi=0, si=0, kind='fact'):
    return {'message_index':mi, 'segment_index':si, 'quote':quote, 'kind':kind}


class Client:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.handler(kwargs)


def extractor(handler, policy):
    client = Client(handler)
    selected = {None:'strict', 'store_raw':'raw_on_unmatched', 'fail':'fail_if_all_unmatched'}[policy]
    return AddMemoryExtractor(configuration(), client, selection_failure_policy=selected), client


def unchanged_storage_fixture(connection, extraction):
    # Compile only the actual two unchanged store methods. Never import store,
    # deployment config, credentials, provider clients, or an operational DB.
    tree = ast.parse((SOURCE/'store.py').read_text(encoding='utf-8'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name=='MemoryStore')
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef)
               and n.name in {'_add_messages', '_replayed_add_count'}]
    class Conflict(ValueError):
        pass
    ns = {'json':json, 'hashlib':hashlib, 'time':time, 'sqlite3':sqlite3, 'AddRequestConflict':Conflict,
          'add_index':add_index, '_pack':lambda vector:array.array('f', vector).tobytes(),
          'fact_layer':SimpleNamespace(store_facts=lambda *args:None, update_digest=lambda *args:None)}
    exec(compile(ast.Module(body=methods, type_ignores=[]), '<unchanged-store-methods>', 'exec'), ns)
    class Store:
        _add_messages = ns['_add_messages']
        _replayed_add_count = ns['_replayed_add_count']
        def __init__(self):
            self._add_extractor = extraction
            self.embedding_calls = 0
        def _connect(self):
            return connection
        def embed(self, texts):
            self.embedding_calls += 1
            return [[1.0, 0.0] for _ in texts]
    connection.row_factory = sqlite3.Row
    connection.executescript('''
      CREATE TABLE items (rid INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT,
        session_id TEXT,role TEXT,content TEXT,created_at REAL,timestamp_origin TEXT,
        status TEXT,seq INTEGER);
      CREATE TABLE vec_items (rowid INTEGER PRIMARY KEY,embedding BLOB);
      CREATE TABLE add_requests (user_id TEXT,request_id TEXT,session_id TEXT,
        payload_hash TEXT,written INTEGER,PRIMARY KEY(user_id,request_id));
    ''')
    add_index.ensure_schema(connection)
    connection.commit()
    return Store()


class SelectionResilienceTests(unittest.TestCase):
    def setUp(self):
        self.log_patch = patch.object(add_memory._LOGGER, 'warning')
        self.logger = self.log_patch.start()
        self.addCleanup(self.log_patch.stop)

    def test_default_retains_original_strict_failure(self):
        item, client = extractor(lambda _:response([selection('invented missing quote')]), None)
        with self.assertRaisesRegex(AddMemoryError, 'quotation is not present'):
            item.extract([{'role':'user', 'content':'The fictional valve is closed.'}])
        self.assertEqual(len(client.calls), 1)

    def test_mixed_quotes_keep_only_exact_original_spans_under_both_policies(self):
        source = 'The fictional valve is closed. Café is available.'
        for policy in ('store_raw', 'fail'):
            with self.subTest(policy=policy):
                item, client = extractor(lambda _:response([
                    selection('valve is closed'), selection('valve is open'), selection('Cafe\u0301')]), policy)
                result = item.extract([{'role':'user','content':source}])
                self.assertEqual([source[s.start:s.end] for s in result.spans], ['valve is closed'])
                self.assertEqual(result.rejected_ungrounded_quotes, 2)
                self.assertEqual((result.prompt_tokens, result.completion_tokens, result.call_count), (87,23,1))
                self.assertEqual(len(client.calls), 1)

    def test_all_ungrounded_store_raw_returns_zero_spans_after_a_valid_model_call(self):
        item, client = extractor(lambda _:response([selection('different invented statement')]), 'store_raw')
        result = item.extract([{'role':'user','content':'The original invented statement.'}])
        self.assertEqual(result.spans, [])
        self.assertEqual(result.rejected_ungrounded_quotes, 1)
        self.assertEqual((result.call_count, result.model), (1,'gpt-4o-mini-2024-07-18'))
        self.assertEqual(len(client.calls), 1)

    def test_all_ungrounded_fail_policy_raises_after_validating_the_response(self):
        item, client = extractor(lambda _:response([selection('different invented statement')]), 'fail')
        with self.assertRaisesRegex(AddMemoryError, '^Add model returned no grounded memory selections$'):
            item.extract([{'role':'user','content':'The original invented statement.'}])
        self.assertEqual(len(client.calls), 1)

    def test_unknown_or_misattributed_integer_source_references_are_rejected_not_reassigned(self):
        for invalid in (selection('first source', mi=1, si=0),
                        selection('second source', mi=1, si=999),
                        selection('first source', mi=-1, si=-1)):
            with self.subTest(invalid=invalid):
                item, _ = extractor(lambda _, value=invalid:response([selection('first source'),value]),'store_raw')
                result = item.extract([{'role':'user','content':'first source'},
                                       {'role':'assistant','content':'second source'}])
                self.assertEqual(len(result.spans),1)
                self.assertEqual(result.spans[0].message_index,0)
                self.assertEqual(result.rejected_source_references,1)
                self.assertEqual(result.rejected_ungrounded_quotes,0)

    def test_all_invalid_references_follow_both_explicit_whole_add_policies(self):
        for policy in ('store_raw','fail'):
            item, client = extractor(lambda _:response([selection('source',mi=0,si=99)]),policy)
            if policy=='fail':
                with self.assertRaisesRegex(AddMemoryError,'no grounded memory selections'):
                    item.extract([{'role':'user','content':'source'}])
            else:
                result = item.extract([{'role':'user','content':'source'}])
                self.assertEqual(result.spans,[])
                self.assertEqual((result.rejected_ungrounded_quotes,result.rejected_source_references),(0,1))
            self.assertEqual(len(client.calls),1)

    def test_strict_unknown_reference_retains_original_error_and_priority(self):
        for bad in (selection('source',mi=1,si=0),selection('',mi=1,si=0)):
            item, _ = extractor(lambda _, value=bad:response([value]),None)
            with self.assertRaisesRegex(AddMemoryError,'^Add model returned an unknown source reference$'):
                item.extract([{'role':'user','content':'source'}])

    def test_unknown_reference_does_not_bypass_quote_type_length_or_kind_validation(self):
        for bad in (selection('',mi=1),selection('source',mi=1,kind='answer'),
                    selection('x'*241,mi=1),selection(123,mi=1)):
            item, _ = extractor(lambda _, value=bad:response([value]),'store_raw')
            with self.assertRaisesRegex(AddMemoryError,'^Add model returned invalid quotation or memory kind$'):
                item.extract([{'role':'user','content':'source'}])

    def test_fixed_rejection_logs_contain_only_codes_and_integer_counts(self):
        item, _ = extractor(lambda _:response([selection('source'),selection('private invented quote'),
                                               selection('source',si=99)]),'store_raw')
        result = item.extract([{'role':'user','content':'source'}])
        self.assertEqual((result.rejected_ungrounded_quotes,result.rejected_source_references),(1,1))
        calls = [call.args for call in self.logger.call_args_list]
        self.assertEqual(calls,[('add_selection_rejected code=%s count=%d','quotation_not_grounded',1),
                                ('add_selection_rejected code=%s count=%d','source_reference_invalid',1)])
        self.assertNotIn('private invented quote',repr(calls))

    def test_unavailable_diagnostic_sink_cannot_change_safe_extraction_success(self):
        self.logger.side_effect = RuntimeError('invented diagnostic sink failure')
        item, _ = extractor(lambda _:response([selection('invented missing')]),'store_raw')
        result = item.extract([{'role':'user','content':'source'}])
        self.assertEqual((result.spans,result.rejected_ungrounded_quotes),([],1))

    def test_genuine_empty_model_selections_still_succeed_in_both_policies(self):
        for policy in ('store_raw','fail'):
            item, client = extractor(lambda _:response(), policy)
            result = item.extract([{'role':'user','content':'Hello fictional reader.'}])
            self.assertEqual((result.spans, result.rejected_ungrounded_quotes, result.call_count), ([],0,1))
            self.assertEqual(len(client.calls), 1)

    def test_valid_selection_in_later_chunk_satisfies_fail_policy_for_whole_add(self):
        def handler(kwargs):
            segments = json.loads(kwargs['messages'][1]['content'])['source_segments']
            segment = segments[0]
            quote = 'invented missing quote' if segment['segment_index']==0 else segment['text'][:30]
            return response([selection(quote, mi=segment['message_index'], si=segment['segment_index'])])
        item, client = extractor(handler, 'fail')
        source = 'Larch fictional evidence. ' * 1000
        result = item.extract([{'role':'user','content':source}])
        self.assertGreater(len(client.calls), 1)
        self.assertTrue(result.spans)
        self.assertEqual(result.rejected_ungrounded_quotes, 1)
        self.assertEqual(result.call_count, len(client.calls))
        self.assertEqual(result.prompt_tokens, 87*len(client.calls))

    def test_invalid_usage_model_refusal_or_truncation_still_fail(self):
        missing = [selection('invented missing quote')]
        cases = [response(missing, model='different-model'),
                 response(missing, usage={'prompt_tokens':True,'completion_tokens':23}),
                 response(missing, choices=[{'finish_reason':'length','message':{'content':'{}'}}]),
                 response(missing, choices=[{'finish_reason':'stop','message':{'refusal':'Invented refusal','content':'{}'}}])]
        for policy in ('store_raw','fail'):
            for case in cases:
                with self.subTest(policy=policy, case=case):
                    item, _ = extractor(lambda _, value=case:value, policy)
                    with self.assertRaises(AddMemoryError):
                        item.extract([{'role':'user','content':'The original invented statement.'}])

    def test_invalid_selection_schema_references_kind_or_blank_quote_still_fail(self):
        invalid = [selection('source', mi=True), selection('source', si=False),
                   selection('source', kind='answer'), selection(''), selection(' '*4),
                   selection('x'*241), {'quote':'source'}]
        for policy in ('store_raw','fail'):
            for bad in invalid:
                with self.subTest(policy=policy, bad=bad):
                    item, _ = extractor(lambda _, value=bad:response([value]), policy)
                    with self.assertRaises(AddMemoryError):
                        item.extract([{'role':'user','content':'source'}])

    def test_provider_failure_is_never_converted_to_model_free_success(self):
        def fail(_):
            raise RuntimeError('invented provider failure')
        for policy in ('store_raw','fail'):
            item, client = extractor(fail, policy)
            with self.assertRaisesRegex(AddMemoryError, '^Required Add model request failed$'):
                item.extract([{'role':'user','content':'Invented original source.'}])
            self.assertEqual(len(client.calls), 1)

    def test_previous_success_common_outputs_match_original_source(self):
        source = 'tea / tea / exact Cafe\u0301 evidence'
        handler = lambda _:response([selection('tea'), selection('Cafe\u0301'), selection('tea')])
        # Golden public fields previously verified against the strict v3.16.0
        # implementation; keep this release's tests self-contained.
        expected = [(0,0,3,'fact'), (0,18,23,'fact')]
        for policy in ('store_raw','fail'):
            item, _ = extractor(handler, policy)
            new = item.extract([{'role':'user','content':source}])
            self.assertEqual(expected, [tuple(vars(s).values()) for s in new.spans])
            self.assertEqual((new.prompt_tokens,new.completion_tokens,new.model,new.call_count),
                             (87,23,'gpt-4o-mini-2024-07-18',1))
            self.assertEqual(new.rejected_ungrounded_quotes, 0)

    def test_raw_source_usage_valid_spans_and_receipt_commit_together(self):
        for policy, selections, expected_spans in (
                ('store_raw',[selection('Invented missing quote')],0),
                ('store_raw',[selection('source',si=99)],0),
                ('store_raw',[selection('valve is closed'),selection('Invented missing quote')],1),
                ('fail',[selection('valve is closed'),selection('Invented missing quote')],1)):
            with self.subTest(policy=policy, expected_spans=expected_spans), closing(sqlite3.connect(':memory:')) as conn:
                item, client = extractor(lambda _, picks=selections:response(picks), policy)
                storage = unchanged_storage_fixture(conn, item)
                messages = [{'role':'user','content':'The fictional valve is closed.'},
                            {'role':'assistant','content':'This entire second raw message remains present.'}]
                count = storage._add_messages('invented-owner','invented-session',messages,request_id='invented-request')
                self.assertEqual(count, 2)
                stored = conn.execute('SELECT role,content FROM items ORDER BY rid').fetchall()
                self.assertEqual([tuple(row) for row in stored], [(m['role'],m['content']) for m in messages])
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM llm_memory_spans').fetchone()[0], expected_spans)
                usage = conn.execute('SELECT prompt_tokens,completion_tokens,call_count FROM add_llm_usage').fetchone()
                self.assertEqual(tuple(usage), (87,23,1))
                self.assertEqual(conn.execute('SELECT written FROM add_requests').fetchone()[0], 2)
                self.assertEqual(storage._add_messages('invented-owner','invented-session',messages,request_id='invented-request'),2)
                self.assertEqual((len(client.calls),storage.embedding_calls),(1,1))

    def test_all_invalid_fail_or_provider_error_leaves_no_partial_source_or_receipt(self):
        def provider_fail(_):
            raise RuntimeError('invented provider failure')
        for handler, policy in ((lambda _:response([selection('missing')]),'fail'),
                                (provider_fail,'store_raw')):
            with closing(sqlite3.connect(':memory:')) as conn:
                item, _ = extractor(handler,policy)
                storage = unchanged_storage_fixture(conn,item)
                with self.assertRaises(AddMemoryError):
                    storage._add_messages('invented-owner','invented-session',
                        [{'role':'user','content':'Original source.'}], request_id='invented-request')
                for table in ('items','vec_items','llm_memory_spans','add_llm_usage','add_requests'):
                    self.assertEqual(conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)

    def test_storage_error_rolls_back_raw_source_spans_usage_and_receipt(self):
        with closing(sqlite3.connect(':memory:')) as conn:
            item, _ = extractor(lambda _:response([selection('missing')]),'store_raw')
            storage = unchanged_storage_fixture(conn,item)
            original = add_index.store_extraction
            def fail_after_usage(*args,**kwargs):
                original(*args,**kwargs)
                raise RuntimeError('invented storage failure')
            with patch.object(add_index,'store_extraction',side_effect=fail_after_usage):
                with self.assertRaisesRegex(RuntimeError,'invented storage failure'):
                    storage._add_messages('invented-owner','invented-session',
                        [{'role':'user','content':'Original source.'}],request_id='invented-request')
            for table in ('items','vec_items','llm_memory_spans','add_llm_usage','add_requests'):
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)

    def test_policy_must_be_explicit_and_from_fixed_choices(self):
        with self.assertRaises(ValueError):
            AddMemoryExtractor(configuration(),Client(lambda _:response()),selection_failure_policy='invented-other')


if __name__=='__main__':
    unittest.main(verbosity=2)
