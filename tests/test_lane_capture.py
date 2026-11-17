"""Offline tests for the lean lane's initial capture components."""

from datetime import datetime, timedelta, timezone
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from bookmark_wiki.lane import config
from bookmark_wiki.lane.bird import Bird, BirdError
from bookmark_wiki.lane.capture import capture, groups
from bookmark_wiki.lane.store import Store


class PageBird:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def run(self, action, args, *, profile):
        return 'user_id: 84692934\n'

    def json(self, action, args, *, profile):
        self.calls.append(args)
        result = self.pages.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class LaneCaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name)
        self.now = datetime(2026, 11, 16, tzinfo=timezone.utc)
        self.store = Store(self.vault, clock=lambda: self.now)
        self.addCleanup(self.store.close)
        self.config = copy.deepcopy(config.DEFAULTS)

    def test_config_does_not_overwrite(self):
        path = config.init(self.vault)
        self.assertEqual(config.load(self.vault), self.config)
        path.write_text('{}', encoding='utf-8')
        config.init(self.vault)
        self.assertEqual(path.read_text(encoding='utf-8'), '{}')
        self.assertEqual(config.load(self.vault), self.config)

    def test_bird_restricts_actions_and_strips_tokens(self):
        calls = []
        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            kwargs['stdout'].write(b'user_id: 84692934\n')
            return subprocess.CompletedProcess(argv, 0)
        bird = Bird(self.config['bird'], runner=run)
        with patch.dict('os.environ', {'AUTH_TOKEN': 'secret', 'CT0': 'secret', 'TWITTER_AUTH_TOKEN': 'secret', 'TWITTER_CT0': 'secret'}):
            bird.run('whoami', [], profile='Profile 1')
        argv, kwargs = calls[0]
        self.assertIn('--cookie-source', argv)
        self.assertIn('--chrome-profile', argv)
        self.assertEqual(argv[-2:], ['--plain', 'whoami'])
        self.assertFalse(set(kwargs['env']) & {'AUTH_TOKEN', 'CT0', 'TWITTER_AUTH_TOKEN', 'TWITTER_CT0'})
        with self.assertRaises(ValueError):
            bird.run('tweet', [], profile='Profile 1')

    def test_bird_large_output_and_limit(self):
        payload = json.dumps({'text': 'x' * 305353}).encode()
        def run(argv, **kwargs):
            self.assertNotIn('capture_output', kwargs)
            kwargs['stdout'].write(payload)
            kwargs['stderr'].write(b'diagnostic')
            return subprocess.CompletedProcess(argv, 0)
        bird = Bird(self.config['bird'], runner=run)
        self.assertEqual(len(bird.json('read', [], profile='p')['text']), 305353)
        with patch('bookmark_wiki.lane.bird.MAX_STDOUT', len(payload) - 1):
            with self.assertRaisesRegex(BirdError, 'stdout exceeds'):
                bird.run('read', [], profile='p')
        with patch('bookmark_wiki.lane.bird.MAX_STDOUT', len(payload)):
            self.assertEqual(bird.run('read', [], profile='p').encode(), payload)

    def test_group_and_skip_known_member(self):
        tweets = [
            {'id': '101', 'threadRootId': '100', 'text': 'root'},
            {'id': '102', 'threadRootId': '100', 'text': 'reply'},
            {'id': '200', 'text': 'already known'},
            {'id': '300', 'text': 'other'},
        ]
        (self.vault / 'manifest.json').write_text(json.dumps({'sources': {'x:200': {}}}), encoding='utf-8')
        class FakeBird:
            def run(self, *args, **kwargs):
                return 'user_id: 84692934\n'
            def json(self, *args, **kwargs):
                return tweets
        self.assertEqual([g[0] for g in groups(tweets)], ['100', '200', '300'])
        self.assertEqual(capture(self.vault, self.store, self.config, FakeBird()), ['x:100', 'x:300'])
        self.assertEqual(capture(self.vault, self.store, self.config, FakeBird()), [])
        self.assertEqual(len(self.store.read_artifact('100', 'capture')['tweets']), 2)

    def test_wrong_account_stops_before_bookmarks(self):
        class FakeBird:
            def run(self, *args, **kwargs):
                return 'user_id: 123\n'
            def json(self, *args, **kwargs):
                raise AssertionError('must not request bookmarks')
        with self.assertRaisesRegex(BirdError, 'CAPTURE_ACCOUNT_MISMATCH'):
            capture(self.vault, self.store, self.config, FakeBird())

    def test_bookmarks_pagination_arguments(self):
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            kwargs['stdout'].write(b'user_id: 84692934\n' if 'whoami' in argv else b'[]')
            return subprocess.CompletedProcess(argv, 0)
        bird = Bird(self.config['bird'], runner=run)
        for count in (1, 20, 21, 40, 41, 800):
            with self.subTest(count=count):
                self.config['limits']['capture_count'] = count
                capture(self.vault, self.store, self.config, bird)
                argv = calls[-1]
                self.assertEqual(argv[argv.index('bookmarks') + 1:],
                                 ['-n', '20', '--all', '--max-pages', '1', '--author-chain', '--thread-meta', '--json'])

    def test_bird_replaces_invalid_utf8_for_all_json_actions(self):
        payload = [b'{"text":"bad\xffvalue"}']
        def run(argv, **kwargs):
            kwargs['stdout'].write(payload[0])
            return subprocess.CompletedProcess(argv, 0)
        bird = Bird(self.config['bird'], runner=run)
        for action in ('bookmarks', 'read', 'thread', 'replies'):
            with self.subTest(action=action):
                self.assertEqual(bird.json(action, [], profile='p'), {'text': 'bad\ufffdvalue'})
                self.assertTrue(bird.last_decode_repaired)
        self.assertEqual(bird.decode_repaired_count, 4)
        payload[0] = b'{"text":"ok"}'
        bird.json('read', [], profile='p')
        self.assertFalse(bird.last_decode_repaired)
        payload[0] = b'\xff not json'
        with self.assertRaisesRegex(BirdError, 'BIRD_FAILED: invalid Bird JSON'):
            bird.json('read', [], profile='p')
        self.assertTrue(bird.last_decode_repaired)

    def test_bird_repairs_control_characters_in_json_values(self):
        payload = [b'{"nested":["a\x00b", {"text":"\xff\x01\\n\\r\\t", "number":1}]}']
        def run(argv, **kwargs):
            kwargs['stdout'].write(payload[0])
            return subprocess.CompletedProcess(argv, 0)
        bird = Bird(self.config['bird'], runner=run)
        self.assertEqual(bird.json('read', [], profile='p'),
                         {'nested': ['a\ufffdb', {'text': '\ufffd\ufffd\n\r\t', 'number': 1}]})
        self.assertTrue(bird.last_decode_repaired)
        self.assertEqual(bird.decode_repaired_count, 1)

        payload[0] = b'{"text":"a\x00b"}'
        self.assertEqual(bird.json('read', [], profile='p'), {'text': 'a\ufffdb'})
        self.assertTrue(bird.last_decode_repaired)
        self.assertEqual(bird.decode_repaired_count, 2)

        payload[0] = b'{"text":"clean\\u0000"}'
        self.assertEqual(bird.json('read', [], profile='p'), {'text': 'clean\x00'})
        self.assertFalse(bird.last_decode_repaired)
        self.assertEqual(bird.decode_repaired_count, 2)

        payload[0] = b'{"text":"a\x00b",}'
        with self.assertRaisesRegex(BirdError, 'BIRD_FAILED: invalid Bird JSON'):
            bird.json('read', [], profile='p')
        self.assertFalse(bird.last_decode_repaired)
        self.assertEqual(bird.decode_repaired_count, 2)

    def test_page_shapes_cursor_and_group_count(self):
        self.config['limits']['capture_count'] = 3
        pages = [
            {'tweets': [{'id': '1', 'threadRootId': '1'}, {'id': '2', 'threadRootId': '1'}], 'nextCursor': 'first'},
            {'tweets': [{'id': '3'}, {'id': '4'}, {'id': '5'}], 'nextCursor': 'second'},
        ]
        bird = PageBird(pages)
        self.assertEqual(capture(self.vault, self.store, self.config, bird), ['x:1', 'x:3', 'x:4'])
        self.assertEqual(len(bird.calls), 2)
        self.assertEqual(bird.calls[1][-2:], ['--cursor', 'first'])
        self.assertEqual(len(self.store.read_artifact('1', 'capture')['tweets']), 2)
        self.assertFalse(self.store.artifact('5', 'capture').exists())

    def test_page_stop_conditions(self):
        for pages, expected in (
            ([[]], 1),
            ([{'tweets': [{'id': '11'}]}], 1),
            ([{'tweets': [{'id': '12'}], 'nextCursor': 'a'}, {'tweets': [], 'nextCursor': 'b'}], 2),
            ([{'tweets': [{'id': '13'}], 'nextCursor': 'a'}, {'tweets': [{'id': '14'}], 'nextCursor': 'a'}], 2),
            ([{'tweets': [{'id': '15'}], 'nextCursor': 'a'}, {'tweets': [{'id': '16'}], 'nextCursor': 'b'}, {'tweets': [{'id': '17'}], 'nextCursor': 'a'}], 3),
        ):
            with self.subTest(pages=pages):
                bird = PageBird(pages)
                capture(self.vault, self.store, self.config, bird)
                self.assertEqual(len(bird.calls), expected)

    def test_incremental_whole_page_known_stops_but_mixed_continues(self):
        (self.vault / 'manifest.json').write_text(json.dumps({'sources': {'x:21': {}, 'x:24': {}}}))
        bird = PageBird([
            {'tweets': [{'id': '21'}, {'id': '22'}], 'nextCursor': 'a'},
            {'tweets': [{'id': '23'}], 'nextCursor': 'b'},
            {'tweets': [{'id': '24'}], 'nextCursor': 'c'},
        ])
        self.assertEqual(capture(self.vault, self.store, self.config, bird), ['x:22', 'x:23'])
        self.assertEqual(len(bird.calls), 3)
        bird = PageBird([{'tweets': [{'id': '21'}], 'nextCursor': 'a'}])
        self.assertEqual(capture(self.vault, self.store, self.config, bird), [])
        self.assertEqual(len(bird.calls), 1)

    def test_hard_page_cap(self):
        self.config['limits']['capture_count'] = 800
        bird = PageBird([{'tweets': [{'id': str(1000 + i)}], 'nextCursor': str(i)} for i in range(61)])
        self.assertEqual(len(capture(self.vault, self.store, self.config, bird)), 60)
        self.assertEqual(len(bird.calls), 60)

    def test_catch_up_passes_lane_pages_and_stops_at_archive(self):
        (self.vault / 'manifest.json').write_text(json.dumps({'sources': {'x:82': {}, 'x:90': {}}}))
        capture(self.vault, self.store, self.config, PageBird([[{'id': '70'}]]))
        pages = [
            {'tweets': [{'id': '70'}], 'nextCursor': 'a'},
            {'tweets': [{'id': '80'}, {'id': '82', 'threadRootId': '81'}], 'nextCursor': 'b'},
            {'tweets': [{'id': '90'}], 'nextCursor': 'c'},
        ]
        normal = PageBird(copy.deepcopy(pages))
        self.assertEqual(capture(self.vault, self.store, self.config, normal), [])
        self.assertEqual(len(normal.calls), 1)
        metadata = {}
        bird = PageBird(copy.deepcopy(pages))
        self.assertEqual(capture(self.vault, self.store, self.config, bird,
                                 catch_up=True, metadata=metadata), ['x:80'])
        self.assertEqual(len(bird.calls), 3)
        self.assertEqual(metadata['archive_seen'], 2)
        self.assertFalse(self.store.artifact('81', 'capture').exists())
        self.assertFalse(self.store.artifact('90', 'capture').exists())

    def test_catch_up_default_and_explicit_count(self):
        self.config['limits']['capture_count'] = 1
        pages = [{'tweets': [{'id': '101'}], 'nextCursor': 'a'},
                 {'tweets': [{'id': '102'}]}]
        bird = PageBird(copy.deepcopy(pages))
        self.assertEqual(capture(self.vault, self.store, self.config, bird, catch_up=True),
                         ['x:101', 'x:102'])
        bird = PageBird(copy.deepcopy(pages))
        self.assertEqual(capture(self.vault, self.store, self.config, bird,
                                 catch_up=True, capture_count=1), [])
        self.assertEqual(len(bird.calls), 1)
        with self.assertRaises(ValueError):
            capture(self.vault, self.store, self.config, PageBird([]), capture_count=0)

    def test_archive_seen_counts_unique_processed_groups(self):
        (self.vault / 'manifest.json').write_text(json.dumps({'sources': {'x:201': {}, 'x:202': {}}}))
        bird = PageBird([{'tweets': [{'id': '201'}, {'id': '203'}], 'nextCursor': 'a'},
                         {'tweets': [{'id': '201'}, {'id': '202'}]}])
        metadata = {}
        self.assertEqual(capture(self.vault, self.store, self.config, bird,
                                 catch_up=True, metadata=metadata), ['x:203'])
        self.assertEqual(metadata['archive_seen'], 2)

    def test_page_failure_preserves_prior_and_first_page_fails(self):
        for failure in (BirdError('BIRD_FAILED: synthetic failure'), {'tweets': 'invalid'},
                        {'tweets': [{'id': 'invalid'}]}, {'tweets': [], 'nextCursor': 3}):
            with self.subTest(failure=failure):
                bird = PageBird([failure])
                with self.assertRaises(BirdError):
                    capture(self.vault, self.store, self.config, bird)
        bird = PageBird([{'tweets': [{'id': '31'}], 'nextCursor': 'a'},
                         BirdError('BIRD_FAILED: synthetic failure')])
        metadata = {}
        self.assertEqual(capture(self.vault, self.store, self.config, bird, metadata=metadata), ['x:31'])
        self.assertEqual(metadata['capture_warning'], 'BIRD_FAILED: synthetic failure')
        self.assertEqual(self.store.read_artifact('31', 'capture')['tweets'][0]['id'], '31')

    def test_lock_and_stale_takeover(self):
        with self.store.locked():
            with self.assertRaisesRegex(RuntimeError, 'LOCKED'):
                with self.store.locked():
                    pass
        lock = self.store.root / 'lane.lock'
        lock.write_text(json.dumps({'token': 'old', 'created_at': (self.now - timedelta(hours=3)).timestamp()}), encoding='utf-8')
        with self.store.locked():
            self.assertTrue(lock.exists())
        self.assertFalse(lock.exists())

    def test_invalid_tweet_path_rejected(self):
        with self.assertRaises(ValueError):
            self.store.item_dir('../outside')
