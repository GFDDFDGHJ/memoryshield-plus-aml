"""Deterministic query intent and explicit privacy exclusions.

This module classifies the requested operation, not source facts or answers.
An exclusion is a separate constraint, never a positive retrieval topic. Its
field categories are deliberately explicit; ordinary factual negation does
not express an exclusion. No database, model, network or credentials are used.
"""
from __future__ import annotations

import re


_FIELDS = {
    'address': r'(?:(?:home|postal|street|residential|private|personal)\s+)?address(?:es)?\b|家庭住址|家庭地址|居住地址|通讯地址|通信地址|私人住址|住址|地址',
    'access_code': r'(?:(?:access|door|entry|cabinet|storage|locker|security)\s+codes?|passcodes?|passwords?|credentials?|PINs?|API\s+keys?)\b|门禁密码|访问密码|访问码|门禁码|验证码|密码|口令|密钥',
    'phone': r'(?:(?:phone|telephone|mobile|cell)\s+(?:numbers?|contacts?))\b|电话号码|手机号码|手机号|私人电话|联系电话',
    'email': r'e[- ]?mail(?:\s+addresses?)?\b|电子邮箱|电子邮件地址|邮件地址|邮箱',
    'medical': r'(?:(?:medical|health|clinical)\s+(?:records?|history|details?|information|data|diagnos(?:is|es))|diagnos(?:is|es)|medications?)\b|医疗记录|病历|健康信息|健康记录|病史|诊断信息|诊断结果|用药记录',
    'financial': r'(?:bank\s+account(?:\s+(?:numbers?|details?))?|credit\s+card\s+(?:numbers?|details?)|financial\s+(?:records?|details?|information)|salary|income)\b|银行账号|银行账户|银行卡号|信用卡号|财务信息|财务记录|工资|薪资|收入',
    'identity': r'(?:passport\s+(?:numbers?|details?)|(?:government|national|tax|social\s+security)\s+(?:ID|identification|numbers?)|ID\s+numbers?)\b|身份证号|身份证号码|护照号码|护照号|证件号码|证件号',
    'private': r'(?:(?:private|personal|sensitive)\s+(?:information|details?|data))\b|私人信息|私密信息|敏感信息|个人隐私',
}
_FIELD = re.compile('|'.join('(?P<'+name+'>'+pattern+')' for name, pattern in _FIELDS.items()), re.I)
_OWNER = re.compile(r'\s*(?:(?:my|our|his|her|their|your|the|any|all|a|an)\s+|我的|我们的|他的|她的|他们的|你的|任何|所有|个人的|私人的)*', re.I)
_EXCLUSION_START = re.compile(
    r'\b(?:omit(?:ting)?|exclud(?:e|ing)|redact(?:ing)?|leave\s+out|leaving\s+out|without|except\s+for)\b'
    r'(?:\s+(?:ever\s+)?(?:sharing|disclosing|revealing|including|mentioning|listing|showing))?'
    r'|\b(?:do\s+not|don\u2019?t|don\x27t|never|avoid)\s+(?:ever\s+)?'
    r'(?:share|sharing|disclos\w*|reveal\w*|includ\w*|mention\w*|list\w*|show\w*|publish\w*)\b'
    r'|(?:省略|略去|隐去|隐藏|删去|去掉|排除|不包含|不包括)'
    r'|(?:不要|别|请勿|不得|不应|不需要|无需|不必)(?:透露|泄露|披露|提供|列出|展示|提及|包含|显示|输出|写出|公开|使用)', re.I,
)
_JOIN = re.compile(r'\s*(?:,\s*(?:(?:and|or|nor)\s+)?|\s+(?:and|or|nor)\s+|以及|还有|[、及和或与])\s*', re.I)
_QUOTED = re.compile(r'"[^"\n]*"|“[^”\n]*”|(?<!\w)\x27[^\x27\n]+\x27(?!\w)')


def _separate_exclusions(query: str):
    quotes = [(match.start(), match.end()) for match in _QUOTED.finditer(query)]
    spans, categories = [], []
    for start in _EXCLUSION_START.finditer(query):
        if any(left <= start.start() < right for left, right in quotes):
            continue
        if any(span['start'] <= start.start() < span['end'] for span in spans):
            continue
        cursor, found = start.end(), []
        while True:
            owner = _OWNER.match(query, cursor)
            field = _FIELD.match(query, owner.end())
            if not field:
                break
            found.append(field.lastgroup)
            cursor = field.end()
            join = _JOIN.match(query, cursor)
            if not join:
                break
            following = _OWNER.match(query, join.end())
            if not _FIELD.match(query, following.end()):
                break
            cursor = join.end()
        if not found:
            continue
        justification = re.match(r'\s+(?:for\s+privacy|to\s+protect\s+(?:my|our)\s+privacy)\b|(?:以保护隐私|为保护隐私)', query[cursor:], re.I)
        if justification:
            cursor += justification.end()
        categories.extend(found)
        spans.append(dict(start=start.start(), end=cursor, text=query[start.start():cursor],
                          categories=list(dict.fromkeys(found))))
    if not spans:
        return query, [], []
    positive, last = [], 0
    for span in spans:
        positive.extend((query[last:span['start']], ' '))
        last = span['end']
    positive.append(query[last:])
    value = ''.join(positive)
    value = re.sub(r'\b(?:but|however|and)\s*(?=[,;.!?]|$)|(?:但是|但|同时|并且)\s*(?=[，；。！？,;.!?]|$)', '', value, flags=re.I)
    value = re.sub(r'\s+([,;.!?，；。！？])', r'\1', value)
    value = re.sub(r'[,;，；]\s*(?=[.!?。！？]|$)', '', value)
    value = re.sub(r'^[\s,;.!?，；。！？]+|[\s,;，；]+$', '', value)
    value = re.sub(r'\s+', ' ', value).strip()
    return value, list(dict.fromkeys(categories)), spans


_INTEGRATION = re.compile(
    r'\b(?:summari[sz]\w*|overview|recap|synthesi[sz]\w*|integrat(?:e|es|ing)|combin(?:e|es|ing)|consolidat(?:e|es|ing)|connect\w*|reflect\w*|compar\w*)\b'
    r'|\b(?:put|pull|bring|draw)\b.{0,70}\b(?:together|picture|portrait|connections?)\b'
    r'|总结|综述|概述|回顾|汇总|串起来|联系起来|综合|整合|跨领域|整体看|总体看', re.I,
)
_INTEGRATED_VIEW = re.compile(
    r'\b(?:give|provide|offer|create|make|draw|build|present|develop)\b'
    r'(?:\s+(?:me|us|a|an|the|one|single)){0,4}\s+'
    r'(?:consolidated|unified|combined|integrated|holistic|overall|comprehensive|big[- ]picture)\s+'
    r'(?:picture|portrait|account|view|understanding|story|interpretation|assessment)\b', re.I,
)
_NOMINAL_INTEGRATION_QUESTION = re.compile(
    r'\b(?:what|which|how|does|do|can|could)\b.{0,100}'
    r'\b(?:combination|integration|consolidation|synthesis|connections?)\b.{0,150}'
    r'\b(?:reveal\w*|suggest\w*|show\w*|indicat\w*|imply|implies|mean\w*|tell\w*)\b', re.I,
)
_THEMATIC_INQUIRY = re.compile(r'\b(?:what|which)\b.{0,35}\bthemes?\b.{0,30}\b(?:emerg\w*|aris\w*|stand\s+out|recur\w*)\b|(?:什么|哪些).{0,12}主题.{0,12}(?:浮现|反复|体现)', re.I)
_MEMORY = re.compile(
    r'\b(?:my|our)\s+(?:(?:past|previous|earlier|shared|personal)\s+)?(?:conversations?|discussions?|exchanges?|history|experiences?|memories)\b'
    r'|\b(?:I|we)(?:\s+have|\x27ve|\u2019ve)?\s+(?:shared|told|described|discussed|mentioned|experienced)\b'
    r'|(?:这些|我们的|我的|以往的|之前的|历次)(?:聊天|对话|讨论|经历|交流)'
    r'|(?:我|我们)(?:一路以来|一直以来|至今|过去).{0,12}(?:选择|取舍|变化|经历)', re.I,
)
_PERSONAL_WHOLE = re.compile(r'\bmy\s+(?:life|journey|priorities|values|choices|decisions|development)\b|我的(?:人生|生活|历程|选择|取舍|变化|价值观)|我这些(?:选择|取舍|变化)', re.I)
_HOLISTIC_OBJECT = re.compile(r'\b(?:themes?|priorities|choices|decisions|reasons?|tradeoffs?|trajectory|changes?|values|constraints?|portrait|picture|things?|points?)\b|主题|取舍|变化|选择|原因|历程|看重|价值观|事情|要点', re.I)
_HISTORY_RANGE = re.compile(r'\b(?:all|entire|whole)\b.{0,45}\b(?:conversations?|discussions?|exchanges?|history|sessions?)\b|\bacross\s+(?:all\s+)?sessions\b|全部.{0,8}(?:聊天|对话|经历|会话)|所有.{0,8}(?:聊天|对话|经历|会话)', re.I)
_FACETS = {
    'work': r'\b(?:work|career|employment|professional|occupational|job|vocation)\b|工作|职业|职场',
    'travel': r'\b(?:travel|traveling|travelling|tourism)\b|旅行|旅游|出行',
    'health': r'\b(?:health|wellbeing|medical|clinical)\b|健康|身心|医疗|临床',
    'learning': r'\b(?:study|learning|education|educational|academic|school)\b|学习|教育|学业',
    'family': r'\b(?:family|caregiving|childcare|eldercare|relationships?)\b|家庭|家人|照护|育儿|人际',
    'interests': r'\b(?:hobbies|interests|volunteering|recreational)\b|爱好|兴趣|志愿',
    'housing': r'\b(?:housing|residential)\b|住房|居住|租房',
    'finances': r'\b(?:finances|financial|economic|monetary)\b|财务|经济|金融',
}
_TOPIC_BOUND = re.compile(r'\b(?:about|regarding|concerning|for)\s+(?!me\b|us\b|myself\b|ourselves\b|all\b|every\b)\S|关于|针对|围绕|有关', re.I)
_TYPED_OBJECT = re.compile(r'\b(?:project|turbine|experiment|contract|permit|device|protocol|puzzle|algorithm)\b|项目|计划|涡轮|实验|合同|许可|设备|协议|谜题|算法', re.I)


def plan(query: str) -> dict:
    """Return an explainable operation plan; preserve concrete topic bounds.

    ``task`` is global_synthesis, topic_synthesis or ordinary. The positive
    query may be empty for a request consisting solely of privacy exclusions.
    Global synthesis requires an integration operation plus either a whole
    conversation range, or a personal/memory object with holistic meaning.
    Explicit topics, typed objects and proper names block that promotion.
    ``requested_facets`` keeps a finite personal-attribute ontology visible;
    it is not permission to disclose unrequested fields.
    """
    query = str(query or '')
    positive, excluded, spans = _separate_exclusions(query)
    operation_text = _QUOTED.sub(' ', positive)
    integrating = bool(_INTEGRATION.search(operation_text) or _THEMATIC_INQUIRY.search(operation_text)
                       or _INTEGRATED_VIEW.search(operation_text) or _NOMINAL_INTEGRATION_QUESTION.search(operation_text))
    facets = [name for name, pattern in _FACETS.items() if re.search(pattern, positive, re.I)]
    without_lead = re.sub(r'^\s*[A-Z][A-Za-z]*\b', ' ', positive)
    named = bool(re.search(r'\b[A-Z][A-Za-z0-9_-]{2,}\b', without_lead))
    # 'about my priorities' names the requested whole-person attribute, not a
    # new external topic. A following proper name/typed object still bounds it.
    scope_text = re.sub(r'\b(?:about|regarding|concerning)\s+(?:my|our)\s+(?:life|priorities|values|choices|decisions|development|experiences?|history)\b', ' ', positive, flags=re.I)
    constrained = bool(_TOPIC_BOUND.search(scope_text) or _TYPED_OBJECT.search(positive)
                       or _QUOTED.search(positive) or re.search(r'\d', positive) or named)
    personal = bool(_MEMORY.search(positive) or _PERSONAL_WHOLE.search(positive)
                    or len(facets) >= 3 and re.search(r'\b(?:my|our)\b|我的|我们的', positive, re.I))
    whole = bool(_HISTORY_RANGE.search(positive))
    holistic = bool(_HOLISTIC_OBJECT.search(positive))
    # A single named life domain remains a topical request. A genuinely whole
    # history request or integration across several personal attributes has
    # independent scope evidence, rather than a topic-word miss fallback.
    domain_limited = bool(facets) and len(facets) < 3 and not whole
    # Omitting all 'personal details' does not itself specify a safe positive
    # scope. A vague reference to things once shared must not open the whole
    # history merely because its exclusion clause was removed. Whole-history,
    # explicit whole-person and multi-facet requests carry independent scope.
    broad_private_limited = ('private' in excluded and not (whole or _PERSONAL_WHOLE.search(positive)
                                                          or personal and len(facets) >= 3
                                                          or constrained or domain_limited))
    global_request = integrating and not constrained and (whole or personal and holistic and not domain_limited
                                                         or personal and len(facets) >= 3) and not broad_private_limited
    if global_request:
        task, reason = 'global_synthesis', 'integration-with-whole-personal-or-memory-object'
    elif integrating:
        task = 'topic_synthesis'
        reason = ('broad-private-exclusion-without-positive-scope' if broad_private_limited else
                  'explicit-topic-bound' if constrained or domain_limited else 'integration-without-global-scope-evidence')
    else:
        task, reason = 'ordinary', 'no-integration-operation'
    return dict(task=task, positive_query=positive, excluded_categories=excluded,
                requested_facets=facets, reason=reason, exclusion_spans=spans,
                original_query=query)


_ASSIGN = r'\s*(?:(?:is|are|was|were|equals?|number\s+is)\b|[:=]|(?:是|为|如下|号码为|号码是))\s*(?P<value>[^\n;。；]+)'
_NON_VALUE = re.compile(r'^(?:not|unknown|unavailable|redacted|missing|unchanged|withheld|null|none|required|mandatory|optional|prohibited|forbidden|protected|confidential)\b|^(?:未提供|已隐藏|已删除|未更新|没有更新|不需要|未知|不可用|保密|未披露|需要更新|应当更新|必须更新|必填)', re.I)
_SENSITIVE_ANCHORS = {
    'address': r'(?:(?:home|postal|street|residential|private|personal)\s+)?address(?:es)?|家庭住址|家庭地址|居住地址|通讯地址|通信地址|住址|地址',
    'access_code': r'(?:(?:access|door|entry|cabinet|storage|locker|security)\s+codes?|passcodes?|passwords?|credentials?|PINs?|API\s+keys?)|门禁密码|访问密码|访问码|门禁码|验证码|密码|口令|密钥',
    'phone': r'(?:phone|telephone|mobile|cell)(?:\s+number)?|电话号码|手机号码|手机号|联系电话',
    'email': r'e[- ]?mail(?:\s+address)?|电子邮箱|电子邮件地址|邮件地址|邮箱',
    'medical': r'(?:medical|health|clinical)\s+(?:record|history|details?|information|data)|diagnosis|medication|病历|病史|诊断|诊断结果|用药|健康记录|医疗记录',
    'financial': r'bank\s+account(?:\s+number)?|credit\s+card(?:\s+number)?|salary|income|银行账号|银行账户|银行卡号|信用卡号|工资|薪资|收入',
    'identity': r'passport\s+number|(?:government|national|tax|social\s+security)\s+(?:ID|identification|number)|ID\s+number|身份证号|身份证号码|护照号码|护照号|证件号码|证件号',
}


def excluded_text(text: str, categories) -> bool:
    """Match explicitly excluded sensitive fields, not arbitrary topics.

    A category name in a policy sentence is insufficient: labels need an
    assigned value, or the value must have a strong identifier format. This
    keeps protective rules such as 'Never disclose private addresses' intact.
    The caller can apply this predicate to individual source sentences and
    retain the rest of the source with its provenance.
    """
    requested = {categories} if isinstance(categories, str) else set(categories or ())
    if 'private' in requested:
        requested.update(_SENSITIVE_ANCHORS)
    text = str(text or '')
    for category in requested:
        anchor = _SENSITIVE_ANCHORS.get(category)
        if anchor:
            for match in re.finditer(r'(?:'+anchor+r')'+_ASSIGN, text, re.I):
                value = match.group('value').strip().strip(' .!?:')
                if category == 'address' and re.search(r'\b(?:IP|MAC|network|server|memory|email|web)\s+$', text[:match.start()], re.I):
                    continue
                if value and not _NON_VALUE.search(value):
                    return True
    if 'email' in requested and re.search(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b', text):
        return True
    if 'address' in requested and re.search(r'\b\d{1,6}\s+(?:[A-Za-z][A-Za-z\x27-]*\s+){1,5}(?:Street|St|Road|Rd|Lane|Ln|Avenue|Ave|Drive|Dr|Boulevard|Blvd)\b|(?:路|街|巷)\d{1,5}号', text, re.I):
        return True
    if 'phone' in requested and re.search(r'(?<!\w)(?:\+\d{1,3}[ -](?:\d[ ()-]?){7,13}\d|\(\d{3}\)\s*\d{3}[- ]\d{4})(?!\w)', text):
        return True
    return False
