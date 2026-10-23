"""New paired data-selection controls; no official data or provider requests.

The favourable-vector data pairs change schema placement, one declared missing
cell, or whether a later batch repeats versus explicitly references its schema.
The final rule pair deliberately crowds an older method below the raw Top 100
using relevant measurements, exercising the advertised identifier-link fallback.
Full return is primary; 8k/16k are separate downstream-prefix diagnostics.
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

EVALUATOR_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(EVALUATOR_ROOT))
from evaluate_extended_context import render
from evaluate_state_transitions import attributed_visible, canonical, decoder_self_check

VERSION = "1.1"
DIM = 4
BASE_MS = 1735689600000
SOURCES = ("store.py", "facts.py", "evidence_links.py", "source_dependencies.py", "query_plan.py", "config.py")
ROW = re.compile(r"(?<![A-Za-z0-9_])(S\d{3})\s*\|\s*(NA|[-+]?\d+(?:\.\d+)?)\s*\|\s*(\d+)\s*\|\s*(dry|wet)(?![A-Za-z])")


def build_cases():
    cases = []

    def turn(text, session, observations=(), score=.97):
        return {"text": text, "session": session, "role": "user", "score": score,
                "observations": list(observations)}

    def observation(index, missing=False):
        pressure = None if missing else 10000 + 125 * index
        item = {"sample": f"S{index:03d}", "pressure_pa": pressure,
                "duration_ms": 600 if index % 7 == 0 else 1000,
                "mode": "wet" if index % 4 == 0 else "dry"}
        value = "NA" if pressure is None else str(pressure)
        return f"{item['sample']} | {value} | {item['duration_ms']} | {item['mode']}", item

    def schema(identifier):
        return (f"Dataset ID {identifier}. Loris observations columns:\n"
                "sample | pressure_Pa | duration_ms | mode\n"
                "Pressure values are pascals, not kilopascals. "
                "Only dry observations with duration_ms at least 800 are usable. "
                "Otherwise, discard the row. Missing pressure is written NA; do not use NA rows.")

    query = "Using all usable Loris observations in dataset DS-LORIS-93, calculate the complete mean pressure in kPa."
    records = [turn(text, "loris-observations", [item]) for text, item in (observation(index) for index in range(223))]
    header = turn(schema("DS-LORIS-93"), "loris-observations", score=.995)
    invalid_scope = [turn("Dataset ID DS-LORIS-94. Schema: private operator code is PRIVATE-OTHER-DATASET-615; pressure unit is MPa.", "other-dataset", score=.999)]

    def data_case(key, messages, *, pair, meaning):
        cases.append({"case": key, "kind": "mean", "query": query, "messages": messages + invalid_scope,
            "required": ["pascals, not kilopascals", "Only dry observations with duration_ms at least 800", "discard the row", "Missing pressure is written NA"],
            "forbidden": ["PRIVATE-OTHER-DATASET-615"], "pair": pair, "meaning": meaning})

    data_case("A01_front_schema", [header] + records, pair="schema-placement",
              meaning="One preceding schema declares a consecutive numeric observation block.")
    data_case("A02_footer_schema", records + [header], pair="schema-placement",
              meaning="Exactly the same records and governing schema; only the schema record arrives last.")
    missing_records = list(records)
    text, item = observation(73, missing=True)
    missing_records[73] = turn(text, "loris-observations", [item])
    data_case("A03_declared_NA_inside_run", [header] + missing_records, pair="declared-missing-cell",
              meaning="One explicitly declared missing pressure is not a new topic or a private field. Later valid rows still belong to the block.")
    body = "\n".join(item["text"] for item in records)
    body_observations = [item["observations"][0] for item in records]
    data_case("A04_one_source_footer_control", [turn(body + "\n" + schema("DS-LORIS-93"), "loris-one-turn", body_observations, .995)],
              pair="schema-placement", meaning="Control: identical table and footer in one original source instead of one row per source.")

    for repeated_schema in (True, False):
        messages = [turn(schema("DS-LORIS-93"), "shared-schema", score=.995)]
        for batch in range(4):
            session = f"loris-batch-{batch}"
            text = schema("DS-LORIS-93") if repeated_schema else (
                f"Dataset ID DS-LORIS-93. Loris observation batch {batch}; use the earlier shared schema and validity convention.")
            messages.append(turn(text, session, score=.99))
            for index in range(batch*56, min(223, (batch+1)*56)):
                literal, item = observation(index)
                messages.append(turn(literal, session, [item]))
        data_case("B01_schema_repeated_each_session" if repeated_schema else "B02_explicit_shared_schema_reference",
                  messages, pair="cross-session-schema-reference",
                  meaning="Every batch explicitly names the same dataset. Repeating the complete schema is a control; a reference should preserve the same data association.")

    method_prefix = ("Dataset ID DS-CELLO-57. The input columns are amount_C and span_ms; their units are C and ms. "
                     "Only the bench configuration is eligible. ")
    method_query = "Given amount_C=8 and span_ms=40, with selector=field, apply dataset DS-CELLO-57: what output is permitted?"
    measurements = [turn(f"Dataset DS-CELLO-57 measurement: amount_C={index+3}; span_ms={40+index}.", "recent-cello-measurements", score=.98) for index in range(127)]
    for key, score, otherwise, expected in (
        ("C01_otherwise_favourable_control", .995, "Otherwise, mark the estimate unavailable.", "unavailable"),
        ("C02_linked_otherwise_unavailable", .35, "Otherwise, mark the estimate unavailable.", "unavailable"),
        ("C03_linked_otherwise_fixed_value", .35, "Otherwise, return the fixed value 17.", 17),
    ):
        cases.append({"case": key, "kind": "rule", "query": method_query,
            "messages": [turn(method_prefix + otherwise, "earlier-cello-method", score=score)] + measurements,
            "required": ["Only the bench configuration is eligible", otherwise], "forbidden": [],
            "pair": "otherwise-branch", "reference_result": expected,
            "meaning": "The query selects field, so the otherwise branch is decisive. The low-rank pair tests complete linked background, not embedding quality."})
    return cases


def fixture_texts(cases):
    return sorted({text for case in cases for text in [case["query"]] + [item["text"] for item in case["messages"]]})


def stats(values):
    if not values:
        return {"count": 0, "mean_kpa": None, "population_variance": None}
    mean = math.fsum(values)/len(values)
    return {"count": len(values), "mean_kpa": mean,
            "population_variance": math.fsum((value-mean)**2 for value in values)/len(values)}


def visible_literals(rows, budget):
    """Read raw/digest literals plus visible original linked-excerpt lines.

    The shared decoder handles raw and compact numeric/literal tables. LINKED
    is another literal source view, not a derived FACT; omitting that layer
    would falsely report equal contexts after an otherwise branch is repaired.
    This decoder never consults source inputs to fill a missing returned line.
    """
    text, witnesses = attributed_visible(rows, budget)
    offset = 0
    for number, row in enumerate(rows, 1):
        block = render([row]).replace("[result 1 ", f"[result {number} ", 1)
        content = str(row.get("content", ""))
        content_start = offset + len(block)-len(content)
        available = min(len(content), max(0, budget-content_start))
        if str(row.get("id", "")).startswith("linked_") and row.get("source_rid") is not None and available:
            body = content[:available]
            if available < len(content):
                body = body.rsplit("\n", 1)[0] if "\n" in body else ""
            first, newline, body = body.partition("\n")
            if newline and first.startswith("[LINKED] [SOURCE "):
                for literal in body.splitlines():
                    if literal.strip():
                        witnesses.append({"rid": int(row["source_rid"]), "role": row.get("role"),
                            "session": row.get("session_id"), "literal": literal.strip()})
        offset += len(block)+2
    return text, witnesses


def linked_decoder_self_check():
    row = {"id": "linked_abc", "source_rid": 7, "role": "assistant", "session_id": "definitions",
           "content": "[LINKED] [SOURCE source_rid=7 role=assistant]\nOnly cold samples qualify.\nOtherwise, return constant 23."}
    text, witnesses = visible_literals([row], 10000)
    assert [item["literal"] for item in witnesses] == ["Only cold samples qualify.", "Otherwise, return constant 23."]
    assert all(item["role"] == "assistant" for item in witnesses)
    assert visible_literals([row], text.index("\nOnly"))[1] == []
    changed = {**row, "content": row["content"].replace("constant 23", "UNAVAILABLE")}
    assert visible_literals([changed], 10000)[1] != witnesses
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
    memory, reports, pair_wording = Offline(), [], {}
    try:
        for case in cases:
            user = "rc9-data:" + case["case"]
            if vectors is None:
                for item in case["messages"]:
                    score = item["score"]
                    memory.vectors[item["text"]] = [score, math.sqrt(1-score*score), 0., 0.]
                memory.vectors[case["query"]] = [1., 0., 0., 0.]
            for _, group in itertools.groupby(enumerate(case["messages"]), key=lambda pair: pair[1]["session"]):
                turns = list(group)
                memory.add_messages(user, turns[0][1]["session"], [{"role": item["role"], "content": item["text"],
                    "timestamp": BASE_MS + index*1000} for index, item in turns])
            with memory._connect() as connection:
                sources = [dict(row) for row in connection.execute("SELECT rid,content,role,session_id FROM items WHERE user_id=? ORDER BY seq,rid", (user,))]
            if len(sources) != len(case["messages"]):
                raise RuntimeError("synthetic observation sources lost")
            by_rid = {source["rid"]: source for source in sources}
            expected = {(source["rid"], observation["sample"]): observation
                        for source, item in zip(sources, case["messages"]) for observation in item["observations"]}
            eligible = {key: item for key, item in expected.items() if item["pressure_pa"] is not None and item["mode"] == "dry" and item["duration_ms"] >= 800}
            expected_stats = stats([item["pressure_pa"]/1000 for item in eligible.values()])
            started = time.perf_counter()
            results = memory.search(user, case["query"], 100)
            elapsed = round((time.perf_counter()-started)*1000, 2)
            rendered = render(results)
            views = []
            full_wording = None
            for name, budget in (("prefix_8000", 8000), ("prefix_16000", 16000), ("full_return", len(rendered))):
                visible, witnesses = visible_literals(results, budget)
                expanded = visible + "\n" + "\n".join(item["literal"] for item in witnesses)
                missing = [literal for literal in case["required"] if literal not in expanded]
                leakage = [literal for literal in case["forbidden"] if literal in expanded]
                decoded, errors, role_errors = {}, [], []
                for witness in witnesses:
                    source = by_rid.get(witness["rid"])
                    if source is None or witness["literal"] not in source["content"]:
                        errors.append({"rid": witness["rid"], "reason": "literal is not in this retained source"})
                        continue
                    if witness.get("role") != source["role"]:
                        role_errors.append(witness["rid"])
                    for match in ROW.finditer(witness["literal"]):
                        key = (witness["rid"], match[1])
                        actual = {"sample": match[1], "pressure_pa": None if match[2] == "NA" else float(match[2]),
                                  "duration_ms": int(match[3]), "mode": match[4]}
                        if key not in expected or actual != expected[key]:
                            errors.append({"rid": key[0], "sample": key[1], "reason": "decoded row differs from original observation"})
                        else:
                            decoded[key] = actual
                valid_decoded = {key: item for key, item in decoded.items() if item["pressure_pa"] is not None and item["mode"] == "dry" and item["duration_ms"] >= 800}
                actual_stats = stats([item["pressure_pa"]/1000 for item in valid_decoded.values()])
                complete = set(valid_decoded) == set(eligible)
                passed = not missing and not leakage and not errors and not role_errors and (case["kind"] != "mean" or complete)
                views.append({"view": name, "passed": passed, "missing_controls": missing,
                    "eligible_observations_expected": len(eligible), "eligible_observations_visible": len(valid_decoded),
                    "all_eligible_observations_visible": complete, "visible_statistics": actual_stats,
                    "decoded_observations_total": len(decoded), "source_literal_failures": errors,
                    "role_failures": role_errors, "privacy_or_scope_leakage": leakage})
                if name == "full_return":
                    full_wording = sorted({item["literal"] for item in witnesses})
            report = {"case": case["case"], "kind": case["kind"], "pair": case["pair"], "query": case["query"],
                "meaning": case["meaning"], "expected_statistics": expected_stats,
                "reference_result": case.get("reference_result"), "input_records": len(sources),
                "result_count": len(results), "returned_chars": sum(len(row["content"]) for row in results),
                "local_search_ms": elapsed, "views": views, "full_return_passed": views[-1]["passed"]}
            if include_context:
                report["returned_context"] = rendered
            reports.append(report)
            if case["kind"] == "rule" and case["case"] != "C01_otherwise_favourable_control":
                pair_wording[case["case"]] = full_wording
    finally:
        for connection in memory.connections:
            connection.close()
    return reports, {"same_complete_return_source_wording": len({canonical(wording) for wording in pair_wording.values()}) == 1,
                     "cases": list(pair_wording), "meaning": "Compare literal source wording only; RID metadata may differ, so complete model inputs are not claimed identical."}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=EVALUATOR_ROOT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--export-fixtures", type=Path)
    parser.add_argument("--vectors-cache", type=Path)
    parser.add_argument("--include-context", action="store_true")
    args = parser.parse_args()
    parser_checks = decoder_self_check()+linked_decoder_self_check()
    cases = build_cases()
    fixture_hash = hashlib.sha256(canonical(cases).encode()).hexdigest()
    if args.export_fixtures:
        args.export_fixtures.write_text(json.dumps({"version": VERSION, "fixture_sha256": fixture_hash,
            "cases": cases, "texts": fixture_texts(cases)}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"cases": len(cases), "unique_texts": len(fixture_texts(cases)), "fixture_sha256": fixture_hash}))
        return 0
    dimension, vectors, cache_metadata = DIM, None, None
    if args.vectors_cache:
        if ".secrets" in (part.casefold() for part in args.vectors_cache.parts):
            parser.error("credential paths are not vector caches")
        document = json.loads(args.vectors_cache.read_text(encoding="utf-8"))
        vectors, dimension = document.get("vectors"), int(document.get("dimensions", 0))
        if not isinstance(vectors, dict) or dimension < 1 or any(text not in vectors for text in fixture_texts(cases)):
            parser.error("complete fixed embedding cache required")
        if any(not isinstance(vector, list) or len(vector) != dimension or not all(type(value) in (int, float) and math.isfinite(value) for value in vector) for vector in vectors.values()):
            parser.error("invalid cached vector")
        cache_metadata = {"sha256": hashlib.sha256(args.vectors_cache.read_bytes()).hexdigest(), "dimensions": dimension, "model": document.get("model")}
    source_root = args.source_root.resolve()
    original_open, original_connect = builtins.open, sqlite3.connect
    def safe_open(file, *a, **kw):
        if isinstance(file, (str, bytes, os.PathLike)) and ".secrets" in (part.casefold() for part in Path(os.fsdecode(file)).parts):
            raise FileNotFoundError("credential reads disabled")
        return original_open(file, *a, **kw)
    def no_network(*a, **kw):
        raise RuntimeError("network disabled in independent synthetic evaluation")
    with tempfile.TemporaryDirectory(prefix="aml-rc9-data-controls-") as directory:
        temporary = Path(directory).resolve()
        def safe_connect(database, *a, **kw):
            if str(database) != ":memory:" and not Path(database).resolve().is_relative_to(temporary):
                raise RuntimeError("only fresh synthetic temporary SQLite is allowed")
            return original_connect(database, *a, **kw)
        environment = {"AML_DB_PATH": str(temporary/"synthetic.db"), "EMBEDDING_DIM": str(dimension),
            "EMBEDDING_API_KEY": "offline-fake", "DASHSCOPE_API_KEY": "offline-fake", "AML_AUTH_TOKEN": "offline-fake",
            "EMBEDDING_MODE": "standard", "EMBEDDING_MODEL": "offline-controlled", "AML_RERANK": "false",
            "AML_GOVERNANCE": "false", "AML_QUARANTINE": "false"}
        with patch.dict(os.environ, environment), patch("builtins.open", safe_open), patch("sqlite3.connect", safe_connect), \
             patch("socket.create_connection", no_network), patch.object(socket.socket, "connect", no_network), patch.object(socket.socket, "connect_ex", no_network):
            sys.path.insert(0, str(source_root))
            module = importlib.import_module("store")
            for name in ("store", "facts", "config"):
                if Path(sys.modules[name].__file__).resolve().parent != source_root:
                    raise RuntimeError("wrong implementation imported")
            reports, rule_pair = evaluate(module, cases, vectors, dimension, args.include_context)
    report = {"evaluator": "rc9-data-controls", "version": VERSION, "fixture_sha256": fixture_hash,
        "decoder_self_checks": parser_checks,
        "evaluator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "source_root": str(source_root),
        "source_sha256": {name: hashlib.sha256((source_root/name).read_bytes()).hexdigest() for name in SOURCES if (source_root/name).exists()},
        "vectors_cache": cache_metadata, "embedding": "fixed cached vectors" if vectors else "controlled 4D vectors; no semantic quality claim",
        "safety": {"temporary_sqlite_only": True, "network": "blocked", "credential_files": "blocked", "provider_calls": 0},
        "meaning": "Necessary numeric/rule inputs and independent descriptive statistics, not generated answers or an official score estimate.",
        "totals": {"cases": len(reports), "full_return_passed": sum(item["full_return_passed"] for item in reports)},
        "rule_pair": rule_pair, "cases": reports}
    if args.output:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"totals": report["totals"], "fixture_sha256": fixture_hash, "rule_pair": rule_pair,
        "cases": [{"case": case["case"], "full_return_passed": case["full_return_passed"], "views": [{"view": view["view"],
            "eligible_visible": view["eligible_observations_visible"], "eligible_expected": view["eligible_observations_expected"],
            "missing_controls": view["missing_controls"], "literal_failures": len(view["source_literal_failures"]),
            "leakage": view["privacy_or_scope_leakage"]} for view in case["views"]]} for case in reports]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
