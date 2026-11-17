# Current project status

This initial repository snapshot includes the latest independently reviewed archive, retrieval, bounded intake, manual learning ledger and existing-archive bulk coordinator code. The Python package remains `bookmark_wiki`; the repository and local checkout are named `hermes-bookmark-wiki`.

## Lean lane: live and in use (2026-11-16)

The lean lane (`bookmark_wiki/lane/`, `scripts/lane-sync.sh`) is a separate,
additive path and is the one used day to day. It was qualified live on the 40
newest bookmarks: 39 delivered, 1 skipped, 0 failed, with reference files
delivered to nine bot profiles and six bot runs. Operating guide:
[LANE_RUNBOOK.md](LANE_RUNBOOK.md). It has no schedule installed, has not
drained the older archive, and its known gaps are listed in the runbook. The
sections below describe the older gated lane and are unchanged.

## Included and reviewed

- Offline archive import, local search and source lookup.
- Bounded, source-linked intake packets and durable manual coordinator progress.
- Bulk agent-or-wiki routing records, dynamic recipient discovery, guarded local publication and reconciliation/no-repeat checks.
- The three latest repair findings are closed by independent review: catalog-tail preservation, authorization before reading a referenced note, and blocker-evidence validation before a fresh dispatch intent. Production Python files in this snapshot match that reviewed candidate exactly.

## Not established by this snapshot

- Real specialist handoffs, retained learning results and live wiki publication for the bulk lane have not passed end-to-end acceptance.
- The first live bulk trial remains capped at 50 existing sources. No trial, automatic archive drain, schedule, profile change or deployment is triggered by publishing this repository.
- Historical B11 scope certification remains **PARTIAL**. Current tests do not retroactively replace missing historical evidence.

## Public/private boundary

Source code, tests and documentation belong here. Actual bookmarks, captured source material, runtime databases, local settings, credentials, private reports, native task records and preserved historical evidence do not.

The default test suite uses synthetic fixtures. Legacy frozen transport tests require an explicit `BOOKMARK_WIKI_PRIVATE_TEST_VAULT` and are skipped when it is absent. Those private artifacts and the pinned executable are intentionally not included. Bulk rendering requires an already qualified interpreter with Python-Markdown.

Historical specifications describe their original phase and approval boundaries. This file and the README explain the current publication state without rewriting private historical receipts or changing the normative bulk contract.
