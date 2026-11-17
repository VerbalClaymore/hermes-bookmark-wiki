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
3. **Triage.** Decides the value (`high`, `normal`, `low`, `keepsake`, `skip`),
   whether it goes to the wiki, which Hermes bots benefit (none, one, or
   several), and whether it is new, partly new, or a duplicate of an earlier
   note (see "Duplicates").
4. **Write.** Produces one source-cited note. For a partly new bookmark the
   note covers only what is new and names the note it builds on.
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
| Catch up to the old archive boundary | `scripts/lane-sync.sh --catch-up` |
| Process only posts dated on or after a day | `scripts/lane-sync.sh --no-capture --newer-than 2026-11-01` |
| Redo a skipped item as a possible keepsake | `LANE retry --id <tweet_id> --from-stage triage` |
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

| Saved meme and clip files | `/Users/vernon/Documents/BookmarkWiki/media/keepsakes/` |
| Keepsake list | `/Users/vernon/Documents/BookmarkWiki/wiki/keepsakes.md` |
| What a bot topic already learned from | `~/.hermes/profiles/<bot>/skills/bookmarks/<topic>/sources.md` |
| Removal log | `.../lane/unbookmark-log.jsonl` |

Item statuses: `done` (delivered), `kept` (keepsake: media saved, no note),
`duplicate` (nothing new; the earlier note got a "Seen again" line),
`skipped` (triage found nothing worth keeping), `failed` (see `error`; retried automatically up to
`limits.max_attempts`, then left for `retry`).

## Where a bookmark ends up

| Bookmark | Result |
| --- | --- |
| Knowledge that changes how a bot works | Wiki note, reference file in that bot's topic skill, and a bot run when relevance is high |
| Knowledge with no fitting bot | Wiki note only. Nothing worth keeping is dropped for lack of an owner |
| No knowledge, but an image, GIF or video (meme, clip, joke) | Keepsake: the media file is saved and listed in `wiki/keepsakes.md`. No note, no bots |
| Same sources as an earlier note and nothing new | Duplicate: no new note; the earlier wiki note gets a "Seen again" line |
| Overlaps an earlier note but adds something | A short note with only the new part, marked `Builds on:` |
| Nothing recoverable and no media | Skipped |

## Duplicates

The lane keeps a ledger (table `sources` in `lane.sqlite3`) of every source a
note was built from: links, GitHub repositories, arXiv papers, YouTube videos,
quoted and parent posts. Before triage, a new bookmark is compared against it.

- `duplicate` is only allowed when the bookmark shares a source with an earlier
  note and brings no new readable source of its own. Similar wording alone is
  never enough; that case becomes a partial note instead.
- Each bot topic has a `sources.md` listing what that topic already absorbed.
  Bot runs are told to consult it and not to re-add lessons.
- To overrule a duplicate verdict: `LANE retry --id <tweet_id> --from-stage triage`.

## Catching up on older bookmarks

Normal sync stops at the first page of bookmarks it already knows. To reach
bookmarks saved after the old archive export (2026-09-23) that the lane has not
seen:

```sh
scripts/lane-sync.sh --catch-up
```

It pages back until a whole page is already in the old archive (at most 800
bookmarks), queues what is new, and processes 25. Keep running
`scripts/lane-sync.sh --no-capture` until `LANE status` shows nothing pending.
Bookmarks that are in the old archive are never queued by capture; those are
reached with `LANE seed --from-archive`.

## Removing synced bookmarks from X (owner only)

`lane unbookmark` is the only command that changes anything on X. **Agents must
never run it.** It is not part of sync and has no unattended mode.

```sh
LANE unbookmark --dry-run          # list what would be removed, change nothing
LANE unbookmark --limit 25         # the real thing, in the owner's own terminal
```

The real run requires an interactive terminal, checks that the Chrome profile
is the owner's account, lists every bookmark it will remove, and proceeds only
if the owner types `DELETE SYNCED BOOKMARKS` exactly. It removes only items
that are `done`, `kept` or `duplicate` and whose delivered files still exist,
one at a time, 25 per run by default (100 at most), stops at the first failure,
and logs every removal. Removal cannot be undone except by bookmarking the post
again by hand; the log holds the URLs.

## Rules for any agent operating this

- X access is read only. Never post, like, follow, or unbookmark. Sync only
  uses `whoami`, `bookmarks`, `read`, `thread` and `replies`. Never run
  `lane unbookmark`; only the owner does, by hand.
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

- Duplicates are detected through shared sources. Two posts that make the same
  point with different links and no shared quoted post are not recognized as
  duplicates; at best they become a partial note.
- Keepsakes save media from the bookmarked post, the author's thread and the
  quoted post. Media hosted outside X is not downloaded.
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
