"""Invented long-context and explicit-reference evidence checks, version 1.0.

This never calls an answering model. Full returned evidence and 8k/16k prefixes
are reported separately. Prefix saturation is not a full-context failure.
Numeric templates are independently decoded from visible declared columns;
observations are counted by source RID, so a raw copy and a digest cannot count
twice, while two identical source records remain two observations.

All implementation imports run behind temporary-SQLite, no-network and
no-secret-file guards. No official tasks or deployment histories are read.
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
import random
import re
import socket
import sqlite3
import sys
import tempfile
import time
from unittest.mock import patch

VERSION = "1.0"
SEED = 20261023
DIM = 4
BASE_MS = 1735689600000
SOURCE_FILES = ("config.py", "facts.py", "store.py", "governance.py")


def build_cases(seed: int = SEED) -> list[dict]:
    """Pure invented fixtures, independent of any implementation's outputs."""
    rng = random.Random(seed)
    result = []

    def turn(text, session="notes", role="user", score=.98, value=None):
        return {"text": text, "session": session, "role": role,
                "score": score, "observation_value": value}

    def add(key, query, messages, required, *, forbidden=(), numeric=False,
            roles=(), compare_batch=False, foreign=()):
        result.append({"case": key, "query": query, "messages": messages,
                       "required": list(required), "forbidden": list(forbidden),
                       "numeric_complete_required": numeric, "role_checks": list(roles),
                       "compare_batch": compare_batch, "foreign": list(foreign)})

    words = ("amber birch cobalt daisy elm fennel garnet hazel indigo jasmine "
             "kelp lilac maple nettle ochre pine quartz reed sorrel thyme umber "
             "violet willow yarrow zinnia bracken cedar heather laurel poplar").split()

    def code(index):
        return "".join(chr(65 + (index // divisor) % 26) for divisor in (676, 26, 1))

    def long_note(project, index):
        names = rng.sample(words, 10)
        marker = f"DECISION-{project.upper()}-{code(index)}"
        condition = f"GATE-{project.upper()}-{code(index)}"
        text = (f"For Project {project}, decision {marker} assigns the {names[0]} component to the {names[1]} review team, "
                f"with the {names[2]} checklist and the {names[3]} shipping envelope kept together; "
                f"the {names[4]} workshop must preserve the {names[5]} design note, the {names[6]} acceptance letter, "
                f"the {names[7]} materials log and the {names[8]} routing sheet before release; "
                f"the decision is conditional on written approval identified by {condition} from the {names[9]} coordinator.")
        return text, marker, condition

    # Larger than rc5's complete Search content budget even after metadata
    # sharing. Different literal words prevent accidental numeric templating.
    history, required = [], []
    for project in ("Aster", "Bramble", "Celandine", "Dovetail", "Evergreen", "Foxglove", "Gannet", "Hawthorn"):
        for index in range(24):
            text, marker, condition = long_note(project, index)
            history.append(turn(text, "project-" + project))
            required += [marker, condition]
    add("X01_global_long_history", "Summarize everything I have shared with you across all of my previous conversations.",
        history, required)

    narrow, required = [], []
    for index in range(144):
        text, marker, condition = long_note("Helio", index)
        narrow.append(turn(text, "helio-" + code(index % 9)))
        required += [marker, condition]
    private = ["UNRELATED-HOME-SENTINEL", "UNRELATED-CARE-SENTINEL", "UNRELATED-ACCESS-SENTINEL"]
    narrow += [turn("My home address is " + private[0] + " Lane.", "personal", score=.15),
               turn("My confidential medical code is " + private[1] + ".", "personal", score=.16),
               turn("The unrelated archive access token is " + private[2] + ".", "private-vault", score=.17)]
    add("X02_narrow_long_history_privacy", "Summarize every decision and approval condition for Project Helio across its sessions.",
        narrow, required, forbidden=private)

    def observations(dataset, count, sessions):
        rows = []
        previous = None
        for index in range(count):
            if index and index % 37 == 0:
                # A genuine repeated record, not a network retry. Text and
                # value repeat while the stored source RID remains distinct.
                text, value = previous
            else:
                value = round(18.125 + (index % 41) * .375 + (17.25 if index % 83 == 0 else 0), 3)
                text = (f"Dataset {dataset}: reading={value:.3f}; load={1.25 + index % 19 * .5:.2f}; "
                        f"duration={2.5 + index % 23 * .25:.2f}; ambient={19.0 + index % 11 * .2:.2f}; "
                        f"gain={.65 + index % 7 * .03:.2f}; offset={-.125 + index % 13 * .025:.3f}; "
                        f"humidity={35.5 + index % 17 * .4:.1f}; mode=dry.")
                previous = text, value
            rows.append(turn(text, "observations-" + code(index % sessions), value=value))
        return rows

    dataset = "ds-LARCH-VA"
    schema = (f"Dataset ID: {dataset}. The reading column is raw force in newtons, load is mass in kilograms, "
              "and duration is elapsed seconds. Keep every independently recorded dry observation, including repeated equal values.")
    add("X03_large_numeric_history", f"Describe the empirical distribution of reading across every observation for dataset {dataset}, including repeated values and outliers.",
        [turn(schema, "observations-AAA")] + observations(dataset, 713, 1),
        ["raw force in newtons", "including repeated equal values"], numeric=True)

    dataset = "ds-MALLOW-JF"
    schema = (f"Dataset ID: {dataset}. Field reading is uncorrected gauge pressure in kPa; load is applied mass in kg; "
              "duration is elapsed seconds. The calibration correction subtracts 1.375 kPa from reading. Only dry-mode rows are admissible.")
    add("X04_shared_dataset_across_sessions", f"Use all observations linked to dataset {dataset}; retain its earlier column meanings, calibration and eligibility conditions before describing the raw reading distribution.",
        [turn(schema, "dataset-registry")] + observations(dataset, 317, 11),
        ["uncorrected gauge pressure in kPa", "applied mass in kg", "elapsed seconds", "subtracts 1.375 kPa", "Only dry-mode rows are admissible"], numeric=True,
        forbidden=["OTHER-DATASET-PRIVATE"], foreign=[turn(f"Dataset ID: {dataset}. Private calibration is OTHER-DATASET-PRIVATE.", "foreign", score=.999)])

    def distractions(count):
        return [turn(f"Unrelated operational note {code(index)} records the location of ordinary spare shelves and empty storage boxes.",
                     "background-" + code(index % 5), score=.55) for index in range(count)]

    reference = "RC-SORREL-QV"
    old = (f"Reference card ID {reference}. The first coordinate is uncorrected pressure in kPa, and the second coordinate is elapsed milliseconds. "
           "Subtract 2.125 kPa from the first coordinate before reporting it. Divide the second coordinate by one thousand to express seconds. "
           "Reject a run labelled damp; only dry runs may proceed.")
    latest = f"New run explicitly adopts reference card {reference}: coordinates=(23.875, 640); label=dry."
    add("X05_explicit_card_migration", f"For the new run adopting reference {reference}, carry over the earlier interpretation and admission conditions.",
        [turn(old, "older-card", score=.70)] + distractions(120) + [turn(latest, "new-run", score=.995)],
        ["uncorrected pressure in kPa", "elapsed milliseconds", "Subtract 2.125 kPa", "one thousand to express seconds", "Reject a run labelled damp", latest],
        forbidden=["WRONG-CARD-CODE"], foreign=[turn(f"Reference card ID {reference}. Use WRONG-CARD-CODE.", "other-user", score=.999)])

    protocol = "PR-WREN-TK"
    old = (f"Protocol ID {protocol}. The input pair carries charge in mC and duration in ms. "
           "Convert duration to seconds before applying the scale factor 0.65. Exclude trials carrying the drift label.")
    latest = f"New case under protocol {protocol}: input=(18.75, 42.50); label=drift."
    unrelated = "Protocol ID PR-WREN-TL. The unrelated private override is UNRELATED-PROTOCOL-SECRET."
    add("X06_assistant_protocol_migration", f"For the latest case tied to protocol {protocol}, recall its old unit convention and decide which admission evidence applies.",
        [turn(old, "older-protocol", role="assistant", score=.72)] + distractions(112)
        + [turn(unrelated, "private-protocol", score=.12), turn(latest, "new-case", score=.995)],
        ["charge in mC and duration in ms", "Convert duration to seconds", "scale factor 0.65", "Exclude trials carrying the drift label", latest],
        forbidden=["UNRELATED-PROTOCOL-SECRET"], roles=[("Convert duration to seconds", "assistant")])

    dataset = "ds-YARROW-CH"
    schema = f"Dataset ID: {dataset}. The reading column is optical power in milliwatts; load is a dimensionless setting."
    withdrawal = (f"Dataset {dataset}: the previous readings are withdrawn due to detector saturation; "
                  "do not use any of those readings for fitting or calibration.")
    latest = f"A new analysis explicitly refers back to dataset {dataset} and asks to reuse its earlier observations."
    add("X07_linked_dataset_withdrawal", f"May the new analysis reuse the earlier observations for dataset {dataset}? Recover the controlling older and later evidence.",
        [turn(schema, "registry")] + observations(dataset, 89, 7)
        + [turn(withdrawal, "validity-update"), turn(latest, "new-analysis")],
        ["optical power in milliwatts", "previous readings are withdrawn", "detector saturation", "do not use any of those readings", latest],
        forbidden=["OTHER-DATASET-VALIDITY-SECRET"],
        foreign=[turn(f"Dataset {dataset}: private validity code OTHER-DATASET-VALIDITY-SECRET.", "foreign", score=.999)])

    dataset = "ds-ZINNIA-NB"
    schema = (f"Dataset ID: {dataset}. Field reading is raw torque in newton metres. "
              "The normal operating condition requires dry mode. Never infer additional rows from identical values.")
    continuation = f"The new session continues dataset {dataset}; carry its previous units and operating condition into this analysis."
    add("X08_stream_batch_explicit_dataset", f"Summarize the entire continued dataset {dataset} with every recorded reading and its earlier interpretation.",
        [turn(schema, "registry")] + observations(dataset, 173, 5) + [turn(continuation, "continuation")],
        ["raw torque in newton metres", "normal operating condition requires dry mode", "Never infer additional rows", continuation],
        numeric=True, compare_batch=True)
    return result


def fixture_texts(seed=SEED):
    return sorted({text for case in build_cases(seed) for text in
                   [case["query"]] + [item["text"] for item in case["messages"] + case["foreign"]]})


def render(rows):
    return "\n\n".join(f"[result {index} " + json.dumps({key: row[key] for key in ("role", "session_id", "seq") if key in row}, sort_keys=True)
                         + "]\n" + str(row.get("content", "")) for index, row in enumerate(rows, 1))


def _role_value(value):
    value = value.removeprefix("role=").strip()
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return value


def decode_visible(text: str, *, complete: bool) -> list[dict]:
    """Independent table decoder: use only visible literals and column names."""
    if not complete and text and not text.endswith(("\n", "\r")):
        text = text.rsplit("\n", 1)[0] if "\n" in text else ""
    decoded, template, columns, shared_role = [], None, None, None
    for line in text.splitlines():
        if line.startswith(("[SOURCE", "[DIGEST", "[result")):
            template, columns = None, None
            if line.startswith("[SOURCE"):
                match = re.search(r'\brole=("[^"\\]*(?:\\.[^"\\]*)*"|[^\s;\]]+)', line)
                shared_role = _role_value(match[1]) if match else None
        elif line.startswith("Literal source template: "):
            template, columns = line[len("Literal source template: "):], None
        elif "Columns: source_rid@seq/segment" in line:
            columns = line.split("Columns: ", 1)[1].split(" | ")[1:]
        match = re.fullmatch(r"(\d+)@(\d+)/(\d+)(?:: (.*)|(?: \| (.*))?)", line)
        if not match:
            continue
        rid, seq, segment = map(int, match.group(1, 2, 3))
        role = shared_role
        if match[4] is not None:
            literal = match[4]
        elif columns is not None:
            is_literal = bool(columns) and columns[-1].strip().casefold() == "literal source text"
            body = match[5]
            values = [] if body is None else body.split(" | ", len(columns) - 1) if is_literal else body.split(" | ")
            if len(values) != len(columns):
                continue
            values_by_column = dict(zip(columns, values))
            if "role" in values_by_column:
                role = _role_value(values_by_column["role"])
            if is_literal:
                literal = values[-1]
            elif template is not None:
                literal = template
                for column, value in values_by_column.items():
                    literal = literal.replace("{" + column + "}", value)
            else:
                continue
        else:
            continue
        decoded.append({"rid": rid, "seq": seq, "segment": segment, "role": role, "literal": literal})
    return decoded


def visible_sources(rows, rendered, budget):
    complete = budget >= len(rendered)
    text = rendered[:budget]
    witnesses = decode_visible(text, complete=complete)
    offset = 0
    for index, row in enumerate(rows, 1):
        block = render([row]).replace("[result 1 ", f"[result {index} ", 1)
        content = str(row.get("content", ""))
        content_start = offset + len(block) - len(content)
        available = min(len(content), max(0, budget - content_start))
        if str(row.get("id", "")).startswith("mem_") and row.get("source_rid") is not None and available:
            visible = content[:available]
            if available < len(content) and not visible.endswith("\n"):
                visible = visible.rsplit("\n", 1)[0] if "\n" in visible else ""
            first, newline, body = visible.partition("\n")
            if newline and first.startswith("[SOURCE ") and not body.startswith("[QUERY-FOCUSED"):
                witnesses.append({"rid": int(row["source_rid"]), "seq": row.get("seq"),
                                  "segment": None, "role": row.get("role"), "literal": body})
        offset += len(block) + 2
    return text, witnesses


def statistics(values):
    if not values:
        return {"count": 0, "mean": None, "population_variance": None}
    average = math.fsum(values) / len(values)
    variance = math.fsum((value - average) ** 2 for value in values) / len(values)
    return {"count": len(values), "mean": average, "population_variance": variance}


def decoder_self_check():
    examples = [
        ("[SOURCE role=user]\nLiteral source template: reading={slot1}; literal={n1}.\nRows. Columns: source_rid@seq/segment | slot1 | recorded_at_offset_s\n3@1/0 | -1.250 | 0", True, [(3, "reading=-1.250; literal={n1}.")]),
        ("[SOURCE role=user]\nColumns: source_rid@seq/segment | session | literal source text\n4@2/0 | session=lab | a=1 | b=2.", True, [(4, "a=1 | b=2.")]),
        ("[SOURCE role=assistant]\nLiteral source template: reading=2.500.\nRows. Columns: source_rid@seq/segment\n5@3/0", True, [(5, "reading=2.500.")]),
        ("[SOURCE role=user]\nColumns: source_rid@seq/segment | literal source text\n6@4/0 | reading=1.25", False, []),
        ("[SOURCE]\nColumns: source_rid@seq/segment | role | literal source text\n7@5/0 | role=\"assistant\" | reading=8.25.\n8@6/0 | role=\"user\" | reading=", False, [(7, "reading=8.25.")]),
    ]
    for text, complete, expected in examples:
        actual = [(item["rid"], item["literal"]) for item in decode_visible(text, complete=complete)]
        if actual != expected:
            raise AssertionError((actual, expected))
    repeated = ("[SOURCE role=user]\nLiteral source template: reading={v1}.\nRows. Columns: source_rid@seq/segment | v1\n"
                "10@1/0 | 1.250\n10@1/0 | 1.250\n11@2/0 | 1.250")
    decoded = decode_visible(repeated, complete=True)
    unique = {item["rid"]: float(re.search(r"reading=([-+\d.]+)\.", item["literal"])[1]) for item in decoded}
    if statistics(list(unique.values())) != {"count": 2, "mean": 1.25, "population_variance": 0.0}:
        raise AssertionError("source RID deduplication must retain genuine equal-valued observations")
    return len(examples) + 1


def evaluate(module, source_root, cases, cached_vectors, dimension, include_context):
    class Offline(module.MemoryStore):
        def __init__(self):
            self._dim, self.connections = dimension, []
            self.vectors = dict(cached_vectors or {})
            self._ensure_schema()

        def _connect(self):
            connection = super()._connect()
            self.connections.append(connection)
            return connection

        def embed(self, texts):
            return [self.vectors[text] for text in texts]

    memory, reports = Offline(), []

    def add(user, turns, online=False):
        if not cached_vectors:
            for item in turns:
                score = item["score"]
                memory.vectors[item["text"]] = [score, math.sqrt(1 - score * score), 0., 0.]
        indexed = list(enumerate(turns))
        chunks = [[item] for item in indexed] if online else [list(group) for _, group in itertools.groupby(indexed, key=lambda pair: pair[1]["session"])]
        for chunk in chunks:
            memory.add_messages(user, chunk[0][1]["session"], [{"role": item["role"], "content": item["text"],
                "timestamp": BASE_MS + index * 1000} for index, item in chunk], request_id="synthetic-" + str(chunk[0][0]))
        with memory._connect() as connection:
            sources = [dict(row) for row in connection.execute("SELECT * FROM items WHERE user_id=? ORDER BY seq,rid", (user,))]
        if len(sources) != len(turns):
            raise RuntimeError("genuine source observations were lost at ingestion")
        return {source["rid"]: {**source, "expected_value": item["observation_value"]}
                for source, item in zip(sources, turns)}

    def retrieve(user, case, sources):
        if not cached_vectors:
            memory.vectors[case["query"]] = [1., 0., 0., 0.]
        started = time.perf_counter()
        rows = memory.search(user, case["query"], 100)
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        rendered = render(rows)
        expected_values = {rid: source["expected_value"] for rid, source in sources.items() if source["expected_value"] is not None}
        expected_statistics = statistics(list(expected_values.values()))
        views = []
        for label, budget in (("prefix_8000", 8000), ("prefix_16000", 16000), ("full_return", len(rendered))):
            text, witnesses = visible_sources(rows, rendered, budget)
            expanded = text + "\n" + "\n".join(item["literal"] for item in witnesses)
            missing = [marker for marker in case["required"] if marker not in expanded]
            forbidden = [marker for marker in case["forbidden"] if marker in expanded]
            invalid, role_failures, observed = [], [], {}
            valid_sources = set()
            for witness in witnesses:
                source = sources.get(witness["rid"])
                if source is None or witness["literal"] not in source["content"]:
                    invalid.append({"rid": witness["rid"], "reason": "unknown source or literal not present in that source"})
                    continue
                valid_sources.add(witness["rid"])
                if witness["role"] is not None and witness["role"] != source["role"]:
                    role_failures.append({"rid": witness["rid"], "expected": source["role"], "actual": witness["role"]})
                if witness["rid"] in expected_values and witness["literal"] == source["content"]:
                    match = re.search(r"\breading=([-+]?(?:\d+(?:\.\d*)?|\.\d+))\b", witness["literal"])
                    if match:
                        value = float(match[1])
                        if value != expected_values[witness["rid"]]:
                            invalid.append({"rid": witness["rid"], "reason": "decoded reading differs from original numeric literal"})
                        else:
                            observed[witness["rid"]] = value
            for marker, role in case["role_checks"]:
                matching = [item for item in witnesses if marker in item["literal"]]
                if not matching or any(item["role"] != role for item in matching):
                    role_failures.append({"marker": marker, "expected": role, "reason": "missing or wrong visible role attribution"})
            actual_statistics = statistics(list(observed.values()))
            all_observations = set(observed) == set(expected_values)
            stats_match = (all_observations and all(actual_statistics[key] is None and expected_statistics[key] is None
                or actual_statistics[key] is not None and expected_statistics[key] is not None
                and math.isclose(actual_statistics[key], expected_statistics[key], rel_tol=1e-12, abs_tol=1e-10)
                for key in ("mean", "population_variance")))
            passed = not missing and not forbidden and not invalid and not role_failures and (not case["numeric_complete_required"] or stats_match)
            views.append({"view": label, "visible_chars": min(len(rendered), budget), "passed": passed,
                "coverage": {"required": len(case["required"]), "present": len(case["required"]) - len(missing), "missing": missing},
                "privacy_or_scope_leakage": forbidden, "source_literal_failures": invalid,
                "role_failures": role_failures, "visible_unique_source_rids": len(valid_sources),
                "duplicate_source_mentions": len(witnesses) - len({item["rid"] for item in witnesses}),
                "observations": {"expected": expected_statistics, "visible": actual_statistics,
                    "all_source_rids_present": all_observations, "statistics_match": stats_match,
                    "missing_rids": sorted(set(expected_values) - set(observed))},
                "prefix_sha256": hashlib.sha256(text.encode()).hexdigest()})
        output = {"result_count": len(rows), "content_chars": sum(len(row["content"]) for row in rows),
                  "rendered_chars": len(rendered), "local_search_ms": elapsed, "views": views,
                  "full_return_passed": views[-1]["passed"] and len(rows) <= 100}
        if include_context:
            output["returned_context"] = rendered
        return output

    try:
        for case in cases:
            user = "extended:" + case["case"]
            sources = add(user, case["messages"], online=case["compare_batch"])
            if case["foreign"]:
                add(user + ":foreign", case["foreign"])
            result = retrieve(user, case, sources)
            if case["compare_batch"]:
                batch_sources = add(user + ":batch", case["messages"])
                batch = retrieve(user + ":batch", case, batch_sources)
                result["batch_comparison"] = {"full_return_passed": batch["full_return_passed"],
                    "views": batch["views"], "meaning": "Compare necessary literals and unique observations, not incidental RID values or result ordering."}
                result["full_return_passed"] = result["full_return_passed"] and batch["full_return_passed"]
            reports.append({"case": case["case"], "query": case["query"], "input_records": len(case["messages"]),
                            "input_chars": sum(len(item["text"]) for item in case["messages"]), **result})
    finally:
        for connection in memory.connections:
            connection.close()
    return reports


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--export-fixtures", type=Path)
    parser.add_argument("--vectors-cache", type=Path)
    parser.add_argument("--self-check-decoder", action="store_true")
    parser.add_argument("--include-context", action="store_true")
    args = parser.parse_args()
    if args.self_check_decoder:
        print(json.dumps({"decoder_checks_passed": decoder_self_check()}))
        return 0
    cases = build_cases(args.seed)
    fixture_hash = hashlib.sha256(json.dumps(cases, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    if args.export_fixtures:
        args.export_fixtures.write_text(json.dumps({"version": VERSION, "seed": args.seed, "fixture_sha256": fixture_hash,
            "cases": cases, "texts": fixture_texts(args.seed)}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"cases": len(cases), "unique_texts": len(fixture_texts(args.seed)), "fixture_sha256": fixture_hash}))
        return 0
    dimension, cached_vectors, cache_metadata = DIM, None, None
    if args.vectors_cache:
        if any(part.casefold() == ".secrets" for part in args.vectors_cache.parts):
            parser.error("credential locations cannot be vector caches")
        document = json.loads(args.vectors_cache.read_text(encoding="utf-8"))
        dimension, cached_vectors = int(document.get("dimensions", 0)), document.get("vectors")
        if dimension <= 0 or not isinstance(cached_vectors, dict):
            parser.error("cache requires dimensions and vectors")
        missing = [text for text in fixture_texts(args.seed) if text not in cached_vectors]
        if missing:
            parser.error(f"shared cache misses {len(missing)} fixture texts")
        if any(not isinstance(vector, list) or len(vector) != dimension or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in vector)
               for vector in cached_vectors.values()):
            parser.error("invalid cached vectors")
        cache_metadata = {"model": document.get("model"), "dimensions": dimension,
                          "sha256": hashlib.sha256(args.vectors_cache.read_bytes()).hexdigest()}
    source_root = args.source_root.resolve()
    original_open, original_connect = builtins.open, sqlite3.connect

    def safe_open(file, *args, **kwargs):
        if isinstance(file, (str, bytes, os.PathLike)) and any(part.casefold() == ".secrets" for part in Path(os.fsdecode(file)).parts):
            raise FileNotFoundError("credential access disabled in extended evaluation")
        return original_open(file, *args, **kwargs)

    def no_network(*args, **kwargs):
        raise RuntimeError("network disabled in extended evaluation")

    with tempfile.TemporaryDirectory(prefix="aml-extended-synthetic-") as directory:
        temporary = Path(directory).resolve()

        def safe_connect(database, *args, **kwargs):
            if str(database) != ":memory:" and not Path(database).resolve().is_relative_to(temporary):
                raise RuntimeError("only fresh synthetic temporary SQLite is allowed")
            return original_connect(database, *args, **kwargs)

        env = {"AML_DB_PATH": str(temporary / "evaluation.db"), "EMBEDDING_DIM": str(dimension),
               "EMBEDDING_API_KEY": "offline-fake", "DASHSCOPE_API_KEY": "offline-fake", "AML_AUTH_TOKEN": "offline-fake",
               "EMBEDDING_MODE": "standard", "EMBEDDING_MODEL": "offline-controlled",
               "EMBEDDING_BASE_URL": "https://offline.invalid/v1", "AML_GOVERNANCE": "false", "AML_QUARANTINE": "false"}
        with patch.dict(os.environ, env), patch("builtins.open", safe_open), patch("sqlite3.connect", safe_connect), \
             patch("socket.create_connection", no_network), patch.object(socket.socket, "connect", no_network), patch.object(socket.socket, "connect_ex", no_network):
            sys.path.insert(0, str(source_root))
            module = importlib.import_module("store")
            for name in ("store", "facts", "config"):
                if Path(sys.modules[name].__file__).resolve().parent != source_root:
                    raise RuntimeError("wrong implementation imported: " + name)
            if Path(module.CONFIG.db_path).resolve().parent != temporary:
                raise RuntimeError("temporary database was not configured")
            results = evaluate(module, source_root, cases, cached_vectors, dimension, args.include_context)
    report = {"evaluator": "extended-context", "version": VERSION, "seed": args.seed,
        "evaluator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "fixture_sha256": fixture_hash,
        "source_root": str(source_root), "source_sha256": {name: hashlib.sha256((source_root / name).read_bytes()).hexdigest() for name in SOURCE_FILES},
        "vectors_cache": cache_metadata, "safety": {"temporary_sqlite_only": True, "provider_requests": 0, "credential_reads": 0},
        "meaning": "Necessary evidence and descriptive arithmetic only. No generated answers, official tasks, official score estimate or asserted platform prefix limit.",
        "acceptance": "Full-return evidence completeness and no privacy/scope/role/fidelity regressions are primary; 8k/16k are separate diagnostics, not mandatory complete-history budgets.",
        "totals": {"cases": len(results), "full_return_passed": sum(item["full_return_passed"] for item in results)}, "cases": results}
    if args.output:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"totals": report["totals"], "fixture_sha256": fixture_hash,
        "cases": [{"case": item["case"], "input_chars": item["input_chars"], "content_chars": item["content_chars"],
                   "full_return_passed": item["full_return_passed"],
                   "views": [{"view": view["view"], "coverage": {key: value for key, value in view["coverage"].items() if key != "missing"},
                              "observations": view["observations"]["visible"]["count"], "leakage": view["privacy_or_scope_leakage"],
                              "literal_errors": len(view["source_literal_failures"]), "role_errors": len(view["role_failures"])} for view in item["views"]]}
                  for item in results], "report": str(args.output.resolve()) if args.output else None}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
