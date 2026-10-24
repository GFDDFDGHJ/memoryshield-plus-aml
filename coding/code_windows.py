"""Literal code windows. No storage, providers, execution or inferred facts.

Offsets use Python string characters (half open), and lines are one based and
inclusive. Formatting is evidence: returned text is always an unchanged slice
of the source. Callers can withhold original character ranges; a line touching
one is never selected or used as an implicit bridge to a neighbouring line.
"""
from __future__ import annotations

from bisect import bisect_right
import re
from typing import Iterable

try:
    from .code_structure import structure_index
except ImportError:  # Standalone app/test commands run with coding/ as cwd.
    from code_structure import structure_index

_IDENTIFIER = re.compile(r"[^\W\d]\w*|\$[A-Za-z_]\w*", re.UNICODE)
_QUALIFIED = re.compile(r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+\b")
_PATH = re.compile(r"(?<![\w.@+-])(?:[A-Za-z]:[\\/]|\.{0,2}[\\/])?(?:[\w.@+-]+[\\/])+[\w.@+-]+")
_FILE = re.compile(r"(?<![\w.@+-])[\w@+.-]+\.(?:py|pyi|js|jsx|ts|tsx|json|yaml|yml|toml|rs|go|java|kt|c|h|cpp|hpp|cs|rb|php|sh|sql|md|txt|log)\b", re.I)
_HEX = re.compile(r"\b0[xX][0-9A-Fa-f]+\b")
_STOP = frozenset("a an the i me my our your you we about tell show please how why what find in on at for with and or to from that this of it when can could do did as be is are was were need want explain remember function method code file lines line".split())
_PY_HEADER = re.compile(r"^[ \t]*(?:(?:async)[ \t]+)?(?:def|class)[ \t]+[^\W\d]\w*\b.*:[ \t]*(?:#.*)?$", re.U)
_BRACE_HEADER = re.compile(
    r"^[ \t]*(?:(?:export|default|public|private|protected|static|async|final|abstract|virtual|inline)\s+)*"
    r"(?:(?:function|fn|func|class|interface|struct|enum|trait|impl)\b.+|"
    r"(?:[\w:<>,.*&\[\]]+\s+)+[A-Za-z_]\w*\s*\([^;]*\)\s*(?:const\s*)?)"
    r"\{[ \t]*(?://.*)?$", re.U)
_FILE_HEADER = re.compile(r"^[ \t]*(?:(?:\#+|//|/\*|\*)[ \t]*)?(?:file|path|filename)[ \t]*:[ \t]*\S+", re.I)
_FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})([^\r\n]*)$")


def code_tokens(text: str) -> set[str]:
    """Keep full identifiers, qualified names and paths; add casefold aliases.

    These are matching keys only. Source text is never casefolded or rewritten.
    Single-letter identifiers remain available to a caller's lexical ranker.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    tokens = set(_IDENTIFIER.findall(text)) | set(_QUALIFIED.findall(text))
    tokens.update(_PATH.findall(text)); tokens.update(_FILE.findall(text)); tokens.update(_HEX.findall(text))
    for token in tuple(tokens):
        tokens.add(token.casefold())
        if '\\' in token:
            tokens.add(token.replace('\\', '/')); tokens.add(token.replace('\\', '/').casefold())
    # Contiguous CJK prose is not a Western identifier. Bigrams permit literal
    # matching without guessing word segmentation or calling a language model.
    for run in re.findall(r"[\u3400-\u9fff]{2,}", text):
        tokens.update(run[index:index+2] for index in range(len(run)-1))
    return tokens


def _indent(line: str) -> int:
    return len(line[:len(line)-len(line.lstrip(' \t'))].expandtabs())


def _literal_offset(line: str, terms: set[str]) -> int:
    """Map matching aliases back to original characters, including ß→ss.

    This view is used solely for choosing a partial line. Returned evidence
    remains an exact source slice, including case and original path slashes.
    """
    positions=[line.find(term) for term in terms if term in line]
    if positions:return min(positions)
    folded=[];original=[]
    for index,char in enumerate(line):
        alias=char.replace('\\','/').casefold()
        folded.append(alias);original.extend([index]*len(alias))
    alias=''.join(folded)
    for term in terms:
        found=alias.find(term.replace('\\','/').casefold())
        if found>=0:positions.append(original[found])
    return min(positions) if positions else 0


def _merge(ranges):
    merged = []
    for start, end, reason in sorted(ranges):
        if merged and start <= merged[-1][1]:
            left, right, reasons = merged[-1]
            merged[-1] = (left, max(right, end), reasons | {reason})
        else:
            merged.append((start, end, {reason}))
    return merged


def _brace_open_through(lines: list[str], start: int, end: int) -> bool:
    """Conservative single-line-string brace check; ambiguous syntax opts out."""
    balance = 0
    for index in range(start, end):
        line = lines[index]
        if any(token in line for token in ('/*', '*/', '`', "'''", '"""')):
            return False
        line = re.sub(r'''"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*' ''', '', line, flags=re.X)
        line = re.sub(r'//.*$', '', line)
        balance += line.count('{')-line.count('}')
        if balance <= 0:
            return False
    return balance > 0


def _headers(lines: list[str], anchor: int) -> list[int]:
    """Attach bounded lexical scope headers, not a transitive dependency graph."""
    result = []
    for index in range(anchor-1, max(-1, anchor-201), -1):
        text = lines[index].rstrip('\r\n')
        if _FILE_HEADER.match(text):
            result.append(index)
            break  # A new explicitly named file is a scope boundary.
        if _PY_HEADER.fullmatch(text) and _indent(lines[anchor]) > _indent(text):
            significant = [line for line in lines[index+1:anchor+1]
                           if line.strip() and not line.lstrip().startswith('#')]
            if significant and all(_indent(line) > _indent(text) for line in significant):
                result.append(index)
        elif _BRACE_HEADER.fullmatch(text) and _brace_open_through(lines,index,anchor):
            result.append(index)
        if len(result) >= 2:
            break
    return result


def code_windows(text: str, query: str, max_chars: int = 6000, *,
                 max_windows: int = 6, context_lines: int = 2,
                 excluded_ranges: Iterable[tuple[int, int]] = ()) -> list[dict]:
    """Select bounded, nonoverlapping exact source slices in original order.

    Matching uses literal identifier/path/error tokens. Statically bounded
    complete functions, referenced imports, direct-call functions, lexical
    headers and actual fence edges compete for the same strict source budget.
    No missing brace, fence,
    declaration, value or dependency is invented. Unknown-language/no-match
    input falls back to an explicitly labelled literal opening, not a summary.
    """
    if not isinstance(text,str) or not isinstance(query,str):
        raise TypeError("text and query must be strings")
    if any(type(value) is not int for value in (max_chars,max_windows,context_lines)):
        raise TypeError("window budgets must be integers")
    if not text or max_chars <= 0 or max_windows <= 0:
        return []
    max_windows = min(max_windows,32); context_lines = max(0,min(context_lines,20))
    excluded = []
    for pair in excluded_ranges:
        if (not isinstance(pair,(tuple,list)) or len(pair)!=2
                or any(type(value) is not int for value in pair)
                or not 0 <= pair[0] < pair[1] <= len(text)):
            raise ValueError("excluded ranges must be valid original character intervals")
        excluded.append(tuple(pair))
    if not excluded and len(text) <= max_chars:
        return [dict(text=text,start=0,end=len(text),start_line=1,
                     end_line=max(1,len(text.splitlines())),reason='complete-source')]
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines: offsets.append(offsets[-1]+len(line))
    allowed = [not any(offsets[i] < end and offsets[i+1] > start for start,end in excluded)
               for i in range(len(lines))]
    explicit = set().union(*(code_tokens(part) for part in re.findall(r'`([^`\n]+)`',query))) if '`' in query else set()
    terms = {token for token in code_tokens(query) if token.casefold() not in _STOP and (len(token)>1 or token in explicit)} | explicit
    def weight(token):
        return 8 if '/' in token or '\\' in token or _FILE.fullmatch(token) else 6 if token in explicit else 4 if '_' in token or '$' in token or _HEX.fullmatch(token) else 2
    matches = []
    for index,line in enumerate(lines):
        overlap = terms & code_tokens(line)
        if allowed[index] and overlap:
            matches.append((sum(weight(token) for token in overlap),index,overlap))
    matches.sort(key=lambda item:(-item[0],item[1]))
    if not matches:
        first = next((i for i,yes in enumerate(allowed) if yes and lines[i].strip()),None)
        if first is None:return []
        matches = [(0,first,set())]

    fence_for = {}; opened = None
    for index,line in enumerate(lines):
        match = _FENCE.fullmatch(line.rstrip('\r\n'))
        if not match:continue
        marker = match[1]
        if opened is None:
            opened = (index,marker)
        elif marker[0] == opened[1][0] and len(marker)>=len(opened[1]) and not match[2].strip():
            for enclosed in range(opened[0]+1,index): fence_for[enclosed]=(opened[0],index)
            opened = None
    chosen = []
    def admit(start,end,reason):
        nonlocal chosen
        if start>=end or any(start<right and end>left for left,right in excluded):return False
        trial = _merge(chosen+[(start,end,reason)])
        if len(trial)>max_windows or sum(right-left for left,right,_ in trial)>max_chars:return False
        chosen.append((start,end,reason));return True
    structure=structure_index(lines,allowed) if any(score for score,_,_ in matches) else None
    expanded=set()
    def admit_lines(start,end,reason):
        return (0<=start<end<=len(lines) and all(allowed[start:end])
                and admit(offsets[start],offsets[end],reason))
    for score,index,overlap in matches[:max_windows*8]:
        start,end = offsets[index],offsets[index+1]
        if end-start>max_chars:
            at = _literal_offset(lines[index],overlap)
            left = max(start,start+at-max_chars//3)
            right = min(end,left+max_chars);left=max(start,right-max_chars)
            admit(left,right,'literal-match-partial-line' if score else 'fallback-partial-line')
            continue
        block=structure.enclosing(index) if structure else None
        complete=False
        if block is not None:
            complete=admit_lines(block.start,block.end,'complete-static-function')
            identity=(block.start,block.end)
            if identity not in expanded:
                expanded.add(identity)
                if complete:
                    for left,right,reason in structure.dependencies(block):
                        # Withheld source is never a bridge to a dependency.
                        if all(allowed[min(left,index):max(right,index+1)]):
                            admit_lines(left,right,reason)
        if not admit(start,end,'literal-token-match' if score else 'fallback-no-literal-match'):continue
        if block is not None and not complete and block.signature:
            admit_lines(*block.signature,'structured-signature-partial-body')
        for header in _headers(lines,index):
            if allowed[header] and all(allowed[min(header,index):max(header,index)+1]):
                admit(offsets[header],offsets[header+1],'lexical-scope-header')
        if index in fence_for:
            for edge in fence_for[index]:
                if allowed[edge]:admit(offsets[edge],offsets[edge+1],'original-fence-edge')
        for distance in range(1,(0 if complete else context_lines)+1):
            for neighbour in (index-distance,index+distance):
                if (0<=neighbour<len(lines) and all(allowed[min(neighbour,index):max(neighbour,index)+1])):
                    admit(offsets[neighbour],offsets[neighbour+1],'adjacent-literal-line')
    result = []
    for start,end,reasons in _merge(chosen):
        result.append(dict(text=text[start:end],start=start,end=end,
                           start_line=bisect_right(offsets,start),
                           end_line=bisect_right(offsets,max(start,end-1)),
                           reason=','.join(sorted(reasons))))
    return result


def render_code_windows(source: str, windows: list[dict]) -> str:
    """Label source coordinates and every omitted gap without rewriting code."""
    if not isinstance(source,str):raise TypeError('source must be a string')
    offsets=[0]
    for line in source.splitlines(keepends=True):offsets.append(offsets[-1]+len(line))
    out=[];at=0
    for window in windows:
        start,end=window['start'],window['end']
        if (type(start) is not int or type(end) is not int or not at<=start<end<=len(source)
                or window['text']!=source[start:end]):
            raise ValueError('windows must be ordered nonoverlapping exact source slices')
        if start>at:
            out.append(f'[SOURCE GAP chars={at}:{start} lines={bisect_right(offsets,at)}:{bisect_right(offsets,start-1)} omitted]\n')
        first,last=bisect_right(offsets,start),bisect_right(offsets,end-1)
        out.append(f'[CODE SOURCE chars={start}:{end} lines={first}:{last}; exact source excerpt]\n')
        out.append(source[start:end])
        if not source[start:end].endswith(('\n','\r')):out.append('\n')
        at=end
    if at<len(source):
        out.append(f'[SOURCE GAP chars={at}:{len(source)} lines={bisect_right(offsets,at)}:{bisect_right(offsets,len(source)-1)} omitted]\n')
    return ''.join(out)
