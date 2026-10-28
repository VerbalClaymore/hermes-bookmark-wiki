"""Sanitized Codex command boundary, bounded runner, and ModelResponseV1 validation."""

from __future__ import annotations

import codecs
import json
import math
import os
import selectors
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


class SubscriptionError(Exception):
    pass


class SchemaError(SubscriptionError):
    pass


class ProtocolError(SubscriptionError):
    pass


@dataclass
class ParsedJSONL:
    response: dict
    usage: dict | None
    events: list[dict]


@dataclass
class CodexResult:
    status: str
    response: dict | None = None
    usage: dict | None = None
    error_code: str | None = None
    stdout: bytes = b""
    stderr: bytes = b""
    wall_seconds: float = 0.0
    attempt_id: str | None = None


# This is the only schema sent to Codex.  The adapter adds trusted attempt metadata
# separately; none of that metadata is part of the model's response contract.
MODEL_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "results"],
    "properties": {
        "schema_version": {"type": "integer", "enum": [1]},
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["unit_id", "outcome", "findings", "reason", "limitations"],
                "properties": {
                    "unit_id": {"type": "string", "minLength": 1},
                    "outcome": {
                        "type": "string",
                        "enum": ["succeeded_findings", "succeeded_no_findings"],
                    },
                    "findings": {
                        "type": "array",
                        "maxItems": 8,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["claim", "evidence_refs"],
                            "properties": {
                                "claim": {"type": "string", "minLength": 1, "maxLength": 1200},
                                "evidence_refs": {
                                    "type": "array",
                                    "minItems": 1,
                                    "items": {"type": "string", "minLength": 1},
                                },
                            },
                        },
                    },
                    "reason": {"type": "string", "minLength": 1},
                    "limitations": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                },
            },
        },
    },
}


DISABLED_FEATURES = (
    "shell_tool",
    "code_mode_host",
    "code_mode",
    "code_mode_only",
    "unified_exec",
    "multi_agent",
    "multi_agent_v2",
    "browser_use",
    "browser_use_external",
    "browser_use_full_cdp_access",
    "in_app_browser",
    "in_app_chat",
    "in_app_dictation",
    "in_app_local_automation",
    "computer_use",
    "apps",
    "plugins",
    "hooks",
    "personality",
    "image_generation",
    "view_image",
    "enable_mcp_apps",
    "request_permissions_tool",
    "request_rule",
    "skill_search",
    "workspace_dependencies",
    "tool_call_mcp_elicitation",
    "remote_plugin",
    "plugin_sharing",
    "recommended_plugins",
    "standalone_web_search",
    "unbounded_connection_retries",
    "shell_snapshot",
    "skill_mcp_dependency_install",
    "tool_suggest",
)
# Kept as a private alias for callers that used the original implementation.
_DISABLED_FEATURES = DISABLED_FEATURES

_FIXED_PATH = "/opt/homebrew/bin:/usr/bin:/bin"
_MAX_IMAGES = 8
_MAX_IMAGE_BYTES = 32 * 1024 * 1024
_MAX_PROMPT_CHARS = 60_000
_MAX_OUTPUT_BYTES = 1_000_000
_MAX_TIMEOUT = 180.0


_BASE_CONFIG = (
    ("model_provider", '"openai"'),
    ("forced_login_method", '"chatgpt"'),
    ("service_tier", '"default"'),
    ("approval_policy", '"on-request"'),
    ("model_reasoning_effort", '"low"'),
    ("web_search", '"disabled"'),
    ("project_doc_max_bytes", "0"),
    ("mcp_servers", "{}"),
)


def _nonblank(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SchemaError(name + " must be a nonempty string")
    return value


def _regular_file(value: object, name: str) -> Path:
    if not isinstance(value, str) or not value.startswith("/") or any(ord(char) < 32 for char in value):
        raise ValueError(name + " must be an absolute path")
    path = Path(value)
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(name + " must exist") from exc
    if not resolved.is_file():
        raise ValueError(name + " must be a regular file")
    return path


def build_child_env(home: str, tmpdir: str, path: str = _FIXED_PATH) -> dict[str, str]:
    """Build the exact non-inherited child environment allowlist."""
    if not isinstance(home, str) or not home.startswith("/") or any(ord(char) < 32 for char in home) or not home:
        raise ValueError("home must be an absolute path")
    if not isinstance(tmpdir, str) or not tmpdir.startswith("/") or any(ord(char) < 32 for char in tmpdir) or not tmpdir:
        raise ValueError("tmpdir must be an absolute path")
    if path != _FIXED_PATH:
        raise ValueError("PATH is fixed by the transport contract")
    return {
        "HOME": home,
        "CODEX_HOME": home.rstrip("/") + "/.codex",
        "PATH": _FIXED_PATH,
        "TMPDIR": tmpdir,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }


def _config_tokens() -> list[str]:
    tokens: list[str] = []
    for key, value in _BASE_CONFIG:
        tokens.extend(("-c", f"{key}={value}"))
    for feature in DISABLED_FEATURES:
        tokens.extend(("-c", f"features.{feature}=false"))
    return tokens


def _compose_command(executable: str, schema_path: str, image_paths: list[str], model: str = "gpt-6-sol") -> list[str]:
    command = [
        executable,
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        "--strict-config",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--model",
        model,
        "--json",
        "--output-schema",
        schema_path,
    ]
    for image in image_paths:
        command.extend(("--image", image))
    command.extend(_config_tokens())
    return command


def _validate_image_paths(image_paths: list[str] | tuple[str, ...]) -> list[str]:
    if not isinstance(image_paths, (list, tuple)) or len(image_paths) > _MAX_IMAGES:
        raise ValueError("image count exceeds limit")
    normalized: list[str] = []
    total = 0
    for image in image_paths:
        path = _regular_file(image, "image path")
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise ValueError("cannot inspect image") from exc
        if size < 0:
            raise ValueError("invalid image size")
        total += size
        if total > _MAX_IMAGE_BYTES:
            raise ValueError("image bytes exceed limit")
        normalized.append(str(path))
    return normalized


def build_codex_command(
    executable: str,
    schema_path: str,
    image_paths: list[str] | tuple[str, ...] = (),
    *,
    model: str = "gpt-6-sol",
) -> list[str]:
    """Build the fixed, strict-config Codex command."""
    if model not in {"gpt-6-sol", "gpt-5.5"}:
        raise ValueError("model is not in the reviewed allowlist")
    if not isinstance(executable, str) or not executable.startswith("/") or any(ord(char) < 32 for char in executable):
        raise ValueError("absolute executable required")
    if not isinstance(schema_path, str) or not schema_path.startswith("/") or any(ord(char) < 32 for char in schema_path):
        raise ValueError("absolute schema path required")
    images = _validate_image_paths(image_paths)
    return _compose_command(executable, schema_path, images, model)


def _validate_command(command: list[str]) -> tuple[str, list[str]]:
    if not isinstance(command, list) or not command or not all(isinstance(item, str) for item in command):
        raise ValueError("command must be a list of strings")
    if any(any(ord(char) < 32 for char in item) for item in command):
        raise ValueError("command contains control character")
    executable = command[0]
    _regular_file(executable, "executable")
    prefix = command[:10]
    if command[:10] != [
        executable, "exec", "--ignore-user-config", "--ignore-rules", "--strict-config",
        "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only", "--model",
    ] or len(command) <= 11 or command[10] not in {"gpt-6-sol", "gpt-5.5"}:
        raise ValueError("command is not the fixed Codex command")
    model = command[10]
    if command[11] != "--json" or command[12] != "--output-schema" or len(command) <= 13:
        raise ValueError("command is not the fixed Codex command")
    schema_path = command[13]
    _regular_file(schema_path, "schema path")
    cursor = 14
    images: list[str] = []
    while cursor < len(command) and command[cursor] == "--image":
        if cursor + 1 >= len(command):
            raise ValueError("missing image path")
        images.append(command[cursor + 1])
        cursor += 2
    if command[cursor:] != _config_tokens():
        raise ValueError("command config is not the fixed safety contract")
    validated_images = _validate_image_paths(images)
    if command != _compose_command(executable, schema_path, validated_images, model):
        raise ValueError("command normalization mismatch")
    return schema_path, validated_images, model


def _validate_env(env: dict[str, str]) -> None:
    if not isinstance(env, dict) or set(env) != {"HOME", "CODEX_HOME", "PATH", "TMPDIR", "LANG", "LC_ALL"}:
        raise ValueError("child environment is not an exact allowlist")
    for key, value in env.items():
        if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
            raise ValueError("child environment value is invalid")
    if not env["HOME"].startswith("/") or env["CODEX_HOME"] != env["HOME"].rstrip("/") + "/.codex":
        raise ValueError("HOME/CODEX_HOME mismatch")
    if not env["TMPDIR"].startswith("/") or env["PATH"] != _FIXED_PATH or env["LANG"] != "C.UTF-8" or env["LC_ALL"] != "C.UTF-8":
        raise ValueError("child environment value is unsafe")


def _identity_values(event: dict[str, Any], key: str) -> list[Any]:
    values = []
    if key in event:
        values.append(event[key])
    item = event.get("item")
    if isinstance(item, dict) and key in item:
        values.append(item[key])
    return values


def _check_identity(event: dict[str, Any], expected_provider: str, expected_model: str) -> None:
    for value in _identity_values(event, "provider") + _identity_values(event, "model_provider"):
        if value != expected_provider:
            raise ProtocolError("provider_mismatch")
    for value in _identity_values(event, "model") + _identity_values(event, "model_name") + _identity_values(event, "model_id"):
        if value != expected_model:
            raise ProtocolError("provider_mismatch")


def _event_error_code(event: dict[str, Any]) -> str:
    raw = json.dumps(event, ensure_ascii=False, sort_keys=True).lower()
    if any(token in raw for token in ("rate_limit", "rate limit", "too many requests", "429")):
        return "rate_limit"
    if any(token in raw for token in ("quota", "insufficient_quota", "billing", "exhausted")):
        return "quota"
    if any(token in raw for token in ("authentication", "unauthorized", "forbidden", "invalid api key", "login required", "401", "403")):
        return "auth"
    if event.get("type") == "turn.failed":
        return "process_error"
    return "process_error"


def _recognized_startup_diagnostic(event: dict[str, Any]) -> bool:
    """Accept only the observed benign Codex deprecation advisory."""
    if event.get("type") != "item.completed":
        return False
    item = event.get("item")
    if not isinstance(item, dict) or item.get("type") != "error":
        return False
    message = item.get("message", item.get("error", ""))
    if not isinstance(message, str):
        return False
    return message == (
        "`[features].web_search_request` is deprecated because web search is enabled by default. "
        "(Set `web_search` to `\"live\"`, `\"indexed\"`, `\"cached\"`, or "
        "`\"disabled\"` at the top level (or under a profile) in config.toml if you want to override it.)"
    )


def _event_is_tool(event: dict[str, Any]) -> bool:
    event_type = event.get("type")
    if not isinstance(event_type, str):
        return True
    lowered = event_type.lower()
    if any(token in lowered for token in ("tool", "command", "mcp", "browser", "shell", "computer", "plugin", "web_search", "image_generation")):
        return True
    if lowered.startswith("item."):
        item = event.get("item")
        if not isinstance(item, dict):
            return True
        item_type = item.get("type")
        return item_type not in {"agent_message", "reasoning", "plan", "message", "todo_list", "user_message"}
    return False


class _JSONLProtocol:
    """Incremental protocol state machine used by both parser and subprocess loop."""

    def __init__(self, *, expected_provider: str = "openai", expected_model: str = "gpt-6-sol"):
        self.decoder = codecs.getincrementaldecoder("utf-8")()
        self.buffer = ""
        self.events: list[dict] = []
        self.response: dict | None = None
        self.usage: dict | None = None
        self.agent_message_completed = False
        self.turn_completed = False
        self.expected_provider = expected_provider
        self.expected_model = expected_model

    def _event(self, event: object) -> None:
        if not isinstance(event, dict):
            raise ProtocolError("malformed_response")
        _check_identity(event, self.expected_provider, self.expected_model)
        event_type = event.get("type")
        if event_type == "error" or event_type == "turn.failed":
            raise ProtocolError(_event_error_code(event))
        if _recognized_startup_diagnostic(event):
            self.events.append(event)
            return
        if _event_is_tool(event):
            raise ProtocolError("unexpected_tool")
        if self.turn_completed:
            raise ProtocolError("malformed_response")
        if event_type == "item.completed":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                if self.agent_message_completed:
                    raise ProtocolError("malformed_response")
                text = item.get("text")
                if not isinstance(text, str) or not text.strip():
                    raise ProtocolError("malformed_response")
                try:
                    decoded = json.loads(text)
                except (UnicodeError, json.JSONDecodeError) as exc:
                    raise ProtocolError("malformed_response") from exc
                if not isinstance(decoded, dict):
                    raise ProtocolError("malformed_response")
                self.response = decoded
                self.agent_message_completed = True
        elif event_type == "turn.completed":
            usage = event.get("usage")
            if usage is not None and not isinstance(usage, dict):
                raise ProtocolError("malformed_response")
            self.usage = usage
            self.turn_completed = True
        self.events.append(event)

    def feed(self, data: bytes) -> None:
        try:
            self.buffer += self.decoder.decode(data, final=False)
        except UnicodeDecodeError as exc:
            raise ProtocolError("malformed_response") from exc
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            if line.endswith("\r"):
                line = line[:-1]
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise ProtocolError("malformed_response") from exc
            self._event(event)

    def finish(self) -> ParsedJSONL:
        try:
            self.buffer += self.decoder.decode(b"", final=True)
        except UnicodeDecodeError as exc:
            raise ProtocolError("malformed_response") from exc
        if self.buffer.strip():
            try:
                event = json.loads(self.buffer)
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise ProtocolError("malformed_response") from exc
            self._event(event)
        if not self.turn_completed or not self.agent_message_completed or not isinstance(self.response, dict):
            raise ProtocolError("malformed_response")
        return ParsedJSONL(self.response, self.usage, self.events)


def parse_jsonl(raw: str | bytes, *, expected_model: str = "gpt-6-sol") -> ParsedJSONL:
    """Parse actual Codex JSONL: completed agent_message text plus turn.completed."""
    if not isinstance(raw, (str, bytes)):
        raise ProtocolError("malformed_response")
    if expected_model not in {"gpt-6-sol", "gpt-5.5"}:
        raise ValueError("model is not in the reviewed allowlist")
    parser = _JSONLProtocol(expected_model=expected_model)
    try:
        parser.feed(raw.encode("utf-8") if isinstance(raw, str) else raw)
        return parser.finish()
    except UnicodeEncodeError as exc:
        raise ProtocolError("malformed_response") from exc


def validate_model_response(
    response: dict,
    expected_unit_ids: list[str] | tuple[str, ...],
    artifact_ids_by_unit: dict[str, list[str]],
) -> dict:
    """Validate exact ModelResponseV1 coverage, bounds, and coordinator citations."""
    if not isinstance(response, dict) or set(response) != {"schema_version", "results"}:
        raise SchemaError("response envelope")
    if type(response.get("schema_version")) is not int or response["schema_version"] != 1:
        raise SchemaError("response schema version")
    if not isinstance(expected_unit_ids, (list, tuple)):
        raise SchemaError("expected unit coverage")
    expected = list(expected_unit_ids)
    if any(not isinstance(unit_id, str) or not unit_id.strip() for unit_id in expected):
        raise SchemaError("expected unit identity")
    if len(set(expected)) != len(expected) or not isinstance(artifact_ids_by_unit, dict) or set(artifact_ids_by_unit) != set(expected):
        raise SchemaError("expected unit coverage")
    for refs in artifact_ids_by_unit.values():
        if not isinstance(refs, list) or any(not isinstance(ref, str) or not ref.strip() for ref in refs) or len(set(refs)) != len(refs):
            raise SchemaError("artifact identity")
    results = response.get("results")
    if not isinstance(results, list) or len(results) != len(expected):
        raise SchemaError("result coverage")
    seen: set[str] = set()
    for result in results:
        if not isinstance(result, dict) or set(result) != {"unit_id", "outcome", "findings", "reason", "limitations"}:
            raise SchemaError("result shape")
        unit_id = result["unit_id"]
        if not isinstance(unit_id, str) or not unit_id.strip() or unit_id not in expected or unit_id in seen:
            raise SchemaError("unit identity")
        seen.add(unit_id)
        outcome = result["outcome"]
        if outcome not in {"succeeded_findings", "succeeded_no_findings"}:
            raise SchemaError("outcome")
        reason = result["reason"]
        limitations = result["limitations"]
        if not isinstance(reason, str) or not reason.strip() or not isinstance(limitations, list):
            raise SchemaError("reason or limitations")
        if any(not isinstance(item, str) or not item.strip() for item in limitations):
            raise SchemaError("limitations")
        findings = result["findings"]
        if not isinstance(findings, list) or len(findings) > 8:
            raise SchemaError("findings")
        for finding in findings:
            if not isinstance(finding, dict) or set(finding) != {"claim", "evidence_refs"}:
                raise SchemaError("finding shape")
            claim = finding["claim"]
            refs = finding["evidence_refs"]
            if not isinstance(claim, str) or not claim.strip() or len(claim) > 1200:
                raise SchemaError("finding claim")
            if not isinstance(refs, list) or not refs:
                raise SchemaError("finding citations")
            if any(not isinstance(ref, str) or not ref.strip() for ref in refs):
                raise SchemaError("finding citations")
            if len(set(refs)) != len(refs):
                raise SchemaError("duplicate finding citation")
            if any(ref not in artifact_ids_by_unit[unit_id] for ref in refs):
                raise SchemaError("finding citations")
        if outcome == "succeeded_findings" and not findings:
            raise SchemaError("findings outcome requires finding")
        if outcome == "succeeded_no_findings" and findings:
            raise SchemaError("no-findings outcome has findings")
    if seen != set(expected):
        raise SchemaError("missing unit")
    return response


def _classify_error_text(data: bytes) -> str | None:
    if not data:
        return None
    text = data.decode("utf-8", errors="replace").lower()
    if any(token in text for token in ("rate_limit", "rate limit", "too many requests", "429")):
        return "rate_limit"
    if any(token in text for token in ("quota", "insufficient_quota", "billing", "exhausted")):
        return "quota"
    if any(token in text for token in ("authentication", "unauthorized", "forbidden", "invalid api key", "login required", "401", "403")):
        return "auth"
    return None


def _stop_process(process: subprocess.Popen, pgid: int | None = None) -> None:
    """Terminate and reap the whole captured process group, including grandchildren."""
    group = pgid if pgid is not None else getattr(process, "pid", None)
    if isinstance(group, int):
        try:
            os.killpg(group, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            try:
                process.terminate()
            except (OSError, ProcessLookupError):
                pass
    else:
        try:
            process.terminate()
        except (OSError, ProcessLookupError):
            pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    except (OSError, ProcessLookupError):
        pass
    # Do this even when the group leader already reaped: a grandchild can keep
    # the process group and pipe descriptors alive after the leader exits.
    if isinstance(group, int):
        try:
            os.killpg(group, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
    else:
        try:
            process.kill()
        except (OSError, ProcessLookupError):
            pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except (OSError, ProcessLookupError):
            pass
        try:
            process.wait(timeout=2)
        except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
            pass
    except (OSError, ProcessLookupError):
        pass


def _append_capped(target: bytearray, chunk: bytes, total: int, cap: int) -> tuple[int, bool]:
    remaining = cap - total
    if remaining <= 0:
        return total, True
    if len(chunk) > remaining:
        target.extend(chunk[:remaining])
        return cap, True
    target.extend(chunk)
    return total + len(chunk), False


def run_codex(
    command: list[str],
    prompt: str,
    env: dict[str, str],
    *,
    cwd: Path | str,
    attempt_id: str,
    timeout: float = _MAX_TIMEOUT,
    output_cap: int = _MAX_OUTPUT_BYTES,
    expected_model: str = "gpt-6-sol",
    popen_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
) -> CodexResult:
    """Run one reserved Codex attempt with nonblocking I/O and group cleanup."""
    _validate_command(command)
    _validate_env(env)
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > _MAX_PROMPT_CHARS:
        raise ValueError("prompt must be nonempty and within character limit")
    if not isinstance(attempt_id, str) or not attempt_id.strip():
        raise ValueError("attempt_id is required")
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0 or timeout > _MAX_TIMEOUT:
        raise ValueError("timeout is outside the contract")
    if not isinstance(output_cap, int) or isinstance(output_cap, bool) or output_cap <= 0 or output_cap > _MAX_OUTPUT_BYTES:
        raise ValueError("output cap is outside the contract")
    if not isinstance(cwd, (str, os.PathLike)):
        raise ValueError("cwd must be an absolute directory path")
    workdir = Path(cwd)
    if not workdir.is_absolute() or not workdir.is_dir() or workdir.is_symlink():
        raise ValueError("cwd must be an existing non-symlink directory")

    started = time.monotonic()
    process: subprocess.Popen | None = None
    selector: selectors.BaseSelector | None = None
    stdout = bytearray()
    stderr = bytearray()
    total_output = 0
    parser = _JSONLProtocol(expected_model=expected_model)
    protocol_error: str | None = None
    timed_out = False
    overflow = False
    pgid: int | None = None
    stdin_stream = None
    try:
        process = popen_factory(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(workdir),
            env=dict(env),
            start_new_session=True,
        )
        if process.stdin is None or process.stdout is None or process.stderr is None:
            raise OSError("subprocess streams unavailable")
        pgid = getattr(process, "pid", None)
        stdin_stream = process.stdin
        selector = selectors.DefaultSelector()
        stdin_fd = stdin_stream.fileno()
        os.set_blocking(stdin_fd, False)
        os.set_blocking(process.stdout.fileno(), False)
        os.set_blocking(process.stderr.fileno(), False)
        selector.register(stdin_stream, selectors.EVENT_WRITE, "stdin")
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        pending = prompt.encode("utf-8")

        while selector.get_map():
            remaining = float(timeout) - (time.monotonic() - started)
            if remaining <= 0:
                timed_out = True
                break
            for key, mask in selector.select(min(0.05, remaining)):
                stream = key.fileobj
                kind = key.data
                if kind == "stdin" and mask & selectors.EVENT_WRITE:
                    try:
                        written = os.write(stdin_fd, pending)
                    except (BrokenPipeError, OSError):
                        protocol_error = "process_error"
                        break
                    if written <= 0:
                        protocol_error = "process_error"
                        break
                    pending = pending[written:]
                    if not pending:
                        selector.unregister(stream)
                        stream.close()
                        stdin_stream = None
                elif kind in {"stdout", "stderr"} and mask & selectors.EVENT_READ:
                    try:
                        chunk = os.read(stream.fileno(), 65536)
                    except BlockingIOError:
                        continue
                    except OSError:
                        protocol_error = "process_error"
                        break
                    if not chunk:
                        selector.unregister(stream)
                        stream.close()
                        continue
                    target = stdout if kind == "stdout" else stderr
                    total_output, overflow = _append_capped(target, chunk, total_output, output_cap)
                    if overflow:
                        break
                    if kind == "stdout":
                        try:
                            parser.feed(chunk)
                        except ProtocolError as exc:
                            protocol_error = str(exc)
                            break
            if timed_out or overflow or protocol_error:
                break

        if timed_out or overflow or protocol_error:
            if process is not None:
                _stop_process(process, pgid)
            code = "timeout" if timed_out else "output_overflow" if overflow else protocol_error
            return CodexResult("failed", error_code=code, stdout=bytes(stdout), stderr=bytes(stderr), wall_seconds=time.monotonic() - started, attempt_id=attempt_id)

        remaining = float(timeout) - (time.monotonic() - started)
        if remaining <= 0:
            if process is not None:
                _stop_process(process, pgid)
            return CodexResult("failed", error_code="timeout", stdout=bytes(stdout), stderr=bytes(stderr), wall_seconds=time.monotonic() - started, attempt_id=attempt_id)
        try:
            returncode = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            _stop_process(process, pgid)
            return CodexResult("failed", error_code="timeout", stdout=bytes(stdout), stderr=bytes(stderr), wall_seconds=time.monotonic() - started, attempt_id=attempt_id)

        if returncode != 0:
            envelope_error = _classify_error_text(bytes(stdout) + b"\n" + bytes(stderr))
            if envelope_error:
                code = envelope_error
            elif stdout:
                code = "malformed_response"
            else:
                code = "process_error"
            return CodexResult("failed", error_code=code, stdout=bytes(stdout), stderr=bytes(stderr), wall_seconds=time.monotonic() - started, attempt_id=attempt_id)
        try:
            parsed = parser.finish()
        except ProtocolError as exc:
            return CodexResult("failed", error_code=str(exc), stdout=bytes(stdout), stderr=bytes(stderr), wall_seconds=time.monotonic() - started, attempt_id=attempt_id)
        return CodexResult("candidate", response=parsed.response, usage=parsed.usage, stdout=bytes(stdout), stderr=bytes(stderr), wall_seconds=time.monotonic() - started, attempt_id=attempt_id)
    except (OSError, BrokenPipeError, subprocess.TimeoutExpired):
        if process is not None:
            _stop_process(process, pgid)
        return CodexResult("failed", error_code="process_error", stdout=bytes(stdout), stderr=bytes(stderr), wall_seconds=time.monotonic() - started, attempt_id=attempt_id)
    finally:
        if selector is not None:
            try:
                selector.close()
            except OSError:
                pass
        for stream in (stdin_stream, getattr(process, "stdout", None), getattr(process, "stderr", None)):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
