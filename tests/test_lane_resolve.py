"""Stage 2 contract tests. All acquisition and process calls are fakes."""

import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bookmark_wiki.lane.config import DEFAULTS
from bookmark_wiki.lane.resolve import make_fetch, resolve
from bookmark_wiki.lane.write import validate


PNG = b'\x89PNG\r\n\x1a\nimage'


class FakeBird:
    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls = []

    def json(self, action, args, *, profile):
        self.calls.append((action, args, profile))
        value = self.answers.get((action, args[0]), [])
        if isinstance(value, Exception):
            raise value
        return value


class FakeFetch:
    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls = []

    def __call__(self, url, kind, max_bytes):
        self.calls.append((url, kind, max_bytes))
        value = self.answers.get(url)
        if isinstance(value, Exception):
            raise value
        if isinstance(value, SimpleNamespace):
            return value
        if value is None:
            return SimpleNamespace(ok=False, final_url=url, reason='missing fake', body=b'')
        if isinstance(value, (dict, list)):
            value = json.dumps(value).encode()
        if isinstance(value, str):
            value = value.encode()
        return SimpleNamespace(ok=True, final_url=url, reason='', body=value)


def tweet(ident='1', text='hello', **kwargs):
    return {'id': ident, 'text': text, 'authorId': 'a',
            'author': {'username': 'alice'}, 'createdAt': 'today', **kwargs}


class ResolveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = copy.deepcopy(DEFAULTS)
        self.bird = FakeBird()
        self.fetch = FakeFetch()
        self.vision_calls = []

    def vision(self, path, prompt):
        self.vision_calls.append((path, prompt))
        return 'A chart with text.'

    def resolve(self, post=None, tweets=None, **kwargs):
        post = post or tweet()
        capture = {'source_id': 'x:1', 'tweet_id': '1', 'tweets': tweets or [post]}
        capture.update(kwargs)
        return resolve(capture, self.config, bird=self.bird, fetch=self.fetch,
                       vision=self.vision, media_dir=Path(self.temp.name) / 'media',
                       which=lambda name: None)

    def test_replies_author_link_and_profile(self):
        self.bird = FakeBird({('replies', '1'): [tweet('2', 'https://example.com/a'),
                                                  tweet('3', 'https://evil.test', authorId='b')]})
        self.fetch = FakeFetch({'https://example.com/a': '<title>A</title><p>content</p>'})
        result = self.resolve(tweet(text='Link in the comments'))
        self.assertTrue(result['signals']['points_elsewhere'])
        self.assertEqual(result['thread'][0]['role'], 'author_reply')
        self.assertEqual(result['links'][0]['found_in'], 'author_reply')
        self.assertEqual(self.bird.calls, [('replies', ['1', '--json'], 'Profile 2')])

    def test_elsewhere_patterns(self):
        phrases = ['Links are in the replies', 'see below', 'repo 👇',
                   'a thread 🧵', "comment hi and I'll send", 'code in bio']
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.bird = FakeBird()
                self.assertTrue(self.resolve(tweet(text=phrase))['signals']['points_elsewhere'])
                self.assertEqual(self.bird.calls[0][0], 'replies')

    def test_elsewhere_external_link_suppresses_replies(self):
        url = 'https://example.com/source'
        self.fetch = FakeFetch({url: '<p>source</p>'})
        result = self.resolve(tweet(text='See the replies ' + url))
        self.assertTrue(result['signals']['points_elsewhere'])
        self.assertFalse(self.bird.calls)

    def test_short_link_to_same_conversation_still_checks_replies(self):
        short = 'https://t.co/self'
        reply_url = 'https://example.com/source'
        redirected = SimpleNamespace(ok=True, final_url='https://x.com/alice/status/1',
                                     reason='', body=b'<p>self</p>')
        self.fetch = FakeFetch({short: redirected, reply_url: '<p>source</p>'})
        self.bird = FakeBird({('replies', '1'): [tweet('2', reply_url)]})
        result = self.resolve(tweet(text='Link in the comments ' + short))
        self.assertEqual([link['url'] for link in result['links']], [reply_url])
        self.assertEqual(self.bird.calls[0][0], 'replies')
        self.assertEqual(sum(call[0] == short for call in self.fetch.calls), 1)

    def test_refresh_and_thread_seed_parent_quote(self):
        post = tweet(text='old', inReplyToStatusId='8', quotedTweet=tweet('9', 'quoted'))
        self.bird = FakeBird({('read', '1'): tweet(text='new longer post'),
                              ('thread', '1'): [tweet('1'), tweet('2', 'chain'),
                                                tweet('3', 'other', authorId='b')],
                              ('read', '8'): tweet('8', 'parent', authorId='b')})
        result = self.resolve(post, needs_refresh=True)
        self.assertEqual(result['post']['text'], 'new longer post')
        self.assertEqual([t['role'] for t in result['thread']], ['self_reply', 'quoted', 'parent'])
        self.assertIn(('thread', ['1', '--max-pages', '2', '--json'], 'Profile 2'), self.bird.calls)

    def test_truncated_refresh_keeps_longer_and_chain_no_extra_call(self):
        post = tweet(text='long truncated text … https://t.co/abc', hasSelfReplies=True)
        self.bird = FakeBird({('read', '1'): tweet(text='short')})
        self.fetch = FakeFetch({'https://t.co/abc': '<p>target</p>'})
        result = self.resolve(post, tweets=[post, tweet('2', 'chain')])
        self.assertEqual(result['post']['text'], post['text'])
        self.assertEqual([c[0] for c in self.bird.calls], ['read'])

    def test_missing_self_chain_uses_thread(self):
        self.bird = FakeBird({('thread', '1'): [tweet('2', 'reply'), tweet('3', 'ignore', authorId='b')]})
        result = self.resolve(tweet(hasSelfReplies=True))
        self.assertEqual(result['thread'], [{'id': '2', 'text': 'reply', 'role': 'self_reply'}])

    def test_bird_failures_produce_limits(self):
        self.bird = FakeBird({('thread', '1'): RuntimeError('unavailable'),
                              ('read', '7'): RuntimeError('parent unavailable')})
        result = self.resolve(tweet(hasSelfReplies=True, inReplyToStatusId='7'))
        self.assertEqual(len(result['limits']), 2)

    def test_github_repo_api_readme_fallback_and_truncation(self):
        url = 'https://github.com/owner/repo'
        base = 'https://raw.githubusercontent.com/owner/repo/main/'
        self.fetch = FakeFetch({url: '<p>github</p>',
            'https://api.github.com/repos/owner/repo': {'description': 'desc', 'default_branch': 'main',
                'stargazers_count': 42, 'license': {'name': 'MIT'}, 'pushed_at': 'today'},
            base + 'readme.md': 'Detailed README'})
        result = self.resolve(tweet(text=url))
        link = result['links'][0]
        self.assertEqual(link['kind'], 'github_repo')
        self.assertEqual(link['status'], 'ok')
        self.assertIn('Stars: 42', link['text'])
        self.assertIn('Detailed README', link['text'])
        self.assertIn(base + 'README.md', [x[0] for x in self.fetch.calls])
        self.config['limits']['link_chars_for_llm'] = 5
        link = self.resolve(tweet(text=url))['links'][0]
        self.assertTrue(link['truncated'])
        self.assertGreater(link['chars'], 5)

    def test_github_blob(self):
        url = 'https://github.com/a/b/blob/main/src/file.py'
        self.fetch = FakeFetch({url: '<p>blob</p>',
            'https://api.github.com/repos/a/b': {'default_branch': 'main'},
            'https://raw.githubusercontent.com/a/b/main/src/file.py': 'print(1)'})
        link = self.resolve(tweet(text=url))['links'][0]
        self.assertEqual(link['status'], 'ok')
        self.assertIn('print(1)', link['text'])

    def test_html_extract_and_failed_fetch(self):
        good, bad = 'https://example.com/a', 'https://example.com/b'
        self.fetch = FakeFetch({good: '<title>Title</title><nav>BAD</nav><h1>Heading</h1>'
                                     '<p>Paragraph <b>bold</b></p><script>BAD</script>'
                                     '<ul><li>Item</li></ul><pre>code</pre><p>' + 'x' * 200 + '</p><footer>BAD</footer>'})
        result = self.resolve(tweet(text=good + ' ' + bad))
        link = result['links'][0]
        self.assertEqual(link['title'], 'Title')
        for word in ('Heading', 'Paragraph', 'bold', 'Item', 'code'):
            self.assertIn(word, link['text'])
        self.assertNotIn('BAD', link['text'])
        self.assertEqual(result['links'][1]['status'], 'failed')
        self.assertIn({'scope': 'link', 'reason': bad + ': could not be fetched (missing fake)'}, result['limits'])

    def test_thin_html_boundary_and_citation(self):
        url = 'https://claude.ai/artifact/example'
        for count in (0, 15, 199, 200):
            with self.subTest(count=count):
                self.fetch = FakeFetch({url: '<p>' + 'a' * count + '</p>'})
                bundle = self.resolve(tweet(text=url))
                link = bundle['links'][0]
                self.assertEqual(link['status'], 'ok' if count >= 200 else 'failed')
                if count < 200:
                    self.assertEqual(link['note'], 'page returned no readable text (likely needs JavaScript)')
                    self.assertEqual(bundle['limits'][0]['reason'], url + ': ' + link['note'])
                    note = {'title': 'Test', 'short_answer': 'Answer [1] [2].',
                            'explanation': 'Fact [1].', 'key_points': ['Point [1].'],
                            'try_it': [], 'limits': ['Unread'],
                            'sources': [{'n': 1, 'label': 'Post', 'url': bundle['post']['url']},
                                        {'n': 2, 'label': 'Page', 'url': url}]}
                    with self.assertRaises(ValueError):
                        validate(note, bundle)

    def test_github_reserved_paths_use_html(self):
        for name in ('sponsors', 'orgs', 'topics', 'features', 'marketplace', 'settings', 'login', 'about'):
            with self.subTest(name=name):
                url = 'https://github.com/' + name.upper() + '/example'
                self.fetch = FakeFetch({url: '<p>' + 'a' * 200 + '</p>'})
                link = self.resolve(tweet(text=url))['links'][0]
                self.assertEqual((link['kind'], link['status']), ('html', 'ok'))
                self.assertEqual([c[0] for c in self.fetch.calls], [url])

    def test_fetch_exception_wording(self):
        url = 'https://example.com/fail'
        self.fetch = FakeFetch({url: OSError('remote access response')})
        result = self.resolve(tweet(text=url))
        self.assertEqual(result['limits'][0]['reason'], url + ': could not be fetched (remote access response)')
        image = 'https://image.test/unavailable'
        self.fetch = FakeFetch({image: OSError('remote access response')})
        result = self.resolve(tweet(media=[{'type': 'photo', 'url': image}]))
        self.assertEqual(result['limits'][0]['reason'], image + ': could not be fetched (remote access response)')

    def test_redirect_dedupe_conversation_and_cap(self):
        one, two = 'https://t.co/one', 'https://t.co/two'
        final = 'https://example.com/article'
        response = SimpleNamespace(ok=True, final_url=final, reason='', body=b'<p>article</p>')
        self.fetch = FakeFetch({one: response, two: response, 'https://example.com/extra': '<p>extra</p>'})
        self.config['limits']['links_per_item'] = 1
        post = tweet(text=' '.join([one, two, 'https://x.com/alice/status/1/photo/1',
                                    'https://example.com/extra']))
        result = self.resolve(post)
        self.assertEqual(len(result['links']), 1)
        self.assertEqual(result['links'][0]['final_url'], final)
        self.assertTrue(any('cap' in x['reason'] for x in result['limits']))

    def test_x_status_link(self):
        url = 'https://x.com/bob/status/50'
        self.bird = FakeBird({('read', '50'): tweet('50', 'linked content')})
        link = self.resolve(tweet(text=url))['links'][0]
        self.assertEqual(link['kind'], 'x_status')
        self.assertEqual(link['text'], 'linked content')

    def test_bracketed_and_malformed_links_do_not_fail_item(self):
        # Real case: quoted text "[https://reference-site.com]." raised
        # "Invalid IPv6 URL" and failed the whole bookmark.
        text = ('see [https://example.com/a]. and (https://example.com/b), '
                'also {https://example.com/c} and https://[bad')
        result = self.resolve(tweet(text=text))
        urls = [link['url'] for link in result['links']]
        self.assertEqual(urls, ['https://example.com/a', 'https://example.com/b', 'https://example.com/c'])

    def test_pdf_not_read_and_arxiv_rewrite(self):
        pdf = 'https://example.com/file.pdf'
        arxiv = 'https://arxiv.org/pdf/1234.5678.pdf'
        self.fetch = FakeFetch({'https://arxiv.org/abs/1234.5678': '<title>Paper</title><p>abstract' + 'x' * 200 + '</p>'})
        result = self.resolve(tweet(text=pdf + ' ' + arxiv))
        self.assertEqual(result['links'][0]['status'], 'not_read')
        self.assertFalse(any(call[0] == pdf for call in self.fetch.calls))
        self.assertEqual(result['links'][1]['kind'], 'html')
        self.assertEqual(result['links'][1]['final_url'], 'https://arxiv.org/abs/1234.5678')
        self.assertIn('abstract', result['links'][1]['text'])

    def test_quote_and_parent_links_and_quote_media(self):
        quoted = tweet('8', 'https://example.com/quoted', media=[
            {'type': 'photo', 'url': 'https://image.test/q'}])
        parent = tweet('9', 'https://example.com/parent')
        self.bird = FakeBird({('read', '9'): parent})
        self.fetch = FakeFetch({'https://example.com/quoted': '<p>quote</p>',
                                'https://example.com/parent': '<p>parent</p>',
                                'https://image.test/q': PNG})
        result = self.resolve(tweet(quotedTweet=quoted, inReplyToStatusId='9'))
        self.assertEqual([link['found_in'] for link in result['links']], ['quoted', 'parent'])
        self.assertEqual(result['media'][0]['found_in'], 'quoted')
        self.assertEqual(result['media'][0]['status'], 'ok')

    def test_youtube_no_transcript(self):
        url = 'https://youtu.be/abcdefghijk'
        api = 'https://www.youtube.com/oembed?url=' + url + '&format=json'
        self.fetch = FakeFetch({api: {'title': 'Video', 'author_name': 'Author'}})
        result = self.resolve(tweet(text=url))
        self.assertEqual(result['links'][0]['title'], 'Video')
        self.assertTrue(any('video not transcribed' in v['reason'] for v in result['limits']))

    def test_youtube_caption_runner_fake(self):
        url = 'https://youtu.be/abcdefghijk'
        api = 'https://www.youtube.com/oembed?url=' + url + '&format=json'
        self.fetch = FakeFetch({api: {'title': 'Video', 'author_name': 'Author'}})
        def run(args, **kwargs):
            path = Path(args[args.index('-o') + 1] + '.en.vtt')
            path.write_text('WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n<c>Hello</c>\n', encoding='utf-8')
            return SimpleNamespace(returncode=0)
        capture = {'source_id': 'x:1', 'tweet_id': '1', 'tweets': [tweet(text=url)]}
        result = resolve(capture, self.config, bird=self.bird, fetch=self.fetch,
                         vision=self.vision, media_dir=Path(self.temp.name),
                         which=lambda name: '/fake/yt-dlp', run=run)
        self.assertIn('Hello', result['links'][0]['text'])
        self.assertEqual(result['limits'], [])

    def test_photo_video_and_image_cap(self):
        photo = {'type': 'photo', 'url': 'https://image.test/1'}
        video = {'type': 'video', 'url': 'https://image.test/2', 'durationMs': 1200}
        self.fetch = FakeFetch({photo['url']: PNG})
        self.config['limits']['images_per_item'] = 1
        result = self.resolve(tweet(media=[photo, video, photo]))
        self.assertEqual([m['status'] for m in result['media']], ['ok', 'not_viewed', 'not_viewed'])
        self.assertEqual(result['media'][1]['duration_ms'], 1200)
        self.assertEqual(len(self.vision_calls), 1)
        self.assertEqual(Path(self.vision_calls[0][0]).read_bytes(), PNG)
        self.assertEqual(len(result['limits']), 2)
        self.assertFalse(any(c[0] == video['url'] for c in self.fetch.calls))

    def test_invalid_image_and_vision_failure(self):
        photo = {'type': 'photo', 'url': 'https://image.test/1'}
        self.fetch = FakeFetch({photo['url']: b'GIF89a'})
        result = self.resolve(tweet(media=[photo]))
        self.assertEqual(result['media'][0]['status'], 'not_viewed')
        self.assertTrue(result['limits'])
        self.fetch = FakeFetch({photo['url']: PNG})
        def fail(path, prompt):
            raise RuntimeError('vision failed')
        self.vision = fail
        result = self.resolve(tweet(media=[photo]))
        self.assertIn('vision failed', result['limits'][0]['reason'])

    def test_fetch_factory_store_methods(self):
        stores = []
        def fake(url, *, store, kind, max_bytes):
            stores.append(store)
            with store.locked():
                self.assertFalse(store.acquisition_stopped())
                store.reserve('acquisition', {})
                store.reserve('http', {})
                store.stop_acquisition('429')
            return (url, kind, max_bytes)
        with patch('bookmark_wiki.lane.resolve.safe_fetch.fetch', fake):
            fetch = make_fetch(self.config)
            self.assertEqual(fetch('https://example.com', 'json', 123), ('https://example.com', 'json', 123))
            fetch('https://example.com', 'image', 42)
        self.assertIs(stores[0], stores[1])
        self.assertEqual(stores[0].calls, 4)


if __name__ == '__main__':
    unittest.main()
