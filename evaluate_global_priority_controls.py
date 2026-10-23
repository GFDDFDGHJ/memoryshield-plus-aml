"""Fresh independent repeated-observation and standing-policy controls.

Only decoded returned source IDs count measurements. No input row is used to
fill missing output. Synthetic temporary SQLite; no model requests or secrets.
"""
from __future__ import annotations

import argparse
import builtins
import hashlib
import importlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
from unittest.mock import patch


def fixture():
    measurements = [dict(session='chamber-'+str(index%11),text='Chamber pressure was 12.5 kPa after 0.75 seconds at 25.0 C.') for index in range(480)]
    measurements.append(dict(session='chamber-outlier',text='Chamber pressure was 95.0 kPa after 0.75 seconds at 25.0 C.'))
    policy = 'Always state the measurement units and the source attribution in every response.'
    policy_rows = [dict(session='policy-'+str(index),text=policy) for index in range(36)]
    policy_rows += [dict(session='decision-'+str(index),text='The revised agreement for case '+str(index)+' retains the written confirmation after considering the practical constraints.') for index in range(120)]
    policy_rows += [dict(session='office-'+str(index),text='Office note '+str(index)+': '+'The administrative review covered filing folders, seating labels, stationery cupboards and routine room cleaning. '*16) for index in range(100)]
    return dict(measurements=measurements,policy=policy,policy_rows=policy_rows,
                query='Summarize all my conversations across all sessions.')


def run(source_root):
    data=fixture()
    original_open,original_connect=builtins.open,sqlite3.connect
    def safe_open(file,*args,**kwargs):
        if isinstance(file,(str,bytes,os.PathLike)) and any(part.casefold()=='.secrets' for part in Path(os.fsdecode(file)).parts):
            raise FileNotFoundError('secrets disabled')
        return original_open(file,*args,**kwargs)
    def no_network(*args,**kwargs): raise RuntimeError('network disabled')
    with tempfile.TemporaryDirectory(prefix='aml-global-priority-') as directory:
        temporary=Path(directory).resolve()
        def safe_connect(database,*args,**kwargs):
            if str(database)!=':memory:' and not Path(database).resolve().is_relative_to(temporary):
                raise RuntimeError('database outside synthetic temporary directory')
            return original_connect(database,*args,**kwargs)
        env=dict(AML_DB_PATH=str(temporary/'memory.db'),EMBEDDING_API_KEY='offline-fake',AML_AUTH_TOKEN='offline-fake',
                 EMBEDDING_DIM='4',EMBEDDING_MODEL='offline-controlled',EMBEDDING_MODE='standard',
                 EMBEDDING_BASE_URL='https://offline.invalid/v1',AML_GOVERNANCE='false',AML_QUARANTINE='false',
                 AML_RERANK_ENABLED='false',AML_EXTENDED_CONTEXT_CHARS='160000',AML_CONTEXT_CONTINUATION_CHARS='128000')
        with patch.dict(os.environ,env),patch('builtins.open',safe_open),patch('sqlite3.connect',safe_connect), \
             patch('socket.create_connection',no_network),patch.object(socket.socket,'connect',no_network), \
             patch.object(socket.socket,'connect_ex',no_network):
            sys.path.insert(0,str(source_root))
            S=importlib.import_module('store')
            if Path(S.__file__).resolve().parent!=source_root: raise RuntimeError('wrong source')
            # Read the fixed public decoder; it decodes only declarations and
            # row strings visible in the returned representation.
            sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
            E=importlib.import_module('evaluate_capabilities')
            class Offline(S.MemoryStore):
                def embed(self,texts):return [[1.,0.,0.,0.] for _ in texts]
            memory=Offline()
            try:
                report=dict(fixture_sha256=hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
                            implementation_sha256={name:hashlib.sha256((source_root/name).read_bytes()).hexdigest() for name in ('facts.py','store.py')},
                            safety=dict(database='fresh temporary directory',network='blocked',secrets='blocked',provider_calls=0),cases=[])
                for user,records in [('measurements',data['measurements']),('policies',data['policy_rows'])]:
                    for index,record in enumerate(records):
                        memory.add_messages(user,record['session'],[dict(role='user',content=record['text'],timestamp=1750000000000+index*60000)])
                    rows=memory.search(user,data['query'],100)
                    text=E._render(rows)
                    if user=='measurements':
                        visible={}
                        for rid,content in E._decoded_sources(text,complete=True):
                            if content in {record['text'] for record in records}:
                                visible[rid]=content
                        common=sum(value==records[0]['text'] for value in visible.values())
                        exceptional=sum(value==records[-1]['text'] for value in visible.values())
                        passed=(len(visible)==481 and common==480 and exceptional==1)
                        result=dict(case='repeated_numeric_observations',passed=passed,expected=481,
                                    visible_distinct_source_ids=len(visible),common_value_observations=common,
                                    outlier_observations=exceptional,returned_chars=len(text))
                    else:
                        result=dict(case='common_global_response_policy',passed=data['policy'] in text,
                                    policy_in_prefix8000=data['policy'] in text[:8000],
                                    policy_in_prefix16000=data['policy'] in text[:16000],
                                    policy_in_full=data['policy'] in text,returned_chars=len(text))
                    report['cases'].append(result)
                return report
            finally:
                memory._pool.shutdown(wait=True);memory._client.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root',type=Path,required=True)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    report=run(args.source_root.resolve())
    if args.output:args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
