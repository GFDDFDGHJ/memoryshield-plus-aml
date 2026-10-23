"""Explicit delimited-table closure over original source-unit indexes.

expand(units, kept) -> (extra_indexes, schema_indexes). No input is modified;
no row is merged, value filled, unit converted, statistic calculated or version
chosen. The caller must preserve each selected unit's original source metadata.

Only declared, stable tables with >=3 rows and >=2 numeric columns qualify.
Schema declarations can precede or follow a block. A declared NA may occupy a
numeric column; arbitrary prose and undeclared missing values stop the block.
Cross-session inheritance additionally requires the same explicit dataset ID,
an explicit reference to an earlier schema, and one unambiguous prior schema.
Source segment/record-sequence gaps, role/session changes and personal fields
are boundaries, including gaps left by privacy filtering.
"""
from __future__ import annotations

import re

MIN_ROWS = 3
MAX_COLUMNS = 16
_SENTENCES = re.compile(r".+?(?:[。！？!?]|\.(?!\d)(?=\s|$)|\n+|$)", re.S)
_DECLARATION = re.compile(r"\b(?:schema|columns?|fields?|column\s+layout)\b|列名|列义|字段|表头", re.I)
_REFERENCE = re.compile(r"\b(?:earlier|previous|prior|shared|same|preceding)\b.{0,70}\b(?:schema|columns?|layout|validity\s+convention)\b|(?:先前|以前|前述|共享|相同|上述).{0,20}(?:模式|列义|列名|字段|表头)", re.I)
_DATASET = re.compile(r"\bdata[ _-]?set\s*(?P<marker>id|identifier|code)?\s*[:=#]?\s*[\"'`]?([A-Za-z0-9][A-Za-z0-9_.:/-]{2,63})|(?:数据集|資料集)(?:编号|标识)?\s*[:：=]?\s*([A-Za-z0-9][A-Za-z0-9_.:/-]{2,63})", re.I)
_NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?(?:\s*[A-Za-zµμ°%][A-Za-z0-9µμ°%/^²³-]{0,12})?")
_NOMINAL = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,31}")
_MISSING = re.compile(r"\bmissing\b[^.!?\n]{0,100}\b(NA|N/A|NULL|NaN)\b|\b(NA|N/A|NULL|NaN)\b[^.!?\n]{0,80}\bmissing\b|缺失[^。！？\n]{0,40}\b(NA|N/A|NULL|NaN)\b", re.I)
_UNIT = re.compile(r"\b(?:units?|pascals?|kilopascals?|seconds?|milliseconds?|kilograms?|grams?|metres?|meters?|litres?|liters?|volts?|amperes?|degrees?|kPa|MPa|Pa|ms|kg|mL)\b|单位|帕|毫秒|千克|毫升", re.I)
_CONTROL = re.compile(r"^\s*(?:only|unless|provided|if|when|except|otherwise|do\s+not|never)\b|^\s*(?:仅|只有|除非|如果|当|否则|不要|不得|禁止)", re.I)
_ELSE = re.compile(r"^\s*otherwise\b|^\s*否则", re.I)
_TABLE_WORD = re.compile(r"\b(?:data|observations?|measurements?|samples?|records?|rows?|readings?|trials?|runs?|cells?|values?)\b|数据|观测|测量|样本|记录|读数|单元格|行", re.I)
_SENSITIVE_HEADER = re.compile(r"\b(?:address|phone|telephone|email|passcode|password|salary|diagnosis|bank[_ ]?account|credit[_ ]?card)\b|住址|地址|手机号|邮箱|密码|工资|诊断|银行账号", re.I)
_PERSONAL = re.compile(r"^\s*(?:my|his|her|your|our)\b|^\s*(?:their)\s+(?!(?:units?|columns?|fields?|dimensions?)\b)|^\s*I\s+(?:live|work|am)\b|(?:我的|他的|她的|私人)(?:地址|住址|电话|邮箱|密码|病历)|\b(?:bank\s+account|medical\s+record|private\s+(?:address|code|phone))\b", re.I)


def _segments(text):
    return [re.sub(r"[^\S\t]+", " ", match.group(0)).strip()
            for match in _SENTENCES.finditer(str(text)) if match.group(0).strip()]


def _ids(text):
    result = set()
    for match in _DATASET.finditer(str(text)):
        value = (match[2] or match[3]).rstrip(".:/")
        if match["marker"] or match[3] or re.search(r"\d|[-_:/]", value):
            result.add(value)
    return result


def _tokens(text):
    return {word.casefold() for word in re.findall(r"[A-Za-z]+", text)}


def _columns(text):
    if _SENSITIVE_HEADER.search(text):
        return None
    for delimiter in ("|", "\t", ",", ";"):
        cells = [cell.strip() for cell in text.strip().strip("|").split(delimiter)]
        if (2 <= len(cells) <= MAX_COLUMNS and all(len(re.findall(r"[A-Za-z0-9_]+", cell)) <= 4 for cell in cells)
                and all(re.search(r"[A-Za-z\u4e00-\u9fff]", cell) for cell in cells)
                and not any(_NUMBER.fullmatch(cell) for cell in cells)):
            return delimiter, tuple(cells)
    return None


def expand(units: list[dict], kept: set[int]) -> tuple[set[int], set[int]]:
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
            originals[rid] = _segments(unit.get("content", unit.get("text", "")))

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

    declarations = []
    for index, unit in enumerate(units):
        parsed = _columns(str(unit.get("text", "")))
        if not parsed or _PERSONAL.search(str(unit.get("text", ""))):
            continue
        segment = unit.get("segment")
        if type(segment) is not int:
            continue
        local = originals[source(index)][max(0, segment-3):segment+2]
        if not any(_DECLARATION.search(text) for text in local):
            continue
        delimiter, columns = parsed
        ids = set().union(*(_ids(text) for text in local)) if local else set()
        # A schema with multiple explicit dataset identifiers cannot silently
        # choose one namespace for a later cross-session reference.
        ids = ids if len(ids) == 1 else set()
        declarations.append({"index": index, "delimiter": delimiter, "columns": columns,
                             "ids": ids, "missing": set(),
                             "tokens": set().union(*(_tokens(column) for column in columns))})

    def metadata(index, declaration):
        text = str(units[index].get("text", ""))
        if _PERSONAL.search(text) or _SENSITIVE_HEADER.search(text):
            return False
        ids = _ids(text)
        if ids:
            return bool(declaration["ids"] and ids <= declaration["ids"])
        if _DECLARATION.search(text) or _REFERENCE.search(text) or _columns(text) == (declaration["delimiter"], declaration["columns"]):
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
        if _PERSONAL.search(text) or _ids(text):
            return None
        cells = [cell.strip() for cell in text.strip("|").split(declaration["delimiter"])]
        if len(cells) != len(declaration["columns"]):
            return None
        mask = []
        for cell in cells:
            if cell.upper() in declaration["missing"]:
                mask.append(None)
            elif _NUMBER.fullmatch(cell):
                mask.append(True)
            elif _NOMINAL.fullmatch(cell):
                mask.append(False)
            else:
                return None
        if sum(value is True or value is None for value in mask) < 2 or mask.count(False) > 2:
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
        if len(rows) >= MIN_ROWS and (anchors & group or anchors & rows):
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
                             and not _SENSITIVE_HEADER.search(str(units[index].get("text", "")))]
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
        if len(rows) >= MIN_ROWS:
            extra.update(rows | reference_group | declaration["group"])
            schemas.update(reference_group | declaration["group"])
    return extra - anchors, schemas
