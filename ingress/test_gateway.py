"""Offline gateway contracts: invented credentials, fake upstreams, no sockets."""
from concurrent.futures import ThreadPoolExecutor
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


if __name__ == '__main__':
    unittest.main(verbosity=2)
