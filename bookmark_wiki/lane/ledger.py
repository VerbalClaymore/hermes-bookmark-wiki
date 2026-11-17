"""Evidence ledger and duplicate-note annotations for the lean lane."""
from __future__ import annotations

import json
from pathlib import Path
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from . import deliver


def _clip(text, limit=200):
    """Collapse whitespace and cut at a word boundary, marking the cut."""
    text = ' '.join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit - 3]
    space = cut.rfind(' ')
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip(' ,;:') + '...'


def keys(bundle) -> list[str]:
    result = []
    def add(value):
        if value and value not in result:
            result.append(value)
    source = bundle.get('source_id')
    if isinstance(source, str) and re.fullmatch(r'x:[0-9]+', source):
        add(source)
    for post in bundle.get('thread', []):
        if isinstance(post, dict) and post.get('role') in ('quoted', 'parent'):
            ident = str(post.get('id', ''))
            if re.fullmatch(r'[0-9]+', ident):
                add('x:' + ident)
    for link in bundle.get('links', []):
        if not isinstance(link, dict):
            continue
        # Status links identify posts even when their fetch failed.
        for raw in (link.get('url'), link.get('final_url')):
            if not isinstance(raw, str):
                continue
            try:
                p = urlsplit(raw)
                host = (p.hostname or '').lower()
                match = re.match(r'^/[^/]+/status(?:es)?/([0-9]+)(?:/|$)', p.path)
                if host in ('x.com', 'www.x.com', 'twitter.com', 'www.twitter.com') and match:
                    add('x:' + match.group(1))
            except ValueError:
                continue
        raw = link.get('final_url')
        if link.get('status') != 'ok' or not isinstance(raw, str):
            continue
        try:
            p = urlsplit(raw)
            host = (p.hostname or '').lower()
            if p.scheme.lower() not in ('http', 'https') or not host:
                continue
            netloc = host + ((':' + str(p.port)) if p.port else '')
            query = urlencode([(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                               if not (k.lower().startswith('utm_') or k.lower() in ('ref', 's', 't'))])
            add(urlunsplit((p.scheme.lower(), netloc, p.path.rstrip('/'), query, '')))
            parts = p.path.strip('/').split('/')
            if host in ('github.com', 'www.github.com') and len(parts) >= 2 and all(parts[:2]):
                add('github:' + '/'.join(parts[:2]).lower().removesuffix('.git'))
            if host in ('arxiv.org', 'www.arxiv.org'):
                m = re.match(r'^/(?:abs|pdf)/(.+?)(?:\.pdf)?/?$', p.path, re.I)
                if m:
                    add('arxiv:' + m.group(1))
            video = None
            if host in ('youtu.be', 'www.youtu.be'):
                video = parts[0]
            elif host in ('youtube.com', 'www.youtube.com', 'm.youtube.com'):
                if p.path.rstrip('/') == '/watch':
                    video = next((v for k, v in parse_qsl(p.query) if k == 'v'), None)
                elif len(parts) >= 2 and parts[0] == 'shorts':
                    video = parts[1]
            if video:
                add('yt:' + video)
        except ValueError:
            continue
    return result


def record(store, source_id, bundle, *, title, takeaway, wiki_path, bots, note_source_id=None, clock):
    timestamp = clock().isoformat()
    bots_json = json.dumps(bots, ensure_ascii=False)
    takeaway = _clip(takeaway)
    store.db.execute('DELETE FROM sources WHERE source_id=?', (source_id,))
    store.db.executemany('''INSERT INTO sources(key,source_id,note_source_id,title,takeaway,wiki_path,bots_json,created_at)
        VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(key,source_id) DO UPDATE SET
        note_source_id=excluded.note_source_id,title=excluded.title,takeaway=excluded.takeaway,
        wiki_path=excluded.wiki_path,bots_json=excluded.bots_json,created_at=excluded.created_at''',
        [(key, source_id, note_source_id or source_id, title, takeaway, wiki_path, bots_json, timestamp)
         for key in keys(bundle)])
    store.db.commit()


def ensure(store) -> None:
    store.db.execute('''CREATE TABLE IF NOT EXISTS sources(key TEXT NOT NULL, source_id TEXT NOT NULL,
        note_source_id TEXT NOT NULL, title TEXT NOT NULL, takeaway TEXT NOT NULL, wiki_path TEXT,
        bots_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(key, source_id))''')
    store.db.commit()
    rows = list(store.db.execute("SELECT * FROM items WHERE status='done' AND NOT EXISTS (SELECT 1 FROM sources WHERE sources.source_id=items.source_id)"))
    for row in rows:
        try:
            bundle = store.read_artifact(row['tweet_id'], 'resolve')
            note = store.read_artifact(row['tweet_id'], 'write')
            delivery = store.read_artifact(row['tweet_id'], 'deliver')
            from datetime import datetime
            date = datetime.fromisoformat(row['updated_at'].replace('Z', '+00:00'))
            record(store, row['source_id'], bundle, title=note['title'], takeaway=note['short_answer'],
                   wiki_path=(delivery.get('wiki') or {}).get('path'), bots=[{k: b[k] for k in ('profile', 'topic_slug', 'reference_path')} for b in delivery['bots']], clock=lambda: date)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue


def prior_coverage(store, bundle, limit=5):
    evidence = keys(bundle)
    own = bundle.get('source_id')
    matching = {}
    known = set()
    for key in evidence:
        if key == own:
            continue
        for row in store.db.execute('''SELECT s.*, i.updated_at AS note_date FROM sources s JOIN items i ON i.source_id=s.note_source_id
                WHERE s.key=? AND s.source_id != ? AND s.note_source_id != ? AND i.status='done' ''', (key, own, own)):
            known.add(key)
            group = matching.setdefault(row['note_source_id'], {'keys': set(), 'date': '', 'row': row})
            group['keys'].add(key)
            if row['note_date'] >= group['date']:
                group['date'], group['row'] = row['note_date'], row
    groups = list(matching.items())
    groups.sort(key=lambda pair: pair[1]['date'], reverse=True)
    groups.sort(key=lambda pair: len(pair[1]['keys']), reverse=True)
    notes = []
    for sid, group in groups:
        try:
            note = store.read_artifact(sid[2:], 'write')
            original = store.db.execute('SELECT bots_json FROM sources WHERE source_id=? LIMIT 1', (sid,)).fetchone()
            bots = json.loads(original['bots_json'] if original else group['row']['bots_json'])
            notes.append({'source_id': sid, 'title': note['title'], 'short_answer': note['short_answer'],
                          'key_points': [point[:300] for point in note['key_points'][:8]], 'shared_keys': [k for k in evidence if k in group['keys']], 'bots': bots})
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if len(notes) >= limit:
            break
    return {'notes': notes, 'known_keys': [k for k in evidence if k in known],
            'new_keys': [k for k in evidence if k not in known]}


def recent_notes(store, limit=60):
    result = []
    for row in store.db.execute("SELECT source_id,tweet_id FROM items WHERE status='done' ORDER BY updated_at DESC, source_id DESC"):
        try:
            note = store.read_artifact(row['tweet_id'], 'write')
            result.append({'source_id': row['source_id'], 'title': note['title'],
                           'short_answer': ' '.join(note['short_answer'].split())[:160]})
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if len(result) >= limit:
            break
    return result


def topic_sources(store, profile, topic_slug):
    result = []
    for row in store.db.execute('SELECT * FROM sources ORDER BY created_at, source_id, key'):
        try:
            for bot in json.loads(row['bots_json']):
                if bot['profile'] == profile and bot['topic_slug'] == topic_slug:
                    result.append({k: row[k] for k in ('key', 'source_id', 'title', 'takeaway', 'created_at')} | {'reference_path': bot['reference_path']})
        except (ValueError, KeyError, TypeError):
            continue
    return result


def item_sources(bundle, note, reference_path, timestamp):
    return [{'key': key, 'source_id': bundle['source_id'], 'title': note['title'],
             'takeaway': _clip(note['short_answer']),
             'created_at': timestamp, 'reference_path': reference_path} for key in keys(bundle)]


def seen_again(vault, item_dir, store, earlier_source_id, bundle, clock):
    row = store.db.execute("SELECT tweet_id FROM items WHERE source_id=? AND status='done'", (earlier_source_id,)).fetchone()
    if row is None:
        return None
    try:
        delivery = store.read_artifact(row['tweet_id'], 'deliver')
        name = (delivery.get('wiki') or {}).get('path')
        if not name:
            return None
        root = Path(vault).absolute()
        target = Path(name)
        if not target.is_absolute():
            target = root / target
        relative = target.relative_to(root / 'wiki' / 'topics')
        path = deliver._safe_path(root, 'wiki', 'topics', *relative.parts)
        if not path.is_file():
            return None
        text = path.read_text(encoding='utf-8')
        post = bundle['post']
        url = post['url']
        if any(url in line for line in text.splitlines() if line.startswith('- Seen again:')):
            return str(path)
        author = post.get('author') or 'unknown'
        if isinstance(author, dict):
            author = author.get('username') or 'unknown'
        author = deliver._label(str(author).lstrip('@'))
        line = f'- Seen again: {clock().date().isoformat()} [@{author}]({url})\n'
        match = re.search(r'(?m)^## Seen again[ \t]*\r?$', text)
        if match:
            next_heading = re.search(r'(?m)^## ', text[match.end():])
            pos = match.end() + next_heading.start() if next_heading else len(text)
            prefix = text[:pos]
            text = prefix.rstrip('\n') + '\n' + line + ('\n' if pos < len(text) else '') + text[pos:]
        else:
            text = text.rstrip('\n') + '\n\n## Seen again\n\n' + line
        deliver._write(path, text, Path(item_dir).absolute())
        return str(path)
    except (OSError, KeyError, TypeError, ValueError):
        return None
