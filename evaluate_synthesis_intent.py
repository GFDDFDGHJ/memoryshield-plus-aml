"""Fixed invented synthesis-intent contrasts; offline full-Search audit.

This tests evidence visibility and source planning, not generated answers.
Constant vectors intentionally hold ranking fixed across paraphrases. The
unknown-topic controls test scope expansion, not semantic vector quality.
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


ROOT = Path(__file__).resolve().parents[2]
TOPICS = (
    ('work', 'I declined the onsite technical role because ferry cancellations made the commute unreliable.'),
    ('travel', 'I exchanged the mountain journey for a nearby stay because the coach route was repeatedly closed.'),
    ('wellbeing', 'I limited climbing sessions after shoulder pain because recovery required longer rest.'),
    ('study', 'I shifted from evening seminars to recorded lessons because the late bus stopped running.'),
    ('craft', 'I chose pottery over wood carving because my rented room could not hold noisy equipment.'),
    ('caregiving', 'I reserve Wednesday afternoons for my aunt because her companion moved away.'),
    ('botany', 'The herbarium project uses photographs instead of pressed specimens because the leaves are protected.'),
    ('volunteering', 'I moved from weekend food deliveries to telephone coordination because bicycle repairs took longer.'),
)


def fixture():
    records = []
    for index in range(128):
        # The conversation moves between domains over time; preserving only
        # the early raw prefix must not look like cross-domain synthesis.
        topic, choice = TOPICS[index // 16]
        token = 'SYNTH-EVIDENCE-' + chr(65+index//26) + chr(65+index%26)
        records.append(dict(topic=topic, marker=token, session='session-'+str(index%19),
                            text=token+': '+choice+' The arrangement was retained after considering alternatives and the practical limitation. '
                            +'A later review was requested before making another commitment.'))
    private = [
        'My home address is 47 Invented Fern Lane. PRIVATE-ADDRESS-WITNESS.',
        'My storage access code is INVENTED-48271. PRIVATE-CODE-WITNESS.',
    ]
    all_topics = [topic for topic, _ in TOPICS]
    cases = [
        ('explicit_global', 'Summarize all my conversations.', 'overview', all_topics),
        ('explicit_overview', 'Give an overview across all sessions.', 'overview', all_topics),
        ('connect_decisions', "Can you connect the dots between the decisions I've described?", 'overview', all_topics),
        ('reflect_constraints', "Reflect on the reasons behind the choices I've described, from the first exchange to the latest.", 'overview', all_topics),
        ('cross_discussion_priorities', 'What does the combination of our discussions reveal about my priorities?', 'overview', all_topics),
        ('personal_portrait', 'Draw a unified portrait of my choices over the course of our exchanges.', 'overview', all_topics),
        ('recurring_constraints', "Which recurring themes connect what I've shared over time?", 'overview', all_topics),
        ('chinese_connect_choices', '把这些聊天串起来，讲讲我一路以来的取舍和变化。', 'overview', all_topics),
        ('chinese_priorities', '跨领域看，我这些选择背后最看重的是什么？', 'overview', all_topics),
        ('abstract_summary', 'Summarize how practical constraints shaped my life choices.', 'overview', all_topics),
        ('topical_herbarium', 'Summarize the herbarium project decisions.', 'topical', ['botany']),
        ('topical_paraphrase', 'Put together the story of our herbarium project choices.', 'topical', ['botany']),
        ('unknown_specific', 'Summarize all sessions about Zephyr cryogenic turbine maintenance.', 'unknown-topic', []),
        ('unknown_specific_paraphrase', 'Put together the whole story of the Zephyr cryogenic turbine.', 'unknown-topic', []),
        ('privacy_constrained', 'Summarize work and travel choices, omitting home addresses and access codes.', 'privacy-constrained', ['work', 'travel']),
        ('ordinary_botany_fact', 'Why does the herbarium use photographs?', 'ordinary-fact', ['botany']),
    ]
    return dict(records=records, private=private,
                cases=[dict(case=name, query=query, expected_intent=intent, expected_topics=topics)
                       for name, query, intent, topics in cases])


def run(source_root: Path):
    data = fixture()
    encoded = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    original_open, original_connect = builtins.open, sqlite3.connect

    def safe_open(file, *args, **kwargs):
        if isinstance(file, (str, bytes, os.PathLike)) and any(part.casefold() == '.secrets' for part in Path(os.fsdecode(file)).parts):
            raise FileNotFoundError('credential access disabled in synthetic audit')
        return original_open(file, *args, **kwargs)

    def no_network(*args, **kwargs):
        raise RuntimeError('network disabled in synthetic audit')

    with tempfile.TemporaryDirectory(prefix='aml-synthesis-intent-') as directory:
        temporary = Path(directory).resolve()

        def safe_connect(database, *args, **kwargs):
            if str(database) != ':memory:' and not Path(database).resolve().is_relative_to(temporary):
                raise RuntimeError('database outside synthetic temporary directory')
            return original_connect(database, *args, **kwargs)

        env = dict(AML_DB_PATH=str(temporary/'memory.db'), EMBEDDING_API_KEY='offline-fake',
                   AML_AUTH_TOKEN='offline-fake', EMBEDDING_DIM='4', EMBEDDING_MODEL='offline-controlled',
                   EMBEDDING_MODE='standard', EMBEDDING_BASE_URL='https://offline.invalid/v1',
                   AML_GOVERNANCE='false', AML_QUARANTINE='false', AML_EXTENDED_CONTEXT_CHARS='96000',
                   AML_CONTEXT_CONTINUATION_CHARS='64000')
        with patch.dict(os.environ, env), patch('builtins.open', safe_open), patch('sqlite3.connect', safe_connect), \
             patch('socket.create_connection', no_network), patch.object(socket.socket, 'connect', no_network), \
             patch.object(socket.socket, 'connect_ex', no_network):
            sys.path.insert(0, str(source_root))
            S = importlib.import_module('store')
            for name in ('store', 'facts', 'config'):
                if Path(sys.modules[name].__file__).resolve().parent != source_root:
                    raise RuntimeError('wrong implementation imported: '+name)

            class Offline(S.MemoryStore):
                def embed(self, texts):
                    # Public narrative records tie exactly; sensitive controls
                    # receive a far score so raw rescue is reported separately.
                    return [[.1, .9949874371, 0., 0.] if 'PRIVATE-' in text else [1., 0., 0., 0.] for text in texts]

            memory = Offline()
            try:
                for index, record in enumerate(data['records']):
                    memory.add_messages('invented-history', record['session'], [dict(role='user', content=record['text'],
                                                                                   timestamp=1750000000000+index*60000)])
                for index, content in enumerate(data['private'], len(data['records'])):
                    memory.add_messages('invented-history', 'private', [dict(role='user', content=content,
                                                                           timestamp=1750000000000+index*60000)])
                connection = memory._connect()
                try:
                    sources = [dict(row) for row in connection.execute("SELECT * FROM items WHERE user_id='invented-history' AND status='active' ORDER BY seq,rid")]
                finally:
                    connection.close()
                report = dict(fixture_sha256=hashlib.sha256(encoded).hexdigest(), source_root=str(source_root),
                              implementation_sha256={name:hashlib.sha256((source_root/name).read_bytes()).hexdigest()
                                                     for name in ('facts.py', 'store.py')},
                              safety=dict(database='new temporary directory only', network='blocked', secrets='blocked', provider_calls=0),
                              meaning='Evidence visibility and scope audit; no generated answers and no official benchmark data.',
                              embedding='constant controlled 4D vectors; paraphrase embedding quality is not measured',
                              scenarios=[])
                for case in data['cases']:
                    rows = memory.search('invented-history', case['query'], 100)
                    rendered = '\n\n'.join('[result '+str(row['id'])+']\n'+row['content'] for row in rows)
                    expected = {record['marker'] for record in data['records'] if record['topic'] in case['expected_topics']}
                    scope, topics = S.fact_layer.context_scope(case['query'])
                    actual_scope, actual_topics, reason = S.fact_layer.source_context_scope(sources, case['query'])
                    selected = S.fact_layer.context_units_for_query(sources, case['query'])
                    selected_text = '\n'.join(unit['text'] for unit in selected)
                    item = dict(case, syntactic_scope=scope, source_scope=actual_scope, source_reason=reason,
                                remaining_topics=sorted(topics), selected_source_topics=sorted(actual_topics),
                                selected_source_segments=len(selected), result_count=len(rows), returned_characters=len(rendered),
                                continuation_count=sum(str(row['id']).startswith('continuation_') for row in rows),
                                full_sha256=hashlib.sha256(rendered.encode()).hexdigest(), windows={})
                    for name, text in (('8000', rendered[:8000]), ('16000', rendered[:16000]), ('full', rendered)):
                        seen = {record['marker'] for record in data['records'] if record['marker'] in text}
                        item['windows'][name] = dict(expected=len(expected), present=len(expected & seen),
                                                   missing=sorted(expected-seen), unrelated=len(seen-expected),
                                                   distinct_choice_reasons_visible=[topic for topic, choice in TOPICS if choice in text],
                                                   source_markers_by_topic={topic:sum(record['marker'] in seen for record in data['records'] if record['topic']==topic) for topic, _ in TOPICS},
                                                   private_witnesses=re.findall(r'PRIVATE-(?:ADDRESS|CODE)-WITNESS', text))
                    item['selected_private_witnesses'] = re.findall(r'PRIVATE-(?:ADDRESS|CODE)-WITNESS', selected_text)
                    item['scope_control_passed'] = (actual_scope != 'global' and not item['selected_private_witnesses']) if case['expected_intent'] in ('unknown-topic', 'privacy-constrained') else None
                    item['full_required_sources_visible'] = bool(expected) and item['windows']['full']['present'] == len(expected)
                    report['scenarios'].append(item)
                return report
            finally:
                memory._pool.shutdown(wait=True)
                memory._client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--export-fixtures', type=Path)
    args = parser.parse_args()
    if args.export_fixtures:
        args.export_fixtures.write_text(json.dumps(fixture(), ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        return
    report = run(args.source_root.resolve())
    if args.output:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(fixture_sha256=report['fixture_sha256'],
                         cases=[dict(case=item['case'], intent=item['expected_intent'], syntactic_scope=item['syntactic_scope'],
                                     source_scope=item['source_scope'], full_coverage=str(item['windows']['full']['present'])+'/'+str(item['windows']['full']['expected']),
                                     returned_chars=item['returned_characters'], scope_control=item['scope_control_passed'])
                                for item in report['scenarios']]), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
