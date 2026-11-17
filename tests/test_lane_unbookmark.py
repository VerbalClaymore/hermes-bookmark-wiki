"""Offline tests for operator-gated bookmark removal."""
import argparse
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from bookmark_wiki.lane import config, unbookmark
from bookmark_wiki.lane.bird import Bird
from bookmark_wiki.lane.store import Store


class FakeBird:
    def __init__(self, identity='user_id: 84692934\n', fail=None):
        self.identity, self.fail = identity, fail
        self.calls = []

    def run(self, action, args, *, profile):
        self.calls.append((action, profile))
        return self.identity

    def unbookmark(self, ident, *, profile):
        self.calls.append((ident, profile))
        if ident == self.fail:
            raise RuntimeError('simulated failure')


class UnbookmarkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name)
        config.init(self.vault)
        self.state = Store(self.vault)
        self.addCleanup(self.state.close)
        self.bird = FakeBird()
        self.output = io.StringIO()
        self.sleeps = []
        self.deps = {'bird': self.bird, 'stdin': io.StringIO(unbookmark.PHRASE + '\n'),
                     'stderr': self.output, 'isatty': lambda stream: True,
                     'clock': lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
                     'sleep': self.sleeps.append}

    def item(self, ident, status='done', delivery=None):
        ident = str(ident)
        self.state.put_capture({'source_id': 'x:' + ident, 'tweet_id': ident, 'tweets': []})
        self.state.db.execute('UPDATE items SET status=? WHERE tweet_id=?', (status, ident))
        self.state.db.commit()
        self.state.write_artifact(ident, 'write', {'title': 'Title ' + ident})
        if delivery is None and status == 'done':
            path = self.vault / ('note' + ident)
            path.write_text('saved')
            delivery = {'wiki': {'path': str(path)}, 'bots': []}
        if delivery is not None:
            self.state.write_artifact(ident, 'deliver', delivery)

    def run_lane(self, **kwargs):
        return unbookmark.run(self.vault, deps=self.deps, **kwargs)

    def test_registration(self):
        parser = argparse.ArgumentParser()
        unbookmark.register(parser.add_subparsers(dest='action'))
        args = parser.parse_args(['unbookmark', '--vault', '/v', '--limit', '3', '--dry-run', '--include-skipped'])
        self.assertEqual((args.action, args.vault, args.limit, args.dry_run, args.include_skipped), ('unbookmark', '/v', 3, True, True))

    def test_noninteractive_refused_before_anything(self):
        self.deps['isatty'] = lambda stream: False
        with self.assertRaisesRegex(ValueError, 'confirmation requires an interactive terminal'):
            unbookmark.run(self.vault / 'nonexistent', deps=self.deps)
        self.assertFalse((self.vault / 'nonexistent').exists())
        self.assertEqual(self.bird.calls, [])

    def test_confirmation_requires_exact_phrase(self):
        self.item(1)
        for value in ('delete synced bookmarks\n', ' DELETE SYNCED BOOKMARKS\n', 'DELETE SYNCED BOOKMARKS \n', 'DELETE SYNCED BOOKMARKS extra\n', 'DELETE SYNCED BOOKMARKS\r\n'):
            with self.subTest(value=value):
                self.deps['stdin'] = io.StringIO(value)
                with self.assertRaisesRegex(ValueError, 'UNBOOKMARK_REFUSED'):
                    self.run_lane()
        self.assertFalse(any(call[0] == '1' for call in self.bird.calls))
        self.deps['stdin'] = io.StringIO(unbookmark.PHRASE + '\n')
        result = self.run_lane()
        self.assertEqual([e['source_id'] for e in result['removed']], ['x:1'])
        self.assertIn('Title 1 https://x.com/i/status/1', self.output.getvalue())

    def test_dry_run_no_identity_prompt_or_removal(self):
        self.item(1)
        self.deps['isatty'] = lambda stream: False
        result = self.run_lane(dry_run=True)
        self.assertEqual(len(result['eligible']), 1)
        self.assertEqual(result['removed'], [])
        self.assertEqual(self.bird.calls, [])
        self.assertEqual(self.output.getvalue(), '')

    def test_wrong_identity_refuses_before_prompt(self):
        self.item(1)
        self.bird.identity = 'user_id: 123\n'
        with self.assertRaisesRegex(ValueError, 'UNBOOKMARK_REFUSED'):
            self.run_lane()
        self.assertEqual(self.output.getvalue(), '')
        self.assertEqual(self.bird.calls, [('whoami', 'Profile 1')])

    def test_delivery_disappears_during_confirmation(self):
        self.item(1)
        path = self.vault / 'note1'
        class Input(io.StringIO):
            def readline(self, *args):
                path.unlink()
                return super().readline(*args)
        self.deps['stdin'] = Input(unbookmark.PHRASE + '\n')
        result = self.run_lane()
        self.assertEqual(result['eligible'], [])
        self.assertEqual(result['ineligible'][0]['source_id'], 'x:1')
        self.assertEqual(self.bird.calls, [('whoami', 'Profile 1')])

    def test_first_failure_stops_and_is_retried_after_confirmation(self):
        for i in range(1, 4):
            self.item(i)
        self.bird.fail = '2'
        result = self.run_lane()
        self.assertEqual([x['source_id'] for x in result['removed']], ['x:1'])
        self.assertEqual(result['failed']['source_id'], 'x:2')
        self.assertEqual(self.sleeps, [2])
        self.assertEqual([c[0] for c in self.bird.calls], ['whoami', '1', '2'])
        records = [json.loads(line) for line in Path(result['log']).read_text().splitlines()]
        self.assertEqual([r['source_id'] for r in records], ['x:1', 'x:2'])
        self.assertTrue(records[1]['failed'])
        self.assertEqual(records[1]['reason'], 'simulated failure')
        self.assertEqual([row['source_id'] for row in self.state.db.execute('SELECT source_id FROM unbookmarked')], ['x:1'])
        self.assertEqual([r['source_id'] for r in self.run_lane(dry_run=True)['eligible']], ['x:2', 'x:3'])
        self.bird.fail = None
        self.deps['stdin'] = io.StringIO(unbookmark.PHRASE + '\n')
        second = self.run_lane()
        self.assertEqual([r['source_id'] for r in second['removed']], ['x:2', 'x:3'])
        self.assertEqual([c[0] for c in self.bird.calls].count('2'), 2)
        self.assertEqual([c[0] for c in self.bird.calls].count('1'), 1)
        self.assertEqual(self.run_lane(dry_run=True)['eligible'], [])

    def test_limits(self):
        for i in range(101):
            self.item(i)
        self.assertEqual(len(self.run_lane(dry_run=True)['eligible']), 25)
        self.assertEqual(len(self.run_lane(dry_run=True, limit=100)['eligible']), 100)
        for invalid in (0, -1, 101, True, 1.5):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.run_lane(dry_run=True, limit=invalid)

    def test_eligibility_shapes_and_missing_files(self):
        self.item(1, delivery={'wiki': {'path': str(self.vault / 'missing')}, 'bots': []})
        self.item(2)
        self.item(3, 'duplicate', {'wiki': None, 'bots': [], 'duplicate_of': 'x:2'})
        self.item(4, 'duplicate', {'wiki': None, 'bots': [], 'duplicate_of': 'x:99'})
        saved = self.vault / 'saved.png'
        saved.write_bytes(b'image')
        self.item(5, 'kept', {'wiki': None, 'bots': [], 'keepsake': {'files': [str(saved)], 'index': 'index'}})
        self.item(6, 'kept', {'wiki': None, 'bots': [], 'keepsake': {'files': [str(saved), 'missing'], 'index': 'index'}})
        self.item(7, 'skipped')
        reference = self.vault / 'reference.md'
        reference.write_text('saved')
        self.item(8, 'done', {'wiki': None, 'bots': [{'reference_path': str(reference)}]})
        result = self.run_lane(dry_run=True, include_skipped=True, limit=100)
        self.assertEqual({e['source_id'] for e in result['eligible']}, {'x:2', 'x:3', 'x:5', 'x:7', 'x:8'})
        self.assertEqual({e['source_id'] for e in result['ineligible']}, {'x:1', 'x:4', 'x:6'})
        self.assertTrue(all(e['reason'] for e in result['ineligible']))
        self.assertNotIn('x:7', {e['source_id'] for e in self.run_lane(dry_run=True)['eligible']})

    def test_bird_explicit_method_and_read_only_gate(self):
        calls = []
        def runner(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0)
        bird = Bird(config.DEFAULTS['bird'], runner=runner)
        for method in (bird.run, bird.json):
            with self.assertRaises(ValueError):
                method('unbookmark', ['1'], profile='p')
        for ident in ('../1', '1a', '１', 1):
            with self.assertRaises(ValueError):
                bird.unbookmark(ident, profile='p')
        bird.unbookmark('123', profile='Profile 1')
        self.assertEqual(calls[0][-2:], ['unbookmark', '123'])
        self.assertIn('Profile 1', calls[0])

    def test_sync_paths_do_not_reference_removal(self):
        root = Path(__file__).resolve().parents[1]
        for path in ('bookmark_wiki/lane/pipeline.py', 'bookmark_wiki/lane/capture.py', 'scripts/lane-sync.sh'):
            with self.subTest(path=path):
                self.assertNotIn('unbookmark', (root / path).read_text(encoding='utf-8').lower())
