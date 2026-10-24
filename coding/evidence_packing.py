"""Pure, bounded packing of already user-scoped Coding source evidence.

Seed ranking is supplied by the caller. A single hop can add implementation,
correction or validation sources with explicit shared code anchors. Session or
record order alone never establishes a relationship or version precedence.
No original source is edited, summarized, executed or treated as a final answer.
"""
from __future__ import annotations

import re
from typing import Callable, Iterable, Mapping

_PATH = re.compile(r'(?<![\w.@+-])(?:[A-Za-z]:[\\/]|\.{0,2}[\\/])?(?:[\w.@+-]+[\\/])+[\w.@+-]+')
_FILE = re.compile(r'(?<![\w.@+-])[\w@+.-]+\.(?:py|pyi|js|jsx|ts|tsx|json|yaml|yml|toml|rs|go|java|kt|c|h|cpp|hpp|cs|rb|php|sh|sql|md|txt|log)\b', re.I)
_DEFINITION = re.compile(r'\b(?:def|class|function|fn|func|struct|interface|trait|enum)\s+([A-Za-z_]\w*)')
_CALL = re.compile(r'\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\(')
_QUALIFIED = re.compile(r'\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+\b')
_WORD = re.compile(r'\b[A-Za-z_]\w*\b')
_QUOTED_SYMBOL = re.compile(r'`([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)`')
_GENERIC_SYMBOLS = frozenset(('get set main run test init setup __init__ print len str int float dict list '
    'tuple set bool object pop keys values items append extend assert return if while for '
    'json api http https readme error warning warn info debug trace pass passed fail failed success ok '
    'none true false').split())
_GENERIC_FILES = frozenset(('readme readme.md readme.rst readme.txt license license.md license.txt').split())
_PHASES = {
    'implementation': re.compile(r'\b(?:implemented?|implementation|patch|diff|changed?|replaced?|updated?)\b|'
        r'(?m:^\s*(?:async\s+)?(?:def|class|function|fn|func|struct)\s+)|实现|补丁|修改', re.I),
    'correction': re.compile(r'\b(?:correction|regression|revert\w*|retry|instead|first\s+attempt|traceback)\b|'
        r'\b(?:fix|fixed|repair|repaired)\b|纠正|修正|回退|重试', re.I),
    'validation': re.compile(r'\b(?:test(?:s|ed|ing)?|pytest|unittest|validation|validated?|verified|verification|'
        r'passed?|assert(?:ion)?|reproduced?|reproduction)\b|测试|验证|复现', re.I),
    'completion': re.compile(r'\b(?:final|complete(?:d)?|resolved|landed)\b|最终|完成|解决', re.I),
}


def _rid(row):
    value = row.get('source_rid', row.get('rid'))
    if type(value) is not int:
        raise ValueError('source rows must have an integer rid/source_rid')
    return value


def _paths(text):
    spans = [(match.start(),match.end(),match.group()) for match in _PATH.finditer(text)]
    found = {value.replace('\\','/').rstrip('.') for _,_,value in spans}
    for match in _FILE.finditer(text):
        # A basename inside a longer explicit path is not a second namespace.
        if not any(start <= match.start() and match.end() <= end for start,end,_ in spans):
            found.add(match.group().rstrip('.'))
    return {value for value in found if value.rsplit('/',1)[-1].casefold() not in _GENERIC_FILES}


def _symbols(text):
    found = set(_DEFINITION.findall(text)) | set(_CALL.findall(text)) | set(_QUOTED_SYMBOL.findall(text))
    found.update(_QUALIFIED.findall(text))
    for word in _WORD.findall(text):
        if ('_' in word or re.search(r'[a-z0-9][A-Z]|[A-Z]{2}[a-z]',word)
                or len(word)>=4 and word.isupper()):
            found.add(word)
    return {value for value in found if value.casefold() not in _GENERIC_SYMBOLS
            and not _FILE.fullmatch(value)}


def _features(row):
    text = str(row['content'])
    return dict(paths=_paths(text),symbols=_symbols(text),
                words=set(_WORD.findall(text)),
                definitions={value for value in _DEFINITION.findall(text)
                             if value.casefold() not in _GENERIC_SYMBOLS},
                phases={name for name,pattern in _PHASES.items() if pattern.search(text)})


def _related(seed, candidate, seed_features, candidate_features, query_features):
    if not candidate_features['phases']:
        return None
    paths = seed_features['paths']
    symbols = seed_features['definitions'] or seed_features['symbols']
    query_paths = paths & query_features['paths']
    query_symbols = ((symbols & query_features['symbols'])
                     | (seed_features['definitions'] & query_features['words']))
    # An explicit requested path/symbol narrows that anchor category. A prose
    # query can still use the retrieved source's own literal code anchors.
    if query_paths:paths=query_paths
    if query_symbols:symbols=query_symbols
    paths=set(sorted(paths)[:16])
    symbols=set(sorted(symbols)[:24])
    path_hits=paths & candidate_features['paths']
    symbol_hits=symbols & candidate_features['symbols']
    # Same-named functions in explicitly different files are not one identity.
    # Cross-file evidence can still name BOTH paths itself; no import graph or
    # basename alias is inferred to erase a conflicting explicit namespace.
    if paths and candidate_features['paths'] and not path_hits:
        return None
    # A query naming the seed definition does not license an unrelated
    # definition merely because it lives in the same file. A test/caller that
    # literally invokes the requested symbol remains useful evidence.
    if query_symbols and candidate_features['definitions'] and not symbol_hits:
        return None
    seed_session=seed.get('session_id')
    same_session=(isinstance(seed_session,str) and bool(seed_session.strip())
                  and seed_session==candidate.get('session_id'))
    if not path_hits and not symbol_hits:
        return None
    if not same_session and not (path_hits and symbol_hits):
        return None
    return dict(score=8*len(path_hits)+3*len(symbol_hits)+(2 if same_session else 0),
                phases=set(candidate_features['phases']),
                reason='same-session-explicit-code-anchor' if same_session else 'cross-session-explicit-path-and-symbol')


def _literal_key(row):
    # Equal text from a different speaker/session remains a different source
    # context. Order does not make one occurrence authoritative over another.
    return row.get('role'),row.get('session_id'),row['content']


def pack_evidence(rows: Iterable[Mapping], seed_rids: Iterable[int], query: str,
                  render: Callable, *, top_k: int = 100, char_budget: int = 48000,
                  max_source_chars: int = 9000) -> list[dict]:
    """Return bounded rendered evidence, retaining its literal source metadata.

    ``rows`` must already be scoped to ONE user. ``seed_rids`` is the caller's
    ranked original-source IDs. At most four distinct leading seed contexts
    can introduce up to three direct related sources apiece; added sources do
    not extend the frontier. Other ranked seeds retain their relative order.

    ``render(row_copy, query, max_chars) -> dict | None`` must provide its final
    evidence in ``content`` with all labels/wrappers included. It may return
    None if no faithful excerpt fits. Oversized/malformed results are withheld,
    never string-truncated here. This system's default budget is 48,000 Python
    characters, not an asserted platform limit. The aggregate also reserves
    two characters between records. Sources and input mappings are unchanged.
    """
    materialized=[dict(row) for row in rows]
    users={row.get('user_id') for row in materialized}
    if len(users)>1:
        raise ValueError('evidence packing requires one user-scoped source set')
    by_rid={}
    for row in materialized:
        rid=_rid(row)
        if rid in by_rid:
            raise ValueError('duplicate source IDs are ambiguous')
        if not isinstance(row.get('content'),str):
            raise ValueError('source content must be an original string')
        by_rid[rid]=row
    if type(top_k) is not int or type(char_budget) is not int or type(max_source_chars) is not int:
        raise ValueError('packing bounds must be integers')
    if top_k<=0 or char_budget<=0 or max_source_chars<=0:
        return []
    top_k=min(top_k,100)
    if not isinstance(query,str):raise ValueError('query must be a string')
    ranked=[]
    for rid in seed_rids:
        if type(rid) is int and rid in by_rid and rid not in ranked:
            ranked.append(rid)
    if not ranked:return []
    features={rid:_features(row) for rid,row in by_rid.items()}
    query_features=_features(dict(content=query))
    primary=[];primary_literals=set()
    for rid in ranked:
        literal=_literal_key(by_rid[rid])
        if literal not in primary_literals:
            primary.append(rid);primary_literals.add(literal)
        if len(primary)==4:break
    related={}
    for seed_rid in primary:
        seed=by_rid[seed_rid];matches=[]
        for rid,candidate in by_rid.items():
            if rid==seed_rid or _literal_key(candidate)==_literal_key(seed):continue
            relation=_related(seed,candidate,features[seed_rid],features[rid],query_features)
            if relation:matches.append((rid,relation))
        chosen=[];covered=set()
        while matches and len(chosen)<3:
            # Complementary source types beat repeated copies of a test log.
            matches.sort(key=lambda item:(-len(item[1]['phases']-covered),-item[1]['score'],
                ranked.index(item[0]) if item[0] in ranked else len(ranked),item[0]))
            rid,relation=matches.pop(0)
            if any(_literal_key(by_rid[rid])==_literal_key(by_rid[old]) for old,_ in chosen):continue
            chosen.append((rid,relation));covered.update(relation['phases'])
        related[seed_rid]=chosen
    plan=[];planned=set();seen_literals=set();deferred=[]
    def schedule(rid,reason,linked=None):
        if rid in planned:return
        planned.add(rid)
        item=(rid,reason,linked)
        literal=_literal_key(by_rid[rid])
        if literal in seen_literals:deferred.append(item)
        else:plan.append(item);seen_literals.add(literal)
    # Preserve the first ranked seed and interleave one context source, leaving
    # room for several independently ranked seed contexts before extra tails.
    for rid in primary:
        schedule(rid,'ranked-seed')
        if related[rid]:
            linked,relation=related[rid][0]
            schedule(linked,relation['reason'],rid)
    for offset in (1,2):
        for rid in primary:
            if len(related[rid])>offset:
                linked,relation=related[rid][offset]
                schedule(linked,relation['reason'],rid)
    for rid in ranked:schedule(rid,'ranked-seed')
    plan.extend(deferred)
    output=[];used=0;admitted=set()
    for rid,reason,linked in plan:
        if len(output)>=top_k:break
        separator=2 if output else 0
        remaining=char_budget-used-separator
        if remaining<=0:break
        # Related sources cannot consume the small reservations for remaining
        # independent primary seeds. The actual renderer determines fidelity.
        reserve=sum(min(1200,max_source_chars) for primary_rid in primary
                    if primary_rid not in admitted and primary_rid!=rid)
        cap=max_source_chars if linked is None else max(1200,max_source_chars//2)
        cap=min(cap,max_source_chars,remaining)
        if remaining>reserve:cap=min(cap,remaining-reserve)
        if cap<=0:continue
        rendered=render(dict(by_rid[rid]),query,cap)
        if (not isinstance(rendered,Mapping) or not isinstance(rendered.get('content'),str)
                or not rendered['content'].strip()):continue
        if len(rendered['content'])>cap or len(rendered['content'])>remaining:continue
        item=dict(rendered)
        item['source_rid']=rid
        for field in ('role','session_id','seq','timestamp_origin'):
            if field in by_rid[rid]:item[field]=by_rid[rid][field]
        item['evidence_reason']=reason
        if linked is not None:item['linked_seed_rid']=linked
        output.append(item);admitted.add(rid);used+=separator+len(item['content'])
    return output
