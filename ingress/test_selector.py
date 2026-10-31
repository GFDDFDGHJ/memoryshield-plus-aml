"""Offline release selection: fake credentials/transports and temporary files."""
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from gateway import GatewaySettings, create_app, load_settings, load_textual_backend


TEXT_KEY='invented-selector-text-token'
CODE_KEY='invented-selector-code-token'


class LocalSelectorTests(unittest.TestCase):
    def test_missing_optional_file_preserves_legacy_default(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(load_textual_backend(Path(directory)/'missing.json'),'legacy_v3140')
        settings=GatewaySettings(TEXT_KEY,CODE_KEY)
        self.assertEqual(settings.selected_text_url,'http://127.0.0.1:8080')
        self.assertEqual(settings.selected_text_version,'v3.14.0')

    def test_only_exact_canonical_named_backends_are_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'selector.json'
            for name,url,version in (
                    ('legacy_v3140','http://127.0.0.1:8080','v3.14.0'),
                    ('gpt_v3160','http://100.93.38.9:8380','v3.16.0'),
                    ('gpt_v3170','http://100.93.38.9:8480','v3.17.0')):
                with self.subTest(name=name):
                    path.write_bytes(json.dumps({'textual_backend':name}).encode())
                    self.assertEqual(load_textual_backend(path),name)
                    settings=GatewaySettings(TEXT_KEY,CODE_KEY,textual_backend=name)
                    self.assertEqual(settings.selected_text_url,url)
                    self.assertEqual(settings.selected_text_version,version)

    def test_invalid_private_config_never_silently_uses_legacy(self):
        values=(b'x'*4097,b'{',b'null',b'[]',b'{}',b'\xff',
                b'{"textual_backend":null}',b'{"textual_backend":[]}',
                b'{"textual_backend":"GPT_V3160"}',b'{"textual_backend":" gpt_v3160 "}',
                b'{"textual_backend":"http://100.93.38.9:8380"}',
                b'{"textual_backend":"gpt_v3160","url":"https://invalid.example"}',
                b'{"textual_backend":"legacy_v3140","textual_backend":"gpt_v3160"}')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'selector.json'
            for value in values:
                with self.subTest(value=value[:70]):
                    path.write_bytes(value)
                    with self.assertRaises(ValueError):load_textual_backend(path)

    def test_unreadable_config_is_not_treated_as_missing(self):
        with patch.object(Path,'open',side_effect=PermissionError('invented private path')):
            with self.assertRaises(PermissionError):load_textual_backend('invented.json')

    def test_settings_load_only_existing_secret_locations_and_local_selector(self):
        reads=[]
        def fake_secret(path,*,encoding):
            reads.append(path)
            self.assertEqual(encoding,'utf-8')
            return TEXT_KEY if path.name=='aml_auth_token.txt' else CODE_KEY
        with patch.object(Path,'read_text',autospec=True,side_effect=fake_secret), \
                patch('gateway.load_textual_backend',return_value='gpt_v3160') as selector:
            settings=load_settings()
        selector.assert_called_once_with()
        root=Path(__file__).resolve().parents[1]
        self.assertEqual(reads,[root/'.secrets/aml_auth_token.txt',root/'coding/.secrets/auth_token.txt'])
        self.assertEqual(settings.text_token,TEXT_KEY)
        self.assertEqual(settings.coding_token,CODE_KEY)
        self.assertEqual(settings.selected_text_url,'http://100.93.38.9:8380')

    def test_settings_never_accept_arbitrary_urls_or_unknown_selectors(self):
        for name in ('http://100.93.38.9:8380','https://invalid.example','coding','GPT_V3160','',None,[]):
            with self.subTest(name=name),self.assertRaises(ValueError):
                GatewaySettings(TEXT_KEY,CODE_KEY,textual_backend=name)
        for kwargs in ({'text_url':'http://100.93.38.9:8380'},
                       {'coding_url':'http://100.93.38.9:8380'},
                       {'text_url':'https://invalid.example'},
                       {'coding_url':'http://127.0.0.1:8080'}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):
                GatewaySettings(TEXT_KEY,CODE_KEY,textual_backend='gpt_v3160',**kwargs)


class GatewaySelectorTests(unittest.TestCase):
    def start(self,*,target='textual',selection='gpt_v3160',respond=None):
        self.calls=[]
        def upstream(request):
            self.calls.append(request)
            return respond(request) if respond else httpx.Response(200,content=request.content)
        settings=GatewaySettings(TEXT_KEY,CODE_KEY,legacy_key_target=target,textual_backend=selection)
        self.client=TestClient(create_app(settings,transport=httpx.MockTransport(upstream)))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__,None,None,None)
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No real sockets'))
        guard.start();self.addCleanup(guard.stop)
        secret_guard=patch('gateway.load_settings',side_effect=AssertionError('No real credentials'))
        secret_guard.start();self.addCleanup(secret_guard.stop)

    def send(self,path='/add',*,key=TEXT_KEY,body=b'{}',headers=None):
        supplied={'Authorization':'Bearer '+key,'Content-Type':'application/json'}
        supplied.update(headers or {})
        return self.client.post(path,content=body,headers=supplied)

    def test_selected_gpt_add_search_use_original_key_and_exact_bytes(self):
        self.start()
        body=b'  {invalid json, "user_id":"invented-user"}\x00\r\n'
        for path in ('/add','/search'):
            with self.subTest(path=path):
                response=self.send(path,body=body)
                request=self.calls[-1]
                self.assertEqual(response.status_code,200)
                self.assertEqual(str(request.url),'http://100.93.38.9:8380'+path)
                self.assertEqual(request.headers['authorization'],'Bearer '+TEXT_KEY)
                self.assertEqual(request.content,body)
                self.assertEqual(response.content,body)
                self.assertEqual(response.headers['x-memory-track'],'textual')

    def test_anonymous_and_authenticated_text_health_use_selected_release(self):
        self.start()
        for headers in ({},{'Authorization':'Bearer '+TEXT_KEY}):
            response=self.client.get('/health',headers=headers)
            self.assertEqual(response.status_code,200)
            self.assertEqual(str(self.calls[-1].url),'http://100.93.38.9:8380/health')
            self.assertEqual(self.calls[-1].headers['authorization'],'Bearer '+TEXT_KEY)

    def test_private_coding_key_never_uses_selected_textual_backend(self):
        self.start()
        for path in ('/add','/search'):
            response=self.send(path,key=CODE_KEY)
            self.assertEqual(response.headers['x-memory-track'],'coding')
            self.assertEqual(str(self.calls[-1].url),'http://127.0.0.1:8181'+path)
            self.assertEqual(self.calls[-1].headers['authorization'],'Bearer '+CODE_KEY)

    def test_legacy_coding_mode_stays_coding_even_with_gpt_text_selected(self):
        self.start(target='coding')
        for key in (TEXT_KEY,CODE_KEY):
            self.assertEqual(self.send('/search',key=key).status_code,200)
            self.assertEqual(str(self.calls[-1].url),'http://127.0.0.1:8181/search')
            self.assertEqual(self.calls[-1].headers['authorization'],'Bearer '+CODE_KEY)
        self.client.get('/health')
        self.assertEqual(str(self.calls[-1].url),'http://100.93.38.9:8380/health')

    def test_payload_query_and_headers_cannot_select_release_or_url(self):
        self.start()
        body=json.dumps({'textual_backend':'legacy_v3140','track':'coding',
                         'url':'https://invalid.example'}).encode()
        response=self.send('/add?textual_backend=legacy_v3140&url=https://invalid.example',body=body,
            headers={'X-Textual-Backend':'legacy_v3140','X-Memory-Track':'coding',
                     'X-Forwarded-Host':'invalid.example','Host':'invalid.example'})
        self.assertEqual(response.status_code,200)
        request=self.calls[-1]
        self.assertEqual(str(request.url),'http://100.93.38.9:8380/add')
        self.assertEqual(request.content,body)
        self.assertEqual(request.headers['host'],'100.93.38.9:8380')
        for header in ('x-textual-backend','x-memory-track','x-forwarded-host'):
            self.assertNotIn(header,request.headers)

    def test_wrong_key_rejected_before_selected_backend(self):
        self.start()
        self.assertEqual(self.send(key='invented-wrong').status_code,401)
        self.assertEqual(self.calls,[])

    def test_safe_internal_health_names_release_without_secret_or_upstream(self):
        self.start()
        response=self.client.get('/__gateway_health')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['version'],'v1.1.3')
        self.assertEqual(response.json()['textual_backend'],'gpt_v3160')
        self.assertEqual(response.json()['textual_version'],'v3.16.0')
        self.assertEqual(response.json()['method_version'],'v3.16.0')
        self.assertEqual(response.json()['legacy_key_target'],'textual')
        self.assertEqual(self.calls,[])
        for value in (TEXT_KEY,CODE_KEY,'100.93.38.9'):
            self.assertNotIn(value,response.text)

    def test_gpt_failure_never_falls_back_to_legacy_or_coding(self):
        def fail(request):raise httpx.ConnectError('invented private '+TEXT_KEY,request=request)
        self.start(respond=fail)
        response=self.send('/search')
        self.assertEqual(response.status_code,503)
        self.assertEqual([str(request.url) for request in self.calls],['http://100.93.38.9:8380/search'])
        self.assertNotIn(TEXT_KEY,response.text)

    def test_v317_routes_exact_bytes_to_only_8480_and_reports_configured_method(self):
        self.start(selection='gpt_v3170')
        body=b'  {invalid; "textual_backend":"gpt_v3160", "track":"coding"}\x00\r\n'
        for path in ('/add','/search'):
            response=self.send(path+'?textual_backend=gpt_v3160',body=body,
                headers={'X-Textual-Backend':'gpt_v3160','X-Memory-Track':'coding'})
            request=self.calls[-1]
            self.assertEqual(response.status_code,200)
            self.assertEqual(str(request.url),'http://100.93.38.9:8480'+path)
            self.assertEqual(request.content,body)
            self.assertEqual(response.content,body)
            self.assertEqual(request.headers['authorization'],'Bearer '+TEXT_KEY)
        for headers in ({},{'Authorization':'Bearer '+TEXT_KEY}):
            self.assertEqual(self.client.get('/health',headers=headers).status_code,200)
            self.assertEqual(str(self.calls[-1].url),'http://100.93.38.9:8480/health')
        before=len(self.calls)
        self.assertEqual(self.send(key='invented-wrong').status_code,401)
        self.assertEqual(len(self.calls),before)
        health=self.client.get('/__gateway_health').json()
        self.assertEqual(health['version'],'v1.1.3')
        self.assertEqual(health['textual_backend'],'gpt_v3170')
        self.assertEqual((health['textual_version'],health['method_version']),('v3.17.0','v3.17.0'))
        self.assertEqual((health['text_add_active'],health['text_add_waiting'],health['active_memory_requests']),(0,0,0))
        self.assertEqual(len(self.calls),before)

    def test_v317_keeps_private_coding_destination_and_credentials(self):
        self.start(selection='gpt_v3170')
        for path in ('/add','/search'):
            response=self.send(path,key=CODE_KEY)
            self.assertEqual(response.status_code,200)
            self.assertEqual(str(self.calls[-1].url),'http://127.0.0.1:8181'+path)
            self.assertEqual(self.calls[-1].headers['authorization'],'Bearer '+CODE_KEY)
            self.assertEqual(response.headers['x-memory-track'],'coding')

    def test_v317_unavailable_is_not_retried_on_v316_or_coding(self):
        def fail(request):
            raise httpx.ConnectError('invented unavailable '+TEXT_KEY,request=request)
        self.start(selection='gpt_v3170',respond=fail)
        response=self.send('/add')
        self.assertEqual(response.status_code,503)
        self.assertEqual([str(request.url) for request in self.calls],['http://100.93.38.9:8480/add'])
        self.assertNotIn(TEXT_KEY,response.text)


if __name__=='__main__':unittest.main(verbosity=2)
