"""Bounded, literal identifier links between already user-scoped sources.

This module does not infer entities, applicability, version precedence or truth.
It only supplies original background sources whose explicit dataset/project/
protocol identifier is anchored by both the query and an already retrieved
source.  No storage, clock, embeddings, network or mutable global index is used.
"""
from __future__ import annotations

import re
from typing import Iterable, Mapping


_LABELS = {
    "dataset": r"data[ _-]?sets?|jeu\s+de\s+donn[ée]es|conjunto\s+de\s+datos|数据集|資料集",
    "project": r"projects?|projet|proyecto|项目|專案",
    "protocol": r"protocols?|protocole|protocolo|协议|協議|规程|規程",
    "method": r"methods?|procedures?|algorithms?|方法|算法|流程",
}
_TYPED_ID = re.compile(
    r"(?<![A-Za-z0-9_])(?P<label>" + "|".join(
        "(?P<" + kind + ">" + pattern + ")" for kind, pattern in _LABELS.items()
    ) + r")(?![A-Za-z])(?P<marker>[ _-]*(?:(?:identifi(?:er|ant)|id|code)(?![A-Za-z])|编号|編號|标识|標識))?"
    r"\s*(?:[:：=#]|为|為|是)?\s*[\"'`]?"
    r"(?P<value>[A-Za-z0-9][A-Za-z0-9_.:/-]{1,63})(?![A-Za-z0-9_.:/-])",
    re.I,
)
_DEPENDENCIES = {
    "revocation": re.compile(
        r"\b(?:revok\w*|withdraw\w*|invalid\w*|retract\w*|discard\w*|"
        r"do\s+not\s+use|no\s+longer\s+valid)\b|撤销|撤回|作废|无效|失效|不可使用|不得使用", re.I),
    "schema": re.compile(
        r"\b(?:schema|columns?|fields?|input\s+(?:pair|tuple|format)|"
        r"units?|dimensions?|denotes?|represents?)\b|列义|列名|字段|模式|单位|输入格式", re.I),
    "definition": re.compile(
        r"\b(?:definitions?|defined|means?|meaning|conventions?|interpretation)\b|定义|含义|约定|解释", re.I),
    "calibration": re.compile(
        r"\b(?:calibrat\w*|normaliz\w*|normalis\w*|conver\w*|"
        r"subtract\w*|divid\w*|multipli\w*|offsets?|scal(?:e|ing))\b|校准|标定|归一|换算|减去|除以|乘以", re.I),
    "condition": re.compile(
        r"\b(?:conditions?|preconditions?|only|unless|provided|if|when|before|after|"
        r"exclu\w*|ignore\w*|except\w*|must|never|require\w*|valid|eligible)\b|条件|仅|只有|除非|如果|当|之前|之后|排除|忽略|例外|必须|禁止|不得|不要|有效", re.I),
}
_UNIT_BOUNDARY = re.compile(r"(?<=[.!?。！？])\s+|(?<=[。！？])(?=\S)|(?<=;)\s+(?![A-Za-z_]\w*\s*=)|\n+")
_PERSONAL_CLAUSE = re.compile(
    r"^(?:(?:my|his|her|their|your|mon|ma|mes|mi|mis|meu|minha)\b|"
    r"[A-Z][a-z]+['’]s\b|我(?:的)?|他(?:的)?|她(?:的)?)", re.I)
_TECHNICAL_POSSESSIVE = re.compile(r"^(?:their|its)\s+(?:units?|columns?|fields?|dimensions?)\b", re.I)
_PERSONAL_ASSIGNMENT = re.compile(
    r"\b(?:my|his|her|their|your|our)\s+(?:(?:private|personal|home|mobile|medical)\s+){0,2}"
    r"(?:address|phone|telephone|email|passcode|password|account|diagnosis|code)(?:\s+number)?\s*(?:is|are|[:=])\s*\S"
    r"|(?:我的|他的|她的|他们的|你的)(?:私人|家庭)?(?:地址|住址|电话|手机号|邮箱|密码|诊断)(?:是|为|[:：])\s*\S", re.I)
_ELSE_PREFIX = re.compile(r"^\s*otherwise\b|^\s*否则", re.I)
_CONTROL_PREFIX = re.compile(
    r"^(?:only|unless|provided|if|when|before|after|exclude\w*|ignore\w*|"
    r"except\w*|do\s+not|never|must|valid|otherwise|however|but)\b"
    r"|^(?:仅|只有|除非|如果|当|之前|之后|排除|忽略|例外|必须|禁止|不得|不要|有效|否则|不过|但是)", re.I)
_DOMAIN_NOUN = re.compile(
    r"\b(?:data|dataset|readings?|samples?|observations?|measurements?|trials?|"
    r"schema|columns?|fields?|inputs?|outputs?|calibrat\w*|rules?|protocols?|"
    r"polic\w*|experiments?|batches|records?|"
    r"(?:operating|operational|eligibility|validity|acceptance|environmental|calibration)"
    r"\s+(?:preconditions?|conditions?|criteria|requirements?|constraints?))\b"
    r"|数据|读数|样本|观测|测量|实验|列|字段|校准|规则|协议|运行条件|工作条件|有效性条件|适用条件", re.I)
_UNIT_VALUE = re.compile(
    r"\b(?:seconds?|milliseconds?|microseconds?|minutes?|hours?|met(?:er|re)s?|"
    r"watts?|joules?|pascals?|kPa|MPa|bar|torr|volts?|amperes?|lit(?:er|re)s?|"
    r"grams?|kilograms?|degrees?|percent|dimensionless|counts?)\b|毫秒|秒|分钟|小时|帕|瓦|米|升|克|百分比", re.I)
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}")
_FUNCTION_WORDS = frozenset("the and that this those these with from only before after however otherwise for not are was has have been".split())


def _units(text: str) -> list[tuple[int, int, str]]:
    """Literal sentence/line units; decimal points are not sentence ends."""
    result, start = [], 0
    for match in list(_UNIT_BOUNDARY.finditer(text)) + [None]:
        end = match.start() if match else len(text)
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end-1].isspace():
            end -= 1
        if end > start:
            result.append((start, end, text[start:end]))
        start = match.end() if match else len(text)
    return result


def _topics(text: str) -> set[str]:
    return {word.casefold() for word in _WORD.findall(text)} - _FUNCTION_WORDS


def _background_quote(content: str, links: list[dict]):
    """Keep an ID's contiguous background block, stopping at a new topic.

    The selector is positive: an unrelated sentence is not copied merely
    because it shares the source turn. In particular, personal-property prose
    is a scope boundary unless it explicitly describes a schema/calibration.
    """
    units = _units(content)
    kept, evidence = set(), set()
    for index, (start, end, text) in enumerate(units):
        matching = any(start <= at < end for link in links for at, _ in link["source_spans"])
        if not matching:
            continue
        typed = _mentions(text)
        stripped = text
        removal = [mention["typed_span"] for mention in typed]
        if not typed:
            removal = [[at-start, last-start] for link in links
                       for at, last in link["source_spans"] if start <= at < end]
        for a, b in sorted(removal, reverse=True):
            stripped = stripped[:a] + stripped[b:]
        stripped = stripped.strip(" .,:;：=。！？；!?\t[]()'\"`")
        header = not stripped

        def background(part: str, *, continuation: bool = False):
            types = {kind for kind, pattern in _DEPENDENCIES.items() if pattern.search(part)}
            strong = types & {"schema", "calibration", "definition"}
            if personal_boundary(part):
                return set()
            if strong:
                return types
            if "revocation" in types and (_DOMAIN_NOUN.search(part) or _CONTROL_PREFIX.search(part)):
                return types
            if "condition" in types and (_CONTROL_PREFIX.search(part) or _DOMAIN_NOUN.search(part)):
                return types
            return {"condition"} if continuation else set()

        def personal_boundary(part: str):
            # 'Their units' refers to already declared columns, not people.
            # The narrow exception requires prior schema evidence and never
            # admits an embedded personal field/value assignment.
            if _PERSONAL_ASSIGNMENT.search(part):
                return True
            if not _PERSONAL_CLAUSE.search(part.strip()):
                return False
            return not ("schema" in evidence and _TECHNICAL_POSSESSIVE.search(part.strip())
                        and _DEPENDENCIES["schema"].search(part))

        types = background(stripped)
        if (not types and len(typed) > 1
                and re.search(r"\b(?:uses?|references?|follows?|adopts?)\b|引用|采用|使用", text, re.I)):
            types = {"definition"}
        if not (header or types):
            continue
        kept.add(index)
        evidence.update(types)
        previous = stripped or text
        qualifying = bool(types & {"condition", "revocation"})
        following = index + 1
        while following < len(units):
            _, _, part = units[following]
            if _mentions(part):
                break
            if personal_boundary(part):
                break
            continuation = qualifying and (bool(_topics(previous) & _topics(part))
                                            or bool(_ELSE_PREFIX.search(part)))
            types = background(part, continuation=continuation)
            if not types and "schema" in evidence and _UNIT_VALUE.search(part):
                types = {"schema"}
            if (not types and "schema" in evidence and re.search(r"[|\t]|,.*[A-Za-z_]",part)
                    and len(re.findall(r"[A-Za-z_]\w*",part)) >= 2
                    and not re.search(r"\d",part)):
                types = {"schema"}
            numeric_block = ("schema" in evidence and len(re.findall(r"[-+]?\d+(?:\.\d+)?", part)) >= 2
                             and bool(re.search(r"[|\t]|\w+\s*=", part)))
            if not types and not numeric_block:
                break
            kept.add(following)
            evidence.update(types or {"measurement"})
            previous, qualifying = part, bool(types & {"condition", "revocation"})
            following += 1
    if not evidence:
        return "", [], []
    spans = [[units[index][0], units[index][1]] for index in sorted(kept)]
    quote = "\n".join(content[start:end] for start, end in spans)
    return quote, spans, [kind for kind in (*_DEPENDENCIES, "measurement") if kind in evidence]


def _strong_identifier(value: str) -> bool:
    """Bare references need an identifier shape, rather than a person's name."""
    if len(value) < 3 or not re.search(r"[A-Za-z]", value):
        return False
    if re.search(r"[0-9]", value):
        return True
    parts = re.split(r"[-_:/]", value)
    return (len(parts) >= 2 and all(part and part.isascii() and part.isalpha() for part in parts)
            and (len(parts) >= 3 or any(part.isupper() for part in parts)))


def _mentions(text: str) -> list[dict]:
    out = []
    for match in _TYPED_ID.finditer(text):
        value = match.group("value").rstrip(".:/")
        if len(value) < 3:
            continue
        # A labelled ID can be alphabetical. Without an ID marker, require a
        # a structured identifier so "project Rowan" cannot join people.
        if (not match.group("marker") and not _strong_identifier(value)
                and not (value.isalpha() and value.isupper())):
            continue
        kind = next(kind for kind in _LABELS if match.group(kind) is not None)
        start = match.start("value")
        out.append({"kind": kind, "value": value, "span": [start, start + len(value)],
                    "typed_span": [match.start(), start + len(value)]})
    return out


def _literal_spans(text: str, value: str) -> list[list[int]]:
    # No substring linking (DS-47 is different from DS-470 and DS-47-B).
    return [[match.start(), match.end()] for match in re.finditer(
        r"(?<![A-Za-z0-9_.:/-])" + re.escape(value)
        + r"(?![A-Za-z0-9_/-]|[.:][A-Za-z0-9_])", text)]


def _rid(row: Mapping) -> int | None:
    raw = row.get("source_rid", row.get("rid"))
    try:
        return int(raw) if raw is not None and not isinstance(raw, bool) else None
    except (TypeError, ValueError, OverflowError):
        return None


def select_linked_sources(
    rows: Iterable[Mapping], query: str, seed_rids: Iterable[int], *,
    max_sources: int = 8, max_chars: int = 12000,
    include_seed_rids: Iterable[int] = (),
) -> list[dict]:
    """Return additional original background sources with auditable links.

    ``rows`` must be active records for one user; dicts and sqlite.Row inputs
    are accepted. Both ``rid`` and source-view ``source_rid`` are supported.
    Caller source metadata, including role/session/time, remains unchanged.

    Only identifiers present in the query AND a seed source may be followed.
    Typed identifiers are matched as (dataset/project/protocol, literal value).
    Method/procedure/algorithm labels share a method namespace. The same
    query-and-seed anchoring and literal-ID guards apply to these sources.
    Bare references require a known, unambiguous structured ID (digits or
    separated upper-case/multipart identifiers), not an ordinary person name;
    plain names require an explicit typed ID in each source. New sources do not
    add identifiers to the frontier. There is no transitive/recency inference.

    The output excludes seeds by default. A caller may explicitly allow only
    its already excerpted seed RIDs via ``include_seed_rids``; those sources
    still require the identical query/source identifier and safe quote rules.
    Their ``seed_background`` quote supplements the SAME original source,
    never an additional observation. Complete seeds should not be allowed.
    Each copy has ``source_rid``, ``dependency_types``
    and ``evidence_links`` with original character spans and seed provenance.
    Original ``content`` is neither rewritten nor truncated. Append ``quote``
    to a context, NOT the complete content: ``quote_spans`` identify its literal
    original units and exclude unrelated sentences in the same source turn.
    Budgets count whole quotes and can return fewer sources for oversized
    records. Caller must still label these as source excerpts with provenance.
    """
    if max_sources <= 0 or max_chars <= 0:
        return []
    materialized = [dict(row) for row in rows]
    users = {row["user_id"] for row in materialized if row.get("user_id") is not None}
    if len(users) > 1:
        raise ValueError("identifier linking requires sources for a single user")
    seeds = {int(rid) for rid in seed_rids}
    included_seeds = seeds & {int(rid) for rid in include_seed_rids}
    records = []
    for position, row in enumerate(materialized):
        rid = _rid(row)
        if rid is None or row.get("status", "active") != "active":
            continue
        content = str(row.get("content", row.get("text", "")))
        records.append((rid, row, content, _mentions(content), position))
    if not records or not seeds:
        return []
    seed_records = [record for record in records if record[0] in seeds]
    if not seed_records:
        return []

    query_typed = _mentions(query)
    catalog: dict[str, set[str]] = {}
    for _, _, _, mentions, _ in records:
        for mention in mentions:
            catalog.setdefault(mention["value"], set()).add(mention["kind"])
    for mention in query_typed:
        catalog.setdefault(mention["value"], set()).add(mention["kind"])

    def references(text: str, mentions: list[dict], key: tuple[str, str]):
        kind, value = key
        typed = [mention["span"] for mention in mentions
                 if (mention["kind"], mention["value"]) == key]
        if typed:
            return typed, "typed-identifier"
        # A differently typed mention of the same literal is not a reference
        # to this domain. Ambiguous bare IDs cannot silently join domains.
        if any(mention["value"] == value for mention in mentions):
            return [], ""
        if _strong_identifier(value) and catalog.get(value) == {kind}:
            spans = _literal_spans(text, value)
            if spans:
                return spans, "known-literal-identifier"
        return [], ""

    query_keys = {(mention["kind"], mention["value"]) for mention in query_typed}
    for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_.:/-]*", query):
        value = token.rstrip(".:/")
        if _strong_identifier(value) and value in catalog:
            query_keys.update((kind, value) for kind in catalog[value])
    anchored: dict[tuple[str, str], dict] = {}
    for key in sorted(query_keys):
        kind, value = key
        query_spans, query_mode = references(query, query_typed, key)
        if not query_spans:
            continue
        seed_matches = []
        for rid, _, content, mentions, _ in seed_records:
            spans, _ = references(content, mentions, key)
            if spans:
                seed_matches.append({"source_rid": rid, "spans": spans})
        if seed_matches:
            anchored[key] = {"identifier_kind": kind, "identifier": value,
                             "query_spans": query_spans, "query_match": query_mode,
                             "seed_sources": seed_matches}
    if not anchored:
        return []

    candidates = []
    for rid, row, content, mentions, position in records:
        if rid in seeds and rid not in included_seeds:
            continue
        links = []
        for key, anchor in sorted(anchored.items()):
            spans, match_mode = references(content, mentions, key)
            if spans:
                links.append({"reason": "same-explicit-identifier", **anchor,
                              "source_spans": spans, "source_match": match_mode,
                              "traversal": "single-hop"})
        if not links:
            continue
        quote, quote_spans, dependencies = _background_quote(content, links)
        if not quote:
            continue
        copy = dict(row)
        copy.setdefault("content", content)
        copy.update(source_rid=rid, dependency_types=dependencies, evidence_links=links,
                    quote=quote, quote_spans=quote_spans)
        if rid in included_seeds:
            copy['seed_background'] = True
        priority = 0 if "revocation" in dependencies else 1
        candidates.append((priority, position, copy))

    selected, seen = [], set()
    remaining = max_chars
    for _, _, row in sorted(candidates, key=lambda item: (item[0], item[1])):
        rid = row["source_rid"]
        size = len(row["quote"])
        if rid in seen or size > remaining:
            continue
        selected.append(row)
        seen.add(rid)
        remaining -= size
        if len(selected) >= max_sources:
            break
    return selected


_NEGATED_REFERENCE = re.compile(
    r"\b(?:do\s+not\s+use|don['’]?t\s+use|never\s+use|instead|rather\s+than|"
    r"not\s+(?:applicable|valid)|no\s+longer|withdraw\w*|revok\w*|invalid\w*)\b"
    r"|不要使用|不得使用|不适用|不再|撤销|撤回|作废|而应|改用", re.I)


def conflicting_source_ids(rows: Iterable[Mapping], query: str) -> set[int]:
    """Return only records explicitly headed by a different same-kind ID.

    This is a conservative scope exclusion, not a relevance threshold. A plain
    reference, negation/revocation, unknown source, comparison or multi-ID
    relationship bridge remains eligible for normal semantic retrieval. The
    query must itself name a typed dataset/project/protocol identifier. No
    source is excluded merely for omitting that identifier.
    """
    materialized = [dict(row) for row in rows]
    users = {row["user_id"] for row in materialized if row.get("user_id") is not None}
    if len(users) > 1:
        raise ValueError("identifier scope requires sources for a single user")
    requested: dict[str, set[str]] = {}
    for mention in _mentions(query):
        requested.setdefault(mention["kind"], set()).add(mention["value"])
    if not requested:
        return set()
    excluded = set()
    for row in materialized:
        rid = _rid(row)
        if rid is None or row.get("status", "active") != "active":
            continue
        content = str(row.get("content", row.get("text", "")))
        mentions = _mentions(content)
        if len({(mention["kind"], mention["value"]) for mention in mentions}) != 1:
            continue
        mention = mentions[0]
        kind, value = mention["kind"], mention["value"]
        if kind not in requested or value in requested[kind]:
            continue
        # Literal mention of any requested ID is evidence of a possible
        # relationship even without a repeated type label.
        if any(_literal_spans(content, target) for target in requested[kind]):
            continue
        if _NEGATED_REFERENCE.search(content):
            continue
        start, end = mention["typed_span"]
        line_start = content.rfind("\n", 0, start) + 1
        prefix = content[line_start:start].strip()
        prefix = re.sub(r"^(?:\#{1,6}|[-*]|\d+[.)])\s*", "", prefix)
        scoped_prefix = bool(re.fullmatch(r"(?:for|in|under|within|针对|用于|对于)", prefix, re.I))
        header = not prefix and bool(re.match(r"\s*(?:[:：.]|\n|$)", content[end:]))
        if scoped_prefix or header:
            excluded.add(rid)
    return excluded
