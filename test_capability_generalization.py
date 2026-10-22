"""Independent invented evidence checks, pairable across released source roots.

Run this unchanged file against either source tree:
    python test_capability_generalization.py --source-root ../quality-candidate-rc4
    python test_capability_generalization.py --source-root . --report ../../tmp/capability-generalization-rc5.json

These are retrieval/source regressions, not generated-answer or official-score
tests. Every database is temporary; embeddings are controlled local vectors.
No existing conversations, deployment databases, credentials or network are read.
"""
from __future__ import annotations

import argparse
import builtins
from dataclasses import replace
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

_parser = argparse.ArgumentParser(add_help=False)
_parser.add_argument("--source-root", type=Path, default=Path(os.environ.get(
    "AML_TEST_SOURCE_ROOT", str(Path(__file__).resolve().parent))))
_parser.add_argument("--report", type=Path)
_args, _unittest_args = _parser.parse_known_args() if __name__ == "__main__" else (_parser.parse_args([]), [])
SOURCE_ROOT = _args.source_root.resolve()
sys.path.insert(0, str(SOURCE_ROOT))
os.environ.setdefault("EMBEDDING_API_KEY", "offline-placeholder")
os.environ.setdefault("AML_AUTH_TOKEN", "offline-placeholder")
_original_open = builtins.open


def _offline_open(file, *args, **kwargs):
    if ".secrets" in str(file):
        raise OSError("No credential access in independent tests")
    return _original_open(file, *args, **kwargs)


with patch("builtins.open", _offline_open), \
     patch("socket.create_connection", side_effect=AssertionError("No network")), \
     patch.object(socket.socket, "connect", side_effect=AssertionError("No network")):
    import store

for _name in ("store", "facts", "config"):
    if Path(sys.modules[_name].__file__).resolve().parent != SOURCE_ROOT:
        raise RuntimeError("Wrong source tree imported for " + _name)


class OfflineStore(store.MemoryStore):
    def __init__(self):
        self._dim = 4
        self.vectors = {}
        self.connections = []
        self._ensure_schema()

    def _connect(self):
        conn = super()._connect()
        self.connections.append(conn)
        return conn

    def embed(self, texts):
        return [self.vectors.get(text, [1., 0., 0., 0.]) for text in texts]

    def close(self):
        for conn in self.connections:
            conn.close()


class CapabilityGeneralizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aml-capability-generalization-")
        self.config = patch.object(store, "CONFIG", replace(store.CONFIG,
            db_path=str(Path(self.temp.name) / "isolated.db"), embedding_dim=4))
        self.config.start()
        self.secret_guard = patch("builtins.open", _offline_open)
        self.secret_guard.start()
        self.network_guard = patch.object(socket.socket, "connect", side_effect=AssertionError("No network"))
        self.network_guard.start()
        self.memory = OfflineStore()

    def tearDown(self):
        self.memory.close()
        self.network_guard.stop()
        self.secret_guard.stop()
        self.config.stop()
        self.temp.cleanup()

    def add(self, text, score=.9, *, session="notes", role="user", user="target"):
        self.memory.vectors[text] = [score, math.sqrt(1-score*score), 0., 0.]
        self.memory.add_messages(user, session, [dict(role=role, content=text, timestamp=1748779200000)])

    def noise(self, count=110):
        messages = []
        for index in range(count):
            text = (f"Notebook {index:03d}: " +
                    "The sensor incident note describes seating, filing locations and ordinary room inventory. " * 28)
            self.memory.vectors[text] = [.85, math.sqrt(1-.85**2), 0., 0.]
            messages.append(dict(role="user", content=text, timestamp=1748779200000))
        self.memory.add_messages("target", "incident-context", messages)

    def search(self, query, *, k=100):
        rows = self.memory.search("target", query, k)
        text = "\n\n".join(row["content"] for row in rows)
        self.assertLessEqual(len(rows), k)
        self.assertLessEqual(sum(len(row["content"]) for row in rows), 32000)
        return rows, text

    def standing_response_rules(self):
        rules = ["Always use SI units in every future response.",
                 "Always include uncertainty estimates in every future response."]
        for text in rules:
            self.add(text, .1, session="standing-output")
        return rules

    def assert_visible(self, text, literals, budget=8000):
        for literal in literals:
            self.assertTrue(literal in text[:budget],
                f"Required source literal missing within {budget} characters: {literal!r}")

    def test_unparsed_natural_name_update_survives_known_attribute_scope(self):
        self.add("My name is Rowan.", .70, session="earlier")
        revised = "An update for the record: I legally changed my name to Sable last week."
        self.add(revised, .99, session="later")
        _, text = self.search("What is my name?")
        self.assert_visible(text, [revised])

    def test_french_attribute_update_remains_retrievable(self):
        self.add("My name is Rowan.", .70, session="earlier")
        revised = "Pour mémoire, mon nom légal est désormais Sable."
        self.add(revised, .99, session="later")
        _, text = self.search("What is my current name?")
        self.assert_visible(text, [revised])

    def test_chinese_attribute_update_remains_retrievable(self):
        self.add("我的名字是琥珀。", .70, session="earlier")
        revised = "补充更新：我已依法更名为苍蓝。"
        self.add(revised, .99, session="later")
        _, text = self.search("我的名字是什么？")
        self.assert_visible(text, [revised])

    def test_unparsed_negative_update_is_evidence_without_inventing_replacement(self):
        self.add("I work at Atlas Research.", .70, session="earlier")
        revised = "Record amendment: I am no longer employed by Atlas Research; no new employer has been selected."
        self.add(revised, .99, session="later")
        _, text = self.search("What is my current employer?")
        self.assert_visible(text, [revised, "no new employer has been selected"])

    def test_attribute_excerpt_preserves_adjacent_effective_date_conditions(self):
        self.add("My name is Rowan.", .70, session="earlier")
        proposal = ("My legal name will become Sable. "
                    "However, that change takes effect only after registry approval. "
                    "Approval has not been granted.")
        self.add(proposal, .99, session="pending-change")
        _, text = self.search("What is my current name?")
        self.assert_visible(text, ["My legal name will become Sable.",
            "However, that change takes effect only after registry approval.",
            "Approval has not been granted."])

    def test_hypothesis_is_not_promoted_to_confirmed_user_state(self):
        self.add("I live in Fern City.", .90, session="residence")
        hypothesis = "If I relocated to Harbor City, I would buy a boat; this is a hypothetical plan, not a completed move."
        self.add(hypothesis, .99, session="hypothesis")
        rows, text = self.search("Where do I currently live, and which plan was only hypothetical?")
        self.assert_visible(text, ["Fern City", "hypothetical plan, not a completed move"])
        self.assertFalse(any(row["id"].startswith(("fact_", "change_"))
            and "[CURRENT]" in row["content"] and "Harbor City" in row["content"] for row in rows))

    def test_other_speakers_update_never_becomes_unattributed_user_fact(self):
        self.add("My name is Rowan.", .90, session="identity")
        other = "For the record, my legal name is now Sable."
        self.add(other, .99, session="dialogue", role="assistant")
        rows, text = self.search("What is my name, and what name did the assistant mention?")
        self.assert_visible(text, ["Rowan", other])
        for row in rows:
            if row["id"].startswith(("fact_", "change_")) and "Sable" in row["content"]:
                self.assertTrue(row.get("role") == "assistant" or "role=assistant" in row["content"])

    def test_relational_name_query_keeps_third_person_source(self):
        self.add("My name is Ada.", .2)
        third = "My doctor's name is Dr Rana."
        self.add(third, .99)
        _, text = self.search("What's my doctor's name?")
        self.assert_visible(text, [third])

    def test_related_update_and_minimal_disclosure_work_together(self):
        self.add("My name is Rowan.", .70, session="old")
        update = "For the record, my legal name has become Sable."
        self.add(update, .99, session="new")
        self.add("My home address is 28 Imaginary Cedar Lane.", .10, session="private")
        self.add("My confidential medical code is MED-8847-PRIVATE.", .08, session="private")
        _, text = self.search("What is my name?")
        self.assert_visible(text, [update])
        self.assertNotIn("28 Imaginary Cedar Lane", text)
        self.assertNotIn("MED-8847-PRIVATE", text)

    def test_known_other_attribute_is_not_reintroduced_by_close_embedding(self):
        self.add("My name is Tamsin.", .95, session="identity")
        self.add("My home address is 28 Imaginary Cedar Lane.", .96, session="private")
        _, text = self.search("What is my name?")
        self.assert_visible(text, ["Tamsin"])
        self.assertNotIn("28 Imaginary Cedar Lane", text)

    def test_unparsed_private_attribute_is_not_reintroduced_by_close_embedding(self):
        self.add("My name is Tamsin.", .95, session="identity")
        private = "My confidential medical code is MED-8874-PRIVATE."
        self.add(private, .96, session="private")
        _, text = self.search("What is my name?")
        self.assert_visible(text, ["Tamsin"])
        self.assertNotIn("MED-8874-PRIVATE", text)

    @staticmethod
    def policy_list():
        return [f"Always cite source ref REF-CAP-A{chr(65+index)} in every future response." for index in range(25)]

    def test_twenty_five_applicable_policies_are_not_reduced_to_twelve(self):
        policies = self.policy_list()
        for index, text in enumerate(policies):
            self.add(text, .1, session=f"policy-{index:02d}")
        self.noise()
        _, text = self.search("Draft the sensor incident note.")
        self.assert_visible(text, policies, budget=16000)

    def test_policy_capacity_is_independent_of_message_segmentation(self):
        policies = self.policy_list()
        self.add(" ".join(policies), .1, session="combined-policy")
        self.noise()
        _, text = self.search("Draft the sensor incident note.")
        self.assert_visible(text, policies, budget=16000)

    def _execution_variant(self, query):
        rules = self.standing_response_rules()
        self.noise()
        _, text = self.search(query)
        self.assert_visible(text, rules)

    def test_polite_embedded_execution_request_keeps_standing_policies(self):
        self._execution_variant("Would you prepare a sensor incident note for me?")

    def test_indirect_nominal_execution_request_keeps_standing_policies(self):
        self._execution_variant("I need a sensor incident note.")

    def test_indirect_chinese_request_keeps_standing_policies(self):
        self._execution_variant("这批传感器的情况需要一份说明。")

    def test_spanish_request_keeps_explicit_all_response_policies(self):
        self._execution_variant("Necesito un informe breve sobre este lote de sensores.")

    def test_explicit_all_response_policies_also_accompany_factual_lookup(self):
        rules = self.standing_response_rules()
        self.add("My name is Tamsin.", .99)
        _, text = self.search("What is my name?")
        self.assert_visible(text, ["Tamsin", *rules])

    def test_global_response_output_modifiers_do_not_become_local_domains(self):
        policies = ["Always write every future response in Spanish.",
                    "Always format every future response in a compact table."]
        for policy in policies:
            self.add(policy, .1, session="standing-output")
        self.add("My name is Tamsin.", .99, session="identity")
        self.noise()
        _, text = self.search("What is my name?")
        self.assert_visible(text, ["Tamsin", *policies])

    def test_top_one_attribute_lookup_is_not_displaced_by_output_policy(self):
        self.standing_response_rules()
        self.add("My name is Tamsin.", .99)
        rows, text = self.search("What is my name?", k=1)
        self.assertEqual(len(rows), 1)
        self.assert_visible(text, ["Tamsin"])

    def test_policy_bundle_does_not_copy_unrelated_local_or_private_source_text(self):
        global_rule = "Always use SI units in every future response."
        local_rule = "Always use the blue archive folder for Orchard invoice exports."
        private_fact = "My private home address is 14 Synthesist Lane."
        self.add(" ".join([global_rule, local_rule, private_fact]), .1, session="mixed-source")
        self.noise()
        _, text = self.search("Draft the sensor incident note.")
        self.assert_visible(text, [global_rule])
        self.assertNotIn(local_rule, text[:8000])
        self.assertNotIn("14 Synthesist Lane", text)

    def test_different_local_scope_is_not_promoted_as_the_first_applicable_rule(self):
        local_rule = "If a Helix export is urgent, then use the ORCHID envelope."
        self.add(local_rule, .10, session="helix-policy")
        applicable = "The Selene sensor report concerns a worn inlet bearing."
        self.add(applicable, .99, session="selene-report")
        self.noise()
        rows, text = self.search("Draft the Selene sensor report.")
        self.assertNotIn(local_rule, rows[0]["content"])
        for row in rows:
            if "ORCHID" in row["content"]:
                self.assertIn("Helix", row["content"])
                self.assertNotEqual(row.get("scope"), "global")
        self.assert_visible(text, [applicable])

    def test_all_outputs_inside_explicit_puzzle_scope_is_not_a_global_response_rule(self):
        local = ("For puzzle BRAID, all outputs must list the steps in descending rule order, "
                 "once each; never revisit skipped rules.")
        self.add(local, .1, session="puzzle-policy", role="system")
        self.add("My name is Tamsin.", .99, session="identity")
        self.noise()
        rows, text = self.search("What is my name?")
        self.assert_visible(text, ["Tamsin"])
        self.assertNotIn("descending rule order", text[:8000])
        with self.memory._connect() as conn:
            rules = store.fact_layer.relevant_rules(conn, "target", "What is my name?", 100)
        self.assertFalse(any("BRAID" in evidence["text"] and evidence.get("scope") == "global" for evidence in rules))

    def test_partial_rule_extraction_never_hides_its_conditions_and_exceptions(self):
        source = ("If humidity exceeds 72.5%, then keep fan duty at 0.35 until humidity drops below 68%. "
                  "Exception: during emergency evacuation, stop the fan instead. "
                  "Unless the emergency supervisor approves, do not enable wet mode. "
                  "Otherwise, retain the dry-mode interlock. "
                  "However, a sensor fault also blocks automatic restart.")
        self.add(source, .99, session="operating-policy")
        _, text = self.search("What fan duty and wet-mode constraints apply to this humid test?")
        self.assert_visible(text, ["72.5%", "0.35", "below 68%", "during emergency evacuation",
            "do not enable wet mode", "dry-mode interlock", "sensor fault also blocks automatic restart"])

    def test_address_privacy_policy_preserves_exception_for_explicit_own_address_request(self):
        policy = ("Always redact street addresses in every future response unless I explicitly ask for my own home address.")
        self.add(policy, .1, session="privacy-policy")
        address = "My home address is 28 Imaginary Cedar Lane."
        self.add(address, .99, session="identity")
        _, text = self.search("What is my own home address?")
        self.assert_visible(text, [address, policy])


class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.outcomes = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.outcomes.append({"test": test.id(), "passed": True})

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.outcomes.append({"test": test.id(), "passed": False, "failure": self._exc_info_to_string(err,test)})

    def addError(self, test, err):
        super().addError(test, err)
        self.outcomes.append({"test": test.id(), "passed": False, "error": self._exc_info_to_string(err,test)})


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    runner = unittest.TextTestRunner(verbosity=2, resultclass=RecordingResult)
    program = unittest.main(argv=[sys.argv[0], *_unittest_args], testRunner=runner, exit=False)
    if _args.report:
        report = {"meaning": "Independent synthetic source/evidence regressions; not an official score or answer evaluation",
            "source_root": str(SOURCE_ROOT),
            "evaluator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "source_sha256": {name: hashlib.sha256((SOURCE_ROOT/name).read_bytes()).hexdigest()
                               for name in ("store.py","facts.py","config.py","governance.py")},
            "safety": {"temporary_sqlite_only": True,"provider_requests": 0,"credential_reads": 0},
            "tests": program.result.testsRun,
            "passed": sum(item["passed"] for item in program.result.outcomes),
            "outcomes": program.result.outcomes}
        _args.report.parent.mkdir(parents=True, exist_ok=True)
        _args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2), encoding="utf-8")
    raise SystemExit(0 if program.result.wasSuccessful() else 1)
