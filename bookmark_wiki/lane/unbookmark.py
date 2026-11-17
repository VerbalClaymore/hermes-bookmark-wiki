"""Operator-confirmed removal of already-delivered bookmarks."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
import time

from . import config, store
from .bird import Bird


PHRASE = 'DELETE SYNCED BOOKMARKS'


def register(actions):
    parser = actions.add_parser('unbookmark')
    parser.add_argument('--vault', required=True)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--include-skipped', action='store_true')
    return parser


def _entry(state, row):
    ident = store.tweet_id(row['tweet_id'])
    if row['source_id'] != 'x:' + ident:
        raise ValueError('invalid source id')
    title = row['source_id']
    url = 'https://x.com/i/status/' + ident
    for stage in ('write', 'triage', 'resolve'):
        try:
            artifact = state.read_artifact(ident, stage)
            candidate = (artifact.get('title') or (artifact.get('core') or {}).get('title')
                         or (artifact.get('post') or {}).get('text'))
            if isinstance(candidate, str) and candidate.strip():
                title = ' '.join(candidate.split())
                break
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return {'source_id': row['source_id'], 'title': title, 'url': url}


def _file(vault, path):
    if not isinstance(path, str) or not path:
        return False
    path = Path(path)
    try:
        return (path if path.is_absolute() else vault / path).is_file()
    except (OSError, ValueError):
        return False


def _reason(state, vault, row):
    if row['status'] == 'skipped':
        return None
    try:
        delivery = state.read_artifact(row['tweet_id'], 'deliver')
        if row['status'] == 'done':
            wiki = delivery.get('wiki')
            bots = delivery.get('bots')
            if not isinstance(bots, list) or not (wiki is None or isinstance(wiki, dict)):
                return 'invalid delivery artifact'
            paths = ([wiki.get('path')] if wiki else []) + [b.get('reference_path') for b in bots if isinstance(b, dict)]
            if not any(_file(vault, path) for path in paths):
                return 'no delivered wiki note or bot reference exists'
        elif row['status'] == 'kept':
            keepsake = delivery.get('keepsake')
            files = keepsake.get('files') if isinstance(keepsake, dict) else None
            if not isinstance(files, list) or not files or not all(_file(vault, path) for path in files):
                return 'saved keepsake files cannot be verified'
        else:
            source = delivery.get('duplicate_of')
            if not isinstance(source, str) or source == row['source_id']:
                return 'invalid duplicate reference'
            earlier = state.db.execute('SELECT status FROM items WHERE source_id = ?', (source,)).fetchone()
            if earlier is None or earlier['status'] != 'done':
                return 'earlier duplicate item is not done'
    except (OSError, ValueError, TypeError, AttributeError):
        return 'delivery artifact cannot be verified'
    return None


def run(vault, *, limit=None, dry_run=False, include_skipped=False, deps=None):
    deps = deps or {}
    stdin = deps.get('stdin', sys.stdin)
    stderr = deps.get('stderr', sys.stderr)
    isatty = deps.get('isatty', lambda stream: stream.isatty())
    if not dry_run and (not isatty(stdin) or not isatty(stderr)):
        raise ValueError('UNBOOKMARK_REFUSED: confirmation requires an interactive terminal')
    if limit is None:
        limit = 25
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('limit must be between 1 and 100')
    vault = Path(vault).absolute()
    cfg = config.load(vault)
    clock = deps.get('clock', store.utc_now)
    sleep = deps.get('sleep', time.sleep)
    bird = deps.get('bird') or Bird(cfg['bird'])
    state = store.Store(vault, clock=clock)
    try:
        with state.locked():
            state.db.execute('CREATE TABLE IF NOT EXISTS unbookmarked(source_id TEXT PRIMARY KEY, tweet_id TEXT NOT NULL, removed_at TEXT NOT NULL)')
            state.db.commit()
            result = {'dry_run': bool(dry_run), 'eligible': [], 'ineligible': [],
                      'removed': [], 'failed': None, 'log': str(state.root / 'unbookmark-log.jsonl')}
            statuses = ('done', 'kept', 'duplicate', 'skipped') if include_skipped else ('done', 'kept', 'duplicate')
            placeholders = ','.join('?' for _ in statuses)
            rows = state.db.execute('SELECT * FROM items WHERE status IN (' + placeholders + ') '
                                    'AND source_id NOT IN (SELECT source_id FROM unbookmarked) '
                                    'ORDER BY created_at, source_id', statuses).fetchall()
            for row in rows:
                try:
                    entry = _entry(state, row)
                    reason = _reason(state, vault, row)
                except (ValueError, TypeError):
                    entry = {'source_id': row['source_id'], 'title': row['source_id'], 'url': ''}
                    reason = 'invalid source or tweet id'
                if reason:
                    result['ineligible'].append({**entry, 'reason': reason})
                elif len(result['eligible']) < limit:
                    result['eligible'].append(entry)
            if dry_run:
                return result
            profile = cfg['bird']['capture_profile']
            identity = bird.run('whoami', [], profile=profile)
            if re.findall(r'(?m)^\s*user_id:\s*(\d+)\s*$', identity) != [cfg['bird']['capture_user_id']]:
                raise ValueError('UNBOOKMARK_REFUSED: capture account not verified')
            print(f"Remove {len(result['eligible'])} synced bookmarks:", file=stderr)
            for entry in result['eligible']:
                print(f"{entry['title']} {entry['url']}", file=stderr)
            print(f'Type {PHRASE}: ', end='', file=stderr, flush=True)
            answer = stdin.readline()
            if answer.endswith('\n'):
                answer = answer[:-1]
            if answer != PHRASE:
                raise ValueError('UNBOOKMARK_REFUSED: confirmation did not match')
            delay = cfg.get('unbookmark', {}).get('delay_s', 2)
            calls = 0
            for entry in list(result['eligible']):
                row = state.db.execute('SELECT * FROM items WHERE source_id = ?', (entry['source_id'],)).fetchone()
                reason = _reason(state, vault, row) if row is not None else 'item no longer exists'
                if reason:
                    result['eligible'].remove(entry)
                    result['ineligible'].append({**entry, 'reason': reason})
                    continue
                if calls:
                    sleep(delay)
                ident = entry['source_id'][2:]
                try:
                    calls += 1
                    bird.unbookmark(ident, profile=profile)
                except Exception as exc:
                    result['failed'] = {**entry, 'reason': str(exc) or 'removal failed'}
                    with open(result['log'], 'a', encoding='utf-8') as stream:
                        stream.write(json.dumps({'time': clock().isoformat(), **result['failed'], 'failed': True}, ensure_ascii=False) + '\n')
                        stream.flush()
                        os.fsync(stream.fileno())
                    break
                now = clock().isoformat()
                state.db.execute('INSERT INTO unbookmarked VALUES (?, ?, ?)', (entry['source_id'], ident, now))
                state.db.commit()
                with open(result['log'], 'a', encoding='utf-8') as stream:
                    stream.write(json.dumps({'time': now, **entry}, ensure_ascii=False) + '\n')
                    stream.flush()
                    os.fsync(stream.fileno())
                result['removed'].append(entry)
            return result
    finally:
        state.close()
