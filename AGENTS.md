# Agent guide

## "Sync the latest bookmarks and ingest them"

Run this and wait for it to finish:

```sh
/Users/vernon/Documents/GitHub/hermes-bookmark-wiki/scripts/lane-sync.sh
```

Then summarize the day's report for the user:

```sh
cat "/Users/vernon/Documents/BookmarkWiki/lane/reports/$(date +%F).md"
```

That command captures new X bookmarks (read only), finds the real content
behind each one, decides which Hermes bots and/or the wiki should get it,
delivers it, and writes the report. If it processed 25 items, run it again;
more may be waiting.

Full operating guide, other commands and troubleshooting:
[docs/LANE_RUNBOOK.md](docs/LANE_RUNBOOK.md). Design: [docs/LEAN_LANE_SPEC.md](docs/LEAN_LANE_SPEC.md).

## Hard rules

- X access is read only. Never post, like, follow or unbookmark.
- Never copy or print browser cookies or other credentials.
- Never edit a bot's `SOUL.md` or `memories/`.
- Do not touch the older archive in the vault (`manifest.json`, `raw/`,
  `state/`, `source-evidence/`) or hand-edit lane state; use `lane retry`.
- Do not commit, push, deploy or install a schedule unless the user asks.

## Working on the code

- Lane code is `bookmark_wiki/lane/`; lane tests are `tests/test_lane_*.py`.
- The rest of `bookmark_wiki/` is the older gated lane. Leave it alone unless
  asked.
- Test command (the `TMPDIR` is required on this machine):

```sh
env -u PYTHONPATH -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1 \
  TMPDIR=/Users/vernon/.hermes/profiles/geoffrey/cache/scratch \
  /Users/vernon/.hermes/hermes-agent/venv/bin/python3 -B -m unittest discover -s tests
```

- Do not weaken tests to make them pass.
