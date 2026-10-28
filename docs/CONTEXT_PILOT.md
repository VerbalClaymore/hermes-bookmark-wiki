# Context pilot modules

This document describes the isolated, stdlib-only context pilot primitives. They do not add a CLI and do not fetch live sources or invoke a model by themselves. The coordinator supplies synthetic or reviewed runtime inputs.

## Test-first evidence

The required RED command was run after adding only the three new test files and before adding production modules:

```sh
python3 -m unittest discover -s tests -v
```

Observed result: exit 1. The original 26 tests passed, and the three new test modules failed to import because `bookmark_wiki.context_state`, `bookmark_wiki.safe_fetch`, and `bookmark_wiki.subscription` did not yet exist.

The full GREEN command after implementation was:

```sh
python3 -m unittest discover -s tests -v
```

Observed result: exit 0, `Ran 43 tests`, `OK` on the first implementation GREEN run. After the final safety additions, the same command was rerun with `Ran 44 tests`, `OK`.

Additional verification commands:

```sh
python3 -m compileall -q bookmark_wiki
python3 -m unittest discover -s tests -v
```

All completed successfully in the isolated checkout. Tests use temporary synthetic roots, fake DNS, fake HTTPS connections, and local child processes only.

## `context_state`

```python
ContextStore(root: pathlib.Path | str, *, run_id: str = "pilot-v1")
```

`ContextStore` accepts only `pilot-v1`. Mutating methods require the shared archive lock:

```python
with store.locked():
    attempt = store.reserve(kind, metadata=None)
    ref = store.write_artifact(data, extension)
    store.write_cache(cache_key, record)
    hit = store.lookup_cache(cache_key, request_identity)
```

`kind` is `acquisition`, `http`, or `model`. Persistent reservations are written before external work and are bounded at 12, 24, and 2 respectively. An unfinished reservation consumes its budget. The lock is the existing `state/update.lock` inode used by `bookmark_wiki.archive`.

```python
validate_unit(unit: dict) -> dict
validate_transition(previous: dict, current: dict) -> dict
validate_analysis_record(record: dict) -> dict
cache_key(identity: object) -> str
```

`validate_unit` checks the complete discovery, acquisition, and analysis matrix, required UTC timestamps, nonempty reasons, evidence path plus SHA256 references, source identity, channel, and revision. `validate_transition` requires a new attempt ID for a changed event and prevents successful states from regressing. `validate_analysis_record` validates trusted coordinator provenance and does not treat empty or legacy model output as success.

`write_artifact` writes `context-evidence/sha256/<sha256>.<extension>` exclusively. An equal rerun is a no-op. A digest collision, symlink, or changed existing byte sequence fails closed. Cache records are immutable. Only a record with `status="accepted"`, an accepted decision, and matching request identity can be returned by `lookup_cache`; candidate and rejected records never hit.

Synthetic example:

```python
from bookmark_wiki.context_state import ContextStore, cache_key

store = ContextStore("./synthetic-vault")
with store.locked():
    evidence = store.write_artifact(b"synthetic evidence", "txt")
    key = cache_key({"stage": "text", "input": [evidence["sha256"]]})
```

## `safe_fetch`

```python
validate_https_url(url: str) -> object
fetch(
    url: str, *, store: ContextStore, kind: str = "text",
    max_bytes: int | None = None, total_timeout: float = 30.0,
    dns_resolver=None, connection_factory=None, clock=time.monotonic,
) -> FetchResult
```

`fetch` reserves an acquisition attempt before URL validation and an HTTP attempt immediately before each request, including redirects. It accepts HTTPS on port 443 only, rejects userinfo, fragments, controls, ambiguous hosts, and non-global IPv4 or IPv6 DNS results. The connection seam receives `(url, validated_ip, remaining_timeout)` and the request retains the original hostname in `Host`, allowing deterministic pinning tests. Proxies, cookies, credentials, compressed responses, bad MIME, empty bodies, redirect overflow, and streaming size overflow are rejected.

`FetchResult` fields are `ok`, `state`, `body`, `final_url`, `error_code`, `reason`, `http_status`, `redirects`, and `attempts`. Successful acquisition has `state="captured"`. Explicit 401, 403, 404, and 410 responses have `state="unavailable"`. Other HTTP failures, DNS failures, timeout, size, MIME, and URL failures have `state="failed"`; none become scoped absence.

No default test calls the network. A coordinator must inject DNS and connection seams in deterministic tests and pass only acquired local files to later frame preparation.

## `subscription`

```python
build_child_env(
    home: str | os.PathLike, tmpdir: str | os.PathLike,
    path: str = "/opt/homebrew/bin:/usr/bin:/bin",
) -> dict[str, str]
build_codex_command(
    executable: str, schema_path: str,
    image_paths: list[str] | tuple[str, ...] = (), *,
    model: str = "gpt-6-sol",
) -> list[str]
parse_jsonl(raw: str | bytes) -> ParsedJSONL
validate_model_response(
    response: dict, expected_unit_ids: list[str] | tuple[str, ...],
    artifact_ids_by_unit: dict[str, list[str]],
) -> dict
run_codex(
    command: list[str], prompt: str, env: dict[str, str], *,
    cwd: pathlib.Path | str, attempt_id: str, timeout: float = 180.0,
    output_cap: int = 1000000, popen_factory=subprocess.Popen,
) -> CodexResult
```

`build_child_env` creates only `HOME`, `CODEX_HOME`, `PATH`, `TMPDIR`, `LANG`, and `LC_ALL`. `build_codex_command` fixes the ChatGPT provider, forced login, model, read-only sandbox, ignored user config and rules, strict config, disabled web search, empty MCP configuration, and the reviewed no-tool feature list. Image arguments are repeated and capped at eight.

`parse_jsonl` requires a final response event, rejects malformed JSON, provider mismatch, and tool or command events, and preserves usage only when present. `validate_model_response` enforces exact keys, exact per-call unit coverage, finding bounds, no-findings reasoning, and citations that resolve only to frozen per-call artifact IDs.

The caller reserves a model attempt with `ContextStore.reserve("model", ...)` before calling `run_codex`. `run_codex` streams stdout and stderr, enforces the combined output cap and timeout, starts a new process group, and terminates, kills, and reaps the group on timeout or overflow. It returns `status="candidate"` only for a zero-exit process with a parseable response. Candidate output still requires independent coordinator validation and acceptance before caching.

Synthetic example:

```python
from bookmark_wiki.subscription import build_child_env, build_codex_command

env = build_child_env("./synthetic-home", "./synthetic-tmp")
command = build_codex_command("/synthetic/bin/codex", "/synthetic/schema.json")
```

## Final repair verification and accepted-cache binding

The coordinator reproduced three narrow reviewer findings as RED regressions: TCP time was not subtracted before TLS, request identity lacked mandatory bindings, and the wire schema used an unnecessary strict-dialect keyword. After repairs, the full suite passed 53 tests and compileall. Earlier 43/44-test results above are historical, not the final acceptance evidence.

`safe_fetch.fetch` additionally accepts `accept="default"|"github_raw"|"github_json"`. It assigns fixed public request headers, not arbitrary caller headers. TCP and TLS share one remaining deadline.

`ContextStore.append_event(event_type, **fields)`, `write_plan(plan, name="plan.json")` and `read_ref(ref, parse_json=False)` require the shared lock. Accepted cache records have exactly `schema_version`, `status`, `cache_key`, `request`, `call_plan_ref`, `attempt_ref`, `model_response_ref`, `acceptance`. Reference objects have `path` and `sha256`.

Request identity has exactly `source_revisions`, `ordered_input_hashes`, `stage`, `provider`, `requested_model`, `prompt_version`, `schema_version`, `prompt_sha256`, `output_schema_sha256`, `expected_unit_ids`, `artifact_ids_by_unit`, `input_artifact_refs`. The input references must be the exact ordered call-plan artifacts followed by the prompt and schema references. All are hash-verified. Fixed identity values are context-pilot, openai-chatgpt-codex, gpt-6-sol, context-pilot-1 and schema version 1. Request model, source revisions, unit/citation mapping and prompt/schema digests must match the frozen call plan. Candidate and rejected records are not accepted-cache hits.

`subscription.MODEL_RESPONSE_SCHEMA` is the strict model wire schema. Actual Codex JSONL requires an `item.completed` agent-message text containing JSON, followed by `turn.completed`. Protocol failure is checked while streaming, not after waiting for a tool invocation to finish. Unknown observed model identity must be reported as unknown.

## Scope and limitations

These modules do not implement the private coordinator, source manifest updates, media frame extraction, login-status probing, real Codex entitlement checks, or claim verification against a corpus. They do not read credentials, use live IDs, resolve real bookmark URLs, or perform network or model work in tests. The coordinator remains responsible for executable validation, frozen call plans, artifact-to-citation resolution, independent acceptance records, and honest deferred or unavailable outcomes.
