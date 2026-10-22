"""Fixed invented method-dependency checks, with independent reference programs.

Retrieval is evaluated for necessary inputs at 8k/16k and in its complete
return. Reference results come from a separate small interpreter, not an
answering model or a claim that quotation fidelity measures answer accuracy.
All implementation execution is offline against fresh temporary SQLite.
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

from evaluate_extended_context import render, visible_sources

VERSION = "1.0"
DIM = 4
BASE_MS = 1735689600000
SOURCE_FILES = ("store.py", "facts.py", "config.py", "evidence_links.py", "query_plan.py", "reranker.py")


def interpret(program):
    """Independent arithmetic, once-in-order rules and explicit-role updates."""
    def expression(value, state):
        if not isinstance(value, dict):
            return value
        if "var" in value:
            return state[value["var"]]
        op, args = value["op"], [expression(item, state) for item in value["args"]]
        if op == "add": return math.fsum(args)
        if op == "sub": return args[0] - args[1]
        if op == "mul": return math.prod(args)
        if op == "div": return args[0] / args[1]
        if op == "gt": return args[0] > args[1]
        if op == "eq": return args[0] == args[1]
        if op == "and": return all(args)
        raise ValueError("Unsupported independent expression: " + op)

    if program["type"] == "ordered-rules":
        state = dict(program["initial"])
        for rule in program["rules"]:
            if expression(rule["when"], state):
                for key, value in rule["assign"].items():
                    state[key] = expression(value, state)
        return expression(program["result"], state)
    if program["type"] == "role-state":
        target = tuple(program["target"])
        active = None
        for event in program["events"]:
            if tuple(event["key"]) != target or event.get("modality", "asserted") != "asserted":
                continue
            if event["operation"] in {"assert", "replace"}:
                active = event["value"]
            elif event["operation"] == "revoke":
                active = None
            else:
                raise ValueError("Unsupported explicit reference operation")
        return active
    raise ValueError("Unsupported reference program")


def op(name, *args):
    return {"op": name, "args": list(args)}


def var(name):
    return {"var": name}


def build_cases():
    cases = []

    def turn(text, session="notes", role="user", score=.98):
        return {"text": text, "session": session, "role": role, "score": score}

    def clutter(count=128):
        return [turn(f"Bench notebook {index:03d}: " +
            "The new observation is listed beside an ordinary cabinet inventory and seating plan; "
            "the note describes filing colours, drawer labels and the route to the instrument room. " * 9,
            "unrelated-notebooks", score=.88) for index in range(count)]

    def add(key, query, messages, required, program, expected, *, forbidden=(), roles=(), decoys=()):
        actual = interpret(program)
        if isinstance(expected, float):
            valid = isinstance(actual, (int, float)) and math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12)
        else:
            valid = actual == expected
        if not valid:
            raise AssertionError((key, actual, expected))
        cases.append({"case": key, "query": query, "messages": messages, "required": list(required),
                      "forbidden": list(forbidden), "roles": list(roles), "decoys": list(decoys),
                      "reference_program": program, "reference_result": actual})

    method = ("For our benchtop fill measurements, the displayed amount is in millilitres and duration is in milliseconds. "
              "Subtract 0.60 mL from the amount before conversion. Convert the corrected amount to litres and duration to seconds before dividing. "
              "This method is valid only with the vent open; observations with a closed vent are rejected.")
    needed = ["displayed amount is in millilitres", "duration is in milliseconds", "Subtract 0.60 mL",
              "Convert the corrected amount to litres", "only with the vent open", "closed vent are rejected"]
    for key, raw, duration, vent, query in (
        ("M01_implicit_method_transfer", 12.60, 400., "open",
         "For the same benchtop fill setup in this new conversation, the gauge shows 12.60 mL over 400 ms with the vent open. What throughput follows our earlier convention?"),
        ("M02_implicit_invalid_observation", 12.60, 400., "closed",
         "The same benchtop fill setup now shows 12.60 mL over 400 ms with the vent closed. Under our earlier convention, should I calculate a usable throughput?"),
        ("M03_cross_language_method_transfer", 25.60, 500., "open",
         "还是之前那套台式灌注装置，这次显示25.60毫升、历时500毫秒，排气口打开。按我们以前的校准和单位约定，流量是多少？"),
    ):
        program = {"type": "ordered-rules", "initial": {"raw": raw, "duration": duration, "vent": vent, "result": "rejected"},
            "rules": [{"when": op("eq", var("vent"), "open"), "assign": {"result": op("div", op("div", op("sub", var("raw"), .60), 1000), op("div", var("duration"), 1000))}}], "result": var("result")}
        new = turn(f"The benchtop fill setup's new observation is amount={raw:.2f} mL; duration={duration:.0f} ms; vent={vent}.", "new-conversation", score=.99)
        required = needed if vent == "open" else ["only with the vent open", "closed vent are rejected"]
        add(key, query, [turn(method, "earlier-calibration", score=.22)] + clutter() + [new], required,
            program, "rejected" if vent == "closed" else .03 if raw == 12.60 else .05)

    shipping = ("For insulated shipment estimates, base cost is 9 credits plus 2.5 credits per kg. "
                "If package mass exceeds 12 kg, add a 7-credit handling surcharge. Exactly 12 kg does not receive that surcharge. "
                "Exception: a documented rescue shipment waives the surcharge but does not waive base or per-kg charges.")
    for key, mass, rescue, expected in (("R01_strict_boundary",12.,False,39.),
                                       ("R02_exception_preserves_other_charges",12.1,True,39.25)):
        program = {"type":"ordered-rules", "initial":{"mass":mass,"rescue":rescue,"surcharge":0},
            "rules":[{"when":op("gt",var("mass"),12),"assign":{"surcharge":7}},
                     {"when":op("eq",var("rescue"),True),"assign":{"surcharge":0}}],
            "result":op("add",9,op("mul",2.5,var("mass")),var("surcharge"))}
        query = f"Quote the total for an insulated shipment of {mass:.1f} kg, documented rescue={'yes' if rescue else 'no'}, using our earlier pricing policy."
        required = ["base cost is 9 credits", "2.5 credits per kg"] + (
            ["documented rescue shipment waives the surcharge", "does not waive base or per-kg charges"] if rescue
            else ["exceeds 12 kg", "Exactly 12 kg does not receive"])
        add(key, query, [turn(shipping,"tariff",score=.40)] + clutter() + [turn(query,"later-order",score=.99)],
            required, program, expected)

    workflow = ("Process these switchboard instructions once each, in their written order. "
                "An instruction whose precondition is false is skipped permanently; never return to an earlier instruction after a later one changes the state. "
                "Instruction 1: if latch is closed, add 10 to counter. Instruction 2: if counter is zero, close the latch. "
                "Instruction 3: if latch is closed, add 1 to counter.")
    program = {"type":"ordered-rules","initial":{"latch":"open","counter":0},"rules":[
        {"when":op("eq",var("latch"),"closed"),"assign":{"counter":op("add",var("counter"),10)}},
        {"when":op("eq",var("counter"),0),"assign":{"latch":"closed"}},
        {"when":op("eq",var("latch"),"closed"),"assign":{"counter":op("add",var("counter"),1)}}],"result":var("counter")}
    add("R03_once_only_skipped_rule", "Using the earlier switchboard procedure, start with latch open and counter zero. What is the final counter?",
        [turn(workflow,"switchboard",score=.45)] + clutter(28),
        ["once each, in their written order", "false is skipped permanently", "never return to an earlier instruction",
         "if latch is closed, add 10", "if counter is zero, close the latch", "if latch is closed, add 1"], program,1)

    name_events = [
        {"key":["user","self","name"],"operation":"assert","value":"Tamsin"},
        {"key":["assistant","self","name"],"operation":"assert","value":"Vale"},
        {"key":["user","self","name"],"operation":"replace","value":"Maren"},
    ]
    add("C01_role_separated_explicit_replacement", "What is my name now, and which earlier value did I explicitly replace?",
        [turn("My name is Tamsin.","identity",score=.96),
         turn("My name is Vale. This is a fictional assistant persona, not the user's identity.","illustration","assistant",.99),
         turn("Correction: my name is now Maren. The earlier name Tamsin no longer applies.","identity-update",score=.98)] + clutter(16),
        ["Maren", "earlier name Tamsin no longer applies"],
        {"type":"role-state","target":["user","self","name"],"events":name_events},"Maren",
        roles=[{"marker":"earlier name Tamsin no longer applies","role":"user","required":True},
               {"marker":"fictional assistant persona","role":"assistant","required":False}])

    old_cutoff = "For the late-shift handling workflow, the pressure cutoff is 72 kPa. Reject any run above that cutoff."
    revoked = ("The late-shift safety board withdrew the 72 kPa cutoff after sensor drift. "
               "No replacement cutoff has been approved; do not treat any new threshold as established.")
    add("C02_revocation_without_replacement", "Can a 70 kPa late-shift run be accepted under the currently approved cutoff? Explain any missing basis.",
        [turn(old_cutoff,"initial-limit",score=.60),turn(revoked,"withdrawal",score=.50),
         turn("Hypothetically, a future revision could choose an 85 kPa cutoff; this has not been approved.","fiction","assistant",.97)]
        + clutter() + [turn("New late-shift run: pressure=70 kPa.","new-run",score=.99)],
        ["withdrew the 72 kPa cutoff", "after sensor drift", "No replacement cutoff has been approved", "do not treat any new threshold as established"],
        {"type":"role-state","target":["user","late-shift","cutoff"],"events":[
            {"key":["user","late-shift","cutoff"],"operation":"assert","value":72},
            {"key":["user","late-shift","cutoff"],"operation":"revoke","value":72},
            {"key":["assistant","late-shift","cutoff"],"operation":"assert","modality":"hypothetical-future","value":85}]},None,
        roles=[{"marker":"No replacement cutoff has been approved","role":"user","required":True}],
        decoys=["future revision could choose an 85 kPa cutoff"])

    authorization = [
        {"key":["user","Aster Community Lab/Mara","release"],"operation":"assert","value":True},
        {"key":["user","Briar Community Lab/Mara","release"],"operation":"assert","value":True},
        {"key":["assistant","Aster Community Lab/Mara","release"],"operation":"assert","modality":"fictional-future","value":True},
        {"key":["user","Aster Community Lab/Mara","release"],"operation":"revoke","value":True},
    ]
    add("C03_scope_role_and_future_do_not_cancel_revocation", "May Mara release results for Aster Community Lab now, and what changed the permission?",
        [turn("Aster Community Lab approved analyst Mara to release results.","initial-approval",score=.95),
         turn("Mara remains approved to release results for Briar Community Lab; that is a different appointment.","other-lab",score=.98),
         turn("In a fictional future scenario, Mara passes a new audit and all Aster restrictions are lifted.","fiction","assistant",.99),
         turn("The audit board withdrew Mara's release authority for Aster Community Lab after a chain-of-custody breach; this revocation is effective immediately.","revocation",score=.96)] + clutter(18),
        ["withdrew Mara's release authority for Aster Community Lab", "chain-of-custody breach", "effective immediately"],
        {"type":"role-state","target":["user","Aster Community Lab/Mara","release"],"events":authorization},None,
        roles=[{"marker":"fictional future scenario","role":"assistant","required":False},
               {"marker":"chain-of-custody breach","role":"user","required":True}],
        decoys=["different appointment", "fictional future scenario"])
    return cases


def fixture_texts():
    return sorted({text for case in build_cases() for text in
                   [case["query"]] + [item["text"] for item in case["messages"]]})


class ReplayReranker:
    """Capture/replay complete ordered synthetic requests; never call a provider."""
    def __init__(self, cache):
        self.cache = cache or {}
        self.requests = {}
        self.hits = 0

    def rerank(self, query, documents):
        request = {"query": query, "documents": list(documents)}
        key = hashlib.sha256(json.dumps(request,ensure_ascii=False,separators=(",",":")).encode()).hexdigest()
        self.requests.setdefault(key, request)
        if key not in self.cache:
            return None
        indices = self.cache[key]
        if (not isinstance(indices,list) or len(indices)!=len(documents)
                or any(not isinstance(index,int) or isinstance(index,bool)
                or index<0 or index>=len(documents) for index in indices) or len(set(indices))!=len(indices)):
            raise ValueError("invalid ordered indices for synthetic rerank request " + key)
        self.hits += 1
        return list(indices)


def self_check():
    """Check interpretation boundaries and the full-request cache contract."""
    cases=build_cases()  # Each independent reference is checked against a manual result.
    requests=[{"query":"first question","documents":["alpha","beta"]},
              {"query":"first question","documents":["beta","alpha"]},
              {"query":"second question","documents":["alpha","beta"]}]
    keys=[hashlib.sha256(json.dumps(item,ensure_ascii=False,separators=(",",":")).encode()).hexdigest() for item in requests]
    assert len(set(keys))==3
    replay=ReplayReranker({keys[0]:[1,0]})
    assert replay.rerank(**requests[0])==[1,0]
    assert replay.rerank(**requests[1]) is None
    assert replay.rerank(**requests[2]) is None
    assert replay.hits==1 and len(replay.requests)==3
    for bad in ([0,0],[0],[False,1],[-1,1]):
        try:
            ReplayReranker({keys[0]:bad}).rerank(**requests[0])
        except ValueError:
            pass
        else:
            raise AssertionError("invalid complete-request order accepted")
    return {"fixture_reference_checks":len(cases),"cache_contract_checks":9}


def evaluate(module, cases, vectors, dimension, include_context, reranker=None):
    class Offline(module.MemoryStore):
        def __init__(self):
            self._dim, self.connections, self.vectors = dimension, [], dict(vectors or {})
            if reranker is not None:
                self._reranker = reranker
            self._ensure_schema()
        def _connect(self):
            connection = super()._connect()
            self.connections.append(connection)
            return connection
        def embed(self, texts):
            return [self.vectors[text] for text in texts]
    memory, reports = Offline(), []
    try:
        for case in cases:
            user = "method-dependency:" + case["case"]
            if not vectors:
                for item in case["messages"]:
                    score = item["score"]
                    memory.vectors[item["text"]] = [score, math.sqrt(1-score*score),0.,0.]
                # Equal strings must have equal embeddings when a new record
                # happens to repeat the exact query, just as in a real cache.
                memory.vectors[case["query"]] = [1.,0.,0.,0.]
            for session, group in itertools.groupby(case["messages"],key=lambda item:item["session"]):
                turns = list(group)
                memory.add_messages(user, session, [{"role":item["role"],"content":item["text"],"timestamp":BASE_MS+index*1000}
                    for index,item in enumerate(turns)])
            started = time.perf_counter()
            rows = memory.search(user,case["query"],100)
            elapsed = (time.perf_counter()-started)*1000
            rendered = render(rows)
            views=[]
            for label,budget in (("prefix_8000",8000),("prefix_16000",16000),("full_return",len(rendered))):
                text,witnesses = visible_sources(rows,rendered,budget)
                expanded = text + "\n" + "\n".join(item["literal"] for item in witnesses)
                missing=[literal for literal in case["required"] if literal not in expanded]
                leaks=[literal for literal in case["forbidden"] if literal in expanded]
                role_failures=[]
                for check in case["roles"]:
                    found=[item for item in witnesses if check["marker"] in item["literal"]]
                    if check["required"] and not found:
                        role_failures.append({**check,"reason":"no visible attributed source witness"})
                    if any(item["role"] != check["role"] for item in found):
                        role_failures.append({**check,"reason":"wrong role on visible witness"})
                views.append({"view":label,"necessary_inputs_complete":not missing,"missing_inputs":missing,
                    "input_coverage":{"required":len(case["required"]),"present":len(case["required"])-len(missing)},
                    "privacy_failures":leaks,"role_failures":role_failures,
                    "attributed_distractors_present":[literal for literal in case["decoys"] if literal in expanded],
                    "passed":not missing and not leaks and not role_failures})
            report={"case":case["case"],"query":case["query"],"input_records":len(case["messages"]),
                "reference_result":interpret(case["reference_program"]),"reference_program":case["reference_program"],
                "reference_meaning":"Independent fixture result only; no generated answer was assessed.",
                "result_count":len(rows),"content_chars":sum(len(row["content"]) for row in rows),
                "local_search_ms":round(elapsed,2),"views":views,"full_return_passed":views[-1]["passed"]}
            if include_context: report["returned_context"]=rendered
            reports.append(report)
    finally:
        for connection in memory.connections: connection.close()
    return reports


def main():
    if hasattr(sys.stdout,"reconfigure"): sys.stdout.reconfigure(encoding="utf-8")
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root",type=Path,default=Path(__file__).resolve().parent)
    parser.add_argument("--output",type=Path)
    parser.add_argument("--export-fixtures",type=Path)
    parser.add_argument("--vectors-cache",type=Path)
    parser.add_argument("--rerank-cache",type=Path)
    parser.add_argument("--capture-rerank-inputs",type=Path)
    parser.add_argument("--include-context",action="store_true")
    parser.add_argument("--self-check",action="store_true")
    args=parser.parse_args()
    if args.self_check:
        print(json.dumps(self_check()))
        return 0
    cases=build_cases()
    fixture_hash=hashlib.sha256(json.dumps(cases,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    if args.export_fixtures:
        document={"version":VERSION,"fixture_sha256":fixture_hash,"cases":cases,"texts":fixture_texts(),
            "provider_input_meaning":"Exact embedding input strings; rerank cache must separately hash complete query plus ordered candidate documents."}
        args.export_fixtures.write_text(json.dumps(document,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps({"cases":len(cases),"unique_texts":len(document["texts"]),"fixture_sha256":fixture_hash}))
        return 0
    dimension,vectors,cache_metadata=DIM,None,None
    if args.vectors_cache:
        if any(part.casefold()==".secrets" for part in args.vectors_cache.parts): parser.error("credential paths are not caches")
        document=json.loads(args.vectors_cache.read_text(encoding="utf-8"))
        dimension,vectors=int(document.get("dimensions",0)),document.get("vectors")
        if dimension<=0 or not isinstance(vectors,dict): parser.error("cache requires dimensions and vectors")
        if any(text not in vectors for text in fixture_texts()): parser.error("embedding cache misses fixture texts")
        if any(not isinstance(vector,list) or len(vector)!=dimension or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in vector) for vector in vectors.values()): parser.error("invalid cached vectors")
        cache_metadata={"model":document.get("model"),"dimensions":dimension,"sha256":hashlib.sha256(args.vectors_cache.read_bytes()).hexdigest()}
    source_root=args.source_root.resolve()
    rerank_cache = None
    if args.rerank_cache:
        if any(part.casefold()==".secrets" for part in args.rerank_cache.parts): parser.error("credential paths are not caches")
        rerank_cache=json.loads(args.rerank_cache.read_text(encoding="utf-8"))
        if not isinstance(rerank_cache,dict): parser.error("rerank cache must map full-request SHA256 to ordered indices")
    reranker=ReplayReranker(rerank_cache) if args.rerank_cache or args.capture_rerank_inputs else None
    original_open,original_connect=builtins.open,sqlite3.connect
    def safe_open(file,*a,**kw):
        if isinstance(file,(str,bytes,os.PathLike)) and any(part.casefold()==".secrets" for part in Path(os.fsdecode(file)).parts):
            raise FileNotFoundError("credential reads disabled")
        return original_open(file,*a,**kw)
    def no_network(*a,**kw): raise RuntimeError("provider calls disabled in independent synthetic evaluation")
    with tempfile.TemporaryDirectory(prefix="aml-method-dependency-") as directory:
        temporary=Path(directory).resolve()
        def safe_connect(database,*a,**kw):
            if str(database)!=":memory:" and not Path(database).resolve().is_relative_to(temporary):
                raise RuntimeError("only fresh synthetic temporary SQLite allowed")
            return original_connect(database,*a,**kw)
        env={"AML_DB_PATH":str(temporary/"synthetic.db"),"EMBEDDING_DIM":str(dimension),"EMBEDDING_API_KEY":"offline-fake",
            "DASHSCOPE_API_KEY":"offline-fake","AML_AUTH_TOKEN":"offline-fake","EMBEDDING_MODE":"standard",
            "EMBEDDING_MODEL":"offline-fixed-vectors","EMBEDDING_BASE_URL":"https://offline.invalid/v1",
            "AML_GOVERNANCE":"false","AML_QUARANTINE":"false"}
        with patch.dict(os.environ,env),patch("builtins.open",safe_open),patch("sqlite3.connect",safe_connect), \
             patch("socket.create_connection",no_network),patch.object(socket.socket,"connect",no_network),patch.object(socket.socket,"connect_ex",no_network):
            sys.path.insert(0,str(source_root))
            module=importlib.import_module("store")
            for name in ("store","facts","config"):
                if Path(sys.modules[name].__file__).resolve().parent!=source_root: raise RuntimeError("wrong source import")
            reports=evaluate(module,cases,vectors,dimension,args.include_context,reranker)
    if args.capture_rerank_inputs:
        args.capture_rerank_inputs.write_text(json.dumps({"fixture_sha256":fixture_hash,
            "cache_key":"SHA256 of UTF-8 json.dumps({query,documents},ensure_ascii=False,separators=(',',':')); preserve document order",
            "requests":reranker.requests},ensure_ascii=False,indent=2),encoding="utf-8")
    report={"evaluator":"method-dependencies","version":VERSION,"fixture_sha256":fixture_hash,
        "evaluator_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),"source_root":str(source_root),
        "source_sha256":{name:hashlib.sha256((source_root/name).read_bytes()).hexdigest() for name in SOURCE_FILES if (source_root/name).exists()},
        "vectors_cache":cache_metadata,"safety":{"temporary_sqlite_only":True,"provider_requests":0,"credential_reads":0},
        "rerank_replay":None if reranker is None else {"unique_requests":len(reranker.requests),"cache_hits":reranker.hits,
            "cache_sha256":hashlib.sha256(args.rerank_cache.read_bytes()).hexdigest() if args.rerank_cache else None,
            "missing_request_keys":sorted(set(reranker.requests)-set(reranker.cache))},
        "meaning":"Necessary-input retrieval plus independently calculated fixture references; not answering-model accuracy or an official score estimate.",
        "totals":{"cases":len(reports),"full_return_passed":sum(item["full_return_passed"] for item in reports)},"cases":reports}
    if args.output: args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"totals":report["totals"],"fixture_sha256":fixture_hash,"cases":[{
        "case":item["case"],"reference_result":item["reference_result"],"full_return_passed":item["full_return_passed"],
        "views":[{"view":view["view"],"coverage":view["input_coverage"],"role_failures":len(view["role_failures"]),"privacy_failures":view["privacy_failures"]} for view in item["views"]]}
        for item in reports]},ensure_ascii=False,indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
