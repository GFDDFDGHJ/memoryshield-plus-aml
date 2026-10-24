"""Conservative source-only function ranges and literal local relationships.

This module parses declarations, never executes source or resolves a program.
All ranges are physical, zero-based half-open line intervals in the original
message. Ambiguous or incomplete syntax opts out to the caller's literal view.
"""
from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass, field
import io
import re
import tokenize


_FILE = re.compile(r'^[ \t]*(?:(?:\#+|//|/\*|\*)[ \t]*)?(?:file|path|filename)[ \t]*:[ \t]*\S+',re.I)
_FENCE = re.compile(r'^[ \t]{0,3}(`{3,}|~{3,})([^\r\n]*)$')
_WORD = re.compile(r'[A-Za-z_$][\w$]*',re.U)
_CALL = re.compile(r'(?<![\w.$])([A-Za-z_$][\w$]*)\s*\(')
_CONTROL = frozenset('if for while switch catch with do else return throw new typeof sizeof assert'.split())


@dataclass
class Block:
    start: int
    end: int
    name: str
    region: tuple[int,int]
    references: frozenset[str] = frozenset()
    calls: frozenset[str] = frozenset()
    shadows: frozenset[str] = frozenset()
    headers: tuple[tuple[int,int],...] = ()
    imports: tuple[tuple[int,int,frozenset[str]],...] = ()
    linkable: bool = False
    signature: tuple[int,int] | None = None
    source_header: tuple[int,int] | None = None


def _regions(lines,allowed):
    """Explicit file markers and actual fences prevent cross-file linking."""
    chunks=[];start=0;source_header=None
    for index,line in enumerate(lines):
        if allowed[index] and _FILE.match(line):
            if start<index:chunks.append((start,index,source_header))
            start=index+1;source_header=(index,index+1)
    if start<len(lines):chunks.append((start,len(lines),source_header))
    for start,end,source_header in chunks:
        at=start;opened=None
        for index in range(start,end):
            match=_FENCE.fullmatch(lines[index].rstrip('\r\n')) if allowed[index] else None
            if not match:continue
            marker=match[1]
            if opened is None:
                if at<index:yield at,index,source_header
                opened=(index,marker)
            elif marker[0]==opened[1][0] and len(marker)>=len(opened[1]) and not match[2].strip():
                if opened[0]+1<index:yield opened[0]+1,index,source_header
                opened=None;at=index+1
        if opened is not None:
            if opened[0]+1<end:yield opened[0]+1,end,source_header
        elif at<end:yield at,end,source_header


def _start(node):
    return min([node.lineno]+[item.lineno for item in getattr(node,'decorator_list',())])-1


def _python_header_ends(source):
    result={};active=None;depth=0
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if active is None and token.type==tokenize.NAME and token.string in ('def','class'):
                active=token.start[0];depth=0
            elif active is not None and token.type==tokenize.OP:
                if token.string in ('(','[','{'):depth+=1
                elif token.string in (')',']','}'):depth-=1
                elif token.string==':' and depth==0:
                    result[active]=token.end[0];active=None
    except (tokenize.TokenError,IndentationError):pass
    return result


def _header(node,base,header_ends):
    # Header tokenization excludes body comments and handles multiline types.
    return base+_start(node),base+header_ends.get(node.lineno,node.lineno)


def _scope_walk(node):
    """Names of a nested definition are a boundary, not its caller's locals."""
    yield node
    for child in ast.iter_child_nodes(node):
        if isinstance(child,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef,ast.Lambda)):
            continue
        yield from _scope_walk(child)


def _import(node,base):
    if isinstance(node,ast.Import):
        names=frozenset(alias.asname or alias.name.split('.')[0] for alias in node.names)
    else:
        names=frozenset(alias.asname or alias.name for alias in node.names if alias.name!='*')
    return base+node.lineno-1,base+node.end_lineno,names


def _python_blocks(source,base,end):
    try:tree=ast.parse(source)
    except (SyntaxError,ValueError,RecursionError,MemoryError):return None
    all_nodes=list(ast.walk(tree))
    functions=[node for node in all_nodes if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))]
    if len(all_nodes)>30000 or len(functions)>256:return []
    parents={child:node for node in all_nodes for child in ast.iter_child_nodes(node)}
    header_ends=_python_header_ends(source)
    scope_cache={};import_cache={}
    def scope_nodes(scope):
        if scope not in scope_cache:scope_cache[scope]=list(_scope_walk(scope))
        return scope_cache[scope]
    blocks=[]
    for node in functions:
        ancestors=[];parent=parents.get(node)
        while parent is not None:
            ancestors.append(parent);parent=parents.get(parent)
        lexical=[item for item in ancestors if isinstance(item,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))]
        headers=tuple(_header(item,base,header_ends) for item in lexical[:2])
        imports=[]
        for scope in [item for item in ancestors if isinstance(item,(ast.Module,ast.FunctionDef,ast.AsyncFunctionDef))]:
            if scope not in import_cache:
                import_cache[scope]=tuple(_import(item,base) for item in scope_nodes(scope) if isinstance(item,(ast.Import,ast.ImportFrom)))
            imports.extend(import_cache[scope])
        nodes=scope_nodes(node)
        refs=frozenset(item.id for item in nodes if isinstance(item,ast.Name) and isinstance(item.ctx,ast.Load))
        calls=frozenset(item.func.id for item in nodes if isinstance(item,ast.Call) and isinstance(item.func,ast.Name))
        shadows={item.id for item in nodes if isinstance(item,ast.Name) and isinstance(item.ctx,ast.Store)}
        shadows.update(item.arg for item in nodes if isinstance(item,ast.arg))
        for item in nodes:
            if isinstance(item,(ast.Import,ast.ImportFrom)):shadows.update(_import(item,base)[2])
            elif isinstance(item,(ast.Global,ast.Nonlocal)):shadows.update(item.names)
        blocks.append(Block(base+_start(node),base+node.end_lineno,node.name,(base,end),refs,calls,
                            frozenset(shadows),headers,tuple(imports),not lexical,_header(node,base,header_ends)))
    return blocks


def _sanitized(source):
    """Blank quoted strings/comments, preserving offsets; unknown regex opts out.

    JS template interpolation and slash expressions are deliberately unsupported
    here. The literal fallback remains available; no closing scope is invented.
    """
    chars=list(source);at=0;size=len(source)
    while at<size:
        char=source[at]
        if char in ('"',"'"):
            quote=char;start=at;at+=1;closed=False
            while at<size:
                if source[at]=='\\':at+=2;continue
                if source[at]==quote:at+=1;closed=True;break
                if source[at] in '\r\n':return None
                at+=1
            if not closed:return None
            for index in range(start,min(at,size)):
                if chars[index] not in '\r\n':chars[index]=' '
        elif source.startswith('//',at):
            finish=source.find('\n',at)
            if finish<0:finish=size
            for index in range(at,finish):
                if chars[index]!='\r':chars[index]=' '
            at=finish
        elif source.startswith('/*',at):
            finish=source.find('*/',at+2)
            if finish<0:return None
            finish+=2
            for index in range(at,finish):
                if chars[index] not in '\r\n':chars[index]=' '
            at=finish
        elif char in ('`','/'):
            return None
        else:at+=1
    return ''.join(chars)


_FUNCTION_PATTERNS=(
    re.compile(r'^[ \t]*(?:(?:export|default|async|public|private|protected|static|final|abstract)\s+)*function\s*\*?\s*(?P<name>[A-Za-z_$][\w$]*)\s*(?:<[^{};]{1,4000}>)?\s*\([^{};]{0,4000}\)[^{;]{0,4000}\{',re.M),
    re.compile(r'^[ \t]*(?:(?:pub|async|unsafe|extern)\s+)*(?:fn|func)\s+(?:\([^{};]{0,4000}\)\s*)?(?P<name>[A-Za-z_$][\w$]*)\s*\([^{};]{0,4000}\)[^{;]{0,4000}\{',re.M),
    re.compile(r'^[ \t]*(?:(?:export|public|private|protected|static|async|readonly|override)\s+)*(?P<name>[A-Za-z_$][\w$]*)\s*\([^{};]{0,4000}\)\s*(?::[^{};=]{1,4000})?\s*\{',re.M),
    re.compile(r'^[ \t]*(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?(?:\([^{};]{0,4000}\)|[A-Za-z_$][\w$]*)\s*(?::[^{};=]{1,4000})?=>\s*\{',re.M),
)
_CLASS=re.compile(r'^[ \t]*(?:(?:export|default|public|private|abstract|final)\s+)*(?:class|interface|struct|trait|impl)\s+[^{};]{1,4000}\{',re.M)
_JS_IMPORT=re.compile(r'^[ \t]*import\s+(?!\()(?P<binding>[^;]{1,4000}?)\s+from\s+[\'\"][^\r\n]{1,4000}?[\'\"][ \t]*;?',re.M)


def _brace_blocks(source,base,end):
    clean=_sanitized(source)
    if clean is None:return []
    stack=[];pairs={}
    for index,char in enumerate(clean):
        if char=='{':stack.append(index)
        elif char=='}':
            if not stack:return []
            pairs[stack.pop()]=index
    if stack:return []
    starts=[0]
    for match in re.finditer('\n',source):starts.append(match.end())
    from bisect import bisect_right
    def row(pos):return bisect_right(starts,pos)-1
    imports=[]
    for match in _JS_IMPORT.finditer(source):
        # It must really be an import token, not text inside a quoted string.
        if clean[match.start():match.start()+len(match.group())].lstrip().startswith('import'):
            binding=match['binding'];names=set()
            for part in binding.strip().strip('{}').split(','):
                words=_WORD.findall(part)
                if words:names.add(words[-1])
            imports.append((base+row(match.start()),base+row(match.end()-1)+1,frozenset(names)))
    raw=[];seen=set()
    for pattern in _FUNCTION_PATTERNS:
        for match in pattern.finditer(clean):
            opening=match.end()-1;name=match['name']
            if name in _CONTROL or opening not in pairs or opening in seen:continue
            seen.add(opening);raw.append((match,opening,pairs[opening]))
            if len(raw)>256:return []
    classes=[(match.start(),match.end()-1,pairs[match.end()-1]) for match in _CLASS.finditer(clean) if match.end()-1 in pairs]
    blocks=[]
    for match,opening,closing in raw:
        name=match['name'];body=clean[opening+1:closing]
        containing=[(other.start(),left,right) for other,left,right in raw if left<opening<closing<right]
        containing.extend(item for item in classes if item[1]<opening<closing<item[2])
        containing.sort(key=lambda item:item[2]-item[1])
        headers=tuple((base+row(start),base+row(left)+1) for start,left,_ in containing[:2])
        signature=clean[match.start():opening]
        shadows=set(_WORD.findall(signature[signature.find('(')+1:signature.rfind(')')]))
        shadows.update(re.findall(r'\b(?:const|let|var|function|class)\s+([A-Za-z_$][\w$]*)',body))
        blocks.append(Block(base+row(match.start()),base+row(closing)+1,name,(base,end),
                            frozenset(_WORD.findall(signature+body)),frozenset(_CALL.findall(body)),
                            frozenset(shadows),headers,tuple(imports),not containing,
                            (base+row(match.start()),base+row(opening)+1)))
    return blocks


@dataclass
class StructureIndex:
    blocks: list[Block] = field(default_factory=list)

    def enclosing(self,line):
        candidates=[block for block in self.blocks if block.start<=line<block.end]
        return min(candidates,key=lambda block:block.end-block.start) if candidates else None

    def dependencies(self,block):
        """Bounded literal dependencies; relation labels do not claim resolution."""
        result=[(start,end,'enclosing-signature') for start,end in block.headers]
        if block.source_header:result.append((*block.source_header,'original-file-header'))
        imports=[(start,end,'referenced-import') for start,end,names in block.imports
                 if names & block.references and not (block.start<=start<end<=block.end)]
        result.extend(imports[:6])
        siblings=[item for item in self.blocks if item.region==block.region]
        if block.linkable and Counter(item.name for item in siblings)[block.name]==1:
            callers=[item for item in siblings if item is not block and block.name in item.calls
                     and block.name not in item.shadows and not (block.start<=item.start<item.end<=block.end)]
            callers.sort(key=lambda item:(not item.name.startswith(('test_','test','check')),abs(item.start-block.start)))
            result.extend((item.start,item.end,'literal-direct-call-function') for item in callers[:2])
        return result[:12]


def structure_index(lines:list[str],allowed:list[bool])->StructureIndex:
    """Build bounded static metadata; withheld lines cannot justify a range."""
    result=StructureIndex()
    if len(lines)>30000 or sum(map(len,lines))>2000000:return result
    for start,end,source_header in _regions(lines,allowed):
        # Masking a withheld final statement could produce a valid but shorter
        # AST function, falsely labelling the visible prefix complete. Do not
        # infer any structural boundary from a segment with withheld source.
        if not all(allowed[start:end]):continue
        source=''.join(lines[start:end])
        if len(source)>200000 or end-start>10000:continue
        try:
            blocks=_python_blocks(source,start,end)
            if blocks is None:blocks=_brace_blocks(source,start,end)
        except (RecursionError,MemoryError):continue
        for block in blocks:
            if all(allowed[block.start:block.end]):
                block.source_header=source_header
                result.blocks.append(block)
    return result
