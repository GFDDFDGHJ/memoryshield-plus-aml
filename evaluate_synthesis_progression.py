"""Fixed invented multi-domain progression audit; no provider calls.

The same thirty substantive statements appear either as short messages or
inside longer messages. Only the document layout changes. Report source
selection separately from visible evidence in full Search and bounded prefixes;
this is not an answer-generation score or an official evaluation fixture.
"""
from __future__ import annotations

import argparse
import builtins
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import sys
import tempfile
from unittest.mock import patch


TRAJECTORIES = {
    'work': (
        'At first I preferred the field role because I liked moving between workshops.',
        'I accepted the Harbor field posting for its weekly site visits.',
        'I withdrew from the Harbor posting when the ferry service was reduced.',
        'I now choose the remote documentation role over field postings.',
        'The remote choice lets me collect my nephew before the childcare centre closes.',
    ),
    'travel': (
        'I used to choose overnight coaches to save the cost of a hotel.',
        'I booked the Ridge overnight coach for the spring trip.',
        'I cancelled the Ridge coach after two nights of severe motion sickness.',
        'I now take the daytime rail connection even though the ticket costs more.',
        'Arriving rested matters more than the earlier saving.',
    ),
    'wellbeing': (
        'I initially liked group fitness classes because the fixed timetable kept me active.',
        'I joined the Elm evening exercise group twice each week.',
        'I left the Elm group when the instructor stopped offering quiet beginner sessions.',
        'I now exercise alone in the small courtyard before breakfast.',
        'The quiet courtyard routine lets me stop immediately when my shoulder hurts.',
    ),
    'learning': (
        'I first preferred classroom lessons because questions could be answered immediately.',
        'I enrolled in the Alder classroom language course.',
        'I withdrew from the Alder classroom course after the last bus was discontinued.',
        'I now study with recorded lessons and a short weekly tutor call.',
        'Recorded lessons let me pause around caring duties without losing my place.',
    ),
    'craft': (
        'I once preferred wood carving because I enjoyed shaping solid materials.',
        'I reserved a place in the Cedar carving workshop.',
        'I gave up the Cedar carving workshop when my landlord prohibited noisy equipment.',
        'I now make small hand-built clay bowls at the kitchen table.',
        'Hand-built bowls need no powered tools and fit the space in my rented room.',
    ),
    'caregiving': (
        'I originally preferred weekend visits because they left workdays uninterrupted.',
        'I arranged to visit my aunt on Saturdays with the Birch support service.',
        'I cancelled the Saturday Birch arrangement after the service stopped serving her neighbourhood.',
        'I now reserve Wednesday afternoons to visit my aunt myself.',
        'Wednesday is when the neighbour can look after my nephew while I accompany my aunt.',
    ),
}
STAGES = ('initial_preference', 'accepted_plan', 'withdrawal', 'current_choice', 'stated_reason')
PADDING = ('The administrative appendix described paper folders, empty rooms, inventory shelves and copying arrangements. '
           'The clerical discussion covered filing conventions, seating labels, stationery cupboards and routine room cleaning. ')


def letters(index):
    return chr(65+index//26)+chr(65+index%26)


def fixture():
    cases = []
    for layout in ('standalone', 'embedded'):
        records, claims = [], []
        def background(index):
            return dict(session='administration-'+letters(index%13), role='user',
                        text='Filing note '+letters(index)+'. '+PADDING*12)
        for index in range(12):
            records.append(background(index))
        for stage_index, stage in enumerate(STAGES):
            for domain_index, (domain, trajectory) in enumerate(TRAJECTORIES.items()):
                text = trajectory[stage_index]
                if layout == 'embedded':
                    position = (2, 7, 13, 22, 34)[stage_index]
                    sentences = [sentence.strip()+'.' for sentence in PADDING.split('. ') if sentence.strip().rstrip('.')]
                    pad = [sentences[index%len(sentences)].rstrip('.')+'.' for index in range(40)]
                    pad.insert(position, text)
                    content = ' '.join(pad)
                else:
                    content = text
                record_index = len(records)
                records.append(dict(session=domain, role='user', text=content))
                claims.append(dict(domain=domain, stage=stage, text=text, record_index=record_index))
                serial = stage_index*len(TRAJECTORIES)+domain_index
                for offset in range(4):
                    records.append(background(12+serial*4+offset))
        for index in range(8):
            records.append(background(132+index))
        records.append(dict(session='work', role='assistant', text='I now prefer travelling instructor posts and I can relocate whenever needed.'))
        cases.append(dict(layout=layout, records=records, claims=claims))
    queries = [
        dict(case='explicit_trajectory_control', query='Summarize the turning points across all sessions, including choices that were later reversed.', domains=list(TRAJECTORIES)),
        dict(case='handover_story', query='I am preparing a handover to someone who has never met me. Write the account they need to understand where I started, what I changed, and why.', domains=list(TRAJECTORIES)),
        dict(case='next_commitment', query='Before I make another commitment, help me make sense of the reversals in my choices over the last few months.', domains=list(TRAJECTORIES)),
        dict(case='annual_review', query='For my annual review, distill the thread running through my decisions and keep the context behind each change of direction.', domains=list(TRAJECTORIES)),
        dict(case='chinese_account', query='我准备把这些经历讲给新同事听：哪些打算后来作废了，新的选择各有什么缘由？', domains=list(TRAJECTORIES)),
        dict(case='work_progression', query='Summarize the Harbor field role and remote documentation choices, keeping the original preference, withdrawal and later reason.', domains=['work']),
    ]
    return dict(layouts=cases, queries=queries)


def metrics(text, claims):
    present = [claim for claim in claims if claim['text'] in text]
    by_domain = {domain:[claim['stage'] for claim in present if claim['domain']==domain]
                 for domain in dict.fromkeys(claim['domain'] for claim in claims)}
    by_stage = {stage:sum(claim['stage']==stage for claim in present) for stage in STAGES}
    return dict(present=len(present), required=len(claims), by_stage=by_stage, by_domain=by_domain,
                complete_domains=[domain for domain, stages in by_domain.items() if len(stages)==len(STAGES)],
                missing=[claim['domain']+'/'+claim['stage'] for claim in claims if claim['text'] not in text],
                repeated_claim_occurrences=sum(max(0,text.count(claim['text'])-1) for claim in present))


def run(source_root):
    data = fixture()
    digest = hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',',':')).encode()).hexdigest()
    original_open, original_connect = builtins.open, sqlite3.connect
    def safe_open(file, *args, **kwargs):
        if isinstance(file, (str, bytes, os.PathLike)) and any(part.casefold()=='.secrets' for part in Path(os.fsdecode(file)).parts):
            raise FileNotFoundError('secrets disabled in synthetic progression audit')
        return original_open(file, *args, **kwargs)
    def no_network(*args, **kwargs):
        raise RuntimeError('network disabled in synthetic progression audit')
    with tempfile.TemporaryDirectory(prefix='aml-progression-') as directory:
        temporary = Path(directory).resolve()
        def safe_connect(database, *args, **kwargs):
            if str(database)!=':memory:' and not Path(database).resolve().is_relative_to(temporary):
                raise RuntimeError('database outside synthetic temporary directory')
            return original_connect(database, *args, **kwargs)
        env = dict(AML_DB_PATH=str(temporary/'memory.db'), EMBEDDING_API_KEY='offline-fake',
                   AML_AUTH_TOKEN='offline-fake', EMBEDDING_DIM='4', EMBEDDING_MODEL='offline-controlled',
                   EMBEDDING_MODE='standard', EMBEDDING_BASE_URL='https://offline.invalid/v1',
                   AML_GOVERNANCE='false', AML_QUARANTINE='false', AML_RERANK_ENABLED='false',
                   AML_EXTENDED_CONTEXT_CHARS='160000', AML_CONTEXT_CONTINUATION_CHARS='128000')
        with patch.dict(os.environ,env), patch('builtins.open',safe_open), patch('sqlite3.connect',safe_connect), \
             patch('socket.create_connection',no_network), patch.object(socket.socket,'connect',no_network), \
             patch.object(socket.socket,'connect_ex',no_network):
            sys.path.insert(0,str(source_root))
            S = importlib.import_module('store')
            for name in ('store','facts','config'):
                if Path(sys.modules[name].__file__).resolve().parent != source_root:
                    raise RuntimeError('wrong implementation imported: '+name)
            class Offline(S.MemoryStore):
                def embed(self,texts): return [[1.,0.,0.,0.] for _ in texts]
            memory=Offline()
            try:
                report=dict(fixture_sha256=digest,source_root=str(source_root),
                            implementation_sha256={name:hashlib.sha256((source_root/name).read_bytes()).hexdigest() for name in ('facts.py','store.py','query_plan.py')},
                            safety=dict(database='new temporary directory only',network='blocked',secrets='blocked',provider_calls=0),
                            meaning='Source evidence visibility, not answer synthesis or an official score.',
                            embedding='constant controlled 4D vectors hold ranking fixed; no semantic embedding or reranker evaluation',scenarios=[])
                for layout in data['layouts']:
                    user='invented-'+layout['layout']
                    for index,record in enumerate(layout['records']):
                        memory.add_messages(user,record['session'],[dict(role=record['role'],content=record['text'],timestamp=1750000000000+index*3600000)])
                    connection=memory._connect()
                    try:
                        sources=[dict(row) for row in connection.execute("SELECT * FROM items WHERE user_id=? AND status='active' ORDER BY seq,rid",(user,))]
                    finally:
                        connection.close()
                    for query in data['queries']:
                        claims=[claim for claim in layout['claims'] if claim['domain'] in query['domains']]
                        rows=memory.search(user,query['query'],100)
                        rendered='\n\n'.join('[result '+str(row['id'])+']\n'+row['content'] for row in rows)
                        selected=S.fact_layer.context_units_for_query(sources,query['query'])
                        selected_text='\n'.join(unit['text'] for unit in selected)
                        scope,topics,reason=S.fact_layer.source_context_scope(sources,query['query'])
                        item=dict(layout=layout['layout'],case=query['case'],query=query['query'],source_scope=scope,
                                  source_reason=reason,topics=sorted(topics),input_records=len(sources),
                                  input_characters=sum(len(row['content']) for row in sources),
                                  selected_segments=len(selected),selection=metrics(selected_text,claims),
                                  returned_results=len(rows),returned_characters=len(rendered),
                                  continuation_count=sum(str(row['id']).startswith('continuation_') for row in rows),windows={})
                        for name,view in [('8000',rendered[:8000]),('16000',rendered[:16000]),('48000',rendered[:48000]),('full',rendered)]:
                            item['windows'][name]=metrics(view,claims)
                        item['selected_but_absent_from_full']=[claim['domain']+'/'+claim['stage'] for claim in claims if claim['text'] in selected_text and claim['text'] not in rendered]
                        item['padding_sentence_occurrences']=sum(rendered.count(sentence.strip()+'.') for sentence in PADDING.split('. ') if sentence.strip().rstrip('.'))
                        item['full_sha256']=hashlib.sha256(rendered.encode()).hexdigest()
                        item['content_chars_by_layer']={kind:sum(len(row['content']) for row in rows if str(row['id']).startswith(kind+'_')) for kind in ('digest','continuation','mem','fact','profile','rule','change')}
                        report['scenarios'].append(item)
                return report
            finally:
                memory._pool.shutdown(wait=True)
                memory._client.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root',type=Path,default=Path(__file__).resolve().parent)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--export-fixtures',type=Path)
    args=parser.parse_args()
    if args.export_fixtures:
        args.export_fixtures.write_text(json.dumps(fixture(),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        return
    report=run(args.source_root.resolve())
    if args.output:
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(fixture_sha256=report['fixture_sha256'],cases=[dict(layout=item['layout'],case=item['case'],scope=item['source_scope'],
        selected=str(item['selection']['present'])+'/'+str(item['selection']['required']),
        prefix8000=item['windows']['8000']['present'],prefix16000=item['windows']['16000']['present'],
        full=item['windows']['full']['present'],complete_domains=item['windows']['full']['complete_domains'],
        rendered_chars=item['returned_characters']) for item in report['scenarios']]),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
