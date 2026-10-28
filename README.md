# Hermes Bookmark Wiki

Repository: [VerbalClaymore/hermes-bookmark-wiki](https://github.com/VerbalClaymore/hermes-bookmark-wiki).
The Python import and command name remains `bookmark_wiki`.

See [current status](docs/STATUS.md) for the distinction between reviewed code and live acceptance. Historical specifications retain their original phase-specific instructions; the initial GitHub publication does not activate a batch or schedule.

BookmarkWiki is a local-only Python 3 reference implementation for importing an allowlisted bookmark snapshot into an append-only manifest and SQLite FTS5 search index.

## Guarantees

- Standard-library only; no network, provider, model, X, or profile operations.
- Snapshot and raw-note digests are validated before publication.
- Raw provider fields are ignored. Only the documented allowlist is persisted.
- Manifest revisions are append-only and the manifest is authoritative over the index.
- Updates use a nonblocking POSIX lock and two-file replacement with fsync.
- CLI stdout is one JSON object; errors use fixed generic messages and exit 2.

## Local commands

Run from this checkout:

```sh
python3 -m bookmark_wiki update --vault ./demo-vault \
  --snapshot ./demo-vault/source-evidence/example/evidence.json \
  --raw-hashes ./demo-vault/source-evidence/example/raw-sha256.json
python3 -m bookmark_wiki status --vault ./demo-vault
python3 -m bookmark_wiki search --vault ./demo-vault --query "synthetic topic"
python3 -m bookmark_wiki show --vault ./demo-vault --id x:123456789
python3 -m bookmark_wiki intake --vault REAL_VAULT --selection REAL_VAULT/state/library-intake-pilot-v1/selection.json --output-root REAL_VAULT/state/library-intake-pilot-v1
```

The example paths are placeholders. No live corpus is included.

## Link learning: completed pilot and next phase

The manual four-link Finn/Vidya learning pilot is complete; its private retained report records A1–A8 passed. This proves the manually coordinated workflow, not an automated feature of this CLI. [Phase 1 specification](docs/LINK_LEARNING_MVP_SPEC.md) preserves the original acceptance contract.

[Phase 2 — BookmarkWiki intake pilot and pipeline roadmap](docs/BOOKMARK_INTAKE_PHASE2_SPEC.md) preserves the verified offline packet adapter and accepted Finn/Vidya learning applications. Historical B11 scope certification remains **partial**: successful current checks cannot replace the missing old per-file baseline or erase the disclosed temporary-folder deviation. No new specialist batch follows automatically.

## Durable manual learning core (3A)

The `learning` family persists coordinator progress for explicitly approved, packet-backed batches. It never creates a task, installs a skill, fetches a source, publishes wiki pages, schedules work or deletes a bookmark. Native Kanban remains task authority; the ledger records historical observations, not live worker status. Specification v1.2 §12 and [the contract](docs/CONTRACT.md#durable-learning-progress-3a) define its limits.

Before init, the operator prepares `<vault>/state/learning-process-v1/inputs/roots.json` with exactly `{"schema_version":1,"hermes_home":"/canonical/operator-selected/hermes-home"}`. This root is pinned by byte hash; no environment discovery or silent roots change occurs. All supplied paths must be canonical and non-symlinked. Inputs are explicit reviewed JSON events under `inputs/`; referenced evidence is read-only within the allowed roots.

```sh
python3 -B -m bookmark_wiki learning init --vault REAL_VAULT
python3 -B -m bookmark_wiki learning apply --vault REAL_VAULT --input REAL_VAULT/state/learning-process-v1/inputs/approved-event.json
python3 -B -m bookmark_wiki learning status --vault REAL_VAULT
python3 -B -m bookmark_wiki learning check --vault REAL_VAULT --batch-id REGISTER_EVENT_SHA256
```

These are placeholders, not an automatic import recipe. Apply requires coordinator-authored authority and exact evidence linkage. Receipt events retain the validated disposition independently of external evidence; `check` rehashes evidence and reports `needs_reconciliation` on drift without changing the historical decision or redispatching. Exact replay is a no-op, not permission to retry task creation. Wiki destinations stay deferred; a blocked/deferred destination prevents local completion. Cutover requires a separate coordinator acceptance report after C1–C12, not merely successful init. Historical receipt files remain immutable evidence authority. Phase1 general-link dedup stays manual.

## Development

```sh
TMPDIR="$(python3 -c 'import os,tempfile; print(os.path.realpath(tempfile.gettempdir()))')" \
  PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -v
git diff --check
```

Set `TMPDIR` to an existing canonical, non-symlink scratch directory **outside this checkout** before tests; system temporary-directory aliases may correctly fail path-safety tests. The command above canonicalizes the existing temporary root. A relative-path regression case assumes its fixture is outside the source tree. Archive and legacy-learning tests use the standard library. Bulk view rendering requires an already qualified Python interpreter with Python-Markdown; tests must run with that interpreter as `python3`. No command installs dependencies.

The legacy frozen transport tests are opt-in: set `BOOKMARK_WIKI_PRIVATE_TEST_VAULT` to the preserved local fixture vault to include them. They require private frozen artifacts and a pinned local executable, neither of which is distributed here. Without that setting they are reported as skipped, not passed. The transport is mocked in those tests; they do not call a model. The default suite uses synthetic fixtures and does not read a private vault.

See [docs/CONTRACT.md](docs/CONTRACT.md) for the portable data and CLI contract. See [docs/CONTEXT_PILOT.md](docs/CONTEXT_PILOT.md) for the isolated context pilot modules. The retrieval skill in `skills/second-brain/SKILL.md` is descriptive and is not auto-activated or installed into a profile.

## Existing-archive backlog (reviewed code; live acceptance pending)

The `bulk` commands implement the bounded §13 coordinator path. Geoffrey makes the
semantic agent-or-wiki decision; Python validates evidence, records progress and
publishes only explicitly reviewed local plans. The CLI never launches a model,
creates native tasks, schedules work, fetches sources or deletes bookmarks.
Recipients come from the installed native name-only profile API at preparation
and are rechecked before pending dispatch, not from a fixed list.

```sh
python3 -B -m bookmark_wiki bulk prepare --vault REAL_VAULT --limit 50
python3 -B -m bookmark_wiki bulk apply --vault REAL_VAULT --input REVIEWED_BULK_EVENT
python3 -B -m bookmark_wiki bulk handoffs --vault REAL_VAULT --batch-id BATCH_SHA256
python3 -B -m bookmark_wiki bulk publish --vault REAL_VAULT --input REVIEWED_PLAIN_PLAN
python3 -B -m bookmark_wiki bulk status --vault REAL_VAULT
python3 -B -m bookmark_wiki bulk digest --vault REAL_VAULT --batch-id BATCH_SHA256
```

Uppercase tokens are documentation placeholders, not valid literal identifiers.
Use the exact schemas in specification §13. `publish` takes the plain plan, not
an apply-event wrapper. Reference retention means retrievable knowledge, not a
claim of improved model ability or consent to follow source advice.

Private `inputs/bulk-v1/settings.json` contains canonical operator-selected
`hermes_python`, `hermes_source` and `renderer_python` paths with schema_version 1.
Qualify native discovery and isolated Python-Markdown import first. Preparation
retains the exact settings at `evidence/bulk-v1/settings/<sha256>.json`. A bulk
batch authority FileRef names a reviewed record containing the exact spec/version,
PASS and 50-source bound, plus exactly one `Settings-SHA256: <sha256>` line. This
binds runtime settings without changing the six-table ledger or event payloads.
This structural guard cannot authenticate user consent or replace semantic review.

Accepted immutable notes and reference files are checked separately from mutable
index/view/parent files. Later committed aggregate updates preserve earlier
coverage; replay never restores an older aggregate. Missing evidence or manual
drift requires reconciliation, not rereading or overwrite. Pending/uncertain work
is resumed in the same batch. The first live run is capped at 50 and stops with a
digest. The bounded repair has passed independent code review and is included in
this checkout. Real specialist/wiki acceptance is still required; offline tests
do not establish those live gates. Historical B11 stays
PARTIAL. No unattended archive drain is enabled.
