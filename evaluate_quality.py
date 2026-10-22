"""Reproducible synthetic retrieval evaluation; never opens a deployment DB.

Examples (run baseline and candidate in separate processes):
    python evaluate_quality.py --source-root tmp/quality-baseline-20261022
    python evaluate_quality.py --source-root . --output tmp/quality-candidate.json

This measures evidence coverage, semantic fidelity, and ranking regressions on
invented examples. It is not an AML test set, model benchmark, or official score
estimate. Identical local vectors deliberately remove provider variability.
No existing database, real requests, audit artifacts, or credentials are read.
"""
from __future__ import annotations

import argparse
import builtins
import hashlib
import importlib
import inspect
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
from unittest.mock import patch


DIM = 4
BASE_MS = 1_735_689_600_000  # Synthetic 2025-01-01 UTC; never wall-clock time.
COVERAGE = "证据覆盖"
FIDELITY = "语义保真"
RANKING = "排序回归"

# Fixed independently of the implementation. The hash in the report identifies
# these inputs as well as the checks below (the complete evaluator file hash).
ZH_HISTORY = [
    "清晨项目记录：我们决定先完成琥珀桥的现场访谈，再讨论后续资源分配。",
    "团队成员逐个说明自己的工作安排，会议记录由轮值同事负责整理。",
    "讨论中的几个开放问题仍需进一步澄清，负责人员会在下次碰面时说明。",
    "大家核对了共享文档中的术语，避免不同小组对同一概念产生不同理解。",
    "会议结束前整理了需要联系的人员名单，并把资料送往公共工作区。",
    "设计同事补充了用户反馈中的背景情况，研发团队继续完善说明。",
    "项目团队暂停了争议问题，先把已经形成共识的工作交给负责人。",
    "中午项目记录：我们否决了银杉方案，因为现场演示无法满足访谈要求。",
    "讨论过程中没有新增结论，大家继续检查之前尚未处理的备注。",
    "轮值同事把文档分享给参与者，并请大家在方便时补充背景信息。",
    "为了便于沟通，各小组把各自使用的术语整理成统一的词汇表。",
    "同事们回看了会议中的讨论过程，把相关材料放进了项目文件夹。",
    "技术团队说明了设计约束，参与者也逐一澄清了容易混淆的表达。",
    "各方继续讨论后续沟通方式，并约定每次讨论都先同步必要背景。",
    "晚间项目记录：我们最终采用星砂流程，并安排负责人员进行交接。",
]
NUMERIC_HISTORY = [
    "实验准备记录：设备已完成预热，操作员开始依照固定顺序逐项测量。",
    "观测甲：有效载荷为 2.50 kg，等待时长为 750 ms，温度为 18.2 °C。",
    "观测乙：有效载荷为 3.25 kg，等待时长为 900 ms，温度为 19.1 °C。",
    "观测丙：有效载荷为 4.75 kg，等待时长为 650 ms，温度为 20.4 °C。",
    "观测丁：有效载荷为 6.00 kg，等待时长为 820 ms，温度为 21.3 °C。",
    "观测戊：有效载荷为 2.50 kg，等待时长为 750 ms，温度为 18.2 °C。",
    "观测己：有效载荷为 5.10 kg，等待时长为 760 ms，温度为 22.6 °C。",
    "观测庚：有效载荷为 7.40 kg，等待时长为 880 ms，温度为 23.7 °C。",
    "观测辛：有效载荷为 8.20 kg，等待时长为 920 ms，温度为 24.8 °C。",
    "观测壬：有效载荷为 9.30 kg，等待时长为 970 ms，温度为 25.9 °C。",
    "观测癸：有效载荷为 2.50 kg，等待时长为 750 ms，温度为 18.2 °C。",
    "实验收尾记录：本轮数据已保留，接下来请检查每次测量的条件是否相同。",
]
DECIMAL_RULE = (
    "If coolant temperature exceeds 37.5 C, then keep pump duty at 0.75 "
    "until the sensor resets."
)
OLD_RULE = "Always use the ORCHID envelope for archive payloads."
LOCATIONS = ["Redwood", "Harbor", "Cedar", "Quartz"]
STAGES = [
    "The first inspection recorded amber casing on the sample.",
    "The next inspection recorded blue casing on the sample.",
    "The final inspection recorded green casing on the sample.",
]


def _joined(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(_joined(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return "\n".join(_joined(v) for v in value)
    return str(value)


def _raw(results: list[dict]) -> list[dict]:
    return [r for r in results if str(r.get("id", "")).startswith("mem_")]


def _invoke_optional(function, conn, user_id: str, query: str, limit: int):
    """Adapt optional public helpers without requiring them in the baseline."""
    params = inspect.signature(function).parameters
    kwargs = {}
    if "query" in params:
        kwargs["query"] = query
    if "limit" in params:
        kwargs["limit"] = limit
    return function(conn, user_id, **kwargs)


def _repeat_count(text: str, token: str) -> int:
    """Accept verbatim repetition or an explicit adjacent frequency count."""
    import re

    count = text.count(token)
    escaped = re.escape(token)
    for pattern in (
        escaped + r"[^\n|;]{0,30}?(?:×|x|count\s*[:=]|出现|共)\s*(\d+)",
        escaped + r"[^\n|;]{0,30}?[（(]\s*(\d+)\s*(?:次|observations?|times?)",
    ):
        for match in re.finditer(pattern, text, re.I):
            count = max(count, int(match.group(1)))
    return count


def evaluate(store_module, source_root: Path, evaluator_hash: str) -> dict:
    class OfflineMemoryStore(store_module.MemoryStore):
        def __init__(self):
            # Do not instantiate an API client. Every vector below is local.
            self._dim = DIM
            self.vectors: dict[str, list[float]] = {}
            self.connections = []
            self._ensure_schema()

        def _connect(self):
            connection = super()._connect()
            self.connections.append(connection)
            return connection

        def embed(self, texts):
            return [self.vectors.get(t, [1.0, 0.0, 0.0, 0.0]) for t in texts]

        def close(self):
            for connection in self.connections:
                connection.close()

    memory = OfflineMemoryStore()
    facts = store_module.fact_layer
    checks = []

    def record(category, name, passed, observed=None):
        checks.append({"metric": category, "check": name, "passed": bool(passed),
                       "observed": observed})

    def add(user, session, texts, timestamps=None, role="user"):
        memory.add_messages(user, session, [
            {"role": role, "content": text,
             "timestamp": timestamps[i] if timestamps else BASE_MS + i * 60_000}
            for i, text in enumerate(texts)
        ])

    def digests(user, query, limit=20):
        with memory._connect() as conn:
            return _joined(facts.get_digests(conn, user, query, limit))

    try:
        # Chinese prose without digits must not disappear from the summary.
        add("zh-coverage", "one-history", ZH_HISTORY)
        summary = digests("zh-coverage", "总结项目历史，从开始到最后发生了什么？")
        for phase, marker in (("早期", "琥珀桥"), ("中期", "银杉方案"), ("晚期", "星砂流程")):
            record(COVERAGE, "中文摘要保留" + phase + "证据", marker in summary,
                   {"marker": marker, "digest_chars": len(summary)})
        results = memory.search("zh-coverage", "总结项目历史，从开始到最后发生了什么？", 12)
        result_text = _joined([r["content"] for r in results])
        record(COVERAGE, "Search摘要包含早中晚三个阶段",
               all(m in result_text for m in ("琥珀桥", "银杉方案", "星砂流程")),
               {"results": len(results), "markers_present": [m for m in
                ("琥珀桥", "银杉方案", "星砂流程") if m in result_text]})

        # Values, units, and multiplicity are necessary evidence for patterns.
        add("numeric", "observations", NUMERIC_HISTORY)
        numbers = digests("numeric", "总结所有实验观测，比较重复测量的规律")
        for token in ("2.50 kg", "750 ms", "18.2 °C", "9.30 kg"):
            record(FIDELITY, "摘要保留数值及单位 " + token, token in numbers,
                   {"token": token, "occurrences": numbers.count(token)})
        for token in ("2.50 kg", "750 ms", "18.2 °C"):
            count = _repeat_count(numbers, token)
            record(FIDELITY, "摘要保留重复观测频次 " + token, count >= 3,
                   {"expected_observations": 3, "visible_or_explicit_count": count})

        # Larger sessions must not permanently crowd out a smaller session 13.
        for i in range(1, 14):
            label = "aurora calibration" if i == 13 else f"background-{i}"
            texts = [f"The {label} notebook contains independent observation {j}."
                     for j in range(3 if i == 13 else 4)]
            add("sessions", f"session-{i:02d}", texts)
        all_digests = digests("sessions", "summarize all sessions", 20)
        record(COVERAGE, "跨会话摘要覆盖第十三会话", "aurora calibration" in all_digests,
               {"session_13_present": "aurora calibration" in all_digests})
        relevant = digests("sessions", "aurora calibration notebook", 2)
        record(COVERAGE, "相关查询能召回第十三会话摘要", "aurora calibration" in relevant,
               {"digest_chars": len(relevant)})
        query = "aurora calibration notebook"
        memory.vectors[query] = [0.0, 1.0, 0.0, 0.0]
        public = memory.search("sessions", query, 12)
        record(COVERAGE, "Search能召回第十三会话的证据",
               "aurora calibration" in _joined([r["content"] for r in public]))

        extracted = [f for f in facts.extract_facts(DECIMAL_RULE) if f.kind == "rule"]
        rules = "\n".join(f.value for f in extracted)
        for fragment in ("37.5 C", "0.75", "until the sensor resets"):
            record(FIDELITY, "条件规则完整保留 " + fragment, fragment in rules,
                   {"rules": [f.value for f in extracted]})
        prohibition = [f.value for f in facts.extract_facts("Never use Markdown.") if f.kind == "rule"]
        record(FIDELITY, "禁止规则保留否定含义",
               any("never" in v.lower() and "Markdown" in v for v in prohibition), prohibition)

        add("old-rules", "standing", [OLD_RULE])
        for i in range(32):
            add("old-rules", f"distractor-{i}", [f"Always keep color{i} labels on container{i}."])
        rule_query = "Which envelope must be used for archive payloads?"
        with memory._connect() as conn:
            helper = getattr(facts, "relevant_rules", None)
            rule_block = (_invoke_optional(helper, conn, "old-rules", rule_query, 4)
                          if helper else facts.build_profile(conn, "old-rules", rule_query))
        record(COVERAGE, "查询相关规则块召回较早规则", "ORCHID" in _joined(rule_block),
               {"helper": "relevant_rules" if helper else "build_profile fallback"})
        public = memory.search("old-rules", rule_query, 12)
        record(COVERAGE, "Search召回较早的相关规则",
               "ORCHID" in _joined([r["content"] for r in public]))

        add("changes", "relocations", [f"I live in {location}." for location in LOCATIONS])
        change_query = "How did my location change from first to last?"
        with memory._connect() as conn:
            helper = getattr(facts, "change_history", None)
            history = (_invoke_optional(helper, conn, "changes", change_query, 8)
                       if helper else facts.changed_facts(conn, "changes", 8))
        history_text = _joined(history)
        for location in LOCATIONS:
            record(COVERAGE, "变化历史保留阶段 " + location, location in history_text,
                   {"helper": "change_history" if helper else "changed_facts fallback"})
        positions = [history_text.find(location) for location in LOCATIONS]
        record(RANKING, "变化历史按最早到最新顺序呈现",
               min(positions) >= 0 and positions == sorted(positions), {"positions": positions})
        current = memory.search("changes", "Where do I currently live?", 1)
        record(RANKING, "当前状态查询返回最新位置",
               bool(current) and "Quartz" in current[0]["content"],
               {"first_result": current[0]["content"] if current else None})

        add("stages", "inspections", STAGES)
        earliest = memory.search("stages", "What casing did the earliest inspection record?", 1)
        record(RANKING, "earliest查询优先呈现最早事件",
               bool(earliest) and "amber" in earliest[0]["content"],
               {"first_result": earliest[0]["content"] if earliest else None})
        latest = memory.search("stages", "What casing did the latest inspection record?", 1)
        record(RANKING, "latest查询优先呈现最新事件",
               bool(latest) and "green" in latest[0]["content"],
               {"first_result": latest[0]["content"] if latest else None})
        relevant_older = "The turbine service interval is exactly 900 hours."
        distractor_newer = "The garden has an unrelated wooden bench."
        memory.vectors[relevant_older] = [1.0, 0.0, 0.0, 0.0]
        memory.vectors[distractor_newer] = [0.5, 0.8660254, 0.0, 0.0]
        add("semantic", "relevance", [relevant_older, distractor_newer])
        semantic = memory.search("semantic", "What is the turbine service interval?", 1)
        record(RANKING, "普通查询保留显著语义相关性优势",
               bool(semantic) and relevant_older in semantic[0]["content"])

        add("punctuation", "languages", ["I maintain C++ modules.", "I maintain C modules."])
        languages = _raw(memory.search("punctuation", "Which languages do I maintain?", 5))
        record(FIDELITY, "去重保留C++与C的语义区别", len(languages) == 2,
               {"raw_results": [r["content"] for r in languages]})
        event = "Valve inspection completed without leakage."
        add("dated-events", "morning-1", [event], [BASE_MS])
        add("dated-events", "morning-2", [event], [BASE_MS + 86_400_000])
        dated = _raw(memory.search("dated-events", "List the valve inspection events.", 5))
        record(FIDELITY, "跨日期同文事件保持独立", len(dated) == 2 and
               len({r["created_at"] for r in dated}) == 2,
               {"raw_count": len(dated), "dates": [r["created_at"] for r in dated]})
        add("exact-repeats", "one-session", [event, event], [BASE_MS, BASE_MS])
        repeated = _raw(memory.search("exact-repeats", "List valve inspections.", 5))
        # Distinct stored source rows can be repeated observations even when
        # their text and timestamp match. Add request_id handles retries.
        record(FIDELITY, "同文同时间的不同来源记录保持独立", len(repeated) == 2 and
               len({row['id'] for row in repeated}) == 2,
               {"raw_count": len(repeated)})

        add("owner-a", "shared-session", [
            "My name is Selena. I live in Bayport.",
            "Always keep synthetic tag PEBBLE-734 in the private notebook.",
            "I prefer jasmine tea for the afternoon break.",
        ])
        add("owner-b", "shared-session", [
            "My name is Marta. I live in Hillford.",
            "Always use synthetic tag TERN-482 for this notebook.",
            "I prefer mint tea for the afternoon break.",
        ])
        scoped = memory.search("owner-b", "Summarize all personal notes and rules.", 20)
        scoped_text = _joined([r["content"] for r in scoped])
        record(FIDELITY, "用户scope隔离所有结构化与原文证据",
               "TERN-482" in scoped_text and all(token not in scoped_text for token in
                ("PEBBLE-734", "Selena", "Bayport")))
        record(FIDELITY, "未知用户无跨scope回退",
               memory.search("unknown-owner", "Summarize everything.", 20) == [])

        for top_k in (1, 2, 5):
            rows = memory.search("zh-coverage", "Find relevant project evidence.", top_k)
            record(RANKING, f"top_k={top_k}结果非空且遵守上限", 0 < len(rows) <= top_k,
                   {"count": len(rows)})
    finally:
        memory.close()

    totals = {}
    for category in (COVERAGE, FIDELITY, RANKING):
        selected = [c for c in checks if c["metric"] == category]
        passed = sum(c["passed"] for c in selected)
        totals[category] = {"passed": passed, "total": len(selected),
                            "percent": round(100.0 * passed / len(selected), 2)}
    return {
        "evaluation": "independent synthetic evidence evaluation v2 (source identity)",
        "meaning": "证据覆盖/语义保真/排序回归；不是官方能力分，不推算Smoke得分",
        "source_root": str(source_root),
        "source_sha256": {name: hashlib.sha256((source_root / name).read_bytes()).hexdigest()
                          for name in ("config.py", "facts.py", "store.py", "governance.py")},
        "evaluator_sha256": evaluator_hash,
        "safety": {"temporary_sqlite_only": True, "provider_requests": 0,
                   "secret_file_reads": 0, "embedding": "controlled 4D local vectors"},
        "metrics": totals,
        "checks": checks,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--output", type=Path, help="Optional JSON report; contains synthetic results only")
    args = parser.parse_args()
    root = args.source_root.resolve()
    for name in ("config.py", "facts.py", "store.py", "governance.py"):
        if not (root / name).is_file():
            parser.error(f"source-root is missing {name}")
    evaluator_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    original_open = builtins.open
    original_connect = sqlite3.connect

    def guarded_open(file, *positional, **kwargs):
        # Config default arguments are eager: even fake env keys do not stop
        # _read_secret(...) being evaluated. Block that read before importing.
        if isinstance(file, (str, bytes, os.PathLike)):
            parts = Path(os.fsdecode(file)).parts
            if any(part.casefold() == ".secrets" for part in parts):
                raise FileNotFoundError("secret access disabled in offline evaluator")
        return original_open(file, *positional, **kwargs)

    def no_network(*args, **kwargs):
        raise RuntimeError("network access is disabled in offline evaluator")

    with tempfile.TemporaryDirectory(prefix="aml-quality-synthetic-") as temp:
        temp_root = Path(temp).resolve()

        def guarded_connect(database, *positional, **kwargs):
            if str(database) != ":memory:":
                path = Path(database).resolve()
                if not path.is_relative_to(temp_root):
                    raise RuntimeError("SQLite access outside evaluator temporary directory")
            return original_connect(database, *positional, **kwargs)

        env = {"AML_DB_PATH": str(temp_root / "evaluation.db"),
               "EMBEDDING_API_KEY": "offline-fake-not-a-credential",
               "AML_AUTH_TOKEN": "offline-fake-auth", "EMBEDDING_DIM": str(DIM),
               "EMBEDDING_MODE": "standard", "EMBEDDING_MODEL": "offline-controlled",
               "EMBEDDING_BASE_URL": "https://offline.invalid/v1",
               "AML_GOVERNANCE": "false", "AML_QUARANTINE": "false"}
        with patch.dict(os.environ, env), patch("builtins.open", guarded_open), \
             patch("sqlite3.connect", guarded_connect), \
             patch("socket.create_connection", no_network), \
             patch.object(socket.socket, "connect", no_network), \
             patch.object(socket.socket, "connect_ex", no_network):
            sys.path.insert(0, str(root))
            module = importlib.import_module("store")
            for name in ("store", "facts", "config"):
                loaded = Path(sys.modules[name].__file__).resolve()
                if loaded.parent != root:
                    raise RuntimeError(f"wrong source imported for {name}: {loaded}")
            if Path(module.CONFIG.db_path).resolve().parent != temp_root:
                raise RuntimeError("temporary database configuration was not applied")
            report = evaluate(module, root, evaluator_hash)

    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(json.dumps({"source_root": report["source_root"], "metrics": report["metrics"],
                      "failed_checks": [c["check"] for c in report["checks"] if not c["passed"]],
                      "report": str(args.output.resolve()) if args.output else None},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
