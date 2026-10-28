import hashlib
import fcntl
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def make_snapshot(bookmarks=None, media=None, categories=None, assignments=None):
    return {
        "captured_at": "2026-09-26T14:26:52+00:00",
        "tables": {
            "bookmarks": bookmarks or [],
            "media": media or [],
            "categories": categories or [],
            "assignments": assignments or [],
        },
        "unknown_secret": "ignore-me",
    }


def bookmark(bookmark_id, tweet_id, text, category_id="c1", tags='["topic"]', entities='{"urls":["https://example.test/a"]}'):
    return {
        "id": bookmark_id,
        "tweetId": tweet_id,
        "text": text,
        "authorHandle": "writer",
        "authorName": "Synthetic Writer",
        "tweetCreatedAt": "2026-09-20T12:00:00+00:00",
        "importedAt": "2026-09-26T14:00:00+00:00",
        "semanticTags": tags,
        "entities": entities,
        "enrichedAt": None,
        "rawJson": {"token": "never-copy"},
        "enrichmentMeta": {"prompt": "never-copy"},
        "localPath": "/private/never-copy",
    }


def write_inputs(vault, snapshot, notes=None, extra_digest=None, wave="wave-1", raw_subtree=None):
    evidence_dir = vault / "source-evidence" / wave
    raw_parts = tuple(raw_subtree) if raw_subtree is not None else (wave,)
    raw_dir = vault.joinpath("raw", *raw_parts, "Twitter Bookmarks")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = evidence_dir / "evidence.json"
    snapshot_bytes = (canonical(snapshot) + "\n").encode()
    snapshot_path.write_bytes(snapshot_bytes)
    (evidence_dir / "capture-manifest.json").write_text(
        canonical({"evidence_sha256": hashlib.sha256(snapshot_bytes).hexdigest()}) + "\n",
        encoding="utf-8",
    )
    notes = notes or {}
    digests = {}
    for filename, content in notes.items():
        path = raw_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        rel = path.relative_to(vault).as_posix()
        digests[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    if extra_digest:
        digests.update(extra_digest)
    hashes_path = evidence_dir / "raw-sha256.json"
    hashes_path.write_text(canonical(digests) + "\n", encoding="utf-8")
    return snapshot_path, hashes_path


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "bookmark_wiki", *map(str, args)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )


class ArchiveTests(unittest.TestCase):
    def test_two_sources_ignore_index_and_private_fields(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as td:
            vault = Path(td) / "vault"
            vault.mkdir()
            categories = [{"id": "c1", "name": "Research", "slug": "research"}]
            bookmarks = [bookmark("b1", "1001", "First synthetic note"), bookmark("b2", "1002", "Second synthetic note")]
            snapshot = make_snapshot(
                bookmarks=bookmarks,
                categories=categories,
                assignments=[{"bookmarkId": "b1", "categoryId": "c1"}, {"bookmarkId": "b2", "categoryId": "c1"}],
            )
            snapshot_path, hashes_path = write_inputs(
                vault,
                snapshot,
                {"First - 1001.md": "first source", "Second - 1002.md": "second source", "_index/catalog.md": "index source"},
            )
            result = run_cli("update", "--vault", vault, "--snapshot", snapshot_path, "--raw-hashes", hashes_path)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["data"]["total_sources"], 2)
            manifest = json.loads((vault / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(sorted(manifest["sources"]), ["x:1001", "x:1002"])
            self.assertNotIn("never-copy", (vault / "manifest.json").read_text(encoding="utf-8"))
            show = run_cli("show", "--vault", vault, "--id", "x:1001")
            self.assertEqual(show.returncode, 0, show.stderr)
            self.assertNotIn("rawJson", show.stdout)
            self.assertNotIn("private/never-copy", show.stdout)
            self.assertNotIn("never-copy", show.stdout)

    def test_same_input_is_byte_for_byte_noop(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as td:
            vault = Path(td) / "vault"
            vault.mkdir()
            snapshot = make_snapshot(
                bookmarks=[bookmark("b1", "1001", "Stable")],
                categories=[{"id": "c1", "name": "Research", "slug": "research"}],
                assignments=[{"bookmarkId": "b1", "categoryId": "c1"}],
            )
            snapshot_path, hashes_path = write_inputs(vault, snapshot, {"Stable - 1001.md": "stable"})
            first = run_cli("update", "--vault", vault, "--snapshot", snapshot_path, "--raw-hashes", hashes_path)
            self.assertEqual(first.returncode, 0, first.stderr)
            manifest_before = (vault / "manifest.json").read_bytes()
            index_before = (vault / "derived" / "search.sqlite3").read_bytes()
            second = run_cli("update", "--vault", vault, "--snapshot", snapshot_path, "--raw-hashes", hashes_path)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(json.loads(second.stdout)["data"]["unchanged"], 1)
            self.assertEqual((vault / "manifest.json").read_bytes(), manifest_before)
            self.assertEqual((vault / "derived" / "search.sqlite3").read_bytes(), index_before)

    def test_invalid_fk_and_digest_tamper_fail_before_outputs(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as td:
            vault = Path(td) / "vault"
            vault.mkdir()
            snapshot = make_snapshot(
                bookmarks=[bookmark("b1", "1001", "Bad FK")],
                categories=[{"id": "c1", "name": "Research", "slug": "research"}],
                assignments=[{"bookmarkId": "b1", "categoryId": "missing"}],
            )
            snapshot_path, hashes_path = write_inputs(vault, snapshot, {"Bad - 1001.md": "bad"})
            result = run_cli("update", "--vault", vault, "--snapshot", snapshot_path, "--raw-hashes", hashes_path)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)["error"]["code"], "INVALID_INPUT")
            self.assertFalse((vault / "manifest.json").exists())
            self.assertFalse((vault / "derived").exists())
            snapshot_path.write_text(snapshot_path.read_text(encoding="utf-8").replace("Bad FK", "tampered"), encoding="utf-8")
            result = run_cli("update", "--vault", vault, "--snapshot", snapshot_path, "--raw-hashes", hashes_path)
            self.assertEqual(json.loads(result.stdout)["error"]["code"], "INTEGRITY_ERROR")

    def test_revision_and_retained_source(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as td:
            vault = Path(td) / "vault"
            vault.mkdir()
            categories = [{"id": "c1", "name": "Research", "slug": "research"}]
            first = make_snapshot(
                bookmarks=[bookmark("b1", "1001", "Version one"), bookmark("b2", "1002", "Retained")],
                categories=categories,
                assignments=[{"bookmarkId": "b1", "categoryId": "c1"}, {"bookmarkId": "b2", "categoryId": "c1"}],
            )
            s1, h1 = write_inputs(vault, first, {"One - 1001.md": "one", "Two - 1002.md": "two"})
            self.assertEqual(run_cli("update", "--vault", vault, "--snapshot", s1, "--raw-hashes", h1).returncode, 0)
            second = make_snapshot(
                bookmarks=[bookmark("b1", "1001", "Version two")],
                categories=categories,
                assignments=[{"bookmarkId": "b1", "categoryId": "c1"}],
            )
            s2, h2 = write_inputs(vault, second, {"One - 1001.md": "one"}, wave="wave-2")
            result = run_cli("update", "--vault", vault, "--snapshot", s2, "--raw-hashes", h2)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)["data"]
            self.assertEqual((data["added"], data["changed"], data["retained"]), (0, 1, 1))
            manifest = json.loads((vault / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["sources"]["x:1001"]["revisions"]), 2)
            self.assertEqual(manifest["sources"]["x:1001"]["integration_status"], "provisional")
            self.assertIn("x:1002", manifest["sources"])

    def test_fault_after_manifest_replace_is_repairable(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as td:
            vault = Path(td) / "vault"
            vault.mkdir()
            snapshot = make_snapshot(
                bookmarks=[bookmark("b1", "1001", "Crash test")],
                categories=[{"id": "c1", "name": "Research", "slug": "research"}],
                assignments=[{"bookmarkId": "b1", "categoryId": "c1"}],
            )
            snapshot_path, hashes_path = write_inputs(vault, snapshot, {"Crash - 1001.md": "crash"})
            from bookmark_wiki.archive import update, SimulatedFault

            with self.assertRaises(SimulatedFault):
                update(vault, snapshot_path, hashes_path, fault_at="after_manifest_replace")
            stale = run_cli("status", "--vault", vault)
            self.assertEqual(json.loads(stale.stdout)["error"]["code"], "STALE_INDEX")
            repaired = run_cli("update", "--vault", vault, "--snapshot", snapshot_path, "--raw-hashes", hashes_path)
            self.assertEqual(repaired.returncode, 0, repaired.stderr)
            self.assertTrue(json.loads(repaired.stdout)["data"]["index_rebuilt"])

    def test_staging_fault_has_no_published_pair_and_index_fault_is_readable(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as td:
            vault = Path(td) / "vault"
            vault.mkdir()
            snapshot = make_snapshot(
                bookmarks=[bookmark("b1", "1001", "Fault phases")],
                categories=[{"id": "c1", "name": "Research", "slug": "research"}],
                assignments=[{"bookmarkId": "b1", "categoryId": "c1"}],
            )
            snapshot_path, hashes_path = write_inputs(vault, snapshot, {"Fault - 1001.md": "fault"})
            from bookmark_wiki.archive import SimulatedFault, update

            with self.assertRaises(SimulatedFault):
                update(vault, snapshot_path, hashes_path, fault_at="after_staging")
            self.assertFalse((vault / "manifest.json").exists())
            with self.assertRaises(SimulatedFault):
                update(vault, snapshot_path, hashes_path, fault_at="after_index_replace")
            result = run_cli("status", "--vault", vault)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_identical_revision_in_later_wave_keeps_first_provenance(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as td:
            vault = Path(td) / "vault"
            vault.mkdir()
            snapshot = make_snapshot(
                bookmarks=[bookmark("b1", "1001", "Same revision")],
                categories=[{"id": "c1", "name": "Research", "slug": "research"}],
                assignments=[{"bookmarkId": "b1", "categoryId": "c1"}],
            )
            s1, h1 = write_inputs(vault, snapshot, {"Same - 1001.md": "same"}, wave="wave-1")
            self.assertEqual(run_cli("update", "--vault", vault, "--snapshot", s1, "--raw-hashes", h1).returncode, 0)
            manifest_before = (vault / "manifest.json").read_bytes()
            s2, h2 = write_inputs(vault, snapshot, {"Same - 1001.md": "same"}, wave="wave-2")
            result = run_cli("update", "--vault", vault, "--snapshot", s2, "--raw-hashes", h2)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["data"]["unchanged"], 1)
            self.assertEqual((vault / "manifest.json").read_bytes(), manifest_before)
            shown = json.loads(run_cli("show", "--vault", vault, "--id", "1001").stdout)["data"]
            self.assertEqual(shown["evidence_path"], "source-evidence/wave-1/evidence.json")

    def test_real_lock_returns_locked_without_publishing(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as td:
            vault = Path(td) / "vault"
            vault.mkdir()
            snapshot = make_snapshot(
                bookmarks=[bookmark("b1", "1001", "Locked")],
                categories=[{"id": "c1", "name": "Research", "slug": "research"}],
                assignments=[{"bookmarkId": "b1", "categoryId": "c1"}],
            )
            snapshot_path, hashes_path = write_inputs(vault, snapshot, {"Locked - 1001.md": "locked"})
            state = vault / "state"
            state.mkdir()
            lock = (state / "update.lock").open("a+b")
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                result = run_cli("update", "--vault", vault, "--snapshot", snapshot_path, "--raw-hashes", hashes_path)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
                lock.close()
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)["error"]["code"], "LOCKED")
            self.assertFalse((vault / "manifest.json").exists())

    def test_missing_orphan_and_symlink_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as td:
            vault = Path(td) / "vault"
            vault.mkdir()
            snapshot = make_snapshot(
                bookmarks=[bookmark("b1", "1001", "Paths")],
                categories=[{"id": "c1", "name": "Research", "slug": "research"}],
                assignments=[{"bookmarkId": "b1", "categoryId": "c1"}],
            )
            snapshot_path, hashes_path = write_inputs(vault, snapshot, {"Paths - 1001.md": "paths"})
            raw = vault / "raw" / "wave-1" / "Twitter Bookmarks"
            (raw / "_index").mkdir()
            (raw / "_index" / "catalog.md").write_text("catalog", encoding="utf-8")
            result = run_cli("update", "--vault", vault, "--snapshot", snapshot_path, "--raw-hashes", hashes_path)
            self.assertEqual(json.loads(result.stdout)["error"]["code"], "INVALID_INPUT")
            outside = Path(td) / "outside.json"
            outside.write_text("{}", encoding="utf-8")
            snapshot_link = vault / "source-evidence" / "wave-1" / "link.json"
            snapshot_link.symlink_to(outside)
            result = run_cli("update", "--vault", vault, "--snapshot", snapshot_link, "--raw-hashes", hashes_path)
            self.assertEqual(json.loads(result.stdout)["error"]["code"], "INVALID_INPUT")

    def test_manifest_validation_reads_shared_evidence_once(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as td:
            vault = Path(td) / "vault"
            vault.mkdir()
            snapshot = make_snapshot(
                bookmarks=[bookmark("b1", "1001", "First"), bookmark("b2", "1002", "Second")],
                categories=[{"id": "c1", "name": "Research", "slug": "research"}],
                assignments=[{"bookmarkId": "b1", "categoryId": "c1"}, {"bookmarkId": "b2", "categoryId": "c1"}],
            )
            snapshot_path, hashes_path = write_inputs(vault, snapshot, {"First - 1001.md": "first", "Second - 1002.md": "second"})
            self.assertEqual(run_cli("update", "--vault", vault, "--snapshot", snapshot_path, "--raw-hashes", hashes_path).returncode, 0)
            from bookmark_wiki import archive

            reads = 0
            original_read_bytes = Path.read_bytes

            def counted_read_bytes(path, *args, **kwargs):
                nonlocal reads
                if path == snapshot_path:
                    reads += 1
                return original_read_bytes(path, *args, **kwargs)

            with patch.object(Path, "read_bytes", counted_read_bytes):
                archive.load_manifest_for_reader(vault)
            self.assertEqual(reads, 1)

    def test_unexpected_native_dirs_and_shapes_are_rejected_globally(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as td:
            vault = Path(td) / "vault"
            vault.mkdir()
            snapshot = make_snapshot(
                bookmarks=[bookmark("b1", "1001", "Paths")],
                categories=[{"id": "c1", "name": "Research", "slug": "research"}],
                assignments=[{"bookmarkId": "b1", "categoryId": "c1"}],
            )
            snapshot_path, hashes_path = write_inputs(vault, snapshot, {"Paths - 1001.md": "paths"})
            unexpected = vault / "raw" / "wave-2" / "Twitter Bookmarks" / "Orphan - 9999.md"
            unexpected.parent.mkdir(parents=True)
            unexpected.write_text("orphan", encoding="utf-8")
            digests = json.loads(hashes_path.read_text(encoding="utf-8"))
            digests[unexpected.relative_to(vault).as_posix()] = hashlib.sha256(unexpected.read_bytes()).hexdigest()
            hashes_path.write_text(canonical(digests) + "\n", encoding="utf-8")
            result = run_cli("update", "--vault", vault, "--snapshot", snapshot_path, "--raw-hashes", hashes_path)
            self.assertEqual(json.loads(result.stdout)["error"]["code"], "INVALID_INPUT")

            invalid_shape = vault / "raw" / "wave-1" / "notes" / "Loose.md"
            invalid_shape.parent.mkdir(parents=True)
            invalid_shape.write_text("loose", encoding="utf-8")
            digests = json.loads(hashes_path.read_text(encoding="utf-8"))
            digests[invalid_shape.relative_to(vault).as_posix()] = hashlib.sha256(invalid_shape.read_bytes()).hexdigest()
            hashes_path.write_text(canonical(digests) + "\n", encoding="utf-8")
            result = run_cli("update", "--vault", vault, "--snapshot", snapshot_path, "--raw-hashes", hashes_path)
            self.assertEqual(json.loads(result.stdout)["error"]["code"], "INVALID_INPUT")

    def test_zero_bookmark_input_rejects_unexpected_native_source(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as td:
            vault = Path(td) / "vault"
            vault.mkdir()
            snapshot_path, hashes_path = write_inputs(vault, make_snapshot(), {})
            orphan = vault / "raw" / "wave-2" / "Twitter Bookmarks" / "Orphan - 9999.md"
            orphan.parent.mkdir(parents=True)
            orphan.write_text("orphan", encoding="utf-8")
            hashes_path.write_text(canonical({orphan.relative_to(vault).as_posix(): hashlib.sha256(orphan.read_bytes()).hexdigest()}) + "\n", encoding="utf-8")
            result = run_cli("update", "--vault", vault, "--snapshot", snapshot_path, "--raw-hashes", hashes_path)
            self.assertEqual(json.loads(result.stdout)["error"]["code"], "INVALID_INPUT")

    def test_invalid_assignment_id_and_malformed_url_are_safe_input(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as td:
            vault = Path(td) / "vault"
            vault.mkdir()
            bad_assignment = make_snapshot(
                bookmarks=[bookmark("b1", "1001", "Bad assignment")],
                categories=[{"id": "c1", "name": "Research", "slug": "research"}],
                assignments=[{"bookmarkId": ["b1"], "categoryId": "c1"}],
            )
            snapshot_path, hashes_path = write_inputs(vault, bad_assignment, {"Bad - 1001.md": "bad"})
            result = run_cli("update", "--vault", vault, "--snapshot", snapshot_path, "--raw-hashes", hashes_path)
            self.assertEqual(json.loads(result.stdout)["error"]["code"], "INVALID_INPUT")

            safe_url = make_snapshot(
                bookmarks=[bookmark("b1", "1001", "Malformed URL", entities='{"urls":["http://[bad"]}')],
                categories=[{"id": "c1", "name": "Research", "slug": "research"}],
                assignments=[{"bookmarkId": "b1", "categoryId": "c1"}],
            )
            snapshot_path, hashes_path = write_inputs(vault, safe_url, {"Bad - 1001.md": "bad"}, wave="wave-2")
            result = run_cli("update", "--vault", vault, "--snapshot", snapshot_path, "--raw-hashes", hashes_path)
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads((vault / "manifest.json").read_text(encoding="utf-8"))
            revision = next(iter(manifest["sources"]["x:1001"]["revisions"].values()))
            self.assertIn("malformed_urls", revision["metadata"]["warnings"])

    def test_manifest_identity_and_allowlist_corruption_is_structural_error(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as td:
            vault = Path(td) / "vault"
            vault.mkdir()
            snapshot = make_snapshot(
                bookmarks=[bookmark("b1", "1001", "Manifest")],
                categories=[{"id": "c1", "name": "Research", "slug": "research"}],
                assignments=[{"bookmarkId": "b1", "categoryId": "c1"}],
            )
            snapshot_path, hashes_path = write_inputs(vault, snapshot, {"Manifest - 1001.md": "manifest"})
            self.assertEqual(run_cli("update", "--vault", vault, "--snapshot", snapshot_path, "--raw-hashes", hashes_path).returncode, 0)
            manifest_path = vault / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["sources"]["x:1001"]["tweet_id"] = "9999"
            manifest_path.write_text(canonical(manifest) + "\n", encoding="utf-8")
            result = run_cli("status", "--vault", vault)
            self.assertEqual(json.loads(result.stdout)["error"]["code"], "CORRUPT_MANIFEST")

            manifest = json.loads((vault / "manifest.json").read_text(encoding="utf-8"))
            manifest["sources"]["x:1001"]["tweet_id"] = "1001"
            revision = next(iter(manifest["sources"]["x:1001"]["revisions"].values()))
            revision["content"]["provider_secret"] = "never-copy"
            manifest_path.write_text(canonical(manifest) + "\n", encoding="utf-8")
            result = run_cli("status", "--vault", vault)
            self.assertEqual(json.loads(result.stdout)["error"]["code"], "CORRUPT_MANIFEST")

    def test_historical_file_mismatch_is_integrity_error(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as td:
            vault = Path(td) / "vault"
            vault.mkdir()
            snapshot = make_snapshot(
                bookmarks=[bookmark("b1", "1001", "Historical")],
                categories=[{"id": "c1", "name": "Research", "slug": "research"}],
                assignments=[{"bookmarkId": "b1", "categoryId": "c1"}],
            )
            snapshot_path, hashes_path = write_inputs(vault, snapshot, {"Historical - 1001.md": "historical"})
            self.assertEqual(run_cli("update", "--vault", vault, "--snapshot", snapshot_path, "--raw-hashes", hashes_path).returncode, 0)
            (vault / "raw" / "wave-1" / "Twitter Bookmarks" / "Historical - 1001.md").write_text("tampered", encoding="utf-8")
            result = run_cli("status", "--vault", vault)
            self.assertEqual(json.loads(result.stdout)["error"]["code"], "INTEGRITY_ERROR")

    def test_versioned_raw_subtrees_are_independent_of_evidence_name(self):
        for raw_subtree in (("revision-42",), ("waves", "revision-42")):
            with self.subTest(raw_subtree=raw_subtree), tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as td:
                vault = Path(td) / "vault"
                vault.mkdir()
                snapshot = make_snapshot(
                    bookmarks=[bookmark("b1", "1001", "Generic raw subtree")],
                    categories=[{"id": "c1", "name": "Research", "slug": "research"}],
                    assignments=[{"bookmarkId": "b1", "categoryId": "c1"}],
                )
                snapshot_path, hashes_path = write_inputs(
                    vault,
                    snapshot,
                    {"Generic - 1001.md": "generic"},
                    wave="evidence-with-a-different-name",
                    raw_subtree=raw_subtree,
                )
                result = run_cli("update", "--vault", vault, "--snapshot", snapshot_path, "--raw-hashes", hashes_path)
                self.assertEqual(result.returncode, 0, result.stderr)
                status = run_cli("status", "--vault", vault)
                self.assertEqual(status.returncode, 0, status.stderr)
                shown = json.loads(run_cli("show", "--vault", vault, "--id", "1001").stdout)["data"]
                expected_raw = "raw/" + "/".join(raw_subtree) + "/Twitter Bookmarks/Generic - 1001.md"
                self.assertEqual(shown["raw_path"], expected_raw)

    def test_dangling_state_symlink_and_vault_ancestor_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as td:
            root = Path(td)
            vault = root / "vault"
            vault.mkdir()
            snapshot = make_snapshot(
                bookmarks=[bookmark("b1", "1001", "Symlink")],
                categories=[{"id": "c1", "name": "Research", "slug": "research"}],
                assignments=[{"bookmarkId": "b1", "categoryId": "c1"}],
            )
            snapshot_path, hashes_path = write_inputs(vault, snapshot, {"Symlink - 1001.md": "symlink"})
            (vault / "state").symlink_to(root / "missing-state", target_is_directory=True)
            result = run_cli("update", "--vault", vault, "--snapshot", snapshot_path, "--raw-hashes", hashes_path)
            self.assertEqual(json.loads(result.stdout)["error"]["code"], "INVALID_INPUT")

            real = root / "real"
            real.mkdir()
            aliased_vault = real / "vault"
            aliased_vault.mkdir()
            s2, h2 = write_inputs(aliased_vault, snapshot, {"Symlink - 1001.md": "symlink"})
            alias_parent = root / "alias-parent"
            alias_parent.symlink_to(real, target_is_directory=True)
            result = run_cli("update", "--vault", alias_parent / "vault", "--snapshot", alias_parent / "vault" / "source-evidence" / "wave-1" / "evidence.json", "--raw-hashes", alias_parent / "vault" / "source-evidence" / "wave-1" / "raw-sha256.json")
            self.assertEqual(json.loads(result.stdout)["error"]["code"], "INVALID_INPUT")


if __name__ == "__main__":
    unittest.main()
