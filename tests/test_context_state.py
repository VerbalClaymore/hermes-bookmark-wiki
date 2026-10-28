import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from bookmark_wiki import context_state
from bookmark_wiki.archive import _update_lock


NOW = "2026-09-26T12:00:00Z"
REV = "a" * 64



def frozen_store(root):
    """Build a fresh synthetic two-reservation baseline; never read live state."""
    state = root / "state/context-pilot/pilot-v1"
    attempts = state / "attempts"
    events = state / "events"
    cache = state / "cache"
    attempts.mkdir(parents=True)
    events.mkdir()
    cache.mkdir()
    for sequence in range(1, 15):
        kind = "acquisition" if sequence <= 12 else "http"
        record = {"schema_version": 1, "sequence": sequence, "kind": kind,
                  "attempt_id": f"{kind}-{sequence:06d}", "reserved_at": NOW,
                  "metadata": {"fixture": "synthetic"}}
        (attempts / f"{sequence:06d}-{kind}.json").write_bytes(
            (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode())
    for sequence, attempt_id in ((15, "model-000015"), (16, "model-000016")):
        record = {"schema_version": 1, "sequence": sequence, "kind": "model",
                  "attempt_id": attempt_id, "reserved_at": NOW,
                  "metadata": {"cache_key": context_state.CONTINUATION_PARENT_CACHE_KEY,
                               "call_number": 1}}
        (attempts / f"{sequence:06d}-model.json").write_bytes(
            (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode())
    for sequence in range(1, 31):
        (events / f"{sequence:06d}.json").write_bytes(b"{}\n")
    evidence = root / "context-evidence/sha256"
    evidence.mkdir(parents=True)
    evidence_digests = {}
    for name, payload in (("parent-a.json", b"synthetic parent a\n"),
                          ("parent-b.json", b"synthetic parent b\n"),
                          ("collision.json", b"synthetic collision\n"),
                          ("rejected.json", b"synthetic rejected cache\n")):
        digest = hashlib.sha256(payload).hexdigest()
        evidence_path = evidence / f"{digest}.json"
        evidence_path.write_bytes(payload)
        evidence_digests[f"context-evidence/sha256/{digest}.json"] = digest
    rejected_path = cache / "ccd1ccdb305d256c246551e41e0e8e40b0e20d68d3ff46d186c9ea604bf375ce-rejected.json"
    rejected_path.write_bytes(b"synthetic rejected cache\n")
    context_state.CONTINUATION_PARENT_RESERVATIONS = {
        "model-000015": ("attempts/000015-model.json", hashlib.sha256((attempts / "000015-model.json").read_bytes()).hexdigest()),
        "model-000016": ("attempts/000016-model.json", hashlib.sha256((attempts / "000016-model.json").read_bytes()).hexdigest()),
    }
    context_state.CONTINUATION_PARENT_EVENTS = {
        "events/000011.json": hashlib.sha256((events / "000011.json").read_bytes()).hexdigest(),
        "events/000030.json": hashlib.sha256((events / "000030.json").read_bytes()).hexdigest(),
    }
    context_state.CONTINUATION_PARENT_EVIDENCE = {
        **evidence_digests,
        "state/context-pilot/pilot-v1/cache/ccd1ccdb305d256c246551e41e0e8e40b0e20d68d3ff46d186c9ea604bf375ce-rejected.json": hashlib.sha256(b"synthetic rejected cache\n").hexdigest(),
    }
    return context_state.ContextStore(root)


def axis(state, method="local", scope="synthetic", refs=None, reason="checked"):
    return {
        "state": state,
        "method": method,
        "scope": scope,
        "checked_at": NOW,
        "evidence_refs": refs or [{"path": "context-evidence/sha256/example.txt", "sha256": "b" * 64}],
        "reason": reason,
    }


def unit(discovery="no_media_found_in_scope", acquisition="not_needed", analysis="not_applicable", **extra):
    value = {
        "unit_id": "unit-synthetic-1",
        "source_id": "x:1234567890",
        "source_revision": REV,
        "channel": "source_text",
        "discovery": axis(discovery, scope="source text", reason="text-only scope has no visual attachment"),
        "acquisition": axis(acquisition, scope="local snapshot", reason="no remote acquisition needed"),
        "analysis": axis(analysis, method="none", scope="local metadata", reason="nonvisual source text"),
    }
    value.update(extra)
    return value


class ContextStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = context_state.ContextStore(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def locked(self):
        return self.store.locked()

    def test_exhaustive_matrix_and_metadata_are_enforced(self):
        valid = [
            ("not_checked", "pending", "pending"),
            ("not_checked", "pending", "deferred"),
            ("unable_to_determine", "pending", "deferred"),
            ("unable_to_determine", "failed", "failed"),
            ("no_media_found_in_scope", "not_needed", "not_applicable"),
            ("media_found", "pending", "pending"),
            ("media_found", "pending", "deferred"),
            ("media_found", "captured", "pending"),
            ("media_found", "captured", "succeeded_findings"),
            ("media_found", "captured", "succeeded_no_findings"),
            ("media_found", "captured", "failed"),
            ("media_found", "captured", "deferred"),
            ("media_found", "unavailable", "deferred"),
            ("media_found", "failed", "failed"),
            ("not_checked", "unavailable", "deferred"),
            ("not_checked", "failed", "failed"),
        ]
        for discovery, acquisition, analysis in valid:
            with self.subTest(discovery=discovery, acquisition=acquisition, analysis=analysis):
                candidate = unit(discovery, acquisition, analysis)
                context_state.validate_unit(candidate)
        for bad in [("no_media_found_in_scope", "failed", "failed"), ("media_found", "not_needed", "not_applicable"), ("unable_to_determine", "captured", "pending")]:
            with self.subTest(bad=bad):
                with self.assertRaises(context_state.InvalidState):
                    context_state.validate_unit(unit(*bad))
        bad = unit()
        bad["discovery"]["reason"] = ""
        with self.assertRaises(context_state.InvalidState):
            context_state.validate_unit(bad)

    def test_transitions_are_append_only_and_success_cannot_regress(self):
        previous = unit("media_found", "captured", "succeeded_no_findings")
        same = json.loads(json.dumps(previous))
        context_state.validate_transition(previous, same)
        regressed = unit("media_found", "captured", "failed")
        with self.assertRaises(context_state.InvalidState):
            context_state.validate_transition(previous, regressed)
        fresh_failure = unit("media_found", "captured", "failed", attempt_id="attempt-2")
        with self.assertRaises(context_state.InvalidState):
            context_state.validate_transition(previous, fresh_failure)
        pending = unit("media_found", "pending", "pending", attempt_id="attempt-2")
        context_state.validate_transition(unit("media_found", "pending", "pending"), pending)

    def test_transition_rejects_identity_changes_failed_deferred_reused_attempt_and_success_metadata_edits(self):
        previous = unit("media_found", "captured", "failed", attempt_id="attempt-1")
        with self.assertRaises(context_state.InvalidState):
            context_state.validate_transition(previous, dict(previous, source_id="x:999"))
        with self.assertRaises(context_state.InvalidState):
            context_state.validate_transition(previous, dict(previous, channel="video"))
        failed_to_deferred = unit("media_found", "captured", "deferred", attempt_id="attempt-2")
        with self.assertRaises(context_state.InvalidState):
            context_state.validate_transition(previous, failed_to_deferred)
        reused = unit("media_found", "captured", "succeeded_no_findings", attempt_id="attempt-1")
        with self.assertRaises(context_state.InvalidState):
            context_state.validate_transition(unit("media_found", "captured", "failed", attempt_id="attempt-1"), reused)
        successful = unit("media_found", "captured", "succeeded_no_findings")
        edited = json.loads(json.dumps(successful))
        edited["analysis"]["reason"] = "rewritten provenance"
        edited["attempt_id"] = "attempt-2"
        with self.assertRaises(context_state.InvalidState):
            context_state.validate_transition(successful, edited)

    def test_artifacts_are_exclusive_and_cache_requires_acceptance(self):
        with self.locked():
            ref = self.store.write_artifact(b"synthetic evidence", "txt")
            self.assertEqual(ref["sha256"], hashlib.sha256(b"synthetic evidence").hexdigest())
            self.assertEqual(self.store.write_artifact(b"synthetic evidence", "txt"), ref)
            artifact_path = self.root / ref["path"]
            artifact_path.write_bytes(b"changed")
            with self.assertRaises(context_state.IntegrityError):
                self.store.write_artifact(b"synthetic evidence", "txt")
            key = context_state.cache_key({"stage": "model", "input": [ref["sha256"]]})
            candidate = {"cache_key": key, "status": "candidate", "request": {"input": [ref["sha256"]]}, "response": {"ok": True}}
            self.store.write_cache(key, candidate)
            self.assertIsNone(self.store.lookup_cache(key, candidate["request"]))
            accepted = dict(candidate, status="accepted", acceptance={"decision": "accepted", "checked_artifact_refs": [ref]})
            with self.assertRaises(context_state.IntegrityError):
                self.store.write_cache(key, accepted)

    def test_budget_is_persistent_bounded_and_requires_shared_lock(self):
        with self.assertRaises(context_state.LockRequired):
            self.store.reserve("acquisition")
        with self.locked():
            for _ in range(12):
                self.store.reserve("acquisition")
            with self.assertRaises(context_state.BudgetExceeded):
                self.store.reserve("acquisition")
            self.store.reserve("model")
        reloaded = context_state.ContextStore(self.root)
        with reloaded.locked():
            reloaded.reserve("model")
            with self.assertRaises(context_state.BudgetExceeded):
                reloaded.reserve("model")
        with _update_lock(self.root):
            with self.assertRaises(context_state.Locked):
                with reloaded.locked():
                    pass
        with self.assertRaises(context_state.InvalidInput):
            context_state.ContextStore(self.root, run_id="other")

    def test_malformed_or_noncontinuous_ledger_fails_closed_after_restart(self):
        with self.store.locked():
            self.store.reserve("acquisition", {"synthetic": True})
        path = self.root / "state" / "context-pilot" / "pilot-v1" / "attempts" / "000001-acquisition.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        record["sequence"] = 2
        path.write_text(json.dumps(record), encoding="utf-8")
        restarted = context_state.ContextStore(self.root)
        with self.assertRaises(context_state.IntegrityError):
            with restarted.locked():
                restarted.reserve("acquisition")

    def test_accepted_cache_verifies_call_plan_response_attempt_and_all_hashes(self):
        def artifact(value):
            return self.store.write_artifact(json.dumps(value, sort_keys=True, separators=(",", ":")).encode(), "json")

        with self.store.locked():
            input_ref = self.store.write_artifact(b"synthetic input", "txt")
            prompt_ref = self.store.write_artifact(b"synthetic prompt", "txt")
            schema_ref = artifact({"type": "object"})
            plan = {
                "call_number": 1,
                "units": [{"unit_id": "unit-synthetic-1", "source_id": "x:1234567890", "source_revision": REV, "method": "text", "limitations": ["synthetic"], "artifact_ids": ["artifact-001"]}],
                "artifacts": [{"artifact_id": "artifact-001", "path": input_ref["path"], "sha256": input_ref["sha256"], "media_type": "text/plain"}],
                "prompt_sha256": prompt_ref["sha256"], "output_schema_sha256": schema_ref["sha256"], "requested_model": "gpt-6-sol", "prompt_version": "context-pilot-1", "schema_version": 1,
            }
            plan_ref = artifact(plan)
            response = {"schema_version": 1, "results": [{"unit_id": "unit-synthetic-1", "outcome": "succeeded_no_findings", "findings": [], "reason": "No bounded synthetic finding.", "limitations": ["synthetic"]}]}
            response_ref = artifact(response)
            request = {"stage": "context-pilot", "provider": "openai-chatgpt-codex", "requested_model": "gpt-6-sol", "prompt_version": "context-pilot-1", "schema_version": 1,
                       "source_revisions": [REV], "ordered_input_hashes": [input_ref["sha256"]], "prompt_sha256": prompt_ref["sha256"], "output_schema_sha256": schema_ref["sha256"],
                       "input_artifact_refs": [input_ref, prompt_ref, schema_ref], "expected_unit_ids": ["unit-synthetic-1"], "artifact_ids_by_unit": {"unit-synthetic-1": ["artifact-001"]}}
            self.store._validate_request_binding(request, plan)
            for field, value in [("requested_model", "wrong-model"), ("source_revisions", []), ("ordered_input_hashes", []), ("prompt_sha256", "e"*64), ("output_schema_sha256", "e"*64), ("expected_unit_ids", []), ("input_artifact_refs", [])]:
                with self.subTest(field=field), self.assertRaises(context_state.IntegrityError):
                    self.store._validate_request_binding(dict(request, **{field:value}), plan)
            with self.assertRaises(context_state.IntegrityError):
                self.store._validate_request_binding({"arbitrary": True}, plan)
            key = context_state.cache_key(request)
            reserved = self.store.reserve("model", {"cache_key": key})
            attempt = {"schema_version": 1, "attempt_id": reserved["attempt_id"], "call_plan_ref": plan_ref, "cache_key": key, "started_at": NOW, "finished_at": NOW, "outcome": "candidate", "error_code": None, "model_response_ref": response_ref, "requested_model": "gpt-6-sol", "observed_model": "unknown", "auth_mode": "chatgpt", "provider": "openai-chatgpt-codex", "usage": None, "wall_seconds": 1.0, "event_log_ref": None}
            attempt_ref = artifact(attempt)
            acceptance = {"schema_version": 1, "cache_key": key, "attempt_id": reserved["attempt_id"], "model_response_ref": response_ref, "decision": "accepted", "checked_at": NOW, "reviewer_method": "coordinator", "reason": "synthetic evidence checked", "checked_artifact_refs": [input_ref]}
            record = {"schema_version": 1, "status": "accepted", "cache_key": key, "request": request, "call_plan_ref": plan_ref, "attempt_ref": attempt_ref, "model_response_ref": response_ref, "acceptance": acceptance}
            self.store.write_cache(key, record)
            self.assertEqual(self.store.lookup_cache(key, request), record)
            response_path = self.root / response_ref["path"]
            response_path.write_bytes(response_path.read_bytes().replace(b"No bounded synthetic finding.", b"tampered response"))
            with self.assertRaises(context_state.IntegrityError):
                self.store.lookup_cache(key, request)

    def test_analysis_record_rejects_legacy_empty_and_requires_provenance(self):
        record = {
            "method": "text",
            "coverage": "full synthetic text",
            "requested_model": "gpt-6-sol",
            "observed_model": "unknown",
            "auth_mode": "chatgpt",
            "prompt_version": "context-pilot-1",
            "schema_version": 1,
            "input_artifact_hashes": ["b" * 64],
            "checked_at": NOW,
            "attempt_id": "model-000001",
            "usage": None,
            "wall_seconds": 1.0,
            "outcome": "succeeded_no_findings",
            "reason": "synthetic response contained no supported finding",
        }
        context_state.validate_analysis_record(record)
        for field in ("method", "coverage", "reason"):
            bad = dict(record, **{field: ""})
            with self.assertRaises(context_state.InvalidState):
                context_state.validate_analysis_record(bad)

    def test_continuation_reservation_rejects_substituted_original_history(self):
        store = frozen_store(self.root)
        path = self.root / "state/context-pilot/pilot-v1/attempts/000015-model.json"
        data = path.read_bytes().replace(b"model-000015", b"model-999999")
        path.write_bytes(data)
        with store.locked():
            auth = {"authorization_kind": "continuation-v1", "authorization_scope": "one-image-supplemental-continuation", "parent_cache_key": context_state.CONTINUATION_PARENT_CACHE_KEY, "request_digest": context_state.CONTINUATION_REQUEST_DIGEST}
            with self.assertRaises(context_state.IntegrityError):
                store.reserve_model_continuation(context_state.CONTINUATION_PARENT_CACHE_KEY, context_state.CONTINUATION_REQUEST_DIGEST, auth)

    def test_continuation_reservation_is_separate_one_time_bound_path(self):
        store = frozen_store(self.root)
        parent = context_state.CONTINUATION_PARENT_CACHE_KEY
        request_digest = context_state.CONTINUATION_REQUEST_DIGEST
        authorization = {"authorization_kind": "continuation-v1", "authorization_scope": "one-image-supplemental-continuation", "parent_cache_key": parent, "request_digest": request_digest}
        with store.locked():
            reservation = store.reserve_model_continuation(parent, request_digest, authorization)
            self.assertEqual(reservation["attempt_id"], "model-000003")
            self.assertEqual(reservation["metadata"]["authorization_kind"], "continuation-v1")
            self.assertEqual(sum(kind == "model" for _, kind, _, _ in store._ledger()), 3)
            with self.assertRaises(context_state.BudgetExceeded):
                store.reserve_model_continuation(parent, request_digest, authorization)

    def test_continuation_rejects_unbound_authorization_tamper_and_accepted_parent(self):
        parent = context_state.CONTINUATION_PARENT_CACHE_KEY
        request_digest = context_state.CONTINUATION_REQUEST_DIGEST
        authorization = {
            "authorization_kind": "continuation-v1",
            "authorization_scope": "one-image-supplemental-continuation",
            "parent_cache_key": parent,
            "request_digest": request_digest,
        }
        for bad in (
            dict(authorization, authorization_kind="arbitrary-label"),
            dict(authorization, parent_cache_key="0" * 64),
            dict(authorization, request_digest="0" * 64),
            dict(authorization, extra=True),
        ):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as td:
                store = frozen_store(Path(td))
                with store.locked():
                    with self.assertRaises(context_state.IntegrityError):
                        store.reserve_model_continuation(parent, request_digest, bad)

        store = frozen_store(self.root)
        with store.locked():
            accepted = self.root / "state/context-pilot/pilot-v1/cache" / f"{parent}-accepted.json"
            accepted.parent.mkdir(parents=True, exist_ok=True)
            accepted.write_text("{}", encoding="utf-8")
            with self.assertRaises(context_state.IntegrityError):
                store.reserve_model_continuation(parent, request_digest, authorization)


if __name__ == "__main__":
    unittest.main()
