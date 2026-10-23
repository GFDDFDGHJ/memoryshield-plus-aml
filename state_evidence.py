"""Quote explicit later qualifications of extracted self assertions.

This selector does not change a fact's state, execute a condition, infer a new
value, query storage, or decide what is true. The caller MUST provide original
sources already restricted to the same user, the same source role, and strictly
later record order. In particular, ingestion order alone is not event order.

The intentionally narrow recognizer accepts a completed withdrawal of a named
self attribute, or an adjacent affirmative outcome for the same actor in one
original conditional source. Unclear coreference and unsupported forms produce
no result. Returned text is verbatim, suitable for a 'current applicability
unverified; see later source' note rather than a replacement assertion.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence


_FIELDS = {
    'name': (r'my\s+(?:(?:legal|registered|official|current)\s+)*name',
             r'我的(?:登记|注册|法定|正式|当前|现用)?(?:姓名|名字)'),
    'nickname': (r'my\s+(?:(?:registered|current)\s+)*nickname', r'我的(?:登记|当前)?昵称'),
    'location': (r'my\s+(?:(?:registered|home|residential|current)\s+)*(?:address|residence)',
                 r'我的(?:登记|注册|当前|家庭)?(?:住址|地址|居所)'),
    'work': (r'my\s+(?:(?:current|registered)\s+)*(?:employment|job|work\s+contract)',
             r'我的(?:当前|现有)?(?:工作|雇佣合同|劳动合同)'),
    'role': (r'my\s+(?:(?:current|official)\s+)*(?:role|position|appointment)',
             r'我的(?:当前|正式)?(?:职位|职务|任命)'),
    'condition': (r'my\s+(?:(?:current|recorded)\s+)*diagnosis', r'我的(?:当前|记录的)?诊断'),
}
_NONFACTUAL = re.compile(
    r'\b(?:if|unless|whenever|would|could|might|may|will|hypothetic\w*|fictional|'
    r'imagine|suppose|example|illustration|quoted?|script|role[- ]?play)\b'
    r'|\b(?:the\s+(?:phrase|sentence)|I\s+(?:said|wrote))\b'
    r'|如果|假如|假设|假想|比如|例如|虚构|角色扮演|这句话|引用|将会|可能', re.I)
_DECLINED = re.compile(
    r'\b(?:denied|declined|rejected|not\s+(?:been\s+)?(?:approved|granted|authorized|authorised))\b'
    r'|否决|拒绝|驳回|未获批|未批准|没有批准', re.I)
_SOURCE_ILLUSTRATION = re.compile(
    r'\b(?:hypothetic\w*|fictional|imagine|suppose|example|illustration|quoted?|script|role[- ]?play)\b'
    r'|假设|假想|示例|比如|例如|虚构|角色扮演|引用|这句话', re.I)
_WITHDRAWN = r'(?:revoked|rescinded|withdrawn|voided|invalidated|cancelled|canceled)'
_COMPLETE = r'(?:(?:has|have|had)\s+(?:now\s+)?been|is|was|were)\s+(?:officially\s+)?'
_CONDITION = re.compile(
    r'^\s*(?:if|once|when)\s+(?P<actor>[^,.!?\n]{2,80}?)\s+'
    r'(?P<verb>approves?|authori[sz]es?)\s+(?P<object>[^,.!?\n]{2,80}),\s*'
    r'(?:then\s+)?(?P<target>.+?)\s*[.!]?\s*$', re.I)
_QUALIFICATION_CUE = re.compile(
    r'\b(?:revoked|rescinded|withdrawn|voided|invalidated|cancelled|canceled|approved|approval|authorized|authorised)\b'
    r'|撤销|撤回|注销|作废|取消|废止', re.I)


def source_sentences(text: str) -> list[str]:
    """Split original text without rewriting or truncating any returned quote."""
    return [part.strip() for part in re.split(
        r'(?<=[。！？!?])\s*|(?<=\.)[ \t]+|[\r\n]+', text) if part.strip()]


def _normal(text: str) -> str:
    return re.sub(r'\s+', ' ', text).strip(' \t.。!?！？').casefold()


def _actor(text: str) -> str:
    return re.sub(r'^the\s+', '', _normal(text))


def _quoted_start(text: str) -> bool:
    return text.lstrip().startswith(('"', "'", '“', '‘', '「', '『'))


def _question(text: str) -> bool:
    return bool(re.search(r'[?？]|(?:吗|呢)[。！!]?\s*$', text))


def _withdrawal(text: str, fields: tuple[str, str], value: str) -> bool:
    if _NONFACTUAL.search(text) or _quoted_start(text) or _question(text):
        return False
    quoted_value = r'(?:["“\']?' + re.escape(value) + r'["”\']?)'
    # A competing value cannot hide in a wildcard between the self field and
    # the completed action: either the old value is verbatim, or it is omitted.
    english = (r'^\s*' + fields[0] + r'(?:\s+(?:of|as))?'
               r'(?:\s*[:=]?\s+' + quoted_value + r')?\s+'
               + _COMPLETE + _WITHDRAWN + r'(?=\b|[.;,!])')
    chinese = (r'^\s*' + fields[1] + r'(?:[：:=]?\s*' + quoted_value + r')?\s*'
               r'(?:已经|现已|已|现在)?(?:被)?(?:撤销|撤回|注销|作废|取消|废止)')
    return bool(re.search(english, text, re.I) or re.search(chinese, text))


def _conditional_pair(first: str, second: str, field: str, value: str) -> bool:
    """Recognize a quoted same-source dependency, never apply its target value."""
    if (_quoted_start(first) or _quoted_start(second) or _DECLINED.search(second)
            or _question(first) or _question(second)):
        return False
    match = _CONDITION.fullmatch(first)
    if not match:
        return False
    target = re.fullmatch(field + r'\s+is\s+([^.!?\n]{1,120})[.!]?\s*',
                          match['target'], re.I)
    if not target or _normal(target[1]) == _normal(value):
        return False
    if _NONFACTUAL.search(match['actor']) or _NONFACTUAL.search(second):
        return False
    actor = re.escape(_actor(match['actor']))
    obj = re.escape(_normal(match['object']))
    # An outcome for a different application/object does not close this link.
    outcome = (r'(?:the\s+)?' + actor + r'\s+(?:(?:has|had)\s+)?'
               r'(?:granted\s+approval|approved\s+' + obj
               + r'|authori[sz]ed\s+' + obj + r')[.!]?')
    return bool(re.fullmatch(outcome, second.strip(), re.I))


def qualification_indexes(subject: str, predicate: str, value: str,
                          sentences: Sequence[str]) -> list[dict]:
    """Return original indexes and reasons; inputs must be ONE original source.

    Only explicit self attributes listed above are supported. Unsupported
    subjects, predicates and unresolved referents remain unqualified.
    """
    fields = _FIELDS.get(str(predicate).casefold())
    if str(subject).casefold() != 'self' or not fields or not isinstance(value, str) or not value.strip():
        return []
    # A role-play/example header may be a separate line from the first-person
    # statement. Prefer a false negative over treating quoted text as identity.
    if any(isinstance(text, str) and _SOURCE_ILLUSTRATION.search(text) for text in sentences):
        return []
    result = []
    for index, text in enumerate(sentences):
        if not isinstance(text, str):
            continue
        if _withdrawal(text, fields, value):
            result.append(dict(reason='explicit-self-withdrawal', sentence_indexes=[index]))
        if index+1 < len(sentences) and isinstance(sentences[index+1], str):
            if _conditional_pair(text, sentences[index+1], fields[0], value):
                result.append(dict(reason='later-same-source-condition-outcome',
                                   sentence_indexes=[index, index+1]))
    return result


def qualifications(assertion: Mapping, later_sources: Iterable[Mapping], limit: int = 4) -> list[dict]:
    """Select exact later source quotes for a noncommittal applicability note.

    Caller guarantees same user/role and strictly later canonical record order.
    This function does not inspect chronology or user identity. Source metadata
    is copied as provided; no inferred or replacement value is returned.
    Supply ``sentences`` to use a caller's original-source segmentation, or
    ``content`` to use this module's conservative splitter.
    """
    if limit <= 0:
        return []
    result = []
    for source in later_sources:
        sentences = source.get('sentences')
        if not isinstance(sentences, (list, tuple)):
            content = str(source.get('content', ''))
            if not _QUALIFICATION_CUE.search(content):
                continue
            sentences = source_sentences(content)
        matches = qualification_indexes(str(assertion.get('subject', '')),
            str(assertion.get('predicate', '')), str(assertion.get('value', '')), sentences)
        for match in matches:
            indexes = match['sentence_indexes']
            item = {key: source[key] for key in (
                'source_rid', 'rid', 'role', 'source_role', 'session_id', 'seq',
                'created_at', 'timestamp_origin') if key in source}
            item.update(reason=match['reason'], sentence_indexes=list(indexes),
                        text='\n'.join(sentences[index] for index in indexes))
            result.append(item)
            if len(result) >= limit:
                return result
    return result
