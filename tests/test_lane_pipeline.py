"""Offline end-to-end tests for the lean lane orchestrator."""
import contextlib
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bookmark_wiki import cli
from bookmark_wiki.lane import config, pipeline
from bookmark_wiki.lane.store import Store


class FakeBird:
    def __init__(self, tweets):
        self.tweets = tweets
        self.calls = []

    def run(self, action, args, *, profile):
        self.calls.append(action)
        return 'user_id: 84692934\n'

    def json(self, action, args, *, profile):
        self.calls.append(action)
        if action == 'bookmarks':
            return self.tweets
        if action == 'read':
            return next((tweet for tweet in self.tweets if tweet['id'] == args[0]), {})
        return []


class FakeLLM:
    def __init__(self):
        self.fail_ids = set()
        self.skip_ids = set()
        self.calls = []
        self.prompts = []

    def vision(self, path, prompt):
        return 'A test image'

    def json(self, prompt, validator):
        data = re.search(r'BEGIN UNTRUSTED BUNDLE\n(.*?)\nEND UNTRUSTED BUNDLE', prompt, re.S)
        if data is None:
            raise AssertionError('missing bundle')
        bundle = json.loads(data.group(1))
        ident = bundle['post']['id']
        self.calls.append(ident)
        self.prompts.append((ident, prompt))
        if ident in self.fail_ids:
            raise RuntimeError('LLM_FAILED: synthetic failure')
        if 'triaging a saved bookmark' in prompt:
            skip = ident in self.skip_ids
            result = {'core': {'title': 'Useful example', 'summary': 'Useful fact',
                               'where_found': 'post', 'primary_url': None},
                      'value': 'skip' if skip else 'normal',
                      'skip_reason': 'Duplicate of previous note' if skip else None,
                      'wiki': not skip, 'bots': [] if skip else [
                          {'profile': 'bot', 'reason': 'Useful to specialist work',
                           'relevance': 'normal', 'topic': 'Useful facts', 'topic_slug': 'useful-facts'}],
                      'unresolved': None}
        else:
            result = {'title': 'Useful example', 'short_answer': 'Useful fact [1].',
                      'explanation': 'Evidence [1].', 'key_points': ['Point [1].'],
                      'try_it': [], 'limits': [],
                      'sources': [{'n': 1, 'label': 'Post', 'url': bundle['post']['url']}]}
        validator(result)
        return result


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.vault = self.root / 'vault'
        self.home = self.root / 'hermes'
        profile = self.home / 'profiles' / 'bot'
        profile.mkdir(parents=True)
        (profile / 'SOUL.md').write_text('A useful specialist.', encoding='utf-8')
        config.init(self.vault)
        settings = config.load(self.vault)
        settings['hermes']['home'] = str(self.home)
        (self.vault / 'lane/config.json').write_text(json.dumps(settings), encoding='utf-8')
        self.bird = FakeBird([])
        self.llm = FakeLLM()
        self.now = datetime(2026, 11, 16, tzinfo=timezone.utc)
        self.deps = {'bird': self.bird, 'llm': self.llm, 'clock': lambda: self.now,
                     'fetch': self.forbidden, 'bot_runner': self.forbidden,
                     'which': lambda name: None, 'run': self.forbidden}

    def forbidden(self, *args, **kwargs):
        raise AssertionError('unexpected external call')

    def tweets(self, *ids):
        self.bird.tweets = [{'id': str(i), 'text': 'A useful fact', 'author': {'username': 'a'},
                             'createdAt': '2026-11-16T12:00:00Z'} for i in ids]

    def run_sync(self, **kwargs):
        return pipeline.sync(self.vault, deps=self.deps, **kwargs)

    def test_full_sync_delivers_wiki_and_skill_and_status(self):
        self.tweets(1)
        result = self.run_sync()
        self.assertEqual(result['items'][0]['status'], 'done')
        self.assertTrue(Path(result['report']).is_file())
        self.assertTrue((self.vault / 'wiki/topics/Bookmark_1_useful-example.md').is_file())
        self.assertTrue((self.home / 'profiles/bot/skills/bookmarks/useful-facts/SKILL.md').is_file())
        self.assertEqual(pipeline.status(self.vault)['statuses'], {'done': 1})
        self.assertEqual(pipeline.show(self.vault, 'x:1')['item']['stage'], 'deliver')

    def test_retry_changed_slug_repairs_links_and_removed_topic(self):
        self.tweets(1)
        first = self.run_sync()['items'][0]['delivery']
        old_wiki = Path(first['wiki']['path'])
        old_ref = Path(first['bots'][0]['reference_path'])
        other = old_wiki.parent / 'other.md'
        other.write_text(f'Builds on: [old]({old_wiki.name})\n')
        other_ref = old_ref.parent / '2-other.md'
        other_ref.write_text(f'Builds on: [old]({old_ref.name})\n')
        pipeline.retry(self.vault, 'x:1', from_stage='triage')
        original = self.llm.json
        def changed(prompt, validator):
            # Only the writer needs a new title.
            def writer(result):
                if 'short_answer' in result:
                    result['title'] = 'New title'
                validator(result)
            return original(prompt, writer)
        self.llm.json = changed
        second = self.run_sync(capture=False)['items'][0]
        self.assertEqual(second['status'], 'done')
        new_wiki = Path(second['delivery']['wiki']['path'])
        new_ref = Path(second['delivery']['bots'][0]['reference_path'])
        self.assertFalse(old_wiki.exists())
        self.assertFalse(old_ref.exists())
        self.assertIn(new_wiki.name, other.read_text())
        self.assertIn(new_ref.name, other_ref.read_text())
        self.assertNotIn(old_wiki.name, (self.vault / 'wiki/index.md').read_text())
        self.assertNotIn(old_ref.name, (old_ref.parent.parent / 'SKILL.md').read_text())
        self.assertTrue(list((self.vault / 'lane/items/1/backups').glob(old_wiki.name + '.*.bak')))
        self.assertTrue(list((self.vault / 'lane/items/1/backups').glob(old_ref.name + '.*.bak')))

        pipeline.retry(self.vault, 'x:1', from_stage='triage')
        def no_bot(prompt, validator):
            def change(result):
                if 'short_answer' not in result:
                    result['bots'] = []
                validator(result)
            return changed(prompt, change)
        self.llm.json = no_bot
        third = self.run_sync(capture=False)['items'][0]
        self.assertEqual(third['status'], 'done')
        self.assertFalse(new_ref.exists())
        self.assertNotIn(new_ref.name, (new_ref.parent.parent / 'SKILL.md').read_text())
        self.assertNotIn('Useful example', (new_ref.parent.parent / 'sources.md').read_text())

    def test_retry_same_slug_has_no_extra_cleanup(self):
        self.tweets(1)
        first = self.run_sync()['items'][0]['delivery']
        path = Path(first['wiki']['path'])
        ref = Path(first['bots'][0]['reference_path'])
        pipeline.retry(self.vault, 'x:1', from_stage='triage')
        self.assertEqual(self.run_sync(capture=False)['items'][0]['status'], 'done')
        self.assertTrue(path.exists())
        self.assertTrue(ref.exists())
        self.assertEqual((self.vault / 'wiki/index.md').read_text().count(path.name), 1)
        self.assertEqual((ref.parent.parent / 'SKILL.md').read_text().count(ref.name), 1)
        self.assertFalse(list((self.vault / 'lane/items/1/backups').glob(path.name + '.*.bak')))

    def test_prune_orphans_preview_and_apply(self):
        self.tweets(1)
        delivery = self.run_sync()['items'][0]['delivery']
        wiki = Path(delivery['wiki']['path'])
        ref = Path(delivery['bots'][0]['reference_path'])
        old_wiki = wiki.with_name('Bookmark_1_old.md')
        old_ref = ref.with_name('1-old.md')
        old_wiki.write_text('old wiki')
        old_ref.write_text('old reference')
        index = self.vault / 'wiki/index.md'
        skill = ref.parent.parent / 'SKILL.md'
        index.write_text(index.read_text() + f'- [old](topics/{old_wiki.name})\n')
        skill.write_text(skill.read_text() + f'- [old](references/{old_ref.name})\n')
        other = wiki.parent / 'other.md'
        other.write_text(f'Builds on: [old]({old_wiki.name})\n')
        before = {p: p.read_bytes() for p in (wiki, ref, old_wiki, old_ref, index, skill, other)}
        preview = pipeline.prune_orphans(self.vault)
        self.assertIn(str(old_wiki), [c['path'] for c in preview['changes']])
        self.assertIn(str(old_ref), [c['path'] for c in preview['changes']])
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        applied = pipeline.prune_orphans(self.vault, apply=True)
        self.assertEqual(preview['changes'], applied['changes'])
        self.assertFalse(old_wiki.exists())
        self.assertFalse(old_ref.exists())
        self.assertNotIn(old_wiki.name, index.read_text())
        self.assertNotIn(old_ref.name, skill.read_text())
        self.assertIn(wiki.name, other.read_text())

    def test_keepsake_dry_run_resume_and_finality(self):
        self.tweets(1)
        self.bird.tweets[0]['media'] = [{'type': 'photo', 'url': 'https://pbs.twimg.com/a'}]
        self.deps['fetch'] = lambda url, kind, maximum: SimpleNamespace(
            ok=True, body=b'\x89PNG\r\n\x1a\nimage', final_url=url, reason='')
        original = self.llm.json
        def select(prompt, validator):
            def change(result):
                result.update(value='keepsake', wiki=False, bots=[])
                validator(result)
            return original(prompt, change)
        self.llm.json = select
        calls = []
        def fake_keep(*args, **kwargs):
            calls.append(True)
            return {'files': ['/saved/file.png'], 'index': '/wiki/keepsakes.md'}
        self.deps['keepsake'] = fake_keep
        first = self.run_sync(dry_run=True)
        self.assertEqual(first['items'][0]['status'], 'pending')
        self.assertFalse(calls)
        self.assertNotIn('write', pipeline.show(self.vault, 'x:1')['artifacts'])
        second = self.run_sync(capture=False)
        self.assertEqual(second['items'][0]['status'], 'kept')
        self.assertEqual(len(calls), 1)
        artifact = pipeline.show(self.vault, 'x:1')['artifacts']['deliver']
        self.assertEqual(artifact, {'wiki': None, 'bots': [], 'keepsake': {'files': ['/saved/file.png'], 'index': '/wiki/keepsakes.md'}})
        self.assertIn('/saved/file.png', Path(second['report']).read_text())
        self.assertEqual(self.run_sync(capture=False)['items'], [])
        self.assertEqual(len(calls), 1)

    def test_catch_up_wiring(self):
        with patch('bookmark_wiki.lane.pipeline.capture_module.capture', return_value=[]) as capture:
            self.run_sync(catch_up=True)
            self.assertTrue(capture.call_args.kwargs['catch_up'])
            self.assertIsNone(capture.call_args.kwargs['capture_count'])
            self.run_sync(catch_up=True, capture_count=17)
            self.assertEqual(capture.call_args.kwargs['capture_count'], 17)

    def test_keepsake_download_failure_is_retried(self):
        self.tweets(1)
        self.bird.tweets[0]['media'] = [{'type': 'photo', 'url': 'https://pbs.twimg.com/a'}]
        self.deps['fetch'] = lambda url, kind, maximum: SimpleNamespace(
            ok=False, reason='fake download failure', final_url=url, body=b'')
        original = self.llm.json
        def select(prompt, validator):
            def change(result):
                result.update(value='keepsake', wiki=False, bots=[])
                validator(result)
            return original(prompt, change)
        self.llm.json = select
        failed = self.run_sync()['items'][0]
        self.assertEqual(failed['status'], 'failed')
        self.assertTrue(failed['error'].startswith('KEEPSAKE_FAILED:'))
        self.deps['fetch'] = lambda url, kind, maximum: SimpleNamespace(
            ok=True, body=b'\x89PNG\r\n\x1a\nimage', final_url=url, reason='')
        result = self.run_sync(capture=False)['items'][0]
        self.assertEqual(result['status'], 'kept')
        self.assertTrue(Path(result['delivery']['keepsake']['files'][0]).is_file())

    def test_cli_catch_up_sync_only(self):
        with patch('bookmark_wiki.lane.pipeline.sync', return_value={}) as sync:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(['lane', 'sync', '--vault', str(self.vault), '--catch-up']), 0)
            self.assertTrue(sync.call_args.kwargs['catch_up'])

    def test_resume_after_resolve_without_bird(self):
        self.tweets(1)
        self.llm.fail_ids.add('1')
        self.assertEqual(self.run_sync()['items'][0]['status'], 'failed')
        self.assertEqual(pipeline.show(self.vault, 'x:1')['item']['stage'], 'resolve')
        self.llm.fail_ids.clear()
        self.bird.calls.clear()
        self.assertEqual(self.run_sync(capture=False)['items'][0]['status'], 'done')
        self.assertEqual(self.bird.calls, [])

    def test_failures_are_isolated_and_stop_at_max_attempts(self):
        self.tweets(1, 2)
        self.llm.fail_ids.add('2')
        result = self.run_sync()
        self.assertEqual({x['source_id']: x['status'] for x in result['items']}, {'x:1': 'done', 'x:2': 'failed'})
        self.run_sync(capture=False)
        self.run_sync(capture=False)
        self.assertEqual(pipeline.show(self.vault, 'x:2')['item']['attempts'], 3)
        self.assertEqual(self.run_sync(capture=False)['items'], [])
        self.assertTrue(pipeline.status(self.vault)['failed'][0]['error'].startswith('LLM_FAILED:'))

    def test_limit_selects_newest_twitter_date_first(self):
        self.tweets(1, 2)
        self.bird.tweets[0]['createdAt'] = 'Mon Nov 16 12:00:00 +0000 2026'
        self.bird.tweets[1]['createdAt'] = 'Sun Nov 15 12:00:00 +0000 2026'
        result = self.run_sync(limit=1)
        self.assertEqual([item['source_id'] for item in result['items']], ['x:1'])

    def test_newer_than_filters_post_date_and_preserves_pending(self):
        self.tweets(1, 2, 3, 4)
        self.bird.tweets[0]['createdAt'] = '2026-11-15T23:59:59Z'
        self.bird.tweets[1]['createdAt'] = '2026-11-16T00:00:00Z'
        self.bird.tweets[2]['createdAt'] = '2026-11-15T20:00:00-05:00'
        del self.bird.tweets[3]['createdAt']
        result = self.run_sync(newer_than='2026-11-16')
        self.assertEqual({item['source_id'] for item in result['items']}, {'x:2', 'x:3'})
        for sid in ('x:1', 'x:4'):
            item = pipeline.show(self.vault, sid)
            self.assertEqual(item['item']['status'], 'pending')
            self.assertEqual(item['item']['attempts'], 0)
            self.assertEqual(set(item['artifacts']), {'capture'})
        for invalid in ('2026-02-30', '2026-1-01', '', True):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.run_sync(newer_than=invalid)
        self.assertEqual({item['source_id'] for item in self.run_sync(capture=False)['items']}, {'x:1', 'x:4'})

    def test_newer_than_processes_pending_reply_after_cutoff(self):
        self.tweets(2)
        self.bird.tweets[0].update(threadRootId='1', createdAt='2026-11-17T12:00:00Z')
        self.assertEqual(self.run_sync(newer_than='2026-11-18')['items'], [])
        self.assertEqual(pipeline.show(self.vault, 'x:1')['item']['status'], 'pending')
        result = self.run_sync(capture=False, newer_than='2026-11-16')
        self.assertEqual([(item['source_id'], item['status']) for item in result['items']], [('x:1', 'done')])

    def test_newer_than_leaves_pending_reply_before_cutoff(self):
        self.tweets(2)
        self.bird.tweets[0].update(threadRootId='1', createdAt='2026-11-15T12:00:00Z')
        self.assertEqual(self.run_sync(newer_than='2026-11-16')['items'], [])
        item = pipeline.show(self.vault, 'x:1')
        self.assertEqual(item['item']['status'], 'pending')
        self.assertEqual(item['item']['attempts'], 0)
        self.assertEqual([tweet['id'] for tweet in item['artifacts']['capture']['tweets']], ['2'])

    def test_newer_than_uses_earliest_parseable_reply_date(self):
        self.tweets(2, 3, 4)
        for tweet, created_at in zip(self.bird.tweets, ('invalid', '2026-11-17T12:00:00Z', '2026-11-15T12:00:00Z')):
            tweet.update(threadRootId='1', createdAt=created_at)
        self.assertEqual(self.run_sync(newer_than='2026-11-16')['items'], [])
        result = self.run_sync(capture=False, newer_than='2026-11-15')
        self.assertEqual([item['source_id'] for item in result['items']], ['x:1'])

    def test_dry_run_writes_no_external_files_then_resumes(self):
        self.tweets(1)
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*')
                  if p.is_file() and self.vault / 'lane' not in p.parents}
        self.assertEqual(self.run_sync(dry_run=True)['items'][0]['status'], 'pending')
        after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*')
                 if p.is_file() and self.vault / 'lane' not in p.parents}
        self.assertEqual(before, after)
        self.assertEqual(self.run_sync(capture=False)['items'][0]['status'], 'done')

    def test_skip_has_no_note(self):
        self.tweets(1)
        self.llm.skip_ids.add('1')
        self.assertEqual(self.run_sync()['items'][0]['status'], 'duplicate')
        self.assertFalse((self.vault / 'wiki').exists())
        self.assertFalse((self.vault / 'lane/items/1/note.json').exists())

    def test_non_duplicate_skip_status(self):
        self.tweets(1)
        original = self.llm.json

        def skip(prompt, validator):
            def change(result):
                result.update(value='skip', skip_reason='Pure opinion', wiki=False, bots=[])
                validator(result)
            result = original(prompt, change)
            return result

        self.llm.json = skip
        self.assertEqual(self.run_sync()['items'][0]['status'], 'skipped')
        self.assertFalse((self.vault / 'wiki').exists())

    def test_coverage_hint_reaches_triage_and_writer(self):
        self.deps['fetch'] = lambda url, kind, maximum: SimpleNamespace(
            ok=True, body=b'<title>Example</title><p>Useful' + b'x' * 200 + b'</p>', final_url=url, reason='')
        self.tweets(1)
        self.bird.tweets[0]['text'] += ' https://example.test/evidence'
        self.assertEqual(self.run_sync()['items'][0]['status'], 'done')
        self.tweets(2)
        self.bird.tweets[0]['text'] += ' https://example.test/evidence'
        self.assertEqual(self.run_sync()['items'][0]['status'], 'done')
        prompts = [prompt for ident, prompt in self.llm.prompts if ident == '2']
        self.assertEqual(len(prompts), 2)
        self.assertIn('BEGIN UNTRUSTED PRIOR COVERAGE', prompts[0])
        self.assertIn('Useful example', prompts[0])
        self.assertIn('BEGIN UNTRUSTED RECENT NOTES', prompts[0])

    def test_capture_retry_with_capture_enabled(self):
        self.tweets(1)
        self.assertEqual(self.run_sync()['items'][0]['status'], 'done')
        pipeline.retry(self.vault, 'x:1', from_stage='capture')
        self.assertEqual(self.run_sync()['items'][0]['status'], 'done')
        self.assertIn('read', self.bird.calls)

    def test_duplicate_dry_run_resume_seen_again_and_retry(self):
        self.deps['fetch'] = lambda url, kind, maximum: SimpleNamespace(ok=True, body=b'<p>' + b'x' * 250 + b'</p>', final_url=url, reason='')
        self.tweets(1)
        self.bird.tweets[0]['text'] += ' https://example.test/shared'
        self.assertEqual(self.run_sync()['items'][0]['status'], 'done')
        self.tweets(2)
        self.bird.tweets[0]['text'] += ' https://example.test/shared'
        original = self.llm.json
        def duplicate(prompt, validator):
            def change(result):
                if 'triaging a saved bookmark' in prompt:
                    result.update(novelty='duplicate', duplicate_of='x:1', wiki=False, bots=[])
                validator(result)
            return original(prompt, change)
        self.llm.json = duplicate
        item = self.run_sync(dry_run=True)['items'][0]
        self.assertEqual(item['status'], 'pending')
        self.assertEqual(pipeline.show(self.vault, 'x:2')['item']['stage'], 'triage')
        item = self.run_sync(capture=False)['items'][0]
        self.assertEqual(item['status'], 'duplicate')
        self.assertEqual(item['delivery'], {'wiki': None, 'bots': [], 'duplicate_of': 'x:1'})
        self.assertNotIn('write', pipeline.show(self.vault, 'x:2')['artifacts'])
        note = Path(item['seen_again_path'])
        self.assertIn('[@a](https://x.com/a/status/2)', note.read_text())
        self.assertIn('Duplicate of: Useful example (x:1)', Path(self.run_sync(capture=False)['report']).read_text())
        pipeline.retry(self.vault, 'x:2', from_stage='triage')
        self.assertEqual(self.run_sync(capture=False)['items'][0]['status'], 'duplicate')
        self.assertEqual(note.read_text().count('Seen again:'), 1)

    def test_duplicate_becomes_partial_if_prior_is_no_longer_done(self):
        self.deps['fetch'] = lambda url, kind, maximum: SimpleNamespace(ok=True, body=b'<p>' + b'x' * 250 + b'</p>', final_url=url, reason='')
        self.tweets(1)
        self.bird.tweets[0]['text'] += ' https://example.test/shared'
        self.assertEqual(self.run_sync()['items'][0]['status'], 'done')
        self.tweets(2)
        self.bird.tweets[0]['text'] += ' https://example.test/shared'
        original = self.llm.json
        def duplicate(prompt, validator):
            def change(result):
                if 'triaging a saved bookmark' in prompt:
                    result.update(novelty='duplicate', duplicate_of='x:1', wiki=False, bots=[])
                validator(result)
            return original(prompt, change)
        self.llm.json = duplicate
        self.assertEqual(self.run_sync(dry_run=True)['items'][0]['status'], 'pending')
        state = Store(self.vault, clock=lambda: self.now)
        state.db.execute("UPDATE items SET status='pending', attempts=99 WHERE source_id='x:1'")
        state.db.commit()
        state.close()
        item = self.run_sync(capture=False)['items'][0]
        self.assertEqual(item['status'], 'done')
        self.assertEqual(item['triage']['novelty'], 'partial')
        self.assertTrue(item['triage']['wiki'])

    def test_partial_pipeline_links_and_ledger_sources(self):
        self.tweets(1)
        first = self.run_sync()['items'][0]
        self.assertEqual(first['status'], 'done')
        state = Store(self.vault, clock=lambda: self.now)
        old_note = state.read_artifact('1', 'write')
        old_note['key_points'].append('Unique previous detail ' + 'z' * 400)
        state.write_artifact('1', 'write', old_note)
        state.close()
        self.tweets(2)
        original = self.llm.json
        def partial(prompt, validator):
            def change(result):
                if 'triaging a saved bookmark' in prompt:
                    result.update(novelty='partial', duplicate_of='x:1')
                else:
                    result['title'] = 'Increment'
                validator(result)
            return original(prompt, change)
        self.llm.json = partial
        item = self.run_sync()['items'][0]
        self.assertEqual(item['status'], 'done')
        self.assertIn('Builds on: [Useful example](Bookmark_1_useful-example.md)', Path(item['delivery']['wiki']['path']).read_text())
        reference = Path(item['delivery']['bots'][0]['reference_path'])
        self.assertIn('Builds on: [Useful example](1-useful-example.md)', reference.read_text())
        prompt = [p for ident, p in self.llm.prompts if ident == '2' and 'triaging a saved bookmark' not in p][0]
        self.assertIn('Point [1].', prompt)
        self.assertIn('Unique previous detail ' + 'z' * 400, prompt)
        self.assertIn('BEGIN UNTRUSTED PRIOR COVERAGE', prompt)
        sources = (reference.parent.parent / 'sources.md').read_text()
        self.assertIn('- https://x.com/i/status/1\n', sources)
        self.assertIn('- https://x.com/i/status/2\n', sources)

    def test_invalid_resolve_discards_dependent_artifacts(self):
        self.tweets(1)
        self.run_sync(dry_run=True)
        bundle = self.vault / 'lane/items/1/bundle.json'
        bundle.write_text('{}')
        self.llm.fail_ids.add('1')
        self.assertEqual(self.run_sync(capture=False)['items'][0]['status'], 'failed')
        item = pipeline.show(self.vault, 'x:1')
        self.assertEqual(item['item']['stage'], 'resolve')
        self.assertNotIn('write', item['artifacts'])

    def test_lock_refusal_and_stale_takeover(self):
        state = Store(self.vault, clock=lambda: self.now)
        self.addCleanup(state.close)
        with state.locked():
            with self.assertRaisesRegex(RuntimeError, 'LOCKED'):
                self.run_sync(capture=False)
        lock = self.vault / 'lane/lane.lock'
        lock.write_text(json.dumps({'token': 'old', 'created_at': (self.now - timedelta(hours=3)).timestamp()}))
        self.assertEqual(self.run_sync(capture=False)['items'], [])
        self.assertFalse(lock.exists())

    def test_seed_and_retry_from_stage(self):
        content = {'tweet_id': '7', 'text': 'Archived', 'author_handle': 'a',
                   'source_url': 'https://x.com/a/status/7', 'expanded_urls': [], 'media': []}
        source = {'current_revision': 'r', 'revisions': {'r': {'content': content}}}
        (self.vault / 'manifest.json').write_text(json.dumps({'sources': {'x:7': source}}))
        self.assertEqual(pipeline.seed(self.vault, from_archive=True, limit=1, newest_first=True, deps=self.deps)['seeded'], ['x:7'])
        self.assertTrue(pipeline.show(self.vault, 'x:7')['artifacts']['capture']['needs_refresh'])
        self.assertEqual(self.run_sync(capture=False)['items'][0]['status'], 'done')
        pipeline.retry(self.vault, 'x:7', from_stage='triage')
        show = pipeline.show(self.vault, 'x:7')
        self.assertEqual(set(show['artifacts']), {'capture', 'resolve'})
        self.bird.calls.clear()
        self.assertEqual(self.run_sync(capture=False)['items'][0]['status'], 'done')
        self.assertEqual(self.bird.calls, [])

    def test_cli_envelope_and_unknown_failure(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(['lane', 'status', '--vault', str(self.vault)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())['command'], 'lane')
        out = io.StringIO()
        with patch('bookmark_wiki.lane.pipeline.status', side_effect=RuntimeError('secret')), contextlib.redirect_stdout(out):
            code = cli.main(['lane', 'status', '--vault', str(self.vault)])
        self.assertEqual(code, 2)
        result = json.loads(out.getvalue())
        self.assertEqual(result['error'], {'code': 'LANE_ERROR', 'message': 'secret'})

    def test_capture_count_cli_and_validation(self):
        original = (self.vault / 'lane/config.json').read_bytes()
        calls = []
        original_json = self.bird.json
        def json_call(action, args, *, profile):
            calls.append((action, args, profile))
            return original_json(action, args, profile=profile)
        self.bird.json = json_call
        out = io.StringIO()
        with patch('bookmark_wiki.lane.pipeline.default_deps', return_value={**self.deps, 'vision': self.llm.vision}), contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(['lane', 'sync', '--vault', str(self.vault), '--capture-count', '800']), 0)
        self.assertEqual(calls[0], ('bookmarks', ['-n', '20', '--all', '--max-pages', '1', '--author-chain', '--thread-meta', '--json'], 'Profile 1'))
        self.assertEqual((self.vault / 'lane/config.json').read_bytes(), original)
        for invalid in (0, 801, True, 1.5):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.run_sync(capture_count=invalid)

    def test_sync_reports_repaired_capture_and_partial_page_failure(self):
        from bookmark_wiki.lane.bird import Bird
        def runner(argv, **kwargs):
            if 'whoami' in argv:
                payload = b'user_id: 84692934\n'
            elif '--cursor' in argv:
                return subprocess.CompletedProcess(argv, 1)
            else:
                payload = (b'{"tweets":[{"id":"91","text":"fact \xff",'
                           b'"author":{"username":"a"}}],"nextCursor":"next"}')
            kwargs['stdout'].write(payload)
            return subprocess.CompletedProcess(argv, 0)
        self.deps['bird'] = Bird(config.DEFAULTS['bird'], runner=runner)
        result = self.run_sync()
        self.assertEqual(result['captured'], ['x:91'])
        self.assertTrue(result['decode_repaired'])
        self.assertEqual(result['capture_warning'], 'BIRD_FAILED: Bird returned a failure')
        self.assertEqual(result['items'][0]['status'], 'done')
        self.assertEqual(pipeline.show(self.vault, 'x:91')['artifacts']['capture']['tweets'][0]['text'], 'fact \ufffd')

    def test_sync_reports_repaired_read_without_capture(self):
        from bookmark_wiki.lane.bird import Bird
        content = {'tweet_id': '92', 'text': 'Archived', 'author_handle': 'a', 'media': []}
        source = {'current_revision': 'r', 'revisions': {'r': {'content': content}}}
        (self.vault / 'manifest.json').write_text(json.dumps({'sources': {'x:92': source}}))
        pipeline.seed(self.vault, ids=['x:92'], deps=self.deps)
        def runner(argv, **kwargs):
            kwargs['stdout'].write(b'{"id":"92","text":"fact \xff", "author":{"username":"a"}}')
            return subprocess.CompletedProcess(argv, 0)
        self.deps['bird'] = Bird(config.DEFAULTS['bird'], runner=runner)
        result = self.run_sync(capture=False)
        self.assertTrue(result['decode_repaired'])
        self.assertEqual(result['items'][0]['status'], 'done')
        self.assertNotIn('decode_repaired', self.run_sync(capture=False))

    def test_sync_script_with_fake_interpreter(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/lane-sync.sh'
        self.assertTrue(os.access(script, os.X_OK))
        root = self.root / 'repo with spaces'
        (root / 'scripts').mkdir(parents=True)
        fake = root / 'fake-python'
        fake.write_text('#!/bin/zsh\nprintf "%s\\n" "$PWD" "${PYTHONPATH-unset}" "${PYTHONHOME-unset}" "$PYTHONDONTWRITEBYTECODE" "$@"\n')
        fake.chmod(0o755)
        copied = root / 'scripts/lane-sync.sh'
        copied.write_text(script.read_text().replace('/Users/vernon/.hermes/hermes-agent/venv/bin/python3', '"' + str(fake) + '"'))
        for vault in (None, str(self.root / 'vault with spaces')):
            env = {**os.environ, 'PYTHONPATH': 'bad', 'PYTHONHOME': 'bad'}
            env.pop('BOOKMARK_VAULT', None)
            if vault is not None:
                env['BOOKMARK_VAULT'] = vault
            result = subprocess.run(['/bin/zsh', str(copied), '--capture-count', '21'],
                                    cwd=self.root, env=env, text=True, capture_output=True, check=True)
            self.assertEqual(result.stdout.splitlines(), [str(root), 'unset', 'unset', '1',
                '-B', '-m', 'bookmark_wiki', 'lane', 'sync', '--vault',
                vault or '/Users/vernon/Documents/BookmarkWiki', '--capture-count', '21'])

    def test_cli_lane_errors_and_progress(self):
        from bookmark_wiki.lane.bird import BirdError
        for exc, expected in ((BirdError('BIRD_FAILED: invalid Bird JSON'),
                               {'code': 'BIRD_FAILED', 'message': 'invalid Bird JSON'}),
                              (ValueError('bad\n' + 'x' * 500),
                               {'code': 'LANE_ERROR', 'message': ('bad ' + 'x' * 500)[:400]})):
            out, err = io.StringIO(), io.StringIO()
            with patch('bookmark_wiki.lane.pipeline.status', side_effect=exc), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(['lane', 'status', '--vault', str(self.vault)])
            self.assertNotEqual(code, 0)
            self.assertEqual(json.loads(out.getvalue())['error'], expected)
            self.assertEqual(err.getvalue(), '')
        self.tweets(1, 2)
        self.llm.fail_ids.add('2')
        out, err = io.StringIO(), io.StringIO()
        with patch('bookmark_wiki.lane.pipeline.default_deps', return_value={**self.deps, 'vision': self.llm.vision}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(['lane', 'sync', '--vault', str(self.vault)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())['status'], 'ok')
        self.assertEqual(len(out.getvalue().splitlines()), 1)
        self.assertIn('x:1 resolve ok', err.getvalue())
        self.assertIn('x:2 triage failed: LLM_FAILED: synthetic failure', err.getvalue())
