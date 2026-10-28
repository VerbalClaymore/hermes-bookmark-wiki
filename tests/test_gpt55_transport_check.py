import json
import os
import shutil
import stat
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import gpt55_transport_check as check
from bookmark_wiki import context_state, subscription

PRIVATE_TEST_VAULT = os.environ.get('BOOKMARK_WIKI_PRIVATE_TEST_VAULT')
VAULT = Path(PRIVATE_TEST_VAULT or '/unconfigured-private-test-vault')
BINARY = VAULT / 'tools/codex/0.157.0/codex-aarch64-apple-darwin'


def raw_response(response, model=check.GPT55_MODEL, provider='openai'):
    events = [
        {'type': 'thread.started', 'provider': provider, 'model': model},
        {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(response)}},
        {'type': 'turn.completed'},
    ]
    return ''.join(json.dumps(item) + '\n' for item in events).encode()


def valid_response(citation='artifact-002'):
    return {'schema_version': 1, 'results': [{'unit_id': 'screenshot-image', 'outcome': 'succeeded_findings', 'findings': [{'claim': 'visible', 'evidence_refs': [citation]}], 'reason': 'visible evidence', 'limitations': ['single image']}]}


def actual_transport(raw: bytes, stderr: bytes = b"", capture: dict | None = None):
    """Patch only run_codex's Popen seam; execute its real protocol loop."""
    class FakeProcess:
        pid = None

        def __init__(self, *args, **kwargs):
            self.stdin_r, stdin_w = os.pipe()
            self.stdout, self.stdout_w = os.pipe()
            self.stderr, self.stderr_w = os.pipe()
            self.stdin = os.fdopen(stdin_w, "wb", buffering=0)
            self.stdout = os.fdopen(self.stdout, "rb", buffering=0)
            self.stderr = os.fdopen(self.stderr, "rb", buffering=0)
            def emit():
                os.write(self.stdout_w, raw)
                os.close(self.stdout_w)
                os.write(self.stderr_w, stderr)
                os.close(self.stderr_w)
            self.worker = threading.Thread(target=emit, daemon=True)
            self.worker.start()

        def wait(self, timeout=None):
            self.worker.join(timeout)
            return 0

        def terminate(self):
            return None

        def kill(self):
            return None

    def factory(*args, **kwargs):
        if capture is not None:
            capture.update(command=args[0], kwargs=kwargs)
        return FakeProcess(*args, **kwargs)

    defaults = dict(subscription.run_codex.__kwdefaults__ or {})
    defaults["popen_factory"] = factory
    return patch.object(subscription.run_codex, "__kwdefaults__", defaults)


@unittest.skipUnless(PRIVATE_TEST_VAULT, 'Requires explicit BOOKMARK_WIKI_PRIVATE_TEST_VAULT and preserved private fixtures')
class Gpt55BehaviorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = check._synthetic_store(self.root)
        for ref in check.request_identity()['input_artifact_refs']:
            source = VAULT / ref['path']
            target = self.root / ref['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        self.executable = self.root / 'codex'
        shutil.copyfile(BINARY, self.executable)
        self.executable.chmod(self.executable.stat().st_mode | stat.S_IXUSR)

    def tearDown(self):
        self.tmp.cleanup()

    def approval(self):
        return {'contract_id': 'BookmarkWiki_Gpt55TransportCheck_v1', 'approval_scope': 'one-image-supplemental-transport-check', 'parent_cache_key': check.PARENT_CACHE_KEY, 'request_digest': check.REQUEST_DIGEST, 'approval_id': 'review-1', 'approved': True}

    def test_command_is_strict_and_model_bound(self):
        command = subscription.build_codex_command(str(self.executable), str(self.root / check.request_identity()['input_artifact_refs'][-1]['path']), [str(self.root / check.request_identity()['input_artifact_refs'][1]['path'])], model=check.GPT55_MODEL)
        self.assertEqual(command[command.index('--model') + 1], 'gpt-5.5')
        self.assertIn('--ignore-user-config', command)
        self.assertIn('--strict-config', command)
        self.assertIn('features.code_mode=false', command)
        self.assertNotIn('--enable', command)

    def test_preflight_is_real_and_tampering_blocks_before_reservation(self):
        self.assertEqual(check.preflight(self.root, self.executable)['state'], 'READY')
        attempt_dir = self.store.state_root / 'attempts'
        original = (attempt_dir / '000003-model.json').read_bytes()
        (attempt_dir / '000003-model.json').write_bytes(original + b' tampered')
        with self.assertRaisesRegex(context_state.ContextStateError, 'three-model|binding|ledger'):
            check.preflight(self.root, self.executable)
        self.assertEqual(len(list(attempt_dir.glob('*-model.json'))), 3)

    def test_preflight_rejects_tampered_frozen_artifact_and_executable(self):
        image = self.root / check.request_identity()['input_artifact_refs'][1]['path']
        image.write_bytes(image.read_bytes() + b'x')
        with self.assertRaisesRegex(RuntimeError, 'frozen reference'):
            check.preflight(self.root, self.executable)
        image.write_bytes((VAULT / check.request_identity()['input_artifact_refs'][1]['path']).read_bytes())
        self.executable.write_bytes(self.executable.read_bytes() + b'x')
        with self.assertRaisesRegex(RuntimeError, 'executable hash'):
            check.preflight(self.root, self.executable)

    def test_invoke_validates_schema_citation_and_identity_without_model_process(self):
        bad = valid_response('artifact-001')
        capture = {}
        with actual_transport(raw_response(bad), capture=capture):
            outcome = check.invoke(self.root, self.executable, self.approval())
        command = capture['command']
        self.assertEqual(command[0], str(self.executable))
        self.assertIn('--model', command); self.assertEqual(command[command.index('--model') + 1], 'gpt-5.5')
        self.assertIn(str(self.root / check.request_identity()['input_artifact_refs'][1]['path']), command)
        self.assertIn(str(self.root / check.request_identity()['input_artifact_refs'][-1]['path']), command)
        self.assertEqual(capture['kwargs']['env']['PATH'], '/opt/homebrew/bin:/usr/bin:/bin')
        self.assertTrue((self.store.state_root / 'attempts' / '000004-model.json').is_file())
        self.assertEqual(outcome['state'], 'FAILED')
        self.assertEqual(outcome['error_code'], 'schema_invalid')
        self.assertEqual(len(list((self.store.state_root / 'attempts').glob('*-model.json'))), 4)
        self.assertTrue((self.store.state_root / 'cache' / f'{check.REQUEST_DIGEST}-rejected.json').is_file())

    def test_conflicting_identity_is_failed_and_missing_identity_is_not_fabricated(self):
        response = valid_response()
        with actual_transport(raw_response(response, model='gpt-6-sol')):
            outcome = check.invoke(self.root, self.executable, self.approval())
        self.assertIn(outcome['error_code'], {'provider_mismatch', 'model_mismatch'})

    def test_malformed_jsonl_is_failed_and_missing_identity_is_candidate_unknown(self):
        malformed = subscription.CodexResult('candidate', stdout=b'{not-json}\n', attempt_id='model-000004')
        with actual_transport(malformed.stdout):
            failed = check.invoke(self.root, self.executable, self.approval())
        self.assertEqual(failed['error_code'], 'malformed_response')

        root = Path(self.tmp.name) / 'missing-identity'
        root.mkdir()
        for ref in check.request_identity()['input_artifact_refs']:
            target = root / ref['path']; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(VAULT / ref['path'], target)
        shutil.copyfile(BINARY, root / 'codex')
        (root / 'codex').chmod((root / 'codex').stat().st_mode | stat.S_IXUSR)
        store = check._synthetic_store(root)
        no_identity = subscription.CodexResult('candidate', response=valid_response(), stdout=json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(valid_response())}}).encode() + b'\n' + json.dumps({'type': 'turn.completed'}).encode() + b'\n', attempt_id='model-000004')
        with actual_transport(no_identity.stdout):
            unknown = check.invoke(root, root / 'codex', self.approval())
        self.assertEqual(unknown['state'], 'CANDIDATE')
        self.assertEqual(unknown['error_code'], 'identity_unknown')

    def test_collision_preserves_bytes_and_failure_history(self):
        cache = self.store.state_root / 'cache'
        cache.mkdir(parents=True, exist_ok=True)
        old = b'{"old":true}\n'
        (cache / f'{check.REQUEST_DIGEST}-candidate.json').write_bytes(old)
        response = valid_response()
        result = subscription.CodexResult('candidate', response=response, stdout=raw_response(response), attempt_id='model-000004')
        with self.store.locked():
            reservation = self.store.reserve_model_transport_check(check.PARENT_CACHE_KEY, check.REQUEST_DIGEST, check.AUTHORIZATION)
            outcome = check._persist_failure(self.store, reservation, check.request_identity(), result, '2026-10-13T00:00:00Z', check.REQUEST_DIGEST, True)
        self.assertEqual(outcome['error_code'], 'cache_collision')
        self.assertEqual((cache / f'{check.REQUEST_DIGEST}-candidate.json').read_bytes(), old)
        self.assertIsNotNone(outcome['history_ref'])
        evidence = json.loads((self.root / outcome['history_ref']['path']).read_text())
        self.assertTrue(evidence['cache_collision'])
        self.assertEqual(evidence['preserved_candidate_ref']['path'], f'state/context-pilot/pilot-v1/cache/{check.REQUEST_DIGEST}-candidate.json')
        self.assertEqual(evidence['preserved_candidate_ref']['sha256'], check._sha(old))
        with self.store.locked():
            self.assertEqual(self.store.read_ref(evidence['preserved_candidate_ref']), old)

    def test_preflight_missing_layout_is_blocked_without_writes(self):
        root = Path(self.tmp.name) / 'missing-layout'
        root.mkdir()
        for ref in check.request_identity()['input_artifact_refs']:
            target = root / ref['path']; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(VAULT / ref['path'], target)
        executable = root / 'codex'; shutil.copyfile(BINARY, executable)
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
        before = sorted(str(path.relative_to(root)) for path in root.rglob('*'))
        with self.assertRaisesRegex(Exception, 'existing attempt ledger'):
            check.preflight(root, executable)
        after = sorted(str(path.relative_to(root)) for path in root.rglob('*'))
        self.assertEqual(before, after)

    def test_missing_or_bad_approval_blocks_without_reservation(self):
        with self.assertRaisesRegex(RuntimeError, 'approval'):
            check.invoke(self.root, self.executable, {})
        self.assertEqual(len(list((self.store.state_root / 'attempts').glob('*-model.json'))), 3)

    def test_persistence_failure_is_explicit_and_reservation_is_consumed(self):
        with self.store.locked():
            reservation = self.store.reserve_model_transport_check(check.PARENT_CACHE_KEY, check.REQUEST_DIGEST, check.AUTHORIZATION)
            result = subscription.CodexResult('failed', error_code='process_error', attempt_id=reservation['attempt_id'])
            with patch.object(self.store, 'write_artifact', side_effect=OSError('disk full')):
                outcome = check._persist_failure(self.store, reservation, check.request_identity(), result, '2026-10-13T00:00:00Z', check.REQUEST_DIGEST)
        self.assertEqual(outcome['state'], 'PERSISTENCE_FAILED')
        self.assertTrue(outcome['consumed_reservation'])
        self.assertFalse(outcome['retry'])
        self.assertEqual(outcome['recovery_ref'], None)
        with self.assertRaises(Exception):
            check.preflight(self.root, self.executable)

    def test_total_write_failure_reports_bounded_limitation_without_refs(self):
        with self.store.locked():
            reservation = self.store.reserve_model_transport_check(check.PARENT_CACHE_KEY, check.REQUEST_DIGEST, check.AUTHORIZATION)
            result = subscription.CodexResult('failed', error_code='process_error', attempt_id=reservation['attempt_id'])
            with patch.object(self.store, 'write_artifact', side_effect=OSError('disk unavailable')), patch.object(self.store, 'append_event', side_effect=OSError('disk unavailable')):
                outcome = check._persist_failure(self.store, reservation, check.request_identity(), result, '2026-10-13T00:00:00Z', check.REQUEST_DIGEST)
        self.assertEqual(outcome['state'], 'PERSISTENCE_FAILED')
        self.assertIsNone(outcome['attempt_ref'])
        self.assertIsNone(outcome['recovery_ref'])
        self.assertTrue(outcome['persistence_failed'])
        self.assertTrue(outcome['consumed_reservation'])
        self.assertFalse(outcome['retry'])

    def test_cli_preflight_is_runnable_and_read_only(self):
        proc = subprocess.run(['python3', '-m', 'gpt55_transport_check', 'preflight', '--vault', str(self.root), '--executable', str(self.executable)], cwd=str(Path(__file__).parents[1]), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)['state'], 'READY')


if __name__ == '__main__':
    unittest.main()
