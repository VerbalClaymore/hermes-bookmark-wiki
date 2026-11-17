"""Stage six report tests."""

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from bookmark_wiki.lane import report


class ReportTests(unittest.TestCase):
    def test_append_item_blocks_and_preserve_earlier_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            clock = lambda: datetime(2026, 11, 16, tzinfo=timezone.utc)
            entry = {'source_id': 'x:1', 'status': 'done',
                     'triage': {'value': 'high', 'core': {'where_found': 'link'}},
                     'note': {'title': 'Example', 'limits': ['Not transcribed']},
                     'delivery': {'wiki': {'path': '/vault/wiki/note.md'}, 'bots': [
                         {'profile': 'bot', 'topic_slug': 'topic', 'reference_path': '/ref',
                          'bot_run': {'answer': 'CHANGED: adjusted', 'error': None,
                                      'changed_paths': ['/skill'], 'memory_reverted': True}}]}}
            path = report.append(vault, [entry], clock=clock)
            first = path.read_text()
            self.assertEqual(path, vault / 'lane/reports/2026-11-16.md')
            for expected in ['## Example', 'x:1', 'high', 'link', '/vault/wiki/note.md',
                             'bot / topic', 'CHANGED: adjusted', 'memory restored',
                             '/skill', 'Not transcribed']:
                self.assertIn(expected, first)
            report.append(vault, [{'source_id': 'x:2', 'status': 'skipped',
                                   'skip_reason': 'duplicate'},
                                  {'source_id': 'x:3', 'status': 'failed',
                                   'error': 'LLM_FAILED: timeout'}], clock=clock)
            final = path.read_text()
            self.assertTrue(final.startswith(first))
            self.assertEqual(final.count('# Lean lane report'), 1)
            self.assertIn('duplicate', final)
            self.assertIn('LLM_FAILED: timeout', final)

    def test_bot_timeout_and_structured_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            entry = {'title': 'Title', 'value': 'normal', 'where_found': 'media',
                     'limits': [{'scope': 'video', 'reason': 'not viewed'}],
                     'delivery': {'bots': [{'profile': 'bot', 'topic_slug': 'video',
                                            'bot_run': {'error': 'bot run timed out'}}]}}
            result = report.append(Path(tmp), [entry], clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))
            text = result.read_text()
            self.assertIn('video: not viewed', text)
            self.assertIn('bot run timed out', text)
