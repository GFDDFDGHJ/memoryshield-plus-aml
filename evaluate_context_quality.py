"""Fixed synthetic context-budget retrieval evaluation (not an AML score).

Run in separate processes against a frozen baseline and a candidate:
    python evaluate_context_quality.py --source-root tmp/quality-baseline-rc2-20261022 --output tmp/context-quality-baseline.json
    python evaluate_context_quality.py --source-root . --output tmp/context-quality-candidate.json

The fixtures are invented. Controlled local vectors give relevant source turns
an advantage, so this evaluates evidence selection, ordering, attribution, and
context consumption rather than embedding quality or answer generation. All
queries request Top K 100. Assertions inspect the first 8,000 or 16,000 rendered
characters actually exposed downstream, rather than searching an unbounded
result list. Reported rates are local regression checks, not official scores.

No deployment database, credentials, existing conversations, or evaluation
payloads are opened. SQLite is restricted to a new temporary directory, secret
file reads are blocked, and network connections are disabled before import.
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


DIM = 4
TOP_K = 100
BASE_MS = 1_735_689_600_000
BUDGETS = (8_000, 16_000)


def _vector(similarity: float) -> list[float]:
    return [similarity, math.sqrt(max(0, 1 - similarity ** 2)), 0., 0.]


def _render(rows: list[dict]) -> str:
    """Model a consumer reading Search rows in order, with supplied metadata.

Only metadata actually returned by Search is rendered. No source attribution
is reconstructed by consulting the evaluator's input records or database.
    """
    pieces = []
    for index, row in enumerate(rows, 1):
        metadata = {key: row[key] for key in ("role", "session_id", "seq", "status")
                    if key in row}
        label = f"[result {index}"
        if metadata:
            label += " " + json.dumps(metadata, ensure_ascii=False, sort_keys=True)
        pieces.append(label + "]\n" + str(row.get("content", "")))
    return "\n\n".join(pieces)


def _role_present(row: dict, role: str) -> bool:
    if row.get("role") == role:
        return True
    content = row.get("content", "")
    return any(marker in content for marker in
               (f"role={role}", f'role="{role}"', f'"role": "{role}"'))


def _visible_rows(rows: list[dict], budget: int) -> list[dict]:
    """Only rows that start before the consumer's character cut-off."""
    visible = []
    for row in rows:
        if len(_render(visible)) >= budget:
            break
        visible.append(row)
    return visible


def evaluate(module, source_root: Path, evaluator_hash: str) -> dict:
    class OfflineStore(module.MemoryStore):
        def __init__(self):
            self._dim = DIM
            self.vectors = {}
            self.connections = []
            self._ensure_schema()

        def _connect(self):
            conn = super()._connect()
            self.connections.append(conn)
            return conn

        def embed(self, texts):
            return [self.vectors.get(text, _vector(.05)) for text in texts]

        def close(self):
            for conn in self.connections:
                conn.close()

    memory = OfflineStore()
    checks = []
    cases = []
    serial = 0

    def add(user, session, text, *, relevance=.97, role="user"):
        nonlocal serial
        serial += 1
        memory.vectors[text] = _vector(relevance)
        memory.add_messages(user, session, [{"role": role, "content": text,
                                           "timestamp": BASE_MS + serial * 60_000}])

    def distract(user, count, *, rules=False, long=False):
        for i in range(count):
            if rules:
                text = f"Always put violet{i} tape on garden container{i} before watering."
            else:
                text = f"Unrelated garden log {i:03d}: the ornamental fence was inspected."
            if long:
                text += (" The landscape notes describe gravel, hedges, and paths in detail." * 13)
            add(user, f"background-{i:03d}", text, relevance=.12)

    def search(case, user, query):
        memory.vectors[query] = [1., 0., 0., 0.]
        rows = memory.search(user, query, TOP_K)
        text = _render(rows)
        case_data = {"case": case, "query": query, "top_k": TOP_K,
                     "result_count": len(rows), "rendered_chars": len(text),
                     "prefix_sha256": {str(b): hashlib.sha256(text[:b].encode()).hexdigest()
                                       for b in BUDGETS}}
        cases.append(case_data)
        return rows, text

    def check(case, name, passed, *, budget=8_000, observed=None):
        checks.append({"case": case, "check": name, "budget_chars": budget,
                       "passed": bool(passed), "observed": observed})

    def markers(case, text, expected, *, budget=8_000):
        prefix = text[:budget]
        present = [token for token in expected if token in prefix]
        check(case, "required source evidence remains inside prefix", len(present) == len(expected),
              budget=budget, observed={"expected": len(expected), "present": len(present),
                                       "missing": [x for x in expected if x not in prefix]})

    try:
        case = "A_exact_fact_amid_many_rules"
        distract(case, 137, rules=True)
        add(case, "project", "The Larkspur release owner is Nerea; the release identifier is LS-582.")
        _, text = search(case, case, "Who owns the Larkspur release LS-582?")
        markers(case, text, ["Nerea", "LS-582"])
        check(case, "irrelevant garden instructions are absent from opening context",
              "garden container" not in text[:1_200], budget=1_200)

        case = "A_user_assistant_attribution"
        add(case, "dialogue", "I work at Bracken Analytics.", role="user", relevance=.99)
        add(case, "dialogue", "I work at Dune Robotics.", role="assistant", relevance=.82)
        distract(case, 108)
        rows, text = search(case, case, "Which employer did I say I work at?")
        markers(case, text, ["Bracken Analytics"])
        visible = _visible_rows(rows, 8_000)
        first_user = next((row for row in visible if "Bracken Analytics" in row.get("content", "")), None)
        check(case, "user statement has user source attribution",
              bool(first_user) and _role_present(first_user, "user"))
        wrong = [row for row in visible if "Dune Robotics" in row.get("content", "")
                 and str(row.get("id", "")).startswith(("fact_", "change_", "profile"))
                 and not _role_present(row, "assistant")]
        check(case, "assistant first-person statement is not promoted as user identity",
              not wrong, observed={"unattributed_derived_assistant_claims": len(wrong)})

        case = "A_named_speaker_and_quote"
        add(case, "interview", 'Ravi said, "I maintain the cobalt adapter." I maintain the ochre adapter.', relevance=.99)
        add(case, "interview", "The interviewer thanked Ravi and closed the equipment interview.", relevance=.78)
        distract(case, 110, rules=True)
        _, text = search(case, case, "Which adapter does Ravi maintain, and which one do I maintain?")
        markers(case, text, ['Ravi said, "I maintain the cobalt adapter."', "I maintain the ochre adapter."])

        case = "B_multi_session_path_control"
        add(case, "people", "Amira leads the Solstice integration program.", relevance=.99)
        add(case, "programs", "The Solstice integration program delivers to the Kestrel platform.", relevance=.98)
        add(case, "platforms", "The Kestrel platform is operated by the Research Operations division.", relevance=.97)
        distract(case, 114)
        _, text = search(case, case, "Trace Amira's program to its platform and operating division.")
        markers(case, text, ["Amira leads", "Solstice integration program delivers", "Research Operations division"])

        case = "E_preferences_and_constraints_control"
        add(case, "preferences", "I prefer jasmine tea, and I avoid caffeine after 16:00.", relevance=.99)
        add(case, "preferences", "For my afternoon drinks, avoid peanuts because of my allergy.", relevance=.98)
        distract(case, 111, rules=True)
        _, text = search(case, case, "Recommend an afternoon drink consistent with my tea preferences and food constraints.")
        markers(case, text, ["jasmine", "avoid caffeine after 16:00", "avoid peanuts"])

        case = "D_current_state_and_speaker_conflict"
        add(case, "old-home", "I live in Reedport.", relevance=.98)
        add(case, "dialogue", "I live in Cormorant City.", role="assistant", relevance=.85)
        add(case, "new-home", "I moved to Marlin Bay. Reedport is my former home.", relevance=.97)
        distract(case, 104)
        _, text = search(case, case, "Where do I currently live?")
        markers(case, text, ["Marlin Bay"], budget=1_200)
        first = text[:1_200].casefold()
        check(case, "current prefix does not assert assistant city as user state",
              "cormorant city" not in first or "assistant" in first, budget=1_200)

        case = "G_conditional_rule_preserves_exception"
        add(case, "operations", "If humidity exceeds 72.5%, then keep fan duty at 0.35 until humidity drops below 68%. Never enable wet mode during this interval.", relevance=.99)
        distract(case, 139, rules=True)
        _, text = search(case, case, "What fan duty and wet-mode constraints apply when humidity reaches 73%?")
        markers(case, text, ["72.5%", "0.35", "below 68%", "Never enable wet mode"])

        case = "G_execution_request_recalls_standing_rules"
        add(case, "writing-guide", "For incident briefs, write exactly three sentences. Start each brief with CASE:. Never include the customer's street address in an incident brief.", relevance=.99)
        distract(case, 141, rules=True)
        _, text = search(case, case, "Draft an incident brief about the hydraulic pump failure.")
        markers(case, text, ["exactly three sentences", "CASE:", "Never include the customer's street address"])

        case = "A_long_record_late_fact"
        filler = "The shared orientation handbook discusses stationery, room layouts, and ordinary arrival arrangements. "
        long_record = ("Orientation material for the Tern calibration team. " + filler * 210
                       + "For the Tern calibration, the accountable owner is Elodie and the approval code is TRN-864. "
                       + filler * 65)
        add(case, "manual", long_record, relevance=.995)
        distract(case, 105)
        _, text = search(case, case, "Who is the accountable owner and what is the approval code for Tern calibration?")
        markers(case, text, ["Elodie", "TRN-864"])

        case = "G_long_record_multiple_evidence_windows"
        filler = "The maintenance appendix records standard storage racks, walkway markings, and ordinary cleaning tasks. "
        long_record = ("This appendix concerns Citrine pump maintenance. " + filler * 87
                       + "Citrine pressure limit: 238.4 kPa under dry operating conditions. " + filler * 87
                       + "Citrine exception: when condensation is present, reduce the limit to 192.6 kPa. " + filler * 87
                       + "Citrine validation: the dry-versus-condensation comparison was verified on 2025-02-19.")
        add(case, "manual", long_record, relevance=.995)
        distract(case, 105)
        _, text = search(case, case, "Summarize Citrine pump pressure limits, the condensation exception, and the validation date.")
        markers(case, text, ["238.4 kPa", "192.6 kPa", "2025-02-19"])

        case = "F_narrow_summary_excludes_other_private_topics"
        add(case, "plover-first", "Plover deployment began with the granite prototype on 2025-03-02.", relevance=.99)
        add(case, "plover-middle", "Plover deployment rejected the copper prototype after the load test.", relevance=.98)
        add(case, "plover-final", "Plover deployment finally adopted the basalt prototype on 2025-03-17.", relevance=.97)
        add(case, "private-home", "My private street address is 11 Juniper Lane, synthetic test town.", relevance=.02)
        add(case, "private-key", "My synthetic personal cabinet access code is CAB-9081-PRIVATE.", relevance=.01)
        distract(case, 110)
        _, text = search(case, case, "Summarize Plover deployment milestones and decisions.")
        markers(case, text, ["granite prototype", "copper prototype", "basalt prototype"])
        check(case, "narrow project summary excludes unrelated private data",
              all(token not in text[:16_000] for token in ("11 Juniper Lane", "CAB-9081-PRIVATE")),
              budget=16_000)

        case = "G_48_numeric_observations"
        expected = []
        for i in range(1, 49):
            token = f"run={i:02d}; input={i * 3 + 2}; output={i * 7 + 3}"
            expected.append(token)
            add(case, "calibration-trials", token + f"; pressure={120 + i / 10:.1f} kPa; latency={410 + i * 3} ms; mode=dry. This is a distinct observed calibration trial.", relevance=.99 - i / 10_000)
        distract(case, 105)
        _, text = search(case, case, "Summarize all calibration trials, retaining the input-output observations and operating conditions.")
        markers(case, text, expected, budget=16_000)
        markers(case, text, ["pressure=120.1 kPa", "pressure=124.8 kPa", "mode=dry"], budget=16_000)
        check(case, "early context is dominated by relevant calibration evidence",
              "garden log" not in text[:8_000])

        case = "F_19_sessions_global_coverage"
        expected = []
        for i in range(1, 20):
            milestone = f"milestone MAP-{i:02d}-VERIFIED"
            expected.append(milestone)
            add(case, f"meeting-{i:02d}", f"Mapping program meeting {i:02d}: the team opened the status review.", relevance=.93)
            add(case, f"meeting-{i:02d}", f"Mapping program meeting {i:02d} confirmed {milestone}.", relevance=.99 - i / 10_000)
            add(case, f"meeting-{i:02d}", f"Mapping program meeting {i:02d}: the team closed the status review.", relevance=.92)
        distract(case, 104)
        _, text = search(case, case, "Summarize all Mapping program meetings and every verified milestone across sessions.")
        markers(case, text, expected, budget=16_000)

        case = "C_event_order_without_old_unrelated_prefix"
        distract(case, 78, long=True)
        for phrase in ("first, the Opal rotor passed inspection", "next, the Opal rotor received balancing", "finally, the Opal rotor entered endurance testing"):
            add(case, "rotor-work", phrase + ".", relevance=.99)
        _, text = search(case, case, "What is the event sequence for Opal rotor inspection, balancing, and endurance testing?")
        expected = ["passed inspection", "received balancing", "entered endurance testing"]
        markers(case, text, expected)
        positions = [text[:8_000].find(token) for token in expected]
        check(case, "relevant stages appear in source order within prefix",
              min(positions) >= 0 and positions == sorted(positions), observed={"positions": positions})

        case = "I_rule_correction_changes_applicable_instruction"
        add(case, "old-policy", "Always use copper sleeves for resin batches.", relevance=.985)
        add(case, "new-policy", "Correction for resin batches: use ceramic sleeves from now on. Copper sleeves are obsolete and must no longer be used.", relevance=.98)
        distract(case, 111, rules=True)
        _, text = search(case, case, "What sleeves should I use for resin batches today?")
        markers(case, text, ["ceramic sleeves", "Copper sleeves are obsolete"], budget=2_000)
        first_copper = text[:2_000].find("copper sleeves")
        first_ceramic = text[:2_000].find("ceramic sleeves")
        check(case, "old instruction does not precede correction as an unqualified command",
              first_ceramic >= 0 and (first_copper < 0 or first_ceramic < first_copper
              or "obsolete" in text[:first_copper + 90].casefold()), budget=2_000)

        case = "I_cross_session_knowledge_transfer"
        add(case, "field-lessons", "On modular circuits, fault R7 means a worn seal; the successful repair is replacing the seal before recalibration.", relevance=.99)
        add(case, "new-device", "The new modular circuit HX-6 now reports fault R7 after an otherwise normal boot.", relevance=.98)
        distract(case, 115)
        _, text = search(case, case, "What prior fault R7 experience can guide the repair of the new modular circuit HX-6?")
        markers(case, text, ["fault R7 means a worn seal", "replacing the seal before recalibration", "HX-6 now reports fault R7"])

        case = "scope_isolation_with_same_session_names"
        add(case + "-other", "shared-notebook", "Synthetic secret of another user: OTHER-USER-2718.", relevance=1.)
        add(case, "shared-notebook", "My project tag is SWIFT-6243.", relevance=.99)
        _, text = search(case, case, "Summarize my project tag and notebook.")
        markers(case, text, ["SWIFT-6243"])
        check(case, "other user evidence is absent", "OTHER-USER-2718" not in text[:16_000], budget=16_000)
    finally:
        memory.close()

    by_case = []
    for case in cases:
        selected = [item for item in checks if item["case"] == case["case"]]
        by_case.append({**case, "passed": sum(item["passed"] for item in selected),
                        "total": len(selected), "all_passed": all(item["passed"] for item in selected)})
    return {"evaluation": "synthetic TopK100 context-budget evidence evaluation v1",
            "meaning": "Fixed invented regression cases; not an official score or prediction. Controlled vectors do not evaluate embedding semantics.",
            "source_root": str(source_root), "evaluator_sha256": evaluator_hash,
            "source_sha256": {name: hashlib.sha256((source_root / name).read_bytes()).hexdigest()
                              for name in ("config.py", "facts.py", "store.py", "governance.py")},
            "safety": {"temporary_sqlite_only": True, "provider_requests": 0,
                       "secret_file_reads": 0, "embedding": "controlled 4D local vectors"},
            "totals": {"checks_passed": sum(item["passed"] for item in checks), "checks": len(checks),
                       "cases_passed": sum(item["all_passed"] for item in by_case), "cases": len(by_case)},
            "cases": by_case, "checks": checks}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.source_root.resolve()
    for name in ("config.py", "facts.py", "store.py", "governance.py"):
        if not (root / name).is_file():
            parser.error(f"source-root is missing {name}")
    evaluator_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    original_open = builtins.open
    original_connect = sqlite3.connect

    def guarded_open(file, *positional, **kwargs):
        if isinstance(file, (str, bytes, os.PathLike)):
            if any(part.casefold() == ".secrets" for part in Path(os.fsdecode(file)).parts):
                raise FileNotFoundError("secret access disabled in offline evaluator")
        return original_open(file, *positional, **kwargs)

    def no_network(*args, **kwargs):
        raise RuntimeError("network access is disabled in offline evaluator")

    with tempfile.TemporaryDirectory(prefix="aml-context-synthetic-") as temp:
        temp_root = Path(temp).resolve()

        def guarded_connect(database, *positional, **kwargs):
            if str(database) != ":memory:":
                if not Path(database).resolve().is_relative_to(temp_root):
                    raise RuntimeError("SQLite access outside evaluator temporary directory")
            return original_connect(database, *positional, **kwargs)

        env = {"AML_DB_PATH": str(temp_root / "evaluation.db"), "EMBEDDING_DIM": str(DIM),
               "EMBEDDING_API_KEY": "offline-fake-not-a-credential", "AML_AUTH_TOKEN": "offline-fake-auth",
               "EMBEDDING_MODE": "standard", "EMBEDDING_MODEL": "offline-controlled",
               "EMBEDDING_BASE_URL": "https://offline.invalid/v1", "AML_GOVERNANCE": "false",
               "AML_QUARANTINE": "false"}
        with patch.dict(os.environ, env), patch("builtins.open", guarded_open), \
             patch("sqlite3.connect", guarded_connect), patch("socket.create_connection", no_network), \
             patch.object(socket.socket, "connect", no_network), patch.object(socket.socket, "connect_ex", no_network):
            sys.path.insert(0, str(root))
            module = importlib.import_module("store")
            for name in ("store", "facts", "config"):
                loaded = Path(sys.modules[name].__file__).resolve()
                if loaded.parent != root:
                    raise RuntimeError(f"wrong source imported for {name}: {loaded}")
            if Path(module.CONFIG.db_path).resolve().parent != temp_root:
                raise RuntimeError("temporary database configuration was not applied")
            report = evaluate(module, root, evaluator_hash)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"source_root": str(root), "totals": report["totals"],
                      "failed": [item for item in report["checks"] if not item["passed"]],
                      "report": str(args.output.resolve()) if args.output else None}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
