# Bookmark lane runbook

This is the operating guide for the lean bookmark lane. If you are an agent and
you were told something like "sync the latest bookmarks and ingest them", this
is the whole job. Design detail lives in [LEAN_LANE_SPEC.md](LEAN_LANE_SPEC.md).

## The one command

```sh
/Users/vernon/Documents/GitHub/hermes-bookmark-wiki/scripts/lane-sync.sh
```

Run it, wait for it to exit, then report the result (see "What to report").
It needs no arguments. It works from any directory.

What it does, in order:

1. **Capture.** Lists the newest X bookmarks from the personal account (read
   only), 20 per page, and stops paging once a page holds only bookmarks it
   already knows.
2. **Resolve.** For each new bookmark, gathers the real content: the post, the
   author's own replies, the quoted post, directly linked pages and GitHub
   repositories, images (described by a vision model), and video (transcript
   plus four sampled frames). One hop only; it does not crawl further.
3. **Triage.** Decides the value (`high`, `normal`, `low`, `skip`), whether it
   goes to the wiki, and which Hermes bots benefit (none, one, or several).
4. **Write.** Produces one source-cited note.
5. **Deliver.** Writes the wiki note, writes a reference file into each chosen
   bot's `skills/bookmarks/<topic>/` skill, and for bots where relevance is
   `high`, runs that bot once so it can fold the lesson into its own skills.
6. **Report.** Appends to `lane/reports/<date>.md` in the vault.

Timing: about 1 to 2 minutes per bookmark, longer with video. A run processes
at most 25 items (`limits.items_per_sync`). If more are waiting, run it again.
Progress lines go to stderr; stdout is a single JSON object.

## What to report

After the run, tell the user:

- how many were delivered, skipped and failed (`lane status`, below)
- for each new item: title, value, and where it went (wiki, which bots)
- any bot run and the change the bot reported
- any item with a `Limit:` line saying a source was not read (JavaScript-only
  page, PDF, failed fetch), since the user may want to look at those by hand

The dated report file already holds all of this:

```sh
cat "/Users/vernon/Documents/BookmarkWiki/lane/reports/$(date +%F).md"
```

## Other commands

All take the same interpreter and vault. Shorthand used below:

```sh
cd /Users/vernon/Documents/GitHub/hermes-bookmark-wiki
LANE() { env -u PYTHONPATH -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1 \
  /Users/vernon/.hermes/hermes-agent/venv/bin/python3 -B -m bookmark_wiki lane "$@" \
  --vault /Users/vernon/Documents/BookmarkWiki; }
```

| Need | Command |
| --- | --- |
| Counts by status, list of failures | `LANE status` |
| Everything recorded for one bookmark | `LANE show --id <tweet_id>` |
| Redo one bookmark from a stage | `LANE retry --id <tweet_id> --from-stage resolve` |
| Process waiting items without contacting X | `scripts/lane-sync.sh --no-capture` |
| Process only a few | `scripts/lane-sync.sh --limit 5` |
| Deliver files but do not run any bot | `scripts/lane-sync.sh --no-bot-runs` |
| Preview without writing wiki or bot files | `scripts/lane-sync.sh --dry-run --limit 3` |
| Look further back than the newest 40 | `scripts/lane-sync.sh --capture-count 200` |
| Queue specific posts | `LANE seed --ids <id>,<id>` |
| Queue from the old archive | `LANE seed --from-archive --newest-first --limit 50` |

Stages for `retry`: `capture`, `resolve`, `triage`, `write`, `deliver`.
`--capture-count` accepts up to 800.

## Where things are

| Thing | Path |
| --- | --- |
| Lane state | `/Users/vernon/Documents/BookmarkWiki/lane/lane.sqlite3` |
| Settings | `/Users/vernon/Documents/BookmarkWiki/lane/config.json` |
| Per-bookmark records | `.../lane/items/<tweet_id>/{capture,bundle,triage,note,delivery}.json` |
| Run reports | `.../lane/reports/<date>.md` |
| Wiki notes | `/Users/vernon/Documents/BookmarkWiki/wiki/topics/Bookmark_<tweet_id>_<slug>.md` |
| Wiki index section | `## Bookmarks (lean lane)` in `wiki/index.md` |
| Bot references | `~/.hermes/profiles/<bot>/skills/bookmarks/<topic>/references/<tweet_id>-<slug>.md` |
| Bot topic skill | `~/.hermes/profiles/<bot>/skills/bookmarks/<topic>/SKILL.md` |

Item statuses: `done` (delivered), `skipped` (triage found nothing worth
keeping), `failed` (see `error`; retried automatically up to
`limits.max_attempts`, then left for `retry`).

## Rules for any agent operating this

- X access is read only. Never post, like, follow, or unbookmark. The lane only
  uses `whoami`, `bookmarks`, `read`, `thread` and `replies`.
- Never copy, print or move browser cookies or other credentials.
- Do not edit a bot's `SOUL.md` or `memories/`. The lane restores `memories/`
  if a bot run touches it.
- Do not write to the older archive: `manifest.json`, `raw/`, `state/`,
  `source-evidence/` in the vault belong to the older gated lane.
- Do not hand-edit `lane.sqlite3` or the per-item JSON. Use `retry`.
- Do not run two syncs at once. The lane takes a lock; a second run fails fast.
- Do not commit, push or install a schedule unless the user asks.

## When something goes wrong

| Symptom | Cause and action |
| --- | --- |
| `BIRD_FAILED: ...` at capture | Chrome profile is logged out of X, or Bird changed. Check with the Bird `whoami` command using the argv in `config.json`. Capture failure does not block `--no-capture` processing. |
| `invalid Bird JSON` or `invalid Bird output encoding` | Bird runs under bun, which truncates piped output and can corrupt output past 256 KB. The lane already pages 20 at a time and writes to a file. Do not raise the page size. |
| One item `failed` | `LANE show --id <id>` for the error, then `LANE retry --id <id> --from-stage <stage>`. |
| Note says a link was "not read" | JavaScript-only pages and PDFs are recorded but not read. Report it to the user. |
| A bot run timed out | The reference file was still delivered. `retry --from-stage deliver` reruns delivery. |
| Tests fail with path-safety errors | Set `TMPDIR=/Users/vernon/.hermes/profiles/geoffrey/cache/scratch` (the default macOS temp path is a symlink). |

## Settings worth knowing (`lane/config.json`)

- `bird.capture_profile`: Chrome profile of the personal X account (bookmark
  listing only). `bird.read_profile`: the separate reader account used for
  threads and replies.
- `llm.argv`, `llm.vision_argv`: the model backend, currently GPT-6.1 Terra
  through `hermes`.
- `hermes.exclude_profiles`: bots that never receive bookmarks.
- `hermes.bot_runs`: set `false` to deliver reference files only.
- `limits.capture_count` (40), `limits.items_per_sync` (25),
  `limits.links_per_item` (10), `limits.images_per_item` (4).

## Known gaps

- Near-duplicate bookmarks (several posts recommending the same tools) are only
  detected when they share a fetched link or GitHub repository. Posts that
  share only a quoted post each get their own note.
- A skipped bookmark keeps nothing but its record under `lane/items/`. The
  video or image file itself is not saved.
- JavaScript-only pages and PDFs are not read.
- Video is a transcript plus four frames, not a full viewing.
- Nothing is scheduled. Someone has to run the command.

## Tests

```sh
env -u PYTHONPATH -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1 \
  TMPDIR=/Users/vernon/.hermes/profiles/geoffrey/cache/scratch \
  /Users/vernon/.hermes/hermes-agent/venv/bin/python3 -B -m unittest discover -s tests
```

Lane tests are `tests/test_lane_*.py` and use fakes only (no network, no X, no
model calls).
