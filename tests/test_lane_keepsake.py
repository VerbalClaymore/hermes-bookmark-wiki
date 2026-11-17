"""Keepsake tests use injected fetch and process fakes only."""
import copy
from datetime import datetime, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from bookmark_wiki.lane.config import DEFAULTS
from bookmark_wiki.lane.keepsake import keep

PNG = b'\x89PNG\r\n\x1a\nimage'


class KeepsakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = copy.deepcopy(DEFAULTS)
        self.post = {'id': '123', 'authorId': 'a', 'author': {'username': 'Alice'}, 'media': []}
        self.capture = {'tweet_id': '123', 'tweets': [self.post]}
        self.bundle = {'source_id': 'x:123'}
        self.triage = {'core': {'summary': 'A funny picture'}}
        self.calls = []
        self.body = PNG
        self.redirect = None
        self.video_bytes = b'mp4 data'

    def fetch(self, url, kind, limit):
        self.calls.append(('fetch', url, kind, limit))
        return SimpleNamespace(ok=True, body=self.body, final_url=self.redirect or url, reason='')

    def fake_run(self, args, **kwargs):
        self.calls.append(('run', args, kwargs))
        Path(args[-1]).write_bytes(self.video_bytes)
        return SimpleNamespace(returncode=0)

    def keep(self):
        return keep(self.root / 'item', self.capture, self.bundle, self.triage, self.config, self.root / 'vault',
                    fetch=self.fetch, run=self.fake_run, which=lambda name: '/fake/ffmpeg',
                    clock=lambda: datetime(2026, 1, 2, tzinfo=timezone.utc))

    def test_image_index_and_idempotent_retry(self):
        self.post['media'] = [{'type': 'photo', 'url': 'https://pbs.twimg.com/media/x'}]
        index = self.root / 'vault/wiki/index.md'
        index.parent.mkdir(parents=True)
        index.write_text('# Wiki\n\n## Bookmarks (lean lane)\n\n- prior\n\n## Limits\n\nkeep\n')
        result = self.keep()
        path = Path(result['files'][0])
        self.assertEqual(path.name, '123-Alice-1.png')
        self.assertEqual(path.read_bytes(), PNG)
        text = Path(result['index']).read_text()
        self.assertIn('2026-01-02 A funny picture by @Alice: [post](https://x.com/Alice/status/123) [file 1]', text)
        self.assertIn('../media/keepsakes/123-Alice-1.png', text)
        first = index.read_text()
        self.triage['core']['summary'] = 'changed'
        self.assertEqual(result, self.keep())
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(Path(result['index']).read_text(), text)
        self.assertEqual(index.read_text(), first)
        self.assertLess(first.index('[Keepsakes]'), first.index('## Limits'))
        self.assertEqual(first.count('[Keepsakes]'), 1)

    def test_video_variants_and_same_author_only(self):
        self.post['media'] = [{'type': 'animated_gif', 'videoUrl': 'https://video.twimg.com/default.mp4',
                               'variants': [{'url': 'https://video.twimg.com/low.mp4', 'bitrate': 100},
                                            {'url': 'https://video.twimg.com/high.mp4', 'bitrate': 200}]}]
        self.capture['tweets'] += [{'id': '456', 'authorId': 'other', 'media': [{'type': 'photo', 'url': 'https://pbs.twimg.com/other'}]},
                                   {'id': '789', 'authorId': 'a', 'media': [{'type': 'photo', 'url': 'https://pbs.twimg.com/self'}]}]
        result = self.keep()
        self.assertEqual(len(result['files']), 2)
        cmd = self.calls[0][1]
        self.assertIn('https://video.twimg.com/high.mp4', cmd)
        self.assertEqual(cmd[cmd.index('-c') + 1], 'copy')
        self.assertEqual(self.calls[1][1], 'https://pbs.twimg.com/self')

    def test_quoted_media_after_own_media_even_from_another_author(self):
        self.post['media'] = [{'type': 'photo', 'url': 'https://pbs.twimg.com/own'}]
        self.post['quotedTweet'] = {'id': '456', 'author': {'username': 'Bob'}, 'media': [
            {'type': 'video', 'videoUrl': 'https://video.twimg.com/quoted.mp4'}]}
        result = self.keep()
        self.assertEqual([Path(p).name for p in result['files']], ['123-Alice-1.png', '123-Alice-2.mp4'])
        self.assertEqual(self.calls[0][1], 'https://pbs.twimg.com/own')
        self.assertIn('https://video.twimg.com/quoted.mp4', self.calls[1][1])

    def test_quote_only_media_and_host_and_duration_limits(self):
        quoted = {'id': '456', 'author': {'username': 'Bob'}, 'media': [
            {'type': 'video', 'videoUrl': 'https://evil.test/video.mp4'}]}
        self.post['quotedTweet'] = quoted
        with self.assertRaisesRegex(ValueError, 'invalid video URL'):
            self.keep()
        quoted['media'][0]['videoUrl'] = 'https://video.twimg.com/video.mp4'
        quoted['media'][0]['durationMs'] = (self.config['media']['max_video_seconds'] + 1) * 1000
        with self.assertRaisesRegex(ValueError, 'video longer than limit'):
            self.keep()
        quoted['media'][0]['durationMs'] = 1
        self.assertEqual(len(self.keep()['files']), 1)

    def test_bad_urls_redirects_and_limits(self):
        for url in ['http://pbs.twimg.com/a', 'https://pbs.twimg.com.evil/a', 'https://u@pbs.twimg.com/a', 'https://pbs.twimg.com:444/a']:
            with self.subTest(url=url):
                self.post['media'] = [{'type': 'photo', 'url': url}]
                with self.assertRaises(ValueError): self.keep()
        self.post['media'] = [{'type': 'photo', 'url': 'https://pbs.twimg.com/a'}]
        self.redirect = 'https://evil.test/a'
        with self.assertRaises(ValueError): self.keep()
        self.redirect = None
        self.config['limits']['keepsake_file_bytes'] = 4
        with self.assertRaises(ValueError): self.keep()
        self.assertEqual(self.calls[-1][-1], 4)

    def test_video_size_duration_and_cleanup(self):
        self.post['media'] = [{'type': 'video', 'videoUrl': 'https://video.twimg.com/v', 'durationMs': 1200001}]
        with self.assertRaises(ValueError): self.keep()
        self.assertFalse(self.calls)
        self.post['media'][0]['durationMs'] = 1
        self.config['limits']['keepsake_file_bytes'] = 4
        with self.assertRaises(ValueError): self.keep()
        self.assertFalse(list((self.root / 'vault/media/keepsakes').glob('*')))

    def test_no_media_and_symlink_rejected(self):
        with self.assertRaisesRegex(ValueError, '^KEEPSAKE_FAILED: no downloadable media$'): self.keep()
        self.post['media'] = [{'type': 'image', 'url': 'https://pbs.twimg.com/a'}]
        (self.root / 'vault').mkdir()
        outside = self.root / 'outside'
        outside.mkdir()
        (self.root / 'vault/media').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'): self.keep()
        self.assertFalse(list(outside.iterdir()))


if __name__ == '__main__':
    unittest.main()
