"""Bounded, source-local selection of explicit qualification sentences.

Only original sentence indexes are returned. This module does not decide which
state is true, infer a value, assign authority, access storage or call a model.
The caller must supply sentences from ONE original source and preserve their
wording, role, source RID and order. Returned indexes exclude the input anchors.

Ambiguous links are left unselected: free-form coreference is not resolved.
At most 24 additional sentences are selected, no farther than 512 sentences
from an anchor; a directly attached qualification chain is at most four long.
"""
from __future__ import annotations

from bisect import bisect_left
import re

MAX_DEPENDENCIES = 24
MAX_DISTANCE = 512
MAX_CHAIN = 4

# Keep this tokenizer source-only. In particular, a terminal number in an
# ordinary sentence is not a list marker, and a decimal is not punctuation.
_SOURCE_SENTENCES = re.compile(r".+?(?:[。！？!?]|\.(?!\d)(?=\s|$)|\n+|$)", re.S)
_ORDINAL_FRAGMENT = re.compile(r"\s*(\d{1,3})\.\s*")


def source_sentence_spans(text: str) -> list[tuple[int, int]]:
    """Return literal sentence spans, attaching physical-line list numbers.

    A bare ``1.`` at the start of a line belongs to the following text on
    that SAME line. This only repairs a tokenizer boundary; it does not
    classify the numbered text as a rule. Original offsets remain exact.
    """
    raw = [(match.start(), match.end()) for match in _SOURCE_SENTENCES.finditer(text)
           if match.group(0).strip()]
    result = []
    at = 0
    while at < len(raw):
        start, end = raw[at]
        marker = _ORDINAL_FRAGMENT.fullmatch(text[start:end])
        if marker and at + 1 < len(raw):
            number_start = start + marker.start(1)
            line_start = text.rfind('\n', 0, number_start) + 1
            following_start, following_end = raw[at + 1]
            same_line = re.match(r'[\t ]+\S', text[end:following_end])
            if (not text[line_start:number_start].strip()
                    and following_start == end and same_line):
                result.append((start, following_end))
                at += 2
                continue
        result.append((start, end))
        at += 1
    return result

_EXECUTION_SUBJECT = re.compile(
    r"\b(?:rules?|steps?|instructions?|operations?|algorithm|execution|procedure|protocol)\b"
    r"|规则|步骤|指令|操作|算法|执行|流程", re.I)
_EXECUTION_CONTROL = re.compile(
    r"\b(?:order|sequence|once|times?|passes?|iterations?|repeat\w*|revisit\w*|"
    r"skip\w*|stop\w*|return|before|after)\b|顺序|次序|一次|遍历|重复|跳过|终止|回到", re.I)
_ENACTED_CONTROL = re.compile(
    r"(?:^|[:;,])\s*(?:please\s+)?(?:process|execute|evaluate|perform|apply|follow|run|repeat|revisit|skip|stop|return)\b"
    r"|\b(?:must|shall|should|never|always|do\s+not|don't)\s+(?:\w+\s+){0,2}"
    r"(?:process|execute|evaluate|perform|apply|follow|run|repeat|revisit|skip|stop|return)\b"
    r"|\b(?:is|are|be|been|being)\s+(?:(?:never|not|always|only|each)\s+){0,2}"
    r"(?:processed|executed|evaluated|performed|applied|followed|run|repeated|revisited|skipped|stopped)\b"
    r"|\b(?:rules?|steps?|instructions?|operations?)\s+(?:(?:each|all|always|only)\s+){0,2}"
    r"(?:run|execute|apply|repeat)\b"
    r"|(?:按照|按顺序|依次|顺序执行|执行顺序|只执行一次|仅执行一次|永久跳过|不再返回|不得重访|禁止重复)", re.I)


def execution_control_indexes(sentences: list[str]) -> set[int]:
    """Select stated execution policies, rather than mere topic mentions.

    A notebook can mention 'skip rules' without instructing anyone to skip a
    rule. Operational wording and an execution subject/control must all be
    present. This quotes controls only; it neither executes nor validates them.
    """
    return {index for index, text in enumerate(sentences)
            if _EXECUTION_SUBJECT.search(text) and _EXECUTION_CONTROL.search(text)
            and _ENACTED_CONTROL.search(text)}


_NUMBERED_LINE = re.compile(r'^\s*(?P<number>\d{1,3})[.)]\s+(?P<body>\S.*)$')
_NAMED_RULE_LINE = re.compile(
    r'^\s*Rule\s+(?P<label>[A-Za-z][A-Za-z0-9_-]{0,31})\s*:\s+(?P<body>\S.*)$', re.I)
_BULLET_RULE_LINE = re.compile(r'^\s*(?P<marker>[-*•])\s+(?P<body>\S.*)$')
_INITIAL_DECLARATION = re.compile(
    r'^\s*(?:initial|starting)\s+(?:state|values?|conditions?|configuration)\s*:\s+\S', re.I)
_RULE_ACTION = (
    r'(?:add|subtract|multiply|divide|set|replace|round|truncate|return|stop|skip|'
    r'repeat|apply|execute|process|use|format|include|omit|keep|write|output|'
    r'close|open|increment|decrement|remove|insert|append|retain|discard|convert)'
)
_ITEM_ACTION = re.compile(r'^' + _RULE_ACTION + r'\b\s+\S', re.I)
_ITEM_ARITHMETIC = re.compile(
    r'^(?:add\b.+\bto|subtract\b.+\bfrom|(?:multiply|divide)\b.+\bby|'
    r'set\b.+\bto|replace\b.+\bwith|round\b.+\b(?:up|down|to))\s+\S', re.I)
_ITEM_OBJECT = re.compile(
    r'^' + _RULE_ACTION + r'\s+(?:the|a|an|this|that|these|those|every|each|all|any)\s+\S', re.I)


def _operational_item(body: str) -> bool:
    """Require instruction grammar, not merely a verb-shaped company name."""
    body = re.sub(r'^(?:then|next|finally)\s*,?\s*', '', body, flags=re.I)
    conditional = re.match(r'^(?:if|when|unless)\b.{1,400}?[,;:]\s*(?:then\s+)?(.+)', body, re.I)
    if conditional:
        return bool(_ITEM_ACTION.match(conditional[1]))
    explicit = re.match(r'^(?:always|never|do\s+not)\s+(.+)', body, re.I)
    if explicit:
        return bool(_ITEM_ACTION.match(explicit[1]))
    return bool(_ITEM_ARITHMETIC.match(body) or _ITEM_OBJECT.match(body))


def _indent_width(text: str) -> int:
    return len(text[:len(text)-len(text.lstrip(' \t'))].expandtabs())


def _numbered_rule_blocks(text: str) -> list[tuple[int, int]]:
    """Find bounded headed literal procedures; never resolve a source.

    A list can use consecutive ordinals, unique explicit ``Rule LABEL:``
    labels, or one uniform bullet marker. Up to two labelled initial
    declarations may occur between its heading and first operation. An
    earlier unscoped declaration is not attached to a later heading, and no
    variable or rule is interpreted.
    """
    spans = source_sentence_spans(text)
    lines = []
    offset = 0
    for line in text.splitlines(keepends=True):
        lines.append((offset, offset + len(line), line.rstrip('\r\n')))
        offset += len(line)
    blocks = []
    at = 0
    while at < len(lines):
        match = _NUMBERED_LINE.fullmatch(lines[at][2])
        named = _NAMED_RULE_LINE.fullmatch(lines[at][2])
        bullet = _BULLET_RULE_LINE.fullmatch(lines[at][2])
        if not match and not named and not bullet:
            at += 1
            continue
        first = at
        style = 'numbered' if match else 'named' if named else bullet['marker']
        item_indent = _indent_width(lines[first][2])
        number = int(match['number']) if match else None
        labels = set()
        bodies = []
        invalid = False
        while at < len(lines):
            current = _NUMBERED_LINE.fullmatch(lines[at][2])
            named = _NAMED_RULE_LINE.fullmatch(lines[at][2])
            bullet = _BULLET_RULE_LINE.fullmatch(lines[at][2])
            if not current and not named and not bullet:
                break
            current_style = 'numbered' if current else 'named' if named else bullet['marker']
            if (current_style != style
                    or style in {'-', '*', '•'} and _indent_width(lines[at][2]) != item_indent):
                invalid = True
            if current:
                if number is None or int(current['number']) != number:
                    invalid = True
                number = int(current['number']) + 1
                body = current['body']
            elif named:
                label = named['label'].casefold()
                if label in labels:
                    invalid = True
                labels.add(label)
                body = named['body']
            else:
                body = bullet['body']
            if not _operational_item(body):
                invalid = True
            bodies.append(body)
            at += 1
            # Indented continuation lines remain literal members of that
            # item; a blank line is an explicit scope barrier.
            # A deeper bullet already served as a continuation under the
            # older numbered/named grammar; do not turn it into a new peer.
            while (at < len(lines) and lines[at][2].strip()
                   and lines[at][2][:1] in {' ', '\t'}
                   and not _NUMBERED_LINE.fullmatch(lines[at][2])
                   and not _NAMED_RULE_LINE.fullmatch(lines[at][2])
                   and (not _BULLET_RULE_LINE.fullmatch(lines[at][2])
                        or style in {'numbered', 'named'}
                        and _indent_width(lines[at][2]) > item_indent)):
                at += 1
        if invalid or not 2 <= len(bodies) <= MAX_DEPENDENCIES or first == 0:
            continue
        heading_index = first - 1
        declarations = 0
        while heading_index >= 0 and _INITIAL_DECLARATION.match(lines[heading_index][2]):
            declarations += 1
            heading_index -= 1
        if declarations > 2 or heading_index < 0:
            continue
        heading = lines[heading_index][2].strip()
        if (not heading or not _EXECUTION_SUBJECT.search(heading)
                or not (heading.endswith((':', '：')) or execution_control_indexes([heading]))):
            continue
        end = at
        # An immediately following stated execution policy qualifies all
        # members, even if it contains none of the queried state variables.
        while end < len(lines) and lines[end][2].strip():
            if not execution_control_indexes([lines[end][2]]):
                break
            end += 1
        block_start = lines[heading_index][0]
        block_end = lines[end - 1][1]
        block = {index for index, (start, finish) in enumerate(spans)
                 if start < block_end and finish > block_start}
        # Do not silently turn a large procedure into a partial sequence.
        if len(block) <= MAX_DEPENDENCIES:
            blocks.append((block_start, block_end))
    return blocks


def numbered_rule_spans(text: str, anchors: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Return whole literal blocks intersecting original-source anchors.

    Character intervals avoid ambiguity if the caller separately protects
    table rows or removes private sentences. The caller must quote only
    allowed original text and must not present a partially omitted block as
    a complete procedure. No content is rewritten or marked authoritative.
    """
    valid = [(start, end) for start, end in anchors
             if type(start) is int and type(end) is int and 0 <= start < end <= len(text)]
    if not valid:
        return []
    return [(start, end) for start, end in _numbered_rule_blocks(text)
            if any(anchor_start < end and anchor_end > start for anchor_start, anchor_end in valid)]


def numbered_rule_indexes(text: str, anchors: set[int]) -> set[int]:
    """Quote a bounded, explicit source-local literal procedure as a block.

    A matching sentence may need earlier steps whose variables differ. The
    procedure must have an adjacent explicit rule/procedure heading and at
    least two consecutive numbered, uniquely named or uniformly bulleted
    operational items. Only labelled initial declarations inside that heading
    belong to the block.
    Blank lines, narration, mixed or duplicate labels, numbering gaps and
    more than 24 items end or reject it. No source is followed, and no rule is
    executed or made active.

    Indexes refer to ``source_sentence_spans(text)``. Callers must keep source
    RID, role and order, and apply their existing exclusion and size checks.
    """
    spans = source_sentence_spans(text)
    valid = {index for index in anchors if type(index) is int and 0 <= index < len(spans)}
    if not valid:
        return set()
    blocks = numbered_rule_spans(text, [spans[index] for index in valid])
    return {index for index, (start, end) in enumerate(spans) if index not in valid
            and any(start < block_end and end > block_start for block_start, block_end in blocks)}

_WORDS = re.compile(r"[A-Za-z][A-Za-z0-9_-]*|[\u4e00-\u9fff]{2,}")
_GOVERNANCE = re.compile(
    r"\b(?:approv\w*|authori[sz]\w*|ratif\w*|consent\w*|grant\w*|den(?:y|ied|ial)|declin\w*|"
    r"reject\w*|revok\w*|rescind\w*|rescission|withdraw\w*|withdrew|supersed\w*|replac\w*|void|waiv\w*|"
    r"exempt\w*|dispensation|laps\w*)\b|\bno\s+(?:replacement|new\s+authorization)\b"
    r"|批准|核准|同意|获批|驳回|否决|拒绝|撤销|撤回|作废|废止|取代|替代|替换|豁免|免除|失效", re.I)
_OUTCOME = re.compile(
    r"\b(?:(?:has|have|had|was|were|is|are)\s+(?:not\s+)?(?:been\s+)?(?:granted|approved|authorized|authorised|ratified|denied|declined|rejected|withdrawn|revoked|rescinded))\b"
    r"|\b(?:has|have|had)\s+(?:not\s+)?(?:granted|approved|authorized|authorised|ratified|denied|declined|rejected)\b"
    r"|\b(?:approval|consent)\s+(?:has\s+)?(?:not\s+)?(?:been\s+)?(?:granted|denied|withdrawn|revoked)\b"
    r"|已经批准|已经核准|已批准|已核准|未批准|没有批准|批准已被撤销|已经驳回|已驳回|已拒绝|未获批准|尚未获批", re.I)
_CONDITION_ACTOR = re.compile(
    r"\b(?:if|when|once|after|unless)\s+(.{1,90}?)\s+(?:approv\w*|authori[sz]\w*|ratif\w*|"
    r"consents?|grants?|denies|rejects?)\b", re.I)
_POINTER = re.compile(
    r"\b(?:this|that|the\s+(?:earlier|previous|prior|former))\s+(?:\w+\s+){0,2}"
    r"(?:approval|authorization|authorisation|permission|grant|entry|selection|specification|rule|policy|arrangement|waiver|exemption|dispensation)\b"
    r"|\b(?:cited|mentioned|described|specified)\s+above\b|\b(?:in\s+its\s+place|nothing\s+else)\b"
    r"|上述|前述|此前的|先前的|原有的|该(?:批准|授权|许可|规则|条款|豁免|约定)|此(?:批准|授权|豁免)", re.I)
_ILLUSTRATION = re.compile(r"\b(?:hypothetic\w*|fictional|for\s+example|for\s+instance|imagine|suppose|role[- ]?play|simulation)\b|假设|假想|举例|例如|虚构|角色扮演", re.I)
_FUTURE_OUTCOME = re.compile(r"\b(?:may|might|could|would|will)\s+(?:(?:be|have(?:\s+been)?)\s+)?(?:approv\w*|grant\w*|authori[sz]\w*|replac\w*|revok\w*)\b|将会批准|可能批准|假如获批", re.I)
_PRICING = re.compile(r"\b(?:billing|pricing|tariff|surcharge|charges?|fees?|per[- ]kg|base\s+cost|dispatch\s+amendment)\b|计费|价格|收费|附加费|基本费用|运费|豁免", re.I)
_WAIVER = re.compile(r"\b(?:waiv\w*|exempt\w*|dispensation|handling\s+surcharge)\b|豁免|免除|附加费", re.I)
_CHAIN = re.compile(
    r"^\s*(?:this|that|its|it|no\s+replacement|nothing\s+else|except|unless|only|provided|however|but|otherwise)\b"
    r"|\b(?:certificate|certification|base\s+and\s+per|base\s+cost|per[- ]kg\s+charges?)\b"
    r"|^\s*(?:该|此|其|但|不过|否则|仅|只有|除非|没有替代|尚无替代)|证书|凭证", re.I)
_SCOPE_HEADER = re.compile(r"^\s*(?:for|regarding|concerning|product|project|protocol|organization|organisation|department|account|team)\b|^\s*(?:针对|关于|产品|项目|协议|组织|部门|账户)", re.I)
_ANCHOR_SUBJECT = re.compile(r"\b(?:approval|authority|authorization|authorisation|permission|licen[cs]e|grant|specification|material|selection|setting|policy|rule|entry|declaration|amendment|contract|pricing|billing|tariff|waiver|exemption)\b|批准|授权|许可|规格|材料|选型|设置|规则|条款|声明|修订|合同|计费|价格|豁免", re.I)
_GENERIC_CAPITALS = frozenset("the a an this that these those if when after once unless my our your his her their it its no nothing only for from in at on by with without following during as product project protocol organization organisation department account team approval authorization permission grant current earlier previous prior former board committee panel registry council review safety quality clinical engineering catalogue inventory base all subject later exception example".split())
_OBJECT_STOP = _GENERIC_CAPITALS | frozenset("is are was were has have had been be not granted approved authorized authorised ratified denied declined rejected approval consent change application request outcome decision effect immediate immediately now then and or but to of does do did the a an name s".split())
_PERSONAL_FIELDS = {
    "name": re.compile(r"\b(?:my|our|user['’]?s?)\s+(?:legal\s+)?(?:name|nickname)\b|我的(?:姓名|名字)|用户(?:姓名|名字)", re.I),
    "address": re.compile(r"\b(?:my|our|home|postal|residential)\s+(?:address|location)\b|住址|家庭地址|我的地址", re.I),
    "health": re.compile(r"\b(?:diagnosis|medication|medical\s+(?:record|history|code)|health\s+(?:record|condition))\b|诊断|病历|病史|医疗|用药", re.I),
    "financial": re.compile(r"\b(?:salary|wages?|income|bank\s+account|credit\s+card|loan|mortgage)\b|工资|收入|银行账号|贷款", re.I),
    "contact": re.compile(r"\b(?:my|our|personal)\s+(?:email|phone|telephone)\b|邮箱|手机号码|电话号码", re.I),
    "credential": re.compile(r"\b(?:passcode|password|access\s+code|API\s+key|passport\s+number)\b|密码|口令|门禁码|护照号", re.I),
    "travel": re.compile(r"\b(?:travel\s+permit|travel\s+reimbursement|visa)\b|旅行许可|签证|差旅报销", re.I),
}


def _words(text):
    return {word.casefold() for word in _WORDS.findall(text)}


def _names(text):
    return {word.casefold() for word in re.findall(r"\b[A-Z][A-Za-z0-9_-]{1,}\b", text)
            if word.casefold() not in _GENERIC_CAPITALS}


def _scope_names(text):
    cue = _GOVERNANCE.search(text)
    prefix = text[:cue.start()] if cue else text
    names = _names(prefix)
    # A named scope can also follow the decision, e.g. 'revoked for Briar Lab'.
    for match in re.finditer(r"\b(?:for|at|within|regarding)\s+((?:[A-Z][\w-]*\s*){1,5})", text):
        names.update(_names(match[1]))
    return names


def _fields(text):
    return {name for name, pattern in _PERSONAL_FIELDS.items() if pattern.search(text)}


def _objects(text):
    text = re.split(r"\b(?:because|due\s+to|after|before|on|since|following)\b", text, maxsplit=1, flags=re.I)[0]
    return _words(text) - _OBJECT_STOP


def _simple_actor_outcome(text, actor_text):
    """An explicit finite past decision, only for the literal condition actor.

    This is evidence selection, including denial; it never executes the
    condition or promotes its target. Questions, quotations, conditions and
    modal or negated forms remain outside this additional recognizer.
    """
    if (re.search(r'[?？]|\b(?:if|unless|when|once|never|not|may|might|could|would|will)\b', text, re.I)
            or text.lstrip().startswith(('"', "'", '“', '‘', '「', '『'))):
        return False
    actor = re.sub(r'^the\s+', '', actor_text.strip(), flags=re.I)
    literal = r'\s+'.join(re.escape(word) for word in actor.split())
    return bool(re.match(r'^\s*(?:the\s+)?' + literal
                         + r'\s+(?:approved|authori[sz]ed|denied|declined|rejected)\s+', text, re.I))


def dependency_indexes(sentences: list[str], anchors: set[int]) -> set[int]:
    """Return bounded extra indexes supported by explicit source-local links.

    Invalid anchor indexes are ignored; empty/all-anchored sources add nothing.
    The source text is never modified. A retained hypothetical stays a quote,
    but a new illustrative/future decision is not attached to a factual anchor.
    """
    valid = sorted(index for index in anchors if type(index) is int and 0 <= index < len(sentences)
                   and isinstance(sentences[index], str) and sentences[index].strip())
    if not valid or len(valid) == len(sentences):
        return set()
    anchored = set(valid)
    features = []
    for text in sentences:
        text = text if isinstance(text, str) else ""
        features.append({"text": text, "words": _words(text), "names": _scope_names(text),
                         "fields": _fields(text), "illustration": bool(_ILLUSTRATION.search(text)),
                         "governance": bool(_GOVERNANCE.search(text))})

    def nearest(index):
        at = bisect_left(valid, index)
        options = valid[max(0, at-1):at+1]
        return min(options, key=lambda other: (abs(index-other), other))

    def compatible(index, anchor, actor=()):
        current, basis = features[index], features[anchor]
        if abs(index-anchor) > MAX_DISTANCE:
            return False
        if current["illustration"] and not basis["illustration"] or _FUTURE_OUTCOME.search(current["text"]):
            return False
        if current["fields"] and (not basis["fields"] or not current["fields"] <= basis["fields"]):
            return False
        if current["names"] - basis["names"] - set(actor):
            return False
        return True

    def pointer_scope_clear(index, anchor):
        basis = features[anchor]
        for middle in range(min(index, anchor)+1, max(index, anchor)):
            feature = features[middle]
            if (_SCOPE_HEADER.search(feature["text"]) and feature["names"] - basis["names"]
                    or feature["fields"] and (not basis["fields"] or not feature["fields"] <= basis["fields"])):
                return False
        return True

    conditions = []
    for anchor in valid:
        for match in _CONDITION_ACTOR.finditer(sentences[anchor]):
            actor = _words(match[1]) - {"the", "a", "an", "has", "have", "had"}
            if actor:
                conditions.append((anchor, actor, _objects(sentences[anchor][match.end():]), match[1]))

    selected = set()
    linked_to = {}
    for index, feature in enumerate(features):
        if index in anchored or not feature["governance"]:
            continue
        anchor = nearest(index)
        linked = False
        if conditions:
            for conditional_anchor, actor, target, actor_text in conditions:
                explicit_past = _simple_actor_outcome(feature["text"], actor_text)
                if not (_OUTCOME.search(feature["text"]) or explicit_past):
                    continue
                if explicit_past and not pointer_scope_clear(index, conditional_anchor):
                    continue
                cue = _GOVERNANCE.search(feature["text"])
                objects = _objects(feature["text"][cue.end():]) if cue else set()
                if explicit_past and objects - target:
                    continue  # A shared generic 'change' cannot license a different object.
                named_objects = _names(feature["text"]) - _names(sentences[conditional_anchor]) - actor
                same_actor = actor <= feature["words"]
                adjacent_bare_outcome = (index == conditional_anchor+1 and bool(re.match(
                    r"\s*(?:the\s+)?(?:approval|consent|authorization|authorisation)\b", feature["text"], re.I)))
                if ((same_actor or adjacent_bare_outcome) and not named_objects
                        and (not objects or bool(objects & target))
                        and compatible(index, conditional_anchor, actor)):
                    anchor, linked = conditional_anchor, True
                    break
        if not linked and compatible(index, anchor):
            basis = features[anchor]
            shared_object = (feature["words"] & basis["words"]) - feature["names"] - basis["names"] - _OBJECT_STOP
            explicit_same_scope = (bool(feature["names"] and feature["names"] & basis["names"])
                                   and bool(shared_object) and bool(_ANCHOR_SUBJECT.search(basis["text"])))
            explicit_pointer = (bool(_POINTER.search(feature["text"])) and bool(_ANCHOR_SUBJECT.search(basis["text"]))
                                and pointer_scope_clear(index, anchor))
            priced_exemption = (bool(_PRICING.search(basis["text"])) and bool(_WAIVER.search(feature["text"]))
                                and pointer_scope_clear(index, anchor))
            linked = explicit_same_scope or explicit_pointer or priced_exemption
        if linked:
            selected.add(index)
            linked_to[index] = anchor
            if len(selected) >= MAX_DEPENDENCIES:
                break
    # Qualification text is quoted as one bounded group, not reduced to a bare
    # 'waived'/'approved' token. No closure crosses an explicit new scope.
    if len(selected) >= MAX_DEPENDENCIES:
        return selected
    for index in sorted(tuple(selected)):
        anchor = linked_to[index]
        for following in range(index+1, min(len(sentences), index+1+MAX_CHAIN)):
            if following in anchored or following in selected:
                continue
            if (not _CHAIN.search(features[following]["text"]) or not compatible(following, anchor)
                    or not pointer_scope_clear(following, index)):
                break
            selected.add(following)
            if len(selected) >= MAX_DEPENDENCIES:
                return selected
    return selected
