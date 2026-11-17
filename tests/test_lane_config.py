"""Configuration compatibility and validation without external services."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from bookmark_wiki.lane import config


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name)
        self.path = config.init(self.vault)

    def load_data(self, data):
        self.path.write_text(json.dumps(data), encoding="utf-8")
        return config.load(self.vault)

    def test_init_defaults_and_non_destructive(self):
        self.assertEqual(json.loads(self.path.read_text()), config.DEFAULTS)
        self.path.write_text('{}', encoding='utf-8')
        config.init(self.vault)
        self.assertEqual(self.path.read_text(), '{}')
        self.assertEqual(config.load(self.vault), config.DEFAULTS)

    def test_legacy_and_partial_config_merge_without_mutation(self):
        original = copy.deepcopy(config.DEFAULTS)
        legacy = copy.deepcopy(original)
        del legacy['media']
        del legacy['limits']['capture_count']
        legacy['limits']['items_per_sync'] = 7
        legacy['llm']['argv'] = ['fake', '']
        loaded = self.load_data(legacy)
        self.assertEqual(loaded['media'], original['media'])
        self.assertEqual(loaded['limits']['capture_count'], 40)
        self.assertEqual(loaded['limits']['items_per_sync'], 7)
        self.assertEqual(loaded['llm']['argv'], ['fake', ''])
        loaded['media']['whisper_argv'].append('changed')
        self.assertEqual(config.DEFAULTS, original)
        self.assertEqual(self.load_data({'media': {'ffmpeg': 'fake-ffmpeg'}})['media'],
                         {**original['media'], 'ffmpeg': 'fake-ffmpeg'})

    def test_invalid_config_rejected(self):
        bad = [None, [], {'unknown': {}}, {'media': None}, {'media': []},
               {'media': {'unknown': 1}}, {'limits': {'bad': 1}},
               {'media': {'max_video_seconds': True}}, {'media': {'videos_per_item': 0}},
               {'media': {'frames_per_video': -1}}, {'media': {'frames_per_video': 1.5}},
               {'media': {'whisper_argv': []}}, {'media': {'whisper_argv': [' ']}},
               {'media': {'whisper_argv': ['fake', 1]}}, {'media': {'whisper_model': ''}},
               {'media': {'ffmpeg': '  '}}, {'limits': {'link_bytes': 3 * 1024 * 1024}}]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.load_data(value)

    def test_invalid_json_rejected(self):
        self.path.write_text('{', encoding='utf-8')
        with self.assertRaises(ValueError):
            config.load(self.vault)


if __name__ == '__main__':
    unittest.main()
