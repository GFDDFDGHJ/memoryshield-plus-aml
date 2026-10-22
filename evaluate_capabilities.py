"""Broad invented evidence-capability checks, with fixed seeds and prefix budgets.

    python evaluate_capabilities.py --source-root . --output tmp/capabilities-rc3-baseline.json

This is a retrieval evidence evaluation, not an answer-generation benchmark or
an official AML score estimate. Queries are paraphrased, and controlled local
vectors independently give relevant turns an advantage. Thus failures isolate
selection, ordering, provenance, privacy, or context consumption; passes say
nothing about an embedding model's semantic quality. No official examples,
deployment databases, credentials, or existing conversations are read.

Every search requests Top K 100. Coverage is checked inside 8k/16k character
prefixes of an explicitly documented ordered rendering. Complete numeric rows
may be reconstructed from the returned literal template and declared columns;
the evaluator never fills omitted rows or consults the input to decode them.
Separate source IDs, rather than repeated surface strings, count observations.
SQLite is restricted to a fresh temporary directory, and network and secret
file access are blocked before the implementation is imported.
"""
from __future__ import annotations

import argparse
import builtins
from collections import defaultdict
from datetime import datetime
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


SEED = 20261022
EVALUATOR_VERSION = "1.1"
DIM = 4
TOP_K = 100
BUDGETS = (8_000, 16_000)
BASE_MS = 1_735_689_600_000
SOURCE_FILES = ("config.py", "facts.py", "store.py", "governance.py")


def fixtures(seed: int = SEED) -> list[dict]:
    """Invented cases fixed before observing the implementation's results."""
    rng = random.Random(seed)
    cases = []
    syllables = ["Aster", "Brindle", "Celandine", "Dovetail", "Everton", "Fennel", "Gannet", "Hawthorn"]

    def name():
        return rng.choice(syllables) + "-" + str(rng.randrange(210, 980))

    def message(text, session="notes", role="user", relevance=.98, timestamp=None):
        return {"text": text, "session": session, "role": role,
                "relevance": relevance, "timestamp": timestamp}

    def noise(count=14, *, long=False, topical="garden"):
        items = []
        for index in range(count):
            text = f"The unrelated {topical} notebook entry {index:03d} describes gravel paths and ordinary storage boxes."
            if long:
                text += " The notes concern landscape borders, wooden benches, shrubs, and gravel deliveries." * 14
            items.append(message(text, "background", relevance=.18 + rng.random() * .16))
        return items

    def case(key, category, query, messages, required, *, forbidden=(), role_checks=(),
             first_within=None, precedence=(), source_count=None, mode="batch", append=(),
             before_required=(), compare_batch=False, foreign=()):
        cases.append({"case": key, "category": category, "query": query,
                      "messages": messages, "required": list(required), "forbidden": list(forbidden),
                      "role_checks": list(role_checks), "first_within": first_within,
                      "precedence": list(precedence), "source_count": source_count,
                      "mode": mode, "append": list(append), "before_required": list(before_required),
                      "compare_batch": compare_batch, "foreign": list(foreign)})

    project, owner = name(), rng.choice(["Mirela", "Nerea", "Elodie", "Tamsin"])
    case("A01_paraphrased_owner", "A", rng.choice([
        "Which colleague is accountable for getting this delivery out?",
        "Whose responsibility is the upcoming rollout?",
    ]), noise(22) + [message(f"The rollout owner for {project} is {owner}; release tag PX-683.", "release")],
         [owner, "PX-683"], first_within=1_200)
    case("A02_first_person_source", "A", "Which company did I tell you pays my salary?", [
        message("I work at Bracken Analytics.", "employment", relevance=.995),
        message("I work at Dune Robotics.", "employment", "assistant", relevance=.86),
    ] + noise(), ["Bracken Analytics"], role_checks=[("Bracken Analytics", "user"), ("Dune Robotics", "assistant")])
    case("A03_named_quote", "A", "Who maintains each of the two adapters mentioned in the interview?", [
        message('Ravi said, "I maintain the cobalt adapter." I maintain the ochre adapter.', "interview"),
    ] + noise(), ['Ravi said, "I maintain the cobalt adapter."', "I maintain the ochre adapter."])
    case("A04_negative_attribute", "A", "Tell me the approved casing material and the rejected alternative.", [
        message("The instrument casing is ceramic, not amber; amber was rejected for brittle fracture.", "design"),
    ] + noise(), ["ceramic, not amber", "rejected for brittle fracture"])
    case("A05_punctuation_distinctions", "A", "Which languages and signed offsets were recorded?", [
        message("I maintain C++ modules and a C module.", "skills"),
        message("Reference offsets are +5.25 ms and -5.25 ms; they are separate measurements.", "timing"),
    ] + noise(), ["C++", "a C module", "+5.25 ms", "-5.25 ms"])

    case("B01_two_session_join", "B", "Which platform supports the initiative that Amira runs?", [
        message("Amira leads the Solstice integration program.", "people"),
        message("The Solstice integration program runs on the Kestrel platform.", "program"),
    ] + noise(24), ["Amira leads", "program runs on the Kestrel"])
    case("B02_narrow_three_hop_summary", "B", "Recap the operating division behind the platform used by the program Amira leads.", [
        message("Amira leads the Solstice integration program.", "people"),
        message("Solstice runs on the Kestrel platform.", "program", relevance=.94),
        message("The Kestrel platform is operated by Research Operations division.", "platform"),
    ] + noise(24), ["Amira leads", "Solstice runs on", "Research Operations division"])
    case("B03_causal_chain", "B", "Explain how the shortage ultimately affected the workshop date.", [
        message("The zircon bearing supplier missed its shipment.", "supply"),
        message("Because the zircon bearings did not arrive, the rotor qualification was deferred.", "qualification"),
        message("Rotor qualification is required before the workshop, so the workshop moved to 2025-06-18.", "workshop"),
    ] + noise(18), ["supplier missed", "qualification was deferred", "required before the workshop", "2025-06-18"])

    case("C01_relative_day_anchor", "C", "What calendar evidence pins down the planned handover day?", [
        message("Today is 2025-04-03, a Thursday.", "handover", relevance=.86),
        message("We will hand over the optical bench next Tuesday, not this Friday.", "handover"),
    ] + noise(25, long=True), ["2025-04-03", "next Tuesday, not this Friday"])
    case("C02_relative_weeks", "C", "How can I work out the date of the follow-up review?", [
        message("The initial clinical-device review took place on 2025-02-12.", "review", relevance=.91),
        message("The follow-up review is exactly three weeks after the initial review.", "review"),
    ] + noise(), ["2025-02-12", "exactly three weeks after"])
    case("C03_timezone_midnight", "C", "Can you recover the local timing needed to compare the two observations?", [
        message("The first sensor observation was 2025-07-02 23:40 at UTC-04:00.", "west"),
        message("The second sensor observation was 2025-07-03 05:10 at UTC+02:00.", "east"),
    ] + noise(), ["2025-07-02 23:40", "UTC-04:00", "2025-07-03 05:10", "UTC+02:00"])
    case("C04_backfilled_event_time", "C", "When did the original inspection actually occur?", [
        message("This is a backfilled note: the original valve inspection happened on 2022-11-08.", "archive", timestamp=BASE_MS),
    ] + noise(20), ["backfilled note", "happened on 2022-11-08"], first_within=1_500)
    dated = [message("The cobalt trial began on 2025-08-04.", "dated", timestamp=BASE_MS + 900_000),
             message("The cobalt trial ended on 2025-08-09.", "dated", timestamp=BASE_MS + 100_000),
             message("The cobalt trial paused on 2025-08-06.", "dated", timestamp=BASE_MS + 500_000)]
    rng.shuffle(dated)
    case("C05_event_dates_vs_arrival", "C", "Reconstruct the cobalt trial timeline using the events' stated dates.", dated + noise(),
         ["began on 2025-08-04", "paused on 2025-08-06", "ended on 2025-08-09"])
    case("C06_implicit_source_sequence", "C", "In which order were the three named steps performed?", noise(35, long=True) + [
        message("Step ORIOLE: the team cleaned the inlet.", "procedure"),
        message("Step FINCH: the team balanced the rotor.", "procedure"),
        message("Step TERN: the team sealed the housing.", "procedure"),
    ], ["Step ORIOLE", "Step FINCH", "Step TERN"], precedence=[("Step ORIOLE", "Step FINCH"), ("Step FINCH", "Step TERN")])
    case("C07_interval_cross_month", "C", "Recover the endpoints and units needed to calculate the maintenance gap.", [
        message("The preventive service was on 2024-02-27 at 18:00 UTC.", "service"),
        message("The next preventive service was on 2024-03-02 at 06:00 UTC; compare elapsed hours.", "service"),
    ] + noise(), ["2024-02-27 at 18:00 UTC", "2024-03-02 at 06:00 UTC", "elapsed hours"])

    case("D01_current_location", "D", rng.choice(["Where am I living now?", "Which city is my current home?"]), [
        message("I live in Juniper City.", "old-home", relevance=.98),
        message("I moved to Rowan City.", "new-home", relevance=.975),
    ] + noise(), ["Rowan City"], precedence=[("Rowan City", "Juniper City")], first_within=1_500)
    case("D02_retraction_without_new_value", "D", "Is the former employer still where I work?", [
        message("I work at Azure Foundry.", "employment", relevance=.97),
        message("I no longer work at Azure Foundry. I have not named a new employer.", "employment", relevance=.99),
    ] + noise(), ["no longer work at Azure Foundry", "not named a new employer"])
    case("D03_disputed_measurement", "D", "What do the two named witnesses disagree about, and who said each value?", [
        message("Technician Lio reported a maximum pressure of 220.5 kPa.", "technician"),
        message("Supervisor Anya disputed Lio's value and reported a maximum pressure of 198.5 kPa.", "supervisor"),
    ] + noise(), ["Technician Lio", "220.5 kPa", "Supervisor Anya disputed", "198.5 kPa"])
    case("D04_explicit_rule_correction", "D", "Which sleeve material should I use for resin batches today?", [
        message("Always use copper sleeves for resin batches.", "old-policy", relevance=.99),
        message("Correction for resin batches: use ceramic sleeves from now on. Copper sleeves are obsolete and must no longer be used.", "new-policy", relevance=.98),
    ] + noise(20), ["ceramic sleeves", "Copper sleeves are obsolete"], first_within=1_500,
         precedence=[("ceramic sleeves", "Always use copper sleeves")])
    case("D05_state_trajectory", "D", "Trace the first home, intermediate moves, and present home.", [
        message("I live in Laurel Town.", "moves"), message("I moved to Willow Town.", "moves"),
        message("I moved to Cedar Town.", "moves"), message("I moved to Maple Town.", "moves"),
    ] + noise(18), ["Laurel Town", "Willow Town", "Cedar Town", "Maple Town"])

    topics = ["work", "travel", "health", "study", "hobbies", "family", "housing", "volunteering"]
    global_notes = []
    global_markers = []
    for index in range(112):
        topic = topics[index % len(topics)]
        if index in (0, 17, 32, 51, 68, 77, 94, 111):
            marker = f"GLOBAL-{index:03d}-" + name()
            global_markers.append(marker)
            text = f"My {topic} discussion ended with the decision {marker}."
            relevance = .995
        else:
            text = f"My {topic} discussion recorded routine background item {index:03d}, pending further information."
            relevance = .94
        global_notes.append(message(text, f"session-{index // 4:02d}", relevance=relevance))
    case("F01_cross_topic_global_history", "F", "Summarize everything we discussed so far, including decisions from every part of my life.",
         global_notes, global_markers)
    case("F02_global_intent_paraphrase", "F", "Give me a consolidated picture of my work, travel, health, study, hobbies, family, housing, and volunteering discussions.",
         global_notes, global_markers)
    project = name()
    milestones = [message(f"{project} deployment began with the granite prototype.", "first"),
                  message(f"{project} deployment rejected the copper prototype after load testing.", "middle"),
                  message(f"{project} deployment adopted the basalt prototype for production.", "last")]
    private = [message("My private home address is 14 Synthesist Lane.", "private", relevance=.08),
               message("My private cabinet code is CAB-6721-SECRET.", "private", relevance=.07)]
    case("F03_narrow_summary_other_sessions", "F", f"Summarize only the {project} deployment choices.", milestones + private + noise(),
         ["granite prototype", "rejected the copper prototype", "basalt prototype"], forbidden=["14 Synthesist Lane", "CAB-6721-SECRET"])
    same_session = [{**item, "session": "mixed-dialogue"} for item in milestones + private]
    case("F04_narrow_summary_same_session", "F", f"Recap only the {project} deployment choices.", same_session + noise(),
         ["granite prototype", "rejected the copper prototype", "basalt prototype"], forbidden=["14 Synthesist Lane", "CAB-6721-SECRET"])
    meeting_messages, meeting_markers = [], []
    for index in range(23):
        marker = f"MEETING-{index:02d}-" + name()
        meeting_markers.append(marker)
        meeting_messages.append(message(f"The mapping program meeting confirmed {marker}.", f"meeting-{index:02d}"))
    case("F05_many_short_sessions", "F", "Give an overview across all sessions of the mapping program decisions.",
         meeting_messages + noise(25), meeting_markers)
    chinese = []
    phase_markers = {0: "先决定采用松涛路线", 19: "随后否决了银杉路线", 36: "最终选择星砂路线"}
    for index in range(37):
        text = phase_markers.get(index, "团队继续核对访谈背景，整理尚未解决的开放问题。")
        chinese.append(message(text, "中文项目", relevance=.99 if index in phase_markers else .88))
    case("F06_chinese_long_history", "F", "请回顾这个项目从早期到最后的关键决定。", chinese + noise(), list(phase_markers.values()))

    tag = name()
    case("G01_arbitrary_condition_and_exception", "G", "What action is permitted in the wet test, including the emergency exception?", [
        message(f"If the specimen tag is {tag}, keep the chamber at 18.75 C during a wet test. Exception: if smoke is detected, stop heating immediately.", "lab"),
    ] + noise(20), [tag, "18.75 C", "during a wet test", "if smoke is detected", "stop heating immediately"])
    case("G02_multiple_exception_chain", "G", "Recover the pressure policy with all of its qualifications.", [
        message("Pressure limit is 238.4 kPa. However, this limit applies only under dry conditions. Otherwise, use 192.6 kPa.", "policy"),
    ] + noise(), ["238.4 kPa", "only under dry conditions", "Otherwise, use 192.6 kPa"])
    case("G03_french_rule_and_negation", "G", "Which valve setting is allowed when condensation is present?", [
        message("Si de la condensation est présente, n'ouvrez jamais la vanne rouge. Utilisez la vanne bleue à 0,75 bar, sauf pendant l'étalonnage.", "french-policy"),
    ] + noise(), ["n'ouvrez jamais la vanne rouge", "0,75 bar", "sauf pendant l'étalonnage"])
    case("G04_chinese_rule_decimals", "G", "湿度很高时能否开湿模式，风扇设置有哪些限制？", [
        message("如果湿度超过72.5%，风扇占空比保持0.35，直到湿度降到68%以下。不要在这段期间启用湿模式。", "中文规则"),
    ] + noise(), ["超过72.5%", "保持0.35", "68%以下", "不要在这段期间启用湿模式"])
    output_rules = ["Always use metric units.", "Always cite sources.", "Always include uncertainty estimates.",
                    "Always protect private addresses.", "Always mark assumptions.", "Always retain relevant dates."]
    case("G05_unspoken_execution_policies", "G", "Draft the pump incident brief.",
         [message(text, "standing-output") for text in output_rules] + [
             message("Always use the blue archive folder for invoices.", "billing", relevance=.12),
         ] + noise(22), output_rules, forbidden=["blue archive folder"])
    case("G06_workflow_rule_scope", "G", "Which packaging constraints govern this Helios archive export?", [
        message("For Helios archive exports, use the ORCHID envelope and omit customer addresses.", "helios"),
        message("For Selene shipping labels, use the MARIGOLD envelope and include a warehouse address.", "selene", relevance=.3),
    ] + noise(), ["ORCHID envelope", "omit customer addresses"], forbidden=["MARIGOLD envelope"])

    table_rows = [message("Nozzle experiment table: column x is load in kg; column y is force in N; temperature is held at 12.5 C.", "table", relevance=.73)]
    table_markers = ["load in kg", "force in N", "held at 12.5 C"]
    for index in range(47):
        token = f"row={index:02d}; x={index + 2}.50; y={index * 3 + 7}.25"
        table_rows.append(message(token + "; dry mode.", "table", relevance=.99 - index * .0001))
        table_markers.append(token)
    case("G07_table_headers_and_rows", "G", "Infer the load-to-force relationship from every nozzle experiment, keeping the column meanings and conditions.",
         table_rows + noise(20), table_markers, source_count={"session": "table", "minimum": 48})
    repeated = [message("Pump pressure was 19.25 kPa after 0.75 seconds in dry mode.", "repeated", relevance=.99) for _ in range(73)]
    case("G08_repeated_observation_multiplicity", "G", "Calculate the empirical distribution across every pump observation.",
         repeated + noise(20), ["19.25 kPa", "0.75 seconds", "dry mode"], source_count={"session": "repeated", "minimum": 73})
    empirical, empirical_markers = [], []
    for index in range(65):
        out = -17.75 if index == 29 else 2.25 * index + 3.5
        token = f"sample={index:02d}; input={index / 4:.2f}; output={out:.2f} N"
        empirical.append(message(token + "; ambient=21.50 C; no filtering was applied.", "empirical"))
        empirical_markers.append(token)
    case("G09_empirical_outlier_and_full_history", "G", "Estimate the empirical response while retaining every sample, including any anomalous result.",
         empirical + noise(18), empirical_markers, source_count={"session": "empirical", "minimum": 65})
    branch = []
    branch_markers = []
    for index in range(34):
        temp = "cold" if index % 2 else "warm"
        token = f"trial={index:02d}; chamber={temp}; input={index + 4}.5 kg; output={index * 2 + 9}.25 N"
        branch.append(message(token + "; do not pool cold and warm trials.", "branches"))
        branch_markers.append(token)
    case("G10_data_conditions_not_pooled", "G", "Compare the two experimental regimes without mixing their observations.",
         branch + noise(), branch_markers + ["do not pool cold and warm trials"], source_count={"session": "branches", "minimum": 34})

    online = [message("I live in Indigo City.", "move"), message("I moved to Garnet City.", "move"),
              message("I no longer live in Garnet City.", "move"), message("I moved to Topaz City.", "move"),
              message("Always use metric units.", "output"), message("Trial one measured 6.25 kg and 8.50 N.", "experiment"),
              message("Trial two measured 7.75 kg and 9.25 N.", "experiment")]
    case("I01_online_vs_batch_equivalence", "I", "Summarize all history, including the location changes, output instruction, and experimental observations.",
         online, ["Indigo City", "Garnet City", "Topaz City", "metric units", "6.25 kg", "9.25 N"],
         mode="online", compare_batch=True)
    case("I02_append_changes_current_state", "I", "Where am I living now?", [
        message("I live in Plum City.", "first"),
    ], ["Apricot City"], append=[message("I moved to Apricot City.", "follow-up")], before_required=["Plum City"],
         precedence=[("Apricot City", "Plum City")], first_within=1_500)
    case("I03_cross_session_transfer", "I", "Which previous repair lesson can help with fault R7 on the new HX-6 unit?", [
        message("On modular circuits, fault R7 means a worn seal; the successful repair is replacing the seal before recalibration.", "old-device"),
        message("The new modular circuit HX-6 now reports fault R7 after an otherwise normal boot.", "new-device"),
    ] + noise(25), ["fault R7 means a worn seal", "replacing the seal before recalibration", "HX-6 now reports fault R7"])
    case("I04_cross_language_transfer", "I", "新设备出现R7故障，之前的经验说明应先做什么，之后再做什么？", [
        message("For fault R7, replace the worn seal first, then recalibrate the modular circuit. Never recalibrate before the seal replacement.", "english-lesson"),
        message("新设备报告R7故障，启动过程正常。", "new-device"),
    ] + noise(), ["replace the worn seal first", "then recalibrate", "Never recalibrate before", "新设备报告R7故障"])

    case("E01_preference_and_health_control", "E", "Choose an afternoon drink that fits what I like and my health constraints.", [
        message("I prefer jasmine tea, and I avoid caffeine after 16:00.", "preference"),
        message("I am allergic to peanuts. Avoid drinks made with peanut milk.", "health"),
    ] + noise(), ["jasmine tea", "avoid caffeine after 16:00", "allergic to peanuts", "Avoid drinks made with peanut milk"])
    case("E02_counter_stereotype", "E", "What meal would match this person's own stated preference?", [
        message("I am a mountain guide, but I prefer mild lentil soup and dislike spicy food.", "preference"),
        message("Some people assume mountain guides want spicy stew; that stereotype does not describe my preference.", "clarification"),
    ] + noise(), ["prefer mild lentil soup", "dislike spicy food", "does not describe my preference"])
    case("E03_attributed_care_dialogue", "E", "Recap the health background mentioned by both speakers before suggesting care.", [
        message("I have asthma and take salbutamol as prescribed.", "care", "user"),
        message("我有糖尿病，也对花粉过敏。", "care", "assistant"),
    ] + noise(), ["asthma", "salbutamol", "糖尿病", "花粉过敏"],
         role_checks=[("asthma", "user"), ("糖尿病", "assistant")])
    case("H01_user_scope_boundary", "H", "Summarize my notebook and project tag.", [
        message("My project tag is SWIFT-6243.", "shared-notebook"),
    ], ["SWIFT-6243"], forbidden=["OTHER-USER-2718"], foreign=[message("Other user secret OTHER-USER-2718.", "shared-notebook")])
    case("H02_unknown_scope_empty", "H", "Tell me everything stored about me.", [], [],
         forbidden=["OTHER-USER-2718"], foreign=[message("Other user secret OTHER-USER-2718.", "shared-notebook")])
    case("H03_minimal_identity_disclosure", "H", "What is my name?", [
        message("My name is Tamsin.", "identity", relevance=.995),
        message("My private street address is 28 Imaginary Cedar Lane.", "identity", relevance=.1),
        message("My confidential medical code is MED-8847-PRIVATE.", "identity", relevance=.08),
    ] + noise(), ["Tamsin"], forbidden=["28 Imaginary Cedar Lane", "MED-8847-PRIVATE"], first_within=1_200)
    case("H04_hypothesis_not_current_state", "H", "Where do I currently live, and which location was only hypothetical?", [
        message("I live in Fern City.", "residence", relevance=.99),
        message("If I lived in Elm City, I would commute by boat. This is a hypothetical scenario, not a relocation.", "hypothesis", relevance=.97),
    ] + noise(), ["Fern City", "If I lived in Elm City", "hypothetical scenario, not a relocation"])
    return cases


def build_cases(seed: int = SEED) -> list[dict]:
    """Pure, public fixture export for a separately prepared shared vector cache."""
    return fixtures(seed)


def fixture_texts(seed: int = SEED) -> list[str]:
    cases = build_cases(seed)
    return list(dict.fromkeys([item["text"] for case in cases
                              for item in case["messages"] + case["append"] + case["foreign"]]
                             + [case["query"] for case in cases]))


def _canonical(text):
    return re.sub(r"\s+", " ", text).strip()


def _render(rows):
    pieces = []
    for index, row in enumerate(rows, 1):
        pieces.append(_render_block(index, row))
    return "\n\n".join(pieces)


def _render_block(index, row):
    metadata = {key: row[key] for key in ("role", "session_id", "seq", "status") if key in row}
    return (f"[result {index} " + json.dumps(metadata, ensure_ascii=False, sort_keys=True)
            + "]\n" + str(row.get("content", "")))


def _visible_rows(rows, budget):
    visible, offset = [], 0
    for index, row in enumerate(rows, 1):
        content = str(row.get("content", ""))
        block = _render_block(index, row)
        content_at = offset + len(block) - len(content)
        if content_at >= budget:
            break
        visible.append((row, min(len(content), budget - content_at)))
        offset += len(block) + 2
    return visible


def _decoded_sources(prefix, *, complete=True):
    """Decode declared literal/numeric columns solely from visible output.

    v1.1 accepts literal rows with per-source metadata columns as well as the
    original colon and numeric-template formats. A literal final column owns
    the remainder of its line, so separators inside the source remain literal.
    ``complete=False`` means the prefix cuts a larger render: its unfinished
    trailing line cannot establish an observation and is discarded entirely.
    """
    sources = []
    template, columns = None, None
    if not complete and prefix and not prefix.endswith(("\n", "\r")):
        prefix = prefix.rsplit("\n", 1)[0] if "\n" in prefix else ""
    for line in prefix.splitlines():
        if line.startswith("Literal source template: "):
            template, columns = line.split(": ", 1)[1], None
        elif "Columns: source_rid@seq/segment" in line:
            columns = [value.strip() for value in line.split("Columns: ", 1)[1].split(" | ")[1:]]
        elif line.startswith("[SOURCE") or line.startswith("[DIGEST"):
            template, columns = None, None
        match = re.match(r"^(\d+)@(\d+)/(\d+)(?:: (.*)|(?: \| (.*))?)$", line)
        if not match:
            continue
        rid = int(match[1])
        if match[4] is not None:
            sources.append((rid, match[4]))
        elif columns is not None:
            literal = bool(columns) and columns[-1].casefold() == "literal source text"
            # Only declared metadata columns are split off. The final literal
            # may legitimately contain any number of " | " separators.
            if match[5] is None:
                values = []
            elif literal:
                values = match[5].split(" | ", len(columns) - 1)
            else:
                values = match[5].split(" | ")
            if len(values) != len(columns):
                continue
            if literal:
                if values[-1].strip():
                    sources.append((rid, values[-1]))
                continue
            if template is None:
                continue
            text = template
            for column, value in zip(columns, values):
                text = text.replace("{" + column + "}", value)
            sources.append((rid, text))
    return sources


def decoder_self_check():
    """Handwritten protocol checks, independent of implementation output."""
    examples = [
        ("legacy colon", "[SOURCE session=s role=user]\n7@3/0: x=1 | y=2.", True,
         [(7, "x=1 | y=2.")]),
        ("legacy numeric", "[SOURCE session=s role=user]\nLiteral source template: pressure={n1} kPa; delay={n2} s.\nRows substitute original strings. Columns: source_rid@seq/segment | n1 | n2\n8@4/0 | -3.50 | 0.75", True,
         [(8, "pressure=-3.50 kPa; delay=0.75 s.")]),
        ("literal with timestamp and pipes", "[SOURCE session=s role=user recorded_at_base=2025-01-01T00:00:00Z base_epoch=1735689600.0]\nColumns: source_rid@seq/segment | recorded_at_offset_s | literal source text\n9@5/0 | 60 | input=2.5 | output=7.5 | mode=dry.", True,
         [(9, "input=2.5 | output=7.5 | mode=dry.")]),
        ("literal mixed metadata", "[SOURCE recorded_at_base=2025-01-01T00:00:00Z]\nColumns: source_rid@seq/segment | session | role | recorded_at_offset_s | timestamp_origin_code | relative_time_anchor | literal source text\n10@6/0 | session=lab | role=user | 120 | M | 2025-01-01T00:02:00Z | Never mix A | B unless dry.", True,
         [(10, "Never mix A | B unless dry.")]),
        ("numeric with timestamp", "[SOURCE session=s role=user]\nLiteral source template: pressure={n1} kPa.\nRows substitute original strings. Columns: source_rid@seq/segment | n1 | recorded_at_offset_s | timestamp_origin_code\n11@7/0 | 19.25 | 180 | M", True,
         [(11, "pressure=19.25 kPa.")]),
        ("constant numeric", "[SOURCE session=s role=user]\nLiteral source template: pressure=19.25 kPa.\nRows substitute original strings. Columns: source_rid@seq/segment\n12@8/0", True,
         [(12, "pressure=19.25 kPa.")]),
        ("incomplete metadata", "[SOURCE session=s role=user]\nColumns: source_rid@seq/segment | recorded_at_offset_s | literal source text\n13@9/0 | 240", True, []),
        ("cut literal row", "[SOURCE session=s role=user]\nColumns: source_rid@seq/segment | recorded_at_offset_s | literal source text\n14@10/0 | 300 | pressure=19.25 k", False, []),
        ("completed row before cut", "[SOURCE session=s role=user]\nColumns: source_rid@seq/segment | recorded_at_offset_s | literal source text\n15@11/0 | 360 | pressure=19.25 kPa.\n16@12/0 | 420 | pressure=20", False,
         [(15, "pressure=19.25 kPa.")]),
        ("cut numeric row", "[SOURCE session=s role=user]\nLiteral source template: pressure={n1} kPa.\nRows substitute original strings. Columns: source_rid@seq/segment | n1 | recorded_at_offset_s\n17@13/0 | 19.25 | 48", False, []),
    ]
    for name, text, complete, expected in examples:
        actual = _decoded_sources(text, complete=complete)
        if actual != expected:
            raise AssertionError((name, actual, expected))
    return len(examples)


def _fidelity(rows, prefix, source_map, user_id, budget):
    checked, failures, witnessed = 0, [], set()
    for row, allowance in _visible_rows(rows, budget):
        rid = row.get("source_rid")
        if rid is not None:
            checked += 1
            source = source_map.get(int(rid))
            if source is None or source["user_id"] != user_id:
                failures.append("source outside requesting user")
                continue
            for key, expected in (("role", source["role"]), ("session_id", source["session_id"]), ("seq", source["seq"])):
                if key in row and row[key] != expected:
                    failures.append("source metadata mismatch: " + key)
            for key in ("created_at", "stored_at"):
                if key in row:
                    try:
                        actual_time = datetime.fromisoformat(str(row[key]).replace("Z", "+00:00")).timestamp()
                        if abs(actual_time - source["created_at"]) > .001:
                            failures.append("stored timestamp mismatch: " + key)
                    except (TypeError, ValueError, OverflowError):
                        failures.append("invalid stored timestamp: " + key)
            content = str(row.get("content", ""))[:allowance]
            if str(row.get("id", "")).startswith("mem_"):
                pieces = []
                for line in content.splitlines():
                    if line.startswith(("[SOURCE", "[QUERY-FOCUSED", "[INCOMPLETE EVIDENCE", "[EXPLICIT CORRECTION", "[… source")):
                        continue
                    if line.strip():
                        pieces.append(line)
                for piece in pieces:
                    if _canonical(piece) not in _canonical(source["content"]):
                        failures.append("raw quotation differs from retained source")
                if any(_canonical(source["content"]) in _canonical(piece) for piece in pieces):
                    witnessed.add(int(rid))
    for rid, text in _decoded_sources(prefix, complete=len(_render(rows)) <= budget):
        checked += 1
        source = source_map.get(rid)
        if source is None or source["user_id"] != user_id:
            failures.append("decoded source outside requesting user")
        elif _canonical(text) not in _canonical(source["content"]):
            failures.append("decoded literal row differs from source")
        elif _canonical(text) == _canonical(source["content"]):
            witnessed.add(rid)
    return {"checked": checked, "failures": sorted(set(failures)), "valid": not failures}, witnessed


def _role_ok(rows, phrase, role, budget):
    matching = []
    for row, allowance in _visible_rows(rows, budget):
        content = row.get("content", "")[:allowance]
        if phrase not in content:
            continue
        attributed = row.get("role") == role or bool(re.search(r"\brole=" + re.escape(role) + r"\b", content))
        matching.append(attributed)
    return bool(matching) and all(matching)


def _preflight(cases):
    assert len(cases) >= 30
    assert len({case["case"] for case in cases}) == len(cases)
    for case in cases:
        originals = "\n".join(item["text"] for item in case["messages"] + case["append"])
        for marker in case["required"]:
            assert marker in originals, (case["case"], "required marker absent from invented source", marker)
        for marker, _ in case["role_checks"]:
            assert marker in originals


def evaluate(module, source_root, seed, evaluator_hash, cached_vectors=None, cache_metadata=None):
    cases = fixtures(seed)
    _preflight(cases)
    fixture_hash = hashlib.sha256(json.dumps(cases, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    dimension = len(next(iter(cached_vectors.values()))) if cached_vectors else DIM

    class OfflineStore(module.MemoryStore):
        def __init__(self):
            self._dim = dimension
            self.vectors = dict(cached_vectors) if cached_vectors else {}
            self.connections = []
            self._ensure_schema()

        def _connect(self):
            conn = super()._connect()
            self.connections.append(conn)
            return conn

        def embed(self, texts):
            return [self.vectors[text] for text in texts]

        def close(self):
            for conn in self.connections:
                conn.close()

    memory, reports = OfflineStore(), []
    def add(user, messages, mode, offset=0):
        if not cached_vectors:
            for item in messages:
                similarity = item["relevance"]
                memory.vectors[item["text"]] = [similarity, math.sqrt(1 - similarity ** 2), 0., 0.]
        indexed = list(enumerate(messages, offset))
        chunks = [[pair] for pair in indexed] if mode == "online" else [list(group) for _, group in itertools.groupby(indexed, key=lambda pair: pair[1]["session"])]
        for chunk in chunks:
            session = chunk[0][1]["session"]
            memory.add_messages(user, session, [{"role": item["role"], "content": item["text"],
                "timestamp": item["timestamp"] if item["timestamp"] is not None else BASE_MS + index * 60_000}
                for index, item in chunk], request_id="synthetic-" + str(chunk[0][0]))

    def retrieve(user, case):
        if not cached_vectors:
            memory.vectors[case["query"]] = [1., 0., 0., 0.]
        started = time.perf_counter()
        rows = memory.search(user, case["query"], TOP_K)
        elapsed = round((time.perf_counter() - started) * 1_000, 2)
        with memory._connect() as conn:
            source_map = {row["rid"]: dict(row) for row in conn.execute("SELECT rid,user_id,session_id,role,content,created_at,seq FROM items")}
        text = _render(rows)
        return rows, text, source_map, elapsed

    try:
        for case in cases:
            user = "capability:" + case["case"]
            add(user, case["messages"], case["mode"])
            if case["foreign"]:
                add(user + "-other", case["foreign"], "batch")
            before = None
            if case["append"]:
                initial_rows, initial_text, _, _ = retrieve(user, case)
                before = {str(budget): all(marker in initial_text[:budget] for marker in case["before_required"])
                          for budget in BUDGETS}
                add(user, case["append"], case["mode"], offset=len(case["messages"]))
            rows, text, source_map, elapsed = retrieve(user, case)
            pair_result = None
            if case["compare_batch"]:
                add(user + "-batch", case["messages"] + case["append"], "batch")
                pair_rows, pair_text, pair_map, _ = retrieve(user + "-batch", case)
                pair_result = (pair_rows, pair_text, pair_map)
            budget_reports = []
            for budget in BUDGETS:
                prefix = text[:budget]
                decoded = _decoded_sources(prefix, complete=len(text) <= budget)
                expanded = prefix + "\n" + "\n".join(value for _, value in decoded)
                missing = [marker for marker in case["required"] if marker not in expanded]
                leaked = [marker for marker in case["forbidden"] if marker in expanded]
                fidelity, witnessed = _fidelity(rows, prefix, source_map, user, budget)
                role_failures = [(phrase, role) for phrase, role in case["role_checks"]
                                 if not _role_ok(rows, phrase, role, budget)]
                positions = [prefix.find(marker) for marker in case["required"] if marker in prefix]
                first_position = min(positions) if positions else None
                rank_checks = []
                if case["first_within"] is not None:
                    rank_checks.append(first_position is not None and first_position < case["first_within"])
                for earlier, later in case["precedence"]:
                    first, second = prefix.find(earlier), prefix.find(later)
                    rank_checks.append(first >= 0 and (second < 0 or first < second))
                count_report = None
                if case["source_count"]:
                    target = case["source_count"]
                    expected_sources = {rid for rid, source in source_map.items()
                        if source["user_id"] == user and source["session_id"] == target["session"]}
                    visible_sources = expected_sources & witnessed
                    count_report = {"expected": target["minimum"], "present": len(visible_sources),
                                    "passes": len(visible_sources) >= target["minimum"]}
                metamorphic = None
                if pair_result:
                    pair_rows, pair_text, pair_map = pair_result
                    pair_prefix = pair_text[:budget]
                    _, pair_witnessed = _fidelity(pair_rows, pair_prefix, pair_map, user + "-batch", budget)
                    left = {(source_map[rid]["seq"], source_map[rid]["role"], source_map[rid]["content"])
                            for rid in witnessed if source_map[rid]["user_id"] == user}
                    right = {(pair_map[rid]["seq"], pair_map[rid]["role"], pair_map[rid]["content"])
                             for rid in pair_witnessed if pair_map[rid]["user_id"] == user + "-batch"}
                    pair_expanded = pair_prefix + "\n" + "\n".join(value for _, value in _decoded_sources(pair_prefix, complete=len(pair_text) <= budget))
                    metamorphic = {"equivalent_visible_sources": left == right, "online_sources": len(left),
                                   "batch_sources": len(right), "batch_has_required": all(marker in pair_expanded for marker in case["required"])}
                passed = (not missing and not leaked and fidelity["valid"] and not role_failures
                          and all(rank_checks) and (not count_report or count_report["passes"])
                          and (not before or before[str(budget)])
                          and (not metamorphic or all(metamorphic[key] for key in ("equivalent_visible_sources", "batch_has_required"))))
                if not case["messages"]:
                    passed = passed and not rows
                budget_reports.append({"budget_chars": budget, "passed": passed,
                    "coverage": {"required": len(case["required"]), "present": len(case["required"]) - len(missing), "missing": missing},
                    "source_fidelity": fidelity, "role_failures": role_failures,
                    "irrelevant_leakage": leaked, "rank": {"first_required_character": first_position, "checks": rank_checks,
                    "passed": all(rank_checks)}, "distinct_observations": count_report,
                    "before_append_passed": before[str(budget)] if before else None, "online_batch": metamorphic,
                    "prefix_sha256": hashlib.sha256(prefix.encode()).hexdigest()})
            reports.append({"case": case["case"], "category": case["category"], "query": case["query"],
                "top_k": TOP_K, "input_records": len(case["messages"]) + len(case["append"]),
                "result_count": len(rows), "content_chars": sum(len(row["content"]) for row in rows),
                "rendered_chars": len(text), "local_search_ms": elapsed, "budgets": budget_reports,
                "passed": all(item["passed"] for item in budget_reports)})
    finally:
        memory.close()

    metrics = {}
    for budget in BUDGETS:
        entries = [next(item for item in report["budgets"] if item["budget_chars"] == budget) for report in reports]
        metrics[str(budget)] = {
            "evidence_coverage": {"present": sum(item["coverage"]["present"] for item in entries),
                                  "required": sum(item["coverage"]["required"] for item in entries)},
            "source_fidelity": {"valid_scenarios": sum(item["source_fidelity"]["valid"] and not item["role_failures"] for item in entries), "scenarios": len(entries)},
            "irrelevant_leakage": {"scenarios_with_leakage": sum(bool(item["irrelevant_leakage"]) for item in entries)},
            "rank": {"passed": sum(item["rank"]["passed"] for item in entries if item["rank"]["checks"]),
                     "tested": sum(bool(item["rank"]["checks"]) for item in entries)},
            "fully_passing_scenarios": sum(item["passed"] for item in entries),
        }
    categories = {}
    for category in sorted({report["category"] for report in reports}):
        selected = [report for report in reports if report["category"] == category]
        categories[category] = {"passed": sum(report["passed"] for report in selected), "scenarios": len(selected)}
    return {"evaluation": "broad synthetic evidence capabilities v" + EVALUATOR_VERSION, "seed": seed,
        "decoder_version": EVALUATOR_VERSION,
        "meaning": "Evidence only; no official tasks, answer grading, embedding semantic score, or Smoke-score prediction.",
        "rendering": "Ordered content with provided role/session/seq/status. Complete literal-table rows are independently decoded; omitted rows are never filled.",
        "source_root": str(source_root), "source_sha256": {name: hashlib.sha256((source_root / name).read_bytes()).hexdigest() for name in SOURCE_FILES},
        "evaluator_sha256": evaluator_hash, "fixture_sha256": fixture_hash,
        "safety": {"temporary_sqlite_only": True, "provider_requests": 0, "secret_file_reads": 0,
                   "embedding": "shared offline cached vectors" if cached_vectors else "controlled local 4D vectors"},
        "vectors_cache": cache_metadata,
        "totals": {"scenarios": len(reports), "fully_passing_scenarios": sum(report["passed"] for report in reports)},
        "metrics_by_budget": metrics, "categories": categories, "scenarios": reports}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--self-check-decoder", action="store_true", help="Run handwritten protocol and truncated-prefix checks, then exit without implementation import")
    parser.add_argument("--export-fixtures", type=Path, help="Export fixed invented cases and unique texts, then exit without importing the implementation")
    parser.add_argument("--vectors-cache", type=Path, help="Offline JSON {model,dimensions,vectors:{text:[floats]}} prepared separately; never makes network requests")
    args = parser.parse_args()
    if args.self_check_decoder:
        print(json.dumps({"decoder_version": EVALUATOR_VERSION, "self_checks_passed": decoder_self_check()}))
        return 0
    if args.export_fixtures:
        cases = build_cases(args.seed)
        _preflight(cases)
        exported = {"seed": args.seed, "cases": cases, "texts": fixture_texts(args.seed),
                    "fixture_sha256": hashlib.sha256(json.dumps(cases, ensure_ascii=False, sort_keys=True).encode()).hexdigest()}
        args.export_fixtures.parent.mkdir(parents=True, exist_ok=True)
        args.export_fixtures.write_text(json.dumps(exported, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"scenarios": len(cases), "unique_texts": len(exported["texts"]),
                          "fixture_sha256": exported["fixture_sha256"], "export": str(args.export_fixtures.resolve())}))
        return 0
    cached_vectors, cache_metadata, dimension = None, None, DIM
    if args.vectors_cache:
        if any(part.casefold() == ".secrets" for part in args.vectors_cache.parts):
            parser.error("credential locations cannot be used as vector caches")
        document = json.loads(args.vectors_cache.read_text(encoding="utf-8"))
        cached_vectors = document.get("vectors")
        dimension = int(document.get("dimensions", 0))
        if not isinstance(cached_vectors, dict) or dimension <= 0:
            parser.error("cache requires dimensions and a vectors mapping")
        missing = [text for text in fixture_texts(args.seed) if text not in cached_vectors]
        if missing:
            parser.error(f"shared cache is missing {len(missing)} fixed fixture texts")
        for vector in cached_vectors.values():
            if not isinstance(vector, list) or len(vector) != dimension or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in vector):
                parser.error("shared cache contains invalid vectors")
        cache_metadata = {"sha256": hashlib.sha256(args.vectors_cache.read_bytes()).hexdigest(),
                          "model": document.get("model", "unspecified"), "dimensions": dimension}
    root = args.source_root.resolve()
    for name in SOURCE_FILES:
        if not (root / name).is_file():
            parser.error("source-root is missing " + name)
    original_open, original_connect = builtins.open, sqlite3.connect

    def safe_open(file, *positional, **kwargs):
        if isinstance(file, (str, bytes, os.PathLike)) and any(part.casefold() == ".secrets" for part in Path(os.fsdecode(file)).parts):
            raise FileNotFoundError("secret access disabled in capability evaluation")
        return original_open(file, *positional, **kwargs)

    def no_network(*args, **kwargs):
        raise RuntimeError("network access disabled in capability evaluation")

    with tempfile.TemporaryDirectory(prefix="aml-capability-synthetic-") as directory:
        temp_root = Path(directory).resolve()

        def safe_connect(database, *positional, **kwargs):
            if str(database) != ":memory:" and not Path(database).resolve().is_relative_to(temp_root):
                raise RuntimeError("database access outside capability temporary directory")
            return original_connect(database, *positional, **kwargs)

        env = {"AML_DB_PATH": str(temp_root / "evaluation.db"), "EMBEDDING_DIM": str(dimension),
               "EMBEDDING_API_KEY": "offline-not-a-credential", "AML_AUTH_TOKEN": "offline-not-a-credential",
               "EMBEDDING_MODE": "standard", "EMBEDDING_MODEL": "offline-controlled",
               "EMBEDDING_BASE_URL": "https://offline.invalid/v1", "AML_GOVERNANCE": "false", "AML_QUARANTINE": "false"}
        with patch.dict(os.environ, env), patch("builtins.open", safe_open), patch("sqlite3.connect", safe_connect), \
             patch("socket.create_connection", no_network), patch.object(socket.socket, "connect", no_network), \
             patch.object(socket.socket, "connect_ex", no_network):
            sys.path.insert(0, str(root))
            module = importlib.import_module("store")
            for name in ("store", "facts", "config"):
                if Path(sys.modules[name].__file__).resolve().parent != root:
                    raise RuntimeError("wrong source root imported for " + name)
            if Path(module.CONFIG.db_path).resolve().parent != temp_root:
                raise RuntimeError("temporary database configuration was not applied")
            report = evaluate(module, root, args.seed, hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), cached_vectors, cache_metadata)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"totals": report["totals"], "metrics_by_budget": report["metrics_by_budget"],
        "categories": report["categories"], "failed_scenarios": [item["case"] for item in report["scenarios"] if not item["passed"]],
        "report": str(args.output.resolve()) if args.output else None}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
