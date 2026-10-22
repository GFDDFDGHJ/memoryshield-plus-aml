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
query. Everything here is deterministic and microsecond-scale; Add does not get
slower in any way the platform can notice.

Conflict handling is deliberately narrow: a new value only retires the *same*
(user, subject, predicate) triple. This is the fix for the old governance rule,
which did LIKE '%<topic>%' and hid 1480 of 3974 rows because it extracted
"that" as a topic from "Yes, that is quite true."
"""
from __future__ import annotations

import re
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
    "use keep reply respond answer write call avoid prefer include start end "
    "format return provide ensure make list explain summarize cite verify "
    "check confirm stop remember".split()
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
        add(subject, _slug(subj_key), value, "fact", 0.9, m.start())

    for rx, (subject, predicate, kind, conf) in _SELF:
        for m in rx.finditer(text):
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
            if m.groups() and m.group(1):
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
        for m in list(_BULLET.finditer(text))[:8]:
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
    # Confidence chooses the bounded candidate set, never the update order.
    # Applying later statements last also allows A -> B -> A in one message.
    return sorted(out[:12], key=lambda f: f.source_pos)


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
    # "self.*" facts may come from any role: in some benchmark datasets the
    # patient's own words sit in the assistant turn (the assistant plays the
    # doctor). Restricting to role=user emptied the profile for exactly those
    # sessions, which is what E3 depends on. Junk is filtered by value shape
    # instead (see _SELF_BAD below).
    _ = role
    written = 0
    for f in facts:
        if f.operation == "retract":
            # Negating an old employer is not an assertion of a new location.
            # Match the named value exactly so denying an older state cannot
            # erase an unrelated, newer state on the same attribute.
            rows = conn.execute(
                "SELECT fid,value FROM facts WHERE user_id=? AND subject=?"
                " AND predicate=? AND status='active'",
                (user_id, f.subject, f.predicate),
            ).fetchall()
            for row in rows:
                if _clean(row["value"]).casefold() == f.value.casefold():
                    conn.execute("UPDATE facts SET status='stale' WHERE fid=?", (row["fid"],))
            continue

        single_valued = f.kind == "fact" or f.subject == "favorite"
        # identical fact already active -> nothing to do
        dup = conn.execute(
            "SELECT fid FROM facts WHERE user_id=? AND subject=? AND predicate=?"
            " AND value=? AND status='active'",
            (user_id, f.subject, f.predicate, f.value),
        ).fetchone()
        if dup:
            if single_valued:
                # Clean up conflicting active values from older derived data,
                # even when this assertion repeats one of those values.
                conn.execute(
                    "UPDATE facts SET status='stale' WHERE user_id=? AND subject=?"
                    " AND predicate=? AND status='active' AND fid<>?",
                    (user_id, f.subject, f.predicate, dup[0]),
                )
            continue

        previous = None
        if single_valued:
            row = conn.execute(
                "SELECT fid, value FROM facts WHERE user_id=? AND subject=? AND predicate=?"
                " AND status='active' ORDER BY seq DESC, fid DESC LIMIT 1",
                (user_id, f.subject, f.predicate),
            ).fetchone()
            if row:
                previous = row[1]
                conn.execute(
                    "UPDATE facts SET status='stale' WHERE user_id=? AND subject=?"
                    " AND predicate=? AND status='active'",
                    (user_id, f.subject, f.predicate),
                )

        conn.execute(
            "INSERT INTO facts(user_id,session_id,source_rid,subject,predicate,value,kind,"
            "confidence,status,previous_value,created_at,seq)"
            " VALUES(?,?,?,?,?,?,?,?,'active',?,?,?)",
            (user_id, session_id, source_rid, f.subject, f.predicate, f.value, f.kind,
             f.confidence, previous, created_at, seq),
        )
        written += 1
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


def _recorded_time(value: float | None) -> str:
    try:
        return datetime.fromtimestamp(float(value), timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError, OSError):
        return "unknown"


def _source_label(row: dict) -> str:
    """A stored timestamp is provenance, never an inferred event date."""
    rid = row.get("source_rid")
    return (
        f"[SOURCE source_rid={rid if rid is not None else 'unknown'}"
        f" session={row.get('session_id', 'unknown')} seq={row.get('seq', 'unknown')}"
        f" role={row.get('role', 'unknown')} recorded_at={_recorded_time(row.get('created_at'))}]"
    )


def _with_source(conn: sqlite3.Connection, row: sqlite3.Row | dict, has_items: bool) -> dict:
    result = dict(row)
    result["role"] = "unknown"
    if has_items and result.get("source_rid") is not None:
        source = conn.execute(
            "SELECT role FROM items WHERE user_id=? AND rid=?",
            (result["user_id"], result["source_rid"]),
        ).fetchone()
        if source:
            result["role"] = source["role"]
    return result


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
    matches = [sentence for sentence in _source_sentences(source["content"])
               if needle in sentence.casefold()]
    return matches[0] if len(matches) == 1 else value


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
    rows = conn.execute(
        "SELECT fid,user_id,session_id,source_rid,subject,predicate,value,kind,"
        "confidence,status,created_at,seq FROM facts"
        " WHERE user_id=? AND kind='rule' AND status='active'"
        " ORDER BY seq,fid", (user_id,),
    ).fetchall()
    q = _tokens(query)
    asks_rules = any(word in query.lower() for word in (
        "rule", "instruction", "guideline", "constraint", "规则", "指令", "要求", "约束",
    ))
    has_items = _has_items(conn)
    ranked = []
    for row in rows:
        value = _complete_source_rule(conn, row, has_items)
        overlap = len(q & _tokens(value + " " + row["predicate"]))
        if not overlap and (not asks_rules or row["confidence"] < 0.6):
            continue
        score = overlap / max(1.0, len(q) ** 0.5) + (0.15 if asks_rules else 0.0)
        ranked.append((score, row, value))
    ranked.sort(key=lambda item: (-item[0], -item[1]["seq"], -item[1]["fid"]))
    out, seen = [], set()
    for score, row, value in ranked:
        key = (row["source_rid"], value.casefold())
        if key in seen:
            continue
        seen.add(key)
        result = _with_source(conn, row, has_items)
        result["text"] = _source_label(result) + " " + value
        result["score"] = round(score, 6)
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
    rows = conn.execute(
        "SELECT fid,user_id,session_id,source_rid,subject,predicate,value,kind,"
        "confidence,status,previous_value,created_at,seq FROM facts"
        " WHERE user_id=? AND kind IN ('fact','preference') AND status IN ('active','stale')"
        " ORDER BY seq,fid", (user_id,),
    ).fetchall()
    groups: dict[tuple[str, str], list] = {}
    for row in rows:
        groups.setdefault((row["subject"], row["predicate"]), []).append(row)
    q = _tokens(query)
    wants_history = any(word in query.lower() for word in _HISTORY_WORDS)
    ranked = []
    for key, history in groups.items():
        if not any(row["status"] == "stale" or row["previous_value"] for row in history):
            continue
        subject, predicate = key
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
    out = []
    for score, row in selected:
        result = _with_source(conn, row, has_items)
        label = "CURRENT" if row["status"] == "active" else "HISTORICAL"
        result["text"] = (
            f"[{label}] {row['subject']}.{row['predicate']}: {row['value']} "
            + _source_label(result)
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
    intent_kinds = intent_kinds or set()
    q = _tokens(query)
    rows = conn.execute(
        "SELECT f.fid,f.subject,f.predicate,f.value,f.kind,f.confidence,"
        " f.previous_value,f.created_at,f.status,"
        " (SELECT live.value FROM facts live WHERE live.user_id=f.user_id"
        "  AND live.subject=f.subject AND live.predicate=f.predicate AND live.status='active'"
        "  ORDER BY live.seq DESC,live.fid DESC LIMIT 1) AS current_value"
        " FROM facts f WHERE f.user_id=? AND f.status IN ('active','stale')"
        " ORDER BY f.confidence DESC, f.fid DESC LIMIT 400",
        (user_id,),
    ).fetchall()
    scored = []
    for r in rows:
        hay = f"{r['subject']} {r['predicate']} {r['value']}".replace("_", " ")
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
    out = []
    for score, r in scored[:limit]:
        text = f"{r['subject']} / {r['predicate']}: {r['value']}"
        if r["status"] == "stale":
            current = r["current_value"]
            text = "[HISTORICAL] " + text
            text += (f" (current value: {current})" if current is not None
                     else " (no active value recorded)")
        elif r["previous_value"]:
            text += f"  (changed from: {r['previous_value']})"
        out.append({"score": round(score, 6), "text": text, "kind": r["kind"]})
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
    rows = conn.execute(
        "SELECT subject,predicate,value,kind,confidence,previous_value FROM facts"
        " WHERE user_id=? AND status='active' ORDER BY"
        "  CASE kind WHEN 'rule' THEN 0 WHEN 'preference' THEN 1 ELSE 2 END,"
        "  confidence DESC, fid DESC LIMIT 400",
        (user_id,),
    ).fetchall()
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
    _ = query
    # Identity first. The personal-info corpus holds 34 facts/user but the
    # profile spent its whole budget on "dislikes X because Y" and hid
    # name/age/city -- which is what E3 (background & care) asks about.
    identity = {"name", "age", "location", "work", "role", "condition",
                "allergy", "medication", "metric", "origin", "nickname"}
    rows.sort(key=lambda r: (
        0 if (r["subject"] == "self" and r["predicate"] in identity) else 1,
        1 if r["kind"] == "preference" else 0,
    ))
    rows = rows[:max_items]

    rules, prefs, facts = [], [], []
    for r in rows:
        if r["kind"] == "rule":
            rules.append(_display_rule(r["predicate"], r["value"]))
        elif r["kind"] == "preference":
            verb = "likes" if r["predicate"] == "like" else (
                "dislikes" if r["predicate"] == "dislike" else r["predicate"])
            # 82% of preference values carried a "because ..." rationale, which
            # burns the profile budget without adding recall.
            val = re.split(r"\s+because\b|\s*因为", r["value"])[0].strip()[:60]
            prefs.append(f"{verb} {val}")
        else:
            facts.append(f"{r['subject']}.{r['predicate']}={r['value']}")
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
    return [re.sub(r"\s+", " ", match.group(0)).strip()
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
        "SELECT rid,session_id,role,content,created_at,seq FROM items"
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


def get_digests(conn: sqlite3.Connection, user_id: str, query: str, limit: int = 2) -> list[str]:
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
