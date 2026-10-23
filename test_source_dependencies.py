"""Invented dependency controls; full Search uses only guarded temporary DBs."""
from __future__ import annotations

import copy
import unittest

import source_dependencies as D


class SourceDependencyTests(unittest.TestCase):
    def test_condition_same_actor_approval_and_denial(self):
        for outcome in ("The licensing panel has granted approval.", "The licensing panel has denied the application."):
            with self.subTest(outcome=outcome):
                self.assertEqual(D.dependency_indexes([
                    "If the licensing panel approves the change, my name is Wren.", outcome], {0}), {1})

    def test_condition_actor_case_is_not_a_different_organization(self):
        self.assertEqual(D.dependency_indexes([
            "If the licensing panel approves the change, my name is Wren.",
            "The Licensing Panel has granted approval."], {0}), {1})

    def test_adjacent_bare_approval_refers_to_explicit_condition(self):
        self.assertEqual(D.dependency_indexes([
            "If the permit office authorizes the change, my name is Wren.",
            "Approval has been denied."], {0}), {1})

    def test_other_approval_actor_is_not_joined(self):
        self.assertEqual(D.dependency_indexes([
            "If the Alder board approves the change, my name is Wren.",
            "The Rowan board has approved the application."], {0}), set())

    def test_same_board_other_person_is_not_the_users_outcome(self):
        self.assertEqual(D.dependency_indexes([
            "If the licensing panel approves the change, my name is Wren.",
            "The licensing panel has approved Ivo's application."], {0}), set())

    def test_same_actor_different_object_is_not_joined(self):
        self.assertEqual(D.dependency_indexes([
            "If the committee approves the licence, the unit may open.",
            "The committee has approved the budget."], {0}), set())

    def test_other_personal_fields_are_not_context(self):
        anchor = "If the panel approves the change, my name is Wren."
        for unrelated in ("The panel has approved my loan of 7200 credits.",
                          "My home address was approved: PRIVATE-ADDRESS-772.",
                          "My medical record was approved: PRIVATE-HEALTH-772.",
                          "The panel has approved my travel reimbursement."):
            with self.subTest(unrelated=unrelated):
                self.assertEqual(D.dependency_indexes([anchor, unrelated], {0}), set())

    def test_nonprefix_explicit_same_scope_revocation(self):
        self.assertEqual(D.dependency_indexes([
            "For Pelican Instruments, Sora holds release authority.",
            "Following the inspection, Pelican Instruments revoked Sora's release authority."], {0}), {1})

    def test_deictic_revocation_after_long_routine_notes(self):
        sentences = ["Pelican Instruments release-authority review for Sora."] + [
            f"Catalogue entry {index} lists ordinary bins and cardboard." for index in range(80)] + [
            "The permission cited above has been rescinded.",
            "Nothing else has been authorized in its place."]
        self.assertEqual(D.dependency_indexes(sentences, {0}), {81, 82})

    def test_deictic_replacement_preserves_new_literal_value(self):
        sentences = ["The Ibis casing material specification is under review.",
                     "The former selection is replaced by QuartzCore resin.",
                     "The prior entry is void."]
        self.assertEqual(D.dependency_indexes(sentences, {0}), {1, 2})
        self.assertEqual(sentences[1], "The former selection is replaced by QuartzCore resin.")

    def test_partial_fee_waiver_keeps_remaining_charges_and_certificate(self):
        self.assertEqual(D.dependency_indexes([
            "Clover dispatch amendment: the revised billing policy is attached.",
            "A one-use exemption removes only the handling surcharge.",
            "The base and per-kg charges remain due.",
            "This dispensation lapses if the signed certificate is missing."], {0}), {1, 2, 3})

    def test_same_product_family_different_model_is_not_joined(self):
        self.assertEqual(D.dependency_indexes([
            "For Pelican Edge, the release authorization remains effective.",
            "For Pelican Pulse, the release authorization is revoked."], {0}), set())

    def test_scope_following_the_verb_still_blocks_other_organization(self):
        self.assertEqual(D.dependency_indexes([
            "For Pelican Instruments, the release authorization remains effective.",
            "The earlier release authorization is revoked for Rowan Instruments."], {0}), set())

    def test_new_topic_blocks_a_later_generic_pointer(self):
        self.assertEqual(D.dependency_indexes([
            "For Pelican Edge, the release authorization remains effective.",
            "Product Rowan Pulse has a separate packing arrangement.",
            "The previous authorization is revoked."], {0}), set())

    def test_bare_pointer_does_not_attach_to_unrelated_weather(self):
        self.assertEqual(D.dependency_indexes([
            "The room was warm and the windows were open.",
            "The previous entry is void."], {0}), set())

    def test_fictional_example_does_not_attach_to_factual_anchor(self):
        for illustrative in ("For example, Pelican Instruments revoked the release authority.",
                             "In a fictional scenario, Pelican Instruments revoked the release authority.",
                             "Hypothetically, the panel has approved the application."):
            with self.subTest(illustrative=illustrative):
                self.assertEqual(D.dependency_indexes([
                    "If the panel approves the change, my name is Wren. Pelican Instruments holds release authority.",
                    illustrative], {0}), set())

    def test_future_and_modal_decisions_do_not_attach_as_outcomes(self):
        for future in ("The panel will approve the application.", "The panel might grant approval.",
                       "The panel could approve the change."):
            with self.subTest(future=future):
                self.assertEqual(D.dependency_indexes([
                    "If the panel approves the change, my name is Wren.", future], {0}), set())

    def test_an_already_anchored_example_remains_original_text(self):
        sentences = ["For example, if the panel approves the change, my name is Wren.",
                     "In this fictional example, the panel has granted approval."]
        original = copy.deepcopy(sentences)
        self.assertEqual(D.dependency_indexes(sentences, {0}), {1})
        self.assertEqual(sentences, original)

    def test_conflicting_explicit_outcomes_are_both_quotes_not_resolved(self):
        sentences = ["If the panel approves the change, my name is Wren.",
                     "The panel has granted approval.", "The panel has denied the application."]
        self.assertEqual(D.dependency_indexes(sentences, {0}), {1, 2})

    def test_no_anchors_does_not_select_governance_on_keywords(self):
        self.assertEqual(D.dependency_indexes(["The grant was revoked.", "The authority was replaced."], set()), set())

    def test_invalid_anchors_are_ignored_and_inputs_not_mutated(self):
        sentences = ["The Ibis material specification is under review.", "The former selection is replaced by resin."]
        anchors = {0, -1, 999, False}
        original_sentences, original_anchors = copy.deepcopy(sentences), set(anchors)
        self.assertEqual(D.dependency_indexes(sentences, anchors), {1})
        self.assertEqual(sentences, original_sentences)
        self.assertEqual(anchors, original_anchors)
        self.assertEqual(D.dependency_indexes(sentences, {0, 1}), set())

    def test_hard_distance_bound_has_a_conservative_fallback(self):
        sentences = ["The Ibis material specification is under review."] + ["Routine cardboard note."] * D.MAX_DISTANCE + [
            "The former selection is replaced by resin."]
        self.assertEqual(D.dependency_indexes(sentences, {0}), set())

    def test_dependency_capacity_including_chains_never_exceeds_bound(self):
        sentences = ["For Pelican Instruments, Sora holds release authority."] + [
            f"Pelican Instruments revoked Sora's release authority after audit {index}." for index in range(40)]
        result = D.dependency_indexes(sentences, {0})
        self.assertEqual(len(result), D.MAX_DEPENDENCIES)
        self.assertTrue(all(type(index) is int and 0 < index < len(sentences) for index in result))

    def test_adjacent_chain_stops_at_private_field(self):
        self.assertEqual(D.dependency_indexes([
            "Clover dispatch amendment: the revised billing policy is attached.",
            "A one-use exemption removes only the handling surcharge.",
            "My bank account was approved: PRIVATE-BANK-772.",
            "This dispensation lapses if the certificate is missing."], {0}), {1})


class PrivacyBoundaryIntegrationTests(unittest.TestCase):
    def test_filtered_qualifier_does_not_jump_original_medical_scope(self):
        from test_rerank_store import S
        content = ("Product Elara material selection is approved. "
                   "My medical record was approved. "
                   "However, that approval has been withdrawn.")
        row = dict(rid=1, user_id="synthetic", session_id="one", role="user", seq=1,
                   content=content, created_at=1735689600, timestamp_origin="message.timestamp")
        query = "Summarize the material selection for Product Elara; omit my medical records."
        units = S.fact_layer.context_units_for_query([row], query)
        self.assertEqual([(unit["segment"], unit["text"]) for unit in units],
                         [(0, "Product Elara material selection is approved.")])

    def test_filtered_preview_keeps_hidden_original_adjacency_barrier(self):
        from test_rerank_store import S
        anchor = "Product Elara material selection is approved."
        private = "My medical record was approved."
        pointer = "However, that approval has been withdrawn."
        routine = "Routine cardboard notes describe bins and folding chairs. "
        original = anchor + " " + private + " " + pointer + " " + routine * 50
        filtered = anchor + " " + pointer + " " + routine * 50
        preview = S._query_preview(filtered, "Elara material selection", budget=700, source_text=original)
        self.assertIn(anchor, preview)
        self.assertNotIn(pointer, preview,
            "A cue adjacent only after filtering must not bind across a hidden original scope.")
        self.assertNotIn(private, preview)

    def test_ambiguous_duplicate_filtered_anchor_cannot_choose_first_source_occurrence(self):
        from test_rerank_store import S
        from unittest.mock import patch
        repeated = "The release authorization remains effective."
        old_outcome = "The permission cited above has been revoked."
        scope = "Product Rowan has a separate authorization arrangement."
        later_outcome = "However, the permission cited above has been withdrawn."
        routine = "Routine cardboard notes describe bins and folding chairs. "
        original = " ".join((repeated, old_outcome, scope, repeated, later_outcome, routine * 50))
        # Only the later identical anchor survived. Text alone cannot prove
        # which occurrence it came from, so no dependency traversal may guess.
        filtered = " ".join((repeated, later_outcome, routine * 50))
        with patch.object(S.dependency_layer, "dependency_indexes", wraps=D.dependency_indexes) as lookup:
            preview = S._query_preview(filtered, "release authorization", budget=700, source_text=original)
        lookup.assert_not_called()
        self.assertIn(repeated, preview)
        self.assertNotIn(later_outcome, preview)
        self.assertNotIn(old_outcome, preview)

    def test_exclusion_keeps_original_scope_barrier_during_full_search(self):
        """Removing a private sentence must not rebind its later pointer."""
        import builtins
        from contextlib import ExitStack
        from dataclasses import replace
        import importlib
        import os
        from pathlib import Path
        import socket
        import sqlite3
        import tempfile
        from unittest.mock import patch

        original_open, original_connect = builtins.open, sqlite3.connect
        with tempfile.TemporaryDirectory(prefix="aml-private-scope-regression-") as directory:
            temporary = Path(directory).resolve()

            def safe_open(file, *args, **kwargs):
                if isinstance(file, (str, bytes, os.PathLike)) and any(
                    part.casefold() == ".secrets" for part in Path(os.fsdecode(file)).parts
                ):
                    raise FileNotFoundError("secret access disabled in synthetic regression")
                return original_open(file, *args, **kwargs)

            def safe_connect(database, *args, **kwargs):
                if str(database) != ":memory:" and not Path(database).resolve().is_relative_to(temporary):
                    raise RuntimeError("database outside synthetic temporary directory")
                return original_connect(database, *args, **kwargs)

            def no_network(*args, **kwargs):
                raise RuntimeError("network disabled in synthetic regression")

            environment = dict(
                AML_DB_PATH=str(temporary / "memory.db"), EMBEDDING_API_KEY="offline-fake",
                AML_AUTH_TOKEN="offline-fake", EMBEDDING_DIM="4", EMBEDDING_MODEL="offline",
                EMBEDDING_MODE="standard", EMBEDDING_BASE_URL="https://offline.invalid/v1",
                AML_GOVERNANCE="false", AML_QUARANTINE="false", AML_RERANK="false",
            )
            with ExitStack() as guards:
                guards.enter_context(patch.dict(os.environ, environment))
                guards.enter_context(patch("builtins.open", safe_open))
                guards.enter_context(patch("sqlite3.connect", safe_connect))
                guards.enter_context(patch("socket.create_connection", no_network))
                guards.enter_context(patch.object(socket.socket, "connect", no_network))
                guards.enter_context(patch.object(socket.socket, "connect_ex", no_network))
                S = importlib.import_module("store")
                guards.enter_context(patch.object(S, "CONFIG", replace(
                    S.CONFIG, db_path=str(temporary / "memory.db"), embedding_dim=4,
                    embedding_api_key="offline-fake", governance_enabled=False, quarantine_enabled=False,
                    rerank_enabled=False,
                )))

                class Offline(S.MemoryStore):
                    def __init__(self):
                        self._dim = 4
                        self.connections = []
                        self._ensure_schema()

                    def _connect(self):
                        connection = super()._connect()
                        self.connections.append(connection)
                        return connection

                    def embed(self, texts):
                        return [[1., 0., 0., 0.] for _ in texts]

                memory = Offline()
                try:
                    anchor = "For Pelican Edge, release authorization remains effective."
                    private = "My bank account was approved: PRIVATE-BANK-772."
                    pointer = "The permission cited above has been rescinded."
                    routine = "Routine cardboard notes describe inventory shelves and folding chairs. "
                    content = anchor + " " + private + " " + routine * 80 + pointer
                    query = "Summarize the Pelican Edge release authorization without financial details."
                    memory.add_messages("synthetic-scope", "one-source", [dict(
                        role="user", content=content, timestamp=1750000000000,
                    )])
                    rows = memory.search("synthetic-scope", query, 100)
                    visible = "\n".join(row["content"] for row in rows)
                    self.assertIn(anchor, visible)
                    self.assertNotIn("PRIVATE-BANK-772", visible)
                    self.assertNotIn(pointer, visible,
                        "A private field remains a scope barrier even when its value is omitted; "
                        "its generic revocation must not become the technical authorization's outcome.")
                finally:
                    for connection in memory.connections:
                        connection.close()


if __name__ == "__main__":
    unittest.main()
