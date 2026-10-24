"""Isolated Coding memory: literal messages, scoped vectors and code excerpts.

No text-track modules, inference, model configuration or global source index are
used here. Providers are injected; source windows retain original offsets.
"""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import struct
import threading
import time
from typing import Callable

try:
    from .code_windows import code_tokens, code_windows, render_code_windows
except ImportError:  # uvicorn --app-dir coding imports modules at this level.
    from code_windows import code_tokens, code_windows, render_code_windows


class AddRequestConflict(ValueError):
    """A user's request ID already identifies different original messages."""


class InvalidAddInput(ValueError):
    """An Add cannot be stored without altering or losing its literal input."""


_LOCK_GUARD = threading.Lock()
_REQUEST_LOCKS: dict[tuple[str, str, str], tuple[threading.RLock, int]] = {}
_QUERY_STOP = frozenset('a an the and or is are was were to of for in on at with '
    'what which where how why show find locate explain please me my this that '
    'from about can could do does did use using code file function implementation'.split())


@contextmanager
def _request_lock(key):
    """Coalesce same-process retries, without serializing unrelated providers."""
    if key is None:
        yield
        return
    with _LOCK_GUARD:
        lock, count = _REQUEST_LOCKS.get(key, (threading.RLock(), 0))
        _REQUEST_LOCKS[key] = (lock, count + 1)
    try:
        with lock:
            yield
    finally:
        with _LOCK_GUARD:
            _, count = _REQUEST_LOCKS[key]
            if count == 1:
                del _REQUEST_LOCKS[key]
            else:
                _REQUEST_LOCKS[key] = (lock, count - 1)


def _identifier(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 1024:
        raise InvalidAddInput(f'{label} must be a nonempty string of at most 1024 characters')
    return value


def _iso(seconds):
    return (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds)).isoformat().replace('+00:00', 'Z')


class MemoryStore:
    """One isolated SQLite store; embeddings and ranking adapters take lists.

    ``embed(texts)`` returns one finite vector of ``dim`` values per text.
    Optional ``rerank(query, documents)`` returns a complete index permutation;
    invalid results or failures leave deterministic local fusion in place.
    """

    def __init__(self, db_path, *, embed: Callable, dim: int = 1024,
                 rerank: Callable | None = None):
        if type(dim) is not int or not 1 <= dim <= 65536:
            raise ValueError('dim must be a positive bounded integer')
        if not callable(embed) or rerank is not None and not callable(rerank):
            raise TypeError('embedding and optional reranking adapters must be callable')
        self.db_path = str(Path(db_path).resolve())
        self.dim, self.embed, self.rerank = dim, embed, rerank
        self._pack_format = '<' + 'f' * dim
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA busy_timeout=30000')
        try:
            yield connection
        finally:
            connection.close()

    def _ensure_schema(self):
        with self._connect() as connection:
            existing = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if existing and 'coding_meta' not in existing:
                raise ValueError('the database belongs to another application')
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('BEGIN IMMEDIATE')
            try:
                tables = {row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                if tables and 'coding_meta' not in tables:
                    raise ValueError('the database belongs to another application')
                if 'coding_meta' in tables:
                    metadata = dict(connection.execute('SELECT key,value FROM coding_meta'))
                    if metadata.get('store_kind') != 'coding-literal-v1' or metadata.get('dimension') != str(self.dim):
                        raise ValueError('incompatible Coding database format or vector dimension')
                connection.execute('CREATE TABLE IF NOT EXISTS coding_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
                connection.executemany('INSERT OR IGNORE INTO coding_meta(key,value) VALUES(?,?)',
                    [('store_kind','coding-literal-v1'),('dimension',str(self.dim))])
                connection.execute('''CREATE TABLE IF NOT EXISTS coding_items(
                    rid INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
                    message_json TEXT NOT NULL, timestamp_ms REAL, created_at REAL NOT NULL,
                    timestamp_origin TEXT NOT NULL, seq INTEGER NOT NULL,
                    embedding BLOB NOT NULL, norm REAL NOT NULL, UNIQUE(user_id,seq))''')
                connection.execute('CREATE INDEX IF NOT EXISTS coding_items_user ON coding_items(user_id,seq)')
                connection.execute('''CREATE TABLE IF NOT EXISTS coding_add_requests(
                    user_id TEXT NOT NULL, request_id TEXT NOT NULL, session_id TEXT NOT NULL,
                    payload_hash TEXT NOT NULL, written INTEGER NOT NULL,
                    PRIMARY KEY(user_id,request_id))''')
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def _vectors(self, texts):
        values = self.embed(texts)
        if not isinstance(values, (list, tuple)) or len(values) != len(texts):
            raise ValueError('embedding adapter returned an incorrect vector count')
        output = []
        for vector in values:
            if len(vector) != self.dim:
                raise ValueError('embedding adapter returned an incorrect dimension')
            converted = [float(value) for value in vector]
            if not all(math.isfinite(value) for value in converted):
                raise ValueError('embedding adapter returned nonfinite values')
            try:
                packed = struct.pack(self._pack_format, *converted)
            except (OverflowError, struct.error) as error:
                raise ValueError('embedding adapter returned unrepresentable values') from error
            # Norms and dot products use the same persisted float32 values.
            exact = struct.unpack(self._pack_format, packed)
            output.append((packed, math.sqrt(math.fsum(value*value for value in exact))))
        return output

    @staticmethod
    def _replayed(connection, user, request, session, payload_hash):
        row = connection.execute('SELECT session_id,payload_hash,written FROM coding_add_requests '
                                 'WHERE user_id=? AND request_id=?', (user,request)).fetchone()
        if row is None:
            return None
        if row['session_id'] != session or row['payload_hash'] != payload_hash:
            raise AddRequestConflict('request_id already identifies different messages or session')
        return row['written']

    def add_messages(self, user_id, session_id, messages, request_id=None):
        user_id = _identifier(user_id, 'user_id')
        session_id = _identifier(session_id, 'session_id')
        if request_id is not None:
            request_id = _identifier(request_id, 'request_id')
        if not isinstance(messages, list):
            raise InvalidAddInput('messages must be a list')
        now = time.time()
        prepared = []
        for message in messages:
            if not isinstance(message, dict) or not isinstance(message.get('content'), str):
                raise InvalidAddInput('every message content must be an original string')
            role = _identifier(message.get('role', 'user'), 'role')
            stamp = message.get('timestamp')
            if stamp is not None and (isinstance(stamp, bool) or not isinstance(stamp, (int,float)) or not math.isfinite(stamp)):
                raise InvalidAddInput('timestamp must be finite Unix milliseconds')
            recorded = now if stamp is None else float(stamp)/1000
            try:
                _iso(recorded)
                original = json.dumps(message,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
            except (OverflowError, TypeError, ValueError) as error:
                raise InvalidAddInput('message has invalid timestamp or non-JSON fields') from error
            prepared.append((role,message['content'],original,stamp,recorded,
                             'ingestion-clock' if stamp is None else 'message.timestamp'))
        try:
            payload = json.dumps(messages,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
        except (TypeError,ValueError) as error:
            raise InvalidAddInput('messages must contain JSON values') from error
        payload_hash = hashlib.sha256(payload.encode('utf-8')).hexdigest()
        key = (self.db_path.casefold(),user_id,request_id) if request_id is not None else None
        with _request_lock(key):
            if request_id is not None:
                with self._connect() as connection:
                    replayed = self._replayed(connection,user_id,request_id,session_id,payload_hash)
                if replayed is not None:
                    return replayed
            actual = [index for index,item in enumerate(prepared) if item[1].strip()]
            vectors = self._vectors([prepared[index][1] for index in actual]) if actual else []
            by_index = dict(zip(actual,vectors))
            zero = struct.pack(self._pack_format, *([0.0]*self.dim))
            with self._connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                try:
                    if request_id is not None:
                        replayed = self._replayed(connection,user_id,request_id,session_id,payload_hash)
                        if replayed is not None:
                            connection.rollback()
                            return replayed
                    seq = connection.execute('SELECT COALESCE(MAX(seq),0)+1 FROM coding_items WHERE user_id=?', (user_id,)).fetchone()[0]
                    for index,item in enumerate(prepared):
                        role,content,original,stamp,recorded,origin = item
                        vector,norm = by_index.get(index,(zero,0.0))
                        connection.execute('''INSERT INTO coding_items(user_id,session_id,role,content,message_json,
                            timestamp_ms,created_at,timestamp_origin,seq,embedding,norm) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                            (user_id,session_id,role,content,original,stamp,recorded,origin,seq+index,vector,norm))
                    if request_id is not None:
                        connection.execute('INSERT INTO coding_add_requests(user_id,request_id,session_id,payload_hash,written) '
                            'VALUES(?,?,?,?,?)',(user_id,request_id,session_id,payload_hash,len(messages)))
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
        return len(messages)

    def search(self, user_id, query, top_k):
        user_id = _identifier(user_id, 'user_id')
        if not isinstance(query,str) or not query.strip():
            return []
        if type(top_k) is not int or top_k <= 0:
            return []
        top_k = min(top_k,100)
        with self._connect() as connection:
            rows = [dict(row) for row in connection.execute('SELECT * FROM coding_items '
                    "WHERE user_id=? AND content<>'' ORDER BY seq,rid", (user_id,))]
        if not rows:
            return []
        packed,qnorm = self._vectors([query])[0]
        qvector = struct.unpack(self._pack_format,packed)
        terms = {token for token in code_tokens(query) if token.casefold() not in _QUERY_STOP}
        weights = {token:(3.0 if any(char in token for char in '_./\\:-') or any(char.isdigit() for char in token)
                          or any(char.isupper() for char in token) else 1.0) for token in terms}
        source_tokens = [code_tokens(row['content']) for row in rows]
        frequencies = Counter(token for tokens in source_tokens for token in terms if token in tokens)
        lexical = []
        for index,(row,tokens) in enumerate(zip(rows,source_tokens)):
            vector = struct.unpack(self._pack_format,row['embedding'])
            row['cosine'] = (math.fsum(a*b for a,b in zip(qvector,vector))/(qnorm*row['norm'])
                             if qnorm and row['norm'] else 0.0)
            score = math.fsum(weights[token]*(1+math.log((len(rows)+1)/(frequencies[token]+1)))
                              for token in terms & tokens)
            row['lexical'] = score
            if score:lexical.append(index)
        dense = sorted(range(len(rows)),key=lambda i:(-rows[i]['cosine'],rows[i]['seq'],rows[i]['rid']))
        lexical.sort(key=lambda i:(-rows[i]['lexical'],-rows[i]['cosine'],rows[i]['seq'],rows[i]['rid']))
        scores = {index:1/(60+rank) for rank,index in enumerate(dense,1)}
        for rank,index in enumerate(lexical,1):scores[index] += 1/(60+rank)
        ranked = sorted(range(len(rows)),key=lambda i:(-scores[i],-rows[i]['lexical'],-rows[i]['cosine'],rows[i]['seq'],rows[i]['rid']))
        if self.rerank is not None and len(ranked)>1:
            pool = ranked[:64]
            documents = [f"[SOURCE role={json.dumps(rows[index]['role'])} session={json.dumps(rows[index]['session_id'])}]\n"
                +render_code_windows(rows[index]['content'],code_windows(rows[index]['content'],query,max_chars=5000)) for index in pool]
            try:
                order = self.rerank(query,documents)
                if (isinstance(order,(list,tuple)) and len(order)==len(pool)
                        and all(type(index) is int for index in order) and set(order)==set(range(len(pool)))):
                    ranked = [pool[index] for index in order]+ranked[len(pool):]
            except Exception:
                pass  # Ranking is optional; literal local evidence remains available.
        output = []
        for rank,index in enumerate(ranked[:top_k],1):
            row = rows[index]
            windows = code_windows(row['content'],query,max_chars=6000)
            label = (f"[SOURCE source_rid={row['rid']} session={json.dumps(row['session_id'],ensure_ascii=False)} "
                     f"seq={row['seq']} role={json.dumps(row['role'],ensure_ascii=False)} "
                     f"recorded_at={_iso(row['created_at'])} timestamp_origin={row['timestamp_origin']}; "
                     'recorded time is source provenance, not a code-version precedence claim]')
            output.append(dict(id=f"code_mem_{row['rid']}",content=label+'\n'+render_code_windows(row['content'],windows),
                score=round(1/(60+rank),8),cosine_similarity=round(row['cosine'],8),
                created_at=_iso(row['created_at']),source_rid=row['rid'],session_id=row['session_id'],
                seq=row['seq'],role=row['role'],timestamp_origin=row['timestamp_origin'],
                source_char_ranges=[[window['start'],window['end']] for window in windows],
                source_line_ranges=[[window['start_line'],window['end_line']] for window in windows],
                source_chars=len(row['content']),excerpt=sum(len(window['text']) for window in windows)<len(row['content'])))
        return output
