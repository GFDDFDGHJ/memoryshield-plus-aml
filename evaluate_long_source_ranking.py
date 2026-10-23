"""Invented long-source ranking-copy and returned-evidence controls.

The actual target RerankerClient uses an injected offline transport, so both
whole Store requests and bounded UTF-8 wire copies are captured. No provider
calls or generated answers occur. Source rank, necessary evidence coverage
and ranking-input visibility are deliberately separate measurements.
"""
from __future__ import annotations

import argparse
import builtins
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
from unittest.mock import patch

from evaluate_extended_context import decode_visible, render, visible_sources

VERSION = "1.0"
DIM = 4


def request_key(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,separators=(",",":")).encode()).hexdigest()


def build_cases():
    cases=[]
    def turn(text,session,role="user",score=.88):
        return {"text":text,"session":session,"role":role,"score":score}
    def preamble(topic):
        return (f"The {topic} notebook mentions measurements, limits, adjustments and operating conditions, "
                "but this entry only catalogues cupboard labels, empty drawers and the route to the equipment room. ")*58+"\n"
    def noise(topic):
        return [turn(f"Clerical note {index}: the {topic} measurements and conditions appear on a filing label; "
            "this entry records furniture colours and contains no calibration or admission decision.",
            f"clerical-{index}",score=.89) for index in range(9)]
    def add(key,query,prefix,tail,required,decoy_tail,*,role="user",followup=None,reference=None):
        assert len(prefix.encode())>3500
        records=[turn(prefix+tail,"definition",role,.72),turn(prefix+decoy_tail,"unrelated-example","user",.98)]
        records+=noise(key)
        if followup:records.append(turn(followup,"new-conversation","user",.99))
        cases.append({"case":key,"query":query,"messages":records,"target_index":0,"required":list(required),
            "tail_start_bytes":len(prefix.encode()),"target_role":role,"target_session":"definition","reference":reference})
    add("L01_identical_prefix_tail_calibration",
        "Apply the argon transfer method to sensor=36.00 kPa, exhaust valve shut and moisture absent. Which calibration, boundary and restrictions govern acceptance?",
        preamble("argon transfer pressure sensor valve moisture"),
        "Binding argon-transfer method: calibration subtracts 1.25 kPa from the sensor reading. "
        "The corrected-pressure ceiling is 34.75 kPa, inclusive. Only runs with the exhaust valve shut are admissible. "
        "If moisture is detected, reject the run instead of applying the ceiling.",
        ["subtracts 1.25 kPa","34.75 kPa, inclusive","exhaust valve shut","moisture is detected, reject"],
        "This appendix is a hypothetical decorative mock-up. It supplies no argon-transfer method, calibration value or admission threshold.",
        reference={"corrected_kPa":36.-1.25,"ceiling_kPa":34.75,"accepted":True})
    add("L02_tail_exception_and_permission_chain",
        "For resin curing, temperature is 46.8 C and the vessel is unsealed with a documented leak check. Which release criterion and exceptions apply?",
        preamble("resin curing temperature release safety tolerance"),
        "Resin release criterion: the measured temperature must be at least 41.5 C. "
        "However, this threshold is applicable only to a fully sealed vessel. Otherwise, use a 47.25 C threshold. "
        "Unless the supervisor has documented a leak check, release is forbidden. "
        "Never substitute an illustrative future threshold for these criteria.",
        ["at least 41.5 C","only to a fully sealed vessel","47.25 C threshold",
         "documented a leak check, release is forbidden","Never substitute an illustrative future threshold"],
        "These cabinet inventory notes describe a different resin exhibition. A fictional future demonstration has no active release criterion.",
        reference={"applicable_threshold_C":47.25,"released":False})
    # More than 3500 bytes but shorter than the 3200-character raw preview
    # trigger: this isolates ranking-prefix loss from long-raw omission.
    prefix=("灌注装置记录只提及显示体积、计时、单位与有效条件，本段仅登记空柜、器材位置和座位，不规定任何计算方法。"*29)+"\n"
    add("L03_multibyte_wire_boundary_raw_complete",
        "灌注装置显示10.85毫升、计时500毫秒且排气口打开。请按正式计算方法确定校准、单位及有效条件。",
        prefix,"正式灌注计算方法：显示体积须先减去0.85毫升；计时单位为毫秒。"
        "然后把修正体积换成升、计时换成秒再相除。只有排气口打开的观测才有效；否则必须拒绝计算。",
        ["减去0.85毫升","计时单位为毫秒","修正体积换成升","只有排气口打开的观测才有效","否则必须拒绝计算"],
        "本附录是虚构的未来器材陈列，不提供正式灌注计算方法，也不提供任何替代校准。",
        reference={"litres_per_second":(10.85-.85)/1000/(500/1000)})
    add("L04_cross_session_explicit_background_control",
        "Apply protocol PX-MARIGOLD to the new q=8.40, t=400, mode=dry sample using the established units and exclusions.",
        preamble("impulse measurements new sample earlier convention protocol"),
        "Protocol ID: PX-MARIGOLD. Schema: q is impulse in millinewton seconds and t is elapsed milliseconds. "
        "Calibration subtracts 0.40 from q. Convert t to seconds before dividing. Only dry-mode observations are valid. "
        "Never create extra observations from repeated equal values.",
        ["millinewton seconds","elapsed milliseconds","subtracts 0.40","Convert t to seconds",
         "Only dry-mode observations","Never create extra observations"],
        "Protocol ID: PX-MARIGOLD-EXHIBIT. This source describes only an unrelated exhibition packing list.",
        followup="Protocol PX-MARIGOLD new sample: q=8.40; t=400; mode=dry.",
        reference={"corrected_impulse_per_second":(8.40-.40)/.400})
    add("L05_long_role_attribution_and_execution_control",
        "Apply the human operator's switchboard instructions to latch=open and counter=0. What execution controls and conditions must be retained?",
        preamble("switchboard latch counter skip rules"),
        "Human operator switchboard instructions: process the instructions once each in written order. "
        "An instruction whose condition is false is skipped permanently; never revisit it. "
        "Rule A: if latch is closed, add 10 to counter. Rule B: if counter is zero, close the latch. "
        "Rule C: if latch is closed, add 1 to counter.",
        ["once each in written order","false is skipped permanently","never revisit it",
         "add 10 to counter","if counter is zero, close the latch","add 1 to counter"],
        "This is a fictional future persona. It changes no human operator instructions and introduces no applicable switchboard control.",
        reference={"counter_after_one_pass":1})
    cases[-1]["messages"][1]["role"]="assistant"
    add("L06_tail_table_schema_units_and_exclusion",
        "For the flow experiment sample dose_mg=4.50, elapsed_ms=250, condition=sealed, which schema, rate conversion and exclusion apply?",
        preamble("flow experiment dose elapsed sample condition rate"),
        "Flow experiment schema: dose_mg is measured dose and elapsed_ms is elapsed duration.\n"
        "dose_mg | elapsed_ms | condition\n4.50 | 250 | sealed\n9.00 | 500 | sealed\n"
        "Only sealed samples may be used. Otherwise, discard the sample. "
        "Divide elapsed_ms by 1000 to obtain seconds before dividing dose_mg by elapsed time.",
        ["dose_mg is measured dose","elapsed_ms is elapsed duration","4.50 | 250 | sealed",
         "Only sealed samples may be used","Otherwise, discard the sample","Divide elapsed_ms by 1000"],
        "The flow experiment display is an unrelated visual mock-up and supplies no empirical table or computation instructions.",
        reference={"mg_per_second":4.5/(250/1000)})
    return cases


class RankingRecorder:
    """Replay Store requests but capture actual target-client wire payloads."""
    def __init__(self,client_type,cache):
        self.cache=cache or {};self.requests={};self.wire={};self.current_key=None;self.hits=0
        self.client=client_type("offline-synthetic-placeholder",transport=self.transport)
    def transport(self,url,payload,headers,timeout):
        # Headers, URL and credential are intentionally never retained.
        self.wire[self.current_key]={"query":payload["input"]["query"],"documents":payload["input"]["documents"],
            "model":payload["model"],"parameters":payload["parameters"]}
        if self.current_key not in self.cache:return 503,{}
        order=self.cache[self.current_key];n=len(payload["input"]["documents"])
        if not isinstance(order,list) or len(order)!=n or any(type(i) is not int for i in order) or set(order)!=set(range(n)):
            raise ValueError("replay requires a complete index permutation")
        self.hits+=1
        return 200,{"output":{"results":[{"index":index,"relevance_score":1.-rank/(n+1)} for rank,index in enumerate(order)]}}
    def rerank(self,query,documents):
        request={"query":query,"documents":list(documents)};key=request_key(request)
        self.current_key=key;self.requests.setdefault(key,request)
        return self.client.rerank(query,list(documents))


def expanded(text):
    return text+"\n"+"\n".join(item["literal"] for item in decode_visible(text,complete=True))


def evaluate(module,cases,cache,dimension,vectors,include_context):
    recorder=RankingRecorder(module.RerankerClient,cache)
    class Offline(module.MemoryStore):
        def __init__(self):
            self._dim,self.connections,self.vectors=dimension,[],dict(vectors or {})
            self._reranker=recorder;self.traces=[];self._ensure_schema()
        def _connect(self):
            connection=super()._connect();self.connections.append(connection);return connection
        def embed(self,texts):return [self.vectors[text] for text in texts]
        def _rerank_rows(self,query,ranked):
            before=[row["rid"] for _,row in ranked];result=super()._rerank_rows(query,ranked)
            self.traces.append({"query":query,"before_rids":before,"after_rids":[row["rid"] for _,row in result]})
            return result
    memory,reports=Offline(),[]
    try:
        for case in cases:
            user="long-source:"+case["case"]
            if not vectors:
                for item in case["messages"]:
                    score=item["score"];memory.vectors[item["text"]]=[score,math.sqrt(1-score*score),0.,0.]
                memory.vectors[case["query"]]=[1.,0.,0.,0.]
            for index,item in enumerate(case["messages"]):
                memory.add_messages(user,item["session"],[{"role":item["role"],"content":item["text"],"timestamp":1735689600000+index*1000}])
            with memory._connect() as conn:
                sources=[dict(row) for row in conn.execute("SELECT * FROM items WHERE user_id=? ORDER BY seq,rid",(user,))]
            target=sources[case["target_index"]];keys_before=set(recorder.requests);trace_count=len(memory.traces)
            # Separate deterministic representation probe, even when the
            # Store intentionally skips reranking a summary/temporal query.
            # Its one-document request is not exported for paid preparation.
            original=target["content"]
            document=(module._ranking_document(case["query"],target) if hasattr(module,"_ranking_document")
                else f"[speaker role={target['role']}]\n{target['content']}")
            probe=RankingRecorder(module.RerankerClient,{})
            probe.rerank(case["query"],[document])
            probe_wire=next(iter(probe.wire.values()),None)
            wire_document="" if probe_wire is None else probe_wire["documents"][0]
            representation_probe={"meaning":"One-document offline client-copy diagnostic, not a Store rerank request or model ranking.",
                "ranking_copy_bytes":len(document.encode()),"wire_bytes":len(wire_document.encode()),
                "wire_present":[literal for literal in case["required"] if literal in wire_document],
                "wire_missing":[literal for literal in case["required"] if literal not in wire_document],
                "role_header_preserved":f"[speaker role={case['target_role']}]" in wire_document,
                "source_unchanged":target["content"]==original}
            rows=memory.search(user,case["query"],100);text=render(rows)
            ranks=[];first_complete=None;accumulated=[];metadata_errors=[]
            for index,row in enumerate(rows,1):
                witnesses=decode_visible(row["content"],complete=True)
                if row.get("source_rid")==target["rid"] or any(item["rid"]==target["rid"] for item in witnesses):
                    ranks.append(index)
                    if row.get("source_rid")==target["rid"] and row.get("role") is not None and row["role"]!=case["target_role"]:
                        metadata_errors.append({"rank":index,"role":row.get("role")})
                accumulated.append(expanded(row["content"]))
                if first_complete is None and all(literal in "\n".join(accumulated) for literal in case["required"]):first_complete=index
            views=[]
            for label,budget in (("prefix_8000",8000),("prefix_16000",16000),("full_return",len(text))):
                visible,witnesses=visible_sources(rows,text,budget)
                visible+="\n"+"\n".join(item["literal"] for item in witnesses)
                missing=[literal for literal in case["required"] if literal not in visible]
                views.append({"view":label,"required":len(case["required"]),"present":len(case["required"])-len(missing),"missing":missing,"complete":not missing})
            ranking=[]
            for key in sorted(set(recorder.requests)-keys_before):
                request=recorder.requests[key];wire=recorder.wire.get(key);docs=[] if wire is None else wire["documents"]
                missing=[literal for literal in case["required"] if literal not in "\n".join(docs)]
                twins=[[a,b] for a in range(len(docs)) for b in range(a+1,len(docs)) if docs[a]==docs[b]]
                ranking.append({"request_key":key,"document_count":len(request["documents"]),"client_sent_wire":wire is not None,
                    "wire_evidence_present":len(case["required"])-len(missing),"wire_evidence_required":len(case["required"]),"wire_missing":missing,
                    "identical_wire_document_pairs":twins,"largest_wire_document_bytes":max((len(doc.encode()) for doc in docs),default=0),
                    "weighted_wire_utf8_bytes":0 if wire is None else len(wire["query"].encode())*len(docs)+sum(len(doc.encode()) for doc in docs)})
            trace=memory.traces[trace_count:]
            reports.append({"case":case["case"],"query":case["query"],"reference":case["reference"],"tail_start_bytes":case["tail_start_bytes"],
                "target_source_metadata":{key:target[key] for key in ("rid","session_id","role","seq","created_at","timestamp_origin")},
                "target_source_chars":len(target["content"]),"target_source_bytes":len(target["content"].encode()),
                "first_relevant_source_result_rank":min(ranks) if ranks else None,"first_complete_evidence_result_rank":first_complete,
                "source_metadata_errors":metadata_errors,"dense_or_pre_rerank_target_rank":next((item["before_rids"].index(target["rid"])+1 for item in trace if target["rid"] in item["before_rids"]),None),
                "post_rerank_target_rank":next((item["after_rids"].index(target["rid"])+1 for item in trace if target["rid"] in item["after_rids"]),None),
                "result_count":len(rows),"content_chars":sum(len(row["content"]) for row in rows),"views":views,"ranking_requests":ranking,
                "representation_only_probe":representation_probe,
                **({"returned_context":text} if include_context else {})})
    finally:
        for connection in memory.connections:connection.close()
    return reports,recorder


def main():
    if hasattr(sys.stdout,"reconfigure"):sys.stdout.reconfigure(encoding="utf-8")
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root",type=Path,default=Path(__file__).resolve().parent)
    parser.add_argument("--output",type=Path);parser.add_argument("--export-fixtures",type=Path)
    parser.add_argument("--capture-rerank-inputs",type=Path);parser.add_argument("--rerank-cache",type=Path)
    parser.add_argument("--vectors-cache",type=Path);parser.add_argument("--include-context",action="store_true")
    args=parser.parse_args();cases=build_cases()
    fixture_hash=hashlib.sha256(json.dumps(cases,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    texts=sorted({text for case in cases for text in [case["query"]]+[item["text"] for item in case["messages"]]})
    if args.export_fixtures:
        args.export_fixtures.write_text(json.dumps({"version":VERSION,"fixture_sha256":fixture_hash,"cases":cases,"texts":texts},ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps({"cases":len(cases),"unique_texts":len(texts),"fixture_sha256":fixture_hash}));return 0
    cache=json.loads(args.rerank_cache.read_text(encoding="utf-8")) if args.rerank_cache else {}
    dimension,vectors=DIM,None
    if args.vectors_cache:
        document=json.loads(args.vectors_cache.read_text(encoding="utf-8"));dimension=int(document["dimensions"]);vectors=document["vectors"]
        if dimension<=0 or any(text not in vectors or len(vectors[text])!=dimension or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in vectors[text]) for text in texts):
            raise ValueError("invalid or incomplete fixed embedding cache")
    root=args.source_root.resolve();original_open,original_connect=builtins.open,sqlite3.connect
    def safe_open(file,*a,**kw):
        if ".secrets" in str(file):raise FileNotFoundError("no credential file access")
        return original_open(file,*a,**kw)
    def no_network(*a,**kw):raise RuntimeError("no provider requests in long-source evaluation")
    with tempfile.TemporaryDirectory(prefix="aml-long-source-ranking-") as directory:
        temporary=Path(directory).resolve()
        def safe_connect(database,*a,**kw):
            if str(database)!=":memory:" and not Path(database).resolve().is_relative_to(temporary):raise RuntimeError("only synthetic temporary SQLite allowed")
            return original_connect(database,*a,**kw)
        env={"AML_DB_PATH":str(temporary/"synthetic.db"),"EMBEDDING_DIM":str(dimension),"EMBEDDING_API_KEY":"offline-fake",
             "DASHSCOPE_API_KEY":"offline-fake","AML_AUTH_TOKEN":"offline-fake","AML_GOVERNANCE":"false","AML_QUARANTINE":"false"}
        with patch.dict(os.environ,env),patch("builtins.open",safe_open),patch("sqlite3.connect",safe_connect),patch("socket.create_connection",no_network),patch.object(socket.socket,"connect",no_network),patch.object(socket.socket,"connect_ex",no_network):
            sys.path.insert(0,str(root));module=importlib.import_module("store")
            for name in ("store","facts","config","reranker"):
                if Path(sys.modules[name].__file__).resolve().parent!=root:raise RuntimeError("wrong source imported")
            reports,recorder=evaluate(module,cases,cache,dimension,vectors,args.include_context)
    if args.capture_rerank_inputs:
        args.capture_rerank_inputs.write_text(json.dumps({"fixture_sha256":fixture_hash,"requests":recorder.requests,"wire_requests":recorder.wire,
            "cache_key":"SHA256(json.dumps({query,documents},ensure_ascii=False,separators=(',',':')).encode()); preserve document order"},ensure_ascii=False,indent=2),encoding="utf-8")
    report={"evaluator":"long-source-ranking","version":VERSION,"fixture_sha256":fixture_hash,"evaluator_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "source_root":str(root),"source_sha256":{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in ("store.py","facts.py","reranker.py","query_plan.py","evidence_links.py")},
        "safety":{"synthetic_temporary_sqlite_only":True,"provider_requests":0,"credential_reads":0},
        "meaning":"Fallback/replayed source order, necessary evidence and actual ranking-copy visibility. No live reranker or answering-model accuracy claim.",
        "rerank_cache_hits":recorder.hits,"totals":{"cases":len(reports),"full_evidence_complete":sum(item["views"][-1]["complete"] for item in reports),
        "unique_ranking_requests":len(recorder.requests)},"cases":reports}
    if args.output:args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"totals":report["totals"],"fixture_sha256":fixture_hash,"cases":[{key:item[key] for key in
        ("case","tail_start_bytes","first_relevant_source_result_rank","first_complete_evidence_result_rank","views","ranking_requests")} for item in reports]},ensure_ascii=False,indent=2))
    return 0


if __name__=="__main__":raise SystemExit(main())
