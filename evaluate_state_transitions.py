"""Invented state-transition evidence checks, fixed before a candidate change.

This checks the necessary *inputs* for resolving replacements, withdrawals,
ratifications and exemptions. It neither generates answers nor treats literal
fidelity as proof that a rule can be executed. No official examples, deployment
databases, credentials or provider services are used. Related sources receive
favourable controlled cosine scores; passes do not measure embedding quality.
"""
from __future__ import annotations

import argparse
import builtins
import hashlib
import importlib
import itertools
import json
import math
import os
from pathlib import Path
import re
import socket
import sqlite3
import sys
import tempfile
import time
from unittest.mock import patch

from evaluate_extended_context import decode_visible, render

VERSION = "1.1"
DIM = 4
BASE_MS = 1735689600000
TOP_K = 100
SOURCE_FILES = ("store.py", "facts.py", "config.py", "query_plan.py", "reranker.py", "evidence_links.py")


def build_cases():
    """Pure, exportable fixtures; required markers bind to individual sources.

    A marker is required only when it distinguishes the requested state or
    calculation. An unrelated product's withdrawal, an assistant's fictional
    future and the amount of an explicitly waived charge are not prerequisites.
    """
    cases = []

    def message(text, session, *, role="user", score=.98):
        return {"text": text, "session": session, "role": role, "score": score}

    def need(source, *markers):
        return [{"source": source, "literal": marker} for marker in markers]

    def noise(count=18):
        return [message(f"Inventory notebook {index:02d}: cabinets contain washers and hinges; the room has grey shelving and ordinary packing boxes.",
                        "unrelated-inventory", score=.24) for index in range(count)]

    def minutes():
        return " ".join(f"Catalogue entry {index:03d} records cardboard sleeves, drawer dividers and routine packing counts."
                        for index in range(95))

    def add(key, query, messages, required, reference, *, decoys=(), pair=None, meaning=""):
        for check in required:
            if check["literal"] not in messages[check["source"]]["text"]:
                raise AssertionError((key, check))
        cases.append({"case": key, "query": query, "messages": messages + noise(),
                      "required": required, "reference_result": reference,
                      "reference_meaning": meaning, "attributed_decoys": list(decoys), "pair": pair})

    old_material = message("The Corvid pump casing specification selects anodized aluminium.", "initial-design", score=.99)
    new_material = message("An engineering notice supersedes the Corvid pump casing specification: the approved selection is glass-filled PPS with immediate effect.", "design-amendment")
    add("T01_explicit_supersession_short", "Which Corvid pump casing material is approved now, and which earlier specification was replaced?",
        [old_material, new_material], need(0, "selects anodized aluminium") + need(1, "supersedes", "approved selection is glass-filled PPS", "immediate effect"),
        {"current_material": "glass-filled PPS", "previous_material": "anodized aluminium"},
        meaning="The query requests both ends; neither source alone identifies the complete change.")

    approval = message("Halcyon Instruments authorized analyst Rhea to release microvalve reports.", "original-authorization", score=.99)
    withdrawal = message("The Halcyon Instruments board rescinded Rhea's microvalve release authorization after an audit failure. No replacement authorization has been issued.", "authorization-withdrawal")
    add("T02_withdrawal_no_replacement_short", "May Rhea release a microvalve report for Halcyon Instruments now, and what changed the permission?",
        [approval, withdrawal], need(0, "authorized analyst Rhea") + need(1, "rescinded Rhea's microvalve release authorization", "No replacement authorization has been issued"),
        {"permission": "withdrawn", "replacement": None})

    proposal = message("The Oriole coating team proposed a 31-minute cure cycle; this was a proposal and had not been approved.", "proposed-cycle", score=.99)
    ratification = message("The Oriole safety committee has ratified the proposed 31-minute cycle, effective 2025-08-14. The approval does not authorize use before that date.", "cycle-ratification")
    add("T03_ratification_effective_date", "Is the proposed Oriole 31-minute cure cycle authorized for a run on 2025-08-13?",
        [proposal, ratification], need(1, "has ratified the proposed 31-minute cycle", "effective 2025-08-14", "does not authorize use before that date"),
        {"authorized": False, "effective_date": "2025-08-14"},
        meaning="The old proposal is context, not a mandatory premise: the later ratification states the cycle and temporal restriction.")

    tariff = message("For Nacre dispatches, base cost is 11 credits plus 2.25 credits per kg. A shipment over 10 kg also has an 8-credit handling charge.", "dispatch-tariff", score=.99)
    waiver = message("For Nacre's documented rescue dispatch only, the handling charge is waived. Base and per-kg charges remain payable. The waiver requires a signed rescue certificate.", "rescue-waiver")
    order = message("The Nacre rescue dispatch weighs 14 kg and carries a signed rescue certificate.", "new-order")
    add("T04_conditional_partial_exemption", "Quote the total for the certified 14 kg Nacre rescue dispatch under the amended pricing arrangement.",
        [tariff, waiver, order], need(0, "base cost is 11 credits", "2.25 credits per kg") + need(1, "handling charge is waived", "Base and per-kg charges remain payable", "requires a signed rescue certificate") + need(2, "weighs 14 kg", "carries a signed rescue certificate"),
        {"total_credits": 11 + 2.25 * 14},
        meaning="The waived surcharge amount and ordinary heavy-shipment threshold are deliberately not mandatory inputs.")

    long_withdrawal = message("Halcyon Instruments release-authority review for analyst Rhea and microvalve reports. " + minutes() +
        " The grant cited above has been rescinded with immediate effect. Nothing else has been authorized in its place.", "long-withdrawal-minutes")
    add("T05_long_nonprefix_withdrawal", "May Rhea issue a microvalve report for Halcyon Instruments today, and which decision governs this?",
        [approval, long_withdrawal], need(0, "authorized analyst Rhea") + need(1, "grant cited above has been rescinded", "Nothing else has been authorized in its place"),
        {"permission": "withdrawn", "replacement": None},
        meaning="The memo heading identifies exactly one approval; its final operative decision cannot be inferred from that heading.")

    long_supersession = message("Corvid pump casing material review. This memo records the decision on the former specification. " + minutes() +
        " The former selection is replaced by graphitic PEEK with immediate effect. The prior entry is void.", "long-supersession-minutes")
    add("T06_long_nonprefix_replacement", "Which casing material should currently be used for the Corvid pump?",
        [old_material, long_supersession], need(1, "former selection is replaced by graphitic PEEK", "prior entry is void"),
        {"current_material": "graphitic PEEK"},
        meaning="The query asks only current material, so the old material source is a distractor rather than a required premise.")

    long_waiver = message("Nacre dispatch amendment. This review concerns the revised billing arrangement for the delivery. " + minutes() +
        " A one-use exemption removes only the handling surcharge. The ordinary base and per-kg charges remain due. This dispensation lapses if its signed certificate is missing.", "long-waiver-minutes")
    add("T07_long_partial_exemption", "Calculate the Nacre dispatch total under the revised arrangement for this certified 14 kg rescue delivery.",
        [tariff, long_waiver, order], need(0, "base cost is 11 credits", "2.25 credits per kg") + need(1, "exemption removes only the handling surcharge", "base and per-kg charges remain due", "lapses if its signed certificate is missing") + need(2, "weighs 14 kg", "carries a signed rescue certificate"),
        {"total_credits": 11 + 2.25 * 14})

    initial_name = message("My name is Mira.", "account-identity", score=.99)
    conditional = "If the registry approves the change, my name is Lark. "
    for suffix, outcome, expected in (("approved", "The registry has granted approval.", "Lark"),
                                      ("declined", "The registry has declined the application.", "Mira")):
        update = message(conditional + outcome, "conditional-account-change")
        add("T08_conditional_name_" + suffix, "What is my name now?", [initial_name, update],
            need(0, "My name is Mira.") + need(1, conditional.strip(), outcome),
            {"current_name": expected}, pair="registry-outcome",
            meaning="The only differing source wording is the registry outcome. Its two outcomes require different current-name conclusions.")

    add("T09_assistant_persona_cannot_replace_user", "What is my name now, and which earlier name did I replace?", [
        message("My name is Mira.", "prior-user-name", score=.99),
        message("My name is Vale. This is a fictional assistant persona for an acting exercise.", "assistant-persona", role="assistant", score=.985),
        message("I have replaced my earlier name Mira with Lark on my account; the change is in force.", "user-name-update")],
        need(0, "My name is Mira.") + need(2, "replaced my earlier name Mira with Lark", "change is in force"),
        {"current_user_name": "Lark"}, decoys=[{"source": 1, "literal": "fictional assistant persona", "role": "assistant"}])

    add("T10_organization_appointment_scope", "May analyst Neri release results for Ember Community Lab now, and what changed that permission?", [
        message("Ember Community Lab authorized Neri to release test results.", "ember-appointment", score=.99),
        message("Neri remains authorized to release results for Dune Community Lab, a separate appointment.", "dune-appointment", score=.985),
        message("The Ember Community Lab board has revoked Neri's release authority because sample seals were broken. This decision is effective immediately.", "ember-revocation")],
        need(0, "Ember Community Lab authorized Neri") + need(2, "Ember Community Lab board has revoked Neri's release authority", "sample seals were broken", "effective immediately"),
        {"ember_permission": "revoked", "dune_permission": "unchanged"},
        decoys=[{"source": 1, "literal": "separate appointment", "role": "user"}])

    add("T11_other_product_withdrawal_is_not_a_prerequisite", "Until which date is Bayleaf Edge supported under its current commitment?", [
        message("Bayleaf Edge support is committed through 2028-03-31.", "edge-product", score=.99),
        message("Bayleaf Pulse support was committed through 2027-11-30.", "pulse-product", score=.98),
        message("The Bayleaf Pulse deadline is withdrawn while the supplier reviews the contract. No new Pulse deadline is confirmed.", "pulse-withdrawal", score=.985)],
        need(0, "Bayleaf Edge support is committed through 2028-03-31"),
        {"edge_support_end": "2028-03-31"},
        meaning="A different product's transition must not be a mandatory premise or override the requested product.")
    return cases


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fixture_texts():
    return sorted({text for case in build_cases() for text in [case["query"]] + [item["text"] for item in case["messages"]]})


def attributed_visible(rows, budget):
    """Decode only returned text; original inputs are never used to fill gaps."""
    rendered = render(rows)
    visible = rendered[:budget]
    witnesses = decode_visible(visible, complete=budget >= len(rendered))
    offset = 0
    for number, row in enumerate(rows, 1):
        block = render([row]).replace("[result 1 ", f"[result {number} ", 1)
        content = str(row.get("content", ""))
        content_start = offset + len(block) - len(content)
        available = min(len(content), max(0, budget - content_start))
        # A source RID and role in the actual API record attribute literal
        # query-focused excerpts too. Derived claims are reported separately.
        if row.get("source_rid") is not None and available and str(row.get("id", "")).startswith("mem_"):
            body = content[:available]
            if available < len(content):
                body = body.rsplit("\n", 1)[0] if "\n" in body else ""
            if body.startswith("[SOURCE "):
                _, newline, body = body.partition("\n")
                if not newline:
                    body = ""  # Prefix contains metadata but no source text.
            excerpt = body.startswith("[QUERY-FOCUSED")
            body = re.sub(r"^\[QUERY-FOCUSED[^\]]*\]\n", "", body)
            # The API explicitly declares excerpts; a newline can separate
            # selected source sentences that were not adjacent in the input.
            # Check their visible literal lines individually, without filling
            # omitted text or treating the joined excerpt as one original span.
            pieces = body.splitlines() if excerpt else [body]
            for literal in pieces:
                if literal == "[… source text omitted …]":
                    continue
                if literal.strip():
                    witnesses.append({"rid": int(row["source_rid"]), "role": row.get("role"),
                                      "session": row.get("session_id"), "literal": literal.strip()})
        offset += len(block) + 2
    return visible, witnesses


def decoder_self_check():
    row = {"id": "mem_7", "source_rid": 7, "role": "user", "session_id": "notes", "seq": 1,
           "content": "[SOURCE source_rid=7 role=user]\nLiteral first sentence."}
    text, witnesses = attributed_visible([row], 10000)
    assert [item["literal"] for item in witnesses] == ["Literal first sentence."]
    header_end = text.index("\nLiteral")
    assert attributed_visible([row], header_end)[1] == []
    assert attributed_visible([row], header_end + 6)[1] == []
    row["content"] = ("[SOURCE source_rid=7 role=user]\n[QUERY-FOCUSED SOURCE EXCERPTS]\n"
                      "Literal first sentence.\nLiteral later sentence.")
    _, witnesses = attributed_visible([row], 10000)
    assert [item["literal"] for item in witnesses] == ["Literal first sentence.", "Literal later sentence."]
    return 4


def evaluate(module, cases, vectors, dimension, include_context):
    class Offline(module.MemoryStore):
        def __init__(self):
            self._dim, self.vectors, self.connections, self._reranker = dimension, dict(vectors or {}), [], None
            self._ensure_schema()
        def _connect(self):
            connection = super()._connect()
            self.connections.append(connection)
            return connection
        def embed(self, texts):
            return [self.vectors[text] for text in texts]

    memory, reports, pair_words = Offline(), [], {}
    try:
        for case in cases:
            user = "state-transition:" + case["case"]
            if vectors is None:
                for item in case["messages"]:
                    score = item["score"]
                    memory.vectors[item["text"]] = [score, math.sqrt(1-score*score), 0., 0.]
                memory.vectors[case["query"]] = [1., 0., 0., 0.]
            indexed = list(enumerate(case["messages"]))
            for session, group in itertools.groupby(indexed, key=lambda pair: pair[1]["session"]):
                chunk = list(group)
                memory.add_messages(user, session, [{"role": item["role"], "content": item["text"],
                    "timestamp": BASE_MS + index*1000} for index, item in chunk])
            with memory._connect() as connection:
                sources = [dict(row) for row in connection.execute("SELECT rid,content,session_id,role,seq FROM items WHERE user_id=? ORDER BY seq,rid", (user,))]
            if len(sources) != len(case["messages"]):
                raise AssertionError("synthetic source records lost")
            by_rid = {source["rid"]: source for source in sources}
            started = time.perf_counter()
            rows = memory.search(user, case["query"], TOP_K)
            elapsed = round((time.perf_counter()-started)*1000, 3)
            rendered = render(rows)
            views = []
            for name, budget in (("prefix_8000", 8000), ("prefix_16000", 16000), ("full_return", len(rendered))):
                text, witnesses = attributed_visible(rows, budget)
                missing, wrong_role, wrong_session, invalid = [], [], [], []
                for check in case["required"]:
                    source = sources[check["source"]]
                    matching = [item for item in witnesses if item["rid"] == source["rid"] and check["literal"] in item["literal"]]
                    if not matching:
                        missing.append(check)
                    elif any(item.get("role") != source["role"] for item in matching):
                        wrong_role.append(check)
                    elif any(item.get("session") is not None and item["session"] != source["session_id"] for item in matching):
                        wrong_session.append(check)
                for witness in witnesses:
                    source = by_rid.get(witness["rid"])
                    if source is None or witness["literal"] not in source["content"]:
                        invalid.append({"rid": witness["rid"], "reason": "returned literal not in that retained source"})
                    elif witness.get("role") != source["role"]:
                        wrong_role.append({"rid": witness["rid"], "expected": source["role"], "actual": witness.get("role")})
                decoys = []
                for check in case["attributed_decoys"]:
                    source = sources[check["source"]]
                    matching = [item for item in witnesses if item["rid"] == source["rid"] and check["literal"] in item["literal"]]
                    if matching:
                        decoys.append({**check, "visible_roles": sorted({item.get("role") or "unknown" for item in matching})})
                views.append({"view": name, "necessary_inputs_complete": not missing,
                    "coverage": {"required": len(case["required"]), "present": len(case["required"])-len(missing)},
                    "missing_inputs": missing, "role_failures": wrong_role, "session_failures": wrong_session,
                    "source_literal_failures": invalid, "attributed_decoys_present": decoys,
                    "passed": not missing and not wrong_role and not wrong_session and not invalid})
            if case["pair"]:
                _, witnesses = attributed_visible(rows, len(rendered))
                pair_words.setdefault(case["pair"], []).append({"case": case["case"],
                    "reference_result": case["reference_result"],
                    "source_wording": sorted({item["literal"] for item in witnesses}),
                    "visible_derived_claims": [row["content"] for row in rows if row["id"].startswith(("fact_", "change_"))]})
            output = {"case": case["case"], "query": case["query"], "input_records": len(sources),
                "reference_result": case["reference_result"], "reference_meaning": case["reference_meaning"],
                "result_count": len(rows), "content_chars": sum(len(row["content"]) for row in rows),
                "local_search_ms": elapsed, "views": views, "full_return_passed": views[-1]["passed"]}
            if include_context:
                output["returned_context"] = rendered
            reports.append(output)
    finally:
        for connection in memory.connections:
            connection.close()
    pairs = [{"pair": name, "cases": [{key: value for key, value in item.items() if key != "source_wording"} for item in items],
              "same_visible_source_wording": len({canonical(item["source_wording"]) for item in items}) == 1,
              "different_reference_results": len({canonical(item["reference_result"]) for item in items}) > 1}
             for name, items in pair_words.items()]
    return reports, pairs


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--export-fixtures", type=Path)
    parser.add_argument("--vectors-cache", type=Path)
    parser.add_argument("--include-context", action="store_true")
    args = parser.parse_args()
    parser_checks = decoder_self_check()
    cases = build_cases()
    fixture_hash = hashlib.sha256(canonical(cases).encode()).hexdigest()
    if args.export_fixtures:
        args.export_fixtures.write_text(json.dumps({"version": VERSION, "fixture_sha256": fixture_hash,
            "cases": cases, "texts": fixture_texts()}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"cases": len(cases), "fixture_sha256": fixture_hash, "unique_texts": len(fixture_texts())}))
        return 0
    dimension, vectors, cache_metadata = DIM, None, None
    if args.vectors_cache:
        if ".secrets" in (part.casefold() for part in args.vectors_cache.parts):
            parser.error("credential paths are not vector caches")
        document = json.loads(args.vectors_cache.read_text(encoding="utf-8"))
        vectors, dimension = document.get("vectors"), int(document.get("dimensions", 0))
        if not isinstance(vectors, dict) or dimension < 1 or any(text not in vectors for text in fixture_texts()):
            parser.error("complete fixed vector cache required")
        if any(not isinstance(vector, list) or len(vector) != dimension or not all(type(number) in (int, float) and math.isfinite(number) for number in vector) for vector in vectors.values()):
            parser.error("invalid cached vector")
        cache_metadata = {"sha256": hashlib.sha256(args.vectors_cache.read_bytes()).hexdigest(), "model": document.get("model"), "dimensions": dimension}
    source_root = args.source_root.resolve()
    original_open, original_connect = builtins.open, sqlite3.connect
    def safe_open(file, *a, **kw):
        if isinstance(file, (str, bytes, os.PathLike)) and ".secrets" in (part.casefold() for part in Path(os.fsdecode(file)).parts):
            raise FileNotFoundError("credential reads disabled in synthetic evaluation")
        return original_open(file, *a, **kw)
    def no_network(*a, **kw):
        raise RuntimeError("network disabled in synthetic evaluation")
    with tempfile.TemporaryDirectory(prefix="aml-state-transitions-") as directory:
        temporary = Path(directory).resolve()
        def safe_connect(database, *a, **kw):
            if str(database) != ":memory:" and not Path(database).resolve().is_relative_to(temporary):
                raise RuntimeError("only fresh synthetic temporary databases allowed")
            return original_connect(database, *a, **kw)
        environment = {"AML_DB_PATH": str(temporary/"synthetic.db"), "EMBEDDING_API_KEY": "offline-fake",
            "DASHSCOPE_API_KEY": "offline-fake", "AML_AUTH_TOKEN": "offline-fake", "EMBEDDING_MODE": "standard",
            "EMBEDDING_DIM": str(dimension), "EMBEDDING_MODEL": "offline-controlled", "AML_RERANK": "false",
            "AML_GOVERNANCE": "false", "AML_QUARANTINE": "false"}
        with patch.dict(os.environ, environment), patch("builtins.open", safe_open), patch("sqlite3.connect", safe_connect), \
             patch("socket.create_connection", no_network), patch.object(socket.socket, "connect", no_network), patch.object(socket.socket, "connect_ex", no_network):
            sys.path.insert(0, str(source_root))
            module = importlib.import_module("store")
            for name in ("store", "facts", "config"):
                if Path(sys.modules[name].__file__).resolve().parent != source_root:
                    raise RuntimeError("wrong source-root module import")
            reports, pairs = evaluate(module, cases, vectors, dimension, args.include_context)
    report = {"evaluator": "state-transitions", "version": VERSION, "fixture_sha256": fixture_hash,
        "decoder_self_checks": parser_checks,
        "evaluator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "source_root": str(source_root),
        "source_sha256": {name: hashlib.sha256((source_root/name).read_bytes()).hexdigest() for name in SOURCE_FILES if (source_root/name).exists()},
        "vectors_cache": cache_metadata, "embedding": "fixed cached vectors" if vectors else "favourable controlled 4D cosine vectors",
        "safety": {"temporary_sqlite_only": True, "network": "blocked", "credential_files": "blocked", "provider_calls": 0},
        "meaning": "Necessary state/rule inputs and literal attribution, not generated answers, inferred state correctness or an official score estimate.",
        "totals": {"cases": len(reports), "full_return_passed": sum(item["full_return_passed"] for item in reports)},
        "pairs": pairs, "cases": reports}
    if args.output:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"totals": report["totals"], "fixture_sha256": fixture_hash, "cases": [{"case": case["case"],
        "full_return_passed": case["full_return_passed"], "views": [{"view": view["view"], "coverage": view["coverage"],
        "missing": view["missing_inputs"], "literal_failures": len(view["source_literal_failures"])} for view in case["views"]]} for case in reports],
        "pairs": pairs}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
