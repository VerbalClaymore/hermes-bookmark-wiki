"""Offline, coordinator-owned learning progress. No dispatch or profile writes.

The event log is append-only; native Kanban remains task authority. File evidence
is revalidated, not copied. Static path guards are not an OS security sandbox.
"""
from __future__ import annotations

from contextlib import contextmanager
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
from urllib.parse import urlsplit

HASH = re.compile(r'[0-9a-f]{64}\Z')
TASK = re.compile(r't_[0-9a-f]{8}\Z')
SOURCE = re.compile(r'x:[0-9]+\Z')
TIME = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\Z')
STATES = {'ready','running','blocked','review','done','triage','todo','scheduled','archived'}
DISPOSITIONS = {'adopted','reference_saved','already_covered','not_useful','blocked'}
INPUT_LIMIT = 1048576
FILE_LIMIT = 67108864
SKILL_LIMIT = 1048576
SKILLS = {'finn': 'profiles/finn/skills/creative/visual-style-research',
          'vidya': 'profiles/vidya/skills/product-video-workflow'}
COLUMNS = {
    'meta': ('key','value'),
    'events': ('seq','event_id','kind','payload_json','recorded_at'),
    'batches': ('batch_id','register_json'),
    'destinations': ('destination_id','target_id','destination','profile','target_json'),
    'batch_destinations': ('batch_id','destination_id'),
    'task_bindings': ('task_id','intent_id','profile','stage','ordinal','body_sha256'),
}
DDL = '''
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL, kind TEXT NOT NULL, payload_json TEXT NOT NULL, recorded_at TEXT NOT NULL);
CREATE TABLE batches(batch_id TEXT PRIMARY KEY REFERENCES events(event_id), register_json TEXT NOT NULL);
CREATE TABLE destinations(destination_id TEXT PRIMARY KEY, target_id TEXT NOT NULL, destination TEXT NOT NULL, profile TEXT, target_json TEXT NOT NULL);
CREATE TABLE batch_destinations(batch_id TEXT REFERENCES batches(batch_id), destination_id TEXT REFERENCES destinations(destination_id), PRIMARY KEY(batch_id,destination_id));
CREATE TABLE task_bindings(task_id TEXT PRIMARY KEY, intent_id TEXT UNIQUE, profile TEXT NOT NULL, stage TEXT NOT NULL, ordinal INTEGER NOT NULL, body_sha256 TEXT NOT NULL);
'''

class LearningError(Exception):
    def __init__(self, code, reason=None):
        self.code = code
        self.reason = reason
        super().__init__(code)

def fail(code='INVALID_ARGUMENT', reason=None):
    raise LearningError(code, reason)

def canonical(obj):
    try:
        return (json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)+'\n').encode('utf-8')
    except (TypeError, ValueError, UnicodeError):
        fail()

def identity(obj):
    return hashlib.sha256(canonical(obj)).hexdigest()

def _pairs(pairs):
    result = {}
    for k, v in pairs:
        if k in result: fail()
        result[k] = v
    return result

def _decode(raw, code='INVALID_ARGUMENT'):
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                          parse_constant=lambda _: fail())
    except (ValueError, UnicodeError, LearningError, RecursionError):
        fail(code, 'IDENTITY_MISMATCH')

def _exact(obj, keys, code='INVALID_ARGUMENT'):
    if type(obj) is not dict or set(obj) != set(keys.split()): fail(code, 'IDENTITY_MISMATCH')
    return obj

def _string(value, maximum=None, code='INVALID_ARGUMENT'):
    if type(value) is not str or not value.strip() or (maximum is not None and len(value)>maximum): fail(code, 'IDENTITY_MISMATCH')
    try: value.encode('utf-8')
    except UnicodeError: fail(code, 'IDENTITY_MISMATCH')
    return value

def _hash(value, code='INVALID_ARGUMENT'):
    if type(value) is not str or not HASH.fullmatch(value): fail(code, 'IDENTITY_MISMATCH')
    return value

def _array(value, low, high, code='INVALID_ARGUMENT'):
    if type(value) is not list or not low <= len(value) <= high: fail(code, 'IDENTITY_MISMATCH')
    return value

def _scope(value, low=1):
    _array(value, low, 20)
    for x in value: _string(x, 256)
    if len(set(value)) != len(value): fail()

def _time(value, receipt=False):
    if type(value) is not str or (not TIME.fullmatch(value) and not (receipt and value.endswith('+00:00'))): fail('INTEGRITY_ERROR', 'IDENTITY_MISMATCH')
    try: parsed = dt.datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError: fail('INTEGRITY_ERROR', 'IDENTITY_MISMATCH')
    if parsed.tzinfo is None or parsed.utcoffset() != dt.timedelta(0): fail('INTEGRITY_ERROR', 'IDENTITY_MISMATCH')
    return parsed

def _within(path, root):
    return path == root or root in path.parents

def _lex(value, absolute=False):
    if not isinstance(value, (str, os.PathLike)): fail()
    p = Path(value)
    if absolute and not p.is_absolute(): fail()
    if '..' in p.parts: fail()
    if not p.is_absolute(): p = Path.cwd()/p
    cursor = Path(p.anchor)
    for part in p.parts[1:]:
        cursor /= part
        if cursor.is_symlink(): fail()
    # Canonical check is deliberately AFTER lexical checks.
    if p != p.resolve(): fail()
    return p

def _regular(path, absent='NOT_FOUND'):
    _lex(path)
    try: mode = path.stat().st_mode
    except FileNotFoundError: fail(absent, 'MISSING_EVIDENCE')
    if not stat.S_ISREG(mode): fail('INVALID_ARGUMENT')

def _read(path, limit=INPUT_LIMIT, absent='NOT_FOUND'):
    _regular(path, absent)
    if path.stat().st_size > limit: fail('INPUT_TOO_LARGE')
    with path.open('rb') as f: data = f.read(limit+1)
    if len(data)>limit: fail('INPUT_TOO_LARGE')
    return data

class Context:
    def __init__(self, vault, require_db=False):
        self.vault = _lex(vault)
        self.root = _lex(self.vault/'state/learning-process-v1')
        self.db = _lex(self.root/'ledger.sqlite3')
        for path in (self.root/'.learning.lock', self.root/'ledger.sqlite3-journal'):
            _lex(path)
            if path.exists(): _regular(path)
        if require_db and not self.db.exists(): fail('NOT_INITIALIZED')
        for path in (self.vault, self.root, self.root/'inputs', self.root/'evidence', self.root/'reports'):
            _lex(path)
            if path.exists() and not path.is_dir(): fail()
        self.roots_path = _lex(self.root/'inputs/roots.json')
        raw = _read(self.roots_path)
        roots = _decode(raw)
        _exact(roots, 'schema_version hermes_home')
        if type(roots['schema_version']) is not int or roots['schema_version']!=1: fail()
        self.home = _lex(roots['hermes_home'], absolute=True)
        if not self.home.is_dir(): fail('NOT_FOUND')
        self.roots_hash = hashlib.sha256(raw).hexdigest()
        self.allowed = [self.vault/'state/link-learning-pilot-v1', self.vault/'state/library-intake-pilot-v1',
                        self.root/'evidence', self.home/'kanban/boards/bookmark-skills/attachments']
        self.skill_roots = {p: self.home/s for p,s in SKILLS.items()}
        self.allowed += list(self.skill_roots.values())
        self.refs = {}
        self.ref_count = 0

    def file(self, obj):
        _exact(obj, 'path sha256')
        _hash(obj['sha256'])
        p = _lex(obj['path'], absolute=True)
        if not any(_within(p, root) and p!=root for root in self.allowed): fail()
        limit = SKILL_LIMIT if any(_within(p,r) for r in self.skill_roots.values()) else FILE_LIMIT
        try: raw = _read(p,limit,absent='INTEGRITY_ERROR')
        except LearningError as e:
            if e.code=='INPUT_TOO_LARGE': raise
            if e.reason=='MISSING_EVIDENCE': fail('INTEGRITY_ERROR','MISSING_EVIDENCE')
            raise
        if hashlib.sha256(raw).hexdigest()!=obj['sha256']: fail('INTEGRITY_ERROR','HASH_MISMATCH')
        self.ref_count += 1
        if self.ref_count > 100: fail('INPUT_TOO_LARGE')
        self.refs[str(p)] = (obj['sha256'],limit)
        return raw

    def revalidate(self):
        if hashlib.sha256(_read(self.roots_path)).hexdigest()!=self.roots_hash: fail('INTEGRITY_ERROR','HASH_MISMATCH')
        for path,(sha,limit) in self.refs.items():
            raw = _read(Path(path),limit,absent='INTEGRITY_ERROR')
            if hashlib.sha256(raw).hexdigest()!=sha: fail('INTEGRITY_ERROR','HASH_MISMATCH')

    def authority(self, obj, required=None):
        _exact(obj, 'record scope')
        if obj['scope'] not in ('historical_import','approved_batch'): fail()
        if required is not None and obj['scope']!=required: fail('INVALID_TRANSITION')
        return self.file(obj['record'])

@contextmanager
def _lock(ctx):
    path = _lex(ctx.root/'.learning.lock')
    if path.exists(): _regular(path)
    with path.open('a+b') as f:
        try: fcntl.flock(f.fileno(), fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: fail('LOCKED')
        try: yield
        finally: fcntl.flock(f.fileno(),fcntl.LOCK_UN)

def _connect(ctx, readonly=True):
    _regular(ctx.db,'NOT_INITIALIZED')
    try:
        c = sqlite3.connect(ctx.db.as_uri()+('?mode=ro' if readonly else '?mode=rw'),uri=True,timeout=0)
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('PRAGMA busy_timeout=0')
        if not readonly: c.execute('PRAGMA synchronous=FULL')
        _schema(c,ctx)
        return c
    except (sqlite3.DatabaseError,LearningError):
        if 'c' in locals(): c.close()
        raise

def _schema(c, ctx):
    version = c.execute('PRAGMA user_version').fetchone()[0]
    if version!=1: fail('UNSUPPORTED_SCHEMA')
    if c.execute('PRAGMA integrity_check').fetchall()!=[('ok',)] or c.execute('PRAGMA foreign_key_check').fetchall(): fail('INTEGRITY_ERROR')
    if c.execute('PRAGMA journal_mode').fetchone()[0]!='delete': fail('INTEGRITY_ERROR')
    tables={x[0] for x in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    if tables!=set(COLUMNS): fail('INTEGRITY_ERROR')
    # Exact columns plus declared PK/FK/NOT NULL/UNIQUE constraints, not table names alone.
    expected=sqlite3.connect(':memory:')
    try:
        expected.executescript(DDL)
        for table,columns in COLUMNS.items():
            if tuple(x[1] for x in c.execute('PRAGMA table_info('+table+')'))!=columns: fail('INTEGRITY_ERROR')
            if c.execute('PRAGMA table_info('+table+')').fetchall()!=expected.execute('PRAGMA table_info('+table+')').fetchall(): fail('INTEGRITY_ERROR')
            if c.execute('PRAGMA foreign_key_list('+table+')').fetchall()!=expected.execute('PRAGMA foreign_key_list('+table+')').fetchall(): fail('INTEGRITY_ERROR')
            actual_indices=[(x[2],x[3],x[4],tuple(z[2] for z in c.execute('PRAGMA index_info('+x[1]+')'))) for x in c.execute('PRAGMA index_list('+table+')')]
            expected_indices=[(x[2],x[3],x[4],tuple(z[2] for z in expected.execute('PRAGMA index_info('+x[1]+')'))) for x in expected.execute('PRAGMA index_list('+table+')')]
            if sorted(actual_indices)!=sorted(expected_indices): fail('INTEGRITY_ERROR')
    finally: expected.close()
    meta=dict(c.execute('SELECT key,value FROM meta'))
    if set(meta)!={'schema_version','roots_sha256','spec_id','spec_version'}: fail('INTEGRITY_ERROR')
    if meta!={'schema_version':'1','roots_sha256':ctx.roots_hash,'spec_id':'BOOKMARK-INTAKE-PHASE2','spec_version':'1.2'}: fail('INTEGRITY_ERROR')

def _history(c):
    events=[]
    for seq,eid,kind,payload,recorded in c.execute('SELECT seq,event_id,kind,payload_json,recorded_at FROM events ORDER BY seq'):
        obj=_decode(payload.encode(),'INTEGRITY_ERROR')
        if canonical(obj).decode()!=payload or identity({'schema_version':1,'kind':kind,'payload':obj})!=eid: fail('INTEGRITY_ERROR')
        if kind not in ('register_batch','dispatch_intent','task_observed','receipt_accepted',
                        'bulk_batch','bulk_route','bulk_intent','bulk_observed','bulk_result','bulk_publish'): fail('INTEGRITY_ERROR')
        _time(recorded)
        events.append({'id':eid,'kind':kind,'payload':obj,'seq':seq})
    # Reconcile materialized identity/binding rows against append-only events.
    batches={}; destinations={}; joins=set(); bindings={}
    for e in events:
        p=e['payload']
        if e['kind']=='register_batch':
            batches[e['id']]=canonical(p).decode()
            for t in p['targets']:
                tid=target_id(t)
                for d in p['destinations']:
                    if d['target_id']==tid:
                        did=destination_id(d); row=(tid,d['destination'],d['profile'],canonical(t).decode())
                        if did in destinations:
                            old=destinations[did]
                            # Unchecked scope/evidence vary by packet; only identity fields are stable.
                            if old[:3]!=row[:3]: fail('INTEGRITY_ERROR')
                        else: destinations[did]=row
                        joins.add((e['id'],did))
        elif e['kind']=='task_observed':
            o=p['observation']; row=(p['intent_id'],o['assignee'],p['stage'],p['ordinal'],o['body_sha256'])
            if o['task_id'] in bindings and bindings[o['task_id']]!=row: fail('INTEGRITY_ERROR')
            bindings[o['task_id']]=row
    if dict(c.execute('SELECT batch_id,register_json FROM batches'))!=batches: fail('INTEGRITY_ERROR')
    if {x[0]:x[1:] for x in c.execute('SELECT * FROM destinations')}!=destinations: fail('INTEGRITY_ERROR')
    if set(c.execute('SELECT * FROM batch_destinations'))!=joins: fail('INTEGRITY_ERROR')
    if {x[0]:x[1:] for x in c.execute('SELECT * FROM task_bindings')}!=bindings: fail('INTEGRITY_ERROR')
    return events

def target_id(t):
    return identity({k:t[k] for k in ('identity_url','revision','scope_read')})

def destination_id(d):
    return identity({k:d[k] for k in ('target_id','destination','profile')})

def _batch(events, batch_id):
    _hash(batch_id)
    match=next((e['payload'] for e in events if e['id']==batch_id and e['kind']=='register_batch'),None)
    if match is None: fail('NOT_FOUND')
    return match

def _destination(batch, did):
    _hash(did)
    d=next((d for d in batch['destinations'] if destination_id(d)==did),None)
    if d is None: fail('INVALID_TRANSITION')
    t=next(t for t in batch['targets'] if target_id(t)==d['target_id'])
    return d,t

def _selected(events,p):
    batch=_batch(events,p['batch_id'])
    ids=_array(p['destination_ids'],1,5)
    for did in ids: _hash(did)
    if ids!=sorted(set(ids)): fail()
    dests=[_destination(batch,d)[0] for d in ids]
    if any(d['destination']!='specialist' for d in dests) or len({d['profile'] for d in dests})!=1: fail('INVALID_TRANSITION')
    if p['stage'] not in ('reading','application') or type(p['ordinal']) is not int or p['ordinal'] not in (0,1): fail()
    return batch,dests[0]['profile']

def _register(ctx,p):
    _exact(p,'authority packet targets links destinations')
    ctx.authority(p['authority'])
    packet_raw=ctx.file(p['packet']); packet=_decode(packet_raw,'INTEGRITY_ERROR')
    _exact(packet,'schema_version adapter_version manifest_sha256 records','INTEGRITY_ERROR')
    if type(packet['schema_version']) is not int or packet['schema_version']!=1 or packet['adapter_version']!='1.0': fail('INTEGRITY_ERROR')
    _hash(packet['manifest_sha256'],'INTEGRITY_ERROR')
    packet_path=Path(p['packet']['path'])
    if packet_path.name!='packet.json' or packet_path.parent.name!=p['packet']['sha256'] or canonical(packet)!=packet_raw: fail('INTEGRITY_ERROR')
    records=_array(packet['records'],1,5,'INTEGRITY_ERROR')
    fieldset='source_id tweet_id current_revision revision_count content metadata evidence_path raw_path context_status integration_status wiki_pages removal_eligible warnings'
    for rec in records:
        _exact(rec,fieldset,'INTEGRITY_ERROR')
        if type(rec['source_id']) is not str or not SOURCE.fullmatch(rec['source_id']): fail('INTEGRITY_ERROR')
        _string(rec['current_revision'],128,'INTEGRITY_ERROR')
    sources=[r['source_id'] for r in records]
    if sources!=sorted(set(sources)): fail('INTEGRITY_ERROR')
    targets=_array(p['targets'],1,5)
    tids=[]
    for t in targets:
        _exact(t,'identity_url revision scope_read scope_unchecked evidence')
        url=_string(t['identity_url'])
        try: parts=urlsplit(url); host=parts.hostname
        except ValueError: fail()
        if parts.scheme!='https' or not host or parts.username is not None or parts.password is not None or any(x.isspace() for x in url): fail()
        _string(t['revision'],128); _scope(t['scope_read']); _scope(t['scope_unchecked'],0)
        for f in _array(t['evidence'],1,100): ctx.file(f)
        tids.append(target_id(t))
    if len(tids)!=len(set(tids)): fail()
    links=_array(p['links'],1,10)
    seen=set()
    for link in links:
        _exact(link,'source_id archive_revision target_id')
        sid=link['source_id']
        if type(sid) is not str or not SOURCE.fullmatch(sid): fail()
        _hash(link['target_id']); _string(link['archive_revision'],128)
        triple=(sid,link['archive_revision'],link['target_id'])
        if triple in seen: fail()
        seen.add(triple)
        if link['target_id'] not in tids or not any(r['source_id']==sid and r['current_revision']==link['archive_revision'] for r in records): fail('INTEGRITY_ERROR')
    if {x['target_id'] for x in links}!=set(tids) or {x['source_id'] for x in links}!=set(sources): fail('INTEGRITY_ERROR')
    dests=_array(p['destinations'],1,10); seen_dest=set()
    for d in dests:
        _exact(d,'target_id destination profile reason'); _hash(d['target_id']); _string(d['reason'],2000)
        if d['target_id'] not in tids: fail('INTEGRITY_ERROR')
        if d['destination']=='specialist':
            if type(d['profile']) is not str or d['profile'] not in SKILLS: fail()
        elif d['destination']=='wiki':
            if d['profile'] is not None: fail()
        else: fail()
        did=destination_id(d)
        if did in seen_dest: fail()
        seen_dest.add(did)
    if {d['target_id'] for d in dests}!=set(tids): fail()
    for sid in sources:
        source_targets={l['target_id'] for l in links if l['source_id']==sid}
        if len({d['profile'] for d in dests if d['target_id'] in source_targets and d['destination']=='specialist'})>2: fail()

def _events_for(events,did,kind=None):
    return [e for e in events if (kind is None or e['kind']==kind) and
            (e['payload'].get('destination_id')==did or did in e['payload'].get('destination_ids',[]))]

def _receipt_event(events,did):
    rows=_events_for(events,did,'receipt_accepted')
    resolved=[e for e in rows if e['payload']['disposition']!='blocked']
    return resolved[-1] if resolved else rows[-1] if rows else None

def _intent(ctx,p,events):
    _exact(p,'batch_id destination_ids stage ordinal authority')
    batch,profile=_selected(events,p)
    ctx.authority(p['authority'],'approved_batch')
    if batch['authority']['scope']!='approved_batch': fail('INVALID_TRANSITION')
    for did in p['destination_ids']:
        accepted=_receipt_event(events,did)
        if accepted:
            # Even drift never authorizes a new learning attempt.
            _receipt(ctx,accepted['payload'],[e for e in events if e['seq']<accepted['seq']])
            if accepted['payload']['disposition']!='blocked': fail('INVALID_TRANSITION')
        history=_events_for(events,did)
        latest_tasks={}
        for entry in history:
            if entry['kind']=='task_observed':
                latest_tasks[entry['payload']['observation']['task_id']]=entry['payload']['observation']['status']
        if any(state not in ('done','blocked') for state in latest_tasks.values()): fail('INVALID_TRANSITION')
        for e in history:
            if e['kind']=='dispatch_intent':
                observations=[x for x in events if x['kind']=='task_observed' and x['payload']['intent_id']==e['id']]
                if not observations or observations[-1]['payload']['observation']['status'] not in ('done','blocked'): fail('INVALID_TRANSITION')
        attempts={(e['payload']['stage'],e['payload']['ordinal']) for e in history if e['kind'] in ('dispatch_intent','task_observed')}
        if (p['stage'],p['ordinal']) in attempts: fail('INVALID_TRANSITION')
        stage_obs=[e for e in history if e['kind']=='task_observed' and e['payload']['stage']==p['stage']]
        if p['ordinal']==1:
            initial=[e for e in stage_obs if e['payload']['ordinal']==0]
            if not initial or initial[-1]['payload']['observation']['status'] not in ('done','blocked'): fail('INVALID_TRANSITION')
        elif any(stage==p['stage'] for stage,ordinal in attempts): fail('INVALID_TRANSITION')
        if p['stage']=='application':
            reading=[e for e in history if e['kind']=='task_observed' and e['payload']['batch_id']==p['batch_id'] and e['payload']['stage']=='reading']
            if not reading or reading[-1]['payload']['observation']['status']!='done': fail('INVALID_TRANSITION')

def _task(ctx,p,events):
    _exact(p,'batch_id intent_id destination_ids stage ordinal observation')
    batch,profile=_selected(events,p)
    obs=p['observation']
    _exact(obs,'task_id board assignee status observed_at body_sha256 evidence')
    if type(obs['task_id']) is not str or not TASK.fullmatch(obs['task_id']): fail()
    if obs['board']!='bookmark-skills' or obs['assignee']!=profile or obs['status'] not in STATES: fail('INTEGRITY_ERROR')
    if any(e['kind']=='bulk_observed' and e['payload']['observation']['task_id']==obs['task_id'] for e in events):
        fail('INTEGRITY_ERROR')
    when=_time(obs['observed_at']); _hash(obs['body_sha256'])
    if p['intent_id'] is None:
        authority=ctx.authority(batch['authority'],'historical_import')
        if obs['task_id'].encode() not in authority: fail('INTEGRITY_ERROR')
    else:
        _hash(p['intent_id'])
        intent=next((e for e in events if e['id']==p['intent_id'] and e['kind']=='dispatch_intent'),None)
        if not intent or any(intent['payload'][k]!=p[k] for k in ('batch_id','destination_ids','stage','ordinal')): fail('INVALID_TRANSITION')
    normalized=_decode(ctx.file(obs['evidence']),'INTEGRITY_ERROR')
    _exact(normalized,'task_id board assignee status observed_at body_sha256 attachments','INTEGRITY_ERROR')
    if any(normalized[k]!=v for k,v in obs.items() if k!='evidence'): fail('INTEGRITY_ERROR')
    for f in _array(normalized['attachments'],0,100): ctx.file(f)
    previous=[e['payload'] for e in events if e['kind']=='task_observed' and e['payload']['observation']['task_id']==obs['task_id']]
    if previous:
        old=previous[-1]
        if any(old[k]!=p[k] for k in ('batch_id','intent_id','destination_ids','stage','ordinal')) or old['observation']['assignee']!=profile or old['observation']['body_sha256']!=obs['body_sha256'] or when<_time(old['observation']['observed_at']): fail('INTEGRITY_ERROR')
    if p['intent_id'] is not None and any(e['kind']=='task_observed' and e['payload']['intent_id']==p['intent_id'] and e['payload']['observation']['task_id']!=obs['task_id'] for e in events): fail('INTEGRITY_ERROR')
    if not previous:
        for did in p['destination_ids']:
            if any(e['kind']=='task_observed' and e['payload']['stage']==p['stage'] and e['payload']['ordinal']==p['ordinal'] for e in _events_for(events,did)):
                fail('INVALID_TRANSITION')

def _legacy_ref(ctx,obj):
    if type(obj) is not dict or not {'path','sha256'} <= set(obj): fail('INTEGRITY_ERROR','IDENTITY_MISMATCH')
    raw=ctx.file({k:obj[k] for k in ('path','sha256')})
    if 'size' in obj and (type(obj['size']) is not int or obj['size']!=len(raw)): fail('INTEGRITY_ERROR','IDENTITY_MISMATCH')
    return raw

def _receipt(ctx,p,events):
    try: return _receipt_inner(ctx,p,events)
    except LearningError as e:
        if e.code=='INVALID_TRANSITION': raise
        if e.code in ('INVALID_ARGUMENT','NOT_FOUND'): fail('INTEGRITY_ERROR',e.reason or 'IDENTITY_MISMATCH')
        raise
    except (KeyError,TypeError,ValueError,IndexError): fail('INTEGRITY_ERROR','IDENTITY_MISMATCH')

def _receipt_inner(ctx,p,events):
    _exact(p,'batch_id destination_id disposition receipt review')
    if type(p['disposition']) is not str or p['disposition'] not in DISPOSITIONS: fail('INTEGRITY_ERROR','IDENTITY_MISMATCH')
    batch=_batch(events,p['batch_id']); d,t=_destination(batch,p['destination_id'])
    if d['destination']!='specialist': fail('INVALID_TRANSITION')
    receipt=_decode(ctx.file(p['receipt']),'INTEGRITY_ERROR')
    if type(receipt) is not dict or type(receipt.get('schema_version')) is not int or receipt['schema_version']!=1 or receipt.get('profile')!=d['profile'] or receipt.get('verification')!='accepted' or receipt.get('verified_by')!='geoffrey': fail('INTEGRITY_ERROR','IDENTITY_MISMATCH')
    _time(receipt.get('verified_at'),receipt=True)
    ids=_array(receipt.get('task_ids'),1,100,'INTEGRITY_ERROR')
    if any(type(x) is not str or not TASK.fullmatch(x) for x in ids) or len(ids)!=len(set(ids)): fail('INTEGRITY_ERROR','IDENTITY_MISMATCH')
    decisions=_array(receipt.get('source_decisions'),1,100,'INTEGRITY_ERROR')
    for dec in decisions:
        if type(dec) is not dict or type(dec.get('source_id')) is not str or not SOURCE.fullmatch(dec['source_id']): fail('INTEGRITY_ERROR','IDENTITY_MISMATCH')
        _string(dec.get('revision'),128,'INTEGRITY_ERROR')
    links={(l['source_id'],l['archive_revision']) for l in batch['links'] if l['target_id']==d['target_id']}
    selected=[dec for dec in decisions if (dec['source_id'],dec['revision']) in links]
    if len(selected)!=1: fail('INTEGRITY_ERROR','IDENTITY_MISMATCH')
    dec=selected[0]; disposition=dec.get('disposition')
    if disposition not in DISPOSITIONS or disposition!=p['disposition']: fail('INTEGRITY_ERROR','IDENTITY_MISMATCH')
    _string(dec.get('reason'),code='INTEGRITY_ERROR')
    lt=dec.get('learning_target',{})
    if lt.get('identity')!=t['identity_url'] or lt.get('revision')!=t['revision'] or dec.get('scope_read')!=t['scope_read']: fail('INTEGRITY_ERROR','IDENTITY_MISMATCH')
    evidence=dec.get('evidence')
    for f in (evidence if type(evidence) is list else [evidence]): _legacy_ref(ctx,f)
    application=receipt.get('application')
    if type(application) is not dict: fail('INTEGRITY_ERROR')
    artifacts=_array(application.get('artifacts'),0,100,'INTEGRITY_ERROR')
    for f in artifacts: _legacy_ref(ctx,f)
    review=_decode(ctx.file(p['review']),'INTEGRITY_ERROR')
    _exact(review,'schema_version receipt_sha256 destination_id source_id verdict reason preimages coverage evidence','INTEGRITY_ERROR')
    if type(review['schema_version']) is not int or review['schema_version']!=1 or review['receipt_sha256']!=p['receipt']['sha256'] or review['destination_id']!=p['destination_id'] or review['source_id']!=dec['source_id'] or review['verdict']!='accepted': fail('INTEGRITY_ERROR','IDENTITY_MISMATCH')
    _string(review['reason'],2000,'INTEGRITY_ERROR')
    preimages={}
    for pre in _array(review['preimages'],0,10,'INTEGRITY_ERROR'):
        _exact(pre,'installed_path file','INTEGRITY_ERROR')
        installed=_lex(pre['installed_path'],absolute=True)
        if not _within(installed,ctx.skill_roots[d['profile']]) or installed==ctx.skill_roots[d['profile']] or str(installed) in preimages: fail('INTEGRITY_ERROR')
        ctx.file(pre['file']); preimages[str(installed)]=pre['file']['sha256']
    for cov in _array(review['coverage'],0,10,'INTEGRITY_ERROR'):
        _exact(cov,'file section trigger','INTEGRITY_ERROR')
        ctx.file(cov['file']); _string(cov['section'],2000,'INTEGRITY_ERROR'); _string(cov['trigger'],2000,'INTEGRITY_ERROR')
    for f in _array(review['evidence'],1,20,'INTEGRITY_ERROR'): ctx.file(f)
    obs={}
    for e in _events_for(events,p['destination_id'],'task_observed'):
        o=e['payload']['observation']
        if o['task_id'] in ids:
            # Revalidate each applicable retained observation and attachments.
            raw=_decode(ctx.file(o['evidence']),'INTEGRITY_ERROR')
            if any(raw.get(k)!=v for k,v in o.items() if k!='evidence'): fail('INTEGRITY_ERROR','IDENTITY_MISMATCH')
            for f in raw['attachments']: ctx.file(f)
            obs[o['task_id']]=e['payload']
    if set(obs)!=set(ids) or any(o['observation']['assignee']!=d['profile'] for o in obs.values()): fail('INTEGRITY_ERROR','IDENTITY_MISMATCH')
    reading=[o for o in obs.values() if o['stage']=='reading']
    if batch['authority']['scope']=='approved_batch':
        permitted=('blocked','triage') if disposition=='blocked' else ('done',)
        if not reading or not any(o['observation']['status'] in permitted for o in reading): fail('INTEGRITY_ERROR')
    if disposition=='adopted':
        changes=_array(receipt.get('changes'),1,10,'INTEGRITY_ERROR')
        app_id=application.get('task_id'); correction=application.get('correction_task_id')
        if application.get('status')!='passed' or not artifacts or not reading or not any(o['observation']['status']=='done' for o in reading): fail('INTEGRITY_ERROR')
        for task,ordinal in ((app_id,0),(correction,1)):
            if task is None and ordinal==1: continue
            if task not in obs or obs[task]['stage']!='application' or obs[task]['ordinal']!=ordinal or obs[task]['observation']['status']!='done': fail('INTEGRITY_ERROR')
        if any(o['observation']['status']!='done' for o in obs.values()): fail('INTEGRITY_ERROR')
        seen=set()
        for change in changes:
            if type(change) is not dict or not {'path','action','before_sha256','after_sha256'}<=set(change): fail('INTEGRITY_ERROR')
            path=_lex(change['path'],absolute=True)
            if not _within(path,ctx.skill_roots[d['profile']]) or path==ctx.skill_roots[d['profile']] or str(path) in seen: fail('INTEGRITY_ERROR')
            seen.add(str(path)); _hash(change['after_sha256'],'INTEGRITY_ERROR')
            ctx.file({'path':str(path),'sha256':change['after_sha256']})
            if change['action']=='create':
                if change['before_sha256'] is not None: fail('INTEGRITY_ERROR')
            elif change['action']=='modify':
                _hash(change['before_sha256'],'INTEGRITY_ERROR')
                if preimages.get(str(path))!=change['before_sha256']: fail('INTEGRITY_ERROR')
            else: fail('INTEGRITY_ERROR')
    elif disposition in ('already_covered','reference_saved') and not review['coverage']: fail('INTEGRITY_ERROR')
    ctx.authority(batch['authority'])
    # The captured packet/target provenance remains required evidence on every reuse.
    ctx.file(batch['packet'])
    for f in t['evidence']: ctx.file(f)
    return disposition

def _validate(ctx,kind,p,events):
    {'register_batch':_register,'dispatch_intent':_intent,'task_observed':_task,'receipt_accepted':_receipt}[kind](ctx,p,*(() if kind=='register_batch' else (events,)))

def _insert(c,eid,kind,p,recorded):
    c.execute('INSERT INTO events(event_id,kind,payload_json,recorded_at) VALUES(?,?,?,?)',(eid,kind,canonical(p).decode(),recorded))
    if kind=='register_batch':
        c.execute('INSERT INTO batches VALUES(?,?)',(eid,canonical(p).decode()))
        for d in p['destinations']:
            t=next(t for t in p['targets'] if target_id(t)==d['target_id'])
            did=destination_id(d)
            c.execute('INSERT OR IGNORE INTO destinations VALUES(?,?,?,?,?)',(did,d['target_id'],d['destination'],d['profile'],canonical(t).decode()))
            c.execute('INSERT INTO batch_destinations VALUES(?,?)',(eid,did))
    elif kind=='task_observed':
        o=p['observation']
        c.execute('INSERT OR IGNORE INTO task_bindings VALUES(?,?,?,?,?,?)',(o['task_id'],p['intent_id'],o['assignee'],p['stage'],p['ordinal'],o['body_sha256']))

def _boundary(fn):
    def invoke(*args,**kwargs):
        try: return fn(*args,**kwargs)
        except LearningError: raise
        except sqlite3.OperationalError as e:
            if getattr(e,'sqlite_errorcode',None) in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED): fail('LOCKED')
            fail('INTEGRITY_ERROR')
        except (sqlite3.DatabaseError,KeyError,TypeError,ValueError,IndexError,RecursionError): fail('INTEGRITY_ERROR')
        except OSError: fail('IO_ERROR')
    return invoke

def _fault(callback,point):
    if callback is not None: callback(point)

def _fsync_dir(path):
    fd=os.open(path,os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)

@_boundary
def init(vault, *, fault=None):
    ctx=Context(vault)
    if ctx.db.exists():
        # Reject an invalid existing database before creating a new lock file.
        c=_connect(ctx)
        try: _history(c)
        finally: c.close()
        with _lock(ctx):
            c=_connect(ctx)
            try: _history(c)
            finally: c.close()
        return {'action':'init','schema_version':1,'created':False}
    with _lock(ctx):
        if ctx.db.exists():
            c=_connect(ctx)
            try: _history(c)
            finally: c.close()
            return {'action':'init','schema_version':1,'created':False}
        fd,name=tempfile.mkstemp(prefix='.ledger-stage-',suffix='.sqlite3',dir=ctx.root); os.close(fd)
        stage=_lex(name)
        c=sqlite3.connect(stage,timeout=0)
        try:
            c.execute('PRAGMA journal_mode=DELETE'); c.execute('PRAGMA synchronous=FULL'); c.execute('PRAGMA foreign_keys=ON'); c.execute('PRAGMA busy_timeout=0')
            c.executescript(DDL)
            c.executemany('INSERT INTO meta VALUES(?,?)',{'schema_version':'1','roots_sha256':ctx.roots_hash,'spec_id':'BOOKMARK-INTAKE-PHASE2','spec_version':'1.2'}.items())
            c.execute('PRAGMA user_version=1'); c.commit(); _schema(c,ctx)
        finally: c.close()
        ctx.revalidate()
        with stage.open('rb') as f: os.fsync(f.fileno())
        _fault(fault,'init_before_rename')
        os.rename(stage,ctx.db); _fsync_dir(ctx.root)
    return {'action':'init','schema_version':1,'created':True}

@_boundary
def apply(vault, input_file, *, clock=None, fault=None):
    # Input path rejection precedes any write, including lock creation.
    vault_path=_lex(vault); input_path=_lex(input_file)
    if not _within(input_path,vault_path/'state/learning-process-v1/inputs') or input_path==vault_path/'state/learning-process-v1/inputs': fail()
    raw=_read(input_path)
    obj=_decode(raw); _exact(obj,'schema_version kind payload')
    if type(obj['schema_version']) is not int or obj['schema_version']!=1 or type(obj['kind']) is not str or obj['kind'] not in ('register_batch','dispatch_intent','task_observed','receipt_accepted'): fail()
    eid=identity(obj); ctx=Context(vault,require_db=True)
    c=_connect(ctx)
    try:
        events=_history(c); existing=next((e for e in events if e['id']==eid),None)
        if existing and (existing['kind']!=obj['kind'] or existing['payload']!=obj['payload']): fail('INTEGRITY_ERROR')
        # Revalidate original guards against their historical prefix. A later
        # observation/correction must not make an exact earlier event unreplayable.
        validation_events=[e for e in events if e['seq']<existing['seq']] if existing else events
        _validate(ctx,obj['kind'],obj['payload'],validation_events)
        if existing:
            ctx.revalidate()
            return {'action':'apply','event_id':eid,'applied':False}
    finally: c.close()
    with _lock(ctx):
        c=_connect(ctx,False)
        try:
            c.execute('BEGIN IMMEDIATE')
            events=_history(c)
            if any(e['id']==eid for e in events):
                c.rollback()
                return apply(vault,input_file,clock=clock,fault=fault)
            # Re-check history-dependent guards after acquiring the writer lock.
            ctx.ref_count=0; _validate(ctx,obj['kind'],obj['payload'],events)
            now=(clock or (lambda:dt.datetime.now(dt.timezone.utc)))()
            if not isinstance(now,dt.datetime) or now.tzinfo is None: fail()
            recorded=now.astimezone(dt.timezone.utc).isoformat().replace('+00:00','Z')
            _time(recorded)
            _insert(c,eid,obj['kind'],obj['payload'],recorded)
            _fault(fault,'before_revalidate'); ctx.revalidate()
            if hashlib.sha256(_read(input_path)).hexdigest()!=hashlib.sha256(raw).hexdigest(): fail('INTEGRITY_ERROR','HASH_MISMATCH')
            _fault(fault,'before_commit'); c.commit(); _fault(fault,'after_commit')
        except BaseException:
            c.rollback(); raise
        finally: c.close()
    return {'action':'apply','event_id':eid,'applied':True}

def _recorded(events,did,destination):
    receipt=_receipt_event(events,did)
    if receipt:
        # Only the validated append-only event defines the historical decision.
        # External evidence bytes are read by check, never by recorded status.
        disposition=receipt['payload']['disposition']
        if disposition not in DISPOSITIONS: fail('INTEGRITY_ERROR')
        if disposition!='blocked': return 'declined' if disposition=='not_useful' else 'accepted'
        later=[e for e in _events_for(events,did) if e['seq']>receipt['seq'] and e['kind'] in ('dispatch_intent','task_observed')]
        if not later: return 'blocked'
    entries=_events_for(events,did)
    if entries:
        latest=entries[-1]
        if latest['kind']=='dispatch_intent': return 'dispatch_uncertain'
        if latest['kind']=='task_observed':
            s=latest['payload']['observation']['status']
            return 'blocked' if s in ('blocked','triage') else 'awaiting_review' if s=='done' else 'awaiting_result'
    return 'deferred' if destination=='wiki' else 'pending'

@_boundary
def status(vault):
    ctx=Context(vault,require_db=True); c=_connect(ctx)
    try:
        events=_history(c); rows=c.execute('SELECT destination_id,target_id,destination,profile FROM destinations ORDER BY destination_id').fetchall()
        if len(rows)>100: fail('INPUT_TOO_LARGE')
        dests=[]
        for did,tid,dest,profile in rows:
            observations=_events_for(events,did,'task_observed')
            latest=observations[-1]['payload']['observation'] if observations else None
            dests.append({'destination_id':did,'target_id':tid,'destination':dest,'profile':profile,'recorded_state':_recorded(events,did,dest),
                          'observed_task_status':latest['status'] if latest else None,'task_observed_at':latest['observed_at'] if latest else None})
        return {'action':'status','schema_version':1,'batches':len([e for e in events if e['kind']=='register_batch']),'destinations':dests}
    finally: c.close()

@_boundary
def check(vault,batch_id):
    _hash(batch_id)
    ctx=Context(vault,require_db=True); c=_connect(ctx)
    try:
        events=_history(c); batch=_batch(events,batch_id); results=[]
        for d in batch['destinations']:
            did=destination_id(d); recorded=_recorded(events,did,d['destination']); effective=recorded
            reason='WIKI_DEFERRED' if d['destination']=='wiki' else 'NO_ACCEPTED_RECEIPT'
            accepted=_receipt_event(events,did)
            if accepted:
                try:
                    ctx.ref_count=0; ctx.refs={}
                    disposition=_receipt(ctx,accepted['payload'],[e for e in events if e['seq']<accepted['seq']])
                    ctx.authority(batch['authority']); ctx.file(batch['packet'])
                    for t in batch['targets']:
                        if target_id(t)==d['target_id']:
                            for f in t['evidence']: ctx.file(f)
                    ctx.revalidate()
                    if disposition!='blocked': effective='known_noop'; reason='INTACT_RECEIPT'
                except LearningError as e:
                    effective='needs_reconciliation'; reason=e.reason or 'IDENTITY_MISMATCH'
                    if reason not in ('MISSING_EVIDENCE','HASH_MISMATCH','IDENTITY_MISMATCH'): reason='IDENTITY_MISMATCH'
            results.append({'destination_id':did,'recorded_state':recorded,'effective_state':effective,'reason_code':reason})
        results.sort(key=lambda x:x['destination_id'])
        return {'action':'check','batch_id':batch_id,'results':results,'locally_complete':all(r['effective_state']=='known_noop' for r in results)}
    finally: c.close()
