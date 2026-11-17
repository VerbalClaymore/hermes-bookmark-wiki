"""Capture and group read-only Bird bookmarks."""

from __future__ import annotations

import json
from pathlib import Path
import re

from .bird import BirdError
from .store import Store, tweet_id


def manifest_sources(vault: str | Path) -> dict:
    path = Path(vault) / "manifest.json"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as stream:
        manifest = json.load(stream)
    sources = manifest.get("sources")
    if not isinstance(sources, dict):
        raise ValueError("invalid manifest sources")
    return sources


def groups(tweets: list[dict]) -> list[tuple[str, list[dict]]]:
    grouped: dict[str, list[dict]] = {}
    for tweet in tweets:
        if not isinstance(tweet, dict):
            raise ValueError("invalid bookmark")
        item_id = tweet_id(str(tweet.get("id", "")))
        root = tweet.get("threadRootId") or tweet.get("conversationId") or item_id
        root = tweet_id(str(root))
        grouped.setdefault(root, []).append(tweet)
    results = []
    for root, members in grouped.items():
        explicit = next((str(t["threadRootId"]) for t in members if t.get("threadRootId")), None)
        chosen = tweet_id(explicit) if explicit else min((str(t["id"]) for t in members), key=int)
        results.append((chosen, members))
    return results


def capture(vault: str | Path, store: Store, config: dict, bird, *, metadata=None) -> list[str]:
    profile = config["bird"]["capture_profile"]
    expected = config["bird"]["capture_user_id"]
    identity = bird.run("whoami", [], profile=profile)
    matches = re.findall(r"(?m)^\s*user_id:\s*(\d+)\s*$", identity)
    if matches != [expected]:
        raise BirdError("CAPTURE_ACCOUNT_MISMATCH: capture account not verified")
    count = config["limits"]["capture_count"]
    known = set(manifest_sources(vault))
    for row in store.db.execute("SELECT source_id, tweet_id, stage FROM items"):
        known.add(row["source_id"])
        if row["stage"] is None and not store.artifact(row["tweet_id"], "capture").exists():
            # An explicit retry from capture removes this artifact until refresh.
            continue
        try:
            artifact = store.read_artifact(row["tweet_id"], "capture")
            known.update("x:" + str(t["id"]) for t in artifact.get("tweets", []))
        except (OSError, ValueError, KeyError, TypeError):
            # Do not silently continue if existing deduplication state is corrupt.
            raise ValueError("invalid existing capture") from None
    added = []
    seen_groups = set()
    seen_cursors = set()
    cursor = None
    for page in range(60):
        args = ["-n", "20", "--all", "--max-pages", "1",
                "--author-chain", "--thread-meta", "--json"]
        if cursor is not None:
            args += ["--cursor", cursor]
        try:
            try:
                result = bird.json("bookmarks", args, profile=profile)
            finally:
                if metadata is not None and getattr(bird, "last_decode_repaired", False):
                    metadata["decode_repaired"] = True
            if isinstance(result, list):
                tweets, next_cursor = result, None
            elif isinstance(result, dict) and isinstance(result.get("tweets"), list):
                tweets, next_cursor = result["tweets"], result.get("nextCursor")
                if next_cursor is not None and not isinstance(next_cursor, str):
                    raise BirdError("BIRD_FAILED: invalid bookmarks cursor")
            else:
                raise BirdError("BIRD_FAILED: invalid bookmarks output")
            try:
                page_groups = groups(tweets)
            except (ValueError, KeyError, TypeError) as exc:
                raise BirdError("BIRD_FAILED: invalid bookmarks output") from exc
        except BirdError as exc:
            if not page:
                raise
            if metadata is not None:
                metadata["capture_warning"] = " ".join(str(exc).split())[:400]
            break
        if not page_groups:
            break
        page_all_known = all(
            known.intersection({"x:" + root} | {"x:" + str(t["id"]) for t in members})
            for root, members in page_groups
        )
        for root, members in page_groups:
            if root in seen_groups:
                continue
            seen_groups.add(root)
            ids = {"x:" + root} | {"x:" + str(t["id"]) for t in members}
            if not known.intersection(ids):
                record = {"source_id": "x:" + root, "tweet_id": root,
                          "captured_at": store.clock().isoformat(), "tweets": members}
                if store.put_capture(record):
                    added.append(record["source_id"])
                    known.update(ids)
            if len(seen_groups) >= count:
                break
        if len(seen_groups) >= count or page_all_known or not next_cursor or next_cursor in seen_cursors:
            break
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    return added
