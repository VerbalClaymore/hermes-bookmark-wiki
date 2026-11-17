"""Resumable orchestration for the independent bookmark lane."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import urlsplit

from . import bird as bird_module, capture as capture_module, config as config_module
from . import deliver, llm as llm_module, report, resolve, store as store_module, triage, write


def default_deps(config):
    """Construct the production adapters; callers may override any entry in deps."""
    model = llm_module.LLM(config)
    return {'bird': bird_module.Bird(config['bird']), 'llm': model,
            'fetch': resolve.make_fetch(config), 'vision': model.vision,
            'bot_runner': subprocess.run, 'clock': store_module.utc_now,
            'which': shutil.which, 'run': subprocess.run}


def _deps(config, overrides):
    result = default_deps(config)
    result.update(overrides or {})
    if overrides and 'llm' in overrides and 'vision' not in overrides:
        result['vision'] = result['llm'].vision
    return result


def init(vault):
    return {'config': str(config_module.init(vault))}


def _source_id(source_id):
    if not isinstance(source_id, str) or not source_id.startswith('x:'):
        raise ValueError('invalid source id')
    store_module.tweet_id(source_id[2:])
    return source_id[2:]


def _row(store, source_id):
    _source_id(source_id)
    row = store.db.execute('SELECT * FROM items WHERE source_id = ?', (source_id,)).fetchone()
    if row is None:
        raise ValueError('source not found')
    return dict(row)


def _update(store, source_id, **values):
    values['updated_at'] = store.clock().isoformat()
    sql = ', '.join(key + ' = ?' for key in values)
    store.db.execute('UPDATE items SET ' + sql + ' WHERE source_id = ?', (*values.values(), source_id))
    store.db.commit()


def _artifact(store, ident, stage, context):
    path = store.artifact(ident, stage)
    if not path.exists():
        return None
    obj = store.read_artifact(ident, stage)
    if stage == 'capture':
        if obj.get('source_id') != 'x:' + ident or obj.get('tweet_id') != ident or not isinstance(obj.get('tweets'), list) or not obj['tweets']:
            raise ValueError('invalid capture artifact')
    elif stage == 'resolve':
        if (obj.get('source_id') != 'x:' + ident or not isinstance(obj.get('post'), dict)
                or not all(isinstance(obj.get(k), list) for k in ('thread', 'links', 'media', 'limits'))
                or not isinstance(obj.get('signals'), dict)):
            raise ValueError('invalid resolve artifact')
    elif stage == 'triage':
        triage.validate(obj, {p['id'] for p in context['roster']})
    elif stage == 'write':
        write.validate(obj, context['resolve'])
    elif stage == 'deliver':
        if set(obj) != {'wiki', 'bots'} or not isinstance(obj['bots'], list) or not (obj['wiki'] is None or isinstance(obj['wiki'], dict)):
            raise ValueError('invalid delivery artifact')
    return obj


def _evidence(bundle):
    links = [x for x in bundle.get('links', [])
             if x.get('status') == 'ok' and x.get('final_url')]
    keys = {'url:' + links[0]['final_url']} if links else set()
    for link in links:
        parsed = urlsplit(link['final_url'])
        parts = parsed.path.strip('/').split('/')
        if link.get('kind') == 'github_repo' and (parsed.hostname or '').lower() == 'github.com' and len(parts) >= 2:
            keys.add('github:' + '/'.join(parts[:2]).lower().removesuffix('.git'))
    return keys


def _covered_by(store, bundle):
    keys = _evidence(bundle)
    if not keys:
        return None
    for row in store.db.execute("SELECT tweet_id FROM items WHERE status = 'done' ORDER BY updated_at DESC"):
        try:
            other = store.read_artifact(row['tweet_id'], 'resolve')
            if keys & _evidence(other):
                return store.read_artifact(row['tweet_id'], 'write')['title']
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None


def _error(exc, stage):
    message = ' '.join(str(exc).split())[:400] or 'operation failed'
    if re.match(r'^[A-Z][A-Z_]+: ', message):
        return message
    code = {'capture': 'BIRD_FAILED', 'resolve': 'RESOLVE_FAILED', 'triage': 'LLM_FAILED',
            'write': 'LLM_FAILED', 'deliver': 'DELIVER_FAILED'}.get(stage, 'ITEM_FAILED')
    return code + ': ' + message


def _timestamp(value):
    if not isinstance(value, str) or not value:
        return 0.0
    try:
        try:
            date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return date.timestamp()
    except (ValueError, TypeError, OverflowError):
        return 0.0


def sync(vault, *, limit=None, capture_count=None, capture=True, bot_runs=True, dry_run=False, deps=None):
    vault = Path(vault).absolute()
    cfg = config_module.load(vault)
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError('invalid limit')
    if capture_count is not None:
        if type(capture_count) is not int or not 1 <= capture_count <= 800:
            raise ValueError('capture_count must be between 1 and 800')
        cfg['limits']['capture_count'] = capture_count
    maximum = min(limit or cfg['limits']['items_per_sync'], cfg['limits']['items_per_sync'])
    d = _deps(cfg, deps)
    decode_count = getattr(d['bird'], 'decode_repaired_count', 0)
    state = store_module.Store(vault, clock=d['clock'])
    try:
        with state.locked():
            capture_metadata = {}
            captured = capture_module.capture(vault, state, cfg, d['bird'], metadata=capture_metadata) if capture else []
            rows = [dict(row) for row in state.db.execute(
                "SELECT * FROM items WHERE status IN ('pending', 'failed') AND attempts < ?",
                (cfg['limits']['max_attempts'],))]
            def newest(row):
                try:
                    obj = state.read_artifact(row['tweet_id'], 'capture')
                    dates = [_timestamp(t.get('createdAt')) for t in obj.get('tweets', []) if isinstance(t, dict)]
                    return (max(dates, default=0.0) or _timestamp(row['created_at']), int(row['tweet_id']))
                except (OSError, ValueError, TypeError):
                    return (_timestamp(row['created_at']), int(row['tweet_id']))
            rows.sort(key=newest, reverse=True)
            entries = []
            for row in rows[:maximum]:
                ident, source_id = row['tweet_id'], row['source_id']
                entry = {'source_id': source_id}
                ctx = {}
                stage = 'capture'
                try:
                    ctx['roster'] = triage.roster(cfg)
                    for stage in store_module.STAGES:
                        if stage == 'deliver' and dry_run:
                            break
                        print(f'{source_id} {stage} started', file=sys.stderr, flush=True)
                        try:
                            obj = _artifact(state, ident, stage, ctx)
                        except (OSError, ValueError, KeyError, TypeError):
                            obj = None
                        if obj is None:
                            index = store_module.STAGES.index(stage)
                            for later in store_module.STAGES[index:]:
                                state.artifact(ident, later).unlink(missing_ok=True)
                            _update(state, source_id, stage=store_module.STAGES[index - 1] if index else None)
                            if stage == 'capture':
                                tweet = d['bird'].json('read', [ident, '--json'],
                                                       profile=cfg['bird']['read_profile'])
                                if isinstance(tweet, list):
                                    tweet = next((t for t in tweet if isinstance(t, dict) and str(t.get('id')) == ident), None)
                                if not isinstance(tweet, dict) or str(tweet.get('id')) != ident:
                                    raise ValueError('capture refresh returned no matching post')
                                obj = {'source_id': source_id, 'tweet_id': ident,
                                       'captured_at': state.clock().isoformat(), 'tweets': [tweet]}
                            elif stage == 'resolve':
                                obj = resolve.resolve(ctx['capture'], cfg, bird=d['bird'], fetch=d['fetch'],
                                                      vision=d['vision'], media_dir=state.item_dir(ident) / 'media',
                                                      which=d['which'], run=d['run'])
                            elif stage == 'triage':
                                try:
                                    obj = triage.triage(ctx['resolve'], ctx['roster'], deliver.lane_wiki_titles(vault),
                                                        d['llm'], covered_by=_covered_by(state, ctx['resolve']))
                                finally:
                                    store_module.atomic_json(state.artifact(ident, 'resolve'), ctx['resolve'])
                            elif stage == 'write':
                                try:
                                    obj = write.write(ctx['resolve'], ctx['triage'], d['llm'],
                                                      covered_by=_covered_by(state, ctx['resolve']))
                                finally:
                                    store_module.atomic_json(state.artifact(ident, 'resolve'), ctx['resolve'])
                            else:
                                obj = deliver.deliver(state.item_dir(ident), ctx['resolve'], ctx['triage'], ctx['write'],
                                                      cfg, vault, bot_runner=d['bot_runner'], clock=d['clock'], bot_runs=bot_runs)
                            state.write_artifact(ident, stage, obj)
                        ctx[stage] = obj
                        print(f'{source_id} {stage} ok', file=sys.stderr, flush=True)
                        if stage in ('resolve', 'triage', 'write', 'deliver'):
                            entry[{'resolve': 'bundle', 'write': 'note', 'deliver': 'delivery'}.get(stage, stage)] = obj
                        if stage == 'triage':
                            destinations = {'wiki': obj['wiki'], 'bots': [b['profile'] for b in obj['bots']]}
                            _update(state, source_id, value=obj['value'], destinations_json=json.dumps(destinations))
                            if obj['value'] == 'skip':
                                status_value = 'duplicate' if 'duplicat' in obj['skip_reason'].lower() else 'skipped'
                                _update(state, source_id, stage='triage', status=status_value, error=None)
                                entry['status'] = status_value
                                break
                    else:
                        _update(state, source_id, stage='deliver', status='done', error=None)
                        entry['status'] = 'done'
                    if dry_run and 'status' not in entry:
                        _update(state, source_id, stage='write', status='pending', error=None)
                        entry['status'] = 'pending'
                except Exception as exc:
                    error = _error(exc, stage)
                    print(f'{source_id} {stage} failed: {error}', file=sys.stderr, flush=True)
                    _update(state, source_id, status='failed', attempts=row['attempts'] + 1, error=error)
                    entry.update(status='failed', error=error)
                entries.append(entry)
            path = report.append(vault, entries, clock=d['clock'])
            now = state.clock().isoformat()
            state.db.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES ('last_sync',?)", (now,))
            state.db.commit()
            if getattr(d['bird'], 'decode_repaired_count', 0) > decode_count:
                capture_metadata['decode_repaired'] = True
            return {'captured': captured, 'items': entries, 'report': str(path), 'last_sync': now, 'dry_run': dry_run, **capture_metadata}
    finally:
        state.close()


def seed(vault, *, ids=None, from_archive=False, limit=None, newest_first=False, deps=None):
    cfg = config_module.load(vault)
    d = _deps(cfg, deps)
    if bool(ids) == bool(from_archive) or (limit is not None and (type(limit) is not int or limit < 1)):
        raise ValueError('choose ids or archive and a positive limit')
    sources = capture_module.manifest_sources(vault)
    selected = ids.split(',') if isinstance(ids, str) else list(ids or sources)
    for sid in selected:
        _source_id(sid)
        if sid not in sources:
            raise ValueError('source not found in archive')
    def content(sid):
        source = sources[sid]
        return source['revisions'][source['current_revision']]['content']
    if newest_first:
        selected.sort(key=lambda sid: (_timestamp(content(sid).get('tweet_created_at')), int(sid[2:])), reverse=True)
    state = store_module.Store(vault, clock=d['clock'])
    try:
        with state.locked():
            added = []
            for sid in selected:
                if limit is not None and len(added) >= limit:
                    break
                c = content(sid)
                ident = _source_id(sid)
                text = c.get('text') or ''
                urls = c.get('expanded_urls') or []
                text += ''.join(' ' + url for url in urls if isinstance(url, str) and url not in text)
                tweet = {'id': ident, 'text': text, 'createdAt': c.get('tweet_created_at'),
                         'author': {'username': c.get('author_handle') or ''}, 'media': c.get('media') or []}
                record = {'source_id': sid, 'tweet_id': ident, 'captured_at': state.clock().isoformat(),
                          'tweets': [tweet], 'needs_refresh': True}
                if state.put_capture(record):
                    added.append(sid)
            return {'seeded': added}
    finally:
        state.close()


def status(vault):
    config_module.load(vault)
    state = store_module.Store(vault)
    try:
        statuses = {r[0]: r[1] for r in state.db.execute('SELECT status, count(*) FROM items GROUP BY status')}
        stages = {r[0]: r[1] for r in state.db.execute('SELECT stage, count(*) FROM items GROUP BY stage')}
        failed = [dict(r) for r in state.db.execute("SELECT source_id, attempts, error FROM items WHERE status='failed' ORDER BY updated_at DESC")]
        last = state.db.execute("SELECT value FROM metadata WHERE key='last_sync'").fetchone()
        return {'statuses': statuses, 'stages': stages, 'failed': failed, 'last_sync': last[0] if last else None}
    finally:
        state.close()


def show(vault, source_id):
    config_module.load(vault)
    state = store_module.Store(vault)
    try:
        row = _row(state, source_id)
        artifacts = {}
        for stage in store_module.STAGES:
            if state.artifact(row['tweet_id'], stage).exists():
                artifacts[stage] = state.read_artifact(row['tweet_id'], stage)
        return {'item': row, 'artifacts': artifacts}
    finally:
        state.close()


def retry(vault, source_id, *, from_stage='capture'):
    config_module.load(vault)
    if from_stage not in store_module.STAGES:
        raise ValueError('invalid stage')
    state = store_module.Store(vault)
    try:
        with state.locked():
            row = _row(state, source_id)
            index = store_module.STAGES.index(from_stage)
            for stage in store_module.STAGES[index:]:
                state.artifact(row['tweet_id'], stage).unlink(missing_ok=True)
            previous = store_module.STAGES[index - 1] if index else None
            values = {'stage': previous, 'status': 'pending', 'attempts': 0, 'error': None}
            if index <= store_module.STAGES.index('triage'):
                values.update(value=None, destinations_json=None)
            _update(state, source_id, **values)
            return {'source_id': source_id, 'from_stage': from_stage}
    finally:
        state.close()
