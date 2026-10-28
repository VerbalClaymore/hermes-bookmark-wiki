import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_archive import bookmark, make_snapshot, run_cli, write_inputs


class CliTests(unittest.TestCase):
    def test_json_errors_redact_paths_and_help_is_available(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as td:
            vault = Path(td) / "vault"
            vault.mkdir()
            result = run_cli("show", "--vault", vault, "--id", "not-an-id")
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stderr, "")
            payload = json.loads(result.stdout)
            self.assertEqual(payload["error"]["code"], "INVALID_ARGUMENT")
            self.assertNotIn(str(vault), result.stdout)
            help_result = run_cli("--help")
            self.assertEqual(help_result.returncode, 0)
            self.assertIn("update", help_result.stdout)

    def test_status_and_schema_have_no_extra_provider_fields(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as td:
            vault = Path(td) / "vault"
            vault.mkdir()
            snapshot = make_snapshot(
                bookmarks=[bookmark("b1", "3001", "CLI")],
                categories=[{"id": "c1", "name": "Research", "slug": "research"}],
                assignments=[{"bookmarkId": "b1", "categoryId": "c1"}],
            )
            snapshot_path, hashes_path = write_inputs(vault, snapshot, {"Cli - 3001.md": "cli"})
            update = run_cli("update", "--vault", vault, "--snapshot", snapshot_path, "--raw-hashes", hashes_path)
            self.assertEqual(update.returncode, 0, update.stderr)
            data = json.loads(run_cli("status", "--vault", vault).stdout)["data"]
            self.assertEqual(set(data), {"total_sources", "revision_count", "context_pending", "captured", "provisional", "index_fresh", "manifest_sha256"})
            self.assertTrue(data["index_fresh"])
    def test_unknown_command_does_not_echo_argv(self):
        leaked = "/private/synthetic/absolute-command"
        result = run_cli(leaked)
        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["command"], "unknown")
        self.assertNotIn(leaked, result.stdout)


class LearningCliTests(unittest.TestCase):
    def test_learning_malformed_inputs_keep_command_and_fixed_envelope(self):
        for args in [(), ('unknown',), ('apply',), ('init', '--vault'), ('check', '--batch-id', 'bad')]:
            result = run_cli('learning', *args)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stderr, '')
            self.assertEqual(json.loads(result.stdout), {
                'schema_version': 1, 'command': 'learning', 'status': 'error',
                'error': {'code': 'INVALID_ARGUMENT', 'message': 'Invalid argument.'}})


if __name__ == "__main__":
    unittest.main()
