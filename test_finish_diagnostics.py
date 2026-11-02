"""Pure invented responses; no config, database, credentials, or API calls."""
import dataclasses
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
import types
import unittest
from unittest.mock import patch

import test_add_failure_observability as obs


SOURCE = "Invented source with a literal fact."
PRIVATE = "PRIVATE-INVENTED-NEVER-LOG"
MESSAGE = "Add model response did not finish completely"


class DoNotRender:
    def __str__(self):
        raise AssertionError("Must not render private values")

    def __repr__(self):
        raise AssertionError("Must not render private values")


class StringSubclass(str):
    def __str__(self):
        raise AssertionError("Must not render subclasses")


class IntSubclass(int):
    pass


def response(reason="length", usage=None):
    return {"model": "gpt-4o-mini-2024-07-18", "choices": [{
        "finish_reason": reason,
        "message": {"content": PRIVATE, "refusal": PRIVATE, "tool_calls": [{"id": PRIVATE}]},
    }], "usage": {"prompt_tokens": 101, "completion_tokens": 23, "total_tokens": 124} if usage is None else usage,
        "id": PRIVATE, "headers": {"authorization": PRIVATE}}


def client_for(result):
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return result
    return types.SimpleNamespace(chat=types.SimpleNamespace(
        completions=types.SimpleNamespace(create=create))), calls


class FinishDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.records = []
        self.enterContext(patch.object(obs.candidate, "_audit", side_effect=self.records.append))
        self.enterContext(patch.object(socket.socket, "connect", side_effect=AssertionError("No sockets")))
        self.enterContext(patch.object(sqlite3, "connect", side_effect=AssertionError("No databases")))
        obs.candidate._ADD_FAILURE_COUNTS.clear()
        obs.candidate.store.error = None

    def extract_error(self, result):
        client, calls = client_for(result)
        extractor = obs.extraction.AddMemoryExtractor(obs.configuration, client=client)
        with self.assertRaises(obs.extraction.AddMemoryError) as caught:
            extractor.extract([{"role": "user", "content": SOURCE}])
        error = caught.exception
        self.assertEqual(error.args, (MESSAGE,))
        self.assertEqual(len(calls), 1)
        self.assertEqual(str(error), MESSAGE)
        return error

    def record(self, error):
        obs.candidate._record_add_failure(error)
        encoded = json.dumps(self.records[-1])
        self.assertNotIn(PRIVATE, encoded)
        self.assertNotIn(SOURCE, encoded)
        self.assertEqual(self.records[-1]["failure_code"], "finish_incomplete")
        self.assertEqual(self.records[-1]["observability_version"], "v3.16.2")
        return self.records[-1]

    def test_four_known_finish_reasons_remain_failures_with_actual_usage(self):
        for reason in ("length", "content_filter", "tool_calls", "function_call"):
            with self.subTest(reason=reason):
                record = self.record(self.extract_error(response(reason)))
                self.assertEqual(record["finish_reason"], reason)
                self.assertEqual({k: record[k] for k in ("prompt_tokens", "completion_tokens", "total_tokens")},
                                 {"prompt_tokens": 101, "completion_tokens": 23, "total_tokens": 124})

    def test_unknown_or_malicious_finish_values_never_render(self):
        reasons = (PRIVATE, None, 123, {"private": PRIVATE}, DoNotRender(), StringSubclass("length"))
        for index, reason in enumerate(reasons):
            with self.subTest(index=index):
                self.assertEqual(self.record(self.extract_error(response(reason)))["finish_reason"], "unknown")

    def test_invalid_usage_values_are_individually_omitted_without_conversion(self):
        values = (True, False, -1, 1_000_001, 2**200, "2048", 3.0, None,
                  float("inf"), float("nan"), DoNotRender(), IntSubclass(10))
        for index, value in enumerate(values):
            with self.subTest(index=index):
                record = self.record(self.extract_error(response(usage={
                    "prompt_tokens": value, "completion_tokens": value, "total_tokens": value})))
                self.assertTrue(all(name not in record for name in ("prompt_tokens", "completion_tokens", "total_tokens")))

    def test_zero_and_bounded_integer_usage_are_preserved_exactly(self):
        for value in (0, 1, 2048, 1_000_000):
            with self.subTest(value=value):
                record = self.record(self.extract_error(response(usage={
                    "prompt_tokens": value, "completion_tokens": value, "total_tokens": value})))
                self.assertEqual([record[k] for k in ("prompt_tokens", "completion_tokens", "total_tokens")], [value] * 3)

    def test_usage_getter_errors_do_not_replace_original_failure(self):
        class PoisonUsage:
            @property
            def prompt_tokens(self):
                raise RuntimeError(PRIVATE)
            completion_tokens = 17
            total_tokens = DoNotRender()
        record = self.record(self.extract_error(response(usage=PoisonUsage())))
        self.assertNotIn("prompt_tokens", record)
        self.assertEqual(record["completion_tokens"], 17)
        self.assertNotIn("total_tokens", record)

    def test_response_usage_getter_error_does_not_change_failure(self):
        raw = response()
        class PoisonResponse:
            model = raw["model"]
            choices = raw["choices"]
            @property
            def usage(self):
                raise RuntimeError(PRIVATE)
        record = self.record(self.extract_error(PoisonResponse()))
        self.assertEqual(record["finish_reason"], "length")
        self.assertNotIn("prompt_tokens", record)

    def test_app_resanitizes_mutated_metadata_and_drops_unknown_fields(self):
        error = self.extract_error(response())
        error.finish_diagnostics = {"finish_reason": PRIVATE, "prompt_tokens": DoNotRender(),
                                    "completion_tokens": 9, "total_tokens": True,
                                    "source": PRIVATE, "exception": DoNotRender()}
        record = self.record(error)
        self.assertEqual(record["finish_reason"], "unknown")
        self.assertEqual(record["completion_tokens"], 9)
        self.assertNotIn("source", record)
        self.assertNotIn("exception", record)
        self.assertNotIn("prompt_tokens", record)
        self.assertNotIn("total_tokens", record)

    def test_missing_or_nonplain_metadata_has_only_unknown_reason(self):
        error = self.extract_error(response())
        class PoisonDict(dict):
            def get(self, *args):
                raise AssertionError("Do not invoke metadata subclass")
        for index, metadata in enumerate((DoNotRender(), PoisonDict(finish_reason="length"), None)):
            with self.subTest(index=index):
                error.finish_diagnostics = metadata
                record = self.record(error)
                self.assertEqual(record["finish_reason"], "unknown")
                self.assertTrue(all(name not in record for name in ("prompt_tokens", "completion_tokens", "total_tokens")))

    def test_same_public_503_and_retry_header_without_diagnostics_in_response(self):
        error = self.extract_error(response())
        obs.candidate.store.error = error
        body = obs.candidate.AddRequest.model_validate({"user_id": "invented-owner", "session_id": "invented-session",
            "request_id": "invented-request", "messages": [{"role": "user", "content": SOURCE}]})
        with self.assertRaises(obs.HTTPException) as caught:
            obs.candidate.add(body, authorization="Bearer invented-auth", x_api_key=None)
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(caught.exception.detail, {"reason": "add_memory_processing_failed"})
        self.assertEqual(caught.exception.headers, {"Retry-After": "5"})
        self.assertNotIn("length", json.dumps(caught.exception.detail))
        self.assertNotIn(PRIVATE, json.dumps(self.records))

    def test_other_failures_cannot_gain_finish_or_usage_fields(self):
        error = obs.extraction.AddMemoryError("Required Add model is busy", finish_diagnostics={
            "finish_reason": "length", "prompt_tokens": 42})
        obs.candidate._record_add_failure(error)
        self.assertEqual(self.records[-1]["failure_code"], "model_busy")
        self.assertTrue(all(k not in self.records[-1] for k in ("finish_reason", "prompt_tokens", "completion_tokens", "total_tokens")))

    def test_success_output_and_every_provider_parameter_match_untouched_source(self):
        configured = os.environ.get("AML_FINISH_BASELINE_SOURCE")
        if not configured:
            self.skipTest("Set AML_FINISH_BASELINE_SOURCE to explicitly compare the pinned old source")
        baseline_path = Path(configured).expanduser()
        self.assertTrue(baseline_path.is_file() and not baseline_path.is_symlink(),
                        "Explicit baseline must be a regular source file")
        current_source = Path(__file__).resolve().with_name("add_memory.py")
        self.assertFalse(baseline_path.samefile(current_source), "Baseline must be independent of current source")
        payload = baseline_path.read_bytes()
        self.assertEqual(hashlib.sha256(payload).hexdigest(),
                         "8fa2881eaf0f574b64a155b3940bea36733060cbf51198c1a13e49d00cc4e0f1",
                         "Baseline must match the pinned pre-diagnostic v3.17.0 source")
        spec = importlib.util.spec_from_file_location("_finish_diagnostics_untouched_baseline", baseline_path)
        baseline = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = baseline
        exec(compile(payload, str(baseline_path), "exec"), baseline.__dict__)
        successful = {"model": "gpt-4o-mini-2024-07-18", "choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps({"selections": [{"message_index": 0, "segment_index": 0,
                "quote": SOURCE, "kind": "fact"}]}), "refusal": None}}],
            "usage": {"prompt_tokens": 101, "completion_tokens": 23}}
        old_client, old_calls = client_for(successful)
        new_client, new_calls = client_for(successful)
        messages = [{"role": "user", "content": SOURCE}]
        old_result = baseline.AddMemoryExtractor(obs.configuration, client=old_client).extract(messages)
        new_result = obs.extraction.AddMemoryExtractor(obs.configuration, client=new_client).extract(messages)
        self.assertEqual(dataclasses.asdict(old_result), dataclasses.asdict(new_result))
        self.assertEqual(old_calls, new_calls)
        self.assertEqual(new_calls[0]["max_completion_tokens"], 2048)
        self.assertEqual(new_calls[0]["timeout"], 60)


if __name__ == "__main__":
    unittest.main(verbosity=2)
