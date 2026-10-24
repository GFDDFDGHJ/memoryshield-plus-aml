"""Offline gateway contracts: invented credentials, fake upstreams, no sockets."""
from concurrent.futures import ThreadPoolExecutor
import asyncio
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import sys
import threading
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))  # Match the gateway's standalone --app-dir import.
SPEC = importlib.util.spec_from_file_location('offline_track_gateway', ROOT / 'gateway.py')
gateway = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = gateway
with patch.object(Path, 'read_text', side_effect=AssertionError('Secrets must not be read')):
    SPEC.loader.exec_module(gateway)

TEXT_KEY = 'invented-text-key-for-offline-contract'
CODE_KEY = 'invented-coding-key-for-offline-contract'
SETTINGS = gateway.GatewaySettings(TEXT_KEY, CODE_KEY)


class GatewayContractTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.lock = threading.Lock()
        self.respond = lambda request: httpx.Response(200, content=b'{"data":[]}')

        def upstream(request):
            with self.lock:
                self.calls.append(request)
            return self.respond(request)

        self.settings_guard = patch.object(gateway, 'load_settings', side_effect=AssertionError('No live secrets'))
        self.settings_guard.start()
        self.addCleanup(self.settings_guard.stop)
        self.client = TestClient(gateway.create_app(SETTINGS, transport=httpx.MockTransport(upstream)))
        self.client.__enter__()
        # Windows asyncio creates an internal socketpair when its event loop
        # starts. Block connects after that initialization; upstream transport
        # is already a fake before lifespan starts.
        self.socket_guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('No real sockets'))
        self.socket_guard.start()
        self.addCleanup(self.socket_guard.stop)

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.settings_guard.stop()
        self.socket_guard.stop()

    def send(self, path='/add', *, key=TEXT_KEY, body=b'{}', headers=None):
        supplied = {'authorization': 'Bearer ' + key, 'content-type': 'application/json'}
        if headers:
            supplied.update(headers)
        return self.client.post(path, content=body, headers=supplied)

    def test_add_and_search_preserve_exact_request_bytes_and_track(self):
        payload = ('{ "request_id":"invented-r", "user_id":"invented-u",'
                   ' "session_id":"invented-s", "messages":[{"role":"user",'
                   '"content":"def \u03b1():\\r\\n\\treturn 17  \\n"}],'
                   ' "unknown":{"x":1,"x":2}, "options":["A","B"] }\r\n').encode()
        for path in ('/add', '/search'):
            for key, port, track in ((TEXT_KEY, 8080, 'textual'), (CODE_KEY, 8181, 'coding')):
                with self.subTest(path=path, track=track):
                    result = self.send(path, key=key, body=payload)
                    request = self.calls[-1]
                    self.assertEqual(result.status_code, 200)
                    self.assertEqual(result.headers['x-memory-track'], track)
                    self.assertEqual(str(request.url), f'http://127.0.0.1:{port}{path}')
                    self.assertEqual(request.method, 'POST')
                    self.assertEqual(request.content, payload)
                    self.assertEqual(request.headers['authorization'], 'Bearer ' + key)
                    self.assertEqual(request.headers['content-type'], 'application/json')

    def test_large_code_payload_is_not_clipped_or_reencoded(self):
        code = 'def invented_handler_\u03b1():\r\n\treturn "\u6c49\u5b57"  \r\n' * 40000
        payload = json.dumps({'user_id': 'invented-large', 'messages': [{'role': 'user', 'content': code}]},
                             ensure_ascii=False, indent=2).encode('utf-8')
        self.assertGreater(len(payload), 1000000)
        self.assertEqual(self.send(key=CODE_KEY, body=payload).status_code, 200)
        self.assertEqual(len(self.calls[-1].content), len(payload))
        self.assertEqual(hashlib.sha256(self.calls[-1].content).digest(), hashlib.sha256(payload).digest())

    def test_gateway_does_not_parse_or_repair_upstream_payload(self):
        payload = b'  { invalid json, "duplicate": 1, "duplicate": 2 }\x00\r\n'
        self.respond = lambda request: httpx.Response(422, content=b'{"detail":"invented validation"}')
        result = self.send(key=CODE_KEY, body=payload)
        self.assertEqual(result.status_code, 422)
        self.assertEqual(self.calls[-1].content, payload)
        self.assertEqual(result.content, b'{"detail":"invented validation"}')

    def test_health_without_key_is_text_and_coding_key_selects_coding(self):
        self.respond = lambda request: httpx.Response(200, json={'status': 'ok', 'port': request.url.port})
        for headers, port, track in (({}, 8080, 'textual'),
                                     ({'Authorization': 'Bearer ' + TEXT_KEY}, 8080, 'textual'),
                                     ({'Authorization': 'Bearer ' + CODE_KEY}, 8181, 'coding'),
                                     ({'X-Api-Key': CODE_KEY}, 8181, 'coding')):
            with self.subTest(track=track, headers=list(headers)):
                response = self.client.get('/health', headers=headers)
                request = self.calls[-1]
                self.assertEqual(response.json(), {'status': 'ok', 'port': port})
                self.assertEqual(response.headers['x-memory-track'], track)
                self.assertEqual(str(request.url), f'http://127.0.0.1:{port}/health')
                self.assertEqual(request.method, 'GET')

    def test_internal_health_does_not_contact_either_backend(self):
        result = self.client.get('/__gateway_health')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['system'], 'MemoryShield+ track gateway')
        self.assertEqual(self.calls, [])
        self.assertNotIn(TEXT_KEY, result.text)
        self.assertNotIn(CODE_KEY, result.text)

    def test_missing_and_wrong_credentials_never_call_upstream(self):
        for path in ('/add', '/search'):
            for headers in ({}, {'Authorization': 'Bearer invented-wrong'}, {'X-Api-Key': 'invented-wrong'}):
                with self.subTest(path=path, headers=headers):
                    result = self.client.post(path, content=b'{not-json', headers=headers)
                    self.assertEqual(result.status_code, 401)
        self.assertEqual(self.client.get('/health', headers={'Authorization': 'Bearer invented-wrong'}).status_code, 401)
        self.assertEqual(self.calls, [])

    def test_conflicting_authentication_is_rejected_before_upstream(self):
        result = self.client.post('/search', content=b'{}', headers={
            'Authorization': 'Bearer ' + TEXT_KEY, 'X-Api-Key': CODE_KEY})
        self.assertEqual(result.status_code, 401)
        self.assertEqual(self.calls, [])

    def test_duplicate_authentication_headers_are_rejected_even_if_equal(self):
        for name, value in (('Authorization', 'Bearer ' + CODE_KEY), ('X-Api-Key', TEXT_KEY)):
            with self.subTest(name=name):
                result = self.client.post('/add', content=b'{}', headers=[(name, value), (name, value)])
                self.assertEqual(result.status_code, 401)
        self.assertEqual(self.calls, [])

    def test_supported_credentials_are_normalized_to_the_selected_backend(self):
        for headers in ({'Authorization': 'tOkEn ' + CODE_KEY},
                        {'X-Api-Key': CODE_KEY},
                        {'Authorization': 'Bearer ' + CODE_KEY, 'X-Api-Key': CODE_KEY}):
            with self.subTest(headers=list(headers)):
                result = self.client.post('/search', content=b'{}', headers=headers)
                self.assertEqual(result.status_code, 200)
                self.assertEqual(self.calls[-1].url.port, 8181)
                self.assertEqual(self.calls[-1].headers['authorization'], 'Bearer ' + CODE_KEY)
                self.assertNotIn('x-api-key', self.calls[-1].headers)

    def test_payload_queries_and_forwarding_headers_cannot_choose_a_backend(self):
        body = b'{"track":"textual","url":"https://invalid.example","user_id":"invented-u"}'
        result = self.send('/search?track=textual&url=https%3A%2F%2Finvalid.example', key=CODE_KEY, body=body,
                           headers={'X-Memory-Track': 'textual', 'X-Forwarded-Host': 'invalid.example',
                                    'Forwarded': 'host=invalid.example;proto=https', 'Host': 'invalid.example'})
        self.assertEqual(result.status_code, 200)
        request = self.calls[-1]
        self.assertEqual(str(request.url), 'http://127.0.0.1:8181/search')
        self.assertEqual(request.content, body)
        self.assertEqual(request.headers['host'], '127.0.0.1:8181')
        for name in ('x-memory-track', 'x-forwarded-host', 'forwarded'):
            self.assertNotIn(name, request.headers)

    def test_upstream_status_body_and_retry_after_remain_intact(self):
        payload = b'  {"detail":{"reason":"invented_quota"}}\r\n'
        for status in (200, 400, 401, 409, 422, 429, 500, 503):
            with self.subTest(status=status):
                self.respond = lambda request: httpx.Response(status, content=payload,
                    headers={'content-type': 'application/problem+json; charset=utf-8', 'retry-after': '13'})
                result = self.send('/search', key=CODE_KEY)
                self.assertEqual(result.status_code, status)
                self.assertEqual(result.content, payload)
                self.assertEqual(result.headers['content-type'], 'application/problem+json; charset=utf-8')
                self.assertEqual(result.headers['retry-after'], '13')
                self.assertEqual(result.headers['x-memory-track'], 'coding')
                self.assertEqual(int(result.headers['content-length']), len(payload))

    def test_backend_redirect_is_not_followed(self):
        self.respond = lambda request: httpx.Response(307, content=b'invented redirect',
                                                     headers={'location': 'https://invalid.example/steal'})
        result = self.send(key=CODE_KEY)
        self.assertEqual(result.status_code, 307)
        self.assertEqual(result.content, b'invented redirect')
        self.assertEqual(len(self.calls), 1)

    def test_decoded_upstream_response_has_no_stale_encoding_or_length(self):
        decoded = b'{"data":[{"id":"invented-r","content":"intact"}]}'
        compressed = gzip.compress(decoded)
        self.respond = lambda request: httpx.Response(200, content=compressed,
            headers={'content-encoding': 'gzip', 'content-length': str(len(compressed))})
        result = self.send('/search')
        self.assertEqual(result.content, decoded)
        self.assertNotIn('content-encoding', result.headers)
        self.assertEqual(int(result.headers['content-length']), len(decoded))

    def test_timeout_returns_sanitized_504_without_other_backend_fallback(self):
        def failing(request):
            raise httpx.ReadTimeout('invented private provider detail ' + CODE_KEY, request=request)
        self.respond = failing
        result = self.send('/search', key=CODE_KEY)
        self.assertEqual(result.status_code, 504)
        self.assertEqual(result.json()['detail']['reason'], 'memory_upstream_timeout')
        self.assertNotIn(CODE_KEY, result.text)
        self.assertEqual([request.url.port for request in self.calls], [8181])

    def test_connect_failure_returns_sanitized_503_without_other_backend_fallback(self):
        def failing(request):
            raise httpx.ConnectError('invented private provider detail ' + TEXT_KEY, request=request)
        self.respond = failing
        result = self.send(key=TEXT_KEY)
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json()['detail']['reason'], 'memory_upstream_unavailable')
        self.assertNotIn(TEXT_KEY, result.text)
        self.assertEqual([request.url.port for request in self.calls], [8080])

    def test_parallel_tracks_keep_request_credentials_and_bytes_isolated(self):
        def reflect(request):
            status = 201 if request.url.port == 8080 else 202
            return httpx.Response(status, content=request.content, headers={'content-type': 'application/octet-stream'})
        self.respond = reflect

        def invoke(index):
            key, port = (TEXT_KEY, 8080) if index % 2 == 0 else (CODE_KEY, 8181)
            body = json.dumps({'user_id': 'invented-parallel-' + str(index), 'index': index}).encode()
            response = self.send('/search', key=key, body=body)
            return body, port, response

        with ThreadPoolExecutor(max_workers=16) as executor:
            outcomes = list(executor.map(invoke, range(32)))
        self.assertEqual(len(self.calls), 32)
        observed = {request.content: request for request in self.calls}
        for body, port, response in outcomes:
            self.assertEqual(response.content, body)
            self.assertEqual(response.status_code, 201 if port == 8080 else 202)
            self.assertEqual(response.headers['x-memory-track'], 'textual' if port == 8080 else 'coding')
            self.assertEqual(observed[body].url.port, port)
            expected_key = TEXT_KEY if port == 8080 else CODE_KEY
            self.assertEqual(observed[body].headers['authorization'], 'Bearer ' + expected_key)

    def test_unknown_methods_paths_and_schema_routes_do_not_touch_backends(self):
        for method, path, status in (('GET', '/add', 405), ('POST', '/health', 405),
                                     ('POST', '/search/unknown', 404), ('GET', '/docs', 404),
                                     ('GET', '/openapi.json', 404)):
            with self.subTest(method=method, path=path):
                self.assertEqual(self.client.request(method, path).status_code, status)
        self.assertEqual(self.calls, [])

    def test_settings_fail_closed_for_missing_equal_or_nonlocal_destinations(self):
        for text_key, code_key, kwargs in (('', CODE_KEY, {}), (TEXT_KEY, '', {}), (TEXT_KEY, TEXT_KEY, {}),
            (TEXT_KEY, CODE_KEY, {'text_url': 'http://127.0.0.1:8282'}),
            (TEXT_KEY, CODE_KEY, {'coding_url': 'https://invalid.example'})):
            with self.subTest(kwargs=kwargs, empty=not bool(text_key and code_key)):
                with self.assertRaises(ValueError):
                    gateway.GatewaySettings(text_key, code_key, **kwargs)


class LegacyModeGatewayContractTests(unittest.TestCase):
    """Only fake keys/mode readers/transports: never a live mode file."""
    def setUp(self):
        self.calls=[]
        self.call_lock=threading.Lock()
        self.respond=lambda request:httpx.Response(200,content=request.content)
        self.started=threading.Event()
        self.release=threading.Event()
        self.block_legacy=False
        self.client=None
        self.settings_guard=patch.object(gateway,'load_settings',side_effect=AssertionError('No live secrets'))
        self.settings_guard.start()
        self.addCleanup(self.settings_guard.stop)

    def tearDown(self):
        self.release.set()
        if self.client is not None:self.client.__exit__(None,None,None)

    def start(self,target='coding',controller=None):
        self.settings=gateway.GatewaySettings(TEXT_KEY,CODE_KEY,legacy_key_target=target)
        async def upstream(request):
            with self.call_lock:self.calls.append(request)
            if self.block_legacy and request.headers.get('x-invented-test-block') is None:
                # Only the first POST blocks; health/private Code remain usable.
                if request.method=='POST' and len(self.calls)==1:
                    self.started.set()
                    released=await asyncio.to_thread(self.release.wait,5)
                    if not released:raise AssertionError('Offline test release timed out')
            return self.respond(request)
        self.client=TestClient(gateway.create_app(self.settings,transport=httpx.MockTransport(upstream),
                                                mode_controller=controller))
        self.client.__enter__()
        socket_guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No real sockets'))
        socket_guard.start()
        self.addCleanup(socket_guard.stop)
        return self.client

    def send(self,path='/add',*,key=TEXT_KEY,body=b'{}',headers=None):
        supplied={'Authorization':'Bearer '+key,'Content-Type':'application/json'}
        if headers:supplied.update(headers)
        return self.client.post(path,content=body,headers=supplied)

    def assert_destination(self,response,port,token,track,path):
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.headers['x-memory-track'],track)
        self.assertEqual(str(self.calls[-1].url),f'http://127.0.0.1:{port}{path}')
        self.assertEqual(self.calls[-1].headers['authorization'],'Bearer '+token)

    def test_default_legacy_mode_is_textual(self):
        self.assertEqual(gateway.GatewaySettings(TEXT_KEY,CODE_KEY).legacy_key_target,'textual')
        self.start('textual')
        for path in ('/add','/search'):
            result=self.send(path)
            self.assert_destination(result,8080,TEXT_KEY,'textual',path)

    def test_original_key_targets_code_and_replaces_internal_auth_in_coding_mode(self):
        self.start('coding')
        payload=b'  {not-json; "track":"textual"}\x00\r\n'
        for path in ('/add','/search'):
            result=self.send(path,body=payload)
            self.assert_destination(result,8181,CODE_KEY,'coding',path)
            self.assertEqual(self.calls[-1].content,payload)
            self.assertEqual(result.content,payload)
            self.assertNotIn(TEXT_KEY,self.calls[-1].headers.values())

    def test_old_and_private_keys_can_both_target_code_without_double_writes(self):
        self.start('coding')
        for key in (TEXT_KEY,CODE_KEY):
            result=self.send('/search',key=key)
            self.assert_destination(result,8181,CODE_KEY,'coding','/search')
        self.assertEqual(len(self.calls),2)
        self.assertEqual([request.url.port for request in self.calls],[8181,8181])

    def test_request_content_track_query_and_headers_cannot_change_local_mode(self):
        target=['coding']
        controller=gateway.LegacyModeController(lambda:target[0])
        self.start('textual',controller)
        for mode,port,token in (('coding',8181,CODE_KEY),('textual',8080,TEXT_KEY)):
            target[0]=mode
            opposite='textual' if mode=='coding' else 'coding'
            payload=json.dumps({'track':opposite,'legacy_key_target':opposite,
                                'user_id':'invented-'+opposite,'query':'def choose_mode(): pass',
                                'messages':[{'role':'user','content':'Natural prose '+opposite}]}).encode()
            result=self.send('/search?track='+opposite+'&legacy_key_target='+opposite,body=payload,
                             headers={'X-Memory-Track':opposite,'X-Legacy-Key-Target':opposite,
                                      'X-Forwarded-Host':'invalid.example','Host':'invalid.example'})
            self.assert_destination(result,port,token,mode,'/search')
            self.assertEqual(self.calls[-1].content,payload)
            for header in ('x-memory-track','x-legacy-key-target','x-forwarded-host'):
                self.assertNotIn(header,self.calls[-1].headers)
            self.assertEqual(controller.status()['legacy_key_target'],mode)
            self.assertEqual(controller.status()['legacy_active_requests'],0)

    def test_health_is_anonymous_text_old_key_current_mode_private_key_fixed_code(self):
        target=['coding'];controller=gateway.LegacyModeController(lambda:target[0])
        self.start('textual',controller)
        for key,port,track,token in ((None,8080,'textual',TEXT_KEY),(TEXT_KEY,8181,'coding',CODE_KEY),
                                     (CODE_KEY,8181,'coding',CODE_KEY)):
            headers={'Authorization':'Bearer '+key} if key else {}
            response=self.client.get('/health',headers=headers)
            self.assert_destination(response,port,token,track,'/health')
            self.assertEqual(controller.status()['legacy_active_requests'],0)
        count=len(self.calls)
        response=self.client.get('/__gateway_health')
        status=response.json()
        self.assertEqual(len(self.calls),count)
        for name in ('legacy_key_target','requested_legacy_key_target','legacy_active_requests',
                     'legacy_config_valid','legacy_switch_pending'):
            self.assertIn(name,status)
        self.assertEqual(status['legacy_key_target'],'coding')
        self.assertEqual(status['legacy_active_requests'],0)
        self.assertEqual(status['active_memory_requests'],0)
        self.assertTrue(status['legacy_config_valid'])
        self.assertFalse(status['legacy_switch_pending'])
        self.assertNotIn(TEXT_KEY,response.text)
        self.assertNotIn(CODE_KEY,response.text)

    def test_unknown_key_is_rejected_even_when_legacy_mode_is_code(self):
        self.start('coding')
        for path in ('/add','/search'):
            self.assertEqual(self.send(path,key='invented-unknown-key').status_code,401)
        self.assertEqual(self.calls,[])

    def test_invalid_mode_fails_closed_while_private_code_and_anonymous_health_work(self):
        target=['invented-invalid-mode']
        controller=gateway.LegacyModeController(lambda:target[0])
        self.start('textual',controller)
        for path in ('/add','/search'):
            response=self.send(path)
            self.assertEqual(response.status_code,503)
            self.assertNotIn(TEXT_KEY,response.text)
            self.assertNotIn(CODE_KEY,response.text)
        self.assertEqual(self.client.get('/health',headers={'Authorization':'Bearer '+TEXT_KEY}).status_code,503)
        self.assertEqual(self.calls,[])
        status=controller.status()
        self.assertFalse(status['legacy_config_valid'])
        self.assertEqual(status['legacy_active_requests'],0)
        self.assert_destination(self.send(key=CODE_KEY),8181,CODE_KEY,'coding','/add')
        self.assert_destination(self.client.get('/health'),8080,TEXT_KEY,'textual','/health')
        target[0]='coding'
        self.assert_destination(self.send('/search'),8181,CODE_KEY,'coding','/search')

    def test_mode_reader_error_does_not_expose_detail_or_change_private_code(self):
        def fail_reader():raise OSError('invented private mode-file detail '+TEXT_KEY)
        controller=gateway.LegacyModeController(fail_reader)
        self.start('textual',controller)
        response=self.send('/search')
        self.assertEqual(response.status_code,503)
        self.assertNotIn(TEXT_KEY,response.text)
        self.assertNotIn('private mode-file',response.text)
        self.assertEqual(self.calls,[])
        self.assert_destination(self.send('/search',key=CODE_KEY),8181,CODE_KEY,'coding','/search')

    def test_active_request_stays_on_old_target_pending_rejects_then_new_mode_applies(self):
        target=['textual'];controller=gateway.LegacyModeController(lambda:target[0])
        self.start('textual',controller)
        self.block_legacy=True
        with ThreadPoolExecutor(max_workers=2) as executor:
            first=executor.submit(self.send,'/add',body=b'first-invented-body')
            try:
                self.assertTrue(self.started.wait(3),'Initial request did not reach fake upstream')
                self.assertEqual(controller.status()['legacy_active_requests'],1)
                target[0]='coding'
                rejected=self.send('/search',body=b'second-invented-body')
                self.assertEqual(rejected.status_code,503)
                self.assertEqual(rejected.headers['retry-after'],'1')
                self.assertEqual(len(self.calls),1)
                status=self.client.get('/__gateway_health').json()
                self.assertEqual(status['legacy_key_target'],'textual')
                self.assertEqual(status['requested_legacy_key_target'],'coding')
                self.assertEqual(status['legacy_active_requests'],1)
                self.assertEqual(status['active_memory_requests'],1)
                self.assertTrue(status['legacy_switch_pending'])
                # An explicit private key is independent of the busy legacy mode.
                explicit=self.send('/search',key=CODE_KEY,body=b'private-code-body')
                self.assert_destination(explicit,8181,CODE_KEY,'coding','/search')
                self.assertEqual(controller.status()['legacy_active_requests'],1)
            finally:self.release.set()
            result=first.result(timeout=3)
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.headers['x-memory-track'],'textual')
        self.assertEqual(result.content,b'first-invented-body')
        self.assertEqual(self.calls[0].url.port,8080)
        self.assertEqual(self.calls[0].headers['authorization'],'Bearer '+TEXT_KEY)
        self.assertEqual(controller.status()['legacy_active_requests'],0)
        self.assertEqual(self.client.get('/__gateway_health').json()['active_memory_requests'],0)
        self.assert_destination(self.send('/search'),8181,CODE_KEY,'coding','/search')
        self.assertFalse(controller.status()['legacy_switch_pending'])

    def test_timeout_holds_active_until_error_then_releases_without_fallback(self):
        target=['coding'];controller=gateway.LegacyModeController(lambda:target[0])
        self.start('textual',controller)
        self.block_legacy=True
        def timeout(request):raise httpx.ReadTimeout('invented timeout '+CODE_KEY,request=request)
        self.respond=timeout
        with ThreadPoolExecutor(max_workers=1) as executor:
            task=executor.submit(self.send,'/search')
            try:
                self.assertTrue(self.started.wait(3))
                self.assertEqual(controller.status()['legacy_active_requests'],1)
            finally:self.release.set()
            result=task.result(timeout=3)
        self.assertEqual(result.status_code,504)
        self.assertEqual(controller.status()['legacy_active_requests'],0)
        self.assertEqual(self.client.get('/__gateway_health').json()['active_memory_requests'],0)
        self.assertEqual([request.url.port for request in self.calls],[8181])
        self.assertNotIn(CODE_KEY,result.text)
        self.respond=lambda request:httpx.Response(200,content=b'recovered')
        target[0]='textual'
        self.assert_destination(self.send('/search'),8080,TEXT_KEY,'textual','/search')

    def test_connect_failure_and_unexpected_error_both_release_active_counter(self):
        controller=gateway.LegacyModeController(lambda:'coding')
        self.start('textual',controller)
        def fail_connect(request):raise httpx.ConnectError('invented backend detail',request=request)
        self.respond=fail_connect
        self.assertEqual(self.send('/add').status_code,503)
        self.assertEqual(controller.status()['legacy_active_requests'],0)
        self.assertEqual(self.client.get('/__gateway_health').json()['active_memory_requests'],0)
        self.assertEqual([request.url.port for request in self.calls],[8181])
        def fail_unexpected(request):raise RuntimeError('invented unexpected failure')
        self.respond=fail_unexpected
        with self.assertRaises(RuntimeError):self.send('/search')
        self.assertEqual(controller.status()['legacy_active_requests'],0)
        self.assertEqual([request.url.port for request in self.calls],[8181,8181])


if __name__ == '__main__':
    unittest.main(verbosity=2)
