"""Frozen, candidate-only gpt-5.5 transport check.

The preflight is read-only.  The invocation path is intentionally gated by an
external approval record and uses the real subscription transport exactly once.
It never inspects image contents and never writes an accepted cache.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
from pathlib import Path

from bookmark_wiki import context_state, subscription

GPT55_MODEL = "gpt-5.5"
GPT55_PROVIDER = "openai-chatgpt-codex"
EXECUTABLE_SHA256 = "ad0be20d04e2ba6146ecdb51d7f8b7b0fe15420a15dc9b0057518d858f1f3714"
EXECUTABLE_VERSION = "codex-cli 0.157.0"
IMAGE_SHA256 = "705d2882a1868c01cf843c9434c53f4749a89908dde7a50408e1584bf24f13ba"
PROMPT_SHA256 = "93a99b288018ac6942acc965936ad523796a35e80785b5e73619cea02586c877"
SCHEMA_SHA256 = "b146975db55793c0db0cea19877f116a8afe3509529b9b5ba5c84cc15ff8ef14"
REQUEST_DIGEST = context_state.GPT55_TRANSPORT_REQUEST_DIGEST
AUTHORIZATION = context_state.GPT55_TRANSPORT_AUTHORIZATION
PARENT_CACHE_KEY = context_state.CONTINUATION_PARENT_CACHE_KEY
PROMPT_VERSION = "gpt55-transport-check-1"
STAGE = "context-pilot-gpt55-transport-check-v1"
CANDIDATE_ONLY = True
PROCESSES_IMAGE_CONTENT = False
PROMPT = ('Analyze only the single attached screenshot artifact for a private source-grounded wiki. '
          'Return exactly schema-valid JSON with one result for unit_id "screenshot-image". '
          'Use only visible evidence in the attached image; do not use tools or infer hidden context. '
          'Keep claims short and specific and cite only artifact-002. Distinguish visible evidence '
          'from post-author claims. Use succeeded_no_findings only after inspecting the image and '
          'explain why. No invented license, price, hardware, endorsement, or unseen workflow detail.')


def _encoded(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _ref(vault: Path, relative: str, digest: str) -> dict:
    path = vault / Path(*relative.split("/"))
    if not path.is_file() or path.is_symlink() or _sha(path.read_bytes()) != digest:
        raise RuntimeError(f"frozen reference mismatch: {relative}")
    return {"path": relative, "sha256": digest}


def request_identity() -> dict:
    return {
        "source_revisions": ["3c78741ccdb67fdc3524140588c0abf865c1138ce4d4fcd480e4d66b5ab773e"],
        "ordered_input_hashes": ["61ab558723d8632989227aefa041624a395b9a016520654ee5cfc708c4e906fa", IMAGE_SHA256],
        "stage": STAGE, "provider": GPT55_PROVIDER, "requested_model": GPT55_MODEL,
        "prompt_version": PROMPT_VERSION, "schema_version": 1, "prompt_sha256": PROMPT_SHA256,
        "output_schema_sha256": SCHEMA_SHA256, "expected_unit_ids": ["screenshot-image"],
        "artifact_ids_by_unit": {"screenshot-image": ["artifact-001", "artifact-002"]},
        "input_artifact_refs": [
            {"path": "context-evidence/sha256/61ab558723d8632989227aefa041624a395b9a016520654ee5cfc708c4e906fa.json", "sha256": "61ab558723d8632989227aefa041624a395b9a016520654ee5cfc708c4e906fa"},
            {"path": f"context-evidence/sha256/{IMAGE_SHA256}.jpg", "sha256": IMAGE_SHA256},
            {"path": f"context-evidence/sha256/{PROMPT_SHA256}.txt", "sha256": PROMPT_SHA256},
            {"path": f"context-evidence/sha256/{SCHEMA_SHA256}.json", "sha256": SCHEMA_SHA256},
        ],
    }


def _verify_model_history(store: context_state.ContextStore, *, read_only: bool = False) -> None:
    entries = store._ledger(create=not read_only)
    models = [row for row in entries if row[1] == "model"]
    if len(models) != 3:
        raise RuntimeError("preserved three-model history is required")
    if any(row[3]["attempt_id"] == "model-000004" for row in models):
        raise RuntimeError("fourth reservation already exists")
    continuations = [row for row in models if row[3]["metadata"].get("authorization_kind") == "continuation-v1"]
    if len(continuations) != 1:
        raise RuntimeError("preserved continuation reservation is missing")
    if continuations[0][3]["metadata"] != {
        "authorization_kind": "continuation-v1", "authorization_scope": "one-image-supplemental-continuation",
        "parent_cache_key": PARENT_CACHE_KEY, "request_digest": context_state.CONTINUATION_REQUEST_DIGEST, "call_number": 1,
    }:
        raise RuntimeError("continuation reservation binding mismatch")
    if any(row[3]["metadata"].get("authorization_kind") == "gpt55-transport-check-v1" for row in models):
        raise RuntimeError("gpt-5.5 reservation already exists")
    # When operating on the preserved production vault, bind the complete
    # pre-existing history and its immutable rejected evidence by bytes.
    attempts = store.state_root / "attempts"
    if (attempts / "000015-model.json").exists():
        frozen = {
            "attempts/000015-model.json": context_state.CONTINUATION_PARENT_RESERVATIONS["model-000015"][1],
            "attempts/000016-model.json": context_state.CONTINUATION_PARENT_RESERVATIONS["model-000016"][1],
            "attempts/000017-model.json": "6bc65d41d38095683ef5ef237e4006702004b52fd63fe8c70105ed470300dcf7",
            "events/000011.json": context_state.CONTINUATION_PARENT_EVENTS["events/000011.json"],
            "events/000030.json": context_state.CONTINUATION_PARENT_EVENTS["events/000030.json"],
            "events/000031.json": "d67f3b93777e44851dfd301bec8842fe1710cd529f29d85b1960f2c8e6204476",
            "cache/ccd1ccdb305d256c246551e41e0e8e40b0e20d68d3ff46d186c9ea604bf375ce-rejected.json": "b85410ffc07b19a7f55313ca363ee40e255837ea980ffdc39f4c93c7fba1ba39",
            "../../../../context-evidence/sha256/ba2ac96d41aba82d9288473c1e56e7385da2b894fdfc4d7c26c5b1347ef9ee7e.json": "ba2ac96d41aba82d9288473c1e56e7385da2b894fdfc4d7c26c5b1347ef9ee7e",
        }
        for relative, digest in frozen.items():
            path = store.state_root / Path(*relative.split("/"))
            if relative.startswith("../../../../"):
                path = store.root / "context-evidence/sha256/ba2ac96d41aba82d9288473c1e56e7385da2b894fdfc4d7c26c5b1347ef9ee7e.json"
            if not path.is_file() or _sha(path.read_bytes()) != digest:
                raise RuntimeError("preserved history/cache/evidence bytes changed")


def preflight(vault: Path | str, executable: Path | str) -> dict:
    """Perform the complete read-only gate; no reservation or model call."""
    root, binary = Path(vault).absolute(), Path(executable).absolute()
    if not root.is_dir() or root.is_symlink():
        raise RuntimeError("vault must be an existing non-symlink directory")
    if not binary.is_file() or binary.is_symlink() or _sha(binary.read_bytes()) != EXECUTABLE_SHA256:
        raise RuntimeError("pinned executable hash mismatch")
    version = subprocess.run([str(binary), "--version"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10, check=False)
    if version.returncode != 0 or version.stdout.strip() != EXECUTABLE_VERSION:
        raise RuntimeError("pinned executable version mismatch")
    refs = request_identity()["input_artifact_refs"]
    for ref in refs:
        _ref(root, ref["path"], ref["sha256"])
    prompt = (root / refs[-2]["path"]).read_bytes()
    schema = (root / refs[-1]["path"]).read_bytes()
    if prompt != PROMPT.encode() or _sha(_encoded(subscription.MODEL_RESPONSE_SCHEMA)) != SCHEMA_SHA256 or schema != _encoded(subscription.MODEL_RESPONSE_SCHEMA):
        raise RuntimeError("frozen prompt/schema bytes mismatch")
    if _sha(prompt) != PROMPT_SHA256:
        raise RuntimeError("frozen prompt digest mismatch")
    store = context_state.ContextStore(root)
    # Do not acquire the mutating store lock or call constructor helpers here:
    # a missing state layout is a blocked, zero-write preflight.
    _verify_model_history(store, read_only=True)
    cache_dir = store.state_root / "cache"
    if not cache_dir.is_dir() or cache_dir.is_symlink():
        raise RuntimeError("existing cache directory is required")
    for path in cache_dir.glob("*"):
        if path.name.startswith(REQUEST_DIGEST) or path.name.startswith(REQUEST_DIGEST + "-") or path.name.startswith("model-000004"):
            raise RuntimeError("gpt-5.5 cache history already exists")
    events_dir = store.state_root / "events"
    if not events_dir.is_dir() or events_dir.is_symlink():
        raise RuntimeError("existing event directory is required")
    for path in events_dir.glob("*.json"):
        data = path.read_bytes()
        if REQUEST_DIGEST.encode() in data or b"gpt55-transport-check" in data or b"model-000004" in data:
            raise RuntimeError("gpt-5.5 event history already exists")
    return {"state": "READY", "model_calls": 0, "request_digest": REQUEST_DIGEST, "observed_model": "unknown"}


def _approval(record: object) -> None:
    required = {"contract_id", "approval_scope", "parent_cache_key", "request_digest", "approval_id", "approved"}
    if not isinstance(record, dict) or set(record) != required or record.get("contract_id") != "BookmarkWiki_Gpt55TransportCheck_v1" or record.get("approval_scope") != "one-image-supplemental-transport-check" or record.get("parent_cache_key") != PARENT_CACHE_KEY or record.get("request_digest") != REQUEST_DIGEST or record.get("approved") is not True or not isinstance(record.get("approval_id"), str) or not record["approval_id"]:
        raise RuntimeError("missing exact external gpt-5.5 approval record")


def _persist_failure(store, reservation, request, result, started, cache_key, collision: bool = False) -> dict:
    if collision:
        result.status, result.error_code = "failed", "cache_collision"
    response_ref = None
    logs_ref = None
    attempt_ref = None
    history_ref = None
    record_status = "candidate" if result.status == "candidate" and not collision else "rejected"
    record = None
    try:
        if result.response is not None and result.status == "candidate" and not collision:
            response_ref = store.write_artifact(_encoded(result.response), "json")
        logs_ref = store.write_artifact(_encoded({"stdout": result.stdout.decode("utf-8", "replace"), "stderr": result.stderr.decode("utf-8", "replace")}), "json")
        envelope = {"schema_version": 1, "attempt_id": reservation["attempt_id"], "cache_key": cache_key, "request": request, "status": result.status, "error_code": result.error_code, "model_response_ref": response_ref, "observed_model": "unknown", "observed_provider": "unknown", "candidate_only": True, "logs_ref": logs_ref, "started_at": started, "finished_at": _now(), "wall_seconds": result.wall_seconds, "usage": result.usage}
        attempt_ref = store.write_artifact(_encoded(envelope), "json")
        record = {"schema_version": 1, "status": record_status, "cache_key": cache_key, "request": request, "attempt_ref": attempt_ref, "model_response_ref": response_ref, "acceptance": None}
        if collision:
            preserved_path = store.state_root / "cache" / f"{cache_key}-candidate.json"
            payload = preserved_path.read_bytes()
            preserved_ref = {"path": preserved_path.relative_to(store.root).as_posix(), "sha256": _sha(payload)}
            store.read_ref(preserved_ref)
            history_ref = store.write_artifact(_encoded({"history_kind": "model-attempt", "attempt_id": reservation["attempt_id"], "cache_key": cache_key, "cache_collision": True, "preserved_candidate_ref": preserved_ref}), "json")
        try:
            store.write_cache(cache_key, record)
        except context_state.IntegrityError:
            history_ref = store.write_artifact(_encoded({"history_kind": "model-attempt", "attempt_id": reservation["attempt_id"], "cache_key": cache_key, "record": record, "cache_write_error": "immutable cache collision"}), "json")
        store.append_event("model_attempt", attempt_id=reservation["attempt_id"], cache_key=cache_key, status=record_status, error_code=result.error_code, attempt_ref=attempt_ref, model_response_ref=response_ref, logs_ref=logs_ref, history_ref=history_ref, candidate_only=True)
        return {"state": "FAILED" if record_status == "rejected" else "CANDIDATE", "model_calls": 1, "attempt_id": reservation["attempt_id"], "error_code": result.error_code, "attempt_ref": attempt_ref, "logs_ref": logs_ref, "history_ref": history_ref, "observed_model": "unknown"}
    except (OSError, context_state.ContextStateError, UnicodeError, ValueError, TypeError) as exc:
        # The reservation is consumed.  Make one bounded, append-only recovery
        # attempt; if storage is unavailable, report that fact without refs.
        recovery = {"attempt_id": reservation["attempt_id"], "cache_key": cache_key, "error_code": "persistence_failed", "cause": type(exc).__name__, "consumed_reservation": True, "retry": False, "candidate_only": True}
        recovery_ref = None
        try:
            recovery_ref = store.write_artifact(_encoded(recovery), "json")
        except (OSError, context_state.ContextStateError, UnicodeError, ValueError, TypeError):
            pass
        try:
            store.append_event("model_attempt_persistence_failed", attempt_id=reservation["attempt_id"], cache_key=cache_key, error_code="persistence_failed", recovery_ref=recovery_ref, consumed_reservation=True, retry=False, candidate_only=True)
        except (OSError, context_state.ContextStateError, UnicodeError, ValueError, TypeError):
            pass
        return {"state": "PERSISTENCE_FAILED", "model_calls": 1, "attempt_id": reservation["attempt_id"], "error_code": "persistence_failed", "attempt_ref": attempt_ref, "logs_ref": logs_ref, "history_ref": history_ref, "recovery_ref": recovery_ref, "consumed_reservation": True, "retry": False, "persistence_failed": True, "observed_model": "unknown"}


def invoke(vault: Path | str, executable: Path | str, approval_record: dict, *, workdir: Path | str | None = None) -> dict:
    """Run exactly one approved transport attempt after mandatory preflight."""
    preflight(vault, executable)
    _approval(approval_record)
    root = Path(vault).absolute(); store = context_state.ContextStore(root); request = request_identity(); cache_key = REQUEST_DIGEST
    schema_path = str(root / request["input_artifact_refs"][-1]["path"]); image_path = str(root / request["input_artifact_refs"][1]["path"])
    command = subscription.build_codex_command(str(Path(executable).absolute()), schema_path, [image_path], model=GPT55_MODEL)
    env = subscription.build_child_env(str(Path.home()), str(root / "state" / "context-pilot" / "scratch"))
    work = Path(workdir or root / "state" / "context-pilot" / "pilot-v1" / "work").absolute()
    work.mkdir(parents=True, exist_ok=True)
    with store.locked():
        reservation = store.reserve_model_transport_check(PARENT_CACHE_KEY, REQUEST_DIGEST, AUTHORIZATION)
        started = _now()
        try:
            result = subscription.run_codex(command, PROMPT, env, cwd=work, attempt_id=reservation["attempt_id"], expected_model=GPT55_MODEL)
            if result.status == "candidate":
                parsed = subscription.parse_jsonl(result.stdout, expected_model=GPT55_MODEL)
                subscription.validate_model_response(result.response, request["expected_unit_ids"], request["artifact_ids_by_unit"])
                for item in result.response["results"]:
                    for finding in item["findings"]:
                        if any(ref != "artifact-002" for ref in finding["evidence_refs"]):
                            raise subscription.SchemaError("finding citations")
                providers = {v for event in parsed.events for v in subscription._identity_values(event, "provider") + subscription._identity_values(event, "model_provider")}
                models = {v for event in parsed.events for v in subscription._identity_values(event, "model") + subscription._identity_values(event, "model_name") + subscription._identity_values(event, "model_id")}
                if providers and providers != {"openai"} or models and models != {GPT55_MODEL}:
                    raise subscription.ProtocolError("provider_mismatch")
                if not providers or not models:
                    result.error_code = "identity_unknown"
            else:
                result.status = "failed"
        except subscription.SchemaError:
            result = subscription.CodexResult("failed", error_code="schema_invalid", stdout=result.stdout if "result" in locals() else b"", stderr=result.stderr if "result" in locals() else b"", attempt_id=reservation["attempt_id"])
        except (subscription.ProtocolError, Exception) as exc:
            result = subscription.CodexResult("failed", error_code=str(exc) or "execution_exception", stdout=getattr(locals().get("result"), "stdout", b""), stderr=getattr(locals().get("result"), "stderr", b""), attempt_id=reservation["attempt_id"])
        collision = (store.state_root / "cache" / f"{cache_key}-candidate.json").exists()
        if collision:
            result.status, result.error_code = "failed", "cache_collision"
        return _persist_failure(store, reservation, request, result, started, cache_key, collision)


class Gpt55TransportCheck:
    REQUEST_DIGEST = REQUEST_DIGEST; AUTHORIZATION = AUTHORIZATION; CANDIDATE_ONLY = True; PROCESSES_IMAGE_CONTENT = False
    synthetic_store = staticmethod(lambda root: _synthetic_store(root))


def _synthetic_store(root: Path) -> context_state.ContextStore:
    root.mkdir(parents=True, exist_ok=True); store = context_state.ContextStore(root)
    with store.locked():
        attempts, cache = store._ensure_dirs()
        (store.state_root / "events").mkdir(exist_ok=True)
        for sequence, metadata in enumerate([{"cache_key": PARENT_CACHE_KEY, "call_number": 1}, {"cache_key": PARENT_CACHE_KEY, "call_number": 1}, {"authorization_kind": "continuation-v1", "authorization_scope": "one-image-supplemental-continuation", "parent_cache_key": PARENT_CACHE_KEY, "request_digest": context_state.CONTINUATION_REQUEST_DIGEST, "call_number": 1}], 1):
            record = {"schema_version": 1, "sequence": sequence, "kind": "model", "attempt_id": f"model-{sequence:06d}", "reserved_at": f"2026-10-13T00:00:0{sequence}Z", "metadata": metadata}
            (attempts / f"{sequence:06d}-model.json").write_bytes(_encoded(record))
    return store


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="candidate-only frozen gpt-5.5 transport check")
    sub = parser.add_subparsers(dest="phase", required=True)
    for phase in ("preflight", "invoke"):
        p = sub.add_parser(phase); p.add_argument("--vault", required=True); p.add_argument("--executable", required=True)
        if phase == "invoke": p.add_argument("--approval-record", required=True); p.add_argument("--workdir")
    args = parser.parse_args(argv)
    try:
        if args.phase == "preflight": result = preflight(args.vault, args.executable)
        else: result = invoke(args.vault, args.executable, json.loads(Path(args.approval_record).read_text(encoding="utf-8")), workdir=args.workdir)
    except Exception as exc:
        print(json.dumps({"state": "BLOCKED", "model_calls": 0, "error": str(exc)}, sort_keys=True)); return 2
    print(json.dumps(result, sort_keys=True)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
