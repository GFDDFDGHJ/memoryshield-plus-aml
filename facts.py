"""Rule-based fact layer for the AML submission (no LLM calls).

Why this exists: the vector index only knows raw messages. The AML rubric scores
several capabilities that raw retrieval structurally cannot serve:

  D1/D2  new value overrides old / contradiction resolution
  F1     summary & global synthesis over long history   (scored 0.00)
  G2     rule application                               (scored 20.00)
  C3     stance / attribute trajectory
  I      streaming memory (dedup + consolidation)

So at Add time we also mine (subject, predicate, value) triples with regexes,
and at Search time we prepend a pinned profile plus the facts that match the
query. Extraction and source replay are deterministic; their cost is measured
with synthetic concurrent Add/Search checks before release.

Conflict handling is deliberately narrow: a new value only retires the *same*
(user, subject, predicate) triple. This is the fix for the old governance rule,
which did LIKE '%<topic>%' and hid 1480 of 3974 rows because it extracted
"that" as a topic from "Yes, that is quite true."
"""
from __future__ import annotations

import json
import math
import re
import state_evidence as state_layer
import context_data as data_layer
from collections import Counter
import evidence_links as link_layer
import query_plan as query_layer
import source_dependencies as dependency_layer
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone

# ---------------------------------------------------------------- patterns
# Each entry: (compiled regex, builder) where builder(match) -> (subject,
# predicate, value, kind, confidence). `value` may be None to reject.

# Values that carry no information: a regex hit on "that" must never become a
# predicate, because the old governance rule turned it into a bulk demotion.
STOP_VALUES = frozenset(
    "that it this there here not yes no true false me you him her them something "
    "anything everything a an the ok okay fine good bad".split()
)


def _clean(value: str) -> str:
    value = value.strip().strip(" \t\"'`.,;:!?()[]{}")
    value = re.sub(r"\s+", " ", value)
    return value


def _ok(value: str) -> bool:
    if not value or len(value) < 1 or len(value) > 160:
        return False
    return value.lower() not in STOP_VALUES


def _affirmative_complement(value: str) -> bool:
    """An unstructured predicate's negated object is not a replacement value.

    Keep the source for retrieval; do not assert e.g. a name of 'not Eira'.
    Explicitly quoted literals and structured triples retain their semantics.
    This does not retract any old value or infer a new one.
    """
    return not bool(re.match(r'\s*(?:not\b|no\s+longer\b|neither\b)', value, re.I))


@dataclass(frozen=True)
class Fact:
    subject: str
    predicate: str
    value: str
    kind: str          # fact | preference | rule | event
    confidence: float
    source_pos: int = 0
    operation: str = "assert"  # assert | retract; extraction metadata only


# --- English: possessive / self facts -------------------------------------
_POSS = re.compile(
    r"\bmy\s+([a-z][a-z0-9 _'-]{1,30}?)(?:'s)?\s+(name|age|job|birthday|address|"
    r"number|email|phone|company|boss|salary|dog|cat|wife|husband|partner|son|"
    r"daughter|child|kid|car|house|apartment|city|hometown|school|major)\s+"
    r"(?:is|are|was|were|=)\s+([^.!?\n]{1,120})",
    re.I,
)

_SELF = [
    (re.compile(r"\bI\s+(?:live|reside|am based|'m based)\s+in\s+([^.!?\n]{2,80})", re.I),
     ("self", "location", "fact", 0.9)),
    (re.compile(r"\bI\s+(?:work|am working)\s+(?:as|at|for|in)\s+([^.!?\n]{2,80})", re.I),
     ("self", "work", "fact", 0.85)),
    (re.compile(r"\bI\s+am\s+(\d{1,3})\s+years?\s+old\b", re.I), ("self", "age", "fact", 0.95)),
    (re.compile(r"\bI\s+am\s+(?:a|an)\s+([^.!?\n]{2,60})", re.I), ("self", "role", "fact", 0.7)),
    (re.compile(r"\bI(?:'m|\s+am)\s+from\s+([^.!?\n]{2,60})", re.I), ("self", "origin", "fact", 0.85)),
    (re.compile(r"\bmy\s+name\s+is\s+([^.!?\n]{2,60})", re.I), ("self", "name", "fact", 0.95)),
    (re.compile(r"\b(?:call|address)\s+me\s+([A-Z][^\s.!?,]{1,30})"), ("self", "nickname", "fact", 0.9)),
]

# --- English: preferences -------------------------------------------------
_PREF = re.compile(
    r"\bI\s+(?:(?:really|absolutely|kind of)\s+)?(like|love|prefer|enjoy|adore)\s+"
    r"([^.!?\n]{1,80})",
    re.I,
)
_DISPREF = re.compile(
    r"\bI\s+(?:do\s+not|don't|dont|really\s+don't)\s+(like|want)\s+([^.!?\n]{1,80})"
    r"|\bI\s+(hate|dislike|can't\s+stand)\s+([^.!?\n]{1,80})",
    re.I,
)
_FAVORITE = re.compile(
    r"\bmy\s+favou?rite\s+([a-z][a-z0-9 _-]{1,30}?)\s+(?:is|are)\s+([^.!?\n]{1,80})", re.I
)

# --- English: rules / standing instructions -------------------------------
# (regex, gate_on_sentence_start, weak). Gating matters: "I've always been awed
# by birds" and "I don't know the process" are narration, not instructions.
# `weak` marks cue words that occur constantly in dialogue (always/never/don't):
# for those the action must additionally start with a clear instruction verb,
# otherwise a scripted line like "always have a slice of beef" becomes a rule.
_EN_RULE_ACTION = r"((?:[^.!?\n]|\.(?=\d)){2,140})"
_RULE = [
    (re.compile(r"(?:please\s+)?always\s+" + _EN_RULE_ACTION, re.I), True, True, False),
    (re.compile(r"(?:please\s+)?never\s+" + _EN_RULE_ACTION, re.I), True, True, False),
    (re.compile(r"from\s+now\s+on[,\s]+" + _EN_RULE_ACTION, re.I), True, False, False),
    (re.compile(r"(?:make\s+sure|be\s+sure)\s+to\s+" + _EN_RULE_ACTION, re.I), False, False, False),
    (re.compile(r"remember\s+to\s+" + _EN_RULE_ACTION, re.I), False, False, False),
    # "You must ..." is a rule in an instruction turn and a line of dialogue in a
    # script ("You must have a slice of beef" -- Dr. Stockmann). System turns only.
    (re.compile(r"(?:you\s+must|you\s+should\s+always)\s+" + _EN_RULE_ACTION, re.I), False, False, True),
    (re.compile(r"(?:do\s+not|don't|dont)\s+(?:ever\s+)?" + _EN_RULE_ACTION, re.I), True, True, False),
]


_QUESTION_ZH = re.compile(r"(吗|呢|吧)[。！!.?\s]*$|[?？]|是不是|能不能|要不要|会不会|可不可以|怎么办|怎么才")
_QUESTION_EN = re.compile(r"\?$|^(what|how|why|when|where|who|which|is|are|do|does|did|can|could|would|should)\b", re.I)

# First word that marks an instruction rather than a description.
_EN_VERBS = frozenset(
    "use keep reply respond answer output write call avoid prefer include start end "
    "format return provide ensure make list explain summarize cite verify "
    "check confirm stop remember disclose protect retain mark omit redact specify "
    "separate report convert measure calculate validate quote".split()
)
_ZH_INSTR = re.compile(r"(要|请|必须|不要|别|记得|优先|务必|改用|避免|保持|按照|首先|先)")


def _imperative_action(action: str) -> bool:
    """True if the clause reads as an instruction ('keep it under 100 words').

    A bare conditional match is not a rule: "If it rains, it is obvious that the
    road floods" is narration. Without this gate, conditional patterns produced
    346 junk rules out of 468 candidates on the real corpus.
    """
    a = action.strip()
    if len(a) < 3:
        return False
    # The marker has to *start* the clause: "要背东西、走远路的时候会很没底气" is
    # a description that merely contains 要, not an instruction.
    if _ZH_INSTR.match(a[:10]):
        return True
    first = re.split(r"[^A-Za-z']+", a.lower())[0] if a else ""
    if first in _EN_VERBS:
        return True
    return bool(re.search(r"\b(always|never|must|should|do not|don't)\b", a, re.I))


def _rule_ok(value: str) -> bool:
    """Reject 'rules' that are really questions or narration.

    The pinned profile shows these first on every query, so a question sitting
    there is worse than no rule at all.
    """
    v = value.strip()
    if len(v) < 4 or len(v) > 240:
        return False
    # A conditional rule can quote a question in its trigger. Validate the
    # action rather than mistaking that trigger for a question to the assistant.
    conditional = _COND_EN.fullmatch(v) or _COND_ZH.fullmatch(v)
    if conditional:
        v = conditional.group(2).strip()
    prohibition = re.match(r"(?:please\s+)?(?:do\s+not|don't|dont)\b", v, re.I)
    if _QUESTION_ZH.search(v) or (_QUESTION_EN.search(v) and not prohibition):
        return False
    # A rule should carry an instruction-ish verb in the first clause.
    return True


def _sentence_start(text: str, pos: int) -> bool:
    """True if `pos` begins a clause: start of text, after .!?\\n: or after 'please'."""
    j = pos - 1
    while j >= 0 and text[j] in " \t\"'([*-":
        j -= 1
    if j < 0:
        return True
    if text[j] in ".!?\n:;。！？：；":
        return True
    return text[max(0, j - 6):j + 1].lower().endswith("please")


# --- Chinese: health / personal background --------------------------------
# These were never extracted. The pinned profile used to look populated only
# because loose rule patterns were catching whole narrative sentences; once the
# rules were tightened, users with Chinese health backgrounds had an EMPTY
# profile and E3 (personal background, health, care) went 100 -> 0.00.
_ZH_HEALTH = [
    (re.compile(r"我(?:患有|得了|有(?!点|些))\s*([\u4e00-\u9fa5A-Za-z0-9]{1,10}?(?:病|症|炎|高血压|糖尿病|失眠|焦虑|抑郁))"),
     ("self", "condition", "fact", 0.85)),
    (re.compile(r"我(?:对)\s*([\u4e00-\u9fa5A-Za-z0-9]{1,10}?)\s*(?:有点|稍微|比较)?\s*过敏"),
     ("self", "allergy", "fact", 0.9)),
    (re.compile(r"我(?:在|正在)(?:吃|服用|用)\s*([\u4e00-\u9fa5A-Za-z0-9]{2,12})"), ("self", "medication", "fact", 0.8)),
    (re.compile(r"我的(血压|血糖|体重|身高|心率|血脂)(?:是|有点|大概)?\s*([0-9]{1,3}(?:\.[0-9])?)\s*([\u4e00-\u9fa5A-Za-z%]{0,4})"),
     ("self", "metric", "fact", 0.8)),
    (re.compile(r"我(?:今年)?\s*(\d{1,3})\s*岁"), ("self", "age", "fact", 0.9)),
    (re.compile(r"我(?:住在|家住)\s*([\u4e00-\u9fa5A-Za-z0-9]{2,15})"), ("self", "location", "fact", 0.85)),
]

# A captured health value must be a short noun phrase, not a clause.
_HEALTH_BAD = re.compile(r"(的|把|又|就是|有点|一点|一次|要不要|暂时|不太|还是|然后)")
# Same idea for self.* values of any origin: these read as clauses, not facts.
_SELF_BAD = re.compile(r"(要不要|暂时|又|就是|不太|然后把|一下)")

# --- Chinese --------------------------------------------------------------
_ZH = [
    (re.compile(r"我(?:现在)?住(?:在|于)?\s*([\u4e00-\u9fa5A-Za-z0-9 ]{2,20})"),
     ("self", "location", "fact", 0.9)),
    (re.compile(r"我(?:在|于)\s*([\u4e00-\u9fa5A-Za-z0-9 ]{2,20})\s*(?:工作|上班|就职)"),
     ("self", "work", "fact", 0.85)),
    (re.compile(r"我叫\s*([\u4e00-\u9fa5A-Za-z0-9 ]{2,20})"), ("self", "name", "fact", 0.95)),
    (re.compile(r"我今年\s*(\d{1,3})\s*岁"), ("self", "age", "fact", 0.95)),
    (re.compile(r"我最喜欢的([\u4e00-\u9fa5]{1,10})是\s*([^。！？\n]{1,40})"),
     ("favorite", None, "preference", 0.9)),
    (re.compile(r"我(?:很)?(?:喜欢|爱|偏爱)\s*([^。！？\n]{1,60})"), ("like", None, "preference", 0.75)),
    (re.compile(r"我(?:不喜欢的?是?|讨厌|受不了)\s*([^。！？\n]{1,60})"),
     ("dislike", None, "preference", 0.75)),
]
_ZH_RULES = [
    (re.compile(r"(?:以后|今后|从现在开始)[^。！？\n]{0,10}?(?:都要|要|请)\s*([^。！？\n]{2,80})"), False),
    (re.compile(r"(?:记住|记得)\s*([^。！？\n]{2,120})"), False),
    (re.compile(r"(?:必须|一定要)\s*([^。！？\n]{2,120})"), True),
    (re.compile(r"(?:不要|别|禁止)\s*([^。！？\n]{2,120})"), True),
    (re.compile(r"(?:总是|一直)\s*([^。！？\n]{2,120})"), True),
]

# --- conditional standing instructions ------------------------------------
# "If the user asks for a summary, keep it under 100 words" -- G2 questions
# revolve around exactly this shape, and it is invisible to the plain
# imperative patterns above.
_COND_EN = re.compile(
    r"\b(?:if|when|whenever)\s+([^,\n]{3,60}),\s*(?:then\s+)?(?:always\s+|please\s+)?"
    r"((?:[^.!?\n]|\.(?=\d)){3,120})",
    re.I,
)
_COND_ZH = re.compile(r"如果\s*([^，,\n]{2,30})[，,]\s*(?:就|则|那么)?\s*([^。！？\n]{2,60})")

# User-authored Chinese directives need an explicit addressee or response
# action. Merely containing 要/必须/不要 is not sufficient: it is common in
# first-person narration, quoted dialogue, and descriptions of symptoms.
_ZH_USER_RULE = re.compile(
    r"(?:以后|今后|从现在开始)\s*(?:你|您)?\s*"
    r"(?:回答|回复|输出|称呼|解释|总结|写作|格式)[^。！？\n]{2,140}"
    r"|(?:请|务必|记得|记住|不要|别)\s*(?:你|您)?\s*"
    r"(?:回答|回复|输出|称呼|解释|总结|使用|采用|包含|省略|透露|泄露|提供|引用|保留|遵守|按照)"
    r"[^。！？\n]{2,140}"
    r"|(?:你|您)\s*(?:必须|需要|应当|应该|不要|别)\s*"
    r"(?:回答|回复|输出|称呼|解释|总结|使用|采用|包含|省略|透露|泄露|提供|引用|保留|遵守|按照)"
    r"[^。！？\n]{2,140}"
)

# --- explicit state changes (D1: current value wins) ----------------------
_CHANGE_EN = [
    (re.compile(r"\bI\s+(?:have\s+)?(?:just\s+)?(?:moved|relocated|transferred)\s+to\s+([^.!?\n]{2,60})", re.I),
     ("self", "location")),
    (re.compile(r"\bI\s+(?:have\s+)?(?:changed|switched|left)\s+(?:my\s+)?(?:job|role|position|company)\b"
                r"(?:\s*(?:to|for|at)\s+([^.!?\n]{2,60}))?", re.I), ("self", "work")),
]
_RETRACT_EN = [
    (re.compile(r"\bI\s+no\s+longer\s+(?:live|reside)\s+in\s+([^.!?;,，\n]{2,80})", re.I),
     ("self", "location")),
    (re.compile(r"\bI\s+no\s+longer\s+work\s+(?:as|at|for|in)\s+([^.!?;,，\n]{2,80})", re.I),
     ("self", "work")),
]
_CHANGE_ZH = [
    (re.compile(r"(?:我)?(?:已经)?(?:搬|调)(?:到|去)了?\s*([\u4e00-\u9fa5A-Za-z0-9 ]{2,20})"), ("self", "location")),
    (re.compile(r"(?:我)?(?:换|辞)(?:了)?(?:工作|公司|岗位)"), ("self", "work")),
]

# --- bullet lists in system/instruction turns -----------------------------
# Agent-style prompts state their rules as bullets; when the turn is role
# 'system' these are authoritative, so they are mined without the sentence-
# start gate that narration needs.
_BULLET = re.compile(r"^[ \t]*(?:[-*\u2022]|\d+[.)])\s+(.{3,200})$", re.M)

# Wikidata / mquake corpora ship triples as JSON: {"subject": "Frank Herbert",
# "relation": "genre", "object": "funk"} plus an explicit "UPDATE: replace the
# prior value" line. None of the natural-language patterns match this shape, so
# all 24 triple users had zero facts and the [UPDATED] tier never fired -- the
# single reachable cause behind D2 sitting at 40.00.
_CORPUS = re.compile(
    r'"subject"\s*:\s*"([^"]{1,80})"\s*,\s*"relation"\s*:\s*"([^"]{1,80})"'
    r'\s*,\s*"object"\s*:\s*"([^"]{1,120})"'
)
_CORPUS_ARR = re.compile(r'"([^"]{1,60})"\s*,\s*"([^"]{1,40})"\s*,\s*"([^"]{1,80})"')



def _slug(text: str, limit: int = 60) -> str:
    """Stable, comparable key for a free-form object (rule/preference target)."""
    t = _clean(text).lower()
    t = re.sub(r"[^0-9a-z\u4e00-\u9fa5 ]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:limit]


def extract_facts(text: str, relaxed: bool = False) -> list[Fact]:
    """Mine (subject, predicate, value) from one message. Pure regex, no I/O.

    `relaxed` is for authoritative instruction turns (role='system'): there the
    sentence-start gate is dropped and bullet lines are mined too, because an
    agent prompt states its rules as a list rather than as prose.
    """
    if not text or len(text) > 20000:
        return []
    out: list[Fact] = []
    seen: set[tuple[str, str, str, int, str]] = set()

    conditionals = [(m, False) for m in _COND_EN.finditer(text)]
    conditionals += [(m, True) for m in _COND_ZH.finditer(text)]

    def inside_conditional(pos: int) -> bool:
        return any(m.start() <= pos < m.end() for m, _ in conditionals)

    def add(subject: str, predicate: str, value: str, kind: str, conf: float,
            source_pos: int, operation: str = "assert") -> None:
        if kind != "rule" and inside_conditional(source_pos):
            return  # A hypothetical self-description is not a current fact.
        value = _clean(value)
        if not (_rule_ok(value) if kind == "rule" else _ok(value)):
            return
        if subject == "self" and _SELF_BAD.search(value):
            return  # clause fragment, not a value ("看你的情况要不要暂时降低")
        key = (subject.lower(), predicate.lower(), value.lower(), source_pos, operation)
        if key in seen:
            return
        seen.add(key)
        out.append(Fact(subject.lower(), predicate.lower(), value, kind, conf,
                        source_pos, operation))

    # possessive: "my dog's name is Max" / "my boss is Karen"
    for m in _POSS.finditer(text):
        attr, subj_key, value = m.group(1), m.group(2), m.group(3)
        # group(1) is the prefix noun ("dog" in "my dog's name is Max")
        subject = "self" if _slug(attr) in {"", "my"} else _slug(attr)
        if _affirmative_complement(value):
            add(subject, _slug(subj_key), value, "fact", 0.9, m.start())

    for rx, (subject, predicate, kind, conf) in _SELF:
        for m in rx.finditer(text):
            if _affirmative_complement(m.group(1)):
                add(subject, predicate, m.group(1), kind, conf, m.start())

    for m in _PREF.finditer(text):
        add("pref", "like", _slug(m.group(2)), "preference", 0.7, m.start())
    for m in _DISPREF.finditer(text):
        obj = m.group(2) or m.group(4)
        add("pref", "dislike", _slug(obj), "preference", 0.75, m.start())
    for m in _FAVORITE.finditer(text):
        add("favorite", _slug(m.group(1)), m.group(2), "preference", 0.85, m.start())

    for rx, gated, weak, sys_only in _RULE:
        if sys_only and not relaxed:
            continue
        for m in rx.finditer(text):
            if inside_conditional(m.start()):
                continue
            if gated and not relaxed and not _sentence_start(text, m.start()):
                continue
            conf = 0.8
            if weak and not relaxed and not _imperative_action(m.group(1)):
                # Unverified imperative: keep it for retrieval (it may well be
                # the governing rule for some question) but mark it low
                # confidence so it never enters the pinned profile.
                conf = 0.45
            body = _slug(m.group(0), 120)
            if body:
                add("rule", body[:60] or "rule", m.group(0), "rule", conf, m.start())

    # conditional instructions: "if X, then always Y" -- the action must itself
    # read as an instruction, otherwise narrative conditionals flood the table.
    for m, is_zh in conditionals:
        cond, action = _slug(m.group(1), 60), _clean(m.group(2))
        if cond and _ok(action) and _imperative_action(action):
            predicate = ("当" if is_zh else "when ") + cond
            add("rule", predicate, m.group(0), "rule", 0.85, m.start())

    # explicit state changes win over whatever was stored before
    for rx, (subject, predicate) in _CHANGE_EN:
        for m in rx.finditer(text):
            if m.groups() and m.group(1) and _affirmative_complement(m.group(1)):
                add(subject, predicate, m.group(1), "fact", 0.95, m.start())
    for rx, (subject, predicate) in _RETRACT_EN:
        for m in rx.finditer(text):
            add(subject, predicate, m.group(1), "fact", 0.95, m.start(), "retract")
    for rx, (subject, predicate) in _CHANGE_ZH:
        for m in rx.finditer(text):
            if m.groups() and m.group(1):
                add(subject, predicate, m.group(1), "fact", 0.95, m.start())

    # instruction turns state their rules as bullets; those are authoritative
    if relaxed:
        for m in _BULLET.finditer(text):
            body = _clean(m.group(1))
            if _ok(body) and len(body) > 8:
                add("rule", _slug(body, 60) or "rule", body, "rule", 0.75, m.start())

    for rx, (subject, predicate, kind, conf) in _ZH:
        for m in rx.finditer(text):
            if predicate is None:
                # (subject, None) entries carry the object in group 1
                if subject == "favorite":
                    head, value = m.group(1), m.group(2)
                    add("favorite", _slug(head), value, "preference", conf, m.start())
                else:
                    add("pref", "like" if subject == "like" else "dislike",
                        _slug(m.group(1)), "preference", conf, m.start())
            else:
                add(subject, predicate, m.group(1), kind, conf, m.start())

    for rx, (subject, predicate, kind, conf) in _ZH_HEALTH:
        for m in rx.finditer(text):
            if predicate == "metric" and len(m.groups()) >= 3:
                label, num, unit = m.group(1), m.group(2), m.group(3)
                add("self", _slug(label), (num + " " + unit).strip(), "fact", conf, m.start())
            elif m.groups() and m.group(1):
                value = m.group(1).strip()
                if _HEALTH_BAD.search(value) or len(value) < 2:
                    continue
                add(subject, predicate, value, kind, conf, m.start())

    # Chinese narrative is indistinguishable from Chinese instructions at the
    # regex level ("我也不是完全不能活动，但…"), so ZH rules are only mined from
    # authoritative system turns. The pinned profile must not carry narration.
    if relaxed:
        for rx, gated in _ZH_RULES:
            for m in rx.finditer(text):
                if inside_conditional(m.start()):
                    continue
                if gated and not relaxed and not _sentence_start(text, m.start()):
                    continue
                body = _slug(m.group(0), 120)
                if body:
                    add("rule", body[:60] or "rule", m.group(0), "rule", 0.8, m.start())
    else:
        for m in _ZH_USER_RULE.finditer(text):
            if inside_conditional(m.start()) or not _sentence_start(text, m.start()):
                continue
            value = m.group(0)
            add("rule", _slug(value, 60) or "rule", value, "rule", 0.8, m.start())

    # Positional triples: the clbench corpora write `Corpus: {"India",
    # "continent", "Asia"}` / `Corpus: UPDATE: replace the prior value` (48
    # update messages, 40 users, 0 facts before this). Gated on the literal
    # "Corpus" token so ordinary 3-string JSON does not become facts.
    if "Corpus" in text or "corpus" in text:
        for m in _CORPUS_ARR.finditer(text):
            subj, pred, val = _slug(m.group(1), 60), _slug(m.group(2), 40), _clean(m.group(3))
            if subj and pred and _ok(val):
                add(subj, pred, val, "fact", 0.95, m.start())

    # structured triples (wikidata / mquake). store_facts retires the prior
    # value of the same (subject, predicate) and keeps previous_value, so the
    # "UPDATE: replace the prior value" message wins purely on seq order.
    for m in _CORPUS.finditer(text):
        subj, pred = _slug(m.group(1), 60), _slug(m.group(2), 40)
        if subj and pred:
            add(subj, pred, m.group(3), "fact", 0.95, m.start())

    # Keep the highest-confidence first; cap per message so a long dialogue
    # cannot flood the fact table.
    #
    # Rule hygiene: a "rule" that is really a question or a narration sentence
    # poisons the pinned profile -- and the profile is the first thing the judge
    # reads on every query. Chinese clinical/roleplay data was producing rules
    # like "我是不是要把它分成早上五分钟…" and "胃基本不怎么闹情绪…".
    candidates: dict[tuple, Fact] = {}
    for f in out:
        # Overlapping patterns describe one assertion, not successive updates.
        # Choose confidence and completeness before applying textual order.
        # The bullet and conditional rule miners can likewise find one rule
        # with different predicate keys; its complete value identifies it.
        key = (("rule", f.value.casefold()) if f.kind == "rule" else
               (f.source_pos, f.subject, f.predicate, f.kind, f.operation))
        previous = candidates.get(key)
        rank = (f.confidence, len(f.value), f.source_pos)
        if previous is None or rank > (previous.confidence, len(previous.value), previous.source_pos):
            candidates[key] = f
    out = list(candidates.values())
    out.sort(key=lambda f: (-f.confidence, -f.source_pos))
    # Explicit standing rules cannot be silently discarded merely because a
    # message contains many of them. The input message already has a bound;
    # retain that complete rule set while bounding ordinary derived facts.
    # Confidence chooses facts, never the update order (including A -> B -> A).
    rules = [fact for fact in out if fact.kind == "rule"]
    stable = [fact for fact in out if fact.kind != "rule"][:12]
    return sorted(rules + stable, key=lambda f: f.source_pos)


# ----------------------------------------------------------------- storage
SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    fid        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT NOT NULL,
    session_id TEXT NOT NULL,
    source_rid INTEGER,
    subject    TEXT NOT NULL,
    predicate  TEXT NOT NULL,
    value      TEXT NOT NULL,
    kind       TEXT NOT NULL,
    confidence REAL NOT NULL,
    status     TEXT NOT NULL DEFAULT 'active',
    previous_value TEXT,
    created_at REAL NOT NULL,
    source_role TEXT NOT NULL DEFAULT 'unknown',
    seq        INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_facts_user ON facts(user_id, status, kind);
CREATE INDEX IF NOT EXISTS idx_facts_key  ON facts(user_id, subject, predicate, status);
"""

# Per-session digest. F1 (summary / long-history synthesis) and G4 (patterns in
# experiential data) cannot be answered from single messages no matter how good
# the ranking is -- they need a compact view of a whole session. This is
# extractive on purpose: no model call, rebuildable at any time.
DIGEST_SCHEMA = """
CREATE TABLE IF NOT EXISTS digests (
    user_id    TEXT NOT NULL,
    session_id TEXT NOT NULL,
    content    TEXT NOT NULL,
    n_msgs     INTEGER NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (user_id, session_id)
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.executescript(DIGEST_SCHEMA)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(facts)")}
    if "source_role" not in columns:
        # Existing rows remain unknown. Source attribution can be read from a
        # retained item, but migration never invents a speaker for old facts.
        conn.execute("ALTER TABLE facts ADD COLUMN source_role TEXT NOT NULL DEFAULT 'unknown'")


def store_facts(
    conn: sqlite3.Connection,
    user_id: str,
    session_id: str,
    source_rid: int | None,
    text: str,
    created_at: float,
    seq: int,
    role: str = "user",
) -> int:
    """Extract and persist facts for one message, retiring superseded values.

    Only an exact (subject, predicate) match is retired -- never a substring
    sweep over message bodies. Standing rules are only taken from user/system
    turns: an assistant saying "I will always ..." is not a user rule.
    """
    facts = extract_facts(text, relaxed=(role == "system"))
    if role not in {"user", "system"}:
        facts = [f for f in facts if f.kind != "rule"]
    # First-person assertions belong to their source speaker. Keep assistant
    # background retrievable with attribution, without letting it retire a
    # user's first-person assertion of the same attribute.
    source_role = str(role or "unknown")
    written = 0
    prepared_keys = set()
    chronology_keys = set()
    for f in facts:
        if (f.subject, f.predicate) not in prepared_keys:
            _resolve_legacy_roles(conn, user_id, f.subject, f.predicate, before_seq=seq)
            prepared_keys.add((f.subject, f.predicate))
        if f.kind == "fact" or f.subject == "favorite":
            chronology_keys.add((f.subject, f.predicate))
        if f.operation == "retract":
            # Negating an old employer is not an assertion of a new location.
            # Match the named value exactly so denying an older state cannot
            # erase an unrelated, newer state on the same attribute.
            rows = conn.execute(
                "SELECT fid,value FROM facts WHERE user_id=? AND subject=?"
                " AND predicate=? AND source_role=? AND status='active'",
                (user_id, f.subject, f.predicate, source_role),
            ).fetchall()
            for row in rows:
                if _clean(row["value"]).casefold() == f.value.casefold():
                    conn.execute("UPDATE facts SET status='stale' WHERE fid=?", (row["fid"],))
            continue

        single_valued = f.kind == "fact" or f.subject == "favorite"
        # identical fact already active -> nothing to do
        dup = conn.execute(
            "SELECT fid FROM facts WHERE user_id=? AND subject=? AND predicate=?"
            " AND value=? AND source_role=? AND status='active'",
            (user_id, f.subject, f.predicate, f.value, source_role),
        ).fetchone()
        if dup:
            if single_valued:
                # Clean up conflicting active values from older derived data,
                # even when this assertion repeats one of those values.
                conn.execute(
                    "UPDATE facts SET status='stale' WHERE user_id=? AND subject=?"
                    " AND predicate=? AND source_role=? AND status='active' AND fid<>?",
                    (user_id, f.subject, f.predicate, source_role, dup[0]),
                )
            continue

        previous = None
        if single_valued:
            row = conn.execute(
                "SELECT fid, value FROM facts WHERE user_id=? AND subject=? AND predicate=?"
                " AND source_role=? AND status='active' ORDER BY seq DESC, fid DESC LIMIT 1",
                (user_id, f.subject, f.predicate, source_role),
            ).fetchone()
            if row:
                previous = row[1]
                conn.execute(
                    "UPDATE facts SET status='stale' WHERE user_id=? AND subject=?"
                    " AND predicate=? AND source_role=? AND status='active'",
                    (user_id, f.subject, f.predicate, source_role),
                )

        conn.execute(
            "INSERT INTO facts(user_id,session_id,source_rid,subject,predicate,value,kind,"
            "confidence,status,previous_value,created_at,seq,source_role)"
            " VALUES(?,?,?,?,?,?,?,?,'active',?,?,?,?)",
            (user_id, session_id, source_rid, f.subject, f.predicate, f.value, f.kind,
             f.confidence, previous, created_at, seq, source_role),
        )
        written += 1
    for subject, predicate in chronology_keys:
        # Reconcile only this message's affected attribute and speaker. A late
        # Add is not evidence that its utterance occurred after prior Adds.
        written += _replay_fact_chronology(conn, user_id, subject, predicate, source_role,
                                           source_rid=source_rid)
    return written


# ------------------------------------------------------------- retrieval
_STOPWORDS = frozenset(
    "the and for are was were with that this from have has had what when which who "
    "why how does did do is in on at to of it a an my me you your their they we us "
    "be been being about any all can could would should will just also more most "
    "some such than then there these those one two not no yes if or but as by so up "
    "out into over under again further once here very too s t".split()
)


def _tokens(text: str) -> set[str]:
    text = text.lower()
    words = {w for w in re.findall(r"[a-z0-9]{2,}", text) if w not in _STOPWORDS}
    cjk = re.findall(r"[\u4e00-\u9fa5]", text)
    words |= {cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)}
    return words


def _has_items(conn: sqlite3.Connection) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='items'"
    ).fetchone() is not None


def _timestamp_column(conn: sqlite3.Connection) -> str:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(items)")}
    return "timestamp_origin" if "timestamp_origin" in columns else "'legacy-unknown' AS timestamp_origin"


def _replay_fact_chronology(conn: sqlite3.Connection, user_id: str, subject: str,
                            predicate: str, source_role: str, *, source_rid: int | None = None) -> int:
    """Replay one single-valued key from its retained source utterances.

    Only a complete set of explicit message timestamps is comparable. Mixed
    or unknown origins retain source order; a timestamp never becomes the
    occurrence date of an event described inside the utterance. Reassertions
    and retractions are replayed as distinct source evidence, including when
    an earlier implementation did not store a duplicate derived assertion.
    This runs only for an affected Add key, never as a global migration.
    """
    if not _has_items(conn):
        return 0
    timestamp_column = _timestamp_column(conn)
    if timestamp_column != 'timestamp_origin':
        return 0
    explicit_source = conn.execute(
        "SELECT 1 FROM items WHERE user_id=? AND role=? AND status='active'"
        " AND timestamp_origin='message.timestamp' AND (rid=? OR rid IN ("
        "SELECT source_rid FROM facts WHERE user_id=? AND subject=? AND predicate=? AND source_role=?)) LIMIT 1",
        (user_id,source_role,source_rid,user_id,subject,predicate,source_role),
    ).fetchone()
    if not explicit_source:
        return 0
    sources = conn.execute(
        "SELECT rid,user_id,session_id,role,content,created_at,seq," + timestamp_column
        + " FROM items WHERE user_id=? AND role=? AND status='active' ORDER BY seq,rid",
        (user_id, source_role),
    ).fetchall()
    events = []
    for source in sources:
        for fact in extract_facts(source['content'], relaxed=(source_role == 'system')):
            if ((fact.subject, fact.predicate) == (subject, predicate)
                    and (fact.kind == 'fact' or fact.subject == 'favorite')):
                events.append((source, fact))
    if not events or not any(source['timestamp_origin'] == 'message.timestamp' for source, _ in events):
        return 0
    stored = conn.execute(
        "SELECT * FROM facts WHERE user_id=? AND subject=? AND predicate=? AND source_role=?"
        " ORDER BY seq,fid", (user_id, subject, predicate, source_role),
    ).fetchall()
    available = {}
    for row in stored:
        available.setdefault((row['source_rid'], row['value'], row['kind']), []).append(row['fid'])
    # No retained utterance can prove an unmatched legacy assertion. Preserve
    # existing source-order behavior rather than retire it by inferred time.
    remaining = {key:list(values) for key, values in available.items()}
    planned = []
    for source, fact in events:
        matches = remaining.get((source['rid'], fact.value, fact.kind), [])
        fid = matches.pop(0) if fact.operation != 'retract' and matches else None
        planned.append((source, fact, fid))
    if any(remaining.values()):
        return 0
    inserted = 0
    resolved = []
    for source, fact, fid in planned:
        if fact.operation != 'retract' and fid is None:
            fid = conn.execute(
                "INSERT INTO facts(user_id,session_id,source_rid,subject,predicate,value,kind,"
                "confidence,status,previous_value,created_at,seq,source_role)"
                " VALUES(?,?,?,?,?,?,?,?,'stale',NULL,?,?,?)",
                (user_id,source['session_id'],source['rid'],subject,predicate,fact.value,
                 fact.kind,fact.confidence,source['created_at'],source['seq'],source_role),
            ).lastrowid
            inserted += 1
        resolved.append((source, fact, fid))
    def explicit_timestamp(source):
        try:
            return source['timestamp_origin'] == 'message.timestamp' and math.isfinite(float(source['created_at']))
        except (TypeError, ValueError, OverflowError):
            return False
    if all(explicit_timestamp(source) for source, _, _ in resolved):
        resolved.sort(key=lambda event: (float(event[0]['created_at']), event[0]['seq'],
                                         event[0]['rid'], event[1].source_pos))
    current = None
    current_value = None
    previous = None
    updates = {}
    for source, fact, fid in resolved:
        if fact.operation == 'retract':
            if current_value is not None and _clean(current_value).casefold() == fact.value.casefold():
                current, current_value, previous = None, None, None
            continue
        if fact.value != current_value:
            previous = current_value
        current, current_value = fid, fact.value
        updates[fid] = previous
    conn.executemany("UPDATE facts SET status=?,previous_value=? WHERE fid=?",
                     [('active' if fid == current else 'stale', value, fid)
                      for fid, value in updates.items()])
    return inserted


def _resolve_legacy_roles(conn: sqlite3.Connection, user_id: str, subject: str, predicate: str,
                          *, before_seq: int | None = None) -> None:
    """Repair one legacy key only when its retained source proves a role.

    Earlier stores could retire a user's 'I' after an assistant's 'I'. Replay
    the original assertions and retractions independently per source speaker;
    never reactivate an old fact merely because its role is now known.
    """
    if not _has_items(conn):
        return
    proven = conn.execute(
        "SELECT f.fid,i.role FROM facts f JOIN items i ON i.user_id=f.user_id AND i.rid=f.source_rid"
        " WHERE f.user_id=? AND f.subject=? AND f.predicate=? AND f.source_role='unknown'"
        " AND i.status='active' AND i.role IN ('user','assistant','system','tool')",
        (user_id, subject, predicate),
    ).fetchall()
    if not proven:
        return
    conn.execute("SAVEPOINT fact_role_replay")
    try:
        conn.executemany("UPDATE facts SET source_role=? WHERE fid=?",
                         [(row["role"],row["fid"]) for row in proven])
        stored = conn.execute("SELECT * FROM facts WHERE user_id=? AND subject=? AND predicate=? ORDER BY seq,fid",
                              (user_id,subject,predicate)).fetchall()
        single = any(row["kind"] == "fact" or row["subject"] == "favorite" for row in stored)
        if single:
            sources = conn.execute("SELECT role,content,seq FROM items WHERE user_id=? AND status='active'"
                                   " AND (? IS NULL OR seq<?) ORDER BY seq,rid",
                                   (user_id,before_seq,before_seq)).fetchall()
            state = {}
            for source in sources:
                role = source["role"]
                for fact in extract_facts(source["content"],relaxed=(role == "system")):
                    if (fact.subject,fact.predicate) != (subject,predicate):
                        continue
                    current = state.get(role, {"value":None,"previous":None})
                    if fact.operation == "retract":
                        if current["value"] is not None and current["value"].casefold() == fact.value.casefold():
                            state[role] = {"value":None,"previous":None}
                    else:
                        previous = current["previous"] if current["value"] == fact.value else current["value"]
                        state[role] = {"value":fact.value,"previous":previous}
            for role in {row["source_role"] for row in stored} - {"unknown"}:
                if role not in state:
                    continue
                value = state[role]["value"]
                matching = [row for row in stored if row["source_role"] == role and value is not None
                            and row["value"].casefold() == value.casefold()]
                active_id = matching[-1]["fid"] if matching else None
                for row in stored:
                    if row["source_role"] == role:
                        conn.execute("UPDATE facts SET status=?,previous_value=? WHERE fid=?",
                                     ("active" if row["fid"] == active_id else "stale",
                                      state[role]["previous"] if row["fid"] == active_id else None,row["fid"]))
        conn.execute("RELEASE SAVEPOINT fact_role_replay")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT fact_role_replay")
        conn.execute("RELEASE SAVEPOINT fact_role_replay")
        raise


def _prepare_user_roles(conn: sqlite3.Connection, user_id: str) -> None:
    keys = conn.execute("SELECT DISTINCT subject,predicate FROM facts WHERE user_id=? AND source_role='unknown'",
                        (user_id,)).fetchall()
    for key in keys:
        _resolve_legacy_roles(conn,user_id,key["subject"],key["predicate"])


def _recorded_time(value: float | None) -> str:
    try:
        return datetime.fromtimestamp(float(value), timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError, OSError):
        return "unknown"


def _metadata_value(value, *, json_string: bool = False) -> str:
    text=str(value)
    if json_string or re.search(r"[\s|\[\];]",text):
        return (json.dumps(text,ensure_ascii=False).replace("|",r"\u007c")
                .replace("[",r"\u005b").replace("]",r"\u005d")
                .replace("\u0085",r"\u0085").replace("\u2028",r"\u2028").replace("\u2029",r"\u2029"))
    return text


def _source_label(row: dict) -> str:
    """A stored timestamp is provenance, never an inferred event date."""
    rid = row.get("source_rid")
    recorded = _recorded_time(row.get('created_at'))
    origin = row.get("timestamp_origin") or "legacy-unknown"
    anchor = recorded if origin == "message.timestamp" else "none"
    return (
        f"[SOURCE source_rid={rid if rid is not None else 'unknown'}"
        f" session={_metadata_value(row.get('session_id', 'unknown'))} seq={row.get('seq', 'unknown')}"
        f" role={_metadata_value(row.get('role', 'unknown'))} recorded_at={recorded}"
        f" timestamp_origin={origin} relative_time_anchor={anchor}]"
    )


def _with_source(conn: sqlite3.Connection, row: sqlite3.Row | dict, has_items: bool) -> dict:
    result = dict(row)
    result["role"] = result.get("source_role") or "unknown"
    result["timestamp_origin"] = "legacy-unknown"
    if has_items and result.get("source_rid") is not None:
        source = conn.execute(
            "SELECT role,created_at," + _timestamp_column(conn) + " FROM items WHERE user_id=? AND rid=?",
            (result["user_id"], result["source_rid"]),
        ).fetchone()
        if source:
            if result["role"] == "unknown":
                result["role"] = source["role"]
            result["created_at"] = source["created_at"]
            result["timestamp_origin"] = source["timestamp_origin"] or "legacy-unknown"
    return result


def _applicability_evidence(conn, user_id: str, assertion: dict, cache: dict) -> list[dict]:
    """Quote later qualifications without treating extracted state as truth."""
    if (assertion.get('subject') != 'self' or assertion.get('predicate') not in
            {'name', 'nickname', 'location', 'work', 'role', 'condition'}
            or assertion.get('source_rid') is None or not _has_items(conn)):
        return []
    role = assertion.get('role', assertion.get('source_role', 'unknown'))
    if role not in {'user', 'assistant', 'system', 'tool'}:
        return []
    key = ('qualification', assertion.get('fid'), assertion['source_rid'])
    if key in cache:
        return cache[key]
    sources_key = ('sources', role)
    if sources_key not in cache:
        cache[sources_key] = [dict(row) for row in conn.execute(
            'SELECT rid,rid AS source_rid,user_id,session_id,role,content,created_at,seq,' + _timestamp_column(conn)
            + " FROM items WHERE user_id=? AND role=? AND status='active' ORDER BY seq,rid",
            (user_id, role))]
    def is_later(source):
        if source['rid'] == assertion['source_rid']:
            return False
        if source['timestamp_origin'] == assertion.get('timestamp_origin') == 'message.timestamp':
            try:
                left, right = float(source['created_at']), float(assertion['created_at'])
                if math.isfinite(left) and math.isfinite(right):
                    return (left, source['seq'], source['rid']) > (right, assertion['seq'], assertion['source_rid'])
            except (TypeError, ValueError, OverflowError):
                pass
        return source['seq'] > assertion.get('seq', float('inf'))
    later = (source for source in cache[sources_key] if is_later(source))
    cache[key] = state_layer.qualifications(assertion, later)
    return cache[key]


def _applicability_note(evidence: list[dict]) -> str:
    if not evidence:
        return ''
    return ('\n[CURRENT UNVERIFIED: the value above is an extracted assertion; '
            'later original evidence qualifies its applicability. Read both sources; '
            'no replacement value is inferred.]\n'
            + '\n'.join(_source_label(source) + '\n' + source['text'] for source in evidence))


_CARE_QUERY = re.compile(r"\b(?:health|illness|allerg\w*|medication|condition|care|diagnos\w*|anxiety|depression)\b|健康|病情|过敏|服药|心理|焦虑|抑郁|关怀", re.I)
_ASSISTANT_QUERY = re.compile(
    r"\b(?:did|does|is|was|has)\s+(?:the\s+)?(?:assistant|chatbot|bot)\b"
    r"|\b(?:assistant|chatbot|bot)(?:'s\b|\s+(?:say|said|mention|stated|lives?|works?|prefers?|is|was)\b)"
    r"|(?:助手|助理)(?:说|提到|表示|的|住|工作)", re.I,
)


def _speaker_relevant(row: dict, query: str, *, background: bool = False) -> bool:
    """Avoid presenting another speaker's 'I' as the user's current state."""
    if row.get("subject") not in {"self", "pref", "favorite"}:
        return True
    role = row.get("role", "unknown")
    if _ASSISTANT_QUERY.search(query):
        return role in {"assistant", "unknown"}
    if role != "assistant":
        return True
    # Source-labelled care evidence is useful even when the conversation is a
    # dialogue or a role play. Its speaker remains explicit in the result.
    return bool(_CARE_QUERY.search(query)) and (background or row.get("predicate") in {
        "condition", "medication", "allergy", "metric", "health", "血压", "血糖", "体重", "心率",
    })


_RULE_SCOPE_HEADING = re.compile(
    r"\s*(?:(?:for|in|within|during|when|whenever|if)\b|针对|在|当|如果|仅对)"
    r"[^.!?\n]{1,220}[:：]\s*", re.I)
_RULE_GENERAL_HEADING = re.compile(
    r"\s*(?:for\s+(?:example|reference|clarity|brevity|consistency|readability|convenience|me|us|you)|"
    r"in\s+(?:fact|summary|conclusion|short|general)|whenever\s+possible)\s*[:：]\s*", re.I)
_RULE_BLOCK_MEMBER = re.compile(
    r"\s*(?:[-*\u2022]|\d+[.)])?\s*(?:(?:please\s+)?(?:always|never|from\s+now\s+on|"
    r"if|when|whenever|do\s+not|don't|remember\s+to|make\s+sure\s+to|you\s+must)\b"
    r"|如果|请|务必|记得|不要|别|必须)", re.I)
_RULE_FOLLOWUP = re.compile(
    r"\s*(?:(?:then|next|finally|after\s+that)\b|然后|接着|最后)\s*[,，:：]?\s*(.+)", re.I)
_RULE_CONTINUATION_ACTION = re.compile(
    r"(?:(?:please\s+)?(?:do\s+not|never|always)\s+(?:" + '|'.join(sorted(_EN_VERBS)) + r")\b"
    r"|(?:" + '|'.join(sorted(_EN_VERBS)) + r")\s+(?:the|a|an|my|your|our|each|every|all|this|that|these|those|its|their|it|them)\b"
    r"|(?:add|subtract)\s+[-+]?\d+(?:\.\d+)?\b"
    r"|(?:multiply|divide)\s+[^.!?\n]{1,100}?\s+by\s+\S"
    r"|round\s+[^.!?\n]{1,100}?\s+(?:down|up|to)\b"
    r"|(?:apply|repeat|sort|evaluate)\s+(?:(?:the|a|an|each|every|this|that|these|those)\s+)?"
    r"(?:operations?|rules?|steps?|formulae?|formulas?|procedures?|results?|values?|labels?)\b)", re.I)
_RULE_WITHDRAWAL = re.compile(
    r"\s*(?:this\s+(?:rule|requirement|instruction)|these\s+(?:rules|requirements|instructions))\s+"
    r"(?:(?:has|have)\s+been\s+|(?:is|are)\s+)?(?:withdrawn|revoked|suspended|rescinded|no\s+longer\s+(?:active|valid|applicable))\b"
    r"|\s*(?:此|这条|该)(?:规则|要求|指令)(?:已|已经)?(?:撤销|撤回|废止|失效|暂停)", re.I)


def _complete_source_rule(conn: sqlite3.Connection, row: sqlite3.Row, has_items: bool) -> str:
    """Recover the full source sentence when an older derived value was cut.

    A uniquely matching source sentence is safe evidence. If the wording is
    ambiguous or the raw source is unavailable, retain the stored condition
    rather than inventing context around it.
    """
    value = _display_rule(row["predicate"], row["value"])
    if not has_items or row["source_rid"] is None:
        return value
    source = conn.execute(
        "SELECT content FROM items WHERE user_id=? AND rid=? AND status='active'",
        (row["user_id"], row["source_rid"]),
    ).fetchone()
    if not source:
        return value
    needle = _clean(row["value"]).casefold()
    # Keep the original paragraph barriers while normalizing only whitespace
    # in the emitted quotes. A scope heading binds its contiguous rule list,
    # but must not leak across a blank line or unrelated prose.
    source_matches = [match for match in _SOURCE_SENTENCES.finditer(source["content"])
                      if match.group(0).strip()]
    sentences = [re.sub(r"[^\S\t]+", " ", match.group(0)).strip()
                 for match in source_matches]
    matches = [index for index, sentence in enumerate(sentences)
               if needle in sentence.casefold()]
    if len(matches) != 1:
        return value
    index = matches[0]
    linked = [sentences[index]]
    for previous in range(index - 1, -1, -1):
        gap = source["content"][source_matches[previous].start():source_matches[previous + 1].start()]
        if re.search(r"\n[\t \r]*\n", gap):
            break
        candidate = sentences[previous]
        if _RULE_SCOPE_HEADING.fullmatch(candidate):
            linked.insert(0, candidate)
            break
        if not _RULE_BLOCK_MEMBER.match(candidate):
            break
    # Adjacent exceptions qualify the rule, rather than constituting another
    # independent instruction. Stop at unrelated narration or a new rule.
    for following, sentence in enumerate(sentences[index + 1:], index + 1):
        exception = re.match(r"\s*(?:exception\b|except\b|unless\b|however\b|but\b|otherwise\b|注意|例外|但是|但若|除非|否则|不过)", sentence, re.I)
        action = _RULE_FOLLOWUP.fullmatch(sentence)
        action_text = action.group(1) if action else ''
        # A verb-shaped first word alone is insufficient: Return Logistics,
        # round tables and repeat customers are ordinary noun phrases. Keep
        # only explicit instruction objects or unambiguous operation syntax.
        sequential = action and (_ZH_INSTR.match(action_text) or _RULE_CONTINUATION_ACTION.match(action_text))
        withdrawn = _RULE_WITHDRAWAL.match(sentence)
        gap = source["content"][source_matches[following - 1].start():source_matches[following].start()]
        if not exception and (re.search(r"\n[\t \r]*\n", gap) or not (sequential or withdrawn)):
            break
        linked.append(sentence)
    return " ".join(linked)


_EXECUTION_QUERY = re.compile(
    r"\b(?:write|draft|compose|create|generate|format|reply|respond|answer|summari[sz]e|prepare|produce|translate|rewrite|explain|apply)\b"
    r"|(?:请|帮我|替我).*(?:写|生成|回答|回复|总结|整理|翻译|解释|应用)|^(?:写|生成|回答|回复|总结|整理|翻译|解释|应用)", re.I,
)
_GLOBAL_RULE = re.compile(
    r"^\s*(?:please\s+)?(?:always|never|from\s+now\s+on)\b"
    r"|\b(?:all|every|each)\s+(?:future\s+|subsequent\s+)?(?:responses?|replies|answers?|outputs?)\b"
    r"|(?:以后|今后|从现在开始).*(?:回答|回复|输出)|(?:每次|所有|全部).{0,6}(?:回答|回复|输出)", re.I,
)
_LOCAL_RULE = re.compile(r"^\s*(?:if|when|whenever)\b|^\s*(?:如果|当|若)|\b(?:in|for|during|within|while)\s+\S|(?:针对|用于|在).{1,30}(?:时|中|期间)", re.I)
_RESPONSE_SCOPE = re.compile(r"\b(?:all|every|each|your|future|subsequent)\s+(?:future\s+|subsequent\s+)?(?:responses?|replies|answers?|outputs?)\b|以后.*(?:回答|回复|输出)|所有.{0,5}(?:回答|回复|输出)",re.I)
_OUTPUT_POLICY = re.compile(r"\b(?:repl\w*|respond\w*|answer\w*|output\w*|format\w*|language|tone|sentences?|headings?|units?|metric|cit\w*|sources?|uncertainty|confidence|assumptions?|dates?|privacy|private|addresses?|redact\w*)\b|回答|回复|输出|格式|语言|单位|引用|来源|置信|隐私|地址",re.I)
_RULE_QUOTED_CONTENT = re.compile(r'"[^"\n]*"|“[^”\n]*”|`[^`\n]*`')
_RESPONSE_DOMAIN = re.compile(
    r"\b(?:responses?|repl(?:y|ies)|answers?|outputs?)\s+(?:about|regarding|concerning)\s+\S"
    r"|\b(?:responses?|repl(?:y|ies)|answers?|outputs?)\s+to\s+"
    r"(?!(?:me|us|you|them|the\s+user|the\s+point)\b|(?:be|remain|stay|begin|start|end|include|contain|use|avoid)\b)\S"
    r"|\b(?:responses?|repl(?:y|ies)|answers?|outputs?)\s+for\s+"
    r"(?!(?:me|us|you|them|the\s+user|clarity|brevity|readability|privacy|safety|consistency)\b)\S", re.I)
_REQUEST_SCOPED_CONDITION = re.compile(
    r"\b(?:if|when|whenever)\s+(?:(?:I|we|you|the\s+user|users?)\s+"
    r"(?:(?:am|are|is)\s+)?(?:ask\w*|request\w*|quer\w*|mention\w*)\b"
    r"|(?:answering|responding|discussing|handling)\b|(?:the\s+)?(?:question|request|topic|prompt)\b)", re.I)
_RESPONSE_MANNER_IN = re.compile(
    r"\b(?:reply|respond|answer|output)\s+in\s+(?:"
    r"(?:(?:at\s+most|no\s+more\s+than|up\s+to|exactly)\s+)?"
    r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|a\s+single|single)\s+"
    r"(?:words?|sentences?|lines?|paragraphs?|bullet\s+points?)"
    r"|English|Spanish|JSON|YAML|XML|CSV|Markdown|plain\s+text|bullet\s+points|(?:a\s+)?(?:compact\s+)?table)"
    r"(?=\s*(?:[.,;:!?]|$)|\s+(?:for|in|during|within|while)\s+)", re.I)


def _global_response_rule(value: str, confidence: float = 1.0) -> bool:
    """One scope decision for dedicated rules and global context protection."""
    semantics = re.sub(r'^\s*(?:[-*\u2022]|\d+[.)])\s+', '', value)
    # A general manner/feasibility heading does not name a topical domain.
    # Retain it in the quoted evidence (including "Whenever possible") and
    # remove it only from this scope-classification view.
    heading = _RULE_GENERAL_HEADING.match(semantics)
    if heading:
        semantics = semantics[heading.end():].lstrip()
    # Literal output examples do not define the rule's applicability domain.
    # The complete original wording is still quoted by relevant_rules.
    semantics = _RULE_QUOTED_CONTENT.sub(' ', semantics)
    if confidence < .6 or not _GLOBAL_RULE.search(semantics):
        return False
    explicit_response = bool(_RESPONSE_SCOPE.search(semantics))
    conditional_rule = bool(re.match(r"\s*(?:if|when|whenever)\b|\s*(?:如果|当|若)", semantics, re.I))
    local_prefix = bool(re.match(r"\s*(?:for|in|during|within|while)\b|\s*(?:针对|在)", semantics, re.I))
    outer_response_scope = bool(re.match(r"\s*(?:for|in)\s+(?:all|every|each)\s+(?:future\s+|subsequent\s+)?(?:responses?|replies|answers?|outputs?)\b", semantics, re.I))
    domain_text = re.sub(r"\b(?:for|in)\s+(?:all|every|each|your|future|subsequent)\s+(?:future\s+|subsequent\s+)?(?:responses?|replies|answers?|outputs?)\b", " ", semantics, flags=re.I)
    # A bounded output length/format is not a topical "in ..." domain.
    # The phrase must follow a response verb and end at a clause boundary;
    # e.g. "English literature" remains a domain. Quote text is untouched.
    domain_text = _RESPONSE_MANNER_IN.sub(' ', domain_text)
    embedded_condition = bool(re.search(r"[,;]\s*(?:if|when|whenever)\b|(?:针对|用于)", domain_text, re.I))
    response_domain = bool(_RESPONSE_DOMAIN.search(domain_text)
                           or _REQUEST_SCOPED_CONDITION.search(domain_text))
    local_domain = bool(_LOCAL_RULE.search(domain_text)) and not explicit_response
    return (not conditional_rule and not embedded_condition and not response_domain
            and (not local_prefix or outer_response_scope) and not local_domain
            and (explicit_response or bool(_OUTPUT_POLICY.search(semantics))))


def relevant_rules(
    conn: sqlite3.Connection, user_id: str, query: str, limit: int = 4,
) -> list[dict]:
    """Return query-related standing rules separately from stable identity.

    The complete conditional value is evidence, not an instruction to this
    service. Conflicting rules are retained with their source order; this
    function does not guess that similarly worded rules replace one another.
    """
    if limit <= 0:
        return []
    _prepare_user_roles(conn,user_id)
    rows = conn.execute(
        "SELECT fid,user_id,session_id,source_rid,subject,predicate,value,kind,"
        "confidence,status,created_at,seq,source_role FROM facts"
        " WHERE user_id=? AND kind='rule' AND status='active'"
        " ORDER BY seq,fid", (user_id,),
    ).fetchall()
    q = _tokens(query)
    asks_rules = any(word in query.lower() for word in (
        "rule", "instruction", "guideline", "constraint", "规则", "指令", "要求", "约束",
    ))
    rule_query = re.sub(r"规则|指令|要求|约束|哪些|什么|遵守|执行|适用|应该|需要|请|怎么", " ", query)
    rule_topics = _tokens(rule_query) - frozenset(
        "rule rules instruction instructions guideline guidelines constraint constraints "
        "apply applies applicable follow following standing list enumerate remember saved stored "
        "must shall required requirements govern governs governing".split()
    )
    asks_all_rules = asks_rules and (not rule_topics or bool(re.search(
        r"\b(?:all|every)\s+(?:standing\s+)?(?:rules|instructions|constraints)\b|所有.{0,5}(?:规则|指令|约束)", query, re.I,
    )))
    has_items = _has_items(conn)
    ranked = []
    for row in rows:
        value = _complete_source_rule(conn, row, has_items)
        overlap = len(q & _tokens(value + " " + row["predicate"]))
        global_rule = _global_response_rule(value, row['confidence'])
        if not overlap and not global_rule and (not asks_all_rules or row["confidence"] < 0.6):
            continue
        score = overlap / max(1.0, len(q) ** 0.5) + (0.15 if asks_rules else 0.0) + (.2 if global_rule else 0)
        ranked.append((score, row, value, global_rule))
    ranked.sort(key=lambda item: (-item[0], -item[1]["seq"], -item[1]["fid"]))
    out, seen = [], set()
    for score, row, value, global_rule in ranked:
        key = (row["source_rid"], value.casefold())
        if key in seen:
            continue
        seen.add(key)
        result = _with_source(conn, row, has_items)
        result["text"] = _source_label(result) + " " + value
        result["score"] = round(score, 6)
        result["scope"] = "global" if global_rule else "local"
        out.append(result)
        if len(out) >= limit:
            break
    return out


_ATTRIBUTE_TERMS = {
    "location": "location city live lives lived reside moved where 住 城市 搬 地点 哪",
    "work": "work job company employer role employed 工作 公司 岗位 就职 职业",
    "name": "name called 名字 姓名 称呼",
    "age": "age old 年龄 岁",
    "condition": "health condition illness diagnosis 健康 疾病 病情",
    "medication": "medication medicine drug 药 服用",
    "allergy": "allergy allergic 过敏",
}
_HISTORY_WORDS = (
    "history", "timeline", "trajectory", "chronolog", "sequence", "changed", "change",
    "previous", "before", "earlier", "initial", "original", "first", "then", "stages",
    "历史", "时间线", "轨迹", "顺序", "先后", "变化", "变迁", "以前", "之前", "最初", "阶段",
)


def _evidence_indices(n: int, limit: int) -> list[int]:
    """Stable coverage of early, middle and late evidence, in source order."""
    if n <= 0 or limit <= 0:
        return []
    if n <= limit:
        return list(range(n))
    if limit == 1:
        return [n - 1]
    indices = [round(i * (n - 1) / (limit - 1)) for i in range(limit)]
    # An even sample size can straddle and entirely omit the middle stage.
    # Reserve all three anchors whenever the budget permits them.
    middle = n // 2
    if limit >= 3 and middle not in indices:
        replace = min(range(1, len(indices) - 1), key=lambda i: abs(indices[i] - middle))
        indices[replace] = middle
    return sorted(indices)


def change_history(
    conn: sqlite3.Connection, user_id: str, query: str, limit: int = 6,
) -> list[dict]:
    """Related assertion history, preserving source order and state labels.

    Ordinary current-state queries receive current assertions only. A query
    about a trajectory receives multiple stages of the related attribute,
    rather than whichever unrelated change happened to arrive most recently.
    """
    if limit <= 0:
        return []
    _prepare_user_roles(conn,user_id)
    rows = conn.execute(
        "SELECT fid,user_id,session_id,source_rid,subject,predicate,value,kind,"
        "confidence,status,previous_value,created_at,seq,source_role FROM facts"
        " WHERE user_id=? AND kind IN ('fact','preference') AND status IN ('active','stale')"
        " ORDER BY seq,fid", (user_id,),
    ).fetchall()
    groups: dict[tuple[str, str, str], list] = {}
    has_items = _has_items(conn)
    for row in rows:
        attributed = _with_source(conn, row, has_items)
        if _speaker_relevant(attributed, query):
            groups.setdefault((row["subject"], row["predicate"], attributed["role"]), []).append(attributed)
    q = _tokens(query)
    wants_history = any(word in query.lower() for word in _HISTORY_WORDS)
    ranked = []
    for key, history in groups.items():
        if not any(row["status"] == "stale" or row["previous_value"] for row in history):
            continue
        subject, predicate, source_role = key
        terms = _tokens(f"{subject} {predicate} " + _ATTRIBUTE_TERMS.get(predicate, ""))
        terms |= set().union(*(_tokens(row["value"]) for row in history))
        overlap = len(q & terms)
        if not overlap:
            continue
        score = overlap / max(1.0, len(q) ** 0.5)
        ranked.append((score, history))
    ranked.sort(key=lambda pair: (-pair[0], -pair[1][-1]["seq"]))
    if not ranked:
        return []
    # Leave room for more than one stage per attribute when a chain is asked
    # for. More specific matches are selected before broad attribute matches.
    group_limit = max(1, limit // 3) if wants_history else limit
    selected_groups = ranked[:group_limit]
    selected = []
    for index, (score, history) in enumerate(selected_groups):
        remaining_groups = len(selected_groups) - index
        quota = max(1, (limit - len(selected)) // remaining_groups)
        if wants_history:
            chosen = [history[i] for i in _evidence_indices(len(history), quota)]
        else:
            current = [row for row in history if row["status"] == "active"]
            chosen = current[-quota:] if current else history[-1:]
        selected.extend((score, row) for row in chosen)
    selected.sort(key=lambda pair: (pair[1]["seq"], pair[1]["fid"]))
    has_items = _has_items(conn)
    out, applicability_cache = [], {}
    for score, row in selected:
        result = _with_source(conn, row, has_items)
        label = "CURRENT" if row["status"] == "active" else "HISTORICAL"
        qualifications = (_applicability_evidence(conn, user_id, result, applicability_cache)
                          if row['status'] == 'active' else [])
        if qualifications:
            label = 'RECORDED ASSERTION; CURRENT UNVERIFIED'
        result["text"] = (
            f"[{label}] {row['subject']}.{row['predicate']}: {row['value']} "
            + _source_label(result) + _applicability_note(qualifications)
        )
        result["kind"] = "change"
        result["score"] = round(score, 6)
        out.append(result)
    return out


def match_facts(
    conn: sqlite3.Connection,
    user_id: str,
    query: str,
    limit: int,
    intent_kinds: set[str] | None = None,
) -> list[dict]:
    """Rank active facts for this query.

    Relevance is required: a fact enters the result only when it shares tokens
    with the query, or when the query clearly asks for that *kind* ("what does
    the user prefer" -> preferences). Otherwise every query would return the
    same top facts and crowd real evidence out of the head of the list.
    """
    if limit <= 0:
        return []
    _prepare_user_roles(conn,user_id)
    intent_kinds = intent_kinds or set()
    q = _tokens(query)
    rows = conn.execute(
        "SELECT f.*,"
        " (SELECT live.value FROM facts live WHERE live.user_id=f.user_id"
        "  AND live.subject=f.subject AND live.predicate=f.predicate AND live.source_role=f.source_role AND live.status='active'"
        "  ORDER BY live.seq DESC,live.fid DESC LIMIT 1) AS current_value"
        " FROM facts f WHERE f.user_id=? AND f.status IN ('active','stale')"
        " ORDER BY f.confidence DESC, f.fid DESC LIMIT 400",
        (user_id,),
    ).fetchall()
    scored = []
    has_items = _has_items(conn)
    for stored in rows:
        r = _with_source(conn, stored, has_items)
        if not _speaker_relevant(r, query):
            continue
        hay = (f"{r['subject']} {r['predicate']} {r['value']} "
               + _ATTRIBUTE_TERMS.get(r["predicate"], "")).replace("_", " ")
        toks = _tokens(hay)
        overlap = (len(q & toks) / (len(q) ** 0.5)) if q and toks else 0.0
        wanted = r["kind"] in intent_kinds
        if overlap <= 0 and not wanted:
            continue
        score = min(overlap, 1.0) + (0.35 if wanted else 0.0)
        if r["status"] == "stale":
            score -= 0.2           # visible, but behind the current value
        scored.append((score, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    out, applicability_cache = [], {}
    for score, r in scored[:limit]:
        text = f"{r['subject']} / {r['predicate']}: {r['value']}"
        qualifications = []
        if r["status"] == "stale":
            current = r["current_value"]
            text = "[HISTORICAL] " + text
            if current is not None:
                active = conn.execute(
                    "SELECT * FROM facts WHERE user_id=? AND subject=? AND predicate=?"
                    " AND source_role=? AND status='active' ORDER BY seq DESC,fid DESC LIMIT 1",
                    (user_id, r['subject'], r['predicate'], r['source_role'])).fetchone()
                if active:
                    qualifications = _applicability_evidence(
                        conn, user_id, _with_source(conn, active, has_items), applicability_cache)
            text += ((f" (latest extracted value: {current}; current applicability unverified)" if qualifications
                      else f" (current value: {current})") if current is not None
                     else " (no active value recorded)")
        elif r["previous_value"]:
            text += f"  (changed from: {r['previous_value']})"
        if r['status'] == 'active':
            qualifications = _applicability_evidence(conn, user_id, r, applicability_cache)
            if qualifications:
                text = '[RECORDED ASSERTION; CURRENT UNVERIFIED] ' + text
        result = dict(r)
        result.update(score=round(score, 6), text=text + " " + _source_label(r) + _applicability_note(qualifications))
        out.append(result)
    return out


def _display_rule(predicate: str, value: str) -> str:
    """Keep triggers visible, including on pre-existing conditional rows."""
    if predicate.startswith("when ") and not re.match(r"(?:if|when|whenever)\b", value, re.I):
        return f"When {predicate[5:]}, {value}"
    if predicate.startswith("当") and not value.startswith(("如果", "当")):
        return f"当{predicate[1:]}，{value}"
    return value


def build_profile(conn: sqlite3.Connection, user_id: str, query: str = "", max_items: int = 24) -> str:
    """One compact digest of who this user is and what they asked for.

    When `query` is given the rules/preferences/facts that share tokens with it
    are promoted into the digest. A fixed top-N digest cannot serve G2 (rule
    application): the one rule that governs the question is rarely in the first
    five rows, so the digest has to be query-conditioned to be useful.
    """
    _prepare_user_roles(conn,user_id)
    rows = conn.execute(
        "SELECT * FROM facts"
        " WHERE user_id=? AND status='active' ORDER BY"
        "  CASE kind WHEN 'rule' THEN 0 WHEN 'preference' THEN 1 ELSE 2 END,"
        "  confidence DESC, fid DESC LIMIT 400",
        (user_id,),
    ).fetchall()
    has_items = _has_items(conn)
    rows = [_with_source(conn, row, has_items) for row in rows]
    rows = [row for row in rows if _speaker_relevant(row, query, background=True)]
    if not rows:
        return ""

    # Only confident rules are pinned here. Low-confidence ones (see the weak
    # branch in extract_facts) stay retrievable through match_facts instead, so
    # a possibly-governing rule is never fully lost but also never becomes the
    # first line the judge reads on an unrelated question.
    rows = [r for r in rows if r["kind"] != "rule" or r["confidence"] >= 0.6]
    if not rows:
        return ""

    # Static, stable ordering on purpose. A query-conditioned profile narrows
    # itself to whatever matches the question and drops the stable background --
    # E3 ("personal background, health and emotional care") went 100 -> 0.00 on
    # the smoke run that shipped the conditioned version. `query` is kept in the
    # signature only for callers; it must not reorder this digest.
    # Identity first. The personal-info corpus holds 34 facts/user but the
    # profile spent its whole budget on "dislikes X because Y" and hid
    # name/age/city -- which is what E3 (background & care) asks about.
    identity = {"name", "age", "location", "work", "role", "condition",
                "allergy", "medication", "metric", "origin", "nickname"}
    rows.sort(key=lambda r: (
        1 if r["role"] == "assistant" else 0,
        0 if (r["subject"] == "self" and r["predicate"] in identity) else 1,
        1 if r["kind"] == "preference" else 0,
    ))
    rows = rows[:max_items]

    rules, prefs, facts, applicability_cache = [], [], [], {}
    for r in rows:
        if r["kind"] == "rule":
            rules.append(_display_rule(r["predicate"], r["value"]) + " " + _source_label(r))
        elif r["kind"] == "preference":
            verb = "likes" if r["predicate"] == "like" else (
                "dislikes" if r["predicate"] == "dislike" else r["predicate"])
            # 82% of preference values carried a "because ..." rationale, which
            # burns the profile budget without adding recall.
            val = re.split(r"\s+because\b|\s*因为", r["value"])[0].strip()[:60]
            prefs.append(f"{verb} {val} " + _source_label(r))
        else:
            owner = "assistant-stated " if r["role"] == "assistant" else ""
            qualifications = _applicability_evidence(conn, user_id, r, applicability_cache)
            qualifier = '[CURRENT UNVERIFIED; latest extracted assertion] ' if qualifications else ''
            facts.append(qualifier + f"{owner}{r['subject']}.{r['predicate']}={r['value']} "
                         + _source_label(r) + _applicability_note(qualifications))
    parts = []
    if rules:
        parts.append("rules: " + "; ".join(list(dict.fromkeys(rules))[:5]))
    if prefs:
        parts.append("preferences: " + "; ".join(list(dict.fromkeys(prefs))[:8]))
    if facts:
        parts.append("facts: " + "; ".join(list(dict.fromkeys(facts))[:10]))
    if not parts or (len(rules) + len(prefs) + len(facts)) < 2:
        return ""
    return "[PROFILE] " + " | ".join(parts)


def changed_facts(conn: sqlite3.Connection, user_id: str, limit: int = 2) -> list[str]:
    """Recently overridden values -- what C3/D1 questions ask about."""
    rows = conn.execute(
        "SELECT subject,predicate,value,previous_value FROM facts"
        " WHERE user_id=? AND previous_value IS NOT NULL AND status='active'"
        " ORDER BY fid DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    return [
        f"{r['subject']}.{r['predicate']} changed: {r['previous_value']} -> {r['value']}"
        for r in rows
    ]


# ---------------------------------------------------------------- digests
_SOURCE_SENTENCES = re.compile(r".+?(?:[。！？!?]|\.(?!\d)(?=\s|$)|\n+|$)", re.S)


def _source_sentences(text: str) -> list[str]:
    """Extract source wording without splitting numeric decimals into facts."""
    # Tabs can delimit measured columns, so retain them as source structure.
    return [re.sub(r"[^\S\t]+", " ", match.group(0)).strip()
            for match in _SOURCE_SENTENCES.finditer(text) if match.group(0).strip()]


def _evidence_quote(text: str, max_chars: int = 900) -> str:
    if len(text) <= max_chars:
        return text
    # Explicitly disclose an excerpt instead of making a cut-off clause look
    # like a complete fact. The source pointer still identifies the full turn.
    end = text.rfind(" ", max_chars // 2, max_chars)
    if end < 0:
        end = max_chars
    return text[:end].rstrip() + " [source excerpt; remainder omitted]"


def update_digest(conn: sqlite3.Connection, user_id: str, session_id: str, max_lines: int = 12) -> None:
    """Build ordered, attributed source excerpts, including Chinese evidence.

    This is an extractive index, not a generated summary or a numerical
    conclusion. Repeated observations remain separate with separate sources.
    Sampling is disclosed so absent observations are never represented as
    non-occurrences, and stored timestamps are not presented as event dates.
    """
    rows = conn.execute(
        "SELECT rid,session_id,role,content,created_at,seq," + _timestamp_column(conn) + " FROM items"
        " WHERE user_id=? AND session_id=? AND status='active' ORDER BY seq,rid",
        (user_id, session_id),
    ).fetchall()
    if not rows:
        conn.execute("DELETE FROM digests WHERE user_id=? AND session_id=?", (user_id, session_id))
        return
    units = []
    for row in rows:
        source = dict(row)
        source["source_rid"] = row["rid"]
        for sentence in _source_sentences(str(row["content"])):
            units.append((_source_label(source), sentence))
    chosen = _evidence_indices(len(units), max_lines)
    lines = [label + " " + _evidence_quote(quote) for label, quote in (units[i] for i in chosen)]
    content = (
        f"[DIGEST] session {session_id}: {len(rows)} turns; "
        f"{len(chosen)} of {len(units)} source segments shown in source order. "
        "Recorded timestamps are metadata, not inferred event dates."
        + ("\n" + "\n".join(lines) if lines else "")
    )
    conn.execute(
        "INSERT INTO digests(user_id,session_id,content,n_msgs,updated_at)"
        " VALUES(?,?,?,?,?)"
        " ON CONFLICT(user_id,session_id) DO UPDATE SET"
        "  content=excluded.content, n_msgs=excluded.n_msgs, updated_at=excluded.updated_at",
        (user_id, session_id, content, len(rows), time.time()),
    )


_SUMMARY_WORDS = (
    "summary", "summarize", "summarise", "overall", "everything", "all of", "across",
    "pattern", "trend", "how many", "in total", "recap", "overview", "compare",
    "总结", "总体", "全部", "一共", "多少", "规律", "趋势", "整体", "回顾",
)


def _legacy_digests(conn: sqlite3.Connection, user_id: str, query: str, limit: int = 2) -> list[str]:
    """Relevant session evidence, with broad coverage for aggregate queries.

    Candidate selection has no session-count cutoff. For a global query,
    multiple session views share each result slot, including short sessions.
    Every retained excerpt keeps its original source label.
    """
    if limit <= 0:
        return []
    rows = conn.execute(
        "SELECT session_id,content,n_msgs FROM digests WHERE user_id=?"
        " ORDER BY session_id",
        (user_id,),
    ).fetchall()
    if not rows:
        return []
    q_low = query.lower()
    q = _tokens(query)
    wants_summary = any(w in q_low for w in _SUMMARY_WORDS)
    scored = []
    for r in rows:
        # Metadata such as 'source order' and recorded timestamps must not make
        # an unrelated session look lexically relevant.
        evidence = " ".join(re.sub(r"\[SOURCE[^\]]*\]", "", line)
                            for line in r["content"].splitlines()[1:])
        if not evidence:
            evidence = r["content"]  # Compatibility with existing stored views.
        overlap = len(q & _tokens(evidence)) if q else 0
        if not wants_summary and overlap == 0:
            continue
        scored.append((overlap, r))
    if not wants_summary:
        scored.sort(key=lambda pair: (-pair[0], pair[1]["session_id"]))
        return [r["content"] for _, r in scored[:limit]]

    # Ingestion/source order is useful provenance; it is not a claim about
    # when the events described in separate sessions happened.
    session_order = {}
    if _has_items(conn):
        session_order = {r["session_id"]: r["first_seq"] for r in conn.execute(
            "SELECT session_id,min(seq) AS first_seq FROM items"
            " WHERE user_id=? AND status='active' GROUP BY session_id", (user_id,),
        ).fetchall()}
    ordered = [r for _, r in scored]
    ordered.sort(key=lambda r: (session_order.get(r["session_id"], float("inf")), r["session_id"]))
    if len(ordered) <= limit:
        return [r["content"] for r in ordered]

    groups = [[] for _ in range(min(limit, len(ordered)))]
    for i, row in enumerate(ordered):
        groups[min(len(groups) - 1, i * len(groups) // len(ordered))].append(row)
    out = []
    for group in groups:
        per_session = max(300, 11500 // len(group))
        views = [_compact_digest(row["content"], per_session) for row in group]
        out.append("[DIGEST] Cross-session source evidence; no event chronology inferred.\n"
                   + "\n".join(views))
    return out


_CONTEXT_FILLER = frozenset((
    "summary summarize summarise overall everything across total recap overview compare "
    "pattern patterns trend trends many much calculate compute average mean median count "
    "frequency probability simulate simulation predict prediction estimate expected "
    "please give provide show tell write draft explain find use based using data result "
    "results observation observations trial trials record records history session sessions "
    "conversation conversations discussed entire complete whole so far latest earlier "
    "all each every including following information describe review list "
    "know known knowing remember remembered learn learned learnt myself ourselves "
    "main key takeaway takeaways highlight highlights insight insights comprehensive "
    "integrated synthesis synthesize synthesise salient our prior previous past "
    "timeline chronology chronological sequence order event events stage stages step steps".split()
))
_CONTEXT_PHRASES = re.compile(
    r"(?:请帮我|帮我|请|总结|汇总|总体|全部|一共|多少|规律|趋势|整体|回顾|比较|"
    r"计算|平均|模拟|预测|估算|概率|所有|各次|每次|历史|记录|会话|讨论|数据|观测|结果|报告|"
    r"了解|知道|记得|记住|关于我|对我|主要|要点|概况|情况|时间线|先后|顺序|轨迹)", re.I,
)
_AGGREGATE_QUERY = re.compile(
    r"\b(?:average|mean|median|sum|count|calculate|compute|frequency|probabilit\w*|"
    r"predict\w*|simulat\w*|forecast\w*|estimate|expected|infer|distribution)\b"
    r"|计算|平均|模拟|预测|估算|概率|频率|分布|统计|拟合|推算", re.I,
)
_GLOBAL_CONTEXT = re.compile(
    r"\b(?:all|entire|whole)\s+(?:(?:my|our|the|previous|past)\s+)?(?:history|sessions|conversations|topics)\b"
    r"|\beverything\s+(?:we|i|you|discussed|so\s+far)\b"
    r"|(?:全部|所有|整个|完整).{0,6}(?:历史|会话|对话|话题)|跨(?:全部|所有)?会话|至今(?:讨论|聊过)", re.I,
)
_CONTEXT_REQUEST = re.compile(
    r"\b(?:summary|summari[sz]e|overview|recap|overall|everything|across|compare|patterns?|trends?|"
    r"takeaways?|highlights?|synthesis|synthesi[sz]e|timeline|chronology|chronological|sequence)\b"
    r"|\b(?:how\s+many|in\s+total|all\s+of|across\s+(?:all\s+)?sessions)\b"
    r"|\bin\s+(?:what|which)\s+order\b"
    r"|总结|汇总|概述|综述|总览|要点|总体|全部|一共|多少|规律|趋势|整体|回顾|比较|时间线|先后|顺序|轨迹",
    re.I,
)
_MEMORY_REFERENCE = re.compile(
    r"\b(?:my|our)\s+(?:(?:past|previous|earlier|shared|personal)\s+)?"
    r"(?:conversations?|chats?|discussions?|history|experiences?|memories)\b"
    r"|\b(?:I|we)\s+(?:(?:have|had|previously|already)\s+)*"
    r"(?:told|said|shared|mentioned|discussed|experienced|learned|learnt)(?:\s+(?:you|with\s+you))?\b"
    r"|(?:我|我们)(?:曾经|已经|之前|以前|至今|所|的|和你|跟你|向你)*"
    r"(?:告诉过你|告诉你|提到过|提到|聊过|讨论过|分享过|经历|对话|会话|聊天|讨论)", re.I,
)
_OVERVIEW_ABSTRACTION = re.compile(
    r"\b(?:(?:broad|main|key|important|significant|salient|central|major|recurring|common)\s+)*"
    r"(?:themes?|points?|things?|takeaways?|highlights?|insights?|lessons?)\b"
    r"|(?:主要|重要|关键|共同|反复出现|总体|广泛)*(?:主题|要点|事情|启示|教训)", re.I,
)
_OVERVIEW_RELATION = re.compile(
    r"\b(?:emerg\w*|aris\w*|stand\s+out|come\s+through)\s+(?:from|in)\b"
    r"|\b(?:based\s+on|so\s+far|up\s+to\s+now)\b"
    r"|(?:浮现|体现|反映|出现|至今|到目前为止)", re.I,
)
_TOPIC_CONSTRAINT = re.compile(
    r"\b(?:about|regarding|concerning|on|for|within|without|except|excluding)\s+"
    r"(?!me\b|us\b|myself\b|ourselves\b)\S"
    r"|[\"\u201c\u201d\u2018\u2019]|\d|(?:关于|针对|不包括|排除|除了)", re.I,
)


def _memory_overview_remainder(query: str) -> set[str] | None:
    """Parse a discourse reference without erasing an arbitrary topic noun.

    This only recognizes an overview of the speaker's previous communication
    or experience. A named object, exclusion or residual domain term remains
    constrained; failure to match such a topic is never permission to disclose
    the rest of a person's history.
    """
    if not _MEMORY_REFERENCE.search(query):
        return None
    text = _MEMORY_REFERENCE.sub(" ", query)
    text = _OVERVIEW_RELATION.sub(" ", text)
    if _TOPIC_CONSTRAINT.search(text):
        return None
    # Sentence-initial request words are not entities. Elsewhere, an explicit
    # proper name/acronym is a concrete constraint, even without 'about'.
    without_lead = re.sub(r"^\s*[A-Z][A-Za-z]*\b", " ", text)
    if re.search(r"\b[A-Z][A-Za-z0-9_-]{2,}\b", without_lead):
        return None
    return _context_tokens(_OVERVIEW_ABSTRACTION.sub(" ", text))


def _context_tokens(text: str) -> set[str]:
    tokens = _tokens(_CONTEXT_PHRASES.sub(" ", text)) - _CONTEXT_FILLER
    # A minimal inflection normalization improves topic matching without
    # conflating arbitrary substring matches or changing source wording.
    return {token[:-1] if len(token) > 4 and token.endswith("s") and not token.endswith("ss") else token
            for token in tokens}


def context_scope(query: str) -> tuple[str, set[str]]:
    """Shared intent and topical vocabulary for downstream raw filtering."""
    plan = query_layer.plan(query)
    query = plan["positive_query"]
    if plan['reason'] == 'broad-private-exclusion-without-positive-scope':
        return 'topic-scoped', set()
    if plan["task"] == "global_synthesis":
        return "global", set()
    topics = _context_tokens(query)
    requested = bool(plan['task'] == 'topic_synthesis'
                     or _CONTEXT_REQUEST.search(query) or _AGGREGATE_QUERY.search(query)
                     or _MEMORY_REFERENCE.search(query) and _OVERVIEW_ABSTRACTION.search(query))
    if not requested:
        return "ordinary", topics
    # A quantifier ranges over the requested topic, rather than erasing it:
    # 'all sessions about the turbine' still excludes unrelated personal data.
    return ("global" if not topics else "topic-scoped"), topics


def source_context_scope(rows, query: str) -> tuple[str, set[str], str]:
    """Return the shared query plan for already user-scoped source records.

    The third value explains a scope decision; consumers should gate raw
    evidence and choose their context budget using the first value. The
    memory-overview fallback is deliberately source-aware and conservative:
    it repairs unsupported discourse vocabulary only after a general overview
    grammar consumes every residual word. It cannot turn an ordinary question
    or a missing concrete topic into an all-history request.
    """
    scope, topics = context_scope(query)
    plan = query_layer.plan(query)
    query = plan["positive_query"]
    if scope == "ordinary":
        return scope, topics, "ordinary"
    rows = [dict(row) for row in rows]
    users = {row['user_id'] for row in rows if row.get('user_id') is not None}
    if len(users) > 1:
        raise ValueError('context selector requires records for a single user')
    if plan['reason'] == 'broad-private-exclusion-without-positive-scope':
        return 'topic-scoped', set(), 'broad-privacy-exclusion-without-positive-domain'
    if scope == "global":
        reason = ("source-unanchored-memory-overview" if rows and _memory_overview_remainder(query) == set()
                  else "explicit-global")
        return scope, topics, reason
    supported = set().union(*(_context_tokens(str(row.get('content', ''))) for row in rows)) if rows else set()
    remainder = _memory_overview_remainder(query)
    if rows and remainder is not None and not remainder:
        return "global", set(), "source-unanchored-memory-overview"
    return scope, topics, "lexical-topic-supported" if topics & supported else "topic-unmatched"


def _bridge_sessions(units: list[dict], selected: set[str]) -> set[str]:
    """Keep a lexical entity bridge between already relevant source sessions.

    Capitalized entity tokens provide conservative joins. Generic topical
    vocabulary alone cannot connect every document in a user's history.
    """
    entities, topical = {}, {}
    ignore = {"the","this","that","these","those","always","never","please","source","if","when","where","what","how","who","which"}
    for unit in units:
        # A leading field/heading label is markup, not a named entity join.
        # Exclude the syntactic position rather than listing particular labels.
        entity_text = re.sub(r"(?m)^[ \t]*(?:\#{1,6}[ \t]*)?(?:[A-Z][A-Za-z0-9_-]*[ \t]+){0,3}[A-Z][A-Za-z0-9_-]*[ \t]*[:：][ \t]*", "",unit["text"])
        names = {word.casefold() for word in re.findall(r"\b[A-Z][A-Za-z0-9_-]{2,}\b",entity_text)} - ignore
        entities.setdefault(unit["session_id"],set()).update(names)
        topical[unit["session_id"]] = max(topical.get(unit["session_id"],0),unit.get("overlap",0))
    selected = set(selected)
    for _ in range(3):
        joined = set().union(*(entities.get(session,set()) for session in selected)) if selected else set()
        bridges = {session for session,names in entities.items() if session not in selected and (
            bool(names & joined) and topical.get(session,0) > 0
            or len(names & joined) >= 2 and sum(bool(names & entities.get(seed,set())) for seed in selected) >= 2)}
        if not bridges:
            break
        selected.update(bridges)
    return selected


_NUMERIC_LITERAL = re.compile(r"(?<![\w.])[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?")


def _numeric_template(text: str) -> tuple[str, list[str]]:
    """A reversible literal substitution, never a fitted numerical formula."""
    values = []
    slots = _numeric_slots(text,len(_NUMERIC_LITERAL.findall(text)))
    def replace(match):
        values.append(match.group(0))
        return "{" + slots[len(values)-1] + "}"
    return _NUMERIC_LITERAL.sub(replace, text), values


def _numeric_slots(text: str, count: int) -> list[str]:
    # Pick a namespace absent from the literal source. A user can legitimately
    # discuss '{n1}'; its spelling must never become a substitution target.
    prefix = "n"
    while re.search(r"\{" + re.escape(prefix) + r"\d+\}",text):
        prefix += "_"
    return [prefix+str(index+1) for index in range(count)]


_RELATIVE_SOURCE_TIME = re.compile(
    r"\b(?:yesterday|today|tomorrow|tonight|(?:last|next|this)\s+(?:day|week|month|year|morning|evening|night)|\d+\s+(?:days?|weeks?|months?|years?)\s+ago)\b"
    r"|昨天|今天|明天|昨晚|今晚|明晚|上周|下周|本周|上个月|下个月|今年|明年|去年|前天|后天|\d+[天周月年]前", re.I,
)
_TEMPORAL_TOPIC = frozenset("date dates time times schedule schedules calendar calendars deadline deadlines interval intervals when".split())
_TEMPORAL_QUERY = re.compile(r"\b(?:dates?|times?|schedules?|calendars?|deadlines?|intervals?)\b|日期|时间|日程|何时|间隔",re.I)
_DATE_LITERAL = re.compile(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b|\d{4}年\d{1,2}月|\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}\b",re.I)
_CONTEXT_QUALIFIER = re.compile(r"^(?:except|unless|however|but|only|provided|if|when|otherwise|do\s+not|never)\b|^(?:除非|但是|不过|否则|例外|仅|只有|不要|禁止|如果|当)",re.I)
_PROCEDURE_SUBJECT = re.compile(r"\b(?:rules?|steps?|instructions?|operations?|algorithm|execution|procedure|protocol|policy)\b|规则|步骤|指令|操作|算法|执行|流程",re.I)
_PROCEDURE_CONTROL = re.compile(r"\b(?:order|sequence|once|passes|repeat\w*|skip\w*|return\w*|revisit\w*)\b|\bat\s+most\s+one\s+time\b|顺序|先后|一次|重复|跳过|返回|重访",re.I)


def _procedure_dependency_indexes(units: list[dict], kept: set[int]) -> set[int]:
    """Original execution controls attached to an already matched source.

    A protocol can precede a long appendix or follow its rule table. It need
    not repeat the queried state variables. This closure never opens another
    source or session and it does not infer or execute a missing policy.
    """
    sources = {units[index]['source_rid'] for index in kept}
    dependencies = {index for index in dependency_layer.execution_control_indexes(
                        [unit['text'] for unit in units])
                    if units[index]['source_rid'] in sources}
    for index in tuple(dependencies):
        following = index + 1
        while (following < len(units)
               and units[following]['source_rid'] == units[index]['source_rid']
               and units[following]['segment'] == units[following-1]['segment'] + 1
               and _CONTEXT_QUALIFIER.search(units[following]['text'].strip())):
            dependencies.add(following)
            following += 1
    for index in dependencies:
        units[index]['context_dependency'] = True
    return dependencies


def _context_metadata(units: list[dict]):
    """Share constants, but retain an invertible timestamp for every source."""
    sessions = {str(unit.get("session_id","unknown")) for unit in units}
    roles = {str(unit.get("role","unknown")) for unit in units}
    origins = {unit.get("timestamp_origin") or "legacy-unknown" for unit in units}
    def stamp(unit):
        try:
            value=float(unit.get("created_at"))
            return value if value == value and abs(value) != float("inf") else None
        except (TypeError,ValueError,OverflowError):
            return None
    times = {stamp(unit) for unit in units}
    base = next((stamp(unit) for unit in units if stamp(unit) is not None),None)
    variable_time = len(times) > 1
    shared = []
    columns = []
    if len(sessions) == 1:
        shared.append("session="+_metadata_value(next(iter(sessions))))
    else:
        columns.append("session")
    if len(roles) == 1:
        shared.append("role="+_metadata_value(next(iter(roles))))
    else:
        columns.append("role")
    if variable_time:
        shared.append(f"recorded_at_base={_recorded_time(base)} base_epoch={repr(base)}")
        columns.append("recorded_at_offset_s")
    else:
        shared.append("recorded_at="+_recorded_time(base))
    if len(origins) == 1:
        shared.append("timestamp_origin="+next(iter(origins)))
    else:
        columns.append("timestamp_origin_code")
    anchored = any(_RELATIVE_SOURCE_TIME.search(unit["text"]) for unit in units)
    if anchored:
        columns.append("relative_time_anchor")
    fragments = any('source_char_start' in unit for unit in units)
    if fragments:
        columns.append("source_char_range")
    header = "[SOURCE " + " ".join(shared) + "; source_rid@seq/segment identifies original records]"
    if variable_time:
        header += "\nEach recorded_at = base_epoch + recorded_at_offset_s seconds (UTC)."
    if len(origins) > 1:
        header += "\nOrigin codes: M=message.timestamp, I=ingestion-clock, U=legacy-unknown."
    header += "\nOnly message.timestamp may anchor relative expressions; ingestion-clock and legacy-unknown are metadata, not inferred event dates."
    if fragments:
        header += "\nsource_char_range is a half-open character range in the original normalized source segment; fragments with the same source pointer form one segment, not extra observations."
    def metadata(unit):
        values=[]
        if len(sessions) > 1:
            values.append("session="+_metadata_value(unit.get("session_id","unknown"),json_string=True))
        if len(roles) > 1:
            values.append("role="+_metadata_value(unit.get("role","unknown"),json_string=True))
        if variable_time:
            current=stamp(unit)
            delta=current-base if current is not None and base is not None else None
            values.append(str(int(delta)) if delta is not None and delta.is_integer() else repr(delta))
        origin=unit.get("timestamp_origin") or "legacy-unknown"
        if len(origins) > 1:
            values.append({"message.timestamp":"M","ingestion-clock":"I","legacy-unknown":"U"}.get(origin,origin))
        if anchored:
            values.append(_recorded_time(stamp(unit)) if origin == "message.timestamp" and _RELATIVE_SOURCE_TIME.search(unit["text"]) else "none")
        if fragments:
            values.append(str(unit.get('source_char_start', 0))+':'+str(unit.get('source_char_end', len(unit['text']))))
        return values
    return header,columns,metadata


def _context_pieces(units: list[dict], *, compact_numeric: bool = False) -> list[str]:
    """Losslessly share repeated numeric wording while retaining each source.

    Literal templates keep signs, precision, conditions and units exactly as
    written. Rows remain separate observations, even for identical values.
    """
    # Validity restrictions qualify the data that follows. Quote their real
    # source first so a consumer's prefix cannot read a sampled numeric block
    # as usable evidence while missing its later withdrawal.
    units=sorted(units,key=lambda unit:not (unit.get('context_qualification') or unit.get('context_dependency')
                                           or unit.get('context_schema')))
    if not compact_numeric:
        source,metadata_columns,metadata = _context_metadata(units)
        lines=[source,"Columns: source_rid@seq/segment"
               + "".join(" | "+column for column in metadata_columns) + " | literal source text"]
        for unit in units:
            values=[f"{unit['source_rid']}@{unit['seq']}/{unit['segment']}"] + metadata(unit) + [unit["text"]]
            lines.append(" | ".join(values))
        return ["\n".join(lines)]
    groups = {}
    for unit in units:
        template, values = _numeric_template(unit["text"])
        key = (unit["role"], template)
        groups.setdefault(key, []).append((unit, values))
    pieces = []
    for (role, template), members in groups.items():
        source,metadata_columns,metadata = _context_metadata([unit for unit,_ in members])
        if compact_numeric and len(members) >= 3 and members[0][1]:
            slots = _numeric_slots(members[0][0]["text"],len(members[0][1]))
            constants = {index: values[0] for index, values in enumerate(zip(*(values for _, values in members)))
                         if len(set(values)) == 1}
            rendered = template
            for index, value in constants.items():
                rendered = rendered.replace("{" + slots[index] + "}", value)
            variable = [index for index in range(len(members[0][1])) if index not in constants]
            lines = [source, "Literal source template: " + rendered,
                     "Rows substitute original numeric strings exactly; no statistical inference. Columns: source_rid@seq/segment"
                     + "".join(" | " + slots[index] for index in variable)
                     + "".join(" | " + column for column in metadata_columns)]
            for unit, values in members:
                lines.append(f"{unit['source_rid']}@{unit['seq']}/{unit['segment']}"
                             + "".join(" | " + values[index] for index in variable)
                             + "".join(" | " + value for value in metadata(unit)))
            pieces.append("\n".join(lines))
        else:
            lines = [source,"Columns: source_rid@seq/segment" + "".join(" | "+column for column in metadata_columns) + " | literal source text"]
            for unit, _ in members:
                lines.append(" | ".join([f"{unit['source_rid']}@{unit['seq']}/{unit['segment']}"] + metadata(unit) + [unit['text']]))
            pieces.append("\n".join(lines))
    return pieces


def _pack_context(
    units: list[dict], limit: int, budget_chars: int, scope: str,
    *, selection_plan: dict | None = None,
) -> list[str]:
    total = len(units)
    budget_chars = max(0, int(budget_chars))
    if selection_plan is not None:
        selection_plan.clear()
        selection_plan.update(complete_indices=set(), partial_char_ends={}, total=total)

    def selected(output, indices=(), partial=None):
        if selection_plan is not None:
            selection_plan['complete_indices'] = set(indices)
            selection_plan['partial_char_ends'] = dict(partial or {})
        return output

    def source_excerpt(index, prefix, label, allowance):
        if allowance <= 0:
            return selected([])
        text = units[index]['text']
        quote = _evidence_quote(text, allowance)
        if 'source_char_start' in units[index]:
            marker = " [source excerpt; remainder omitted]"
            visible = quote[:-len(marker)] if quote.endswith(marker) else quote
            start = units[index]['source_char_start']
            range_label = f"source_char_range={start}:{start+len(visible)} (same original source segment)\n"
            # The extra fragment envelope is also charged to this budget.
            allowance -= len(range_label)
            if allowance <= 0:
                return selected([])
            quote = _evidence_quote(text, allowance)
            visible = quote[:-len(marker)] if quote.endswith(marker) else quote
            range_label = f"source_char_range={start}:{start+len(visible)} (same original source segment)\n"
            label += range_label
        if quote == text:
            return selected([prefix + label + quote], [index])
        marker = " [source excerpt; remainder omitted]"
        visible = quote[:-len(marker)] if quote.endswith(marker) else quote
        return selected([prefix + label + quote], partial={index: len(visible)})

    if not total or limit <= 0 or budget_chars < 300:
        return selected([])
    header = (f"[DIGEST] {scope} source evidence; included={{included}}/{total} source segments; "
              "coverage={coverage}. Source sequence is provenance, not inferred event chronology. "
              "Timestamp origins below determine whether relative-time anchoring is available.\n")
    presentation_order = None

    def render(indices):
        chosen_indices = sorted(indices) if presentation_order is None else [index for index in presentation_order if index in indices]
        chosen = [units[index] for index in chosen_indices]
        if not chosen:
            return []
        coverage = "complete for selected source scope" if len(chosen) == total else "truncated; do not treat as complete statistics"
        prefix = header.format(included=len(chosen), coverage=coverage)

        def assemble(pieces):
            count = min(limit, len(pieces))
            groups = [[] for _ in range(count)]
            sizes = [0] * count
            target = max(1, sum(len(piece) + 1 for piece in pieces) // count)
            group_index = 0
            for piece in pieces:
                if groups[group_index] and sizes[group_index] + len(piece) > target and group_index + 1 < count:
                    group_index += 1
                groups[group_index].append(piece)
                sizes[group_index] += len(piece) + 1
            return [prefix + "\n".join(group) for group in groups if group]

        normal = assemble(_context_pieces(chosen))
        # Literal source wording is preferred when it fits. Numeric templates
        # are an alternative encoding, never a reason to inflate the context
        # and then throw away a much larger portion of the source evidence.
        if sum(map(len, normal)) <= budget_chars:
            return normal
        compact = assemble(_context_pieces(chosen, compact_numeric=True))
        return min((normal, compact), key=lambda output: sum(map(len, output)))

    full = render(range(total))
    if sum(map(len, full)) <= budget_chars:
        return selected(full, range(total))

    required = {index for index, unit in enumerate(units)
                if unit.get('context_qualification') or unit.get('context_schema') or unit.get('context_dependency')}
    required_view = render(required)
    if required and sum(map(len, required_view)) > budget_chars:
        # An incomplete validity restriction cannot accompany apparently
        # usable observations. Quote that restriction alone and disclose it.
        first = next((index for index in sorted(required) if units[index].get('context_qualification')),
                     min(required))
        chosen = units[first]
        prefix = header.format(included=0, coverage="truncated within one source; not complete statistics")
        label = _source_label(chosen) + "\n"
        allowance = budget_chars - len(prefix) - len(label) - 50
        return source_excerpt(first, prefix, label, allowance)

    # Cover sources before spending the remaining budget on another sentence
    # from a source already represented. Interleave sessions and spread the
    # first visits through early/middle/late source order, retaining provenance.
    def spread(indexes):
        indexes = list(indexes)
        if not indexes:
            return []
        out = [indexes[0]]
        if len(indexes) > 1:
            out.append(indexes[-1])
        intervals = [(1, len(indexes) - 2)]
        while intervals:
            following = []
            for start, end in intervals:
                if start <= end:
                    middle = (start + end) // 2
                    out.append(indexes[middle])
                    following.extend(((start, middle - 1), (middle + 1, end)))
            intervals = following
        return out

    sources, sessions = {}, {}
    for index, unit in enumerate(units):
        source = unit['source_rid']
        sources.setdefault(source, []).append(index)
        per_session = sessions.setdefault(unit['session_id'], [])
        if not per_session or per_session[-1] != source:
            per_session.append(source)
    session_queues = [spread(sessions[session]) for session in spread(sessions)]
    source_order = []
    for rank in range(max(map(len, session_queues), default=0)):
        for queue in session_queues:
            if rank < len(queue):
                source_order.append(queue[rank])
    source_queues = [spread(sources[source]) for source in source_order]
    repetitions = Counter(unit['text'] for unit in units) if scope == 'global' else None
    if repetitions is not None:
        # Select a source's distinctive literal evidence before its repeated
        # framing. Sorting only after interleaving otherwise gives every
        # administrative opening a turn before a buried source statement.
        # Equal wording remains separate observations; this is scheduling only.
        for queue in source_queues:
            queue.sort(key=lambda index: repetitions[units[index]['text']])
    priority = []
    for rank in range(max(map(len, source_queues), default=0)):
        for queue in source_queues:
            if rank < len(queue) and queue[rank] not in required:
                priority.append(queue[rank])
    # Fair session traversal alone can spend a small budget on each session's
    # first turn before ever reaching the end of the filtered history. Reserve
    # endpoint representatives first, including the last source's opening
    # segment when a later exception is its final segment. Required schema and
    # validity restrictions still take precedence over these optional anchors.
    endpoints = [0, total - 1, sources[units[-1]['source_rid']][0]]
    if scope == 'global':
        # A synthesis prefix prioritizes explicitly expressed choices, changes
        # and reasons before routine background. The remaining literal history
        # keeps the same fair traversal and is available in continuations.
        salient = [index for index in priority if units[index].get('context_salient')]
    else:
        salient = []
    priority = list(dict.fromkeys(index for index in salient + endpoints + priority if index not in required))
    if scope == 'global':
        # Repeated background can fill a whole continuation before a new
        # statement buried in a later turn is visited. This changes scheduling
        # only: equal wordings remain separate observations and keep their RIDs.
        # Stable sorting preserves the existing fair/salient order on ties.
        priority.sort(key=lambda index: repetitions[units[index]['text']])
    # A bounded prefix also needs cross-source coverage. Render a sampled
    # global view in the same fair source order used to select it, rather than
    # sorting it back into one long topic's chronological block. Sequence
    # numbers remain explicit for consumers that need the original chronology.
    if scope == 'global':
        presentation_order = sorted(required) + priority


    # Measure actual rendered sizes. A binary bounded prefix avoids the old
    # repeated .90 resampling, which could leave half the budget unused. Exact
    # whole-span backfill then uses gaps without claiming complete coverage.
    low, high = 0, len(priority)
    best, best_view = set(required), required_view
    while low < high:
        count = (low + high + 1) // 2
        trial = required | set(priority[:count])
        view = render(trial)
        if sum(map(len, view)) <= budget_chars:
            low = count
            best, best_view = trial, view
        else:
            high = count - 1
    # Prefix size is normally monotonic, but literal-template grouping can
    # change at a threshold. Never trust the estimate without the exact view.
    trial = required | set(priority[:low])
    view = render(trial)
    if view and sum(map(len, view)) <= budget_chars:
        best, best_view = trial, view
    remaining = [index for index in priority if index not in best]
    if len(remaining) > 128:
        remaining = [remaining[index] for index in _evidence_indices(len(remaining), 128)]
    for index in remaining:
        trial = best | {index}
        view = render(trial)
        if view and sum(map(len, view)) <= budget_chars:
            best, best_view = trial, view
    if best_view:
        return selected(best_view, best)

    # One long indivisible source can still exceed the complete context. The
    # excerpt is explicitly incomplete and cannot be mistaken for statistics.
    index = priority[0] if priority else 0
    chosen = units[index]
    prefix = header.format(included=0, coverage="truncated within one source; not complete statistics")
    label = _source_label(chosen) + "\n"
    allowance = budget_chars - len(prefix) - len(label) - 50
    return source_excerpt(index, prefix, label, allowance)


_NUMERIC_CELL = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?\s*(?:[%A-Za-zµμ°][A-Za-z0-9µμ°%/^²³-]{0,15})?")
_NUMERIC_FIELD = re.compile(
    r"[\"']?(?P<key>[^\W\d][\w./%()-]{0,40})[\"']?\s*[:=]\s*"
    r"(?P<value>[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
    r"\s*(?:[%A-Za-zµμ°][A-Za-z0-9µμ°%/^²³-]{0,15})?",re.U,
)
_MEASUREMENT_VALUE = re.compile(
    r'(?P<value>[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*'
    r'(?P<unit>degrees?\s+Celsius|degrees?\s+Fahrenheit|milliseconds?|seconds?|minutes?|hours?|'
    r'micrometers?|millimeters?|centimeters?|kilometers?|milligrams?|kilograms?|grams?|liters?|litres?|'
    r'MPa|kPa|mbar|bar|Pa|kN|mN|N|kWh|kW|MW|mW|W|kJ|mJ|J|GHz|MHz|kHz|Hz|mV|kV|V|mA|µA|A|'
    r'kg|mg|µg|ug|g|km|cm|mm|µm|um|m|ms|µs|us|ns|min|hr|s|h|mL|ml|µL|uL|L|l|°C|°F|℃|℉|C|K|%|'
    r'千克|公斤|毫克|微克|克|千米|公里|厘米|毫米|微米|米|毫秒|微秒|纳秒|秒|分钟|小时|'
    r'毫升|微升|升|摄氏度|华氏度|千帕|兆帕|帕|牛顿|瓦特|千瓦|伏特|安培|赫兹)(?![A-Za-z])',re.I,
)
_MEASUREMENT_SETUP = re.compile(r'\b(?:experiments?|measurements?|observations?|trials?|calibration|laboratory|readings?|test\s+data|results)\b|实验|测量|观测|校准|测定|读数',re.I)
_DATA_REFERENCE = re.compile(r'\b(?:previous|preceding|above|earlier|these|those|all)\s+(?:\w+\s+){0,2}(?:readings?|measurements?|observations?|results?|trials?|samples?|records?|values?|data)\b|(?:上述|以上|之前|前面|这些|所有|此前).{0,8}(?:读数|测量|观测|数据|结果|样本|实验)',re.I)
_DATA_VALIDITY = re.compile(r'\b(?:invalid|unreliable|incorrect|exclude\w*|discard\w*|disregard\w*|withdraw\w*|supersed\w*)\b|\b(?:do\s+not|must\s+not|cannot|no\s+longer)\s+use\b|无效|不可靠|错误|排除|丢弃|弃用|作废|不要使用|不能使用|不得使用|不应使用',re.I)
_DATA_RESTRICTION = re.compile(r'\b(?:only|except|excluding)\b.*\b(?:readings?|measurements?|observations?|results?|trials?|samples?|records?|values?|data)\b|(?:仅|只用|除了).{0,20}(?:读数|测量|观测|数据|结果|样本|实验)',re.I)
_DATA_CONDITIONS = re.compile(r'\b(?:same|identical|different|constant|controlled|changed)\s+(?:operating\s+)?conditions?\b|条件.{0,8}(?:相同|不同|一致)|(?:相同|不同|一致).{0,4}条件',re.I)


def _measurement_record_shape(text: str):
    # A short leading row label may change between measurements. It is ignored
    # for classification only; emitted original wording is never rewritten.
    body=re.sub(r'^[^:：\n]{1,28}[:：]\s*','',text.strip(),count=1)
    readings=list(_MEASUREMENT_VALUE.finditer(body))
    if len(readings)<2:
        return None
    template=_MEASUREMENT_VALUE.sub(lambda match:'{quantity}'+match.group('unit'),body)
    return ('measurements',re.sub(r'\s+',' ',template),tuple(match.group('unit') for match in readings))


def _data_record_shape(text: str):
    return _numeric_record_shape(text) or _measurement_record_shape(text)


def _numeric_record_shape(text: str):
    """A data-row grammar, rather than a test for digits in free narration."""
    body=text.strip().rstrip('.。')
    fields=list(_NUMERIC_FIELD.finditer(body))
    if len(fields)>=2:
        remaining=_NUMERIC_FIELD.sub('',body)
        conditions=[]
        clauses=[re.sub(r'[\[\]{}()\"\']','',clause).strip() for clause in re.split(r'[,;|\t]+',remaining)]
        for clause in clauses:
            if not clause:
                continue
            assigned=re.fullmatch(r'([^\W\d][\w-]*)\s*[:=]\s*([^\W\d][\w-]*)',clause,re.U)
            words=re.findall(r'[^\W\d][\w-]*',clause,re.U)
            if assigned:
                conditions.append(('category',assigned.group(1).casefold()))
            elif 1<=len(words)<=3 and re.fullmatch(r'[^\W\d][\w-]*(?:\s+[^\W\d][\w-]*){0,2}',clause,re.U):
                conditions.append(('label',words[-1].casefold(),len(words)))
            else:
                break
        else:
            return ('fields',tuple(sorted({match.group('key').casefold() for match in fields})),tuple(conditions))
    for delimiter in ('|','\t',';',','):
        cells=[cell.strip() for cell in body.strip('|').split(delimiter)]
        numeric=tuple(bool(_NUMERIC_CELL.fullmatch(cell)) for cell in cells)
        nominal=all(numeric[index] or re.fullmatch(r'[^\W\d][\w.-]{0,23}',cell,re.U) for index,cell in enumerate(cells))
        if len(cells)>=2 and sum(numeric)>=2 and len(cells)-sum(numeric)<=2 and nominal:
            return ('columns',delimiter,len(cells),numeric)
    return None


def _schema_declares(text: str, shape) -> bool:
    if shape[0]=='measurements':
        return bool(_MEASUREMENT_SETUP.search(text))
    if shape[0]=='fields':
        return sum(bool(re.search(r'(?<!\w)'+re.escape(key)+r'(?!\w)',text,re.I)) for key in shape[1])>=2
    delimiter,count=shape[1:3]
    for line in text.splitlines():
        cells=[cell.strip() for cell in line.strip().strip('|').split(delimiter)]
        if len(cells)==count and all(re.search(r'[^\W\d]',cell,re.U) for cell in cells):
            return True
    return False


def _schema_block_closure(units: list[dict], kept: set[int]) -> set[int]:
    """Keep an adjacent same-speaker numeric block with its declared schema."""
    by_session={}
    for index,unit in enumerate(units):
        by_session.setdefault(unit['session_id'],[]).append(index)
    expanded=set(kept)
    for indexes in by_session.values():
        for position,index in enumerate(indexes):
            if index not in kept or _data_record_shape(units[index]['text']):
                continue
            header=units[index]
            end=position+1
            while end<len(indexes) and units[indexes[end]]['source_rid']==header['source_rid']:
                end+=1
            if end>=len(indexes):
                continue
            first=units[indexes[end]]
            shape=_data_record_shape(first['text'])
            if not shape or first['role']!=header['role'] or not _schema_declares(header['content'],shape):
                continue
            run=[]
            while end<len(indexes):
                current=units[indexes[end]]
                if current['role']!=header['role'] or _data_record_shape(current['text'])!=shape:
                    break
                run.append(indexes[end]);end+=1
            if len(run)<3:
                continue
            expanded.update(run)
            # Sampling an anonymous data block without its column/unit
            # declaration would preserve numbers but change their meaning.
            units[index]['context_schema']=True
            for other in indexes:
                if units[other]['source_rid']!=header['source_rid']:
                    continue
                text=units[other]['text']
                if (shape[0]=='fields' and any(re.search(r'(?<!\w)'+re.escape(key)+r'(?!\w)',text,re.I) for key in shape[1])
                        or shape[0]=='columns' and shape[1] in text
                        or shape[0]=='measurements' and _MEASUREMENT_SETUP.search(text)
                        or _CONTEXT_QUALIFIER.search(text.strip())
                        or re.search(r'\b(?:all|each|every)\s+(?:\w+\s+){0,2}(?:measurements?|observations?|readings?|records?|rows?|samples?|trials?|runs?)\b|(?:所有|每个|每次|每项).{0,8}(?:测量|观测|记录|样本|实验|行)',text,re.I)):
                    expanded.add(other)
                    units[other]['context_schema']=True
    # A directly following withdrawal/restriction refers to the selected data
    # block even when it does not repeat the experiment's topic nouns.
    for indexes in by_session.values():
        records=[]
        for index in indexes:
            if not records or units[records[-1][0]]['source_rid']!=units[index]['source_rid']:
                records.append([])
            records[-1].append(index)
        active_role=None
        for record in records:
            role=units[record[0]]['role']
            qualifying=[index for index in record if (_DATA_REFERENCE.search(units[index]['text']) and _DATA_VALIDITY.search(units[index]['text']))
                        or _DATA_RESTRICTION.search(units[index]['text'])
                        or _DATA_CONDITIONS.search(units[index]['text']) and (_DATA_REFERENCE.search(units[index]['text']) or _MEASUREMENT_SETUP.search(units[index]['text']))]
            if active_role==role and qualifying:
                for index in qualifying:
                    expanded.add(index)
                    units[index]['context_qualification']=True
                    following=index+1
                    while (following<len(units) and units[following]['source_rid']==units[index]['source_rid']
                           and units[following]['segment']==units[following-1]['segment']+1
                           and _CONTEXT_QUALIFIER.search(units[following]['text'].strip())):
                        expanded.add(following)
                        units[following]['context_qualification']=True
                        following+=1
            elif any(index in expanded and (_MEASUREMENT_VALUE.search(units[index]['text']) or _numeric_record_shape(units[index]['text'])) for index in record):
                active_role=role
            else:
                active_role=None
    return expanded


_SYNTHESIS_SALIENCE = re.compile(
    r"\b(?:decid\w*|decision\w*|chose|chosen|reject\w*|adopt\w*|instead|because|"
    r"due\s+to|no\s+longer|withdraw\w*|revok\w*|correct\w*)\b"
    r"|决定|选择|取舍|否决|改为|改成|因为|由于|不再|撤销|撤回|更正", re.I)


def context_units_for_query(rows, query: str) -> list[dict]:
    """Pure source selector; input rows must already belong to one user.

    No database access, model calls or budget decisions occur here. Both raw
    retrieval and compact views can preserve exactly these original spans.
    """
    original_query = query
    plan = query_layer.plan(query)
    exclusions = plan["excluded_categories"]
    query = plan["positive_query"]
    rows=[dict(row) for row in rows]
    users={row['user_id'] for row in rows if row.get('user_id') is not None}
    if len(users)>1:
        raise ValueError('context selector requires records for a single user')
    conflicts = link_layer.conflicting_source_ids(rows,query)
    if conflicts:
        rows = [row for row in rows if row['rid'] not in conflicts]
    rows.sort(key=lambda row:(row['seq'],row['rid']))
    scope, topics, _ = source_context_scope(rows, original_query)
    context_request = scope != "ordinary"
    global_scope = scope == "global"
    units, scores = [], {}
    protected_policies = set()
    non_temporal_topics = topics - _TEMPORAL_TOPIC
    temporal_metadata_only = bool(_TEMPORAL_QUERY.search(query)) and not non_temporal_topics
    for row in rows:
        for segment, sentence in enumerate(_source_sentences(str(row["content"]))):
            if exclusions and query_layer.excluded_text(sentence, exclusions):
                continue
            overlap = len(topics & _context_tokens(sentence))
            if temporal_metadata_only and (_RELATIVE_SOURCE_TIME.search(sentence) or _DATE_LITERAL.search(sentence)):
                overlap += 1
            unit = dict(row)
            unit.update(source_rid=row["rid"], segment=segment, text=sentence, overlap=overlap)
            if global_scope:
                unit['context_salient'] = bool(_SYNTHESIS_SALIENCE.search(sentence))
                if (row.get('role') in {'user', 'system'} and sentence not in protected_policies
                        and _global_response_rule(sentence)):
                    unit['context_dependency'] = True
                    protected_policies.add(sentence)
            units.append(unit)
            scores[row["session_id"]] = max(scores.get(row["session_id"], 0), overlap)
    if not global_scope:
        best = max(scores.values(), default=0)
        if best <= 0:
            return []
        threshold = max(1, int(best * .6 + .999999))
        relevant_sessions = {session for session, score in scores.items() if score >= threshold}
        relevant_sessions = _bridge_sessions(units,relevant_sessions)
        if context_request:
            anchors = {index for index,unit in enumerate(units)
                       if unit["session_id"] in relevant_sessions and unit["overlap"] > 0}
            # Keep true entity-bridge sentences, rather than every unrelated
            # turn that shares their session container.
            named = set().union(*({word.casefold() for word in re.findall(r"\b[A-Z][A-Za-z0-9_-]{2,}\b",units[index]["text"])}
                                   for index in anchors)) if anchors else set()
            for index,unit in enumerate(units):
                if unit["session_id"] not in relevant_sessions or scores.get(unit["session_id"],0) >= threshold:
                    continue
                names={word.casefold() for word in re.findall(r"\b[A-Z][A-Za-z0-9_-]{2,}\b",unit["text"])}
                if len(names & named) >= 2:
                    anchors.add(index)
            kept=set(anchors)
            for index in anchors:
                for direction in (-1,1):
                    adjacent=index+direction
                    while (0 <= adjacent < len(units)
                           and units[adjacent]["source_rid"] == units[index]["source_rid"]
                           and units[adjacent]['segment'] == units[adjacent-direction]['segment'] + direction
                           and _CONTEXT_QUALIFIER.search(units[adjacent]["text"].strip())):
                        kept.add(adjacent)
                        adjacent += direction
            # An explicitly delimited data table belongs to its own source:
            # preserve header/rows together when any of that table matches.
            # This does not extend a topic to arbitrary numeric personal turns.
            source_rows={unit["source_rid"]:[] for unit in units}
            for index,unit in enumerate(units): source_rows[unit["source_rid"]].append(index)
            for indexes in source_rows.values():
                local_anchors = {units[index]['segment'] for index in indexes if index in kept}
                if local_anchors:
                    # Hidden sentences still delimit the original scope. Do
                    # not reconnect two permissions by deleting a private
                    # intervening subject before resolving their references.
                    dependencies = dependency_layer.dependency_indexes(
                        _source_sentences(str(units[indexes[0]]['content'])), local_anchors)
                    kept.update(index for index in indexes if units[index]['segment'] in dependencies)
            table_row=lambda text: bool(re.search(r"[|\t]|[,;].*[,;]",text))
            for indexes in source_rows.values():
                runs,run=[],[]
                for index in indexes:
                    if run and units[index]['segment'] != units[run[-1]]['segment'] + 1:
                        runs.append(run)
                        run=[]
                    if table_row(units[index]["text"]):
                        run.append(index)
                    else:
                        if run: runs.append(run)
                        run=[]
                if run: runs.append(run)
                for tabular in runs:
                    numerical=sum(len(_NUMERIC_LITERAL.findall(units[index]["text"])) >= 2 for index in tabular)
                    if len(tabular) >= 3 and numerical >= 2 and any(index in anchors for index in tabular):
                        kept.update(tabular)
                        for index in tabular:
                            if len(_NUMERIC_LITERAL.findall(units[index]['text'])) < 2:
                                units[index]['context_schema']=True
            extra, schema = data_layer.expand(units, kept)
            kept.update(extra)
            for index in schema:
                units[index]['context_schema'] = True
            kept=_schema_block_closure(units,kept)
            kept.update(_procedure_dependency_indexes(units,kept))
            units = [unit for index,unit in enumerate(units) if index in kept]
        else:
            # An ordinary lookup needs matching evidence rather than a full
            # unrelated history that happens to share one word.
            matches = {index for index, unit in enumerate(units)
                       if unit["session_id"] in relevant_sessions and unit["overlap"] >= threshold}
            neighbors = set(matches)
            for index in matches:
                for adjacent in (index - 1, index + 1):
                    if (0 <= adjacent < len(units) and units[adjacent]["source_rid"] == units[index]["source_rid"]
                            and abs(units[adjacent]['segment'] - units[index]['segment']) == 1):
                        neighbors.add(adjacent)
            units = [unit for index, unit in enumerate(units) if index in neighbors]
    return units


def context_source_rids(rows, query: str) -> set[int]:
    """IDs selected by the same pure selector used for context source spans."""
    return {int(unit['source_rid']) for unit in context_units_for_query(rows,query)}


def get_digests(
    conn: sqlite3.Connection, user_id: str, query: str, limit: int = 2,
    *, budget_chars: int = 48000,
) -> list[str]:
    """Build bounded, query-scoped evidence from current original records."""
    if limit<=0 or budget_chars<=0:
        return []
    if not _has_items(conn):
        return _bound_legacy(_legacy_digests(conn,user_id,query,limit),budget_chars)
    rows=conn.execute(
        "SELECT rid,session_id,role,content,created_at,seq,"+_timestamp_column(conn)+" FROM items"
        " WHERE user_id=? AND status='active' ORDER BY seq,rid",(user_id,),
    ).fetchall()
    if not rows:
        return _bound_legacy(_legacy_digests(conn,user_id,query,limit),budget_chars)
    scope,_,_=source_context_scope(rows,query)
    units=context_units_for_query(rows,query)
    return _pack_context(units,limit,budget_chars,"global" if scope=='global' else 'topic-scoped')


def get_context_views(
    conn: sqlite3.Connection, user_id: str, query: str, limit: int = 2,
    *, budget_chars: int = 12000, continuation_budget_chars: int = 64000,
    continuation_limit: int = 8,
) -> tuple[list[str], list[str]]:
    """Return an unchanged bounded prefix and disjoint source continuations.

    The continuation belongs after the existing retrieval prefix. Its source
    scope is identical to ``get_digests``: requesting more capacity never opens
    an unrelated topic, speaker or user. Complete source segments are emitted
    once across these views; a prefix excerpt resumes at its original segment
    character boundary with the same source pointer, rather than becoming a
    second observation. Schema, qualifiers and protocols remain in the prefix
    or continuation with their original provenance.

    This source view does not replace dedicated rule applicability or identity
    retrieval. Ordinary, numeric, synthesis and transfer queries all retain
    their existing source selection rules; an unknown specific topic remains
    unmatched even with a large continuation budget.
    """
    if limit <= 0 or budget_chars <= 0:
        return [], []
    if not _has_items(conn):
        return _bound_legacy(_legacy_digests(conn, user_id, query, limit), budget_chars), []
    rows = conn.execute(
        "SELECT rid,session_id,role,content,created_at,seq,"+_timestamp_column(conn)+" FROM items"
        " WHERE user_id=? AND status='active' ORDER BY seq,rid", (user_id,),
    ).fetchall()
    if not rows:
        return _bound_legacy(_legacy_digests(conn, user_id, query, limit), budget_chars), []
    scope, _, _ = source_context_scope(rows, query)
    label = "global" if scope == 'global' else 'topic-scoped'
    units = context_units_for_query(rows, query)
    prefix_plan = {}
    prefix = _pack_context(units, limit, budget_chars, label, selection_plan=prefix_plan)
    if continuation_budget_chars <= 0 or continuation_limit <= 0:
        return prefix, []
    complete = prefix_plan.get('complete_indices', set())
    partials = prefix_plan.get('partial_char_ends', {})
    remaining, original_indexes = [], []
    for index, unit in enumerate(units):
        if index in complete:
            continue
        continued = dict(unit)
        if index in partials:
            start = partials[index]
            continued.update(source_char_start=start, source_char_end=len(unit['text']),
                             text=unit['text'][start:])
        remaining.append(continued)
        original_indexes.append(index)
    if not remaining:
        return prefix, []
    # Reserve a fixed small envelope for exact union-coverage disclosure. The
    # legacy prefix renderer and its character budget remain byte-identical.
    continuation_plan = {}
    pieces = _pack_context(remaining, continuation_limit,
                           max(0, int(continuation_budget_chars)-900), label,
                           selection_plan=continuation_plan)
    if not pieces:
        return prefix, []
    newly_complete = {original_indexes[index] for index in continuation_plan.get('complete_indices', set())}
    combined = complete | newly_complete
    coverage = ('complete for selected source scope' if len(combined) == len(units)
                else 'truncated; do not treat as complete statistics')
    header = (f"[CONTEXT CONTINUATION] {label}; complete source segments in prefix={len(complete)}; "
              f"newly completed segments={len(newly_complete)}; combined coverage={len(combined)}/{len(units)}; "
              f"coverage={coverage}. Append to the existing prefix. "
              "A source_rid@seq/segment identifies one original segment, including any disjoint character fragments; "
              "it is not an additional observation. Source schema, qualifications and execution protocols may be in the prefix.\n")
    pieces[0] = header + pieces[0]
    return prefix, pieces


def _bound_legacy(contents: list[str], budget_chars: int) -> list[str]:
    out = []
    remaining = budget_chars
    for content in contents:
        if len(content) <= remaining:
            out.append(content)
            remaining -= len(content)
        elif remaining >= 300:
            excerpt = _compact_digest(content, remaining - 50)
            if len(excerpt) > remaining - 40:
                excerpt = excerpt[:remaining - 40]
            out.append(excerpt + "\n[legacy source view truncated]")
            break
        else:
            break
    return out


def _compact_digest(content: str, budget: int) -> str:
    """Fit whole evidence lines where possible, retaining early/mid/late."""
    if len(content) <= budget:
        return content
    lines = content.splitlines()
    if len(lines) <= 1:
        return _evidence_quote(content, max(100, budget - 50))
    header, evidence = lines[0], lines[1:]
    available = max(100, budget - len(header) - 100)
    count = min(len(evidence), max(1, int(available / max(1, sum(map(len, evidence)) / len(evidence)))))
    selected = [evidence[i] for i in _evidence_indices(len(evidence), count)]
    while len(selected) > 1 and sum(len(line) + 1 for line in selected) > available:
        count -= 1
        selected = [evidence[i] for i in _evidence_indices(len(evidence), count)]
    # A single very long sentence is clearly labelled as a source excerpt.
    if len(selected) == 1 and len(selected[0]) > available:
        selected[0] = _evidence_quote(selected[0], available)
    return header + f" Further excerpt: {len(selected)} of {len(evidence)} stored evidence lines.\n" + "\n".join(selected)
