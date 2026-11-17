"""Video and redirect coverage using fake processes and acquisition only."""
import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from bookmark_wiki.lane.config import DEFAULTS
from bookmark_wiki.lane.resolve import resolve
from test_lane_resolve import FakeBird, FakeFetch, tweet


class VideoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'media'
        self.config = copy.deepcopy(DEFAULTS)
        self.calls = []
        self.vision_calls = []
        self.bird = FakeBird()
        self.fetch = FakeFetch()
        self.audio = True
        self.transcript = 'spoken evidence'
        self.frame_fail = set()
        self.which = lambda name: '/fake/ffmpeg' if name == 'ffmpeg' else None

    def run_fake(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if args[0] == '/fake/ffmpeg' and '-vn' in args:
            if self.audio:
                Path(args[-1]).write_bytes(b'wave')
            return SimpleNamespace(returncode=0 if self.audio else 1)
        if args[0] == '/fake/ffmpeg':
            index = int(Path(args[-1]).stem.rsplit('-', 1)[-1])
            if index not in self.frame_fail:
                Path(args[-1]).write_bytes(b'jpeg')
            return SimpleNamespace(returncode=1 if index in self.frame_fail else 0)
        if self.transcript is not None:
            Path(args[1]).with_suffix('.txt').write_text(self.transcript, encoding='utf-8')
        return SimpleNamespace(returncode=0 if self.transcript is not None else 1)

    def vision(self, path, prompt):
        self.vision_calls.append((path, prompt))
        return 'UI and text'

    def resolve(self, post, tweets=None):
        return resolve({'source_id': 'x:1', 'tweet_id': '1', 'tweets': tweets or [post]},
                       self.config, bird=self.bird, fetch=self.fetch, vision=self.vision,
                       media_dir=self.directory, which=self.which, run=self.run_fake)

    @staticmethod
    def video(**kw):
        return {'type': 'video', 'url': 'https://x.com/a/status/1/video/1',
                'videoUrl': 'https://video.twimg.com/video.mp4', 'durationMs': 10000, **kw}

    def test_audio_frames_commands_and_truncation(self):
        self.config['limits']['link_chars_for_llm'] = 6
        item_bundle = self.resolve(tweet(media=[self.video()]))
        item = item_bundle['media'][0]
        self.assertEqual(item['status'], 'ok')
        self.assertEqual(item['transcript'], 'spoken')
        self.assertEqual([f['t'] for f in item['frames']], [2, 4, 6, 8])
        self.assertEqual([kw['timeout'] for _, kw in self.calls], [300, 1800, 120, 120, 120, 120])
        audio = self.calls[0][0]
        self.assertEqual(audio[:-1], ['/fake/ffmpeg', '-nostdin', '-v', 'error', '-y', '-i',
                                     'https://video.twimg.com/video.mp4', '-vn', '-ac', '1', '-ar', '16000'])
        whisper = self.calls[1][0]
        self.assertEqual(whisper[0], self.config['media']['whisper_argv'][0])
        self.assertEqual(whisper[2:], ['--model', 'large-v3-turbo', '--output_format', 'txt',
                                       '--output_dir', str(self.directory), '--fp16', 'False'])
        frame = self.calls[2][0]
        self.assertEqual(frame[1:9], ['-nostdin', '-v', 'error', '-y', '-ss', '2.0', '-i',
                                     'https://video.twimg.com/video.mp4'])
        self.assertEqual(frame[9:-1], ['-frames:v', '1', '-vf', "scale='min(1280,iw)':-2"])
        self.assertFalse((self.directory / 'video-1.wav').exists())
        self.assertIn('untrusted', self.vision_calls[0][1])
        reasons = [v['reason'] for v in item_bundle['limits']]
        self.assertEqual(reasons, ['video-1: transcript truncated',
                                   'video-1: only sampled frames viewed, not the full video'])
        self.assertFalse(self.fetch.calls)

    def test_no_audio_unknown_duration_and_failed_frames(self):
        self.audio = False
        self.frame_fail = {2, 4}
        result = self.resolve(tweet(media=[self.video(type='animated_gif', durationMs=None)]))
        item = result['media'][0]
        self.assertEqual(item['status'], 'ok')
        self.assertEqual(item['note'], 'no audio track')
        self.assertEqual(item['transcript'], '')
        self.assertEqual([f['t'] for f in item['frames']], [1, 15])
        self.assertEqual([v['reason'] for v in result['limits']], [
            'video-1: no audio track', 'video-1: some sampled frames unavailable',
            'video-1: only sampled frames viewed, not the full video'])

    def test_transcription_failure_frames_failure_and_cleanup(self):
        self.transcript = None
        self.frame_fail = {1, 2, 3, 4}
        result = self.resolve(tweet(media=[self.video()]))
        self.assertEqual(result['media'][0]['status'], 'not_viewed')
        self.assertFalse((self.directory / 'video-1.wav').exists())
        self.assertTrue(any('transcription failed' in v['reason'] for v in result['limits']))
        self.assertFalse(any('only sampled frames viewed' in v['reason'] for v in result['limits']))

    def test_transcript_only_and_stale_artifacts_not_reused(self):
        self.directory.mkdir(parents=True)
        (self.directory / 'video-1-frame-1.jpg').write_bytes(b'old frame')
        self.frame_fail = {1, 2, 3, 4}
        result = self.resolve(tweet(media=[self.video()]))
        self.assertEqual(result['media'][0]['status'], 'ok')
        self.assertEqual(result['media'][0]['transcript'], 'spoken evidence')
        self.assertEqual(result['media'][0]['frames'], [])
        self.assertEqual([v['reason'] for v in result['limits']],
                         ['video-1: some sampled frames unavailable'])
        (self.directory / 'video-1.wav').write_bytes(b'old audio')
        (self.directory / 'video-1.txt').write_text('old transcript')
        self.audio = False
        result = self.resolve(tweet(media=[self.video()]))
        self.assertEqual(result['media'][0]['status'], 'not_viewed')
        self.assertEqual(result['media'][0]['transcript'], '')
        self.assertFalse((self.directory / 'video-1.wav').exists())

    def test_url_allowlist_duration_caps_and_absent_ffmpeg(self):
        for url in ['http://video.twimg.com/a', 'https://video.twimg.com.evil/a',
                    'https://evil.test/a', 'https://video.twimg.com@evil.test/a', None]:
            with self.subTest(url=url):
                result = self.resolve(tweet(media=[self.video(videoUrl=url)]))
                self.assertEqual(result['media'][0]['status'], 'not_viewed')
                self.assertIn('invalid video URL', result['limits'][0]['reason'])
        self.assertFalse(self.calls)
        result = self.resolve(tweet(media=[self.video(durationMs=1200001)]))
        self.assertEqual(result['limits'][0]['reason'], 'video-1: video longer than limit')
        self.which = lambda name: None
        result = self.resolve(tweet(media=[self.video()]))
        self.assertIn('ffmpeg unavailable', result['limits'][0]['reason'])
        self.which = lambda name: '/fake/ffmpeg'
        self.config['media']['videos_per_item'] = 1
        result = self.resolve(tweet(media=[self.video(), self.video()]))
        self.assertEqual(result['media'][1]['note'], 'video cap exceeded')
        self.assertEqual(len(self.calls), 6)

    def test_roles_and_per_item_cap(self):
        self.config['media']['videos_per_item'] = 5
        self.bird = FakeBird({('replies', '1'): [tweet('3', 'reply', media=[self.video()])]})
        post = tweet(text='link in the comments', media=[self.video()],
                     quotedTweet=tweet('4', 'quote', media=[self.video(type='animated_gif')]))
        result = self.resolve(post, [post, tweet('2', 'chain', media=[self.video()])])
        self.assertEqual([x['found_in'] for x in result['media']],
                         ['post', 'self_reply', 'author_reply', 'quoted'])
        self.assertTrue(all(x['status'] == 'ok' for x in result['media']))

    def test_failed_redirect_to_evidence_suppressed_without_noise(self):
        for ok in [False, True]:
            for target in ['1', '8', '9']:
                with self.subTest(ok=ok, target=target):
                    short = 'https://t.co/abc'
                    self.fetch = FakeFetch({short: SimpleNamespace(ok=ok,
                        final_url='https://x.com/other/status/' + target,
                        reason='robots blocked', body=b'')})
                    self.bird = FakeBird({('read', '9'): tweet('9', 'parent'),
                                          ('replies', '1'): [tweet('2', 'reply')]})
                    post = tweet(text='link in the comments ' + short,
                                 quotedTweet=tweet('8', 'quote'), inReplyToStatusId='9')
                    result = self.resolve(post)
                    self.assertEqual(result['links'], [])
                    self.assertEqual(result['limits'], [])
                    self.assertIn('author_reply', [t['role'] for t in result['thread']])
                    self.assertNotIn(('read', [target, '--json'], 'Profile 2'),
                                     [c for c in self.bird.calls if target != '9'])


if __name__ == '__main__':
    unittest.main()
