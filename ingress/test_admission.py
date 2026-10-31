"""Invented operations and fake ASGI upstreams only; no credentials or sockets."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import socket
import sys
import threading
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
from admission import TextAddAdmission, AdmissionUnavailable, AdmissionDisconnected
import gateway


class AdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_twenty_nine_requests_fifo_and_only_two_active(self):
        gate = TextAddAdmission(wait_timeout=2)
        active = maximum = 0
        order = []
        async def operation(index):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            order.append(index)
            await asyncio.sleep(0.002)
            active -= 1
            return index
        result = await asyncio.gather(*(gate.run(lambda i=i: operation(i)) for i in range(29)))
        self.assertEqual(result, list(range(29)))
        self.assertEqual(order, list(range(29)))
        self.assertEqual(maximum, 2)
        self.assertEqual(gate.status()['text_add_active'], 0)
        self.assertEqual(gate.status()['text_add_waiting'], 0)

    async def test_waiter_count_and_timeout_are_bounded(self):
        gate = TextAddAdmission(concurrency=1, max_waiting=1, wait_timeout=0.02, poll_interval=0.005)
        release = asyncio.Event()
        first = asyncio.create_task(gate.run(release.wait))
        await asyncio.sleep(0)
        second = asyncio.create_task(gate.run(release.wait))
        await asyncio.sleep(0)
        with self.assertRaisesRegex(AdmissionUnavailable, 'queue_full'):
            await gate.run(release.wait)
        with self.assertRaisesRegex(AdmissionUnavailable, 'queue_timeout'):
            await second
        self.assertEqual(gate.status()['text_add_waiting'], 0)
        release.set()
        await first
        self.assertEqual(await gate.run(lambda: asyncio.sleep(0, result='ok')), 'ok')

    async def test_cancelled_waiter_removed_without_blocking_next(self):
        gate = TextAddAdmission(concurrency=1)
        release = asyncio.Event()
        first = asyncio.create_task(gate.run(release.wait))
        await asyncio.sleep(0)
        second = asyncio.create_task(gate.run(release.wait))
        await asyncio.sleep(0)
        second.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await second
        self.assertEqual(gate.status()['text_add_waiting'], 0)
        release.set()
        await first
        self.assertEqual(gate.status()['text_add_active'], 0)

    async def test_active_cancel_keeps_slot_until_upstream_really_finishes(self):
        gate = TextAddAdmission(concurrency=1)
        started, release = asyncio.Event(), asyncio.Event()
        async def operation():
            started.set()
            await release.wait()
        first = asyncio.create_task(gate.run(operation))
        await started.wait()
        first.cancel()
        await asyncio.sleep(0)
        self.assertEqual(gate.status()['text_add_active'], 1)
        self.assertFalse(first.done())
        second = asyncio.create_task(gate.run(lambda: asyncio.sleep(0, result='next')))
        await asyncio.sleep(0)
        self.assertEqual(gate.status()['text_add_waiting'], 1)
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await first
        self.assertEqual(await second, 'next')
        self.assertEqual(gate.status()['text_add_active'], 0)

    async def test_cancel_after_grant_releases_slot(self):
        gate = TextAddAdmission(concurrency=1)
        await gate._acquire(None)
        second = asyncio.create_task(gate._acquire(None))
        await asyncio.sleep(0)
        gate._release()  # Grant before allowing the waiting task to resume.
        second.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await second
        self.assertEqual(gate.status()['text_add_active'], 0)
        self.assertEqual(gate.status()['text_add_waiting'], 0)

    async def test_disconnected_waiter_never_starts_paid_operation(self):
        gate = TextAddAdmission(concurrency=1)
        release = asyncio.Event()
        first = asyncio.create_task(gate.run(release.wait))
        await asyncio.sleep(0)
        called = []
        async def disconnected():
            return True
        async def operation():
            called.append(True)
        with self.assertRaises(AdmissionDisconnected):
            await gate.run(operation, disconnected=disconnected)
        self.assertEqual(called, [])
        release.set()
        await first
        self.assertEqual(gate.status()['text_add_waiting'], 0)

    async def test_upstream_failure_releases_slot_and_has_no_automatic_retry(self):
        gate = TextAddAdmission(concurrency=1)
        called = []
        async def operation():
            called.append(True)
            raise httpx.ReadTimeout('invented')
        with self.assertRaises(httpx.ReadTimeout):
            await gate.run(operation)
        self.assertEqual(called, [True])
        self.assertEqual(gate.status()['text_add_active'], 0)


class AdmissionGatewayTests(unittest.TestCase):
    def test_twenty_nine_adds_use_two_upstreams_without_503_and_preserve_bodies(self):
        active = maximum = 0
        observed = []
        async def upstream(request):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            observed.append(request.content)
            try:
                if active > 2:
                    return httpx.Response(503, content=b'invented backend busy')
                await asyncio.sleep(0.005)
                return httpx.Response(200, content=request.content)
            finally:
                active -= 1
        settings = gateway.GatewaySettings('invented-text', 'invented-code', textual_backend='gpt_v3160')
        with patch.object(gateway, 'load_settings', side_effect=AssertionError('No secrets')):
            with TestClient(gateway.create_app(settings, transport=httpx.MockTransport(upstream))) as client:
                with patch.object(socket.socket, 'connect', side_effect=AssertionError('No sockets')):
                    def send(index):
                        body = ('{ "request_id":"invented-'+str(index)+'", "x":1,"x":2 }\r\n').encode()
                        response = client.post('/add', content=body,
                            headers={'Authorization':'Bearer invented-text'})
                        return body, response
                    with ThreadPoolExecutor(max_workers=29) as executor:
                        outcomes = list(executor.map(send, range(29)))
                    for body, response in outcomes:
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.content, body)
                    self.assertEqual(maximum, 2)
                    self.assertEqual(len(observed), 29)
                    self.assertCountEqual(observed, [body for body, _ in outcomes])
                    state = client.get('/__gateway_health').json()
                    self.assertEqual(state['active_memory_requests'], 0)
                    self.assertEqual(state['legacy_active_requests'], 0)
                    self.assertEqual(state['text_add_active'], 0)
                    self.assertEqual(state['text_add_waiting'], 0)

    def test_text_add_queue_never_blocks_search_coding_auth_or_health(self):
        calls = []
        started, release = threading.Event(), threading.Event()
        gate = TextAddAdmission(concurrency=1, max_waiting=1, wait_timeout=0.2)
        async def upstream(request):
            calls.append(request)
            if request.url.port == 8380 and request.url.path == '/add':
                started.set()
                await asyncio.to_thread(release.wait, 2)
            return httpx.Response(200, content=request.content)
        settings = gateway.GatewaySettings('invented-text', 'invented-code', textual_backend='gpt_v3160')
        with patch.object(gateway, 'load_settings', side_effect=AssertionError('No secrets')):
            with TestClient(gateway.create_app(settings, transport=httpx.MockTransport(upstream),
                                              add_admission=gate)) as client:
                with patch.object(socket.socket, 'connect', side_effect=AssertionError('No sockets')):
                    def send(path='/add', key='invented-text', body=b' bytes \x00\r\n'):
                        return client.post(path, content=body, headers={'Authorization': 'Bearer '+key})
                    with ThreadPoolExecutor(max_workers=2) as executor:
                        first = executor.submit(send)
                        try:
                            self.assertTrue(started.wait(1))
                            second = executor.submit(send)
                            self.assertEqual(send('/search').status_code, 200)
                            self.assertEqual(send(key='invented-code').status_code, 200)
                            self.assertEqual(send(key='invalid').status_code, 401)
                            self.assertEqual(client.get('/health').status_code, 200)
                            result = second.result(timeout=1)
                            self.assertEqual(result.status_code, 503)
                            self.assertEqual(result.json()['detail']['reason'], 'text_add_queue_timeout')
                            self.assertEqual(result.headers['retry-after'], '5')
                            self.assertEqual(len([r for r in calls if r.url.port == 8380 and r.url.path == '/add']), 1)
                        finally:
                            release.set()
                        self.assertEqual(first.result(timeout=1).content, b' bytes \x00\r\n')
                    state = client.get('/__gateway_health').json()
                    self.assertEqual(state['version'], 'v1.1.2')
                    self.assertEqual(state['textual_version'], 'v3.16.0')
                    self.assertEqual(state['text_add_active'], 0)
                    self.assertEqual(state['text_add_waiting'], 0)
                    self.assertEqual(state['legacy_active_requests'], 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
