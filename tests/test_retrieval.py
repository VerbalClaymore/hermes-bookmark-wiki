import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from test_archive import bookmark, make_snapshot, run_cli, write_inputs


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR"))
        self.vault = Path(self.tmp.name) / "vault"
        self.vault.mkdir()
        snapshot = make_snapshot(
            bookmarks=[
                bookmark("b1", "2001", "Python source text about maps", tags='["python"]', entities='{"urls":["https://example.test/maps"]}'),
                bookmark("b2", "2002", "A quiet unrelated note", tags='["python"]', entities='{"urls":[]}'),
            ],
            categories=[{"id": "c1", "name": "Research", "slug": "research"}],
            assignments=[{"bookmarkId": "b1", "categoryId": "c1"}, {"bookmarkId": "b2", "categoryId": "c1"}],
        )
        self.snapshot_path, self.hashes_path = write_inputs(self.vault, snapshot, {"Maps - 2001.md": "maps", "Quiet - 2002.md": "quiet"})
        result = run_cli("update", "--vault", self.vault, "--snapshot", self.snapshot_path, "--raw-hashes", self.hashes_path)
        self.assertEqual(result.returncode, 0, result.stderr)

    def tearDown(self):
        self.tmp.cleanup()

    def test_exact_url_ranks_first_and_excerpts_are_bounded(self):
        result = run_cli("search", "--vault", self.vault, "--query", "https://example.test/maps", "--limit", "2", "--excerpt-chars", "100")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)["data"]
        self.assertEqual(data["match_mode"], "exact")
        self.assertEqual(data["results"][0]["source_id"], "x:2001")
        self.assertLessEqual(len(data["results"][0]["excerpt"]), 100)
        self.assertIn("identity", data["results"][0]["matched_fields"])

    def test_all_then_or_fill_and_literal_injection_is_safe(self):
        result = run_cli("search", "--vault", self.vault, "--query", 'python OR * " ) ; DROP TABLE search_fts; --', "--limit", "2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(json.loads(result.stdout)["status"], {"ok", "not_found"})
        result = run_cli("search", "--vault", self.vault, "--query", "python maps", "--limit", "2")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)["data"]
        self.assertEqual(data["match_mode"], "any")
        self.assertEqual(data["results"][0]["source_id"], "x:2001")

    def test_invalid_bounds_blank_and_unknown_show(self):
        for args in [
            ("search", "--vault", self.vault, "--query", " "),
            ("search", "--vault", self.vault, "--query", "x", "--limit", "0"),
            ("search", "--vault", self.vault, "--query", "x", "--excerpt-chars", "1201"),
        ]:
            result = run_cli(*args)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)["error"]["code"], "INVALID_ARGUMENT")
        result = run_cli("show", "--vault", self.vault, "--id", "x:9999")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "NOT_FOUND")

    def test_reader_states_fail_closed(self):
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        result = run_cli("status", "--vault", empty)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "NOT_INITIALIZED")
        (self.vault / "derived" / "search.sqlite3").unlink()
        result = run_cli("status", "--vault", self.vault)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "STALE_INDEX")

    def test_semantic_index_tampering_is_stale_for_all_readers_and_rebuilt_without_loss(self):
        index_path = self.vault / "derived" / "search.sqlite3"
        manifest_before = (self.vault / "manifest.json").read_bytes()
        mutations = [
            ("document content", lambda connection: connection.execute(
                "UPDATE documents SET content_json=? WHERE source_id=?",
                (json.dumps({"text": "tampered"}), "x:2001"),
            )),
            ("document metadata", lambda connection: connection.execute(
                "UPDATE documents SET metadata_json=? WHERE source_id=?",
                (json.dumps({"categories": []}), "x:2001"),
            )),
            ("malformed document JSON", lambda connection: connection.execute(
                "UPDATE documents SET content_json=? WHERE source_id=?", ("{", "x:2001")
            )),
            ("FTS field", lambda connection: connection.execute(
                "UPDATE search_fts SET text=? WHERE source_id=?", ("tampered", "x:2001")
            )),
            ("FTS coverage", lambda connection: connection.execute(
                "DELETE FROM search_fts WHERE source_id=?", ("x:2001",)
            )),
        ]
        for name, mutate in mutations:
            with self.subTest(name=name):
                connection = sqlite3.connect(index_path)
                try:
                    mutate(connection)
                    connection.commit()
                finally:
                    connection.close()
                for command in (
                    ("status", "--vault", self.vault),
                    ("search", "--vault", self.vault, "--query", "maps"),
                    ("show", "--vault", self.vault, "--id", "2001"),
                ):
                    result = run_cli(*command)
                    self.assertEqual(result.returncode, 2, (name, command, result.stderr))
                    self.assertEqual(json.loads(result.stdout)["error"]["code"], "STALE_INDEX")
                repaired = run_cli(
                    "update", "--vault", self.vault,
                    "--snapshot", self.snapshot_path,
                    "--raw-hashes", self.hashes_path,
                )
                self.assertEqual(repaired.returncode, 0, repaired.stderr)
                self.assertTrue(json.loads(repaired.stdout)["data"]["index_rebuilt"])
                self.assertEqual((self.vault / "manifest.json").read_bytes(), manifest_before)

        status = json.loads(run_cli("status", "--vault", self.vault).stdout)["data"]
        self.assertEqual(status["total_sources"], 2)
        search = json.loads(run_cli("search", "--vault", self.vault, "--query", "maps").stdout)["data"]
        self.assertEqual([item["source_id"] for item in search["results"]], ["x:2001"])
        shown = json.loads(run_cli("show", "--vault", self.vault, "--id", "2001").stdout)["data"]
        self.assertEqual(shown["content"]["text"], "Python source text about maps")

    def test_semantic_hint_matches_add_generated_warning_and_original_url_is_indexed(self):
        hint = run_cli("search", "--vault", self.vault, "--query", "python", "--limit", "2")
        self.assertEqual(hint.returncode, 0, hint.stderr)
        hint_data = json.loads(hint.stdout)["data"]
        by_id = {item["source_id"]: item for item in hint_data["results"]}
        self.assertIn("generated_search_hints", by_id["x:2002"]["warnings"])
        self.assertEqual(by_id["x:2002"]["matched_fields"], ["semantic_hints"])

        original_url = run_cli("search", "--vault", self.vault, "--query", "2001", "--limit", "2")
        self.assertEqual(original_url.returncode, 0, original_url.stderr)
        original_data = json.loads(original_url.stdout)["data"]
        self.assertEqual(original_data["results"][0]["source_id"], "x:2001")
        self.assertIn("urls", original_data["results"][0]["matched_fields"])


if __name__ == "__main__":
    unittest.main()
