"""Save captured same-author and quoted media as a resumable keepsake."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urlsplit

from . import deliver, store


def _allowed(url, host):
    try:
        p = urlsplit(url)
        return (p.scheme.lower() == 'https' and p.hostname == host and
                p.username is None and p.password is None and p.port in (None, 443))
    except (ValueError, TypeError):
        return False


def _video_url(media):
    variants = media.get('variants') or (media.get('video_info') or {}).get('variants') or (media.get('videoInfo') or {}).get('variants') or []
    choices = []
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        url = variant.get('url')
        content = variant.get('content_type', variant.get('contentType', 'video/mp4'))
        bitrate = variant.get('bitrate', 0)
        if content == 'video/mp4' and _allowed(url, 'video.twimg.com'):
            choices.append((bitrate if isinstance(bitrate, (int, float)) and not isinstance(bitrate, bool) else 0, url))
    if choices:
        return max(choices)[1]
    url = media.get('videoUrl')
    return url if _allowed(url, 'video.twimg.com') else None


def _index(path, item_dir):
    original = path.read_text(encoding='utf-8') if path.exists() else ''
    heading = '## Bookmarks (lean lane)'
    match = re.search(r'(?m)^## Bookmarks \(lean lane\)[ \t]*\r?$', original)
    newline = '\r\n' if '\r\n' in original else '\n'
    entry = '- [Keepsakes](keepsakes.md): saved media with no reference content' + newline
    if match:
        start = match.end()
        following = re.search(r'(?m)^## ', original[start:])
        pos = start + following.start() if following else len(original)
        section = original[start:pos]
        if re.search(r'\]\(keepsakes\.md\)', section):
            return
        entries = [line for line in section.splitlines() if line.strip()]
        result = original[:start] + newline * 2 + ''.join(line + newline for line in entries) + entry + newline + original[pos:]
    else:
        limits = re.search(r'(?m)^## Limits[ \t]*\r?$', original)
        pos = limits.start() if limits else len(original)
        before = original[:pos]
        result = before + (newline if before and not before.endswith('\n') else '') + heading + newline * 2 + entry + newline + original[pos:]
    deliver._write(path, result, item_dir)


def _keep(item_dir, capture, bundle, triage_result, config, vault, *, fetch, run, which, clock):
    item_dir, vault = Path(item_dir).absolute(), Path(vault).absolute()
    source_id = bundle['source_id']
    if not isinstance(source_id, str) or not source_id.startswith('x:'):
        raise ValueError('invalid source id')
    ident = store.tweet_id(source_id[2:])
    if str(capture.get('tweet_id')) != ident:
        raise ValueError('capture source mismatch')
    tweets = capture.get('tweets') or []
    post = next((t for t in tweets if isinstance(t, dict) and str(t.get('id')) == ident), None)
    if post is None:
        raise ValueError('capture has no original post')
    author_id = post.get('authorId')
    author = post.get('author') or {}
    handle = author.get('username', '') if isinstance(author, dict) else str(author)
    if not isinstance(handle, str) or not re.fullmatch(r'[A-Za-z0-9_]{1,15}', handle):
        raise ValueError('invalid author')
    media = []
    for tweet in tweets:
        if not isinstance(tweet, dict):
            continue
        if tweet is not post:
            other_author = tweet.get('author') or {}
            other_handle = other_author.get('username', '') if isinstance(other_author, dict) else str(other_author)
            same_author = (tweet.get('authorId') == author_id if author_id is not None
                           else bool(other_handle) and other_handle.lower() == handle.lower())
            if not same_author:
                continue
        for entry in tweet.get('media') or []:
            if isinstance(entry, dict) and str(entry.get('type', '')).lower() in {'photo', 'image', 'video', 'animated_gif'}:
                media.append(entry)
    quoted = post.get('quotedTweet')
    if isinstance(quoted, dict):
        for entry in quoted.get('media') or []:
            if isinstance(entry, dict) and str(entry.get('type', '')).lower() in {'photo', 'image', 'video', 'animated_gif'}:
                media.append(entry)
    if not media:
        raise ValueError('no downloadable media')
    max_bytes = config['limits'].get('keepsake_file_bytes', 300000000)
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError('invalid keepsake file limit')
    directory = deliver._safe_path(vault, 'media', 'keepsakes')
    index = deliver._safe_path(vault, 'wiki', 'keepsakes.md')
    wiki_index = deliver._safe_path(vault, 'wiki', 'index.md')
    files = []
    for number, entry in enumerate(media, 1):
        kind = str(entry['type']).lower()
        prefix = f'{ident}-{handle}-{number}'
        is_video = kind in {'video', 'animated_gif'}
        if is_video:
            url = _video_url(entry)
            duration = entry.get('durationMs')
            if not url:
                raise ValueError('invalid video URL')
            if isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration > config['media']['max_video_seconds'] * 1000:
                raise ValueError('video longer than limit')
            extensions = ['.mp4']
        else:
            url = entry.get('url') or entry.get('previewUrl')
            if not _allowed(url, 'pbs.twimg.com'):
                raise ValueError('invalid image URL')
            extensions = ['.jpg', '.png', '.gif', '.webp']
        existing = [deliver._safe_path(directory, prefix + ext) for ext in extensions]
        for old in existing:
            if old.is_file() and old.stat().st_size > max_bytes:
                old.unlink()
                raise ValueError('file exceeds size limit')
        existing = [p for p in existing if p.is_file() and 0 < p.stat().st_size <= max_bytes]
        if existing:
            files.append(str(existing[0]))
            continue
        directory.mkdir(parents=True, exist_ok=True)
        if is_video:
            executable = which(config['media']['ffmpeg'])
            if not executable:
                raise ValueError('ffmpeg unavailable')
            fd, temp = tempfile.mkstemp(prefix='.keepsake-', suffix='.mp4', dir=directory)
            os.close(fd)
            try:
                result = run([executable, '-nostdin', '-v', 'error', '-y', '-protocol_whitelist', 'https,tls,tcp', '-i', url, '-c', 'copy', '-fs', str(max_bytes + 1), '-f', 'mp4', temp], capture_output=True, text=True, timeout=300, check=False)
                if result.returncode or not Path(temp).is_file() or not 0 < Path(temp).stat().st_size <= max_bytes:
                    raise ValueError('video download failed or exceeds size limit')
                path = deliver._safe_path(directory, prefix + '.mp4')
                os.replace(temp, path)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
        else:
            response = fetch(url, 'image', min(max_bytes, config['limits']['image_bytes']))
            if not response.ok:
                raise ValueError(response.reason or 'image download failed')
            if not _allowed(response.final_url or url, 'pbs.twimg.com'):
                raise ValueError('invalid image redirect')
            body = response.body
            if not isinstance(body, bytes) or not 0 < len(body) <= min(max_bytes, config['limits']['image_bytes']):
                raise ValueError('image exceeds size limit or is empty')
            if body.startswith(b'\xff\xd8\xff'):
                ext = '.jpg'
            elif body.startswith(b'\x89PNG\r\n\x1a\n'):
                ext = '.png'
            elif body.startswith((b'GIF87a', b'GIF89a')):
                ext = '.gif'
            elif body.startswith(b'RIFF') and body[8:12] == b'WEBP':
                ext = '.webp'
            else:
                raise ValueError('unsupported image format')
            path = deliver._safe_path(directory, prefix + ext)
            fd, temp = tempfile.mkstemp(prefix='.keepsake-', dir=directory)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(body)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp, path)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
        files.append(str(path))
    original = index.read_text(encoding='utf-8') if index.exists() else '# Keepsakes\n'
    post_url = 'https://x.com/' + handle + '/status/' + ident
    if not re.search(r'\]\(https?://(?:www\.)?(?:x\.com|twitter\.com)/[^/)]+/status/' + re.escape(ident) + r'(?:[/?#][^)]*)?\)', original):
        date = clock().date().isoformat()
        caption = deliver._label(deliver._line(triage_result['core']['summary'], 160))
        links = ' '.join(f'[file {n}](../media/keepsakes/{Path(p).name})' for n, p in enumerate(files, 1))
        line = f'- {date} {caption} by @{handle}: [post]({post_url}) {links}'
        deliver._write(index, original + ('' if original.endswith('\n') else '\n') + line + '\n', item_dir)
    _index(wiki_index, item_dir)
    return {'files': files, 'index': str(index)}


def keep(item_dir, capture, bundle, triage_result, config, vault, *, fetch, run, which, clock):
    try:
        return _keep(item_dir, capture, bundle, triage_result, config, vault,
                     fetch=fetch, run=run, which=which, clock=clock)
    except Exception as exc:
        raise ValueError('KEEPSAKE_FAILED: ' + (str(exc) or 'download failed')) from exc
