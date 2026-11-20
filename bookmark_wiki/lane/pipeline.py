"""Resumable orchestration for the independent bookmark lane."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
from types import SimpleNamespace


from . import bird as bird_module, capture as capture_module, config as config_module
from . import deliver, keepsake, ledger, llm as llm_module, report, resolve, store as store_module, triage, write


def default_deps(config):
    """Construct the production adapters; callers may override any entry in deps."""
    model = llm_module.LLM(config)
    return {'bird': bird_module.Bird(config['bird']), 'llm': model,
            'fetch': resolve.make_fetch(config), 'vision': model.vision,
            'bot_runner': subprocess.run, 'clock': store_module.utc_now,
            'which': shutil.which, 'run': subprocess.run, 'keepsake': keepsake.keep}


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
        triage.validate(obj, {p['id'] for p in context['roster']}, has_media=bool(context['resolve'].get('media')))
        if obj.get('novelty') == 'duplicate':
            earlier = store.db.execute("SELECT 1 FROM items WHERE source_id=? AND status='done'", (obj['duplicate_of'],)).fetchone()
            if earlier:
                triage.validate(obj, {p['id'] for p in context['roster']}, has_media=bool(context['resolve'].get('media')),
                                prior=context['prior'], source_id=context['resolve']['source_id'])
    elif stage == 'write':
        write.validate(obj, context['resolve'])
    elif stage == 'deliver':
        if (set(obj) not in ({'wiki', 'bots'}, {'wiki', 'bots', 'keepsake'}, {'wiki', 'bots', 'duplicate_of'})
                or not isinstance(obj['bots'], list) or not (obj['wiki'] is None or isinstance(obj['wiki'], dict))
                or ('duplicate_of' in obj and (obj['wiki'] is not None or obj['bots'] or not isinstance(obj['duplicate_of'], str) or not obj['duplicate_of']))
                or ('keepsake' in obj and (obj['wiki'] is not None or obj['bots'] or not isinstance(obj['keepsake'], dict)
                    or set(obj['keepsake']) != {'files', 'index'} or not isinstance(obj['keepsake']['files'], list)
                    or not all(isinstance(p, str) for p in obj['keepsake']['files']) or not isinstance(obj['keepsake']['index'], str)))):
            raise ValueError('invalid delivery artifact')
    return obj


def _preserve_delivery(state, ident):
    path = state.artifact(ident, 'deliver')
    previous = state.item_dir(ident) / 'delivery.previous.json'
    if path.exists():
        try:
            delivery = state.read_artifact(ident, 'deliver')
        except (OSError, ValueError):
            return
        store_module.atomic_json(previous, delivery)


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


def sync(vault, *, limit=None, capture_count=None, capture=True, bot_runs=True, dry_run=False, catch_up=False, newer_than=None, deps=None):
    cutoff = None
    if newer_than is not None:
        if not isinstance(newer_than, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', newer_than):
            raise ValueError('newer_than must be a valid YYYY-MM-DD date')
        try:
            cutoff = datetime.combine(date.fromisoformat(newer_than), datetime.min.time(), tzinfo=timezone.utc).timestamp()
        except ValueError as exc:
            raise ValueError('newer_than must be a valid YYYY-MM-DD date') from exc
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
            ledger.ensure(state)
            capture_metadata = {}
            captured = capture_module.capture(vault, state, cfg, d['bird'], metadata=capture_metadata,
                                              catch_up=catch_up, capture_count=capture_count) if capture else []
            rows = [dict(row) for row in state.db.execute(
                "SELECT * FROM items WHERE status IN ('pending', 'failed') AND attempts < ?",
                (cfg['limits']['max_attempts'],))]
            def post_date(row):
                try:
                    obj = state.read_artifact(row['tweet_id'], 'capture')
                    post = next((t for t in obj.get('tweets', []) if isinstance(t, dict)
                                 and str(t.get('id')) == row['tweet_id']), None)
                    if post is not None:
                        return _timestamp(post.get('createdAt'))
                    dates = (_timestamp(t.get('createdAt')) for t in obj.get('tweets', []) if isinstance(t, dict))
                    return min((timestamp for timestamp in dates if timestamp), default=0.0)
                except (OSError, ValueError, TypeError):
                    return 0.0
            dated = [(row, post_date(row)) for row in rows]
            if cutoff is not None:
                dated = [(row, timestamp) for row, timestamp in dated if timestamp and timestamp >= cutoff]
            dated.sort(key=lambda pair: (pair[1] or _timestamp(pair[0]['created_at']), int(pair[0]['tweet_id'])), reverse=True)
            rows = [row for row, _ in dated]
            entries = []
            for row in rows[:maximum]:
                ident, source_id = row['tweet_id'], row['source_id']
                entry = {'source_id': source_id}
                ctx = {}
                stage = 'capture'
                try:
                    ctx['roster'] = triage.roster(cfg)
                    for stage in store_module.STAGES:
                        if stage == 'write' and ctx['triage']['value'] == 'keepsake':
                            continue
                        if stage == 'deliver' and dry_run:
                            break
                        print(f'{source_id} {stage} started', file=sys.stderr, flush=True)
                        try:
                            obj = _artifact(state, ident, stage, ctx)
                        except (OSError, ValueError, KeyError, TypeError):
                            obj = None
                        if obj is None:
                            index = store_module.STAGES.index(stage)
                            _preserve_delivery(state, ident)
                            for later in store_module.STAGES[index:]:
                                state.artifact(ident, later).unlink(missing_ok=True)
                            previous = store_module.STAGES[index - 1] if index else None
                            if stage == 'deliver' and ctx['triage']['value'] == 'keepsake':
                                previous = 'triage'
                            _update(state, source_id, stage=previous)
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
                                                        d['llm'], prior=ctx['prior'])
                                finally:
                                    store_module.atomic_json(state.artifact(ident, 'resolve'), ctx['resolve'])
                            elif stage == 'write':
                                try:
                                    earlier_note = None
                                    if ctx['triage'].get('novelty') == 'partial':
                                        earlier_id = ctx['triage']['duplicate_of']
                                        earlier_row = _row(state, earlier_id)
                                        artifact = state.read_artifact(earlier_row['tweet_id'], 'write')
                                        earlier_note = {'source_id': earlier_id, **{key: artifact[key] for key in ('title', 'short_answer', 'key_points')}}
                                    obj = write.write(ctx['resolve'], ctx['triage'], d['llm'], prior=ctx['prior'], earlier_note=earlier_note)
                                finally:
                                    store_module.atomic_json(state.artifact(ident, 'resolve'), ctx['resolve'])
                            else:
                                if ctx['triage']['value'] == 'keepsake':
                                    saved = d['keepsake'](state.item_dir(ident), ctx['capture'], ctx['resolve'], ctx['triage'],
                                                          cfg, vault, fetch=d['fetch'], run=d['run'], which=d['which'], clock=d['clock'])
                                    obj = {'wiki': None, 'bots': [], 'keepsake': saved}
                                else:
                                    builds_on = None
                                    if ctx['triage'].get('novelty') == 'partial':
                                        earlier_id = ctx['triage']['duplicate_of']
                                        earlier_row = _row(state, earlier_id)
                                        earlier_note = state.read_artifact(earlier_row['tweet_id'], 'write')
                                        try:
                                            earlier_delivery = state.read_artifact(earlier_row['tweet_id'], 'deliver')
                                        except (OSError, ValueError):
                                            earlier_delivery = {}
                                        builds_on = {'source_id': earlier_id, 'title': earlier_note['title'],
                                                     'wiki_path': (earlier_delivery.get('wiki') or {}).get('path'),
                                                     'references': {(b['profile'], b['topic_slug']): b['reference_path']
                                                                    for b in earlier_delivery.get('bots', [])}}
                                    previous_path = state.item_dir(ident) / 'delivery.previous.json'
                                    previous_delivery = json.loads(previous_path.read_text(encoding='utf-8')) if previous_path.exists() else None
                                    obj = deliver.deliver(state.item_dir(ident), ctx['resolve'], ctx['triage'], ctx['write'],
                                                          cfg, vault, bot_runner=d['bot_runner'], clock=d['clock'], bot_runs=bot_runs,
                                                          builds_on=builds_on, sources=lambda profile, topic: ledger.topic_sources(state, profile, topic),
                                                          previous=previous_delivery)
                            state.write_artifact(ident, stage, obj)
                            if stage == 'deliver':
                                (state.item_dir(ident) / 'delivery.previous.json').unlink(missing_ok=True)
                        ctx[stage] = obj
                        if stage == 'resolve':
                            ctx['prior'] = {'coverage': ledger.prior_coverage(state, obj), 'recent': ledger.recent_notes(state)}
                            entry['prior'] = ctx['prior']
                        print(f'{source_id} {stage} ok', file=sys.stderr, flush=True)
                        if stage in ('resolve', 'triage', 'write', 'deliver'):
                            entry[{'resolve': 'bundle', 'write': 'note', 'deliver': 'delivery'}.get(stage, stage)] = obj
                        if stage == 'triage':
                            if obj.get('novelty') == 'duplicate':
                                earlier_id = obj['duplicate_of']
                                earlier = state.db.execute("SELECT * FROM items WHERE source_id=? AND status='done'", (earlier_id,)).fetchone()
                                if earlier is None:
                                    obj = dict(obj, novelty='partial', wiki=True, bots=[])
                                    state.write_artifact(ident, 'triage', obj)
                                    ctx['triage'] = entry['triage'] = obj
                                elif dry_run:
                                    _update(state, source_id, stage='triage', status='pending', error=None)
                                    entry['status'] = 'pending'
                                    break
                                else:
                                    previous_note = state.read_artifact(earlier['tweet_id'], 'write')
                                    previous_delivery = state.read_artifact(earlier['tweet_id'], 'deliver')
                                    wiki_path = (previous_delivery.get('wiki') or {}).get('path')
                                    path = ledger.seen_again(vault, state.item_dir(ident), state, earlier_id, ctx['resolve'], d['clock'])
                                    ledger.record(state, source_id, ctx['resolve'], title=previous_note['title'], takeaway=previous_note['short_answer'], wiki_path=wiki_path, bots=[], note_source_id=earlier_id, clock=d['clock'])
                                    delivery_obj = {'wiki': None, 'bots': [], 'duplicate_of': earlier_id}
                                    state.write_artifact(ident, 'deliver', delivery_obj)
                                    _update(state, source_id, stage='deliver', status='duplicate', value=obj['value'], destinations_json=json.dumps({'wiki': False, 'bots': []}), error=None)
                                    entry.update(status='duplicate', delivery=delivery_obj, seen_again_path=path, earlier_title=previous_note['title'])
                                    break
                            if obj.get('novelty') == 'partial':
                                match = next((n for n in ctx['prior']['coverage']['notes'] + ctx['prior']['recent'] if n['source_id'] == obj['duplicate_of']), None)
                                if match:
                                    entry['earlier_title'] = match['title']
                                else:
                                    try:
                                        earlier_row = _row(state, obj['duplicate_of'])
                                        earlier_note = state.read_artifact(earlier_row['tweet_id'], 'write')
                                        entry['earlier_title'] = earlier_note['title']
                                    except (OSError, ValueError, KeyError, TypeError):
                                        pass
                            destinations = {'wiki': obj['wiki'], 'bots': [b['profile'] for b in obj['bots']]}
                            _update(state, source_id, value=obj['value'], destinations_json=json.dumps(destinations))
                            if obj['value'] == 'skip':
                                status_value = 'duplicate' if 'duplicat' in obj['skip_reason'].lower() else 'skipped'
                                _update(state, source_id, stage='triage', status=status_value, error=None)
                                entry['status'] = status_value
                                break
                    else:
                        final_status = 'kept' if ctx['triage']['value'] == 'keepsake' else 'done'
                        if final_status == 'done':
                            ledger.record(state, source_id, ctx['resolve'], title=ctx['write']['title'], takeaway=ctx['write']['short_answer'], wiki_path=(ctx['deliver'].get('wiki') or {}).get('path'), bots=[{k: b[k] for k in ('profile', 'topic_slug', 'reference_path')} for b in ctx['deliver']['bots']], clock=d['clock'])
                        _update(state, source_id, stage='deliver', status=final_status, error=None)
                        entry['status'] = final_status
                    if dry_run and 'status' not in entry:
                        _update(state, source_id, stage='triage' if ctx['triage']['value'] == 'keepsake' else 'write', status='pending', error=None)
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
            _preserve_delivery(state, row['tweet_id'])
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


def prune_orphans(vault, *, apply=False):
    """Find only superseded files with a current, existing same-destination file."""
    vault = Path(vault).absolute()
    cfg = config_module.load(vault)
    home = Path(cfg['hermes']['home']).absolute()
    state = store_module.Store(vault) if apply else None
    def scan():
        changes = []
        items = deliver._safe_path(vault, 'lane', 'items')
        if not items.is_dir():
            return {'apply': apply, 'changes': changes}
        for item in sorted(items.iterdir()):
            if item.is_symlink() or not item.is_dir() or not re.fullmatch(r'[0-9]+', item.name):
                continue
            artifact = deliver._safe_path(item, 'delivery.json')
            if not artifact.is_file():
                continue
            try:
                current = state.read_artifact(item.name, 'deliver') if state else json.loads(artifact.read_text(encoding='utf-8'))
                if not isinstance(current, dict) or not isinstance(current.get('bots'), list):
                    continue
                old = {'wiki': None, 'bots': []}
                wiki_dir = deliver._safe_path(vault, 'wiki', 'topics')
                wiki = (current.get('wiki') or {}).get('path')
                if isinstance(wiki, str):
                    target = Path(wiki)
                    if (target.parent == wiki_dir and re.fullmatch(r'Bookmark_' + item.name + r'_[a-z0-9-]+\.md', target.name)
                            and target.is_file() and target.resolve() == target.absolute()):
                        for candidate in sorted(wiki_dir.glob('Bookmark_' + item.name + '_*.md')):
                            if candidate != target and not candidate.is_symlink() and candidate.is_file():
                                old['wiki'] = {'path': str(candidate)}
                                changes.extend(deliver.cleanup_delivery(item, vault, home, item.name, old, current, apply=apply))
                                old['wiki'] = None
                valid_bots = []
                for bot in current['bots']:
                    if not isinstance(bot, dict):
                        continue
                    profile, topic = bot.get('profile'), bot.get('topic_slug')
                    if not isinstance(profile, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', profile):
                        continue
                    if not isinstance(topic, str) or not re.fullmatch(r'[a-z0-9-]{3,40}', topic):
                        continue
                    directory = deliver._safe_path(home, 'profiles', profile, 'skills', 'bookmarks', topic, 'references')
                    value = bot.get('reference_path')
                    if not isinstance(value, str):
                        continue
                    target = Path(value)
                    if (target.parent != directory or not re.fullmatch(item.name + r'-[a-z0-9-]+\.md', target.name)
                            or not target.is_file() or target.resolve() != target.absolute()):
                        continue
                    valid_bots.append((profile, topic, target))
                if len(valid_bots) != len(current['bots']) or not valid_bots:
                    continue
                profiles = deliver._safe_path(home, 'profiles')
                for directory in sorted(profiles.glob('*/skills/bookmarks/*/references')):
                    if directory.resolve() != directory.absolute() or not directory.is_dir():
                        continue
                    profile, topic = directory.parents[3].name, directory.parent.name
                    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', profile) or not re.fullmatch(r'[a-z0-9-]{3,40}', topic):
                        continue
                    target = next((p for prof, top, p in valid_bots if (prof, top) == (profile, topic)), None)
                    for candidate in sorted(directory.glob(item.name + '-*.md')):
                        if (candidate != target and not candidate.is_symlink() and candidate.is_file()
                                and re.fullmatch(item.name + r'-[a-z0-9-]+\.md', candidate.name)):
                            old['bots'] = [{'profile': profile, 'topic_slug': topic, 'reference_path': str(candidate)}]
                            changes.extend(deliver.cleanup_delivery(item, vault, home, item.name, old, current,
                                           sources=topic_sources, apply=apply))
                            old['bots'] = []
            except (OSError, ValueError, TypeError, KeyError):
                if apply:
                    raise
                continue
        return {'apply': apply, 'changes': changes}
    if state is None:
        db_path = vault / 'lane' / 'lane.sqlite3'
        connection = sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True) if db_path.is_file() else None
        if connection:
            connection.row_factory = sqlite3.Row
        topic_sources = (lambda profile, topic: ledger.topic_sources(SimpleNamespace(db=connection), profile, topic)) if connection else None
        try:
            return scan()
        finally:
            if connection:
                connection.close()
    try:
        with state.locked():
            topic_sources = lambda profile, topic: ledger.topic_sources(state, profile, topic)
            return scan()
    finally:
        state.close()
