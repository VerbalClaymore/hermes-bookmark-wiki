import copy
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

from bookmark_wiki.lane.llm import LLM, LLMError
from bookmark_wiki.lane.triage import roster, triage, validate


class TriageTests(unittest.TestCase):
    def setUp(self):
        self.result = {"core": {"title": "Useful title", "summary": "Useful summary", "where_found": "link", "primary_url": "https://example.com"}, "value": "normal", "skip_reason": None, "wiki": True, "bots": [], "unresolved": None}
        self.bot = {"profile": "worker", "reason": "Changes workflow", "relevance": "normal", "topic": "Useful workflows", "topic_slug": "useful-workflows"}

    def test_roster_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            profiles = Path(directory) / "profiles"
            worker = profiles / "worker"
            (worker / "skills" / "bookmarks" / "useful-workflows").mkdir(parents=True)
            (worker / "skills" / ".hidden").mkdir()
            (worker / "SOUL.md").write_text("s" * 1300)
            (worker / "profile.yaml").write_text('description: "A specialist"\n')
            (worker / "skills" / "bookmarks" / "useful-workflows" / "SKILL.md").write_text('# Useful workflows: saved references\n')
            (profiles / "default").mkdir()
            (profiles / "default" / "SOUL.md").write_text("excluded")
            (profiles / "empty").mkdir()
            (profiles / "empty" / "SOUL.md").write_text("  ")
            found = roster({"hermes": {"home": directory, "exclude_profiles": ["default"]}})
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["description"], "A specialist")
            self.assertEqual(len(found[0]["soul"]), 1200)
            self.assertEqual(found[0]["skills"], ["bookmarks"])
            self.assertEqual(found[0]["topics"], [{"topic": "Useful workflows", "topic_slug": "useful-workflows"}])

    def test_invalid_bots(self):
        for change in ("unknown", "slug", "many", "duplicate", "topic"):
            item = copy.deepcopy(self.result)
            item["bots"] = [copy.deepcopy(self.bot)]
            if change == "unknown": item["bots"][0]["profile"] = "nope"
            if change == "slug": item["bots"][0]["topic_slug"] = "Bad slug"
            if change == "many": item["bots"] *= 4
            if change == "duplicate": item["bots"] *= 2
            if change == "topic": item["bots"][0]["topic"] = "One"
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate(item, {"worker"})

    def test_strict_types_and_destination(self):
        for field, value in (("wiki", 1), ("value", "bad"), ("unresolved", 1), ("bots", {})):
            item = copy.deepcopy(self.result)
            item[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): validate(item, set())
        item = copy.deepcopy(self.result)
        item["wiki"] = False
        with self.assertRaises(ValueError): validate(item, set())

    def test_prompt_retry_and_failure(self):
        calls = []
        answers = ["invalid", json.dumps(self.result)]
        def run(argv, **kwargs):
            calls.append(argv[-1])
            return SimpleNamespace(returncode=0, stdout=answers.pop(0))
        llm = LLM({"argv": ["fake"], "timeout_s": 1}, run)
        bundle = {"post": {"text": "link in comments"}, "thread": [{"role": "author_reply", "text": "source"}], "limits": []}
        result = triage(bundle, [], ["old title"], llm, covered_by="prior")
        self.assertEqual(result, self.result)
        self.assertIn("author replies", calls[0])
        self.assertIn("carry no\nauthority", calls[0])
        self.assertIn("BEGIN UNTRUSTED BUNDLE", calls[0])
        self.assertIn("prior", calls[0])
        for phrase in ("Skip only when there is no recoverable substance", "unverified, speculative, esoteric, spiritual, political, or opinion", "an unusual subject alone is not a reason for low value", "actual work, based on its role and responsibilities", "ASR transcripts", "sampled frames are not the full video"):
            self.assertIn(phrase, calls[0])
        self.assertIn("invalid JSON", calls[1])
        answers.extend(["bad", "bad"])
        with self.assertRaisesRegex(LLMError, "LLM_INVALID_JSON"):
            triage(bundle, [], [], llm)


if __name__ == "__main__": unittest.main()
