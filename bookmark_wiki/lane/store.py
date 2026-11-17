"""Lane-local state and atomic artifacts."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import uuid

STAGES = ("capture", "resolve", "triage", "write", "deliver")
ARTIFACTS = dict(zip(STAGES, ("capture.json", "bundle.json", "triage.json", "note.json", "delivery.json")))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def tweet_id(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9]+", value) is None:
        raise ValueError("invalid tweet id")
    return value


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".artifact-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class Store:
    def __init__(self, vault: str | Path, clock=utc_now):
        self.root = Path(vault) / "lane"
        self.root.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.db = sqlite3.connect(self.root / "lane.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS items (
            source_id TEXT PRIMARY KEY, tweet_id TEXT NOT NULL, stage TEXT,
            status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
            error TEXT, value TEXT, destinations_json TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        self.db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def item_dir(self, item_id: str) -> Path:
        return self.root / "items" / tweet_id(item_id)

    def artifact(self, item_id: str, stage: str) -> Path:
        return self.item_dir(item_id) / ARTIFACTS[stage]

    def put_capture(self, capture: dict) -> bool:
        item_id = tweet_id(capture["tweet_id"])
        if capture["source_id"] != "x:" + item_id:
            raise ValueError("invalid source id")
        if self.db.execute("SELECT 1 FROM items WHERE source_id = ?", (capture["source_id"],)).fetchone():
            return False
        atomic_json(self.artifact(item_id, "capture"), capture)
        now = self.clock().isoformat()
        self.db.execute("INSERT INTO items (source_id, tweet_id, stage, status, created_at, updated_at) VALUES (?, ?, 'capture', 'pending', ?, ?)", (capture["source_id"], item_id, now, now))
        self.db.commit()
        return True

    def read_artifact(self, item_id: str, stage: str) -> dict:
        with self.artifact(item_id, stage).open(encoding="utf-8") as stream:
            value = json.load(stream)
        if not isinstance(value, dict):
            raise ValueError("invalid artifact")
        return value

    def write_artifact(self, item_id: str, stage: str, value: dict) -> None:
        if not isinstance(value, dict):
            raise ValueError("invalid artifact")
        atomic_json(self.artifact(item_id, stage), value)
        self.db.execute("UPDATE items SET stage = ?, updated_at = ? WHERE tweet_id = ?", (stage, self.clock().isoformat(), tweet_id(item_id)))
        self.db.commit()

    @contextmanager
    def locked(self):
        """Acquire a lane-only lock, taking over a lock older than two hours."""
        path = self.root / "lane.lock"
        token = uuid.uuid4().hex
        for _ in range(3):
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump({"token": token, "created_at": self.clock().timestamp()}, stream)
                break
            except FileExistsError:
                try:
                    with path.open(encoding="utf-8") as stream:
                        old = json.load(stream)
                    if self.clock().timestamp() - float(old["created_at"]) <= 7200:
                        raise RuntimeError("LOCKED: lane sync already running")
                    # Verify identity before removal; another process may have replaced it.
                    with path.open(encoding="utf-8") as stream:
                        if json.load(stream) != old:
                            continue
                    path.unlink()
                except FileNotFoundError:
                    continue
                except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                    raise RuntimeError("LOCKED: invalid lane lock") from None
        else:
            raise RuntimeError("LOCKED: cannot acquire lane lock")
        try:
            yield
        finally:
            try:
                with path.open(encoding="utf-8") as stream:
                    current = json.load(stream)
                if current.get("token") == token:
                    path.unlink()
            except FileNotFoundError:
                pass
