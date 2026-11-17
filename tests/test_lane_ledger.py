"""Offline source ledger regression tests."""
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from bookmark_wiki.lane import ledger
from bookmark_wiki.lane.store import Store



class ClipTests(unittest.TestCase):
    def test_takeaway_cut_at_word_boundary(self):
        from bookmark_wiki.lane.ledger import _clip
        self.assertEqual(_clip('short  text'), 'short text')
        long = ' '.join(['word'] * 60)
        clipped = _clip(long)
        self.assertLessEqual(len(clipped), 200)
        self.assertTrue(clipped.endswith('word...'))
        self.assertEqual(_clip('x' * 300), 'x' * 197 + '...')

class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = datetime(2026, 11, 16, tzinfo=timezone.utc)
        self.clock = lambda: self.now
        self.store = Store(self.root, clock=self.clock)
        self.addCleanup(self.store.close)
        ledger.ensure(self.store)

    def bundle(self, ident, links=()):
        return {'source_id': 'x:' + ident, 'post': {'id': ident, 'url': 'https://x.com/alice/status/' + ident, 'author': {'username': 'alice'}}, 'thread': [], 'links': list(links)}

    def delivered(self, ident, bundle, wiki=None):
        self.store.put_capture({'source_id': 'x:' + ident, 'tweet_id': ident, 'tweets': [{}]})
        self.store.write_artifact(ident, 'resolve', bundle)
        self.store.write_artifact(ident, 'write', {'title': 'Title ' + ident, 'short_answer': 'A takeaway', 'key_points': ['fact']})
        self.store.write_artifact(ident, 'deliver', {'wiki': {'path': str(wiki)} if wiki else None, 'bots': [{'profile': 'bot', 'topic_slug': 'topic', 'reference_path': '/reference'}]})
        self.store.db.execute("UPDATE items SET status='done' WHERE source_id=?", ('x:' + ident,))
        self.store.db.commit()

    def test_keys_normalize_all_and_extract_identifiers(self):
        b = self.bundle('1', [
            {'status': 'ok', 'final_url': 'https://GitHub.COM/Owner/Repo/tree/main/?utm_source=a&ref=b&keep=c#fragment'},
            {'status': 'ok', 'final_url': 'https://arxiv.org/pdf/2401.12345.pdf'},
            {'status': 'ok', 'final_url': 'https://www.youtube.com/watch?v=abc&t=3'},
            {'status': 'ok', 'final_url': 'https://youtu.be/abc'},
            {'status': 'ok', 'final_url': 'https://youtube.com/shorts/def'},
            {'status': 'failed', 'url': 'https://twitter.com/user/status/55'},
        ])
        b['thread'] = [{'id': '44', 'role': 'quoted'}, {'id': '45', 'role': 'parent'}, {'id': '46', 'role': 'self_reply'}]
        found = ledger.keys(b)
        for value in ('x:1', 'x:44', 'x:45', 'x:55', 'github:owner/repo', 'arxiv:2401.12345', 'yt:abc', 'yt:def', 'https://github.com/Owner/Repo/tree/main?keep=c'):
            self.assertIn(value, found)
        self.assertNotIn('x:46', found)
        self.assertEqual(len(found), len(set(found)))

    def test_backfill_coverage_topics_and_recent(self):
        link = {'status': 'ok', 'final_url': 'https://example.org/article'}
        b = self.bundle('1', [link])
        self.delivered('1', b)
        self.delivered('2', self.bundle('2'))
        ledger.ensure(self.store)
        coverage = ledger.prior_coverage(self.store, self.bundle('3', [link]))
        self.assertEqual(coverage['notes'][0]['source_id'], 'x:1')
        self.assertEqual(coverage['notes'][0]['bots'][0]['profile'], 'bot')
        self.assertIn('https://example.org/article', coverage['known_keys'])
        self.assertIn('x:3', coverage['new_keys'])
        self.assertEqual(len(ledger.recent_notes(self.store)), 2)
        self.assertEqual(set(ledger.recent_notes(self.store)[0]), {'source_id', 'title', 'short_answer'})
        self.assertEqual(len(ledger.topic_sources(self.store, 'bot', 'topic')), 3)
        ledger.ensure(self.store)
        self.assertEqual(len(ledger.topic_sources(self.store, 'bot', 'topic')), 3)

    def test_recent_and_coverage_are_bounded(self):
        link = {'status': 'ok', 'final_url': 'https://example.org/article'}
        for ident in range(65):
            self.delivered(str(ident), self.bundle(str(ident), [link]))
        self.store.write_artifact('64', 'write', {'title': 'Large', 'short_answer': 'Hello\n' + 'z' * 300,
                                                   'key_points': ['p' * 400] * 10})
        ledger.ensure(self.store)
        recent = ledger.recent_notes(self.store)
        self.assertEqual(len(recent), 60)
        large = next(note for note in recent if note['source_id'] == 'x:64')
        self.assertEqual(large['short_answer'], ('Hello ' + 'z' * 300)[:160])
        self.assertEqual(set(large), {'source_id', 'title', 'short_answer'})
        coverage = ledger.prior_coverage(self.store, self.bundle('999', [link]), limit=65)
        large = next(note for note in coverage['notes'] if note['source_id'] == 'x:64')
        self.assertEqual(large['key_points'], ['p' * 300] * 8)

    def test_seen_again_idempotent_and_symlink_safe(self):
        note = self.root / 'wiki/topics/note.md'
        note.parent.mkdir(parents=True)
        note.write_text('# Note\n')
        self.delivered('1', self.bundle('1'), note)
        b = self.bundle('2')
        self.assertEqual(ledger.seen_again(self.root, self.store.item_dir('2'), self.store, 'x:1', b, self.clock), str(note))
        ledger.seen_again(self.root, self.store.item_dir('2'), self.store, 'x:1', b, self.clock)
        self.assertEqual(note.read_text().count('Seen again:'), 1)
        self.assertIn('[@alice](https://x.com/alice/status/2)', note.read_text())
        outside = self.root / 'outside.md'
        outside.write_text('safe')
        note.unlink()
        note.symlink_to(outside)
        self.assertIsNone(ledger.seen_again(self.root, self.store.item_dir('2'), self.store, 'x:1', b, self.clock))
        self.assertEqual(outside.read_text(), 'safe')
