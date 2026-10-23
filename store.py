"""Vector-backed memory store for the AML Add/Search contract.

Layout:
  items      raw messages + metadata + per-user_id isolation boundary
  vec_items  sqlite-vec index over message embeddings
  facts      (subject, predicate, value) triples mined at write time

Retrieval combines attributed raw records with related rules, state history,
personal background and bounded session views within the requesting user.
Explicit event-sequence queries retain local source order around relevant
events; current-state queries may prefer newer records among semantic near-ties.

Raw search is *per user and exact*: with a few hundred messages per user a
numpy dot product is milliseconds, and it removes two failure modes of the old
global-ANN-then-filter design -- a user's records being crowded out of the
shared candidate pool (sqlite-vec `k` is capped at 4096, the deployment had
3974 rows and a 4000 pool, i.e. 26 rows of headroom), and approximation error
on the ranked head.
"""
from __future__ import annotations

import array
import hashlib
import json
import math
import os
import re
import sqlite3
import time
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx
import sqlite_vec
from openai import OpenAI

import facts as fact_layer
import evidence_links as link_layer
import query_plan as query_layer
import source_dependencies as dependency_layer
from reranker import (RerankerClient, MAX_QUERY_BYTES, MAX_DOCUMENT_BYTES,
                      MAX_WEIGHTED_INPUT_BYTES)
from config import CONFIG

try:  # NumPy accelerates exact per-user scoring but is optional.
    import numpy as np
except Exception:  # noqa: BLE001
    np = None


def _pack(vec: list[float]) -> bytes:
    return array.array("f", vec).tobytes()


def _structured_id(kind: str, user_id: str, content: str) -> str:
    """Identify structured evidence independently of its query-time rank."""
    payload = json.dumps([kind, user_id, content], ensure_ascii=False, separators=(",", ":"))
    return f"{kind}_{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


_PUNCT = re.compile(r"[\s\u3000]+")


def _norm_text(text: str) -> str:
    """Normalize presentation without erasing meaningful symbols or numbers."""
    return _PUNCT.sub(" ", unicodedata.normalize("NFC", text).casefold()).strip()


_CHRONOLOGY_QUERY = re.compile(
    r"\b(?:first|earliest|original|initial|previously|earlier|before|after|"
    r"history|historical|chronological|chronology|timeline|sequence)\b"
    r"|\bin\s+(?:what|which)\s+order\b"
    r"|\border\s+of\s+(?:events|stages|steps|operations|occurrences|changes)\b"
    r"|\bused\s+to\b|先后|时间线|顺序|最初|原来|之前|以前|历史|演变|变化过程|最早",
    re.I,
)
_CURRENT_QUERY = re.compile(
    r"\b(?:current|currently|now|latest|still)\b|目前|现在|当前|最新|还在|仍然",
    re.I,
)
_INITIAL_QUERY = re.compile(r"\b(?:first|earliest|original|initial)\b|最初|最早|原来", re.I)
_STATE_ATTRIBUTE_QUERY = re.compile(
    r"\b(?:value|code|password|key|name|location|address|setting|configuration|status|state)\b"
    r"|数值|代码|口令|密码|密钥|名字|姓名|住址|地址|设置|配置|状态", re.I,
)
_SEQUENCE_QUERY = re.compile(
    r"\b(?:chronological|chronology|timeline|sequence)\b"
    r"|\bin\s+(?:what|which)\s+order\b"
    r"|\border\s+of\s+(?:events|stages|steps|operations|occurrences|changes)\b"
    r"|\b(?:what|which)\s+(?:happened|occurred|came)\s+first\b"
    r"|\b(?:what|which)\s+(?:event|stage|step|action|incident)\s+(?:happened|occurred|came)\s+first\b"
    r"|\bwhat\s+happened\s+before\s+and\s+after\b"
    r"|时间线|先后|顺序|阶段排序|变化过程", re.I,
)
_PERSONAL_QUERY = re.compile(r"\b(?:my|me|i|user|their|his|her)\b|我|用户|个人", re.I)
_PROFILE_QUERY = re.compile(
    r"\b(?:background|profile|personal|personalized|personality|advice|recommend\w*|care)\b"
    r"|\bknow\s+about\b|\babout\s+(?:me|myself|the\s+user)\b"
    r"|\bwho\s+am\s+i\b|背景|个人信息|个人情况|建议|推荐|照顾|护理|关于我",
    re.I,
)
_RULE_REQUEST = re.compile(
    r"\b(?:rules?|instructions?|guidelines?|constraints?|must|should|required|allowed|"
    r"prohibited|forbidden|apply|applies)\b|规则|要求|指令|约束|必须|应该|能否|禁止|按照",
    re.I,
)
# Use the same intent decision for rule selection, ordering and budgeting.
_EXECUTION_REQUEST = fact_layer._EXECUTION_QUERY
_RULE_QUERY_WORDS = frozenset(
    "rule rules instruction instructions guideline guidelines constraint constraints "
    "must should required allowed prohibited forbidden apply applies write draft "
    "create compose generate format reply respond answer summarize summarise prepare "
    "produce list translate rewrite explain please".split()
)
_CORRECTION_CUE = re.compile(
    r"^\s*(?:correction\b|actually\b|no\s*[,，:]|to\s+correct\b)"
    r"|^\s*(?:更正|纠正|修正|不对|实际上)", re.I,
)
_LINK_REFERENCE = re.compile(
    r"\b(?:data[ _-]?sets?|projects?|protocols?|methods?|procedures?|algorithms?)\b|数据集|项目|协议|规程|方法|算法|流程"
    r"|[A-Za-z][A-Za-z0-9_.]*[-:/][A-Za-z0-9_.:/-]+", re.I,
)


def _evidence_body(text: str) -> str:
    """Remove labels generated by this service, preserving the quoted wording."""
    text = re.sub(r"^\[(?:RULE|CHANGE|FACT)\]\s*", "", text)
    return re.sub(r"\[SOURCE[^\]]*\]\s*", "", text).strip()


def _single_slot_rule(query: str, evidence: dict) -> bool:
    """Only a strongly related instruction may replace a one-slot raw lookup."""
    if not (_RULE_REQUEST.search(query) or _EXECUTION_REQUEST.search(query)):
        return False
    if evidence.get("scope") == "global" and _EXECUTION_REQUEST.search(query):
        return True
    topics = fact_layer._tokens(query) - _RULE_QUERY_WORDS
    if not topics:
        return bool(_RULE_REQUEST.search(query))
    overlap = topics & fact_layer._tokens(_evidence_body(evidence["text"]))
    return len(overlap) >= max(1, math.ceil(.6 * len(topics)))


def _summary_query(query: str) -> bool:
    # One intent decision controls both source selection and its context budget.
    return fact_layer.context_scope(query)[0] != "ordinary"


def _original_state_query(query: str) -> bool:
    """The first asserted state is distinct from the earliest dated event."""
    explicit_state = re.search(r"\b(?:original|originally|initial)\b|\bat\s+the\s+beginning\b|最初|原来", query, re.I)
    return bool(explicit_state and _STATE_ATTRIBUTE_QUERY.search(query))


def _single_personal_attribute(query: str) -> str | None:
    """Recognize an unambiguous requested property, without a sensitive denylist.

    This positive scope is deliberately narrow. Multi-property, historical,
    relational, advice and summary requests keep their normal evidence path.
    """
    if (_summary_query(query) or _profile_query(query) or _chronology_query(query)
            or re.search(r"\band\b|\bor\b|以及|分别|同时|和|与", query, re.I)):
        return None
    if not re.search(r"\bmy\b|我的|我(?:叫|住)|\bwhere\b.{0,20}\bi\b", query, re.I):
        return None
    tokens = fact_layer._tokens(query)
    if len(tokens) > 12:
        return None
    attributes = [attribute for attribute, terms in fact_layer._ATTRIBUTE_TERMS.items()
                  if tokens & fact_layer._tokens(terms)]
    if len(attributes) != 1:
        return None
    attribute = attributes[0]
    residual = query
    for term in sorted(fact_layer._ATTRIBUTE_TERMS[attribute].split(), key=len, reverse=True):
        pattern = r"\b" + re.escape(term) + r"\b" if term.isascii() else re.escape(term)
        residual = re.sub(pattern, " ", residual, flags=re.I)
    residual = re.sub(
        r"\b(?:current|currently|now|latest|still|tell|remind|recall|remember|state|"
        r"give|provide|say|said|mention|mentioned|please|own|full|legal|am)\b"
        r"|请问|告诉我|提醒我|我的|我|是什么|什么|目前|现在|当前|最新|仍然|的|叫", " ", residual, flags=re.I,
    )
    # An unexplained relation/entity makes 'my' ambiguous: the requested name
    # may belong to another person. Preserve semantic fallback in that case.
    return None if fact_layer._tokens(residual) else attribute


def _chronology_query(query: str) -> bool:
    return bool(_CHRONOLOGY_QUERY.search(query))


def _sequence_query(query: str) -> bool:
    """A temporal restriction does not by itself request ordered event context."""
    return bool(_SEQUENCE_QUERY.search(query))


def _current_query(query: str) -> bool:
    # A question comparing the original and current states needs both ends.
    return bool(_CURRENT_QUERY.search(query)) and not _chronology_query(query)


def _profile_query(query: str) -> bool:
    return bool(_PERSONAL_QUERY.search(query)) and bool(_PROFILE_QUERY.search(query))


_SENT_SPLIT = re.compile(r"(?<=[。！？!?\.])\s*")


def _excerpt(text: str, query: str, head: int = 320, window: int = 520) -> str:
    """Return the readable part of a long message instead of the whole blob.

    Messages here reach several thousand characters (agent prompts, scripted
    scenes). Returning them verbatim buries the one sentence that answers the
    question, and the platform reads the array in order. Keep the opening (it
    states the context) plus the window with the highest lexical overlap.
    """
    if len(text) <= head + window + 200:
        return text
    sentences = [s for s in _SENT_SPLIT.split(text) if s and s.strip()]
    if not sentences:
        return text[: head + window]
    q = fact_layer._tokens(query)
    best, best_score = "", -1.0
    for s in sentences:
        s_toks = fact_layer._tokens(s)
        score = len(q & s_toks) / (len(q) ** 0.5 + 1e-9) if q else 0.0
        if score > best_score:
            best, best_score = s, score
    opening = text[:head].rstrip()
    if best_score <= 0:
        return opening + " … " + text[head:head + window]
    at = text.find(best)
    chunk = text[at:at + window] if at >= 0 else best[:window]
    return opening + " … " + chunk.strip()


def _query_preview(text: str, query: str, budget: int = 3000, *, source_text: str | None = None) -> str:
    """Show complementary literal windows; keep the original turn in storage.

    Greedy token coverage prevents one repeated topic sentence from using all
    of the preview while another requested attribute is far away in the turn.
    Windows are labelled excerpts, not standalone synthesized assertions.
    """
    if len(text) <= budget:
        return text
    tokens = fact_layer._tokens(query)
    spans = []
    # Keep literal table delimiters: whitespace normalization would erase TSV
    # column boundaries before a matched numeric row can recover its schema.
    def split_units(value):
        result, ranges = [], []
        offset = 0
        for physical in value.splitlines(keepends=True):
            line = physical.rstrip('\r\n')
            if len(line) <= 500 and any(line.count(delimiter) >= 2 for delimiter in ("|", "\t", ",")):
                if line.strip():
                    result.append(line.strip())
                    ranges.append((offset,offset+len(line)))
            else:
                for start,end in dependency_layer.source_sentence_spans(line):
                    result.append(re.sub(r"[^\S\t]+", " ", line[start:end]).strip())
                    ranges.append((offset+start,offset+end))
            offset += len(physical)
        return result,ranges
    units,unit_ranges = split_units(text)
    original_units,original_ranges = split_units(source_text) if source_text is not None else (units,unit_ranges)
    # A second preview may receive already filtered excerpts. Resolve links
    # against the whole source, including hidden scope boundaries; output can
    # only contain units that survived the caller's filtering.
    original_to_local = {}
    next_original = 0
    original_counts, local_counts = Counter(original_units), Counter(units)
    ambiguous = any(original_counts[part] > count for part, count in local_counts.items()
                    if original_counts[part] > 1)
    for local, part in enumerate(units):
        if ambiguous:
            break
        match = next((index for index in range(next_original, len(original_units))
                      if original_units[index] == part), None)
        if match is None:
            original_to_local.clear()
            break
        original_to_local[match] = local
        next_original = match + 1
    local_to_original = {local: original for original, local in original_to_local.items()}
    def source_adjacent(left, right):
        return (left in local_to_original and right in local_to_original
                and local_to_original[right] == local_to_original[left] + 1)
    def dependencies_for(anchor):
        if anchor not in local_to_original:
            return set()
        dependencies = dependency_layer.dependency_indexes(original_units, {local_to_original[anchor]})
        original = source_text if source_text is not None else text
        for start,end in dependency_layer.numbered_rule_spans(original,[original_ranges[local_to_original[anchor]]]):
            members={index for index,(left,right) in enumerate(original_ranges) if left < end and right > start}
            if members <= original_to_local.keys():
                dependencies.update(members)
        dependencies.discard(local_to_original[anchor])
        return {original_to_local[index] for index in dependencies if index in original_to_local}
    locations = []
    start = 0
    for unit, part in enumerate(units):
        at = text.find(part, start)
        if at < 0:
            at = start
        start = at + len(part)
        locations.append(at)
        # Very long paragraphs still expose a query match far from the head.
        offsets = range(0, max(1, len(part)), 600) if len(part) > 1500 else [0]
        for offset in offsets:
            piece = part[offset:offset + 900] if len(part) > 1500 else part
            spans.append((at + offset, piece, tokens & fact_layer._tokens(piece), unit))
    if not spans:
        return _excerpt(text, query)
    # An adjacent exception can reverse a matched sentence without repeating
    # its nouns. Reserve it with that sentence before choosing extra matches.
    context_cue = re.compile(
        r"^(?:except|unless|however|but|only|provided|if|when|otherwise|do\s+not|never)\b"
        r"|^(?:除非|但是|不过|否则|例外|仅|只有|不要|禁止)", re.I)
    # The introduction can define an execution protocol without repeating
    # the requested variables. Its dependent exception chain must still pass
    # the same all-or-disclosed-omission check as any other matching source.
    opening = units[0][:min(500, max(0, budget // 5))]
    if len(units) > 1 and source_adjacent(0, 1) and context_cue.search(units[1].strip()):
        opening = ""
    selected = [(locations[0], opening, 0)] if opening else []
    covered = tokens & fact_layer._tokens(opening)
    remaining = budget - len(opening) - (35 if opening else 0)
    blocked_chain = False
    if opening:
        dependencies = dependencies_for(0)
        needed = sum(len(units[index]) + 35 for index in dependencies)
        if needed <= remaining:
            selected.extend((locations[index], units[index], index) for index in sorted(dependencies))
            remaining -= needed
            for index in dependencies:
                covered |= tokens & fact_layer._tokens(units[index])
        elif dependencies:
            selected = []
            remaining = budget
            blocked_chain = True
    def table_header(unit: int) -> int | None:
        """Retain the schema of a literal pipe/TSV/CSV numeric row."""
        row = units[unit].strip().strip("|")
        for delimiter in ("|", "\t", ","):
            cells = [cell.strip() for cell in row.split(delimiter)]
            if len(cells) < 3 or sum(bool(re.match(r"^[+-]?(?:\d|\.\d)", cell)) for cell in cells) < 2:
                continue
            for previous in range(unit - 1, -1, -1):
                if not source_adjacent(previous, previous + 1):
                    break
                candidate = units[previous].strip().strip("|")
                columns = [cell.strip() for cell in candidate.split(delimiter)]
                if len(columns) != len(cells):
                    break
                numeric = sum(bool(re.match(r"^[+-]?(?:\d|\.\d)", cell)) for cell in columns)
                labels = sum(bool(re.search(r"[A-Za-z\u4e00-\u9fff]", cell)) for cell in columns)
                if numeric == 0 and labels >= 2:
                    return previous
        return None
    # Document-wide execution controls qualify every selected rule. They need
    # not repeat a requested variable, and can occur after the rule bodies.
    protocol = dependency_layer.execution_control_indexes(units)
    for index in tuple(protocol):
        following = index + 1
        while (following < len(units) and source_adjacent(following - 1, following)
               and context_cue.search(units[following].strip())):
            protocol.add(following)
            following += 1
    if protocol:
        # Replace a partial opening with the whole dependency group. If it
        # cannot fit, disclose its omission rather than quote a partial policy.
        selected = [piece for piece in selected if piece[2] not in protocol]
        remaining = budget - sum(len(piece) + 35 for _,piece,_ in selected)
        needed = sum(len(units[index]) + 35 for index in protocol)
        if needed <= min(remaining, max(500, budget // 2)):
            selected.extend((locations[index], units[index], index) for index in sorted(protocol))
            remaining -= needed
            for index in protocol:
                covered |= tokens & fact_layer._tokens(units[index])
        else:
            blocked_chain = True
            spans = [span for span in spans if span[3] not in protocol]
    while spans and remaining >= 150:
        best = max(range(len(spans)), key=lambda i: (
            len(spans[i][2] - covered), -original_counts[units[spans[i][3]]],
            len(spans[i][2]) / max(1, len(fact_layer._tokens(spans[i][1]))),
            len(spans[i][2]), -spans[i][0]))
        at, piece, overlap, unit = spans.pop(best)
        if not overlap:
            break
        if any(at < previous[0] + len(previous[1]) and previous[0] < at + len(piece)
               for previous in selected):
            continue
        context = []
        schema = table_header(unit)
        if schema is not None and not any(previous[2] == schema for previous in selected):
            context.append((locations[schema], units[schema], schema))
        following = unit + 1
        while (following < len(units) and source_adjacent(following - 1, following)
               and context_cue.search(units[following].strip())):
            if not any(previous[2] == following for previous in selected):
                context.append((locations[following], units[following], following))
            following += 1
        existing_context = {index for _, _, index in context} | {index for _, _, index in selected}
        for index in sorted(dependencies_for(unit)):
            if index not in existing_context:
                context.append((locations[index], units[index], index))
        needed = len(piece) + 35 + sum(len(line) + 35 for _, line, _ in context)
        if needed > remaining:
            blocked_chain = blocked_chain or bool(context)
            continue
        selected.append((at, piece, unit))
        selected.extend(context)
        covered |= overlap
        remaining -= needed
        if len(selected) >= 8:
            break
    if not selected:
        if blocked_chain:
            return ("[INCOMPLETE EVIDENCE: a related condition/exception chain exceeds the "
                    "excerpt budget and is omitted in full. Do not infer an unconditional "
                    "rule; consult the complete source_rid before applying it.]")
        return _excerpt(text, query)
    selected.sort()
    notice = ("[INCOMPLETE EVIDENCE: an additional related condition/exception chain "
              "is omitted in full; consult the complete source_rid.]\n") if blocked_chain else ""
    return notice + "\n[… source text omitted …]\n".join(piece for _, piece, _ in selected)


class _DatabaseConnection(sqlite3.Connection):
    """Commit or roll back a context, then release its database resources."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def _ranking_document(query: str, row) -> str:
    """Give the ranker literal query windows instead of a blind long prefix.

    This representation is disposable. The source row, returned quotations and
    source chronology are unchanged. Exception/protocol/schema dependencies
    share the existing all-or-explicitly-incomplete preview rules.
    """
    header = f"[speaker role={row['role']}]\n"
    content = row['content']
    if len((header + content).encode('utf-8')) <= MAX_DOCUMENT_BYTES:
        return header + content
    header += "[QUERY-FOCUSED RANKING EXCERPTS; source text omitted]\n"
    allowance = MAX_DOCUMENT_BYTES - len(header.encode('utf-8'))
    for budget in (min(3000, allowance), 2000, 1200, 800, 400):
        preview = _query_preview(content, query, budget=budget)
        if len(preview.encode('utf-8')) <= allowance:
            return header + preview
    # The preview's incompleteness notice is bounded even if an individual
    # paragraph is indivisible; avoid byte-cutting an apparently complete rule.
    return header + "[INCOMPLETE EVIDENCE: selected source spans exceed the ranking budget.]"


class AddRequestConflict(ValueError):
    """A request ID was reused with a different Add payload or session."""


class MemoryStore:
    def __init__(self) -> None:
        self._client = OpenAI(
            base_url=CONFIG.embedding_base_url,
            api_key=CONFIG.embedding_api_key or "not-needed",
        )
        self._dim = CONFIG.embedding_dim
        self._headers = {
            "Authorization": f"Bearer {CONFIG.embedding_api_key}",
            "Content-Type": "application/json",
        }
        self._pool = ThreadPoolExecutor(max_workers=CONFIG.embedding_concurrency)
        self._reranker = (RerankerClient(CONFIG.rerank_api_key, CONFIG.rerank_base_url,
                                       CONFIG.rerank_model, CONFIG.rerank_timeout)
                          if CONFIG.rerank_enabled and CONFIG.rerank_api_key else None)
        self._ensure_schema()

    # ------------------------------------------------------------------ db
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(CONFIG.db_path, timeout=30.0, factory=_DatabaseConnection)
        try:
            conn.row_factory = sqlite3.Row
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            return conn
        except BaseException:
            conn.close()
            raise

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS items (
                    rid INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    timestamp_origin TEXT NOT NULL DEFAULT 'legacy-unknown',
                    status TEXT NOT NULL DEFAULT 'active',
                    seq INTEGER NOT NULL
                )
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(items)")}
            if "timestamp_origin" not in columns:
                conn.execute("ALTER TABLE items ADD COLUMN timestamp_origin TEXT NOT NULL DEFAULT 'legacy-unknown'")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_items_user ON items(user_id, status)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS add_requests (
                    user_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    written INTEGER NOT NULL,
                    PRIMARY KEY (user_id, request_id)
                )
                """
            )
            conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_items USING vec0(embedding float[{self._dim}])"
            )
            fact_layer.ensure_schema(conn)
            conn.commit()

    # ------------------------------------------------------------- embed
    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if CONFIG.embedding_mode == "multimodal":
            embeddings = self._embed_multimodal(texts)
        else:
            embeddings = []
            # DashScope text-embedding-v4 accepts at most ten texts per batch.
            batch_size = max(1, min(CONFIG.embedding_batch_size, 10))
            for start in range(0, len(texts), batch_size):
                batch = texts[start:start + batch_size]
                resp = self._client.embeddings.create(
                    model=CONFIG.embedding_model, input=batch, dimensions=self._dim,
                )
                ordered = sorted(resp.data, key=lambda d: d.index)
                if [d.index for d in ordered] != list(range(len(batch))):
                    raise ValueError("embedding response count or indices do not match input")
                embeddings.extend(d.embedding for d in ordered)
        if len(embeddings) != len(texts):
            raise ValueError("embedding response count does not match input")
        if any(len(vec) != self._dim for vec in embeddings):
            raise ValueError("embedding response dimensions do not match configuration")
        return embeddings

    def _embed_one_multimodal(self, text: str) -> list[float]:
        url = CONFIG.embedding_base_url.rstrip("/") + "/embeddings/multimodal"
        payload = {
            "model": CONFIG.embedding_model,
            "input": [{"type": "text", "text": text}],
        }
        # One retry; the multimodal endpoint accepts one text item per request.
        for attempt in range(2):
            try:
                r = httpx.post(url, headers=self._headers, json=payload, timeout=60.0)
                if r.status_code == 200:
                    return r.json()["data"]["embedding"]
                last = f"HTTP {r.status_code}: {r.text[:200]}"
            except Exception as exc:  # noqa: BLE001
                last = repr(exc)
            time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"multimodal embedding failed: {last}")

    def _embed_multimodal(self, texts: list[str]) -> list[list[float]]:
        # Preserve order while fanning out concurrent single-text requests.
        results = list(self._pool.map(self._embed_one_multimodal, texts))
        return results

    # ---------------------------------------------------------------- add
    @staticmethod
    def _replayed_add_count(
        conn: sqlite3.Connection,
        user_id: str,
        request_id: str,
        session_id: str,
        payload_hash: str,
    ) -> int | None:
        row = conn.execute(
            "SELECT session_id, payload_hash, written FROM add_requests"
            " WHERE user_id=? AND request_id=?",
            (user_id, request_id),
        ).fetchone()
        if row is None:
            return None
        if row["session_id"] != session_id or row["payload_hash"] != payload_hash:
            raise AddRequestConflict("request_id was already used with a different session or messages")
        return row["written"]

    def add_messages(
        self,
        user_id: str,
        session_id: str,
        messages: list[dict],
        *,
        request_id: str | None = None,
    ) -> int:
        """Persist a chunk once per user/request ID; return its original count."""
        now_ms = time.time() * 1000.0
        contents = [m.get("content", "") for m in messages]
        contents = [c if isinstance(c, str) else _flatten_content(c) for c in contents]
        payload_hash = ""
        if request_id:
            payload = json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            # Avoid embedding known retries. A second check under the write lock
            # below handles callers that race while their embeddings are in flight.
            with self._connect() as conn:
                written = self._replayed_add_count(conn, user_id, request_id, session_id, payload_hash)
            if written is not None:
                return written
        embeddings = self.embed(contents)

        with self._connect() as conn:
            # Serialize sequence allocation and the idempotency check with the
            # eventual writes, while keeping embedding API work outside the lock.
            conn.execute("BEGIN IMMEDIATE")
            if request_id:
                written = self._replayed_add_count(conn, user_id, request_id, session_id, payload_hash)
                if written is not None:
                    return written
            seq = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM items WHERE user_id=?",
                (user_id,),
            ).fetchone()[0]
            for i, (msg, emb) in enumerate(zip(messages, embeddings)):
                provided_timestamp = msg.get("timestamp") is not None
                ts = msg["timestamp"] if provided_timestamp else now_ms
                created_at = float(ts) / 1000.0
                timestamp_origin = "message.timestamp" if provided_timestamp else "ingestion-clock"
                role = msg.get("role", "user")
                content = contents[i]
                cur = conn.execute(
                    "INSERT INTO items(user_id,session_id,role,content,created_at,timestamp_origin,status,seq)"
                    " VALUES(?,?,?,?,?,?,'active',?)",
                    (user_id, session_id, role, content, created_at, timestamp_origin, seq + i),
                )
                rid = cur.lastrowid
                conn.execute(
                    "INSERT INTO vec_items(rowid, embedding) VALUES(?, ?)",
                    (rid, _pack(emb)),
                )
                # Structured layer: deterministic regex mining, no model call.
                fact_layer.store_facts(
                    conn, user_id, session_id, rid, content, created_at, seq + i, role
                )
            # Aggregate view of the session, rebuilt from its messages.
            fact_layer.update_digest(conn, user_id, session_id)
            if request_id:
                conn.execute(
                    "INSERT INTO add_requests(user_id,request_id,session_id,payload_hash,written)"
                    " VALUES(?,?,?,?,?)",
                    (user_id, request_id, session_id, payload_hash, len(messages)),
                )
            conn.commit()
        return len(messages)

    # ------------------------------------------------------------ search
    def search(self, user_id: str, query: str, top_k: int) -> list[dict]:
        qvec = self.embed([query])[0]
        with self._connect() as conn:
            fact_layer.ensure_schema(conn)
            results = self._layered(conn, user_id, query, qvec, top_k)
        return results

    def _layered(
        self,
        conn: sqlite3.Connection,
        user_id: str,
        query: str,
        qvec: list[float],
        top_k: int,
    ) -> list[dict]:
        if top_k <= 0:
            return []
        plan = query_layer.plan(query)
        if plan['reason'] == 'broad-private-exclusion-without-positive-scope':
            return []
        now_iso = _iso(time.time())
        candidates: list[dict] = []
        reserve = min(8, max(1, (top_k + 1) // 2))
        structured_limit = top_k - reserve
        link_rows, conflicting_sources = [], set()
        if top_k > 1 and _LINK_REFERENCE.search(query) and not _single_personal_attribute(query):
            link_rows = [dict(row) for row in conn.execute(
                "SELECT rid,user_id,session_id,role,content,created_at,timestamp_origin,status,seq"
                " FROM items WHERE user_id=? AND status='active' ORDER BY seq,rid", (user_id,),
            )]
            conflicting_sources = link_layer.conflicting_source_ids(link_rows, query)

        # Query-related rules are individual evidence blocks. In particular, a
        # conditional rule keeps its trigger and is not limited to the five
        # rules displayed by the compact background profile.
        # Applicable instructions compete on the actual evidence budget, not
        # a fixed count that silently drops the thirteenth constraint.
        rule_limit = max(1, structured_limit)
        rules = fact_layer.relevant_rules(conn, user_id, query, rule_limit)
        rules = [evidence for evidence in rules if evidence.get("source_rid") not in conflicting_sources]
        if top_k == 1:
            rules = [evidence for evidence in rules if _single_slot_rule(query, evidence)][:1]
        if top_k == 1 and rules:
            structured_limit = 1
        for evidence in rules:
            candidates.append(self._structured_evidence("rule", user_id, evidence, 1.95))
        remaining = max(0, structured_limit - len(candidates))
        history_limit = min(6, max(2 if _chronology_query(query) else 1, remaining)) if remaining else 0
        if top_k == 1 and not rules and _current_query(query):
            history_limit = 1
        history = fact_layer.change_history(conn, user_id, query, history_limit)
        if top_k == 1 and not rules and _current_query(query):
            history = [evidence for evidence in history if evidence.get("status") == "active"]
            if history:
                structured_limit = 1
        for evidence in history:
            candidates.append(self._structured_evidence("change", user_id, evidence, 1.9))

        # Stable identity remains useful for personal advice and background
        # questions, but an unrelated profile cannot precede an exact lookup.
        if len(candidates) < structured_limit and _profile_query(query):
            profile = fact_layer.build_profile(conn, user_id, query)
            if profile:
                candidates.append({"id": "profile", "content": profile,
                                   "score": 1.85, "created_at": now_iso})

        # Non-summary queries can also benefit from a session whose source
        # evidence matches the topic. The fact layer excludes metadata from
        # this relevance test and has no unrelated largest-session fallback.
        wants_summary = _summary_query(query)
        source_size = conn.execute(
            "SELECT count(*),COALESCE(sum(length(content)),0) FROM items"
            " WHERE user_id=? AND status='active'", (user_id,),
        ).fetchone()
        raw_count, raw_chars = source_size[0], source_size[1]
        # A short history already fits verbatim: an extractive copy would only
        # displace the exact record and repeat its neighboring turns.
        digest_cap = 6 if wants_summary else (2 if raw_count > top_k else 0)
        digest_limit = min(digest_cap, max(0, structured_limit - len(candidates)))
        if (wants_summary and plan['task'] == 'global_synthesis'
                and top_k >= 8 and structured_limit > 0):
            # A full candidate list of mined rules has not spent the actual
            # response budget yet. Let a global source view compete before
            # digest-first packing; the same count/character limits still
            # bound the selected output and retain omitted-policy disclosure.
            digest_limit = max(1, digest_limit)
        digest_budget = 12000 if wants_summary else 2000
        continuation_views = []
        if (wants_summary and top_k >= 8 and digest_limit
                and (raw_chars > 24000 or raw_count > top_k)):
            digest_views, continuation_views = fact_layer.get_context_views(
                conn, user_id, query, digest_limit, budget_chars=digest_budget,
                continuation_budget_chars=max(0,min(128000,CONFIG.context_continuation_chars)),
                continuation_limit=min(8,max(1,top_k//4)),
            )
        else:
            digest_views = fact_layer.get_digests(
                conn, user_id, query, digest_limit, budget_chars=digest_budget,
            )
        for text in digest_views:
            # The fact layer budgets the complete list, including compact
            # source tables whose continuation rows share an earlier label.
            # Preserve that representation intact instead of re-sampling it.
            if not text or len(text) > digest_budget:
                continue
            candidates.append({"id": _structured_id("digest", user_id, text),
                               "content": text, "score": 1.8,
                               "created_at": now_iso})

        q_low = query.lower()
        intent = {"preference"} if any(
            word in q_low for word in ("prefer", "like", "favourite", "favorite", "hobby",
                                       "偏好", "喜欢", "习惯")
        ) else set()
        for evidence in fact_layer.match_facts(conn, user_id, query, min(4, top_k), intent):
            if evidence.get("kind") == "rule":
                continue  # Dedicated rule retrieval applies relevance and scope.
            candidates.append(self._structured_evidence("fact", user_id, evidence, 1.7))

        # A result-count limit is not a downstream context budget. Bound the
        # supplementary layer in characters as well, and keep the strongest
        # raw matches ahead of it for an ordinary fact lookup. Complete source
        # turns stay in storage; long returned views disclose their sampling.
        structured: list[dict] = []
        seen_contents: set[str] = set()
        structured_budget = 12000 if wants_summary else (
            8000 if rules else 6000 if _profile_query(query) or _EXECUTION_REQUEST.search(query) else 2000)
        if wants_summary:
            candidates.sort(key=lambda evidence: not evidence["id"].startswith("digest_"))
        for evidence in candidates:
            if len(structured) >= structured_limit:
                break
            if evidence.get("source_rid") in conflicting_sources:
                continue
            key = _norm_text(evidence["content"])
            if key in seen_contents or len(evidence["content"]) > structured_budget:
                continue
            seen_contents.add(key)
            structured.append(evidence)
            structured_budget -= len(evidence["content"])

        raw = self._search_raw(
            conn, user_id, qvec, top_k,
            1.0 if _current_query(query) else 0.0, query,
        )
        linked = []
        if link_rows and raw:
            seeds = {row["source_rid"] for row in raw[:4] if row.get("source_rid") is not None}
            excerpted_seeds = {row["source_rid"]:row for row in raw[:4]
                               if row.get("source_rid") is not None and row.get("excerpt")}
            related = link_layer.select_linked_sources(
                link_rows, query, seeds, max_sources=min(8,top_k-1), max_chars=10000,
                include_seed_rids=excerpted_seeds)
            for row in related:
                if row.get('seed_background'):
                    # A source may be semantically relevant while its query
                    # preview omits a distant unit definition or restriction.
                    # Add only safe, still missing literal background to the
                    # existing source view, preserving its identity and rank.
                    view = excerpted_seeds.get(row['source_rid'])
                    if view is None:
                        continue
                    visible = view['content']
                    missing, ranges = [], []
                    for start, end in row['quote_spans']:
                        literal = row['content'][start:end]
                        if literal and literal not in visible:
                            missing.append(literal)
                            ranges.append([start,end])
                            visible += '\n' + literal
                    if missing:
                        view['content'] += (
                            '\n[SAME-SOURCE EXPLICIT-ID BACKGROUND; excerpts from this source, '
                            'not additional observations]\n' + '\n'.join(missing))
                        view['same_source_background'] = True
                        view['background_source_char_ranges'] = ranges
                        view['evidence_links'] = row['evidence_links']
                    continue
                quote = row["quote"]
                label = fact_layer._source_label(row)
                evidence = self._structured_evidence("linked", user_id,
                    {**row,"text":label+"\n"+quote}, 1.65)
                evidence.update(evidence_links=row["evidence_links"],
                                source_char_ranges=row["quote_spans"], excerpt=True,
                                source_chars=len(row["content"]))
                linked.append(evidence)
        scoped_attribute = next((row.get("requested_attribute") for row in raw
                                 if row.get("requested_attribute")), None)
        if scoped_attribute:
            predicates = {scoped_attribute, "nickname"} if scoped_attribute == "name" else {scoped_attribute}
            structured = [evidence for evidence in structured
                          if (evidence.get("subject") == "self"
                              and evidence.get("predicate") in predicates)
                          or (evidence["id"].startswith("rule_")
                              and evidence.get("scope") == "global")]
        complete_sources: dict[int, dict] = {}
        for evidence in structured:
            rid = evidence.get("source_rid")
            if rid is None:
                continue
            source = conn.execute(
                "SELECT content FROM items WHERE user_id=? AND rid=? AND status='active'",
                (user_id, int(rid)),
            ).fetchone()
            # Deduplicate layers only when the selected derived block quotes
            # this entire source turn. A single rule span must never hide the
            # other facts, conditions or exceptions in its original message.
            if source and _norm_text(_evidence_body(evidence["content"])) == _norm_text(source["content"]):
                complete_sources.setdefault(int(rid), evidence)
        strong_rule_ids = {
            _structured_id("rule", user_id, f"[RULE] {evidence['text']}")
            for evidence in rules if _single_slot_rule(query, evidence)
        }
        def preferred(evidence: dict) -> bool:
            identity = evidence["id"]
            return (
                (wants_summary and identity.startswith("digest_"))
                or (_profile_query(query) and identity == "profile")
                or (_current_query(query) and identity.startswith("change_")
                    and evidence.get("status") == "active")
                or identity in strong_rule_ids
                or (top_k > 1 and identity.startswith("rule_")
                    and evidence.get("scope") == "global")
            )
        leading = [evidence for evidence in structured if preferred(evidence)]
        supplementary = [evidence for evidence in structured if not preferred(evidence)]
        if raw and raw[0].get("explicit_correction"):
            correction = raw.pop(0)
            correction["content"] = (
                "[EXPLICIT CORRECTION SOURCE; read related earlier statements with this source]\n"
                + correction["content"]
            )
            for evidence in leading + supplementary:
                shared_topics = (fact_layer._context_tokens(query)
                    & fact_layer._context_tokens(correction["content"])
                    & fact_layer._context_tokens(evidence["content"]))
                earlier_property = bool(scoped_attribute and evidence.get("subject") == "self"
                    and evidence.get("predicate") in ({scoped_attribute,"nickname"} if scoped_attribute == "name" else {scoped_attribute})
                    and evidence.get("role") == correction.get("role"))
                if (evidence.get("seq", float("inf")) < correction["seq"]
                        and (earlier_property or (evidence["id"].startswith("rule_") and len(shared_topics) >= 2))):
                    evidence["content"] = (
                        "[EARLIER RELATED SOURCE; later explicit correction quoted above; "
                        "do not assume unchanged applicability]\n" + evidence["content"]
                    )
                    evidence["id"] = _structured_id(evidence["id"].split("_",1)[0], user_id, evidence["content"])
            leading.insert(0, correction)
        head_count = min(reserve, max(0, top_k - len(leading)))
        # Explicitly linked definitions and restrictions follow the first
        # matching observation, ahead of repetitive semantic-neighbor tails.
        out = (leading + raw[:min(1,head_count)] + linked
               + raw[min(1,head_count):head_count] + supplementary + raw[head_count:])
        packed: list[dict] = []
        seen_evidence: set[int] = set()
        remaining_chars = 32000
        for evidence in out:
            if evidence["id"].startswith("mem_"):
                # If ordinary relevance puts the source ahead of its complete
                # derived quote, put that quote at the same position rather
                # than moving the evidence behind unrelated raw records.
                evidence = complete_sources.get(evidence.get("source_rid"), evidence)
            identity = id(evidence)
            if identity in seen_evidence:
                continue
            if len(packed) >= top_k:
                break
            if len(evidence["content"]) > remaining_chars:
                continue
            packed.append(evidence)
            seen_evidence.add(identity)
            remaining_chars -= len(evidence["content"])
        if continuation_views:
            # Keep the original first 16k readable characters intact. Omitted
            # selected source segments take precedence over repetitive raw
            # tails, and result-count limits still apply to the complete array.
            protected, used = 0, 0
            while protected < len(packed) and used < 16000:
                used += len(packed[protected]["content"])
                protected += 1
            total_context = sum(map(len,digest_views+continuation_views))
            ceiling = 32000 if total_context <= 32000 else max(32000,min(160000,CONFIG.extended_context_chars))
            expanded = packed[:protected]
            seen = {_norm_text(row["content"]) for row in expanded}
            for text in continuation_views:
                if len(expanded) >= top_k or len(text)+used > ceiling:
                    continue
                key = _norm_text(text)
                if key in seen:
                    continue
                expanded.append({"id":_structured_id("continuation",user_id,text),
                                 "content":text,"score":1.6,"created_at":now_iso})
                seen.add(key)
                used += len(text)
            for row in packed[protected:]:
                if len(expanded) >= top_k:
                    break
                if len(row["content"])+used <= ceiling:
                    expanded.append(row)
                    used += len(row["content"])
            packed = expanded
        exclusions = query_layer.plan(query)["excluded_categories"]
        if exclusions:
            source_rows = conn.execute(
                "SELECT rid,user_id,session_id,role,content,created_at,seq,timestamp_origin FROM items"
                " WHERE user_id=? AND status='active' ORDER BY seq,rid", (user_id,),
            ).fetchall()
            private_tables = fact_layer.excluded_table_segments(source_rows, exclusions)
            packed = [evidence for evidence in packed
                      if evidence.get('source_rid') not in private_tables
                      and not query_layer.excluded_text(evidence["content"], exclusions)]
        return packed

    @staticmethod
    def _structured_evidence(kind: str, user_id: str, evidence: dict, score: float) -> dict:
        text = evidence["text"]
        content = f"[{kind.upper()}] {text}"
        created_at = evidence.get("created_at")
        result = {
            "id": _structured_id(kind, user_id, content), "content": content,
            "score": score,
            "created_at": _iso(float(created_at)) if created_at is not None else _iso(time.time()),
        }
        for key in ("source_rid", "session_id", "seq", "role", "status", "subject", "predicate", "scope"):
            if key in evidence:
                result[key] = evidence[key]
        return result

    def _rerank_rows(self, query: str, ranked: list) -> list:
        """Reorder a bounded semantic shortlist without changing evidence.

        Scope, ownership and explicit-state safeguards retain their existing
        decisions. Relative model scores never delete rows or establish facts.
        """
        client = getattr(self, "_reranker", None)
        if (client is None or len(ranked) < 2 or not query.strip()
                or _summary_query(query) or _sequence_query(query) or _original_state_query(query)
                or _current_query(query) or _single_personal_attribute(query)
                or _CORRECTION_CUE.search(ranked[0][1]["content"])):
            return ranked
        # Dense and sparse recall complement each other: an old calibration
        # may rank below many repetitive observations. Reserve half the pool
        # for rare query terms across this user's eligible original sources.
        head = []
        terms = fact_layer._context_tokens(query)
        tokens = {row["rid"]: fact_layer._context_tokens(row["content"]) for _, row in ranked}
        weights = {term: math.log(1 + len(ranked) / (1 + sum(term in words for words in tokens.values())))
                   for term in terms}
        sparse = sorted(ranked, key=lambda pair: sum(weights[term] for term in terms & tokens[pair[1]["rid"]])
                        / math.sqrt(max(1, len(tokens[pair[1]["rid"]]))), reverse=True)
        selected_ids = set()
        weighted_size = 0
        ranking_documents = {}
        query_size = min(MAX_QUERY_BYTES, len(query.encode("utf-8")))
        # Interleave recall paths before applying the provider input ceiling;
        # trimming a long pool must not remove every sparse-only candidate.
        interleaved = [pair for pair_group in zip(ranked, sparse) for pair in pair_group]
        fallback, fallback_ids = [], set()
        for pair in interleaved:
            if pair[1]['rid'] not in fallback_ids:
                fallback.append(pair)
                fallback_ids.add(pair[1]['rid'])
        # Keep both recall paths when the optional provider is busy or fails.
        # Falling all the way back to dense rank would strand an older
        # definition behind many nearly identical new observations.
        if all(a[1]['rid'] == b[1]['rid'] for a, b in zip(fallback, ranked)):
            fallback = ranked
        for pair in interleaved:
            if len(head) >= 64:
                break
            if pair[1]["rid"] in selected_ids:
                continue
            document = _ranking_document(query, pair[1])
            needed = query_size + len(document.encode("utf-8"))
            if weighted_size + needed > MAX_WEIGHTED_INPUT_BYTES:
                break
            head.append(pair)
            selected_ids.add(pair[1]["rid"])
            ranking_documents[pair[1]['rid']] = document
            weighted_size += needed
        if len(head) < 2:
            return fallback
        documents = [ranking_documents[row['rid']] for _, row in head]
        try:
            order = client.rerank(query, documents)
            if (not isinstance(order, list) or len(order) != len(head)
                    or any(type(index) is not int for index in order)
                    or set(order) != set(range(len(head)))):
                return fallback
        except Exception:
            return fallback
        return [head[index] for index in order] + [pair for pair in ranked if pair[1]["rid"] not in selected_ids]

    def _search_raw(
        self,
        conn: sqlite3.Connection,
        user_id: str,
        qvec: list[float],
        limit: int,
        recency_mult: float = 1.0,
        query: str = "",
        *,
        exclude_source_rids: set[int] | None = None,
    ) -> list[dict]:
        if limit <= 0:
            return []
        rows = conn.execute(
            "SELECT i.rid, i.session_id, i.role, i.content, i.created_at, i.timestamp_origin, i.seq, v.embedding FROM items i"
            " JOIN vec_items v ON v.rowid = i.rid"
            " WHERE i.user_id = ? AND i.status = 'active' ORDER BY i.seq, i.rid",
            (user_id,),
        ).fetchall()
        exclusions = query_layer.plan(query)["excluded_categories"]
        if exclusions:
            private_tables = fact_layer.excluded_table_segments(rows, exclusions)
            # Anonymous cell values have their field meaning in an earlier
            # schema. Withhold their source before ranking and raw rescue;
            # sentence-level assignment matching cannot identify these cells.
            # Compact views can still quote safe sentences in a mixed source.
            rows = [row for row in rows if row['rid'] not in private_tables
                    and not query_layer.excluded_text(row["content"], exclusions)]
        query = query_layer.plan(query)["positive_query"]
        if exclusions and not query.strip():
            return []
        if not rows:
            return []

        if np is not None:
            mat = np.frombuffer(b"".join(r["embedding"] for r in rows), dtype=np.float32)
            mat = mat.reshape(len(rows), -1)
            q = np.asarray(qvec, dtype=np.float32)
            sims = (mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)) @ (
                q / (np.linalg.norm(q) + 1e-9)
            )
            sims = sims.tolist()
        else:
            # Keep exact, per-user cosine scoring when NumPy is unavailable.
            # A global ANN pool can be filled entirely by other users' vectors.
            q_norm = math.sqrt(sum(v * v for v in qvec)) + 1e-9
            sims = []
            for r in rows:
                vec = array.array("f")
                vec.frombytes(r["embedding"])
                if len(vec) != len(qvec):
                    raise ValueError("embedding dimensions do not match query")
                norm = math.sqrt(sum(v * v for v in vec)) + 1e-9
                dot = sum(v * q for v, q in zip(vec, qvec))
                sims.append(dot / (norm * q_norm))

        excluded = set(exclude_source_rids or ())
        if _LINK_REFERENCE.search(query):
            excluded.update(link_layer.conflicting_source_ids(rows, query))
        ranked = [(float(sim), row) for row, sim in zip(rows, sims)
                  if row["rid"] not in excluded]
        ranked.sort(key=lambda pair: pair[0], reverse=True)

        attribute_quotes: dict[int, str] = {}
        attribute_corrections: set[int] = set()
        attribute_fallback: dict[int, str] = {}
        requested_attribute = _single_personal_attribute(query)
        if requested_attribute:
            predicates = {requested_attribute}
            if requested_attribute == "name":
                predicates.add("nickname")
            attribute_words = fact_layer._tokens(fact_layer._ATTRIBUTE_TERMS.get(requested_attribute, ""))
            direct_self = re.compile(
                r"\bmy\s+(?:" + "|".join(re.escape(word) for word in attribute_words if word.isascii())
                + r")\b|我的(?:" + "|".join(re.escape(word) for word in attribute_words if not word.isascii()) + ")", re.I,
            )
            # First-person grammar is independent of the attribute extractor.
            # A dense near-tie alone must not pull arbitrary unparsed records
            # into a narrowly scoped personal-property response.
            self_reference = re.compile(
                r"\b(?:i|my|me|myself|je|mon|ma|mes|moi|yo|mi|mis|eu|meu|minha|"
                r"ich|mein|meine|mich)\b|\bj['’]|我", re.I)
            for _, row in ranked:
                if not fact_layer._speaker_relevant({
                    "subject": "self", "predicate": requested_attribute, "role": row["role"],
                }, query):
                    continue
                source_spans = dependency_layer.source_sentence_spans(row["content"])
                sentences = [row["content"][start:end].strip() for start,end in source_spans]
                header_spans = fact_layer._assertion_header_spans(row["content"])
                governing_headers = {}
                previous_header = None
                for index, (start, end) in enumerate(source_spans):
                    if fact_layer._ASSERTION_HEADER.fullmatch(sentences[index]):
                        previous_header = index
                    if previous_header is not None:
                        source_part = row["content"][start:end]
                        anchor_pos = start + len(source_part) - len(source_part.lstrip())
                        if fact_layer._assertion_header_block(row["content"], anchor_pos, header_spans):
                            governing_headers[index] = previous_header
                matched_indexes, unresolved = set(), []
                for sentence_index, sentence in enumerate(sentences):
                    parsed = fact_layer.extract_facts(sentence)
                    correction = bool(_CORRECTION_CUE.search(sentence) and direct_self.search(sentence))
                    known_self = any(fact.subject == "self" and fact.predicate in predicates for fact in parsed)
                    known_other = any(fact.subject != "self" and fact.predicate in predicates for fact in parsed)
                    # A first-person source can discuss the requested property
                    # without matching the extractor's assertion grammar. Keep
                    # its literal words; do not manufacture an updated fact.
                    unparsed_self = (not known_other and self_reference.search(sentence)
                                     and bool(attribute_words & fact_layer._tokens(sentence)))
                    if correction or known_self or unparsed_self:
                        matched_indexes.add(sentence_index)
                        if correction:
                            attribute_corrections.add(row["rid"])
                    elif not parsed:
                        # A recognizable key/value statement about a different
                        # personal property is not an ambiguous update to this
                        # one, even if its embedding is unusually close.
                        field = re.search(
                            r"\bmy\s+([^.!?\n:：=]{1,60}?)\s*(?:\b(?:is|are|was|were)\b|[:：=])"
                            r"|我的([^。！？\n:：=]{1,30}?)(?:是|为|[:：=])", sentence, re.I)
                        field_text = next((part for part in field.groups() if part), "") if field else ""
                        if not field or attribute_words & fact_layer._tokens(field_text):
                            unresolved.append(sentence)
                if matched_indexes:
                    retained_indexes = set(matched_indexes)
                    retained_indexes.update(dependency_layer.dependency_indexes(sentences, matched_indexes))
                    for index in matched_indexes:
                        # A source header governs its contiguous paragraph,
                        # even when a preceding quoted line concerns another
                        # property. Keep only that exact attribution header,
                        # rather than exposing intervening personal fields.
                        if index in governing_headers:
                            retained_indexes.add(governing_headers[index])
                        previous = index - 1
                        while previous >= 0 and fact_layer._CONTEXT_QUALIFIER.search(sentences[previous].strip()):
                            retained_indexes.add(previous)
                            previous -= 1
                        following, qualifying = index + 1, False
                        previous_topics = fact_layer._context_tokens(sentences[index])
                        while following < len(sentences):
                            sentence = sentences[following]
                            topics = fact_layer._context_tokens(sentence)
                            cue = bool(fact_layer._CONTEXT_QUALIFIER.search(sentence.strip()))
                            continuation = qualifying and bool(previous_topics & topics)
                            if not (cue or continuation):
                                break
                            retained_indexes.add(following)
                            qualifying, previous_topics = True, topics
                            following += 1
                    # A quote can span several source sentences. Selecting
                    # only its requested field must not erase its quotation
                    # scope. Preserve missing original delimiters, without
                    # copying other fields merely to reach the closing quote.
                    opening_quotes, closing_quotes = {}, {}
                    selected_spans = [(index, source_spans[index]) for index in sorted(retained_indexes)]
                    for quotation in fact_layer._ASSERTION_QUOTES.finditer(row["content"]):
                        left, right = quotation.span()
                        members = [index for index,(start,end) in selected_spans if start < right and end > left]
                        if not members:
                            continue
                        width = 3 if quotation.group().startswith('```') else 1
                        if not any(start <= left < end for _,(start,end) in selected_spans):
                            opening_quotes.setdefault(members[0], []).append(row["content"][left:left+width])
                        if not any(start < right <= end for _,(start,end) in selected_spans):
                            closing_quotes.setdefault(members[-1], []).append(row["content"][right-width:right])
                    pieces = []
                    for index in sorted(retained_indexes):
                        pieces.extend(opening_quotes.get(index, ()))
                        pieces.append(sentences[index])
                        pieces.extend(closing_quotes.get(index, ()))
                    attribute_quotes[row["rid"]] = "\n".join(pieces)
                elif unresolved and self_reference.search(" ".join(unresolved)):
                    attribute_fallback[row["rid"]] = "\n".join(unresolved)
            # Parsing failure is uncertainty, not evidence of irrelevance.
            # Preserve semantically competitive unresolved statements while
            # continuing to quote only the relevant property from known turns.
            if attribute_quotes:
                reference = max(score for score,row in ranked if row["rid"] in attribute_quotes)
                retained = []
                for score,row in ranked:
                    rid = row["rid"]
                    if rid in attribute_quotes:
                        retained.append((score,row))
                    elif rid in attribute_fallback and score >= reference - .10:
                        attribute_quotes[rid] = attribute_fallback[rid]
                        retained.append((score,row))
                ranked = retained

        scope, topics, scope_reason = fact_layer.source_context_scope(rows, query)
        context_quotes: dict[int, str] = {}
        if scope == "topic-scoped" and topics:
            # Share exactly the same scope selector as compact source views.
            # It can retain a schema's following observation block without
            # admitting every unrelated record in the same session.
            selected_units = fact_layer.context_units_for_query(
                [dict(row) for row in rows], query,
            )
            by_source: dict[int, list[str]] = {}
            for unit in selected_units:
                by_source.setdefault(unit["source_rid"], []).append(unit["text"])
            if by_source:
                best_similarity = ranked[0][0] if ranked else 0.0
                ranked = [pair for pair in ranked if pair[1]["rid"] in by_source
                          or best_similarity - pair[0] <= .12]
                for _, row in ranked:
                    if row["rid"] in by_source:
                        quote = "\n".join(by_source[row["rid"]])
                        context_quotes[row["rid"]] = (
                            row["content"] if _norm_text(quote) == _norm_text(row["content"]) else quote
                        )

        correction_rids: set[int] = set()
        if ranked and not _chronology_query(query):
            topic = fact_layer._context_tokens(query)
            best_similarity = ranked[0][0]
            corrections = [pair for pair in ranked
                if pair[1]["rid"] in attribute_corrections or (
                    best_similarity - pair[0] <= .15 and _CORRECTION_CUE.search(pair[1]["content"])
                    and len(topic & fact_layer._context_tokens(pair[1]["content"])) >= 2)]
            if corrections:
                correction = max(corrections, key=lambda pair: (pair[1]["seq"], pair[1]["rid"]))
                ranked.remove(correction)
                ranked.insert(0, correction)
                correction_rids.add(correction[1]["rid"])

        if ranked and _original_state_query(query):
            # An explicit request for the original value can be answered by a
            # later description with very similar embedding vocabulary. Prefer
            # the first source only inside the same lexical topic and a bounded
            # semantic neighborhood, never across the user's whole history.
            topic = fact_layer._tokens(_INITIAL_QUERY.sub(" ", query))
            best_similarity, best_row = ranked[0]
            covered_topic = topic & fact_layer._tokens(best_row["content"])
            if len(covered_topic) >= 2:
                eligible = [pair for pair in ranked
                            if best_similarity - pair[0] <= .1 + 1e-9
                            and covered_topic <= (topic & fact_layer._tokens(pair[1]["content"]))]
                if len(eligible) > 1:
                    first = min(eligible, key=lambda pair: (pair[1]["seq"], pair[1]["rid"]))
                    ranked.remove(first)
                    ranked.insert(0, first)

        # Semantic relevance remains the primary signal. Only an explicit
        # current-state query may favor a newer source among cosine near-ties;
        # this never moves a message across a gap larger than 0.01. There is no
        # age bias for an ordinary lookup or a question about an earlier state.
        if _current_query(query) and recency_mult > 0:
            tolerance = min(0.01, max(0.0, CONFIG.recency_weight))
            start = 0
            while start < len(ranked):
                stop = start + 1
                best = ranked[start][0]
                while stop < len(ranked) and best - ranked[stop][0] <= tolerance + 1e-9:
                    stop += 1
                ranked[start:stop] = sorted(ranked[start:stop],
                                            key=lambda pair: pair[1]["seq"], reverse=True)
                start = stop

        ranked = self._rerank_rows(query, ranked)
        selected: list[tuple[float, sqlite3.Row]] = []
        seen: set[int] = set()

        def take(pair: tuple[float, sqlite3.Row]) -> None:
            _, row = pair
            if len(selected) >= limit or row["rid"] in excluded:
                return
            # Two rows are two observations, even when the speaker, timestamp
            # and wording match. Network retries are deduplicated at Add via
            # request_id; retrieval must not erase genuine repetitions.
            key = row["rid"]
            if key not in seen:
                seen.add(key)
                selected.append(pair)

        chronology = _sequence_query(query)
        if chronology and limit >= 3:
            sessions: dict[str, list[tuple[float, sqlite3.Row]]] = {}
            positions: dict[int, int] = {}
            eligible_rids = {row["rid"] for _, row in ranked}
            for row, sim in zip(rows, sims):
                if scope != "ordinary" and row["rid"] not in eligible_rids:
                    continue
                members = sessions.setdefault(row["session_id"], [])
                positions[row["rid"]] = len(members)
                members.append((float(sim), row))
            # One preceding/following turn gives a retrieved event its local
            # ordering context. Expansion stays in the same user's session.
            for pair in ranked[:min(4, max(1, limit // 3))]:
                row = pair[1]
                members = sessions[row["session_id"]]
                position = positions[row["rid"]]
                # Order a small local group, then continue with the next best
                # semantic anchor. Never seq-sort the entire retrieved set.
                if position > 0:
                    take(members[position - 1])
                take(pair)
                if position + 1 < len(members):
                    take(members[position + 1])
        for pair in ranked:
            take(pair)
            if len(selected) >= limit:
                break
        results = []
        for score, row in selected:
            content = attribute_quotes.get(row["rid"], context_quotes.get(row["rid"], row["content"]))
            stored_at = _iso(row["created_at"])
            timestamp_origin = row["timestamp_origin"]
            anchor = stored_at if timestamp_origin == "message.timestamp" else None
            time_note = (
                "provided record timestamp may anchor relative expressions, not event occurrence time"
                if anchor is not None else
                "no provided temporal anchor; do not resolve relative dates from ingestion time"
            )
            session = json.dumps(row["session_id"], ensure_ascii=False)
            long_content = len(content) > 3200
            excerpted = long_content or content != row["content"]
            source_chars = len(row["content"])
            if long_content:
                content = _query_preview(content, query, source_text=row['content'])
            if excerpted:
                content = (
                    "[QUERY-FOCUSED SOURCE EXCERPTS; additional source text omitted; "
                    "complete source retained at source_rid]\n" + content
                )
            content = (
                f"[SOURCE source_rid={row['rid']} session={session} seq={row['seq']} "
                f"role={row['role']} recorded_at={stored_at} timestamp_origin={timestamp_origin}; "
                + time_note + "]\n" + content
            )
            result = {"id": f"mem_{row['rid']}", "content": content,
                      "score": round(0.5 + 0.4 * score, 6), "created_at": stored_at}
            result.update(source_rid=row["rid"], session_id=row["session_id"],
                          seq=row["seq"], role=row["role"], stored_at=stored_at,
                          recorded_at=stored_at, timestamp_origin=timestamp_origin,
                          relative_time_anchor=anchor)
            if row["rid"] in attribute_quotes:
                result["requested_attribute"] = requested_attribute
            if row["rid"] in correction_rids:
                result["explicit_correction"] = True
            if excerpted:
                result.update(excerpt=True, source_chars=source_chars)
            results.append(result)
        return results

    # ------------------------------------------------------- governance
    def supersede_old(self, user_id: str, keyword: str, except_seq: int) -> int:
        """Mark earlier active records mentioning a keyword as superseded.

        Retained for API compatibility. Disabled by default: measured against the
        real AML DB this rule hid 1480 of 3974 rows by treating pronouns as
        topics (see config.governance_enabled).
        """
        if not keyword:
            return 0
        like = f"%{keyword.lower()}%"
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE items SET status='superseded' WHERE user_id=? AND status='active'"
                " AND seq < ? AND lower(content) LIKE ?",
                (user_id, except_seq, like),
            )
            conn.commit()
            return cur.rowcount


def _flatten_content(part) -> str:
    """Multimodal content parts -> plain text for the text track."""
    if isinstance(part, str):
        return part
    if isinstance(part, list):
        out = []
        for p in part:
            if isinstance(p, dict):
                if p.get("type") == "text":
                    out.append(str(p.get("text", "")))
                elif p.get("type") == "image_url":
                    out.append("[image]")
            else:
                out.append(str(p))
        return "\n".join(out)
    return str(part)


def _iso(ts: float) -> str:
    instant = datetime.fromtimestamp(ts, timezone.utc)
    precision = "seconds" if instant.microsecond == 0 else "milliseconds"
    return instant.isoformat(timespec=precision).replace("+00:00", "Z")
