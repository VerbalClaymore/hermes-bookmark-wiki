"""Append-only context state, evidence, budgets, and fail-closed cache primitives."""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import os
import re
from contextlib import contextmanager
from pathlib import Path

from . import archive


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_ALLOWED_ARTIFACT_EXTENSIONS = {"json", "txt", "html", "jpg", "png", "mp4"}
_DISCOVERY = {"not_checked", "media_found", "no_media_found_in_scope", "unable_to_determine"}
_ACQUISITION = {"not_needed", "pending", "captured", "unavailable", "failed"}
_ANALYSIS = {"not_applicable", "pending", "succeeded_findings", "succeeded_no_findings", "failed", "deferred"}
_CHANNELS = {"source_text", "native_media", "video", "linked_content", "thread"}
_MATRIX = {
    ("not_checked", "pending", "pending"), ("not_checked", "pending", "deferred"),
    ("unable_to_determine", "pending", "deferred"), ("unable_to_determine", "failed", "failed"),
    ("no_media_found_in_scope", "not_needed", "not_applicable"),
    ("media_found", "pending", "pending"), ("media_found", "pending", "deferred"),
    ("media_found", "captured", "pending"), ("media_found", "captured", "succeeded_findings"),
    ("media_found", "captured", "succeeded_no_findings"), ("media_found", "captured", "failed"),
    ("media_found", "captured", "deferred"), ("media_found", "unavailable", "deferred"),
    ("media_found", "failed", "failed"), ("not_checked", "unavailable", "deferred"),
    ("not_checked", "failed", "failed"),
}


class ContextStateError(Exception):
    """Base class for context-pilot contract failures."""


class InvalidInput(ContextStateError):
    pass


class InvalidState(ContextStateError):
    pass


class IntegrityError(ContextStateError):
    pass


class LockRequired(ContextStateError):
    pass


class Locked(ContextStateError):
    pass


class BudgetExceeded(ContextStateError):
    pass


class CacheMiss(ContextStateError):
    pass


# These are contract identities, not caller-provided authorization inputs.
CONTINUATION_PARENT_CACHE_KEY = "ccd1ccdb305d256c246551e41e0e8e40b0e20d68d3ff46d186c9ea604bf375ce"
CONTINUATION_REQUEST_DIGEST = "ce513d0a30a040a5535a4ff0a74ba66dcb540f121b55403c015d41c3fd602658"
GPT55_TRANSPORT_REQUEST_DIGEST = "64f0f7750b7697655463506c7a03216dd4ac784fd09286ba3c0726bd1fb4b40b"
GPT55_TRANSPORT_AUTHORIZATION = {
    "authorization_kind": "gpt55-transport-check-v1",
    "authorization_scope": "one-image-supplemental-transport-check",
    "parent_cache_key": CONTINUATION_PARENT_CACHE_KEY,
    "request_digest": GPT55_TRANSPORT_REQUEST_DIGEST,
    "requested_model": "gpt-5.5",
    "call_number": 1,
}
CONTINUATION_PARENT_RESERVATIONS = {
    "model-000015": ("attempts/000015-model.json", "55db26a3295f4386c049b06d2158df8796605f86746fdc1d0afdee6374fed806"),
    "model-000016": ("attempts/000016-model.json", "ae317dfc8ade3f2ad186cebe693d0a5e9c0c54424110119f4717a443d9b923fd"),
}
CONTINUATION_PARENT_EVENTS = {
    "events/000011.json": "a35f8312cd4f30ad2fb26d05a6ed3a13eda8cac4cb04c5f011fcb70cc844fb3d",
    "events/000030.json": "c250b524bc1999ba650c65ed83b1e01bfb71c7dd335d16c08b2be5ec660dda80",
}
CONTINUATION_PARENT_EVIDENCE = {
    "context-evidence/sha256/ca01ccebe7aeee1d5f10a2927775c47449cef327a2c3a5c3d96c753966794daa.json": "ca01ccebe7aeee1d5f10a2927775c47449cef327a2c3a5c3d96c753966794daa",
    "context-evidence/sha256/22ef24d825b34ae896171543f022a3432a4ae4792679165149438e15473a798c.json": "22ef24d825b34ae896171543f022a3432a4ae4792679165149438e15473a798c",
    "state/context-pilot/pilot-v1/cache/ccd1ccdb305d256c246551e41e0e8e40b0e20d68d3ff46d186c9ea604bf375ce-rejected.json": "b85410ffc07b19a7f55313ca363ee40e255837ea980ffdc39f4c93c7fba1ba39",
    "context-evidence/sha256/db7c9f25ed322708131122edf588463ff44ab540d2ac7a0827138ebde8102f97.json": "db7c9f25ed322708131122edf588463ff44ab540d2ac7a0827138ebde8102f97",
}


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise InvalidInput("value is not canonical JSON") from exc


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _aware_utc(value: object, name: str = "checked_at") -> None:
    if not isinstance(value, str):
        raise InvalidState(name + " must be a timestamp")
    try:
        parsed = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidState("invalid " + name) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != _dt.timedelta(0):
        raise InvalidState(name + " must be UTC")


def _require_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidState(name + " must be nonempty")
    return value


def _check_sha(value: object, name: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise InvalidState(name + " must be lowercase SHA256")
    return value


def _safe_relative(path: object) -> str:
    if not isinstance(path, str) or not path or "\\" in path or path.startswith("/"):
        raise InvalidInput("invalid artifact path")
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise InvalidInput("invalid artifact path")
    return path


def _check_no_symlink_path(path: Path, root: Path) -> None:
    """Reject symlinks in every existing lexical component beneath root."""
    root = root.absolute()
    path = path.absolute()
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise InvalidInput("path outside store") from exc
    current = root
    if current.is_symlink():
        raise InvalidInput("symlink store root")
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise InvalidInput("symlink path")


def _check_integrity_path(path: Path, root: Path) -> None:
    try:
        _check_no_symlink_path(path, root)
    except InvalidInput as exc:
        raise IntegrityError("symlink or invalid state path") from exc


def _make_safe_directory(path: Path, root: Path) -> None:
    _check_no_symlink_path(path, root)
    relative = path.absolute().relative_to(root.absolute())
    current = root.absolute()
    for part in relative.parts:
        current = current / part
        if current.exists() and not current.is_dir():
            raise InvalidInput("path component is not a directory")
        current.mkdir(exist_ok=True)
        _check_no_symlink_path(current, root)


def _fsync_directory(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError as exc:
        raise IntegrityError("directory fsync failed") from exc


def _exclusive_write(path: Path, payload: bytes) -> None:
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise IntegrityError("immutable file already exists") from exc
    except OSError as exc:
        raise IntegrityError("file write failed") from exc
    _fsync_directory(path.parent)


def _axis(value: object, name: str) -> None:
    if not isinstance(value, dict) or set(value) != {"state", "method", "scope", "checked_at", "evidence_refs", "reason"}:
        raise InvalidState(name + " axis shape")
    _require_text(value["state"], name + " state")
    _require_text(value["method"], name + " method")
    _require_text(value["scope"], name + " scope")
    _aware_utc(value["checked_at"])
    _require_text(value["reason"], name + " reason")
    refs = value["evidence_refs"]
    if not isinstance(refs, list) or not refs:
        raise InvalidState(name + " evidence_refs required")
    for ref in refs:
        if not isinstance(ref, dict) or set(ref) != {"path", "sha256"}:
            raise InvalidState(name + " evidence reference shape")
        _safe_relative(ref["path"])
        _check_sha(ref["sha256"], name + " evidence digest")


def validate_unit(unit: dict) -> dict:
    """Validate one Context State V1 unit and its exhaustive axis combination."""
    if not isinstance(unit, dict):
        raise InvalidState("unit must be an object")
    required = {"unit_id", "source_id", "source_revision", "channel", "discovery", "acquisition", "analysis"}
    if set(unit) - required - {"attempt_id"} or not required.issubset(unit):
        raise InvalidState("unit shape")
    _require_text(unit["unit_id"], "unit_id")
    if not isinstance(unit["source_id"], str) or not re.fullmatch(r"x:[0-9]+", unit["source_id"]):
        raise InvalidState("source_id")
    _check_sha(unit["source_revision"], "source_revision")
    if not isinstance(unit["channel"], str) or unit["channel"] not in _CHANNELS:
        raise InvalidState("channel")
    if "attempt_id" in unit:
        _require_text(unit["attempt_id"], "attempt_id")
    for name in ("discovery", "acquisition", "analysis"):
        _axis(unit[name], name)
    values = (unit["discovery"]["state"], unit["acquisition"]["state"], unit["analysis"]["state"])
    if values not in _MATRIX:
        raise InvalidState("invalid discovery/acquisition/analysis combination")
    if unit["discovery"]["state"] not in _DISCOVERY or unit["acquisition"]["state"] not in _ACQUISITION or unit["analysis"]["state"] not in _ANALYSIS:
        raise InvalidState("unknown axis state")
    return unit


def _edge(axis: str, old: str, new: str) -> bool:
    if old == new:
        return True
    if axis == "discovery":
        return (old in {"not_checked", "unable_to_determine"} and new in {"media_found", "no_media_found_in_scope", "unable_to_determine"})
    if axis == "acquisition":
        return (old == "pending" and new in {"captured", "unavailable", "failed"}) or (old in {"failed", "unavailable"} and new == "captured")
    if axis == "analysis":
        return (old == "pending" and new in {"succeeded_findings", "succeeded_no_findings", "failed", "deferred"}) or (old in {"failed", "deferred"} and new in {"succeeded_findings", "succeeded_no_findings"})
    return False


def validate_transition(previous: dict, current: dict) -> dict:
    """Validate one append-only event; immutable successes permit exact no-op only."""
    validate_unit(previous)
    validate_unit(current)
    for identity in ("unit_id", "source_id", "source_revision", "channel"):
        if previous[identity] != current[identity]:
            raise InvalidState("transition identity changed")
    if previous == current:
        return current
    if "attempt_id" not in current or ("attempt_id" in previous and current["attempt_id"] == previous["attempt_id"]):
        raise InvalidState("new transition requires a fresh attempt_id")
    for axis_name in ("discovery", "acquisition", "analysis"):
        old_axis = previous[axis_name]
        new_axis = current[axis_name]
        old = old_axis["state"]
        new = new_axis["state"]
        if old in {"succeeded_findings", "succeeded_no_findings"}:
            if old_axis != new_axis:
                raise InvalidState("successful analysis metadata is immutable")
        elif old_axis == new_axis:
            continue
        elif not _edge(axis_name, old, new):
            raise InvalidState("invalid " + axis_name + " transition")
        # An unchanged state is not an excuse to rewrite provenance metadata.
        if old == new and old_axis != new_axis:
            raise InvalidState("unchanged axis metadata is immutable")
    return current


def validate_analysis_record(record: dict) -> dict:
    """Validate trusted coordinator provenance attached to an analysis outcome."""
    required = {"method", "coverage", "requested_model", "observed_model", "auth_mode", "prompt_version", "schema_version", "input_artifact_hashes", "checked_at", "attempt_id", "usage", "wall_seconds", "outcome", "reason"}
    if not isinstance(record, dict) or set(record) != required:
        raise InvalidState("analysis record shape")
    for field in ("method", "coverage", "requested_model", "observed_model", "auth_mode", "prompt_version", "attempt_id", "reason"):
        _require_text(record[field], field)
    if type(record["schema_version"]) is not int or record["schema_version"] != 1 or not isinstance(record["outcome"], str) or record["outcome"] not in {"succeeded_findings", "succeeded_no_findings", "failed", "deferred"}:
        raise InvalidState("analysis record version or outcome")
    _aware_utc(record["checked_at"])
    hashes = record["input_artifact_hashes"]
    if not isinstance(hashes, list) or not hashes or not all(isinstance(item, str) and _SHA256.fullmatch(item) for item in hashes):
        raise InvalidState("analysis input hashes")
    if record["wall_seconds"] is not None and (isinstance(record["wall_seconds"], bool) or not isinstance(record["wall_seconds"], (int, float)) or not math.isfinite(record["wall_seconds"]) or record["wall_seconds"] < 0):
        raise InvalidState("analysis wall time")
    if record["usage"] is not None and not isinstance(record["usage"], dict):
        raise InvalidState("analysis usage")
    return record


def cache_key(identity: object) -> str:
    """Return the SHA256 cache key for canonical request identity."""
    return hashlib.sha256(_canonical(identity)).hexdigest()


def _ref_shape(ref: object, name: str = "reference") -> dict:
    if not isinstance(ref, dict) or set(ref) != {"path", "sha256"}:
        raise IntegrityError(name + " shape")
    _safe_relative(ref["path"])
    _check_sha(ref["sha256"], name + " digest")
    return ref


class ContextStore:
    """Synthetic-vault store using the archive module's state/update.lock inode."""

    LIMITS = {"acquisition": 12, "http": 24, "model": 2}

    def __init__(self, root: Path | str, *, run_id: str = "pilot-v1"):
        if run_id != "pilot-v1" or not _RUN_ID.fullmatch(run_id):
            raise InvalidInput("only pilot-v1 is allowed")
        self.root = Path(root).absolute()
        if not self.root.is_dir() or self.root.is_symlink():
            raise InvalidInput("invalid store root")
        self.run_id = run_id
        self._lock = None

    @property
    def state_root(self) -> Path:
        return self.root / "state" / "context-pilot" / self.run_id

    @contextmanager
    def locked(self):
        """Hold the shared nonblocking update lock for a complete mutation/attempt."""
        if self._lock is not None:
            yield self
            return
        try:
            with archive._update_lock(self.root):
                self._lock = True
                try:
                    yield self
                finally:
                    self._lock = None
        except archive.Locked as exc:
            raise Locked("shared update lock is held") from exc

    def _require_lock(self) -> None:
        if self._lock is None:
            raise LockRequired("operation requires shared update lock")

    def _ensure_dirs(self) -> tuple[Path, Path]:
        self._require_lock()
        _make_safe_directory(self.state_root, self.root)
        attempts = self.state_root / "attempts"
        cache = self.state_root / "cache"
        _make_safe_directory(attempts, self.root)
        _make_safe_directory(cache, self.root)
        return attempts, cache

    def _ledger(self, *, create: bool = True) -> list[tuple[int, str, Path, dict]]:
        if create:
            attempts, _ = self._ensure_dirs()
        else:
            attempts = self.state_root / "attempts"
            if not attempts.is_dir() or attempts.is_symlink():
                raise IntegrityError("existing attempt ledger is required")
            _check_integrity_path(attempts, self.root)
        entries = []
        for path in sorted(attempts.iterdir(), key=lambda item: item.name):
            _check_integrity_path(path, self.root)
            if not path.is_file():
                raise IntegrityError("invalid attempt ledger entry")
            match = re.fullmatch(r"([0-9]{6})-(acquisition|http|model)\.json", path.name)
            if not match:
                raise IntegrityError("invalid attempt ledger filename")
            sequence, kind = int(match.group(1)), match.group(2)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise IntegrityError("invalid attempt ledger record") from exc
            expected_keys = {"schema_version", "sequence", "kind", "attempt_id", "reserved_at", "metadata"}
            if not isinstance(data, dict) or set(data) != expected_keys:
                raise IntegrityError("invalid attempt ledger record")
            expected_attempt_id = f"{kind}-{sequence:06d}"
            supplemental_attempt_id = kind == "model" and data["metadata"].get("authorization_kind") in {"continuation-v1", "gpt55-transport-check-v1"} and data["attempt_id"] in {"model-000003", "model-000004"}
            if type(data["schema_version"]) is not int or data["schema_version"] != 1 or type(data["sequence"]) is not int or data["sequence"] != sequence or data["kind"] != kind or (data["attempt_id"] != expected_attempt_id and not supplemental_attempt_id):
                raise IntegrityError("invalid attempt ledger binding")
            try:
                _aware_utc(data["reserved_at"], "reserved_at")
            except InvalidState as exc:
                raise IntegrityError("invalid reservation timestamp") from exc
            if not isinstance(data["metadata"], dict):
                raise IntegrityError("invalid reservation metadata")
            entries.append((sequence, kind, path, data))
        if [item[0] for item in entries] != list(range(1, len(entries) + 1)):
            raise IntegrityError("attempt ledger sequence is not continuous")
        if len({item[3]["attempt_id"] for item in entries}) != len(entries):
            raise IntegrityError("duplicate attempt id")
        for kind, limit in self.LIMITS.items():
            count = sum(item[1] == kind for item in entries)
            if kind != "model" and count > limit:
                raise IntegrityError(kind + " budget exceeded")
            if kind == "model":
                self._validate_model_budget(entries, count)
        return entries

    @staticmethod
    def _validate_model_budget(entries: list[tuple[int, str, Path, dict]], count: int) -> None:
        continuations = [item for item in entries if item[1] == "model" and item[3]["metadata"].get("authorization_kind") == "continuation-v1"]
        if count > 4 or len(continuations) > 1:
            raise IntegrityError("model budget exceeded")
        if count == 4:
            checks = [item for item in entries if item[1] == "model" and item[3]["metadata"].get("authorization_kind") == "gpt55-transport-check-v1"]
            if len(checks) != 1 or len([item for item in entries if item[1] == "model" and item not in checks]) != 3 or checks[0][3]["metadata"] != GPT55_TRANSPORT_AUTHORIZATION:
                raise IntegrityError("gpt55 transport-check history is invalid")
        elif count == 3:
            if len(continuations) != 1:
                raise IntegrityError("third model reservation is not a continuation")
            continuation_metadata = continuations[0][3]["metadata"]
            required = {"authorization_kind", "authorization_scope", "parent_cache_key", "request_digest", "call_number"}
            if set(continuation_metadata) != required or continuation_metadata.get("authorization_scope") != "one-image-supplemental-continuation" or continuation_metadata.get("call_number") != 1:
                raise IntegrityError("continuation authorization metadata is invalid")
            _check_sha(continuation_metadata.get("parent_cache_key"), "continuation parent cache key")
            _check_sha(continuation_metadata.get("request_digest"), "continuation request digest")
            ordinary = [item for item in entries if item[1] == "model" and item not in continuations]
            if len(ordinary) != 2:
                raise IntegrityError("continuation parent history is invalid")
        elif continuations:
            raise IntegrityError("orphan continuation reservation")

    def reserve(self, kind: str, metadata: dict | None = None) -> dict:
        """Persist a reservation before acquisition, HTTP, or model work."""
        self._require_lock()
        if kind not in self.LIMITS:
            raise InvalidInput("invalid reservation kind")
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            raise InvalidInput("reservation metadata must be an object")
        entries = self._ledger()
        if sum(item[1] == kind for item in entries) >= self.LIMITS[kind]:
            raise BudgetExceeded(kind + " budget exhausted")
        sequence = len(entries) + 1
        record = {"schema_version": 1, "sequence": sequence, "kind": kind, "attempt_id": f"{kind}-{sequence:06d}", "reserved_at": _now(), "metadata": metadata}
        attempts, _ = self._ensure_dirs()
        _exclusive_write(attempts / f"{sequence:06d}-{kind}.json", _canonical(record) + b"\n")
        return record

    def _validate_continuation_baseline(self) -> list[tuple[int, str, Path, dict]]:
        """Verify the reviewed live baseline byte-for-byte before any append."""
        entries = self._ledger()
        models = [item for item in entries if item[1] == "model"]
        if len(models) != 2 or {item[3]["attempt_id"] for item in models} != set(CONTINUATION_PARENT_RESERVATIONS):
            raise IntegrityError("continuation requires the exact two frozen model reservations")
        for attempt_id, (relative, digest) in CONTINUATION_PARENT_RESERVATIONS.items():
            base = self.state_root if relative.startswith(("attempts/", "events/")) else self.root
            path = base / Path(*relative.split("/"))
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise IntegrityError("frozen reservation bytes changed")
            entry = next(item[3] for item in models if item[3]["attempt_id"] == attempt_id)
            if entry["metadata"] != {"cache_key": CONTINUATION_PARENT_CACHE_KEY, "call_number": 1}:
                raise IntegrityError("frozen reservation metadata changed")
        for relative, digest in {**CONTINUATION_PARENT_EVENTS, **CONTINUATION_PARENT_EVIDENCE}.items():
            base = self.state_root if relative.startswith(("attempts/", "events/")) else self.root
            path = base / Path(*relative.split("/"))
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise IntegrityError("frozen continuation evidence changed")
        events, paths = self._event_paths()
        allowed = {"000011.json", "000030.json"}
        if not allowed.issubset({path.name for path in paths}):
            raise IntegrityError("required baseline events are missing")
        # No continuation marker/history may already exist anywhere in the state.
        for path in paths:
            payload = path.read_bytes()
            if b"continuation" in payload or b"model-000003" in payload:
                raise IntegrityError("continuation history already exists")
        _, cache = self._ensure_dirs()
        for path in cache.iterdir():
            if path.name.endswith("-accepted.json") or CONTINUATION_REQUEST_DIGEST in path.name or path.name.startswith("model-000003"):
                raise IntegrityError("accepted cache blocks continuation")
        evidence_dir = self.root / "context-evidence" / "sha256"
        if evidence_dir.exists():
            for path in evidence_dir.iterdir():
                if path.is_file() and path.suffix == ".json":
                    payload = path.read_bytes()
                    if b"continuation_attempt" in payload or b"model-000003" in payload or CONTINUATION_REQUEST_DIGEST.encode() in payload:
                        raise IntegrityError("continuation evidence history already exists")
        return entries

    def reserve_model_continuation(self, parent_cache_key: str | None = None, request_digest: str | None = None, authorization: dict | None = None) -> dict:
        """Reserve exactly one contract-bound supplemental model attempt."""
        self._require_lock()
        if parent_cache_key != CONTINUATION_PARENT_CACHE_KEY or request_digest != CONTINUATION_REQUEST_DIGEST:
            raise IntegrityError("continuation identity is not the frozen contract identity")
        expected_auth = {"authorization_kind": "continuation-v1", "authorization_scope": "one-image-supplemental-continuation", "parent_cache_key": CONTINUATION_PARENT_CACHE_KEY, "request_digest": CONTINUATION_REQUEST_DIGEST}
        if authorization != expected_auth:
            raise IntegrityError("continuation authorization is not contract-bound")
        entries = self._ledger()
        models = [item for item in entries if item[1] == "model"]
        if len(models) >= 3 or any(item[3]["metadata"].get("authorization_kind") == "continuation-v1" for item in models):
            raise BudgetExceeded("continuation budget exhausted")
        if len(models) != 2:
            raise BudgetExceeded("continuation requires exactly two original model reservations")
        entries = self._validate_continuation_baseline()
        _, cache = self._ensure_dirs()
        if (cache / f"{CONTINUATION_PARENT_CACHE_KEY}-accepted.json").exists():
            raise IntegrityError("continuation parent has accepted cache")
        metadata = dict(expected_auth, call_number=1)
        sequence = len(entries) + 1
        record = {"schema_version": 1, "sequence": sequence, "kind": "model", "attempt_id": "model-000003", "reserved_at": _now(), "metadata": metadata}
        attempts, _ = self._ensure_dirs()
        _exclusive_write(attempts / f"{sequence:06d}-model.json", _canonical(record) + b"\n")
        return record

    def reserve_model_transport_check(self, parent_cache_key: str, request_digest: str, authorization: dict) -> dict:
        """Append exactly one reviewed gpt-5.5 transport-check reservation."""
        self._require_lock()
        if parent_cache_key != CONTINUATION_PARENT_CACHE_KEY or request_digest != GPT55_TRANSPORT_REQUEST_DIGEST or authorization != GPT55_TRANSPORT_AUTHORIZATION:
            raise IntegrityError("gpt55 transport-check authorization is not contract-bound")
        entries = self._ledger()
        models = [item for item in entries if item[1] == "model"]
        if len(models) != 3:
            raise BudgetExceeded("gpt55 transport-check requires the preserved three-attempt history")
        sequence = len(entries) + 1
        record = {"schema_version": 1, "sequence": sequence, "kind": "model", "attempt_id": "model-000004", "reserved_at": _now(), "metadata": dict(authorization)}
        attempts, _ = self._ensure_dirs()
        _exclusive_write(attempts / f"{sequence:06d}-model.json", _canonical(record) + b"\n")
        return record

    def read_ref(self, ref: dict, *, parse_json: bool = False):
        """Read and hash-verify one vault-relative immutable reference."""
        self._require_lock()
        ref = _ref_shape(ref)
        path = self.root / Path(*ref["path"].split("/"))
        _check_integrity_path(path, self.root)
        if not path.is_file() or path.is_symlink():
            raise IntegrityError("missing referenced evidence")
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise IntegrityError("referenced evidence unreadable") from exc
        if hashlib.sha256(payload).hexdigest() != ref["sha256"]:
            raise IntegrityError("referenced evidence digest mismatch")
        if parse_json:
            try:
                return json.loads(payload.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise IntegrityError("referenced JSON is invalid") from exc
        return payload

    verify_ref = read_ref

    def write_artifact(self, data: bytes, extension: str) -> dict:
        """Write or verify an immutable vault-relative evidence artifact."""
        self._require_lock()
        if not isinstance(data, bytes) or extension not in _ALLOWED_ARTIFACT_EXTENSIONS:
            raise InvalidInput("invalid artifact")
        digest = hashlib.sha256(data).hexdigest()
        directory = self.root / "context-evidence" / "sha256"
        _make_safe_directory(directory, self.root)
        path = directory / f"{digest}.{extension}"
        _check_no_symlink_path(path, self.root)
        if path.exists() or path.is_symlink():
            if path.is_symlink() or not path.is_file() or path.read_bytes() != data:
                raise IntegrityError("artifact digest collision")
        else:
            _exclusive_write(path, data)
        return {"path": path.relative_to(self.root).as_posix(), "sha256": digest}

    def _event_paths(self):
        events = self.state_root / "events"
        if not events.exists():
            return events, []
        _check_no_symlink_path(events, self.root)
        if not events.is_dir():
            raise IntegrityError("invalid event directory")
        paths = sorted(events.iterdir(), key=lambda item: item.name)
        for path in paths:
            _check_integrity_path(path, self.root)
            if not path.is_file() or not re.fullmatch(r"[0-9]{6}\.json", path.name):
                raise IntegrityError("invalid event entry")
        if [int(path.stem) for path in paths] != list(range(1, len(paths) + 1)):
            raise IntegrityError("event sequence is not continuous")
        return events, paths

    def append_event(self, event_type: str, **fields) -> dict:
        """Append one immutable event under the shared lock."""
        self._require_lock()
        _require_text(event_type, "event_type")
        if set(fields) & {"schema_version", "sequence", "event_type", "checked_at"}:
            raise InvalidInput("event metadata is reserved")
        events, paths = self._event_paths()
        _make_safe_directory(events, self.root)
        sequence = len(paths) + 1
        record = {"schema_version": 1, "sequence": sequence, "event_type": event_type, "checked_at": _now()}
        record.update(fields)
        _canonical(record)  # reject non-JSON values before publication
        _exclusive_write(events / f"{sequence:06d}.json", _canonical(record) + b"\n")
        return record

    def acquisition_stopped(self) -> bool:
        self._require_lock()
        _, paths = self._event_paths()
        for expected, path in enumerate(paths, 1):
            try:
                event = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise IntegrityError("corrupt event") from exc
            if not isinstance(event, dict) or type(event.get("schema_version")) is not int or event.get("schema_version") != 1 or type(event.get("sequence")) is not int or event.get("sequence") != expected or not isinstance(event.get("event_type"), str):
                raise IntegrityError("invalid event record")
            if event["event_type"] == "acquisition_stopped":
                if set(event) != {"schema_version", "sequence", "event_type", "checked_at", "reason"}:
                    raise IntegrityError("invalid stop event")
                try:
                    _aware_utc(event["checked_at"])
                except InvalidState as exc:
                    raise IntegrityError("invalid stop timestamp") from exc
                _require_text(event["reason"], "reason")
                return True
        return False

    def stop_acquisition(self, reason: str) -> None:
        self._require_lock()
        if not self.acquisition_stopped():
            self.append_event("acquisition_stopped", reason=_require_text(reason, "reason"))

    def write_plan(self, plan: dict, name: str = "plan.json") -> Path:
        """Write an exclusive state plan; callers must make it immutable."""
        self._require_lock()
        if not isinstance(plan, dict) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}\.json", name):
            raise InvalidInput("invalid plan")
        _make_safe_directory(self.state_root, self.root)
        path = self.state_root / name
        _check_no_symlink_path(path, self.root)
        _exclusive_write(path, _canonical(plan) + b"\n")
        return path

    def _validate_call_plan(self, plan: object) -> tuple[list[str], dict[str, list[str]]]:
        required = {"call_number", "units", "artifacts", "prompt_sha256", "output_schema_sha256", "requested_model", "prompt_version", "schema_version"}
        if not isinstance(plan, dict) or set(plan) != required or type(plan["schema_version"]) is not int or plan["schema_version"] != 1 or type(plan["call_number"]) is not int or plan["call_number"] not in {1, 2}:
            raise IntegrityError("invalid call plan")
        for field in ("prompt_sha256", "output_schema_sha256"):
            _check_sha(plan[field], "call plan " + field)
        _require_text(plan["requested_model"], "requested_model")
        _require_text(plan["prompt_version"], "prompt_version")
        units, artifacts = plan["units"], plan["artifacts"]
        if not isinstance(units, list) or not isinstance(artifacts, list) or not units:
            raise IntegrityError("invalid call plan lists")
        artifact_ids = []
        artifact_by_id = {}
        for index, item in enumerate(artifacts, 1):
            if not isinstance(item, dict) or set(item) != {"artifact_id", "path", "sha256", "media_type"} or item["artifact_id"] != f"artifact-{index:03d}":
                raise IntegrityError("invalid call plan artifact")
            _require_text(item["media_type"], "media_type")
            _ref_shape({"path": item["path"], "sha256": item["sha256"]}, "call plan artifact")
            artifact_ids.append(item["artifact_id"])
            artifact_by_id[item["artifact_id"]] = item
        expected_ids, by_unit = [], {}
        for item in units:
            if not isinstance(item, dict) or set(item) != {"unit_id", "source_id", "source_revision", "method", "limitations", "artifact_ids"}:
                raise IntegrityError("invalid call plan unit")
            _require_text(item["unit_id"], "call plan unit_id")
            if item["unit_id"] in by_unit or not isinstance(item["source_id"], str) or not re.fullmatch(r"x:[0-9]+", item["source_id"]):
                raise IntegrityError("invalid call plan unit identity")
            _check_sha(item["source_revision"], "call plan source revision")
            _require_text(item["method"], "call plan method")
            if not isinstance(item["limitations"], list) or not all(isinstance(value, str) and value for value in item["limitations"]):
                raise IntegrityError("invalid call plan limitations")
            if not isinstance(item["artifact_ids"], list) or not item["artifact_ids"] or len(set(item["artifact_ids"])) != len(item["artifact_ids"]):
                raise IntegrityError("invalid call plan artifact mapping")
            if not all(isinstance(value, str) and value in artifact_by_id for value in item["artifact_ids"]):
                raise IntegrityError("unknown call plan artifact")
            expected_ids.append(item["unit_id"])
            by_unit[item["unit_id"]] = list(item["artifact_ids"])
        return expected_ids, by_unit

    def _validate_attempt(self, attempt: object, cache: str, call_plan_ref: dict, response_ref: dict, requested_model: str) -> None:
        required = {"schema_version", "attempt_id", "call_plan_ref", "cache_key", "started_at", "finished_at", "outcome", "error_code", "model_response_ref", "requested_model", "observed_model", "auth_mode", "provider", "usage", "wall_seconds", "event_log_ref"}
        if not isinstance(attempt, dict) or set(attempt) != required or type(attempt["schema_version"]) is not int or attempt["schema_version"] != 1:
            raise IntegrityError("invalid attempt envelope")
        _require_text(attempt["attempt_id"], "attempt_id")
        _check_sha(attempt["cache_key"], "attempt cache key")
        if attempt["cache_key"] != cache or attempt["call_plan_ref"] != call_plan_ref or attempt["model_response_ref"] != response_ref:
            raise IntegrityError("attempt binding mismatch")
        if attempt["requested_model"] != requested_model:
            raise IntegrityError("attempt model mismatch")
        ledger = self._ledger()
        matching = [item for item in ledger if item[1] == "model" and item[3]["attempt_id"] == attempt["attempt_id"]]
        if not matching:
            raise IntegrityError("attempt is not reserved")
        reserved_cache_key = matching[0][3]["metadata"].get("cache_key")
        if reserved_cache_key is not None and reserved_cache_key != cache:
            raise IntegrityError("reserved attempt cache mismatch")
        _aware_utc(attempt["started_at"], "started_at")
        _aware_utc(attempt["finished_at"], "finished_at")
        if attempt["outcome"] != "candidate" or attempt["error_code"] is not None:
            raise IntegrityError("accepted cache requires candidate attempt")
        for field in ("requested_model", "observed_model", "auth_mode", "provider"):
            _require_text(attempt[field], field)
        if attempt["auth_mode"] != "chatgpt" or attempt["provider"] != "openai-chatgpt-codex":
            raise IntegrityError("attempt provider binding mismatch")
        if attempt["usage"] is not None and not isinstance(attempt["usage"], dict):
            raise IntegrityError("invalid attempt usage")
        if isinstance(attempt["wall_seconds"], bool) or not isinstance(attempt["wall_seconds"], (int, float)) or not math.isfinite(attempt["wall_seconds"]) or attempt["wall_seconds"] < 0:
            raise IntegrityError("invalid attempt wall time")
        if attempt["event_log_ref"] is not None:
            _ref_shape(attempt["event_log_ref"], "event log reference")
            self.read_ref(attempt["event_log_ref"])

    def _validate_acceptance(self, acceptance: object, cache: str, attempt_id: str, response_ref: dict) -> None:
        required = {"schema_version", "cache_key", "attempt_id", "model_response_ref", "decision", "checked_at", "reviewer_method", "reason", "checked_artifact_refs"}
        if not isinstance(acceptance, dict) or set(acceptance) != required or type(acceptance["schema_version"]) is not int or acceptance["schema_version"] != 1:
            raise IntegrityError("invalid acceptance record")
        if acceptance["cache_key"] != cache or acceptance["attempt_id"] != attempt_id or acceptance["model_response_ref"] != response_ref or acceptance["decision"] != "accepted":
            raise IntegrityError("acceptance binding mismatch")
        _aware_utc(acceptance["checked_at"])
        _require_text(acceptance["reviewer_method"], "reviewer_method")
        _require_text(acceptance["reason"], "acceptance reason")
        refs = acceptance["checked_artifact_refs"]
        if not isinstance(refs, list) or not refs:
            raise IntegrityError("acceptance artifacts required")
        for ref in refs:
            self.read_ref(_ref_shape(ref, "acceptance artifact"))

    def _validate_request_binding(self, request: dict, plan: dict) -> None:
        required = {"source_revisions", "ordered_input_hashes", "stage", "provider", "requested_model", "prompt_version", "schema_version", "prompt_sha256", "output_schema_sha256", "expected_unit_ids", "artifact_ids_by_unit", "input_artifact_refs"}
        if not isinstance(request, dict) or set(request) != required:
            raise IntegrityError("request identity shape")
        expected_ids, by_unit = self._validate_call_plan(plan)
        expected = {
            "source_revisions": [u["source_revision"] for u in plan["units"]],
            "ordered_input_hashes": [a["sha256"] for a in plan["artifacts"]],
            "stage": "context-pilot", "provider": "openai-chatgpt-codex", "requested_model": "gpt-6-sol",
            "prompt_version": "context-pilot-1", "schema_version": 1,
            "prompt_sha256": plan["prompt_sha256"], "output_schema_sha256": plan["output_schema_sha256"],
            "expected_unit_ids": expected_ids, "artifact_ids_by_unit": by_unit,
        }
        if type(request["schema_version"]) is not int or any(request[k] != v for k,v in expected.items()):
            raise IntegrityError("request identity does not match call plan")
        if plan["requested_model"] != request["requested_model"] or plan["prompt_version"] != request["prompt_version"]:
            raise IntegrityError("request model or prompt binding mismatch")
        refs = request["input_artifact_refs"]
        originals = [{"path":a["path"], "sha256":a["sha256"]} for a in plan["artifacts"]]
        if not isinstance(refs,list) or len(refs) != len(originals)+2 or refs[:-2] != originals:
            raise IntegrityError("request input evidence binding mismatch")
        for ref in refs:
            _ref_shape(ref, "request input")
        if refs[-2].get("sha256") != plan["prompt_sha256"] or refs[-1].get("sha256") != plan["output_schema_sha256"]:
            raise IntegrityError("request prompt or schema digest mismatch")
        for ref in refs:
            self.read_ref(_ref_shape(ref, "request input"))

    def _validate_accepted_cache(self, key: str, record: dict, request_identity: object | None = None) -> dict:
        required = {"schema_version", "status", "cache_key", "request", "call_plan_ref", "attempt_ref", "model_response_ref", "acceptance"}
        if not isinstance(record, dict) or set(record) != required or type(record["schema_version"]) is not int or record["schema_version"] != 1 or record["status"] != "accepted" or record["cache_key"] != key:
            raise IntegrityError("accepted cache record shape")
        request = record["request"]
        if not isinstance(request, dict) or cache_key(request) != key:
            raise IntegrityError("cache request identity mismatch")
        if request_identity is not None and request != request_identity:
            raise IntegrityError("cache request mismatch")
        call_plan_ref = _ref_shape(record["call_plan_ref"], "call plan reference")
        attempt_ref = _ref_shape(record["attempt_ref"], "attempt reference")
        response_ref = _ref_shape(record["model_response_ref"], "model response reference")
        plan = self.read_ref(call_plan_ref, parse_json=True)
        expected_ids, artifact_by_unit = self._validate_call_plan(plan)
        self._validate_request_binding(request, plan)
        for item in plan["artifacts"]:
            self.read_ref({"path": item["path"], "sha256": item["sha256"]}, parse_json=False)
        if isinstance(request.get("expected_unit_ids"), list) and request["expected_unit_ids"] != expected_ids:
            raise IntegrityError("request unit mapping mismatch")
        if isinstance(request.get("artifact_ids_by_unit"), dict) and request["artifact_ids_by_unit"] != artifact_by_unit:
            raise IntegrityError("request artifact mapping mismatch")
        input_refs = request.get("input_artifact_refs", [])
        if not isinstance(input_refs, list):
            raise IntegrityError("invalid request input refs")
        for ref in input_refs:
            self.read_ref(_ref_shape(ref, "input artifact"))
        response = self.read_ref(response_ref, parse_json=True)
        from .subscription import validate_model_response  # local import avoids a module cycle
        if not isinstance(response, dict) or type(response.get("schema_version")) is not int or response.get("schema_version") != 1:
            raise IntegrityError("invalid cached model response")
        try:
            validate_model_response(response, expected_ids, artifact_by_unit)
        except Exception as exc:
            raise IntegrityError("invalid cached model response") from exc
        attempt = self.read_ref(attempt_ref, parse_json=True)
        self._validate_attempt(attempt, key, call_plan_ref, response_ref, plan["requested_model"])
        acceptance = record["acceptance"]
        self._validate_acceptance(acceptance, key, attempt["attempt_id"], response_ref)
        return record

    def write_cache(self, key: str, record: dict) -> Path:
        """Write an immutable candidate/accepted/rejected cache record."""
        self._require_lock()
        _check_sha(key, "cache key")
        if not isinstance(record, dict) or record.get("cache_key") != key or record.get("status") not in {"candidate", "accepted", "rejected"}:
            raise InvalidState("cache record")
        if record["status"] == "accepted":
            self._validate_accepted_cache(key, record)
        _, cache = self._ensure_dirs()
        path = cache / f"{key}-{record['status']}.json"
        _check_integrity_path(path, self.root)
        payload = _canonical(record) + b"\n"
        if path.exists() or path.is_symlink():
            if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
                raise IntegrityError("cache overwrite")
            return path
        _exclusive_write(path, payload)
        return path

    def lookup_cache(self, key: str, request_identity: object) -> dict | None:
        """Return only an intact coordinator-accepted cache record; corrupt cache fails closed."""
        self._require_lock()
        _check_sha(key, "cache key")
        _, cache = self._ensure_dirs()
        records = {}
        for status in ("candidate", "accepted", "rejected"):
            path = cache / f"{key}-{status}.json"
            _check_integrity_path(path, self.root)
            if not path.exists():
                continue
            if path.is_symlink() or not path.is_file():
                raise IntegrityError("invalid cache entry")
            try:
                records[status] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise IntegrityError("corrupt cache") from exc
            if not isinstance(records[status], dict) or records[status].get("cache_key") != key or records[status].get("status") != status:
                raise IntegrityError("cache record binding")
        accepted = records.get("accepted")
        if accepted is None:
            return None
        if set(accepted) == {"schema_version", "status", "cache_key", "request", "call_plan_ref", "attempt_ref", "model_response_ref", "acceptance"}:
            return self._validate_accepted_cache(key, accepted, request_identity)
        raise IntegrityError("accepted cache record shape")
