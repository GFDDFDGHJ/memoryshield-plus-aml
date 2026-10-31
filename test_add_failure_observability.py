"""Offline invented requests only; no live config, database, logs, or sockets."""
import ast
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import socket
import sys
import types
import unittest
from unittest.mock import mock_open, patch

import httpx
from fastapi import HTTPException
from openai import (APIConnectionError, APIStatusError, APITimeoutError,
                    AuthenticationError, PermissionDeniedError, RateLimitError)


HERE = Path(__file__).resolve().parent
PRODUCTION_SOURCE = HERE


def load_source(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


extraction = load_source("_offline_observability_extraction", PRODUCTION_SOURCE / "add_memory.py")


class FakeConflict(Exception):
    pass


class FakeStore:
    error = None

    def add_messages(self, user_id, session_id, messages, *, request_id):
        if self.error is not None:
            raise self.error
        return len(messages)


configuration = types.SimpleNamespace(
    version="v3.16.0", auth_token="invented-auth", governance_enabled=False,
    add_llm_api_key="invented-key", add_llm_model="gpt-4o-mini",
    add_llm_base_url="https://api.openai.com/v1", add_llm_timeout=60,
    add_llm_total_timeout=180, add_llm_concurrency=2,
    add_llm_chunk_chars=12000, add_llm_max_output_tokens=2048,
    embedding_model="text-embedding-v4", rerank_enabled=False,
    rerank_model="qwen3-rerank",
)
config_stub = types.ModuleType("config")
config_stub.CONFIG = configuration
store_stub = types.ModuleType("store")
store_stub.MemoryStore = FakeStore
store_stub.AddRequestConflict = FakeConflict
governance_stub = types.ModuleType("governance")
governance_stub.maybe_govern_add = lambda *args: None

with patch.dict(sys.modules, {"config": config_stub, "store": store_stub,
                             "governance": governance_stub, "add_memory": extraction}):
    candidate = load_source("_offline_observability_app", HERE / "app.py")
original_audit = candidate._audit


class ObservabilityTests(unittest.TestCase):
    def setUp(self):
        self.records = []
        self.audit_patch = patch.object(candidate, "_audit", side_effect=self.records.append)
        self.audit_patch.start()
        self.socket_patch = patch.object(socket.socket, "connect", side_effect=AssertionError("No sockets"))
        self.socket_patch.start()
        candidate._ADD_FAILURE_COUNTS.clear()
        candidate.store.error = None

    def tearDown(self):
        self.audit_patch.stop()
        self.socket_patch.stop()

    def send_add(self):
        body = candidate.AddRequest.model_validate({
            "user_id": "invented-owner", "session_id": "invented-session",
            "request_id": "invented-request", "messages": [{"role": "user", "content": "Invented source."}],
        })
        try:
            result = candidate.add(body, authorization="Bearer invented-auth", x_api_key=None)
            return httpx.Response(200, json=result)
        except HTTPException as error:
            return httpx.Response(error.status_code, json={"detail": error.detail}, headers=error.headers)

    def failure_records(self):
        return [record for record in self.records if record.get("event") == "add_failure"]

    def test_allowlist_covers_all_literal_local_error_sites(self):
        messages = set()
        for filename in ("add_memory.py", "store.py"):
            tree = ast.parse((PRODUCTION_SOURCE / filename).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
                        and isinstance(node.exc.func, ast.Name) and node.exc.func.id == "AddMemoryError"):
                    self.assertEqual(len(node.exc.args), 1)
                    argument = node.exc.args[0]
                    self.assertIsInstance(argument, ast.Constant)
                    self.assertIs(type(argument.value), str)
                    messages.add(argument.value)
        self.assertEqual(messages, set(candidate._ADD_FAILURE_CODES))

    def test_each_local_error_keeps_public_error_contract_and_safe_audit(self):
        for message, code in candidate._ADD_FAILURE_CODES.items():
            with self.subTest(code=code):
                candidate.store.error = extraction.AddMemoryError(message)
                response = self.send_add()
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json(), {"detail": {"reason": "add_memory_processing_failed"}})
                self.assertEqual(response.headers["retry-after"], "5")
                record = self.failure_records()[-1]
                self.assertEqual(record["failure_code"], code)
                self.assertEqual(set(record), {"ts", "path", "status", "event", "failure_code", "failure_code_process_total", "observability_version"})
                self.assertEqual(record["observability_version"], "v3.16.1")
                encoded = json.dumps(record)
                self.assertNotIn("invented-owner", encoded)
                self.assertNotIn("invented-session", encoded)
                self.assertNotIn("invented-request", encoded)
                self.assertNotIn("Invented source.", encoded)
                self.assertNotIn(message, encoded)

    def test_provider_classes_only_do_not_serialize_private_attributes(self):
        sentinel = "PRIVATE-INVENTED-ERROR-CONTENT"
        request = httpx.Request("POST", "https://invented.invalid/private")
        response = httpx.Response(429, request=request, text=sentinel)
        examples = [
            (APITimeoutError(request=request), "provider_timeout"),
            (httpx.ReadTimeout(sentinel, request=request), "provider_timeout"),
            (RateLimitError(sentinel, response=response, body={"private": sentinel}), "provider_rate_or_quota_limited"),
            (AuthenticationError(sentinel, response=response, body={"private": sentinel}), "provider_auth_failed"),
            (PermissionDeniedError(sentinel, response=response, body={"private": sentinel}), "provider_forbidden"),
            (APIConnectionError(message=sentinel, request=request), "provider_connection_failed"),
            (APIStatusError(sentinel, response=response, body={"private": sentinel}), "provider_http_failed"),
            (RuntimeError(sentinel), "provider_request_failed"),
        ]
        for upstream, expected in examples:
            with self.subTest(code=expected):
                try:
                    raise upstream
                except Exception:
                    try:
                        raise extraction.AddMemoryError("Required Add model request failed") from None
                    except extraction.AddMemoryError as error:
                        candidate.store.error = error
                response = self.send_add()
                self.assertEqual(response.status_code, 503)
                self.assertEqual(self.failure_records()[-1]["failure_code"], expected)
                self.assertNotIn(sentinel, json.dumps(self.records))
                self.assertNotIn(sentinel, response.text)

    def test_real_extractor_preserves_timeout_class_without_model_or_network(self):
        request = httpx.Request("POST", "https://invented.invalid")
        def fail(**kwargs):
            raise APITimeoutError(request=request)
        client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=fail)))
        extractor = extraction.AddMemoryExtractor(configuration, client=client)
        with self.assertRaises(extraction.AddMemoryError) as captured:
            extractor.extract([{"role": "user", "content": "Invented source."}])
        self.assertTrue(captured.exception.__suppress_context__)
        self.assertEqual(candidate._add_failure_code(captured.exception), "provider_timeout")

    def test_unknown_messages_and_nonstring_arguments_use_one_fixed_code(self):
        class DoNotRender:
            def __str__(self):
                raise AssertionError("Exception values must not be rendered")
        errors = [extraction.AddMemoryError("PRIVATE-INVENTED-UNKNOWN"),
                  extraction.AddMemoryError(DoNotRender()), extraction.AddMemoryError(),
                  extraction.AddMemoryError("Required Add model is busy", "private-extra")]
        for error in errors:
            candidate.store.error = error
            self.assertEqual(self.send_add().status_code, 503)
            self.assertEqual(self.failure_records()[-1]["failure_code"], "add_unknown")
        self.assertNotIn("PRIVATE-INVENTED-UNKNOWN", json.dumps(self.records))

    def test_process_counters_are_thread_safe_and_bounded_by_codes(self):
        error = extraction.AddMemoryError("Required Add model is busy")
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: candidate._record_add_failure(error), range(64)))
        self.assertEqual(dict(candidate._ADD_FAILURE_COUNTS), {"model_busy": 64})
        self.assertEqual(sorted(record["failure_code_process_total"] for record in self.failure_records()), list(range(1, 65)))

    def test_success_and_conflict_keep_existing_contract(self):
        response = self.send_add()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["written"], 1)
        self.assertEqual(self.failure_records(), [])
        candidate.store.error = FakeConflict()
        response = self.send_add()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {"detail": {"reason": "request_id_conflict"}})
        self.assertEqual(self.failure_records(), [])

    def test_method_versions_are_preserved_with_separate_observability_metadata(self):
        result = candidate.health()
        response = httpx.Response(result.status_code, content=result.body, headers=result.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["version"], configuration.version)
        self.assertEqual(candidate.app.version, configuration.version)
        self.assertEqual(response.json()["observability_version"], "v3.16.1")
        self.assertEqual(response.json()["add_model"], "gpt-4o-mini")
        self.assertNotIn("add_failures", response.json())
        fake_open = mock_open()
        with patch("builtins.open", fake_open), patch.object(candidate.os.path, "getsize", return_value=0):
            original_audit({"event": "invented-audit"})
        written = fake_open().write.call_args.args[0]
        self.assertEqual(json.loads(written)["system_version"], configuration.version)


if __name__ == "__main__":
    unittest.main(verbosity=2)
