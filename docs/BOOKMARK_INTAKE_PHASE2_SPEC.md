# Phase 2 — BookmarkWiki intake pilot and pipeline roadmap

> Documentation only. No implementation, specialist dispatch, profile installation, new source fetch, schedule, publication or bookmark deletion is authorized by writing this plan.

**Spec ID:** BOOKMARK-INTAKE-PHASE2  
**Version:** 1.3.1 (narrow Geoffrey review repair R1–R3; accepted §12 v1.2 history retained)  
**Status:** Phase 2A verified. Phase 2B applications accepted; exact-input replay verified; historic scope certification remains partial. Section 12's bounded manual durable-processing core is implemented and accepted through C1–C12; limited progress-authority cutover is recorded privately. No unattended service or later phase is activated. See §11, not historical pre-execution wording, for current evidence.  
**Parent:** [Link Learning MVP](LINK_LEARNING_MVP_SPEC.md). Its Phase 1 acceptance contract is preserved. This document owns Phase 2, accepted §12 v1.2, and the new §13 backlog contract. §13 is awaiting the separate Geoffrey review required by Vernon; verified PASS releases already-authorized implementation and the capped first run. §10 remains roadmap context, overridden only where §13 explicitly says so.

## 1. What exists, and what was missing

The original MVP spec already documented the order: manual learning → existing library → fresh capture/wiki → scheduling/cleanup. The private recurring-pipeline design brief described the eventual architecture, but explicitly was not decision-complete. Neither contained a concrete Phase 2 implementation contract.

Phase 1's retained report at `<vault>/state/link-learning-pilot-v1/report.md` records A1–A8 passed: two specialists used installed changes in fresh tasks; duplicate replay created no new learning work. This proves the manual path, not an automatic learning engine. Taste approval is separate.

Read-only inspection for this plan confirmed:

- `python3 -B -m bookmark_wiki status --vault <vault>` returned `ok`, 2,070 sources/revisions, fresh index, all sources captured/context-pending. Captured does not mean integrated or learned.
- `bookmark_wiki/cli.py` exposes only `update`, `status`, `search`, `show`. `retrieval.status/search/show` and the validated archive manifest are reusable.
- `show` returns archive source ID, revision, source content/expanded links, raw/evidence paths and warnings. Search excerpts and generated semantic hints are not sufficient learning evidence.
- Existing tests use standard-library `unittest` and synthetic temporary vaults, with helpers in `tests/test_archive.py`. No new dependency is required.
- The checkout remains unborn `master`, with no HEAD and untracked existing source/docs/tests. Preserve it; no clean/reset/stage/commit or invented worktree base.
- Native tasks and retained artifacts worked in Phase 1. Completion notices arrived late; automatic coordinator continuation/restart reliability was not certified. Manual reconciliation is adequate for this pilot, not for unattended scheduling.

## 2. The next useful slice

**Outcome:** An explicitly selected, bounded set of existing archive records produces reproducible source packets. Geoffrey reconciles them with prior learning, routes only useful unknown material, and obtains verified destination receipts through the same manual workflow.

Two approval boundaries prevent “build the adapter” from silently becoming “change the profiles”:

1. **2A — Offline adapter and dry run:** implement/test packet export; inspect a small live archive batch read-only. No workers, profile edits, network or model calls in the adapter.
2. **2B — Approved learning batch:** Geoffrey presents the exact packet identities, owners, evidence gaps, profile-file allowlist and application briefs. After explicit batch execution approval, perform source reading, proposal review, guarded installation and fresh application using the Phase 1 procedure. Approval covers routine steps inside that frozen batch; do not ask repeatedly for each routine operation.

A generic instruction to implement this spec authorizes 2A only. A separate approval of the concrete 2B batch is required because its sources and skill targets cannot honestly be selected from an unread library. This gate is not a new architecture-planning round.

### Locked bounds

- Discover candidates with at most five existing `search` calls, each `--limit 10`; record queries and candidate IDs privately. This is a topical sample, not a full-backlog census or a newest-bookmark query.
- Select one to five distinct `x:<ASCII digits>` archive IDs explicitly. Empty batches and duplicates are invalid. Do not pad a sparse result set or invent a useful source.
- No more than two recipients per source, and only `finn` and `vidya` in this Phase 2 pilot. Validate their live canonical roster before 2B; no substitute profile.
- Group unknown sources into at most one reading task per owner. At most one fresh application task per owner, one reading correction and one application correction per owner; stop an exhausted gate. Each worker has a 1,800-second runtime cap. Native retries remain recorded separately.
- Per selected record: at most 131,072 bytes of canonical exported JSON; whole `packet.json` at most 1,048,576 bytes. Reject oversize with no final output, never truncate silently.
- At most five external learning targets in a 2B batch. Any required external retrieval is separately enumerated and frozen in that batch; no recursive crawl, remote execution, download/install of tools or paid media generation.
- No minimum forced adoption. Valid `already_covered`, `reference_saved`, `not_useful` and `blocked` results are useful. End-to-end adoption proof requires one genuine novel improvement; an all-known batch proves no-op handling, not new learning.

## 3. Implementation scope and unchanged behavior

### Repository allowlist for 2A

| Path | Change |
|---|---|
| `bookmark_wiki/library_intake.py` | New offline validation, frozen record export and atomic packet publication |
| `bookmark_wiki/cli.py` | Add `intake` command using the existing JSON envelope/error style |
| `tests/test_library_intake.py` | New synthetic unit/integration cases defined in §7 |
| `tests/test_cli.py` | Add intake argument/error/JSON tests; preserve existing assertions |
| `README.md` | Document the implemented command only after it exists |
| `docs/CONTRACT.md` | Document the intake schema/output/error boundary |
| `docs/BOOKMARK_INTAKE_PHASE2_SPEC.md` | Execution evidence and approved corrections only; no silent acceptance changes |

Do not change `archive.py`, `retrieval.py`, existing update/search/show semantics, dependencies, context-pilot modules, rendering projects or Hermes infrastructure. A required additional production file is a scope checkpoint, not implicit permission.

### Runtime scope

Use `<vault>/state/library-intake-pilot-v1/` for private selection, packets, mapping, batch approval, checks and reports. The adapter may write only inside this dedicated root. It reads the archive through existing retrieval functions and never calls `update`.

Protect archive `manifest.json`, `raw/`, `source-evidence/`, `derived/`, wiki pages/read view, Siftly, all historical pilot evidence and all profiles during 2A. During 2B only the batch-approved profile files may change, with preimage guards/backups. No SOUL/config/autoload/toolset/global-memory changes. No credentials, X sync, unbookmarking, schedules, commits or public publication.

A state directory contains evidence/receipts, not a second worker queue. Native Kanban on `bookmark-skills` remains task authority. A completed card alone is not accepted learning.

## 4. Offline command and packet contract

**Implemented in Phase 2A; contract preserved:**

```text
python3 -B -m bookmark_wiki intake --vault PATH --selection FILE --output-root PATH
```

Compare normalized absolute lexical paths: `--output-root` must equal `<vault>/state/library-intake-pilot-v1`. Reject `..` components and inspect the original absolute lexical paths for symlink components **before** calling `resolve`/`realpath`; do not erase aliases before validation. Apply this check to the vault, output root, selection and every subsequently opened/created child path, including the lock and staging/packet paths. After these checks, separately verify the canonical output target is the dedicated directory beneath the canonical vault. Reject symlink aliases, including system aliases: callers must supply their real paths. A root/selection/path validation rejection must cause no writes. `--selection` is a regular, non-symlink UTF-8 JSON file beneath that root, at most 16,384 bytes. Missing root may be created only as the dedicated output location; reject a conflicting file/symlink. This command has no `--execute`, `--force`, profile or network option.

Selection object has exactly `schema_version: 1` and `source_ids: [string, ...]`. Validate literal IDs before lookup; reject malformed IDs, duplicates, unknown fields and out-of-bound counts. Sort validated IDs lexicographically for packet identity; retain the operator's selection file separately. JSON duplicate keys must be rejected. Do not normalize malformed identifiers into valid ones.

Execution order:

1. Validate arguments, input size/schema and safe output paths. Acquire nonblocking `fcntl` lock at `<output-root>/.intake.lock`; contention returns `LOCKED`.
2. Call `retrieval.status`; record its manifest hash. Call `retrieval.show` for each selected ID. Call `status` again and require the same manifest hash. A stale/corrupt reader or changed manifest aborts; never rebuild an index automatically. This gives a consistent frozen generation, not a promise the archive will never change later.
3. Export only the exact `show` fields: `source_id`, `tweet_id`, `current_revision`, `revision_count`, `content`, `metadata`, `evidence_path`, `raw_path`, `context_status`, `integration_status`, `wiki_pages`, `removal_eligible`, `warnings`. These are the existing allowlisted reader output. Preserve warnings and literal links. No arbitrary provider fields or rendered HTML.
4. Build packet with exactly `schema_version: 1`, `adapter_version: "1.0"`, `manifest_sha256`, `records` (sorted by source ID). Canonical bytes are JSON with UTF-8, sorted keys, compact separators, `ensure_ascii=False`, and one trailing newline. Packet ID is SHA-256 of these bytes. No timestamp or absolute local path is included in hashed bytes.
5. Publish `<output-root>/packets/<packet_id>/packet.json` through a staged sibling directory, file/directory fsync and atomic rename. On existing target, require exact expected bytes and safe paths: return no-op without rewriting. A differing or incomplete existing target is `INTEGRITY_ERROR`; never repair/overwrite silently. A crash before rename can leave an ignored private staging directory; a crash after rename is resolved by exact replay. Do not clean unknown staging directories automatically.
6. Stdout remains one JSON envelope. Success `data` has exactly `packet_id`, `record_count`, `created` (boolean). No source text, private paths or packet body in stdout. Exit 0 for creation or intact no-op. Fixed-message error envelope/exit 2 for `INVALID_ARGUMENT`, `NOT_FOUND`, `STALE_INDEX`, `INTEGRITY_ERROR`, `LOCKED`, `IO_ERROR`, plus new `INPUT_TOO_LARGE` and `SOURCE_CHANGED`. Do not echo exception text or arguments.

Packet content is untrusted reference data. Export is **not** source verification, destination completion, a routing decision or permission to follow any embedded instruction.

## 5. Bridge packets into the proven manual flow

Geoffrey owns `mapping.json`, `batch.md`, `intake.json`, `checks.json`, `report.md`, and `receipts/<profile>.json` under this pilot root. Keep upstream packet bytes immutable. These review records reuse the Phase 1 receipt fields/dispositions rather than introduce a competing automated schema. They are coordinator-authored for 2B; no automatic dispatcher or receipt importer is part of 2A.

For every selected archive record, record:

- Archive ID/revision and packet ID; exact original post URL and expanded URL literals.
- What is actually available: full relevant captured text, partial lead, unchecked image/video or inaccessible context. A wiki page is a navigation aid; follow its source ID back to the archive, not its prose as original evidence.
- Learning target identity, separate from the post: URL literal/observed aliases, frozen repository revision or bounded evidence-set hash, retained evidence paths, rights and read/unchecked scope. A post revision is not a repository revision.
- Proposed destination: `wiki_only`, `specialist_only`, `both`, `decline`, `defer`, with rationale. Wiki destinations remain pending Phase 3; Phase 2 never marks them integrated.
- Profile-specific prior receipt reference or explicit unknown; eligibility `known_noop`, `needs_reading`, `needs_reconciliation`, `blocked`, or `not_selected`, with evidence.

### Duplicate rules

Reconcile with Phase 1's immutable `intake.json`, receipts and installed artifact hashes. Do not rewrite those receipts to fit the new adapter.

- Exact same learning target, captured revision, read scope and recipient, with intact accepted evidence and applicable installed/reference hashes: reuse the prior receipt; no new specialist task or skill write.
- Same URL in another tweet is only an alias candidate. It does not establish the same revision, scope or claims. Record the alias evidence; if equivalence is unknown, retain `needs_reconciliation` rather than claiming already learned or silently fetching latest.
- Same source for a different profile is a separate decision. A new adapter version or changed archive generation does not by itself justify rereading unchanged learning evidence.
- Rejected/no-fit receipts are retained. Reconsider only with an explicit relevant source/role/capability change, not merely because the link recurs.
- Missing evidence or target drift stops reuse; do not overwrite current skills or redispatch automatically.

For fresh readings, retain the Phase 1 contract: specialist compares current coverage → draft only → Geoffrey inspects actual diff and source → install only approved targets with preimages → fresh task loads skill → inspect actual output → accept receipt. Use `adopted` as the final receipt vocabulary, not a mixture of `adopt` and `adopted`. Record negative dispositions with concrete reasons; no skill-sprawl incentive.

### 2B batch authorization packet

Before dispatch, `batch.md` names exact selected IDs/revisions, target evidence, recipient assignments, previous-receipt matches, skill create/modify paths and preimage hashes, rights/access limitations, exact fresh-application brief and pass criteria per owner, maximum tasks and verification methods. Preserve the user's execution approval reference. If targets cannot be safely named yet, permit a draft-only batch; a later installation approval must name exact paths before writes. Do not pretend a draft-only run passes adoption.

## 6. Interruption, feedback and finality

Record native task IDs before proceeding and read back assignments/state. On uncertain create, inspect the exact board and batch identity before any further mutation. No replacement task, automatic retry, custom callback or poller. On resume, inspect packet/mapping/receipts, exact cards and retained attachments. Reconcile completed work even if the notification is delayed. Duplicate/stale notifications never create new work.

Source/rights/prerequisite blockers stop the affected decision; preserve independent accepted work. One bounded correction delivery per failed review/application gate, then flag the unmet property and stop. Historical Phase 1 budgets are not reset.

Record archive/wiki protected-file hashes and inventory before and after the actual pilot. A source can be locally closed for the selected specialist destination without being fully processed for all destinations. `blocked`/`defer` is unresolved. An explicit accepted decline is a resolved destination decision, not a claim of learning. Never translate archive `removal_eligible` into deletion authority.

## 7. Test-first acceptance matrix

Write these tests before production changes; record the meaningful RED failures in private `checks.json`, then implement. Use `test_archive` helpers with synthetic IDs/content only; no live archive rows or private receipts in repository fixtures.

| Gate / test name | Given → when → required assertion |
|---|---|
| B1 `test_intake_exports_selected_revision` | Valid synthetic vault and two IDs → intake → exact sorted allowlisted `show` values, warnings, manifest hash and recomputed packet digest; unrelated rows absent |
| B2 `test_selection_rejects_invalid_bounds` | Empty/six IDs, duplicate or malformed IDs, extra/duplicate keys, oversized selection/record/packet → reject with fixed code, no published packet; accept exactly one/five and byte ceilings |
| B3 `test_intake_fails_closed_on_reader_change` | Unknown ID, stale index, corrupt manifest, or manifest generation changing between reads → corresponding error, no packet and no archive repair |
| B4 `test_intake_replay_and_conflict` | Same generation/IDs in different order → same packet ID, created=false, unchanged bytes/mtime; corrupted existing packet → integrity error, no overwrite |
| B5 `test_output_boundary_and_crash_publication` | Symlink/traversal/wrong root, competing lock, or injected pre/post-rename interruption → fail safely; no outside writes; replay recovers published complete packet, ignores uncommitted staging. Exercise the real CLI with an output-root symlink resolving to the correct directory, a symlinked selection and child lock/packets paths; reject before writes and prove target bytes/inventory unchanged. Real canonical paths must pass |
| B6 `test_intake_is_offline_and_read_only` | Synthetic vault + instrumented network/provider/subprocess failures → intake needs none; protected archive hashes/inventory unchanged; output contains only allowlisted fields |
| B7 `test_intake_cli_json_contract` | Valid and malformed CLI inputs via real subprocess → exact envelope and exit codes; no source text/absolute path/exception leakage; old commands unchanged |
| B8 **Live adapter gate** | One to five actual selected records → command → inspect private packet against original source records and verify protected hashes/status; no specialist tasks during 2A |
| B9 **Real reuse gate** | Live packet mapping + frozen Phase 1 receipts → coordinator reconciliation → known matches reuse exact receipts; re-submit approved batch without new specialist work or writes. If the real archive has no Phase 1 match, report that limitation and exercise cross-post equivalence only synthetically; do not claim live match proof |
| B10 **Real learning gate** | Approved 2B novel source → actual named specialist → retained grounded proposal, approved guarded installation, fresh application and coordinator-accepted receipt. At least one genuine adoption; if none found, stop with adapter-only success |
| B11 **Resume and scope gate** | Resume once after export and once after an actual native task completes, including duplicate notification handling → same task identities, no duplicate dispatch/write, complete retained evidence and unchanged protected roots |

B1–B7 are automated tests; B8–B11 require direct operator/native evidence. A mocked transport or synthetic receipt cannot satisfy B10/B11. No automatic wake/restart claim follows from manually resumed 2B.

## 8. Ordered work and verification commands

1. Verify spec/version and 2A approval; snapshot current untracked checkout and protected archive inventory without committing. Inspect existing tests. Keep unrelated work intact.
2. Write B1–B7 tests first. Run focused tests and retain RED names/causes. Implement only allowlisted adapter/CLI files; then GREEN and regression suite.
3. Select the bounded live batch through existing `status/search/show`. Run B8; retain selection, packet and exact CLI results. Report adapter acceptance separately from learning.
4. Prepare mapping and concrete 2B batch. Request that single scoped approval; do not start workers or installations before it.
5. Run the proven manual learning/reuse workflow, using native retained artifacts; check B9–B11. Report partial outcomes honestly instead of searching indefinitely for novelty.
6. One bounded coordinator review of implementation/evidence and one correction pass; checkpoint after two failed deliveries of the same property. Record remaining gates and stop. No phase expansion to repair infrastructure.

Existing verification command:

```sh
python3 -B -m unittest discover -s tests -v
```

Focused command after new tests exist:

```sh
python3 -B -m unittest discover -s tests -p 'test_library_intake.py' -v
```

Also run `git diff --check`, but because existing files are untracked, compare actual filesystem hashes against the preflight inventory and check changed Markdown for whitespace/links. A clean empty Git diff is not a complete change audit.

Completion report: selected/omitted counts, exact source-to-target mappings, prior-receipt no-ops, task IDs/states, profile changes and useful differences, direct application evidence, B1–B11 results, retries/usage (cost unmeasured unless known), protected-state result, unresolved wiki destinations and next gate. Do not state Phase 2 complete when only 2A passed.

## 9. Stop conditions and explicit non-goals

Stop on missing approval, malformed identity, essential missing evidence/rights, source-generation drift, changed profile preimage, unknown owner, missing durable output, exhausted correction allowance or forbidden-file pressure. Preserve partial artifacts and report the smallest decision needed.

No new queue/database, automatic task launcher, general routing service, Jev provisioning, wiki rebuild, X capture, source freshness monitor, schedule, bookmark deletion, model fine-tuning, Blender/tool installation, trading execution, GPU patching or public release in Phase 2. Ari/Adam/Lamar and other profiles are later roster expansion, not implied permission to trade, publish ads or modify hardware.

## 10. Roadmap from manual pilot to the full pipeline

### Shared end-state flow

```text
Manual links / existing archive / new bookmark capture / bounded discovery
  → immutable source capture + revision identity
  → deduplicate and prepare grounded evidence
  → Geoffrey's destination/owner policy
      → wiki proposal → reviewed local publish → wiki receipt
      → specialist comparison → reviewed skill/reference → fresh use → profile receipt
      → explicit decline or unresolved defer
  → reconcile every required destination → local inbox completion + digest
  → separately approved X unbookmark operation + exact membership verification
```

One source may feed both destinations. Wiki integration and profile learning are independent receipts; neither completes the other. Manual links and future sources enter the same contracts. Archive material is retained even when the pending inbox or X membership changes.

| Stage | Build next | Exit evidence / approval boundary |
|---|---|---|
| **1 — Complete** | Manual learning path | Existing A1–A8 report; not an autonomous service |
| **2A** | Offline library packet adapter | B1–B8; code plus actual read-only packet, no profile writes |
| **2B** | Small approved batch through existing learning path | B9–B11; accepted new learning, known-source no-op, manual resume and preserved archive |
| **3A — Durable processing core** | Replace manual batch bookkeeping with derived SQLite stage/destination records; configure role-interest map; guarded native handoff/reconciliation | Frozen exact schema, command/API contracts, migrations/import of prior receipts, budgets and tests before implementation; crash/uncertain-create/no-op canaries; no parallel task authority |
| **3B — Fresh capture adapter** | Verify existing X/Siftly capture/export, authenticate through approved flow, preserve each wave, import via existing `update` | Real capped capture with pagination/deltas and duplicate replay; staging safety, auth/quota failures pause; never assume old capture script is schedulable |
| **3C — Grounded wiki lane** | Bounded prepare → deterministic validation → overseer proposal → transactional local publish → digest | Qualified model on fixed representative cases; source citations/coverage, human-edit preimages, recovery from interrupted publish and no-op replay; no unrelated pages overwritten |
| **3D — Unified routing and profile expansion** | Wiki-only/specialist-only/both/decline/defer; current roster/interest map; general manual-link adapter; reviewed cross-profile receipts | Relevant owners only, tested no-fit/ambiguous/high-risk routing, separate destination success/failure; no false inbox completion. Extend Finn/Vidya to explicitly selected profiles |
| **4A — Scheduled operation** | Thin scheduled invoker of the verified CLI, bounded batches, pause/status/resume and digest delivery | Manual end-to-end plus restart/duplicate/notification canaries; explicit host, frequency, budgets and destination approval. No reliance on an open chat; sleep/offline pauses honestly |
| **4B — Bounded discovery** | Separate small lane for Geoffrey-found sources | Opt-in interests and call/result ceilings; lower priority than user bookmarks; same rights/provenance/dedup; no unlimited crawl |
| **4C — Cleanup** | Local pending-inbox closure first; separately approved X unbookmarking | Immutable capture and all required accepted destination receipts/declines; reviewed exact candidate list, dry run, exclusion of blocked/deferred work, small mutation cap, exact readback and durable audit |

### Architecture decisions carried forward

- **Reuse, not rebuild:** archive manifest remains source authority, canonical wiki Markdown remains page authority, native Kanban remains specialist-task authority. The Phase 3 SQLite ledger owns stage/destination progress and references native task IDs; it must not impersonate a second worker scheduler.
- **One migration, not parallel queues:** preserve Phase 1/2 JSON receipts verbatim; Phase 3 imports references plus verified hashes into its ledger and proves replay before becoming the progress authority. Retire manual writes only at that explicit transition.
- **Model roles:** cheap/local preparation is a candidate, not a guaranteed competent reader. Separately configure prepare and integrate identities/capabilities; qualify on citations, omissions, contradictions, injection and media gaps. No silent provider or paid fallback. Text cannot stand in for visual/motion evidence.
- **Routing:** Geoffrey owns assignment policy. Live canonical profile IDs and explicit role interests, no-fit option and ambiguity review are required. Jev may later recommend routing after separate qualification; no fabricated scores or prerequisite dependency now.
- **Safety:** sources are untrusted. Financial material becomes research/backtest guidance, not trades; driver articles become reviewable technical references, not patch execution. Source novelty never overrides authorization or rights.
- **Feedback:** specialists can reject, flag existing coverage or request missing evidence. Coordinator accepts useful artifacts, not confident prose. Corrections remain bounded; systemic auth/quota/integrity failures pause their stage.
- **Restart:** atomic stage claims, expiring leases, retained outputs and one wiki publisher; reconcile native task creation uncertainty before retry. Exactly-once inference/delivery is not promised. Test the actual crash boundaries.
- **Visibility:** report captured, queued, running, awaiting review, accepted, declined, blocked and deferred separately. Digest accepted changes plus exceptions and pending counts; never count attempts or citations as learning.
- **Cleanup semantics:** local processed status, immutable archive and live X membership are separate. Keep an audit of removed bookmark IDs/URLs. Removal is not archive deletion, and recovery/rebookmarking is not promised until its API behavior is verified.

Before Phase 3 implementation, turn its next row into one decision-complete spec using the existing private recurring-pipeline brief's preservation/quality requirements. Before Phase 4, separately approve actual scheduling and cleanup policy. This roadmap supplies the path without pretending uninspected auth, provider or scheduler choices are settled.

## 11. Documentation / execution log

- v1.0 — Created after Phase 1 completion. Inspected current CLI, retrieval contract, test helpers, receipts/report and live archive status. Defined bounded 2A/2B gates and the full rollout path. No implementation or new specialist work performed.
- Execution authority: Vernon requested review followed by implementation, then explicitly authorized direct Geoffrey completion after incomplete delegated deliveries. Phase 2B's concrete-batch gate remains unchanged.
- Reviewed planning SHA-256: `1863b6297fd23d590da6715c10c335c30493b53b23ff9e9777696a091e9a0066`; subsequent edits here record status/evidence only.
- B1–B7: PASS after direct completion, independent full-suite rerun and fresh read-only review. Final reviewer withdrew B5's static symlink finding after inspecting the initial validation calls and an explicit CLI regression. No concurrent hostile-filesystem sandbox claim.
- Integrated verification: `python3 -B -m unittest discover -s tests -v` — 90 tests passed, exit 0; focused intake coverage contains 20 tests. Existing baseline was 70. New coverage-only tests were recorded honestly; direct RED also exposed safe-relative-path rejection, corrected before GREEN. Packet-ceiling seam is synthetic because valid capped records cannot reach the whole-packet ceiling.
- B8: PASS on three actual archived records selected by one bounded `motion` search. Exported records exactly match `show`; digest verifies; replay returns `created=false` with unchanged bytes/mtime. All 3,754 files in the bounded protected vault inventory retained hashes; `_verification` and the new intake output root were excluded. Archive status/manifest unchanged.
- Implementation integrated with preimage guards into the existing checkout; no commits or publication. Private evidence and logs: `<vault>/state/library-intake-pilot-v1/`.
- Historical 2A closeout: B9–B11 were NOT RUN at that point. Superseded by the following 2B evidence, not erased.
- 2B execution: two archived-text readings produced reviewed additions to existing Finn/Vidya skills; four guarded files installed; both fresh applications independently accepted, Finn after one wording correction. Exact private task identities, source revisions, target hashes, native attachments and review notes remain in the pilot root. Source media, live freshness and external model efficacy were not verified.
- B9: exact frozen 2B packet replay returns `created=false`; both accepted profile receipts reconcile to `known_noop`, with no new specialist calls/tasks/profile writes. Disposable missing-evidence and wrong-hash receipt variants fail closed. This does not establish a real Phase 1 cross-post match; neither selected source has an exact Phase 1 URL match.
- B10: PASS for both bounded applications. New `intake.json`, `receipts/finn.json`, `receipts/vidya.json`, and `evidence/closeout-checks.json` retain the chain. Original Phase 1 receipts remain unchanged; their now-historical parent-skill hashes must not be silently rewritten or treated as current.
- B11: PARTIAL. Native completion/resume reconciliation retained existing task identities; no duplicate worker was created. The closeout replay preserved all 3,754 files in its protected vault inventory and all four installed hashes. This fresh comparison does not retroactively prove the entire earlier 2B interval. Finn disclosed an out-of-scratch temporary extraction; the deviation is not erased by successful artifact review. Scoped recovery did not locate the historical per-file protected baseline; original aggregate B8 assertions remain, but full Phase 2 scope certification is not claimed.
- Formalization authority: Vernon said, “Continue. I'm going afk for a bit so work freely toward our goal of spec completion.” This authorizes pilot evidence closure and specification completion/review, not implementation of §12, a new reading batch, schedules, source deletion or broader profile edits. One initial independent specification review plus one correction/re-review is the current envelope; unresolved blockers stop readiness.
- v1.2 amendment authority: after approving §12 implementation at reviewed v1.1 SHA-256 `f8039cb739427bbf4d325667d766eb8c988bf295787c8a3ae036bef405b074d4`, Vernon approved the narrow disposition-persistence amendment and continuation of the current bounded implementation. Independent GPT-6 Luna defect review confirmed that a FileRef alone cannot preserve a decision after receipt loss/drift. Persist a validated disposition in the existing acceptance event; no new tables or phase authority. Original reviewed bytes and defect reproduction remain in private evidence. Existing acceptance gates, attempt limits and historical B11 PARTIAL status are unchanged.
- §12 implementation acceptance: independent actual `gpt-6-luna` review PASS at amended specification SHA-256 `ebb2a18e9abe567aa68bfe0fa2b7b1f5e1b56095c22cedd172726db56f7e4544`; coordinator integrated only the seven allowlisted files with preimage guards, then reran 33 focused and 124 full tests, both exit 0, no warnings. AST syntax, changed documentation checks and git diff --check passed; filesystem inventory establishes scope for the unborn/untracked checkout. C1–C10 accepted against actual assertions and read-only independent review, not count alone.
- C11/C12 PASS for existing Phase2 historical import and manual resume: eight events, five original task bindings, two intact known_noop destinations; every input replayed twice in fresh CLI processes without new rows, worker calls, profile writes or ledger-byte/mtime changes. All four installed hashes and three preimages verified; repeated actual native readbacks preserve the original tasks/attachments. All 3,901 files in the declared protected vault inventory and both allowed skill subtrees are unchanged. Excludes the dedicated new runtime and predeclared verification root. Private acceptance-and-cutover report under `<vault>/state/learning-process-v1/reports/` explicitly makes the ledger progress authority for newly registered packet-backed batches; immutable historical receipts/native Kanban retain their own authority. Phase1 general-link history remains manual/external. Historical B11 stays PARTIAL; no retroactive certification. No new batch, schedule, capture, wiki publication, profile changes, deletion or commit. This documentation-only closeout follows the pinned review and is not attributed to its earlier bytes.

## 12. Phase 3A — bounded durable processing contract

### 12.1 Outcome, authority and exact boundary

**Outcome:** persist the manual learning process so the coordinator can answer what is known, what is pending and why; replay accepted input without duplicate work; resume from native task evidence rather than chat memory. This is an offline bookkeeping core with guarded manual native handoffs, not an unattended service.

This section is the sole next-slice execution contract under spec ID `BOOKMARK-INTAKE-PHASE2`, version 1.2. Vernon approved implementation at the reviewed v1.1 hash and subsequently approved only the disposition-persistence amendment recorded in §11. A reviewer PASS establishes readiness, not implementation approval; acceptance of the implementation remains separate. Existing Phase 2 gates remain unchanged. B11's unresolved historical deviation must be explicitly dispositioned by Vernon before a new live specialist batch; it does not prohibit writing/reviewing this specification or later-authorized offline tests.

The accepted base path is:

`existing frozen packet → bounded approved mapping → durable batch record → saved dispatch intent → coordinator/native task → retained proposal → guarded manual installation → fresh application → verified receipt → local specialist closure`

Archive, wiki and live bookmark membership are untouched. Selected wiki destinations can be recorded as deferred, but cannot be completed in 3A. No new selection is silently authorized: the live acceptance first imports the existing two-profile pilot and replays it. A genuinely new batch needs its exact source/profile/path approval as in §5, even after offline implementation approval.

**Owner boundaries:**
- Archive manifest/raw evidence owns source history; wiki Markdown owns published knowledge.
- Native `bookmark-skills` board owns task execution, retries and terminal state.
- The new local database owns only coordinator progress and references after explicit cutover. It is never polled as a second job queue.
- Geoffrey owns routing, semantic review, accepted receipts, and permission checks. Source text and worker self-reports cannot accept themselves.
- Historical JSON receipts stay immutable and readable. The database imports references and hashes, not rewritten versions of those receipts.

### 12.2 Implementation allowlist and private locations

Repository files permitted in the subsequent implementation:

| Path | Contract |
|---|---|
| `bookmark_wiki/learning.py` | New stdlib-only validators, deterministic identities, ledger transactions and read-only verification |
| `bookmark_wiki/cli.py` | `learning` command family and fixed-message errors; existing commands unchanged |
| `tests/test_learning.py` | Contract cases C1–C12 below, synthetic fixtures generated by tests |
| `tests/test_cli.py` | Public-entry JSON/error regression tests; no weakened existing assertions |
| `README.md` | Only implemented commands; distinguish manual coordination from automation |
| `docs/CONTRACT.md` | Exact implemented schema, state and migration contract |
| `docs/BOOKMARK_INTAKE_PHASE2_SPEC.md` | Approved corrections and execution evidence only |

Forbidden: dependencies, archive/retrieval/intake/context modules, existing fixture data, Hermes infrastructure, profile config/SOUL/autoload/global memory, source-capture clients, wiki content and all unrelated projects. No new service, GUI, scheduler, model router, credentials, commit/push or publication. Inspection uses the current unborn/untracked checkout baseline; hash-snapshot allowed files and preserve unrelated work rather than invent a Git base.

Private runtime root is exactly `<vault>/state/learning-process-v1/`. Allowed children are `ledger.sqlite3`, its SQLite rollback journal, `.learning.lock`, `inputs/`, `evidence/`, `reports/`. Coordinator-authored input files go in `inputs/`; CLI writes only database/journal/lock. No dynamic output-root option. Test roots are disposable equivalents under the configured scratch directory. No writes to prior pilot directories during implementation or acceptance. Source inputs reference existing pilot files read-only.

### 12.3 Locked limits and identities

- Initial roster is exactly `finn`, `vidya`; no inferred profiles or aliases. Each batch freezes one to five archive source IDs, at most five learning targets and at most two recipients per source. No automatic search, ranking or model call in this core.
- Each target is an explicitly identified URL literal plus captured revision plus read scope. Preserve literals; syntactic URL validation allows only `https` with a hostname, no userinfo. Do not fetch, normalize aliases or strip query parameters. Revision is a nonempty string bounded to 128 characters; it is not presumed a Git SHA.
- `target_id = sha256(canonical JSON of {identity_url, revision, scope_read})`. Scope is a nonempty ordered list of distinct nonempty strings, each at most 256 characters. Preserve order; semantic scope equivalence is coordinator judgment, not string normalization. Adapter/spec version and archive generation are not part of target identity.
- `destination_id = sha256(canonical JSON of {target_id, destination, profile})`, with destination `specialist`/`wiki`; specialist profile is `finn`/`vidya`, wiki profile is null. Different profiles and different captured scopes never collapse.
- Source links preserve `(packet_id, source_id, archive_revision, target_id)` as provenance. One target may have multiple links; another tweet alone does not prove equivalence. Alias claims require explicit evidence; unknown equivalence remains deferred and is not auto-merged.
- Canonical JSON is UTF-8, sorted object keys, compact separators, `ensure_ascii=False`, one trailing newline; SHA-256 uses those bytes. Reject duplicate JSON keys, unknown fields and nonfinite numbers. Strict integers exclude booleans.
- Input file ceiling: 1,048,576 bytes inclusive. Array and string bounds below are inclusive. Reject before DB mutation, never truncate. All hash fields are exactly 64 lowercase ASCII hex characters. Task IDs match `t_[0-9a-f]{8}` for this verified native deployment; a future format change is a contract change, not normalization.
- Metadata times are UTC `YYYY-MM-DDTHH:MM:SS[.fraction]Z`, parsed as instants. They describe observed/recorded time, not ordering authority. No cooldown, lease expiry, automatic freshness or timeout decision in 3A. SQLite event sequence establishes transition order. The public CLI captures `recorded_at` from the UTC clock after validation, inside the transaction.
- Core cost is zero inference calls. Manual batch envelope retains one reading and one application per owner, at most one correction for each, 1,800 seconds per native task, no automatic fallback. Record actual attempts; cost is null/unmeasured unless actual usage exists. No paid-media allowance.

### 12.4 Public command surface (implemented; manually coordinated)

```text
python3 -B -m bookmark_wiki learning init --vault PATH
python3 -B -m bookmark_wiki learning apply --vault PATH --input FILE
python3 -B -m bookmark_wiki learning status --vault PATH
python3 -B -m bookmark_wiki learning check --vault PATH --batch-id SHA256
```

No `--execute`, `--force`, network, task-create or profile-write flags. All output uses the existing single JSON envelope with `command="learning"`. Success data fields:
- init: `{action:"init", schema_version:1, created:boolean}`.
- apply: `{action:"apply", event_id:SHA256, applied:boolean}`.
- status: `{action:"status", schema_version:1, batches:int, destinations:[{destination_id, target_id, destination, profile, recorded_state, observed_task_status, task_observed_at}]}` sorted by destination ID. No source text or paths.
- check: `{action:"check", batch_id, results:[{destination_id, recorded_state, effective_state, reason_code}], locally_complete:boolean}` sorted by destination ID. Effective state comes from revalidating current file hashes; this command does not update recorded state or acceptance.

`observed_task_status`/time are null when no task observation exists, and otherwise explicitly historical, never claimed live. Status returns at most 100 destinations; on larger ledgers return `INPUT_TOO_LARGE` with no partial list. Pagination is deferred until this actual volume limit is reached; check remains bounded to a batch.

Exit 0 for valid results including unresolved/deferred `check`; exit 2 for errors. Envelope is exactly `{schema_version:1,status:"ok",command:"learning",data:...}` on success and `{schema_version:1,status:"error",command:"learning",error:{code,message}}` on errors after a literal first token `learning`, including malformed subcommands/arguments. Other unknown top-level commands retain the existing `command="unknown"` behavior. Extend cli.py's known-command set and fixed-message map; the new codes must not fall through to its generic message. Fixed-message codes: existing `INVALID_ARGUMENT`, `INPUT_TOO_LARGE`, `NOT_FOUND`, `INTEGRITY_ERROR`, `LOCKED`, `IO_ERROR`, plus `UNSUPPORTED_SCHEMA` (“Unsupported learning schema.”), `INVALID_TRANSITION` (“Learning transition is not permitted.”). `NOT_INITIALIZED` when DB absent outside init. Never print source text, private paths, exception details or task body.

Path validation follows §4's lexical-before-resolve rule for vault/root/input and all accessed children; reject traversal, symlink aliases, nonregular inputs and out-of-root input files. File references use absolute canonical paths under exactly these read roots: the two historical pilot directories, the new runtime `evidence/`, the native `bookmark-skills/attachments/`, and the two explicitly allowed skill subtrees (Finn visual-style-research, Vidya product-video-workflow). The deployment's Hermes home/profile root is provided through an operator-authored roots file `inputs/roots.json` with exactly `{schema_version:1, hermes_home:absolute_path}`; no environment scanning. Derive the native/profile read paths from it, require it for init and pin its SHA-256 in the DB. Changed roots require reconciliation, not silent adoption. No arbitrary file read, no directory recursion outside batch inventory. Static path safety is not a concurrent-hostile-filesystem sandbox.

### 12.5 Input event contract and evidence types

Every apply input is exactly `{schema_version:1, kind, payload}`. `event_id` is SHA-256 of the canonical full object. Reapplying exact bytes/semantic canonical JSON is a no-op; timestamps are not input-generated to force duplicate events. Accepted kinds are `register_batch`, `dispatch_intent`, `task_observed`, `receipt_accepted`. Unknown kinds rejected.

Common types:
- **FileRef**: exactly `{path, sha256}`; regular non-symlink allowed-root file whose bytes match. Max 100 refs per event, each file at most 64 MiB. Installed skill/ref files at most 1 MiB. Do not copy or execute referenced files.
- **Authority**: exactly `{record:FileRef, scope}` where scope is `historical_import` or `approved_batch`. The referenced private Markdown names exact scope, owner authorization, target paths and verification boundary. Core verifies bytes, not human consent authenticity. Geoffrey must inspect actual user authority before authoring it; a worker cannot supply its own authority.
- **NativeObservation**: exactly `{task_id, board, assignee, status, observed_at, body_sha256, evidence:FileRef}`. Board must be `bookmark-skills`; assignee must match recipient; status one of `ready,running,blocked,review,done,triage,todo,scheduled,archived`. Evidence is a retained normalized coordinator readback from actual `kanban_show` plus attachment readback. The referenced JSON has exactly the same scalar fields (excluding evidence) and `attachments:[FileRef,...]`; body digest is calculated from exact native body. Core checks self-consistency and attachment hashes. This is trusted coordinator observation, not cryptographic/native authenticity or live API access. A mock cannot satisfy the live gate.

`register_batch.payload` exactly:
- `authority:Authority`.
- `packet:FileRef` to an existing intake packet; verify canonical content hash equals directory packet ID and records match selected links. Do not require current live archive generation for already captured historical learning.
- `targets`: 1–5 objects, exactly `{identity_url, revision, scope_read, scope_unchecked, evidence:[FileRef,...]}`. Unchecked is a list of distinct nonempty strings, max 20; read max 20. Evidence nonempty. Require each evidence ref to be the packet or an independently retained allowed-root capture.
- `links`: 1–10 objects, exactly `{source_id, archive_revision, target_id}`. Validate source IDs literally and find matching packet records. Target must exist in this payload. Each target has a link, each selected source has a target, no duplicate links. This core supports existing archive inputs only, not general standalone manual URLs.
- `destinations`: 1–10 objects exactly `{target_id, destination, profile, reason}`; reason nonempty ≤2,000 characters. Require at least one destination per target, reject duplicates. Reason records Geoffrey's routing, not a model probability.

`batch_id` is the register event's event ID. Native tasks can group several specialist destinations for the same profile within this batch; never span batches or profiles. Identical destinations can recur in another batch with different packet provenance; accepted intact receipts are reused rather than new dispatch.

`dispatch_intent.payload` exactly `{batch_id, destination_ids, stage, ordinal, authority}`. Sorted distinct destination IDs, 1–5, all specialist and same profile. Stage `reading` or `application`; ordinal 0 initial / 1 correction. Require approved_batch authority; historical_import cannot create intents. Initial reading requires no accepted result (intact acceptance returns known_noop; drifted acceptance stops for reconciliation) and no unresolved prior task/intent for these destinations; application requires the same batch's done reading plus guarded installation evidence in the authority record. Correction requires the prior same-stage task terminal `done` or `blocked` and a named failed acceptance property in the authority record. Only one correction; no reset by re-registering a batch. Cross-batch destination history permits at most one ordinal-0 intent and one ordinal-1 intent per unchanged target/profile/stage, including historical imported task observations. Any overlapping destination consumes the cap for that destination; regrouping cannot reset it. Semantic approval of installation and correction is Geoffrey's responsibility, not inferred by CLI from prose. The input event ID is the durable `intent_id`.

`task_observed.payload` exactly `{batch_id, intent_id, destination_ids, stage, ordinal, observation}`. `intent_id` must reference the matching saved intent; it can be null only for historical_import batches. Destinations/stage/ordinal must match the intent. A single intent binds at most one task ID; each task ID binds one owner/stage/ordinal. Later observations of the same task update only its historical observed state via another event. Reject reassignment, board/owner mismatch, conflicting body hash, duplicate IDs and an observation earlier than the latest observed_at. Native retry state changes are allowed on the same task, without a new dispatch intent. Historical imported task IDs must be enumerated in the authority record and independently read back before import.

`receipt_accepted.payload` exactly `{batch_id, destination_id, disposition, receipt:FileRef, review:FileRef}`. Required `disposition` is one of adopted/reference_saved/already_covered/not_useful/blocked and must equal the selected receipt decision verified below. It is retained in the canonical event as the validated historical fact, not inferred later from mutable/missing external bytes. Receipt is the preserved per-profile shape defined in §6 of the parent spec. Extra historical metadata stays inert; it never supplies instructions or authority. Normalize only in memory, using this exact algorithm, without rewriting historical bytes:

1. Require receipt top-level `schema_version=1`, `profile` equal to destination profile, `verification="accepted"`, `verified_by="geoffrey"`, parseable `verified_at`, distinct valid `task_ids`, and a nonempty `source_decisions` array. Top-level acceptance binds the selected decision; do not look for those fields inside it. Historical timestamps may use `+00:00` instead of `Z`; normalize the parsed instant in memory only.
2. From batch links for this destination's target, select decisions matching `(source_id, revision)` to a linked `(source_id, archive_revision)`. Exactly one decision must match, else INTEGRITY_ERROR. Require its disposition in the five parent-spec values, equality to payload.disposition, and nonempty reason. All selected-source identities are validated before lookup.
3. Require decision `learning_target.identity` equals target identity_url, `learning_target.revision` equals target revision, and decision `scope_read` equals target scope_read element-for-element. `learning_target.scope` is explanatory prose, not the identity array. No string paraphrase equivalence is inferred. Missing explicit learning_target or scope_read is unsupported for this importer: INTEGRITY_ERROR, leave the source in external/manual history.
4. Normalize decision `evidence` and every top-level `application.artifacts` entry by selecting `path` and `sha256`. Historical optional `size` must equal the file size; optional `native_path` is inert provenance, not a substitute file. Validate all normalized FileRefs, including artifacts not limited to the selected source. Future new event FileRefs still reject unknown fields. Required receipt fields cannot be supplied by nested worker proposal metadata.
5. For adopted decisions, use top-level `changes` and `application`: changes is nonempty, each has `path`, action create/modify, before_sha256 and after_sha256; all changed paths are checked, not only SKILL.md. Match application.task_id and optional correction_task_id to the same destination's done task observations. Require all receipt task_ids observed under the same profile, with the reading/application/correction stages intact. For non-adopted decisions, changes may be empty and application.status may be not_run; their disposition-specific coverage checks below replace the fresh-application requirement.

The event's review FileRef points to a coordinator-authored JSON ReviewRecord, not arbitrary Markdown parsed for fields. It has exactly `{schema_version:1, receipt_sha256, destination_id, source_id, verdict, reason, preimages, coverage, evidence}`. Verdict is `accepted`; reason is nonempty ≤2,000 characters. `preimages` is 0–10 objects exactly `{installed_path, file:FileRef}`, unique by installed_path; every nonnull before_sha256 must match its corresponding preimage bytes. `coverage` is 0–10 objects exactly `{file:FileRef, section, trigger}`, with nonempty section/trigger ≤2,000 characters; reference_saved/already_covered require at least one. `evidence` is 1–20 FileRefs to actual coordinator review/authority records and any errata. Match receipt_sha256, destination_id and selected source_id exactly. Core verifies structure, linkage and bytes; Geoffrey owns the semantic review and consent authenticity. This permits a structured new review of immutable historical prose without changing the original receipt.

Adopted requires `application.status=passed`, bound application task `done` (plus done correction when named), nonempty artifacts and matching installed `after_sha256` hashes; changes must be under the recipient's allowed subtree. `before_sha256` is null only for creates; nonnull values must match retained preimage evidence named in review. Match receipt task IDs against observations and recipient, not merely string format. Reference_saved requires current matching reference FileRef and trigger/reason in review; already_covered requires current coverage FileRef plus exact section and reason; not_useful requires concrete profile-relative reason. These three do not require an application render. Resolved dispositions require a done reading task for approved_batch; blocked may instead reference its actual blocked/triage reading task. Historical imports can accept independently inspected prior reference/decline evidence with its original task linkage. Blocked is recorded as an accepted account of a blocker, never learning/closure. Wiki receipts are rejected in 3A. `defer` belongs to destination routing state, not a fabricated Phase1 receipt disposition.

### 12.6 States, conflicts and computed closure

Recorded destination states are derived from events, not freely writable. Accepted receipt dispositions come from the validated disposition persisted in receipt_accepted, never a later reread of external receipt bytes. Missing/changed evidence can degrade effective state but cannot rewrite the historical recorded decision:
- `pending`: registered specialist destination with no prior evidence/intent.
- `deferred`: wiki destination in 3A.
- `dispatch_uncertain`: persisted intent without verified binding. This includes intent saved before any create attempt; safety favors manual reconciliation over guessing.
- `awaiting_result`: bound task whose latest observed state is nonterminal; display actual historical task state separately.
- `awaiting_review`: bound task observed done without accepted applicable receipt.
- `blocked`: native observation blocked/triage or an accepted blocked disposition.
- `accepted`: receipt disposition adopted/reference_saved/already_covered.
- `declined`: accepted not_useful receipt.

Precedence: current accepted/declined receipt > latest task/intent observation > registration. A later native retry cannot invalidate an independently accepted historical result; `check` still validates its current evidence. Duplicate or stale notifications cause readback/reconciliation, never create.

Effective state in `check` is `known_noop` for accepted/declined results with intact required evidence and installed/coverage refs; `needs_reconciliation` for missing/changed evidence or identity mismatch; otherwise it equals recorded state. Reasons are exactly `INTACT_RECEIPT`, `MISSING_EVIDENCE`, `HASH_MISMATCH`, `IDENTITY_MISMATCH`, `NO_ACCEPTED_RECEIPT`, `WIKI_DEFERRED`. Check verifies all applicable FileRefs; it must not trust a previously cached success. No writes, inferred novelty or redispatch follows a failed check.

Local completion is true only if every required destination in the batch is effectively known_noop. A deferred wiki lane or blocked specialist keeps the batch incomplete. A decline completes a decision, not learning. The archive's removal_eligible flag is neither input authority nor an output of this core.

**Skill drift:** historical acceptance remains true at its historical revision, but reuse stops when current hashes differ. Phase2 already changed some Phase1 skill paths, so some Phase1 imports are expected to need reconciliation. Do not overwrite their receipts or roll skills back. A reviewed succession of skill versions requires an explicit future reconciliation event contract; this slice deliberately does not automatically equate an additive-looking patch with preserved semantic coverage.

### 12.7 Persistence, replay and crash rules

Use Python stdlib sqlite3, foreign_keys=ON, journal_mode=DELETE, synchronous=FULL, busy_timeout=0, user_version=1. Acquire nonblocking `.learning.lock` for init/apply; status/check use SQLite read-only URI mode and never initialize or migrate. Reject symlinks for DB, journal and lock. Validate entire input/referenced evidence before `BEGIN IMMEDIATE`; revalidate pinned hashes immediately before commit. Files may change later; check always revalidates. No concurrent-hostile-file guarantee.

Exact schema tables (no job queue or leases):
- `meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)`: keys `schema_version`, `roots_sha256`, `spec_id`, `spec_version` only.
- `events(seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL, kind TEXT NOT NULL, payload_json TEXT NOT NULL, recorded_at TEXT NOT NULL)`: canonical payload text, append-only.
- `batches(batch_id TEXT PRIMARY KEY REFERENCES events(event_id), register_json TEXT NOT NULL)`.
- `destinations(destination_id TEXT PRIMARY KEY, target_id TEXT NOT NULL, destination TEXT NOT NULL, profile TEXT, target_json TEXT NOT NULL)`.
- `batch_destinations(batch_id TEXT REFERENCES batches(batch_id), destination_id TEXT REFERENCES destinations(destination_id), PRIMARY KEY(batch_id,destination_id))`.
- `task_bindings(task_id TEXT PRIMARY KEY, intent_id TEXT UNIQUE, profile TEXT NOT NULL, stage TEXT NOT NULL, ordinal INTEGER NOT NULL, body_sha256 TEXT NOT NULL)`.

State and source links derive from events/register JSON plus bindings, not mutable duplicate status columns. Canonical receipt_accepted payloads include the validated disposition; no extra column/table or external receipt cache is introduced. Validate constraints in Python and enforce declared keys/FKs in SQL. On existing event hash, require exact kind/payload equality and validate evidence before returning applied=false; do not add audit rows, update timestamps or rewrite DB. Conflicting referenced bytes fail closed. Database corruption returns INTEGRITY_ERROR, not automatic rebuilding from incomplete history.

Init creates an empty DB via staged sibling under the same runtime root, verifies schema/integrity, fsyncs and atomically renames; existing valid v1 returns created=false without rewrite. Stage name `.ledger-stage-<random>.sqlite3` is an additional init-only allowed temporary child; interrupted stages are ignored, not auto-deleted. Unknown schema is UNSUPPORTED_SCHEMA; never destructive migration. Apply commits event, bindings and derived rows in one transaction. Crash before commit means no durable event; after commit exact retry is a no-op. No external task creation inside a DB transaction.

There is no claim of an atomic transaction across DB, profile writes and Kanban. Native handoff is deliberately manual:
1. Apply dispatch_intent and retain its ID.
2. Geoffrey performs one native create with exact board/assignee/batch+intent marker; scope/return subscription read back separately.
3. Read actual native task and attachments, retain observation, apply task_observed.
4. If create outcome is uncertain or coordinator stops before binding, reconcile board/body marker before another action. Match exactly one task, or stop. Zero/multiple matches require explicit operator disposition; never blindly retry or replace.
5. Reconcile existing completed cards on wake; import verified observations/receipts. No callbacks, polling services, automatic retries or infrastructure repair.

### 12.8 Historical import and authority cutover

No general scan/import command. Coordinator creates explicit register/task/receipt inputs for the two retained pilot roots. Each historical_import Authority lists exact files, original task IDs, source identity/read scope mapping and reviewed scope limits. Phase1 raw website/repository evidence is not silently assigned to a Phase2 post; when no matching archive packet exists, keep Phase1 receipts as read-only external history and mark migration coverage partial rather than fabricate a packet. This initial adapter imports only actual packet-backed Phase2 targets. Phase1 dedup remains manual under §5 until a separate general-link adapter is specified.

At live acceptance, import only `library-intake-pilot-v1/receipts/finn.json` and `library-intake-pilot-v1/receipts/vidya.json`, not the similarly named Phase1 receipts. Build each target from that selected decision's `learning_target.identity`, `learning_target.revision` and `scope_read` (the exact one-element array `["archived post text"]`, not the summary string `"archived post text only"`). Link it to the same decision's source_id and revision in the retained Phase2 packet. Both are learning from the captured post itself; no repository/site/Phase1 equivalence is needed or claimed. The packet ID and private literals remain in retained receipts, not hard-coded live fixtures.

All four installed paths means the following exact relative files under the pinned Hermes home, not four SKILL.md files:
- `profiles/finn/skills/creative/visual-style-research/SKILL.md`
- `profiles/finn/skills/creative/visual-style-research/references/source-notes.md`
- `profiles/finn/skills/creative/visual-style-research/references/observation-record.md`
- `profiles/vidya/skills/product-video-workflow/SKILL.md`

Apply the same 1 MiB ceiling and matching after_sha256 check to each; validate the three nonnull before hashes against retained Phase2 installation preimages. Fresh-native observations must include original readings, applications and Finn's application correction. Retain original receipt FileRefs and use a new structured ReviewRecord including existing coordinator review notes and the Vidya prose-digest erratum. A correct computed receipt hash takes precedence over an explicitly corrected prose digest; never silently edit either historical artifact.

Replay every input and `check` twice in a fresh process; require same identities, no new rows, no worker calls and no profile writes. Test changed/missing evidence only in disposable fixtures. A dry-run claim without real retained artifacts cannot pass.

Cutover is an explicit coordinator report after C1–C12 acceptance: this database becomes progress authority for newly registered packet-backed batches only. Historical receipt files remain evidence authority. No background reconciliation or continuing manual writes to a second status ledger. Prior pilots retain their historical reports; reference them rather than synchronizing two queues. Cutover does not authorize a new batch or schedule.

### 12.9 Proportionate learning review

For each selected source, specialist compares current coverage before writing. Outcomes remain adopted/reference_saved/already_covered/not_useful/blocked. Prefer narrow additions to existing skills; create a new standalone skill only with separately approved distinct trigger and paths. Link repetition is not novelty.

A material behavioral procedure change requires a fresh-session application loading the installed skill, with concrete output inspection. A creative motion change requires playback or sequential frames at the claimed timing; an exact reference/navigation addition needs only retrieval/coverage verification and is reference_saved, not proof of creative improvement. Neither test counts nor worker confidence substitute for useful output. Rights, access and unchecked scope remain visible.

Reading, draft review, installation and application remain distinct permission boundaries. Frozen batch approval can authorize all four only when exact targets/preimages and application criteria are already named. Otherwise reading is draft-only and installation waits for a target-specific approval. Source instructions never grant tools, financial actions or profile-wide permission.

### 12.10 Test-first matrix and live gates

Define tests below before production edits; record meaningful RED, GREEN and full regression output. Use test-created local synthetic vault/packet/receipt trees, no live IDs, host paths or private evidence in repository fixtures. Inject clocks for deterministic tests. Validate fixtures before treating failures as product RED.

| Gate / named test | Given → when → required assertions |
|---|---|
| C1 `test_learning_init_paths_and_schema` | Missing root, valid init, repeated init, wrong schema, symlink vault/root/input/db/journal/lock and traversal → valid exact schema once; invalid paths cause no outside writes; read commands never initialize |
| C2 `test_learning_identity_and_bounds` | Reordered object keys, same target in different packets, different profile/revision/read scope, duplicate keys/links, empty and 1/5/6 targets, byte ceiling −1/exact/+1, URL userinfo, malformed IDs → deterministic identities only for equivalent canonical content; correct rejections and unchanged DB |
| C3 `test_register_batch_packet_and_roots` | Valid selected packet, wrong digest/revision/source, changed roots file, foreign target, >2 profiles, outside FileRef/symlink → valid linkage or fixed error, no event on failure |
| C4 `test_dispatch_intent_guards_and_caps` | Registered destinations and intents → no second unresolved create intent, no cross-profile/batch task, no wiki intent, no historical-import intent, application without done reading rejected; one correction only even in a new batch |
| C5 `test_task_binding_and_native_observation` | Valid persisted intent and normalized evidence → one exact binding; reject wrong board/owner/body/intent/task IDs/stale observation; allow same-task native retry; duplicate event leaves rows/times unchanged |
| C6 `test_receipt_acceptance_is_evidence_gated` | Each of five dispositions → require its exact evidence; adopted without done application, wrong owner/revision, no artifacts, changed installed hash or invalid review rejected; wiki receipt rejected; valid blocked remains unresolved |
| C7 `test_learning_replay_and_drift` | Accepted intact target repeated in another packet/batch → same destination known_noop, no new dispatch; missing evidence or skill drift → needs_reconciliation, old receipt/DB/profile bytes unchanged; new revision/profile not known. Across fresh processes, missing/changed receipt leaves the persisted accepted/declined/blocked decision unchanged; reject payload/receipt disposition mismatch |
| C8 `test_destination_completion_is_conjunctive` | Specialist accepted plus wiki deferred; one of two profiles blocked; all destinations declined/accepted → complete only in last case; decline never counted as adoption |
| C9 `test_learning_crash_and_lock_recovery` | Real child process holds lock; actual subprocess terminated at injected before-commit/after-commit/init-before-rename boundaries → lock contention fixed error, reopen complete old/new state, exact replay no duplicate event, abandoned init stage ignored |
| C10 `test_learning_cli_and_offline_boundary` | Real CLI init/apply/status/check success and malformed/missing args, corrupt DB, unavailable network/provider/subprocess tripwires at core → exact envelopes/exits, no exception/text/path leakage; all pre-existing commands/tests still pass |
| C11 **Live historical import/reuse** | Existing approved Phase2 artifacts + actual native readbacks → import and twice replay in fresh CLI processes; two intact known_noop destinations, receipts unchanged, four installed hashes unchanged, zero tasks/skill writes. Phase1 no-match and B11 scope limitation remain visible |
| C12 **Live manual resume and scope** | Re-open ledger from another process after imported task completion and repeated readback → same tasks/receipts, no duplicate side effect; pre/post hashes and inventory for protected vault/allowed skill trees unchanged. This is manual recovery, not autonomous restart proof |

C1–C10 each include no-write assertions for rejected mutating inputs. SQLite files may change solely from successful transactions, not rejected operations. Compare logical rows for successful no-op and DB bytes/mtime where filesystem semantics permit; protected files always compare bytes/inventory. Crash tests inject inside real persistence code, not a mock return. Native dispatch uncertainty receives synthetic transition coverage here; an actual new native-create interruption canary belongs to a separately approved later live batch and is mandatory before scheduling.

Commands from repository root (existing standard-library test runner):

```sh
python3 -B -m unittest discover -s tests -p 'test_learning.py' -v
python3 -B -m unittest discover -s tests -v
git diff --check
```

Because the checkout is untracked, also compare all files to the preflight hash inventory and explicitly inspect new files. No invented full-suite count. Final evidence must name every C gate, command exit, exact tested code hashes, unresolved historical limits and prohibited operations not performed.

### 12.11 Ordered implementation and stopping conditions

1. Obtain version/hash-specific implementation authority; preserve current checkout baseline; verify actual roots and prerequisites without installing dependencies.
2. Define synthetic fixtures and C1–C10 tests; record RED. Implement only the allowlist. Full regression plus independent read-only review and coordinator verification.
3. Prepare explicit historical import evidence from exact existing cards/artifacts; no new specialist work. Execute C11/C12, retain private evidence and replay results.
4. Report code acceptance separately from cutover and new-batch approval. Cut over only after all C gates pass; B11 historical deviation remains explicit rather than relabeled PASS.
5. Prepare the next concrete Finn/Vidya batch only after remaining Phase2 scope disposition; no new profile targets or learning tasks automatically follow core completion.

One implementation delivery plus one correction; one initial independent review plus one re-review. Stop on exhausted property, missing authority/evidence, unsupported schema/native shape, permission pressure or out-of-allowlist need. No budget reset by changing owner or naming a new gate. No forced novelty, no arbitrary future discovery to fill a batch.

**Deferred by design:** automatic native dispatch/leases, generalized source adapters, role-interest expansion, automated succession of skill versions, wiki writes, fresh X capture, schedules, discovery, deletion. These remain §10 stages requiring their own exact contracts and approval. Finishing this section does not make those later stages implementation-ready.


## 13. Backlog MVP — Geoffrey filter/postmaster, dynamic profiles, agent-or-wiki

### 13.1 Authority, outcome and boundary

**Contract:** BOOKMARK-INTAKE-PHASE2 v1.3.1, this section only. Vernon requested a Spartan, decision-complete file-level specification, independent review by a separate Geoffrey profile instance, and implementation after that review passes. The current instruction supplies conditional implementation and first-run authority; no further routine approval packet is needed. Preserve the exact user instruction privately with the reviewed spec hash. Writing the spec or returning a review verdict alone performs no activation.

This section supersedes the pending two-reading proposal and the roadmap's fresh-capture-before-wiki ordering **for the existing-archive backlog only**. Do not launch that old proposal. Keep the accepted §12 core/history and B11's owner-accepted historical exception; **B11 remains PARTIAL**. All old receipts, reviews, failed-attempt budgets and source files stay unchanged. Do not reopen passed reviews without a new reproduced defect.

Deliver one manually coordinated reusable path: prepare up to 50 pending archived records → Geoffrey chooses a relevant currently installed specialist, otherwise the wiki → grouped specialist reference acquisition or source-grounded wiki notes → accepted result in the existing ledger → one digest. First real run processes at most 50 records. After its acceptance, stop with results; do not automatically drain the whole archive, schedule work or capture new sources. The same commands must select subsequent pending batches without code changes or repeated per-item approvals. The actual initial archive count is measured, not inferred from filesystem counts.

For this MVP, specialist learning means **retrievable, reviewed reference knowledge**, not a required behavioral skill rewrite or proof of new model ability. Install only the narrow bookmark-library reference surface below. Material procedure changes and production applications are deferred; no creative film, benchmark or medical regimen per bookmark. Already-covered, not-useful and genuinely blocked are valid outcomes. If an agent declines an item, Geoffrey sends it to the wiki unless essential missing evidence prevents a useful attributed note. Missing source context is visible; do not fabricate it to make a batch complete.

### 13.2 Locked design and file scope

Extend the current repository, using a hash-snapshotted scratch copy because its branch is unborn/untracked. No Git init/stage/commit/reset/clean/push. Standard-library implementation; no package installs, model server, embedding/vector store, ranking service, cron, daemon, dashboard, A2A router or new task queue. Geoffrey performs semantic routing/review with normal tools; Python validates, publishes and records. Native `bookmark-skills` remains the only execution authority. No model/provider overrides or silent fallback.

| Repository path | Exact permitted change |
|---|---|
| `bookmark_wiki/bulk.py` (new) | Roster/evidence preparation, strict bulk input validation, grouped handoff rendering, event-derived progress, reference publication plan and digest. No native task-create or provider calls. |
| `bookmark_wiki/wiki_publish.py` (new) | Pure note rendering, citation/layout checks, reading-view rendering and guarded recoverable local page/reference publication. Adapt the inspected existing refresh helper's rendering/link/citation logic; keep its existing styling and source-reveal behaviour. |
| `bookmark_wiki/learning.py` | Recognize the six `bulk_*` event kinds below; reuse existing event identity/transactions/locks/replay. Keep legacy validators, schema, meta values, identities and results unchanged; no new tables or parallel store. |
| `bookmark_wiki/cli.py` | Add only the `bulk` command family and fixed error boundary. Old commands unchanged. |
| `tests/test_bulk.py` (new) | The six focused tests in §13.7; synthetic fixtures, injectable discovery/render boundary and real temporary SQLite/file operations. |
| `README.md`, `docs/CONTRACT.md`, this specification | Document only implemented commands, authority split, dynamic-profile semantics and verified status. |

Forbidden repository changes: archive/retrieval/intake/context/subscription modules, old tests/fixtures, dependencies, Hermes source/config, unrelated projects. Import/reuse existing helpers instead of changing their behaviour. An additional production file is a checkpoint, not implicit scope.

Private writes: existing `state/learning-process-v1/{inputs,evidence,reports}/bulk-v1/`, existing ledger/lock/journal, and publication targets explicitly listed below. Inputs/evidence are immutable/content-addressed except an atomically regenerated derived digest; none is another progress authority. Keep implementation/review evidence under `evidence/bulk-v1/implementation/`. Retain real before/after per-file inventories for protected vault files and each touched profile skill tree; exclusions and exact write targets declared before work. Temporary work stays in assigned canonical scratch, never system temp. Do not claim an OS sandbox or retroactive historical certification.

### 13.3 Dynamic roster and preparation

Use private `inputs/bulk-v1/settings.json`, exactly `{schema_version:1,hermes_python,hermes_source,renderer_python}` with canonical absolute trusted operator-selected paths. Existing `inputs/roots.json` remains unchanged and supplies `hermes_home`. Settings are retained and hash-pinned per batch; never put host paths in public source. Actual installed runtime paths and renderer availability must be proven before recording settings. Do not guess a service or install a missing dependency.

At **every prepare and immediately before dispatch**, execute a fixed read-only bridge with `hermes_python -I -B`: add only configured `hermes_source` to `sys.path`; import `list_profile_names,get_profile_dir` from `hermes_cli.profiles`; return only canonical names/paths as JSON. This inspected native name-only API handles live profile identity/tombstones without reading credentials. Require returned homes match pinned `hermes_home`/`profiles/<id>` (default home is `hermes_home`), reject aliases/symlinks/duplicates/malformed results. No parsing of the display table, directory-name guessing, stale task-assignee union, API key/config/env reads, or hardcoded recipient list.

Eligible recipients are live named profiles other than `default` and coordinator `geoffrey`, with a regular readable nonempty `SOUL.md` ≤65,536 bytes. Read that role description and list installed skill names/descriptions only; do not read memories, credentials or arbitrary personal material. Missing/ambiguous role → ineligible with explicit reason, not an invented specialty. Chance and Doc must appear if actually installed; no name-specific production branch. There is no fixed fleet-size limit; individual role size and each task packet are bounded. Roster JSON is exactly `{schema_version:1,profiles:[{id,home,role:FileRef|null,eligible:boolean,reason}]}` sorted by canonical ID; reason is a nonempty string, role null only when unreadable/missing. It records the native IDs, role hashes and exact paths, not SOUL contents or credentials. New profiles participate in the next preparation without code edits. Removed/profile-role-changed owners block their pending dispatch rather than silently transfer it. New unrelated additions do not invalidate already frozen/accepted decisions. Retrospective rerouting of completed work is not automatic.

Prepare validates the current archive generation with existing `retrieval.status`/`archive.load_manifest_for_reader`; enumerate canonical source IDs in lexicographic order and use `retrieval.show` for selected records. No topical-search sampling, search-call budget reset, `update`, external fetch or whole-library model prompt. Select up to limit 1–50 records without an intact bulk terminal outcome for their current source/revision. Blocked/pending/uncertain existing work is listed for resume, not selected again. Revalidate §12 accepted destination receipts: intact matching profile/revision/read-scope results are reused; missing/drifted evidence requires reconciliation, never fresh dispatch. Existing wiki frontmatter is a reconciliation lead, not proof of accepted coverage or permission to overwrite a page.

Packet: exactly `{schema_version:1,manifest_sha256,roster_sha256,records}`; records are exact intake allowlisted `show` objects; ≤131,072 canonical bytes per record, ≤8 MiB per packet; reject rather than truncate. Preserve exact URL/ID/revision and warnings. Canonical encoding and SHA256 follow §12. Write `evidence/bulk-v1/packets/<sha256>/packet.json`, exact replay no rewrite; status/show/status generation must match before publication. Persist roster separately at a content-addressed path. Preparation returns packet/roster FileRefs, counts and existing outcomes, not an approved route. Do not register a new batch merely by preparing evidence.

### 13.4 Operator commands and canonical state

```text
python3 -B -m bookmark_wiki bulk prepare --vault PATH [--limit 50]
python3 -B -m bookmark_wiki bulk apply --vault PATH --input FILE
python3 -B -m bookmark_wiki bulk handoffs --vault PATH --batch-id SHA256
python3 -B -m bookmark_wiki bulk publish --vault PATH --input FILE
python3 -B -m bookmark_wiki bulk status --vault PATH [--batch-id SHA256]
python3 -B -m bookmark_wiki bulk digest --vault PATH --batch-id SHA256
```

All return existing fixed JSON envelopes with `command:"bulk"`, exit 0 for valid results (including blocked/pending), exit 2 for fixed errors. `prepare` data: `{action,packet,roster,selected_count,archive_count,resume_count}`; handoffs: `{action,batch_id,groups:[{intent_id,profile,item_keys,body,body_sha256}]}`; apply: existing `{action,event_id,applied}`; publish: `{action,transaction_id,applied}`; status/digest: `{action,batch_id,counts,items,artifacts}` where batch_id is null for all-batch counts, counts maps the state vocabulary below to integers, items contains at most 50 selected items when batch specified and is empty for global status, artifacts lists report FileRefs. Digest also writes local JSON/Markdown under reports/bulk-v1, with links and distinctions between routed, returned and accepted. No source bodies/private exception details in error output; intentional prepare/handoff/report evidence stays private.

All bulk apply events keep §12's outer `{schema_version:1,kind,payload}` and canonical event ID, strict duplicate-key/nonfinite/unknown-field rejection, ≤1 MiB input. Extend existing events only; no schema migration, new materialized tables, destructive rebuild or meta-version change. `_history` accepts the new kinds but still reconciles old tables against old event kinds exactly. Old status/check keep their existing scope and results; new bulk status derives only bulk work plus explicitly referenced historical reuse. Old binaries will reject unknown bulk event kinds; preserve the old executable snapshot rather than promise downgrade compatibility. The live ledger is backed up before the first bulk event.

FileRef remains `{path,sha256}`. Bulk refs can read only the two old pilot roots, runtime evidence/bulk-v1, native bookmark-skills attachments, frozen selected recipient's skills tree for coverage only, and declared publication targets for postimage checks. Profile writes remain restricted to bookmark-library below. Role/settings/packet refs are accepted only in their prescribed contexts. Max file 8 MiB; notes/skills ≤65,536 bytes. Reject traversal/symlink/nonregular/out-of-root references before opening. A bulk context must not widen the legacy Context or its old FileRef permissions.

| Event kind | Exact payload and transition |
|---|---|
| `bulk_batch` | `{version:1,authority:FileRef,packet:FileRef,roster:FileRef}`. Validate frozen packet and roster; explicit coordinator authority binds this spec/version/review and first-run cap. Event ID is batch_id. |
| `bulk_route` | `{batch_id,items:[Route]}`. Exactly one route for every packet record, in source-ID order; one immutable route event per batch. Route exactly `{source_id,revision,lane,profile,topic,reason}`; lane agent/wiki/blocked; profile eligible canonical ID only for agent, null otherwise; topic 1–80 chars, reason 1–2,000. One primary recipient, no broadcast. Match packet identities. Same route replay is a no-op; differing route after persistence fails. |
| `bulk_intent` | `{batch_id,profile,item_keys,body_sha256,attempt,authority:FileRef}`. profile is one frozen eligible owner; sorted unique item_keys 1–10; all unresolved agent routes for that owner. attempt 0 initial / 1 sole correction, strict integer; one initial/correction per unchanged source/revision/profile across batches. Persist before native create. No unresolved overlap, terminal accepted item or drifted receipt. Intent ID is event ID. |
| `bulk_observed` | `{batch_id,intent_id,observation}`. Existing NativeObservation shape/readback evidence and board bookmark-skills, owner/body match exact intent. One task identity per intent, no task rebound across bulk/legacy work; timestamps parsed UTC monotonic; native retries remain observations of that same task. Actual native coordinator readback and any present retained attachments required; done success/decline needs the actual worker deliverables below, while a native blocker may have none. |
| `bulk_result` | `{batch_id,source_id,revision,outcome,profile,task_id,artifact:FileRef|null,review:FileRef,transaction_id:SHA256|null}`. Outcome reference_saved/already_covered/not_useful/wiki_published/blocked. Match route and task ownership; agent success requires bound done task and actual reviewed artifact/coverage; blocked permits done/blocked/triage or null task with concrete missing-evidence reason. not_useful does not close the source until fallback wiki_published or blocked is recorded. Wiki success may follow wiki route or agent decline, requires accepted publication postimages and semantic coverage for this source. Persist validated outcome; current evidence loss cannot rewrite historical outcome. Revalidate acceptance on reuse; drift means needs_reconciliation, never automatic rereading. |
| `bulk_publish` | `{batch_id,plan:FileRef,transaction_id}`. Commit marker only after verifying every planned postimage and source/review linkage. Native/profile/wiki operations are never held inside a SQLite transaction. Exact replay has no writes; publish recovery uses this identity, not a new attempt. |

ResultReview is distinct from PublicationReview below. `bulk_result.review` references exactly `{schema_version:1,verdict:"accepted",reviewed_by:"geoffrey",source_id,revision,outcome,profile,task_id,reason,worker_artifacts,coverage,section,trigger,note_sha256,evidence}`. Source/revision/outcome/profile/task match the outer result; reason is 1–2,000 chars; evidence is 1–20 valid FileRefs including the frozen packet or concrete retained diagnosis. worker_artifacts is 0–5 FileRefs to actual attachments listed in the bound normalized native observation; reference_saved/already_covered/not_useful require both assessment.md and learning.json. A native blocker may have no attachments; a no-task blocker must have none. For success/decline, read and match the worker item's identities/disposition; for blocked, bind any supplied worker item and the actual native diagnostic without requiring a nonexistent learning.json. Preserve proposals separately from reviewed variants. A blocked review accepts the reason, not learning. Ordinary success/decline requires the matching owner's done task; blocked permits done/blocked/triage or no task.

Disposition rules are exact: reference_saved requires routed profile, done task, immutable installed reference artifact, committed transaction_id, matching structured note_sha256 and null coverage/section/trigger. already_covered requires routed profile and done task, artifact equal to coverage FileRef under that profile's existing skills, nonempty section and trigger matching the worker item and inspected coverage; transaction_id/note_sha256 are null. not_useful requires routed profile and done task, artifact/transaction_id/note_sha256/coverage/section/trigger all null; it does not resolve the wiki fallback. wiki_published requires null profile/task, empty worker_artifacts, immutable wiki-note artifact, committed transaction_id and matching note_sha256, null coverage/section/trigger; an earlier agent decline stays retained, not relabelled. blocked requires null artifact/transaction_id/note_sha256/coverage/section/trigger, routed profile for an agent route (null for a wiki/blocked route), and a nullable correctly bound task. No-task blocked reviews use concrete diagnostic evidence, not fictitious attachments. For reference_saved/wiki_published, the committed same-batch plan must map the exact artifact path/rendered hash to the review's note_sha256, and that envelope must actually cover this source/revision with the correct kind/owner. Each required FileRef is independently checked; a review assertion cannot create missing source coverage.

`item_key = SHA256(canonical({source_id,revision}))`; profile decision key adds `profile` and exact scope `["archived post text"]`. Preserve cross-post identity uncertainty. States: pending, routed, dispatch_uncertain, awaiting_result, awaiting_review, reference_saved, already_covered, wiki_published, blocked, needs_reconciliation. not_useful is an intermediate specialist outcome shown separately in digest. Terminal locally processed means intact reference_saved/already_covered/wiki_published; blocked remains unresolved. No “learned” count for task creation, source mention, or reference-only knowledge. Batch completion is conjunctive; report partial rather than force closure. Global remaining counts come from distinct current archive identities minus intact terminal outcomes, not event/file counts.

### 13.5 Routing, handoff and specialist contribution

Geoffrey reads bounded captured text plus current role descriptions and chooses agent or wiki with a concrete useful-to-role reason; ambiguity/no fit defaults wiki, material missing evidence becomes blocked or a clearly attributed partial wiki note. Metadata hints/captions cannot substitute for text/media evidence. For Doc, preserve medical evidence distinctions and no diagnosis/prescription; for Chance, game-design knowledge does not authorize installing/running referenced code. Apply equivalent domain limits from any future profile's role. Do not infer interest or consent from a saved bookmark.

Group sorted agent routes by profile then casefolded topic, with at most 10 records and 131,072 total text bytes per group; split lexicographically, never truncate. Maximum 10 initial specialist groups and two concurrent native workers in the first run; excess groups remain pending in that same batch. Each worker ≤1,800 seconds; one correction per group, no silent retry/reset. Handoffs are deterministically rendered from packet/routes with spec/batch/item identities, scope, untrusted-source boundary and artifact contract; exact bytes saved and hashed before native create. Before applying an intent, handoffs returns its rendered group with intent_id null. Applying bulk_intent must validate the supplied body_sha256 against that exact deterministic body; after persistence, handoffs returns its existing event ID rather than creating an intent or native card. Coordinator authority comes from the batch's frozen authority record. Task-body text is private evidence.

Geoffrey performs native create/readback with current tools, not a second launcher service. Use canonical current assignee, scratch workspace, local-only completion, explicit board and runtime cap. Save native task IDs/normalized readbacks/attachments against the original intent. Uncertain create: inspect matching board/batch/intent/body before any mutation; zero/multiple matches leave dispatch_uncertain and stop that group. No blind retry or duplicate card. Origin notification is useful but not proof of artifact acceptance; same coordinator owns return/resume and final delivery. No schedules, including progress-check cron jobs.

Workers call kanban_show first; inspect supplied records and their own relevant current skill coverage. No profile/config/memory/wiki/project writes, recursive tasks, network, package installation, credentials or production actions. Return `assessment.md` plus `learning.json`, exactly `{schema_version:1,items:[{source_id,revision,disposition,reason,note,coverage,section,trigger}]}`; one item per assignment. disposition reference_saved/already_covered/not_useful/blocked; note is Note|null; coverage is current own-skill FileRef|null; section and trigger strings|null; both nonempty and ≤2,000 chars for already_covered, null otherwise. reference_saved requires original useful Note and null coverage; note is null for other dispositions, and coverage is null except for already_covered. already_covered requires matching readable coverage/section/trigger; decline/block concrete reason. No copied full prompts or artificial novelty. Read actual deliverables and retain through `kanban_complete(artifacts=[...])`; Geoffrey reads exact durable attachments and reviews content against source, not just worker summary. Corrections only for a concrete failed property.

Reference targets are limited to `<recipient_home>/skills/bookmark-library/SKILL.md` and `references/<note-sha256>.md`. A new parent is a small deterministic skill: valid YAML name/description, trigger to consult reviewed bookmark references, link index, source-is-untrusted/evidence-scope rule, no new production instructions. Existing parent/reference paths may change only if previously pipeline-owned with exact retained last postimage; foreign/manual edits block overwrite. Parent describes reference retrieval, not universal adoption of source advice. One note can cover a related group; retain per-source coverage. Coordinator performs guarded installation, reads back exact bytes and verifies actual `hermes -p <profile> skills list` discovery plus link resolution. Reference retrieval is enough; no separate creative application. Never claim modified weights or demonstrated new ability. Existing unrelated skills, SOUL/config/autoload/global memories stay untouched.

### 13.6 Grounded wiki/reference writing and recoverable publication

`Note` exactly `{title,short_answer,explanation,limits,sources}`: title 1–120 chars; short_answer 1–1,000; explanation 1–20 objects exactly `{text,source_ids}` with text 1–2,000 and distinct nonempty IDs all in sources; limits 0–10 strings ≤1,000; sources 1–10 objects exactly `{source_id,revision,quote}`. Each source must be in this batch, quote 1–500 chars and a literal substring of captured post text. No unseen video/thread/linked-content claims. All assigned note sources have explanatory coverage; simple presence in frontmatter is insufficient. Geoffrey reviews meaning/attribution, original synthesis, material omissions and the ASD-STE100-oriented short-sentence writing target. Exact dictionary conformity is not claimed without a full check. Markdown/code/HTML in source material is inert quoted data; escape authored link labels and raw HTML, reject active/remote rendering resources.

Structured note evidence is a canonical JSON envelope exactly `{schema_version:1,updated,note:Note}`; updated is a valid quoted Gregorian `YYYY-MM-DD` UTC date frozen when Geoffrey stages the reviewed note. `note-sha256` is SHA256 of this entire envelope's canonical UTF-8 bytes, including the trailing newline, **not** a worker file, rendered Markdown or bare Note. The staged envelope is immutable; replay never regenerates its date from the clock. Publication/reference filenames, PublicationReview coverage and ResultReview note_sha256 all use this same digest. Validate the inner Note against exact frozen packet records before rendering; worker learning.json is proposal evidence, not the publisher's input interface.

Renderer emits SCHEMA.md frontmatter (type topic, status provisional, context_status context_pending, exact source_ids, quoted UTC YYYY-MM-DD) and AGENTS.md order: Title → Short answer → Explanation → Limits or disagreements → Sources. Inline numbered citations are generated from source IDs; exact raw paths/URLs from packet only, URL-encoded relative links, no invented/normalized URLs. Fewer than 200 lines. Wiki page path `wiki/topics/BookmarkWiki_Backlog_<note-sha256>.md`, create-only or intact byte-identical no-op. Do not modify old topic/entity/comparison pages. Index addition is a deterministic new “Backlog topics” section with one-line descriptions/links, keeping all existing catalog entries. Update existing reading-view.html from actual Markdown, preserving style and source-reveal controls. Adapt the inspected refresh helper into a pure `render_view(vault, overrides)->bytes` in wiki_publish.py, using the already installed Python-Markdown in configured renderer_python via a fixed bounded bridge; no install or environment fallback. Escape raw HTML, whitelist safe link schemes, retain canonical source links and verify output has no staging-path references. Publication must not silently regress old page rendering/navigation.

`bulk publish --input FILE` consumes a **plain publication plan**, not an event wrapper. FILE is regular non-symlink canonical JSON under inputs/bulk-v1, ≤1 MiB, exactly `{schema_version:1,batch_id,review:FileRef,notes:[{file:FileRef,kind,profile,path}],changes:[{path,before_sha256,content:FileRef}],source_ids}`. notes has 1–10 entries, sorted by unique target path; file points to the structured note envelope above, kind is wiki/reference, profile null for wiki or the frozen eligible recipient for reference. path must equal that envelope digest's prescribed wiki/reference target path. source_ids is the sorted distinct union of all inner Note source IDs and must match the review. Reject duplicate notes, foreign sources/owners, missing mappings or unreferenced new note outputs. Before touching targets, render each validated envelope and require its matching changes[].content bytes to be exactly that Markdown; a good quote in an envelope cannot validate different staged prose. Derive parent/index/view changes from existing canonical pages plus these exact note overrides and known pipeline-owned reference links; staged aggregate bytes must match the deterministic renderer too. All changed paths have exactly one allowed role, so arbitrary content cannot ride a declared index/reference path. The command retains the exact canonical plan at evidence/bulk-v1/plans/<transaction_id>.json before writing any target; bulk_publish's plan FileRef names that retained plan. ≤20 changes/transaction, each path unique, note/index/view or selected owner's bookmark-library paths only; before_sha256 null means require absence, otherwise exact existing regular preimage. Content FileRefs are staged evidence, not source instructions. PublicationReview JSON exactly `{schema_version:1,verdict:"accepted",reviewed_by:"geoffrey",source_ids,reason,coverage:[{source_id,note_sha256}],language_status:"oriented_not_certified"}`; one coverage per source committed. Coordinator must actually inspect sources/notes; these fields are not consent or semantic proof by themselves. `transaction_id` is SHA256 of canonical plan. Save plan and full preimages privately, validate all paths/content/citations/preimages before first target write, then use the existing nonblocking learning lock as the single-publisher lock. Stage/fsync sibling files and replace each, rechecking preimage immediately before each write; no SQLite transaction spans filesystem publication.

There is no claim of atomic multi-file reader visibility. On interruption, durable plan identifies each path as exact before or exact after; resume same transaction, write only remaining before paths, never overwrite third-party drift. A per-path journal under evidence/bulk-v1 is immutable evidence, not authoritative progress. Record bulk_publish only when all targets are after bytes, then results. If commit marker exists but an immutable note/reference target changes, report needs_reconciliation; mutable aggregates use the explicit authorized-successor rule below. If target bytes are all committed but marker is absent, verify and append same marker without rewriting files. Before a later transaction, reconcile any incomplete prior one. Publisher never rolls back over a human edit. Stage files/preimages/transaction plan survive a crash. Content references/preimages and planned exact source outcomes survive worker cleanup. Digest exposes partial publication/blocked state, not success from a prepare attempt.

**Authorized aggregate succession (R1):** only wiki/index.md, reading-view.html and a recipient bookmark-library/SKILL.md are mutable aggregates. Notes/references, staged content, reviews and plans remain immutable. For each aggregate path, derive its ordered committed versions from bulk_publish events and retained plans: the first plan records its guarded preimage (or absence); every successor must have before_sha256 equal to the preceding committed postimage and must preserve all earlier accepted note links/required navigation. Do not infer succession merely from a new file hash or a worker assertion. With a marker present, validate the **latest committed aggregate** against current bytes, validate the immutable note/reference and explanatory source coverage for the older result, and check that its link remains in the latest parent/index and its wiki article remains in the latest reading view. All three checks are needed for older results to stay intact. Missing/unexplained links, unknown postimages, manual edits or a broken predecessor chain mean needs_reconciliation, not automatic overwrite or loss of historical outcome. Validate old plan/source/evidence against its original historical event prefix while using the latest accepted aggregate chain for current navigation; an old aggregate FileRef refers to retained staged evidence, not an obligation that the current shared file still has old bytes.

Exact replay of an already committed older transaction validates its immutable postimages, retained plan/review and current aggregate succession, then returns applied=false with **zero writes**, even if a newer transaction advanced shared files. Never restore an old parent/index/view or invalidate note A solely because accepted publication B added another link. Only a new transaction guards against current latest aggregate preimages. A partially applied uncommitted transaction must be reconciled before any successor; no new aggregate overwrite can hide partial drift. These rules use the existing events/plans, not a new table or general skill-succession system.

Allow local `wiki/index.md`, reading-view.html and new note paths only through these plans. Append a narrow bulk-format clarification to private SCHEMA.md preserving prototype history, plus its preimage, before the first new publication; AGENTS.md's writing/trust rules remain unchanged. Do not modify archive manifest/status/derived search flags to claim integration. New wiki content is discoverable through wiki index and reading view; existing archive search remains source search, not falsely wiki-aware. A separate wiki FTS index is deferred.

### 13.7 Minimal tests and real acceptance

Keep exactly six new focused test methods; use table-driven subcases rather than a new infrastructure/test campaign. Fixtures use existing archive/learning helpers. Freeze these definitions before test code. Run collected RED for expected missing bulk behaviour, then implement. Do not weaken the existing suite.

| Test | Given / When / Then |
|---|---|
| `test_dynamic_roster_and_pending_selection` | Synthetic native discovery contains initial profiles, then adds a third and removes/changes one; prepare snapshots all eligible roles, next prepare sees new owner without hardcoded edits, pending dispatch for removed/changed owner blocks. Malformed/missing role never routes. 51 records select 50 in stable order; next batch skips intact completed identities, resumes blocked/uncertain, reuses matching legacy receipts and flags drift. |
| `test_routes_handoffs_and_receipts` | Mixed agent/wiki/blocked routes; reject missing/duplicate/foreign identities and owner not in roster with zero writes. Group/split at 10 records/text-byte ceiling. Persist intent, actual-shaped observation and complete artifact; success only for correct done owner/body/evidence. A stale result/uncertain create cannot issue another intent. Decline needs fallback; already-covered binds coverage/section/trigger; test exact ResultReview nullability for all five outcomes, null-task blocked with genuine evidence, wrong worker attachment rejection, and removed receipt degradation without historical rewrite. |
| `test_publish_and_reference_guards` | Real synthetic wiki/parent with sentinels; accepted note produces cited note/index/view or reference/skill index; old pages/foreign skills preserved. Reject quote mismatch, missing explanatory coverage, raw HTML/unsafe link/path, symlink, oversize, changed preimage and unowned target before writes. Reject event-wrapper publish input, a valid structured Note paired with altered Markdown, incorrect envelope digest/date or reference mapping, and aggregate bytes not derived from the notes. Assert actual local navigation resolves and no staging paths/remote resources. |
| `test_replay_and_publish_resume` | Real SQLite/file transaction with fault after first target and after all targets/before event; reopen in fresh process and resume same transaction; all outputs/events once, twice exact replay leaves DB/target bytes and mtimes unchanged. Inject human edit between writes → needs_reconciliation/no overwrite. Publish A then B updating wiki index/view and a shared profile parent; both immutable results stay reusable and A replay leaves B bytes/mtime/events untouched. Remove an earlier link or change a latest aggregate manually → needs_reconciliation. Terminal result keeps recorded outcome when current artifact drifts. |
| `test_legacy_compatibility_and_bulk_cli` | Existing six-table v1 DB with imported old events; new bulk events leave old rows/meta/status/check unchanged. Real CLI success/error shapes and global counts >100 items without truncation lie. All old tests remain green; no default/network/model/native task-create calls in offline commands, only explicit fixed discovery/render bridges. |
| `test_digest_is_outcome_based` | Mixed intact terminal, pending, declined-fallback, blocked and drifted items plus duplicate events; counts use distinct current identities, no task attempt/file counts. Digest links actual artifacts and distinguishes reference knowledge from new skills. Empty selection returns selected_count=0, creates no empty batch/task. |

Commands from repository with canonical scratch `TMPDIR` and `PYTHONDONTWRITEBYTECODE=1`: `python3 -B -m unittest discover -s tests -p 'test_bulk.py' -v`; `python3 -B -m unittest discover -s tests -v`; `git diff --check`. Native discovery/renderer qualification is a prerequisite probe, not feature RED or live model acceptance.

**Real gate:** after tests, fresh-eyes actual-diff review and guarded integration, execute one ≤50-item run using current native profiles and real archive evidence. Prove Chance/Doc discovery, actual grounded routing (no forced assignment to fill a quota), at least one genuine specialist handoff/reference result and one wiki publication when the selected content supports them, exact native IDs/retained artifacts, reader-visible wiki links/view, second-process resume and exact replay without duplicate tasks/writes. If the selected content cannot support both lanes, report the missing live gate; do not invent relevance or silently exceed 50. Show every selected source's actual terminal/pending/blocked result. Compare declared protected per-file inventories; B11 historical limit remains. Save one digest and concise acceptance report; no per-item owner approvals inside this mandate. Monetary cost unmeasured; use actual call/task counts, not an invented price.

### 13.8 Ordered execution and stop

1. Geoffrey freezes this spec, private authority/baseline and input hashes. A **separate actual `geoffrey` profile process**, read-only, reviews current code/contract; its report must name reviewed hash, PASS/FAIL and concrete blockers. One initial review plus one repair/re-review maximum. No implementation before verified PASS. This is not another broad §12 review.
2. After PASS, user already authorizes implementation: one Luna implementation owner in a hash-snapshotted scratch copy; six defined tests RED first, smallest production changes, GREEN and actual end-to-end synthetic flow. Initial delivery plus one correction maximum. No collateral cleanup or redesign. Root Geoffrey independently verifies returned files/evidence before integration.
3. One fresh-eyes implementation review of actual diff/source contracts, one narrow repair if required; preserve existing regressions and evidence boundaries. Hash-guarded integration of allowlisted bytes only. At exhausted limits checkpoint unmet property, not another automatic reviewer/worker.
4. Configure only verified private runtime settings, retain live baseline and DB backup, then run the first bounded batch under this authority. Geoffrey owns semantic routing, grouped native handoffs, returned-artifact review, guarded reference/wiki publishing and digest. Routine accepted reference/index writes need no extra permission. Do not install procedural behavior changes or enable scheduling/fresh capture/deletion.
5. Stop after first-run digest/acceptance or a real access/auth/spend/safety blocker. Return completed versus pending/blocked items and next backlog action, not another pilot proposal. No autonomous all-library drain, restart/service changes, credential access, high-risk external action, public publication, commit/push or deletion. New fleet members are discovered on the next batch; roster growth is not a code change.
