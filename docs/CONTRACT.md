# Portable contract

This repository implements a bounded offline archive. It accepts a caller-selected snapshot and digest file beneath a configured local vault. It never fetches URLs, invokes a provider, reads credentials, or writes a profile.

## Input boundary

The snapshot is a JSON object with a timezone-aware `captured_at` and `tables` arrays named `bookmarks`, `media`, `categories`, and `assignments`. Unknown fields are ignored and are never copied. The accepted fields are:

- bookmark: `id`, `tweetId`, `text`, `authorHandle`, `authorName`, `tweetCreatedAt`, `importedAt`, `semanticTags`, `entities`, `enrichedAt`
- media: `id`, `bookmarkId`, `type`, `url`, `thumbnailUrl`, `imageTags`
- category: `id`, `name`, `slug`
- assignment: `bookmarkId`, `categoryId`

IDs and foreign keys are validated. Tweet IDs are unchanged ASCII digits and become source IDs of the form `x:<digits>`. `semanticTags`, `entities`, and `imageTags` are JSON-encoded strings. Only URL hints, vision status, and allowlisted metadata are retained. Absolute HTTP(S) URL hints are stored without fetching them.

The capture manifest contains `evidence_sha256` for the exact snapshot bytes. The raw digest JSON maps vault-relative `raw/.../*.md` paths to lowercase SHA256 values. Every listed digest is checked. Direct Markdown children of a native `Twitter Bookmarks` directory must end in ` - <ASCII tweet digits>.md`; Markdown index files under `_index/` are checked but never ingested as sources. Symlinks, traversal, malformed paths, missing files, orphan notes, and incomplete digest coverage are rejected.

## Durable files

The authoritative `manifest.json` uses canonical UTF-8 JSON with sorted keys, compact separators, and one trailing newline. It has `schema_version`, UTC `updated_at`, and `sources`. Each source has a current revision, append-only revision map, lifecycle statuses, wiki links, and removal eligibility. Revision hashes cover canonical content, canonical metadata, and the raw digest. Historical evidence and raw bytes are rechecked before every update.

The derived SQLite database is disposable and contains only the current revision of each source. Its `meta` table records schema version and the manifest SHA256. The FTS5 index weights text, author, URLs, categories, and semantic hints. The manifest is never reconstructed from SQLite.

Updates hold a nonblocking exclusive POSIX lock. Candidate manifest and complete SQLite index are staged and fsynced before replacement. Manifest replacement precedes index replacement. Readers validate manifest bytes, SQLite integrity, metadata, generation, and source/revision coverage before returning data.

## CLI

```text
python3 -m bookmark_wiki update --vault PATH --snapshot PATH --raw-hashes PATH
python3 -m bookmark_wiki status --vault PATH
python3 -m bookmark_wiki search --vault PATH --query TEXT [--limit 8] [--excerpt-chars 600]
python3 -m bookmark_wiki show --vault PATH --id x:<ASCII digits>
```

Each successful invocation prints one JSON envelope with `schema_version`, `status`, `command`, and `data`. Errors exit 2 and print one fixed-message envelope with a code such as `INVALID_ARGUMENT`, `INVALID_INPUT`, `INTEGRITY_ERROR`, `LOCKED`, `STALE_INDEX`, or `NOT_FOUND`. Absolute paths, tracebacks, source payloads, and provider fields do not appear in the envelope or stderr.

Search treats the query as literal text, extracts at most 32 unique lowercase Unicode word tokens, quotes tokens for FTS5, ranks exact identity matches first, then AND matches, then OR fill matches. Limits are rejected rather than clamped. Returned excerpts are original source text bounded by the requested codepoint count; no answers are synthesized.

## Bounded intake packet (2A)

`intake --vault PATH --selection FILE --output-root PATH` accepts a UTF-8 JSON selection with exactly `schema_version: 1` and one to five distinct literal `x:<ASCII digits>` IDs. Selection must be a non-symlink file beneath `<vault>/state/library-intake-pilot-v1`; output-root must be exactly that dedicated path. It uses nonblocking `.intake.lock`, reads status/show/status without repairing the archive, and emits only the exact allowlisted `show` fields. Packet canonical JSON is UTF-8, sorted/compact, newline terminated; SHA-256 of packet bytes names `packets/<digest>/packet.json`. Existing exact bytes are a no-op; conflicts and incomplete targets fail closed. Per-record limit is 131,072 canonical bytes; packet limit is 1,048,576 bytes. The CLI response includes only packet ID, count, and created flag. Errors are fixed envelopes and exit 2. Intake does not verify sources, route learning, follow links, or modify profiles.

## Durable learning progress (3A)

Normative bounded contract: [specification v1.2 §12](BOOKMARK_INTAKE_PHASE2_SPEC.md#12-phase-3a--bounded-durable-processing-contract). This is offline manual coordinator bookkeeping, not a task queue, scheduler or automated learning service. The archive owns source history, wiki Markdown owns published pages, native `bookmark-skills` owns task execution, and immutable historical JSON receipts own their evidence. The ledger becomes progress authority only by an explicit C1–C12 acceptance/cutover report for newly registered packet-backed batches.

### Public commands and envelopes

```text
python3 -B -m bookmark_wiki learning init --vault PATH
python3 -B -m bookmark_wiki learning apply --vault PATH --input FILE
python3 -B -m bookmark_wiki learning status --vault PATH
python3 -B -m bookmark_wiki learning check --vault PATH --batch-id SHA256
```

Success envelope is exactly `{schema_version:1,status:"ok",command:"learning",data:...}`; data shapes:

- init: `{action:"init",schema_version:1,created:boolean}`.
- apply: `{action:"apply",event_id:SHA256,applied:boolean}`.
- status: `{action:"status",schema_version:1,batches:int,destinations:[{destination_id,target_id,destination,profile,recorded_state,observed_task_status,task_observed_at}]}`.
- check: `{action:"check",batch_id,results:[{destination_id,recorded_state,effective_state,reason_code}],locally_complete:boolean}`.

Destination lists sort by destination ID. Observed task status/time is explicitly historical or null, never live API state. Status over 100 destinations fails `INPUT_TOO_LARGE` without a partial list; check is batch bounded. Valid unresolved check results exit 0. Errors exit 2 with exactly `{schema_version:1,status:"error",command:"learning",error:{code,message}}`, including malformed arguments after a literal first token `learning`. Unknown top-level commands retain `command:"unknown"`. Help is argparse help, not a data operation.

Fixed codes/messages include `INVALID_ARGUMENT` ("Invalid argument."), `INPUT_TOO_LARGE` ("Input exceeds the permitted size."), `NOT_FOUND` ("Requested source was not found."), `INTEGRITY_ERROR` ("Input integrity check failed."), `LOCKED` ("Update is already in progress."), `IO_ERROR` ("I/O operation failed."), `NOT_INITIALIZED` ("BookmarkWiki is not initialized."), `UNSUPPORTED_SCHEMA` ("Unsupported learning schema."), and `INVALID_TRANSITION` ("Learning transition is not permitted."). No source text, private paths, task bodies or exception details are emitted.

### Files, evidence and identity

Runtime root is exactly `<vault>/state/learning-process-v1/`, with `ledger.sqlite3`, rollback journal, `.learning.lock`, `inputs/`, `evidence/`, `reports/`; `.ledger-stage-<random>.sqlite3` is init-only staging. CLI writes only DB/journal/lock/staging, not coordinator inputs or evidence. Input files are regular UTF-8 JSON under `inputs/`, at most 1,048,576 bytes. JSON rejects duplicate keys, unknown fields and nonfinite numbers; strict integers reject booleans. Canonical JSON is sorted-key, compact UTF-8, ensure_ascii=False, one trailing newline.

Operator-authored `inputs/roots.json` is exactly `{schema_version:1,hermes_home:absolute_path}` and is required for init; its bytes are hash-pinned. Lexical traversal/symlink checks precede resolution for all accessed paths. Allowed FileRef roots: the two historical pilot directories, current runtime `evidence/`, derived native `kanban/boards/bookmark-skills/attachments/`, Finn `profiles/finn/skills/creative/visual-style-research/`, and Vidya `profiles/vidya/skills/product-video-workflow/`. No general file read or directory crawl is part of the core. Path checks are static, not a concurrent-hostile-filesystem sandbox.

FileRef is exactly `{path,sha256}`; at most 100 reference occurrences per event, regular non-symlink files up to 64 MiB, installed skill/reference files up to 1 MiB. All SHA256 literals are 64 lowercase ASCII hex characters. Task IDs are literal `t_[0-9a-f]{8}`, source IDs literal `x:[0-9]+`. Target identity hashes `{identity_url,revision,scope_read}`; destination identity hashes `{target_id,destination,profile}`. HTTPS URL literals require a hostname and no userinfo; they are neither fetched nor normalized. Revision is nonempty and at most 128 characters. Read scope is an ordered, distinct nonempty list (1–20 entries, each at most 256 characters); unchecked scope permits 0–20 entries. Different profile/revision/scope identities never collapse. Each batch has 1–5 targets/source records, 1–10 links/destinations, no more than two recipients per source; initial roster is Finn/Vidya only, wiki profile null.

### Exact event payloads

Every input is exactly `{schema_version:1,kind,payload}`; SHA256 of its canonical bytes is event ID. A register event's ID is batch ID; a dispatch event's ID is intent ID.

- `register_batch`: `{authority,packet,targets,links,destinations}`. Targets exactly `{identity_url,revision,scope_read,scope_unchecked,evidence:[FileRef,...]}`; links exactly `{source_id,archive_revision,target_id}`; destinations exactly `{target_id,destination,profile,reason}`. Verify frozen packet canonical digest/directory and source/revision/target coverage, no duplicates/foreign targets. No current archive-generation requirement for historical captured learning.
- `dispatch_intent`: `{batch_id,destination_ids,stage,ordinal,authority}`. Sorted distinct 1–5 specialist destinations, one profile and batch, approved_batch authority only. Stages reading/application, ordinal 0 initial or 1 correction; unresolved intents/tasks prevent overlapping work. Application requires this batch's done reading; one correction per unchanged destination/stage across batches, including imported observations. Installation approval/failed-property semantics remain Geoffrey's responsibility in the authority record.
- `task_observed`: `{batch_id,intent_id,destination_ids,stage,ordinal,observation}`. Observation exactly `{task_id,board,assignee,status,observed_at,body_sha256,evidence}`. Board must be bookmark-skills and owner match. Normalized evidence has those scalars plus `attachments:[FileRef,...]`, not its own evidence field. Intents bind one task; task IDs cannot be reassigned across owner/stage/ordinal/batch. Same-task retries are historical observations, not new intents. Reject earlier parsed observed instants, wrong body/owner/board, malformed literals. Null intent is historical_import only, with exact task IDs enumerated in inspected authority. Coordinator must obtain actual native readbacks; the core checks linkage/bytes, not native authenticity.
- `receipt_accepted`: `{batch_id,destination_id,disposition,receipt,review}`. Disposition is adopted/reference_saved/already_covered/not_useful/blocked, validated against exactly one linked source decision. Historical top-level acceptance, target identity, exact ordered scope, all artifacts, installed paths and preimages are verified according to §12.5. Only in-memory normalization selects legacy path/SHA256, checks optional sizes and ignores inert native_path; historical bytes are never rewritten. Required facts cannot be supplied by nested proposal metadata.

Authority is exactly `{record:FileRef,scope}` with scope historical_import/approved_batch. It names actual operator consent, exact files/targets/tasks and verification boundary; consent authenticity and semantic review remain Geoffrey's responsibility. ReviewRecord is exactly `{schema_version:1,receipt_sha256,destination_id,source_id,verdict,reason,preimages,coverage,evidence}`, verdict accepted, nonempty reason ≤2,000 characters. Preimages (0–10) exactly `{installed_path,file:FileRef}`, coverage (0–10) exactly `{file:FileRef,section,trigger}`, evidence 1–20 FileRefs. Installed paths are unique and nonnull before hashes must match preimages. Adopted requires done reading/application (and named correction), passed application, artifacts and all current after hashes. Reference_saved/already_covered require exact current coverage/trigger evidence; not_useful needs a concrete role-relative reason; blocked never closes learning. Wiki receipts are rejected.

### State, persistence and recovery

Recorded states: pending, deferred (wiki), dispatch_uncertain, awaiting_result, awaiting_review, blocked, accepted, declined. Validated receipt disposition is retained in the append-only event: external receipt loss/drift cannot rewrite history. Accepted/declined takes precedence over later native retries. Check independently revalidates all applicable references: intact accepted/declined is known_noop; missing/changed/identity-mismatched evidence is needs_reconciliation. Reasons are INTACT_RECEIPT, MISSING_EVIDENCE, HASH_MISMATCH, IDENTITY_MISMATCH, NO_ACCEPTED_RECEIPT, WIKI_DEFERRED. Local completion is conjunctive: every required destination must be known_noop. Decline closes a decision, not learning; wiki deferred/blocked prevents completion. No deletion authority follows.

SQLite uses foreign_keys=ON, journal_mode=DELETE, synchronous=FULL, busy_timeout=0, user_version=1. Exact tables:

```sql
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL, kind TEXT NOT NULL, payload_json TEXT NOT NULL, recorded_at TEXT NOT NULL);
CREATE TABLE batches(batch_id TEXT PRIMARY KEY REFERENCES events(event_id), register_json TEXT NOT NULL);
CREATE TABLE destinations(destination_id TEXT PRIMARY KEY, target_id TEXT NOT NULL, destination TEXT NOT NULL, profile TEXT, target_json TEXT NOT NULL);
CREATE TABLE batch_destinations(batch_id TEXT REFERENCES batches(batch_id), destination_id TEXT REFERENCES destinations(destination_id), PRIMARY KEY(batch_id,destination_id));
CREATE TABLE task_bindings(task_id TEXT PRIMARY KEY, intent_id TEXT UNIQUE, profile TEXT NOT NULL, stage TEXT NOT NULL, ordinal INTEGER NOT NULL, body_sha256 TEXT NOT NULL);
```

Meta keys only schema_version, roots_sha256, spec_id, spec_version (1, pinned roots hash, BOOKMARK-INTAKE-PHASE2, 1.2). Payload text is canonical; rows/bindings/source links reconcile with the append-only events. No status column, lease or second job queue. Init stages/validates/fsyncs and renames; repeated valid init does not rewrite; unknown schema/corruption never auto-migrates/rebuilds. Abandoned init stages are ignored, not deleted. Apply validates before BEGIN IMMEDIATE, rechecks history under nonblocking writer lock, captures UTC recorded_at inside transaction, revalidates input/pinned evidence before commit, and commits all rows atomically. Exact replay rechecks evidence against the original historical prefix without new rows/timestamps or rewriting DB. Status/check open SQLite read-only and never initialize/migrate.

No transaction spans Kanban/profile writes. The coordinator saves intent first, creates at most one native task, reads it and attachments back, then records observation. An uncertain create stops for exact board/body reconciliation; zero/multiple matches require operator disposition, not blind retry/replacement. No automatic callback, poller or infrastructure repair. Historical import is explicit packet-backed Phase2 evidence only; Phase1 general links stay external/manual. C11/C12 require real retained artifacts and native readbacks, twice replayed in fresh processes, protected inventory checks and an explicit cutover report. Historic B11 remains PARTIAL regardless of current checks; no new live batch is authorized by core completion.


## Existing-archive bulk coordination (§13)

This lane extends events only with bulk_batch, bulk_route, bulk_intent,
bulk_observed, bulk_result and bulk_publish. Existing schema/meta/materialized
legacy rows and legacy command scope remain unchanged. Task IDs cannot be rebound
between the legacy and bulk lanes in either direction. Do not open a bulk ledger
with an old binary and promise downgrade compatibility.

All six commands use the fixed JSON envelope, command bulk, valid-result exit 0
and fixed-error exit 2. Exact wire payloads, bounds, ResultReview and
PublicationReview are specified in §13 of BOOKMARK_INTAKE_PHASE2_SPEC.md. Native
Kanban is execution authority; event state is coordinator progress, not live task
polling. Semantic routing/review and actual native readbacks are coordinator duties.

Settings are canonical private JSON and contain only schema_version,
hermes_python, hermes_source and renderer_python. The coordinator first qualifies
those paths; native discovery is a fixed isolated import of list_profile_names
and get_profile_dir. Settings bytes are content-addressed at preparation. The
bulk authority record has exactly one line `Settings-SHA256: <64-lowercase-hex>`
binding those retained bytes. The authority also identifies the reviewed §13
version, PASS and first-run cap. It is a coordinator record, not authority supplied
by source text or a worker. No credentials, environment inventories or role text
are copied into the public repository or source packet.

A returned worker assessment and learning.json must be retained and exactly bound
to the observed done task before reference publication. Done status alone is not
accepted learning. Explicit no-task or native blocked outcomes need concrete
reviewed diagnostic evidence, not fictional attachments. already_covered binds
an actual own-skill FileRef, section and trigger. Decline remains intermediate
until wiki publication or an explicit blocker; recorded outcomes survive evidence
loss, while effective reuse degrades to needs_reconciliation.

A Note envelope is canonical JSON with schema_version 1, a frozen valid UTC date
and the exact structured Note. Its full bytes including newline define the note
digest. The plain publication plan binds that envelope, target kind/owner/path,
reviewed source coverage and staged outputs. The publisher checks literal quotes,
explanatory coverage and byte equality to deterministic rendering before any target
write. Wiki notes are reader-visible via the index and local reading view; archive
FTS is still source search, not wiki search.

Publication uses the existing nonblocking learning lock, immutable retained plans
and preimages, fsynced sibling stages and per-target guarded replacement. No SQL
transaction spans filesystem writes. A partial transaction must be reconciled
before successors. Human drift is never rolled back. Only index, reading view and
pipeline-owned bookmark-library parent are mutable aggregates. Their latest
committed predecessor chain and current navigation guard reuse; old replay has
zero writes and cannot remove later links. Notes/reviews/staged evidence remain
immutable. There is no atomic multi-file reader-visibility claim.

Verification status: isolated offline implementation and synthetic CLI/file/SQLite
flows exercised; fresh-eyes implementation review, original-checkout integration
and the real at-most-50-source flow are separate mandatory gates. No live cutover,
new procedural behavior, network capture, schedule or deletion follows from an
offline test pass. Historical B11 remains PARTIAL.
