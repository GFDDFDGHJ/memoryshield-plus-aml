"""Explicit delimited-table closure over original source-unit indexes.

expand(units, kept) -> (extra_indexes, schema_indexes). No input is modified;
no row is merged, value filled, unit converted, statistic calculated or version
chosen. The caller must preserve each selected unit's original source metadata.

Only explicitly declared, stable tables with >=3 rows qualify. Columns retain
their original numeric or compact categorical type; a probability sample need
not contain two separate numeric measurements to be a table.
Schema declarations can precede or follow a block. A declared NA may occupy a
numeric column; arbitrary prose and undeclared missing values stop the block.
A pipe header's immediate original next-line Markdown alignment is retained
as schema only when every cell and the column count match that header.
Cross-session inheritance additionally requires the same explicit dataset ID,
an explicit reference to an earlier schema, and one unambiguous prior schema.
Source segment/record-sequence gaps, role/session changes and personal fields
are boundaries, including gaps left by privacy filtering.
"""
from __future__ import annotations

import csv
import re
import source_dependencies as dependency_layer

MIN_ROWS = 3
MAX_COLUMNS = 16
_DECLARATION = re.compile(r"\b(?:schema|columns?|fields?|column\s+layout)\b|列名|列义|字段|表头", re.I)
_REFERENCE = re.compile(r"\b(?:earlier|previous|prior|shared|same|preceding)\b.{0,70}\b(?:schema|columns?|layout|validity\s+convention)\b|(?:先前|以前|前述|共享|相同|上述).{0,20}(?:模式|列义|列名|字段|表头)", re.I)
_DATASET = re.compile(r"\bdata[ _-]?set\s*(?P<marker>id|identifier|code)?\s*[:=#]?\s*[\"'`]?([A-Za-z0-9][A-Za-z0-9_.:/-]{2,63})|(?:数据集|資料集)(?:编号|标识)?\s*[:：=]?\s*([A-Za-z0-9][A-Za-z0-9_.:/-]{2,63})", re.I)
_NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?(?:\s*[A-Za-zµμ°%][A-Za-z0-9µμ°%/^²³-]{0,12})?")
_NOMINAL = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,31}")
_MARKDOWN_ALIGNMENT_CELL = re.compile(r":?-{3,}:?")
_MISSING = re.compile(r"\bmissing\b[^.!?\n]{0,100}\b(NA|N/A|NULL|NaN)\b|\b(NA|N/A|NULL|NaN)\b[^.!?\n]{0,80}\bmissing\b|缺失[^。！？\n]{0,40}\b(NA|N/A|NULL|NaN)\b", re.I)
_UNIT = re.compile(r"\b(?:units?|pascals?|kilopascals?|seconds?|milliseconds?|kilograms?|grams?|metres?|meters?|litres?|liters?|volts?|amperes?|degrees?|kPa|MPa|Pa|ms|kg|mL)\b|单位|帕|毫秒|千克|毫升", re.I)
_CONTROL = re.compile(r"^\s*(?:only|unless|provided|if|when|except|otherwise|do\s+not|never)\b|^\s*(?:仅|只有|除非|如果|当|否则|不要|不得|禁止)", re.I)
_ELSE = re.compile(r"^\s*otherwise\b|^\s*否则", re.I)
_TABLE_WORD = re.compile(r"\b(?:data|observations?|measurements?|samples?|records?|rows?|readings?|trials?|runs?|cells?|values?)\b|数据|观测|测量|样本|记录|读数|单元格|行", re.I)
_SENSITIVE_HEADER = re.compile(r"\b(?:address|phone|telephone|email|passcode|password|salary|diagnosis|bank\s+account|credit\s+card|passport|identity|identification|(?:government|national|tax)\s+id|social\s+security)\b|住址|地址|手机号|邮箱|密码|工资|诊断|银行账号|身份证|护照|证件", re.I)
_PRIVATE_COLUMNS = {
    'name': r'\b(?:names?|nicknames?|surnames?)\b|姓名|名字|昵称',
    'identity': r'\b(?:passport|identity|identification|(?:government|national|tax)\s+id|social\s+security|id\s+(?:number|code))\b|身份证|护照|证件',
    'address': r'\baddress(?:es)?\b|住址|地址',
    'phone': r'\b(?:phone|telephone|mobile|cell)\b|手机号|电话',
    'email': r'\be\s?mail\b|邮箱|电子邮件',
    'access_code': r'\b(?:passcode|password|credential|pin|(?:access|door|entry|cabinet|storage|locker|security)\s+code|api\s+key)\b|密码|口令|密钥|门禁码|访问码',
    'medical': r'\b(?:medical|health|clinical|diagnosis|medication)\b|病历|病史|诊断|健康|用药',
    'financial': r'\b(?:salary|income|financial|bank\s+account|credit\s+card)\b|工资|薪资|收入|财务|银行账号|银行卡|信用卡',
}
_PERSONAL = re.compile(r"^\s*(?:my|his|her|your|our)\b|^\s*(?:their)\s+(?!(?:units?|columns?|fields?|dimensions?)\b)|^\s*I\s+(?:live|work|am)\b|(?:我的|他的|她的|私人)(?:地址|住址|电话|邮箱|密码|病历)|\b(?:bank\s+account|medical\s+record|private\s+(?:address|code|phone))\b", re.I)


def _segments(text):
    text = str(text)
    return [re.sub(r"[^\S\t]+", " ", text[start:end]).strip()
            for start, end in dependency_layer.source_sentence_spans(text)]


def _physical_rows(text):
    """Keep original physical-line offsets, including empty-line boundaries."""
    result, offset = [], 0
    for line in str(text).splitlines(keepends=True):
        # Delimiters between physical rows are not private cell content. A
        # following sentence may own that newline in the source tokenizer.
        result.append((offset, offset+len(line.rstrip('\r\n')), line.strip()))
        offset += len(line)
    return result


def _ids(text):
    result = set()
    for match in _DATASET.finditer(str(text)):
        value = (match[2] or match[3]).rstrip(".:/")
        if match["marker"] or match[3] or re.search(r"\d|[-_:/]", value):
            result.add(value)
    return result


def _tokens(text):
    return {word.casefold() for word in re.findall(r"[A-Za-z]+", text)}


def _label_text(text):
    """Normalize separators for field-label recognition, never source output."""
    return re.sub(r'[_-]+', ' ', str(text))


def _sensitive_header(text):
    return bool(_SENSITIVE_HEADER.search(_label_text(text)))


def _excluded_columns(columns, categories):
    requested = set(categories)
    if 'private' in requested:
        requested.update(_PRIVATE_COLUMNS)
    return any(re.search(_PRIVATE_COLUMNS[category], _label_text(column), re.I)
               for column in columns for category in requested if category in _PRIVATE_COLUMNS)


def _columns(text, *, allow_sensitive=False):
    if not allow_sensitive and _sensitive_header(text):
        return None
    for delimiter in ("|", "\t", ",", ";"):
        cells = [cell.strip() for cell in text.strip().strip("|").split(delimiter)]
        if (2 <= len(cells) <= MAX_COLUMNS and all(len(re.findall(r"[A-Za-z0-9_]+", cell)) <= 4 for cell in cells)
                and all(re.search(r"[A-Za-z\u4e00-\u9fff]", cell) for cell in cells)
                and not any(_NUMBER.fullmatch(cell) for cell in cells)):
            return delimiter, tuple(cells)
    return None


def _select_tables(units: list[dict], kept: set[int], *, private_categories=None) -> tuple[set[int], set[int]]:
    """Select only original indexes; schema indexes are budget-critical text."""
    anchors = {index for index in kept if type(index) is int and 0 <= index < len(units)}
    if not anchors:
        return set(), set()
    users = {unit.get("user_id") for unit in units if unit.get("user_id") is not None}
    if len(users) > 1:
        raise ValueError("table closure requires a single user's units")
    originals, records = {}, {}
    for index, unit in enumerate(units):
        rid = unit.get("source_rid", unit.get("rid"))
        records.setdefault(rid, []).append(index)
        if rid not in originals:
            content = unit.get("content", unit.get("text", ""))
            originals[rid] = ([literal for _, _, literal in _physical_rows(content)]
                              if private_categories is not None and unit.get('privacy_physical_rows')
                              else _segments(content))

    def source(index):
        return units[index].get("source_rid", units[index].get("rid"))

    def adjacent(left, right):
        a, b = units[left], units[right]
        if any(a.get(field) != b.get(field) for field in ("user_id", "session_id", "role")):
            return False
        if type(a.get("segment")) is not int or type(b.get("segment")) is not int:
            return False
        if source(left) == source(right):
            return a.get("seq") == b.get("seq") and b["segment"] == a["segment"]+1
        return (type(a.get("seq")) is int and type(b.get("seq")) is int and b["seq"] == a["seq"]+1
                and b["segment"] == 0 and a["segment"] == len(originals[source(left)])-1)

    privacy = private_categories is not None
    declarations = []
    for index, unit in enumerate(units):
        parsed = _columns(str(unit.get("text", "")), allow_sensitive=privacy)
        if not parsed or _PERSONAL.search(str(unit.get("text", ""))):
            continue
        segment = unit.get("segment")
        if type(segment) is not int:
            continue
        local = originals[source(index)][max(0, segment-3):segment+2]
        if not any(_DECLARATION.search(text) for text in local):
            continue
        delimiter, columns = parsed
        if privacy and not _excluded_columns(columns, private_categories):
            continue
        ids = set().union(*(_ids(text) for text in local)) if local else set()
        # A schema with multiple explicit dataset identifiers cannot silently
        # choose one namespace for a later cross-session reference.
        ids = ids if len(ids) == 1 else set()
        declarations.append({"index": index, "delimiter": delimiter, "columns": columns,
                             "ids": ids, "missing": set(),
                             "tokens": set().union(*(_tokens(column) for column in columns))})

    alignment_spans = {}

    def markdown_alignment(index, declaration):
        """Recognize only a declared header's original next physical line.

        An alignment row is source metadata, never a measurement or a reason
        to skip an unknown sentence, empty line, removed segment or new turn.
        """
        header = declaration['index']
        if (declaration['delimiter'] != '|' or index != header + 1
                or source(index) != source(header) or not adjacent(header, index)):
            return False
        text = str(units[index].get('text', '')).strip()
        if text.startswith('|'):
            text = text[1:]
        if text.endswith('|'):
            text = text[:-1]
        cells = [cell.strip() for cell in text.split('|')]
        if (len(cells) != len(declaration['columns'])
                or not all(_MARKDOWN_ALIGNMENT_CELL.fullmatch(cell) for cell in cells)):
            return False
        rid = source(index)
        content = str(units[header].get('content', units[header].get('text', '')))
        physical = bool(privacy and units[header].get('privacy_physical_rows'))
        cache_key = (rid, physical)
        if cache_key not in alignment_spans:
            alignment_spans[cache_key] = ([(start, end) for start, end, _ in _physical_rows(content)]
                if physical else dependency_layer.source_sentence_spans(content))
        spans = alignment_spans[cache_key]
        first, second = units[header]['segment'], units[index]['segment']
        if not (0 <= first < len(spans) and 0 <= second < len(spans)):
            return False
        end, start = spans[first][1], spans[second][0]
        while end > spans[first][0] and content[end-1].isspace():
            end -= 1
        while start < spans[second][1] and content[start].isspace():
            start += 1
        return bool(re.fullmatch(r'[\t ]*\r?\n[\t ]*', content[end:start]))

    def metadata(index, declaration):
        text = str(units[index].get("text", ""))
        if _PERSONAL.search(text) or not privacy and _sensitive_header(text):
            return False
        ids = _ids(text)
        if ids:
            return bool(declaration["ids"] and ids <= declaration["ids"])
        if _DECLARATION.search(text) or _REFERENCE.search(text) or _columns(text, allow_sensitive=privacy) == (declaration["delimiter"], declaration["columns"]):
            return True
        if markdown_alignment(index, declaration):
            return True
        # A source may name its observations in one sentence and introduce
        # their columns in the very next one. Keep that explicit local subject
        # attached to the declaration; an unrelated source title, hidden gap,
        # personal field or different dataset cannot supply this association.
        following = index+1
        if (_TABLE_WORD.search(text) and following < len(units)
                and source(following) == source(index) and adjacent(index, following)
                and _DECLARATION.search(str(units[following].get("text", "")))):
            return True
        words = _tokens(text)
        if _UNIT.search(text) and words & declaration["tokens"]:
            return True
        if _MISSING.search(text):
            return bool(words & declaration["tokens"] or _TABLE_WORD.search(text))
        if _CONTROL.search(text) and (_TABLE_WORD.search(text) or words & declaration["tokens"]):
            return True
        if _ELSE.search(text):
            previous = index-1
            return (previous >= 0 and source(previous) == source(index) and adjacent(previous, index)
                    and bool(_CONTROL.search(str(units[previous].get("text", "")))))
        return False

    def metadata_group(index, declaration):
        group = {index}
        for direction in (-1, 1):
            position = index+direction
            while 0 <= position < len(units) and source(position) == source(index):
                left, right = sorted((position, position-direction))
                if not adjacent(left, right) or not metadata(position, declaration):
                    break
                group.add(position)
                position += direction
        return group

    def row_shape(index, declaration):
        text = str(units[index].get("text", "")).strip().rstrip(".。")
        if (not privacy and _PERSONAL.search(text)) or _ids(text):
            return None
        cells = [cell.strip() for cell in text.split(declaration["delimiter"])]
        if len(cells) != len(declaration["columns"]):
            cells = [cell.strip() for cell in text.strip("|").split(declaration["delimiter"])]
        if privacy and len(cells) != len(declaration['columns']):
            try:
                cells = [cell.strip() for cell in next(csv.reader([text], delimiter=declaration['delimiter'], strict=True))]
            except (csv.Error, StopIteration):
                return None
        if len(cells) != len(declaration["columns"]):
            return None
        mask = []
        for cell in cells:
            if privacy:
                # Withholding a declared private field must not depend on its
                # value looking like a number or an identifier. Phone numbers,
                # full names and street addresses can contain spaces/symbols.
                if '\n' in cell:
                    return None
                mask.append(False)
                continue
            if cell.upper() in declaration["missing"]:
                mask.append(None)
            elif _NUMBER.fullmatch(cell):
                mask.append(True)
            elif _NOMINAL.fullmatch(cell):
                mask.append(False)
            else:
                return None
        return tuple(mask)

    def scan(group, declaration):
        rows, pending = set(), []
        base = None
        for direction, edge in ((-1, min(group)), (1, max(group))):
            branch, position, previous = [], edge+direction, edge
            while 0 <= position < len(units):
                left, right = sorted((position, previous))
                if not adjacent(left, right):
                    break
                shape = row_shape(position, declaration)
                if shape is None:
                    break
                if base is None and None not in shape:
                    base = shape
                if base is not None and any(value is not None and value != base[slot] or value is None and not base[slot]
                                            for slot, value in enumerate(shape)):
                    break
                branch.append((position, shape))
                previous, position = position, position+direction
            pending.extend(branch)
        if base is None:
            return set()
        # No missing numeric cell is inferred from the input. It remains NA in
        # the literal row; only the explicitly declared missing marker is used
        # to recognize that the same table continues after this record.
        for index, shape in pending:
            if all(value is not None and value == base[slot] or value is None and base[slot]
                   for slot, value in enumerate(shape)):
                rows.add(index)
        return rows

    extra, schemas = set(), set()
    for declaration in declarations:
        group = metadata_group(declaration["index"], declaration)
        # Original-content text is used above only for adjacency validation.
        # A remote or privacy-hidden declaration cannot license NA or assign a
        # dataset namespace to this separate contiguous schema group.
        group_ids = set().union(*(_ids(units[index].get("text", "")) for index in group))
        declaration["ids"] = group_ids if len(group_ids) == 1 else set()
        declaration["missing"] = {next(value for value in match.groups() if value).upper()
            for index in group for match in _MISSING.finditer(str(units[index].get("text", "")))}
        rows = scan(group, declaration)
        if len(rows) >= (1 if privacy else MIN_ROWS) and (anchors & group or anchors & rows):
            extra.update(rows | group)
            schemas.update(group)
        declaration["group"] = group
        declaration["signature"] = (declaration["delimiter"], declaration["columns"],
                                    tuple(str(units[index].get("text", "")) for index in sorted(group)))

    # A reference is an explicit original source, not an inferred session link.
    # It can borrow a unique preceding schema for classification only; returned
    # evidence still consists of the old declaration and new original rows.
    for rid, indexes in records.items():
        visible_reference = [str(units[index].get("text", "")) for index in indexes
                             if not _PERSONAL.search(str(units[index].get("text", "")))
                             and (privacy or not _sensitive_header(str(units[index].get("text", ""))))]
        reference_ids = set().union(*(_ids(text) for text in visible_reference))
        if (len(reference_ids) != 1 or not any(_REFERENCE.search(text) for text in visible_reference)
                or not anchors.intersection(indexes)):
            continue
        reference = units[indexes[0]]
        earlier = [declaration for declaration in declarations if declaration["ids"] == reference_ids
                   and units[declaration["index"]].get("role") == reference.get("role")
                   and type(units[declaration["index"]].get("seq")) is int and type(reference.get("seq")) is int
                   and units[declaration["index"]]["seq"] < reference["seq"]]
        if not earlier or len({declaration["signature"] for declaration in earlier}) != 1:
            continue
        declaration = earlier[0]
        if not metadata(indexes[0], declaration):
            continue
        reference_group = metadata_group(indexes[0], declaration)
        if (not set(indexes) <= reference_group
                or not any(_REFERENCE.search(str(units[index].get("text", ""))) for index in reference_group)):
            continue  # hidden/private/unrelated source text is a boundary.
        rows = scan(reference_group, declaration)
        if len(rows) >= (1 if privacy else MIN_ROWS):
            extra.update(rows | reference_group | declaration["group"])
            schemas.update(reference_group | declaration["group"])
    return (extra if privacy else extra - anchors), schemas


def expand(units: list[dict], kept: set[int]) -> tuple[set[int], set[int]]:
    """Select original declared-table indexes for ordinary evidence closure."""
    return _select_tables(units, kept)


def excluded_table_indexes(units: list[dict], categories) -> set[int]:
    """Withhold explicitly excluded table fields, including anonymous rows.

    The complete affected row is withheld rather than inventing a projected or
    redacted observation. Schema, role, session, dataset and original adjacency
    boundaries use the same scanner as table closure. A single private row is
    sufficient; privacy does not require a three-observation sample.
    """
    if not categories or not units:
        return set()
    rows, schemas = _select_tables(units, set(range(len(units))), private_categories=categories)
    return rows | schemas


def excluded_source_spans(rows, categories) -> dict[int, list[tuple[int, int]]]:
    """Locate private table rows before punctuation can split cell values.

    Only privacy classification uses physical lines. All returned spans refer
    to unchanged source text; ordinary evidence tokenization is unaffected.
    Blank physical lines remain segment gaps, including at source boundaries.
    """
    if not categories:
        return {}
    rows = sorted((dict(row) for row in rows), key=lambda row: (row['seq'], row['rid']))
    units = []
    for row in rows:
        for segment, (start, end, literal) in enumerate(_physical_rows(row['content'])):
            if literal:
                units.append(dict(row, source_rid=row['rid'], segment=segment, text=literal,
                                  source_char_start=start, source_char_end=end,
                                  privacy_physical_rows=True))
    withheld = excluded_table_indexes(units, categories)
    result = {}
    for index in sorted(withheld):
        unit = units[index]
        result.setdefault(unit['source_rid'], []).append((unit['source_char_start'], unit['source_char_end']))
    return result
