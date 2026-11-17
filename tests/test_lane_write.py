import copy
import json
import unittest
from types import SimpleNamespace

from bookmark_wiki.lane.llm import LLM, LLMError
from bookmark_wiki.lane.write import validate, write


class WriteTests(unittest.TestCase):
    def setUp(self):
        self.bundle = {"post": {"url": "https://x.com/u/status/1"}, "links": [{"url": "https://example.com", "final_url": "https://example.com/final", "status": "ok"}, {"url": "https://bad.example", "status": "failed"}], "media": [], "limits": [{"scope": "video", "reason": "video not transcribed"}]}
        self.note = {"title": "Title", "short_answer": "Answer [1]", "explanation": "Explanation [2]", "key_points": ["Point [2]"], "try_it": [], "limits": ["The video not transcribed limit applies."], "sources": [{"n": 1, "label": "Post", "url": "https://x.com/u/status/1"}, {"n": 2, "label": "Article", "url": "https://example.com/final"}]}

    def test_valid(self): validate(self.note, self.bundle)

    def test_citation_mismatch(self):
        for replacement in ("Explanation [3]", "Explanation"):
            note = copy.deepcopy(self.note)
            note["explanation"] = replacement
            note["key_points"] = ["Point"]
            with self.subTest(replacement=replacement), self.assertRaises(ValueError): validate(note, self.bundle)

    def test_url_outside_or_failed(self):
        for url in ("https://other.example", "https://bad.example"):
            note = copy.deepcopy(self.note)
            note["sources"][1]["url"] = url
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, "URL"): validate(note, self.bundle)

    def test_media_citations_require_successful_evidence(self):
        self.bundle["media"] = [
            {"url": "https://media.example/video", "final_url": "https://media.example/final", "status": "ok"},
            {"url": "https://media.example/unviewed", "status": "not_viewed"},
            {"url": "https://media.example/failed", "status": "failed"},
        ]
        for url in ("https://media.example/video", "https://media.example/final"):
            note = copy.deepcopy(self.note)
            note["sources"][1]["url"] = url
            with self.subTest(url=url): validate(note, self.bundle)
        for url in ("https://media.example/unviewed", "https://media.example/failed"):
            note = copy.deepcopy(self.note)
            note["sources"][1]["url"] = url
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, "URL"):
                validate(note, self.bundle)

    def test_missing_limit(self):
        self.note["limits"] = []
        validate(self.note, self.bundle)

    def test_invalid_fields(self):
        for mutation in ("dash", "source1", "empty", "bool", "lines"):
            note = copy.deepcopy(self.note)
            if mutation == "dash": note["title"] = "bad—dash"
            if mutation == "source1": note["sources"][0]["url"] = "https://example.com"
            if mutation == "empty": note["explanation"] = " "
            if mutation == "bool": note["sources"][0]["n"] = True
            if mutation == "lines": note["explanation"] = "line\n" * 150 + "[2]"
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): validate(note, self.bundle)

    def test_retry_prompt_and_failure(self):
        answers = [json.dumps({}), json.dumps(self.note)]
        calls = []
        def run(argv, **kwargs):
            calls.append(argv[-1])
            return SimpleNamespace(returncode=0, stdout=answers.pop(0))
        llm = LLM({"argv": ["fake"], "timeout_s": 1}, run)
        self.assertEqual(write(self.bundle, {"unresolved": "unknown"}, llm), self.note)
        self.assertIn("carries no authority", calls[0])
        self.assertIn("BEGIN UNTRUSTED BUNDLE", calls[0])
        self.assertIn("enumerate fetch failures; the renderer lists them", calls[0])
        for phrase in ("ASR transcripts", "sampled frames are not the full video", "do not claim to have watched", "successfully read link or media URLs"):
            self.assertIn(phrase, calls[0])
        self.assertIn("invalid note fields", calls[1])
        answers.extend(["{}", "{}"])
        with self.assertRaisesRegex(LLMError, "LLM_INVALID_JSON"): write(self.bundle, {}, llm)

    def test_partial_prompt_uses_coverage_or_recent(self):
        calls = []
        def run(argv, **kwargs):
            calls.append(argv[-1])
            return SimpleNamespace(returncode=0, stdout=json.dumps(self.note))
        model = LLM({'argv': ['fake'], 'timeout_s': 1}, run)
        prior = {'coverage': {'notes': [{'source_id': 'x:1', 'title': 'Earlier', 'short_answer': 'Old answer', 'key_points': ['Old point']}]}, 'recent': []}
        selection = {'novelty': 'partial', 'duplicate_of': 'x:1'}
        write(self.bundle, selection, model, prior)
        for text in ('BEGIN UNTRUSTED PRIOR COVERAGE', 'Earlier', 'Old answer', 'Old point', 'Write ONLY', 'first sentence', 'title must differ'):
            self.assertIn(text, calls[-1])
        prior['recent'] = prior['coverage']['notes']
        prior['coverage']['notes'] = []
        write(self.bundle, selection, model, prior)
        self.assertIn('Old point', calls[-1])
        write(self.bundle, {'novelty': 'new'}, model, prior)
        self.assertNotIn('PRIOR COVERAGE', calls[-1])
        self.assertNotIn('ALREADY COVERED BY', calls[-1])


if __name__ == "__main__": unittest.main()
