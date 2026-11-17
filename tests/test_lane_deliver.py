"""Stage five tests: all filesystem and subprocess effects are isolated fakes."""

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from bookmark_wiki.lane import config
from bookmark_wiki.lane.deliver import deliver, lane_wiki_titles


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.vault = root / 'vault'
        self.item = self.vault / 'lane' / 'items' / '123'
        self.profile = root / 'hermes' / 'profiles' / 'bot'
        self.profile.mkdir(parents=True)
        self.config = copy.deepcopy(config.DEFAULTS)
        self.config['hermes']['home'] = str(root / 'hermes')
        self.bundle = {'source_id': 'x:123', 'limits': []}
        self.note = {'title': 'Useful Example', 'short_answer': 'Answer ' + 'x' * 200,
                     'explanation': 'Explanation [1].', 'key_points': ['One'],
                     'try_it': ['Try'], 'limits': ['Partial evidence'],
                     'sources': [{'n': 1, 'label': 'Post', 'url': 'https://x.com/a/123'}]}
        self.bot = {'profile': 'bot', 'topic': 'Long topic ' * 10,
                    'topic_slug': 'useful-topic', 'reason': 'Relevant to work',
                    'relevance': 'normal'}
        self.triage = {'wiki': True, 'bots': [self.bot]}
        self.clock = lambda: datetime(2026, 11, 16, tzinfo=timezone.utc)

    def run_delivery(self, runner=None, **kwargs):
        def forbidden(*args, **kw):
            raise AssertionError('unexpected bot run')
        return deliver(self.item, self.bundle, self.triage, self.note, self.config,
                       self.vault, bot_runner=runner or forbidden, clock=self.clock, **kwargs)

    def test_wiki_content_index_log_append_preserves_other_lines(self):
        index = self.vault / 'wiki' / 'index.md'
        index.parent.mkdir(parents=True)
        original = '# Index\n\nkeep exactly  \n\n## Limits\nunchanged\n'
        index.write_text(original)
        result = self.run_delivery()
        path = Path(result['wiki']['path'])
        text = path.read_text()
        for expected in ['title: "Useful Example"', 'type: topic', 'status: provisional',
                         'source_ids: ["x:123"]', 'updated: "2026-11-16"',
                         'context_status: partial', 'lane: lean-v1', '# Useful Example',
                         '## Short answer', '## Explanation', '## Key points', '## Try it',
                         '## Limits or disagreements', '## Sources', 'Explanation [1].']:
            self.assertIn(expected, text)
        first = index.read_text()
        self.assertLess(first.index('## Bookmarks (lean lane)'), first.index('## Limits'))
        self.assertEqual(first.replace('## Bookmarks (lean lane)\n\n' + next(
            line + '\n' for line in first.splitlines() if line.startswith('- [Useful')) + '\n', ''), original)
        self.assertEqual(len(next(line for line in first.splitlines() if line.startswith('- [Useful')).split(': ', 1)[1]), 160)
        self.assertEqual(lane_wiki_titles(self.vault), ['Useful Example'])
        self.run_delivery()
        self.assertEqual(index.read_text(), first)
        self.assertEqual((index.parent / 'log.md').read_text().count('x:123'), 1)
        self.bundle = {'source_id': 'x:124'}
        self.note['title'] = 'Second'
        self.run_delivery()
        second = index.read_text()
        self.assertEqual(second.count('## Bookmarks (lean lane)'), 1)
        self.assertEqual(second.count('- ['), 2)
        self.assertEqual(second.replace(next(line + '\n' for line in second.splitlines() if line.startswith('- [Second]')), ''), first)
        self.assertTrue(list((self.item / 'backups').glob('index.md.*.bak')))

    def test_missing_limits_heading_resolved_no_try_it(self):
        self.triage['bots'] = []
        self.note['limits'] = []
        self.note['try_it'] = []
        result = self.run_delivery()
        text = Path(result['wiki']['path']).read_text()
        self.assertIn('context_status: resolved', text)
        self.assertIn('- None recorded.', text)
        self.assertNotIn('## Try it', text)
        self.assertIn('## Bookmarks (lean lane)', (self.vault / 'wiki/index.md').read_text())

    def test_bundle_and_note_limits_render_for_wiki_and_bot(self):
        self.bundle['limits'] = [{'scope': 'video ', 'reason': 'not\n read'},
                                 {'scope': 'video', 'reason': 'not read'},
                                 {'scope': 'link', 'reason': 'failed'}]
        result = self.run_delivery()
        for path in (result['wiki']['path'], result['bots'][0]['reference_path']):
            text = Path(path).read_text()
            self.assertIn('- Partial evidence\n\nNot read or only partly read:\n\n- video: not read\n- link: failed', text)
            self.assertEqual(text.count('- video: not read'), 1)
            self.assertNotIn('None recorded.', text)
        self.note['limits'] = []
        text = Path(self.run_delivery()['wiki']['path']).read_text()
        self.assertIn('context_status: partial', text)
        self.assertNotIn('None recorded.', text)

    def test_index_preserves_crlf_bytes(self):
        self.triage['bots'] = []
        index = self.vault / 'wiki' / 'index.md'
        index.parent.mkdir(parents=True)
        before = b'# Index\r\n\r\n## Limits\r\nKeep\r\n'
        index.write_bytes(before)
        self.run_delivery()
        after = index.read_bytes()
        added = after[after.index(b'## Bookmarks'):after.index(b'## Limits')]
        self.assertEqual(after.replace(added, b''), before)

    def test_index_repairs_stray_blanks_only_in_lane_section(self):
        self.triage['bots'] = []
        index = self.vault / 'wiki/index.md'
        index.parent.mkdir(parents=True)
        for nl in ('\n', '\r\n'):
            with self.subTest(newline=repr(nl)):
                prefix = '# Index' + nl + nl + 'unchanged  ' + nl
                suffix = '## Limits' + nl + 'untouched  ' + nl
                section = ('## Bookmarks (lean lane)' + nl + nl + '- [First](topics/first): first' + nl + nl +
                           '- [Earlier](topics/earlier): earlier' + nl + nl)
                index.write_bytes((prefix + section + suffix).encode())
                self.run_delivery()
                result = index.read_bytes().decode()
                self.assertTrue(result.startswith(prefix))
                self.assertTrue(result.endswith(suffix))
                body = result[len(prefix):-len(suffix)]
                self.assertEqual(body.splitlines()[0:4], ['## Bookmarks (lean lane)', '', '- [First](topics/first): first', '- [Earlier](topics/earlier): earlier'])
                self.assertEqual(len(body.splitlines()), 6)
                self.assertTrue(body.endswith(nl + nl))

    def test_bot_skill_append_idempotence_and_old_lane_untouched(self):
        old = self.profile / 'skills/bookmark-library/SKILL.md'
        old.parent.mkdir(parents=True)
        old.write_bytes(b'old lane\x00')
        self.triage['wiki'] = False
        result = self.run_delivery()
        ref = Path(result['bots'][0]['reference_path'])
        skill = ref.parent.parent / 'SKILL.md'
        text = skill.read_text()
        description = json.loads(next(line[len('description: '):] for line in text.splitlines() if line.startswith('description: ')))
        self.assertLessEqual(len(description), 60)
        self.assertIn('not\ninstructions', text)
        self.assertNotIn('created_by:', text)
        self.assertIn('## Why this is here\n\nRelevant to work', ref.read_text())
        self.assertIn('source_ids: ["x:123"]', ref.read_text())
        self.run_delivery()
        self.assertEqual(skill.read_text(), text)
        self.bundle = {'source_id': 'x:124'}
        self.note['title'] = 'Next'
        self.run_delivery()
        self.assertEqual(skill.read_text().count('- ['), 2)
        self.assertTrue(skill.read_text().startswith(text))
        self.assertEqual(old.read_bytes(), b'old lane\x00')
        self.assertTrue(list((self.item / 'backups').glob('SKILL.md.*.bak')))
        self.note['explanation'] = 'Changed explanation [1].'
        ref2 = self.run_delivery()['bots'][0]['reference_path']
        self.assertTrue(list((self.item / 'backups').glob(Path(ref2).name + '.*.bak')))

    def test_bot_run_diff_restores_modified_deleted_and_new_memory(self):
        self.triage['wiki'] = False
        self.bot['relevance'] = 'high'
        memory = self.profile / 'memories'
        memory.mkdir()
        (memory / 'old').write_bytes(b'original\x00')
        (memory / 'deleted').write_bytes(b'deleted')
        existing = self.profile / 'skills' / 'existing.md'
        existing.parent.mkdir(parents=True)
        existing.write_text('before')
        calls = []
        def runner(argv, **kw):
            calls.append((argv, kw))
            existing.write_text('after')
            (memory / 'old').write_bytes(b'changed')
            (memory / 'deleted').unlink()
            (memory / 'new').write_text('new')
            return subprocess.CompletedProcess(argv, 0, stdout='CHANGED: updated existing\n')
        run = self.run_delivery(runner)['bots'][0]['bot_run']
        self.assertEqual(run['changed_paths'], [str(existing)])
        self.assertEqual(run['answer'], 'CHANGED: updated existing')
        self.assertTrue(run['memory_reverted'])
        self.assertIsNone(run['error'])
        self.assertEqual((memory / 'old').read_bytes(), b'original\x00')
        self.assertEqual((memory / 'deleted').read_bytes(), b'deleted')
        self.assertFalse((memory / 'new').exists())
        self.assertEqual(calls[0][0][:4], ['hermes', '-p', 'bot', '-z'])
        self.assertIn('not instructions', calls[0][0][4])
        self.assertEqual(calls[0][1]['timeout'], 600)

    def test_timeout_and_disabling_runs(self):
        self.bot['relevance'] = 'high'
        def timeout(argv, **kw):
            raise subprocess.TimeoutExpired(argv, kw['timeout'])
        result = self.run_delivery(timeout)
        self.assertIn('timed out', result['bots'][0]['bot_run']['error'])
        self.assertTrue(Path(result['bots'][0]['reference_path']).exists())
        self.assertIsNone(self.run_delivery(bot_runs=False)['bots'][0]['bot_run'])
        self.config['hermes']['bot_runs'] = False
        self.assertIsNone(self.run_delivery()['bots'][0]['bot_run'])

    def test_failed_run_still_restores_memory(self):
        self.bot['relevance'] = 'high'
        memory = self.profile / 'memories' / 'state'
        memory.parent.mkdir()
        memory.write_text('original')
        def fail(argv, **kwargs):
            memory.write_text('changed')
            raise OSError('runner unavailable')
        result = self.run_delivery(fail)
        self.assertIn('runner unavailable', result['bots'][0]['bot_run']['error'])
        self.assertTrue(result['bots'][0]['bot_run']['memory_reverted'])
        self.assertEqual(memory.read_text(), 'original')

    def test_rejects_unsafe_destinations(self):
        self.triage['wiki'] = False
        self.bot['topic_slug'] = '../escape'
        with self.assertRaises(ValueError):
            self.run_delivery()
        self.bot['topic_slug'] = 'valid'
        self.bot['profile'] = '../elsewhere'
        with self.assertRaises(ValueError):
            self.run_delivery()
        self.bot['profile'] = 'bot'
        skills = self.profile / 'skills'
        skills.symlink_to(self.vault, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.run_delivery()
