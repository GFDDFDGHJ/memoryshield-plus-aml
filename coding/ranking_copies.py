"""Query-aware literal copies for a bounded reranker, never returned evidence."""
try:
    from .code_windows import code_tokens
except ImportError:
    from code_windows import code_tokens

_GAP='\n[RANKING COPY GAP]\n'
_STOP=frozenset('a an the and or is are was were to of for in on at with '
    'what which where how why show find locate explain please me my this that '
    'from about can could do does did use using code file function implementation '
    'def return class source recorded session role'.split())

def utf8_prefix(text,budget):
    return text.encode('utf-8')[:max(0,budget)].decode('utf-8',errors='ignore')

def _suffix(text,budget):
    return text.encode('utf-8')[-budget:].decode('utf-8',errors='ignore') if budget>0 else ''

def _literal_anchor(line,terms):
    # Casefold can expand one source character (e.g. ß -> ss). Keep an explicit
    # index map so all selected spans still refer to original characters.
    folded=[];positions=[]
    for index,character in enumerate(line):
        value=character.casefold().replace('\\','/')
        folded.append(value);positions.extend([index]*len(value))
    folded=''.join(folded)
    for term in terms:
        value=term.casefold().replace('\\','/')
        at=folded.find(value)
        if at>=0 and value:return positions[at],positions[at+len(value)-1]+1
    return 0,0

def _around_anchor(line,terms,budget):
    start,end=_literal_anchor(line,terms)
    needle=line[start:end]
    if len(needle.encode('utf-8'))>budget:return utf8_prefix(needle,budget)
    free=budget-len(needle.encode('utf-8'))
    before=_suffix(line[:start],free//3)
    after=utf8_prefix(line[end:],free-len(before.encode('utf-8')))
    return before+needle+after

def query_copy(query,budget=3000):
    """Retain both task introduction and trailing constraints on long queries."""
    if len(query.encode('utf-8'))<=budget:return query
    gap=_GAP if budget>len(_GAP)+4 else ''
    left=(budget-len(gap.encode('utf-8')))//2
    return utf8_prefix(query,left)+gap+_suffix(query,budget-left-len(gap.encode('utf-8')))

def document_copy(document,query,budget=3500):
    """Fit matching lines before neighbours, preserving literal UTF-8 slices."""
    if len(document.encode('utf-8'))<=budget:return document
    if budget<=0:return ''
    terms={token for token in code_tokens(query) if len(token)>1 and token.casefold() not in _STOP}
    def weight(token):return 5 if any(char in token for char in '_./\\:$') else 2
    lines=document.splitlines(keepends=True)
    matches=[]
    for index,line in enumerate(lines):
        overlap=terms & code_tokens(line)
        if overlap:matches.append((sum(weight(token) for token in overlap),index,overlap))
    matches.sort(key=lambda row:(-row[0],row[1]))
    if not matches:return query_copy(document,budget)
    selected={};used=0
    def admit(index,overlap=()):
        nonlocal used
        if index in selected or not 0<=index<len(lines):return
        cost=len(_GAP.encode('utf-8')) if selected else 0
        remaining=budget-used-cost
        if remaining<16:return
        line=lines[index]
        if len(line.encode('utf-8'))>remaining:
            if selected:return
            ordered=sorted(overlap,key=lambda token:(-weight(token),-len(token),token))
            line=_around_anchor(line,ordered,remaining)
        if line:
            selected[index]=line;used+=cost+len(line.encode('utf-8'))
    for _,index,overlap in matches[:12]:admit(index,overlap)
    for _,index,_ in matches[:6]:
        admit(index-1);admit(index+1)
    # This copy is for relevance ranking only. Gaps are explicit, and the
    # storage layer continues to return independently verified original ranges.
    return _GAP.join(selected[index] for index in sorted(selected))

def prepare_ranking(query,documents,*,max_documents=64,max_query_bytes=3000,
                    max_document_bytes=3500,max_weighted_bytes=100000):
    """Distribute one bounded request across as much of the recall pool as fits."""
    ranking_query=query_copy(query,max_query_bytes)
    query_cost=len(ranking_query.encode('utf-8'))
    count=min(max_documents,len(documents))
    # Ensure each admitted document has useful space, rather than selecting
    # the first few full 3500-byte prefixes and leaving the rest unranked.
    while count and max_weighted_bytes//count-query_cost<512:count-=1
    if count<2:return ranking_query,[]
    budget=min(max_document_bytes,max_weighted_bytes//count-query_cost)
    copies=[document_copy(document,query,budget) for document in documents[:count]]
    assert len(ranking_query.encode('utf-8'))*count+sum(len(doc.encode('utf-8')) for doc in copies)<=max_weighted_bytes
    return ranking_query,copies
