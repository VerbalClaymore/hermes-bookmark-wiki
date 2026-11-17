"""Read-only Bird subprocess adapter."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile

ALLOWED = {"whoami", "bookmarks", "read", "thread", "replies"}
FORBIDDEN_ENV = ("AUTH_TOKEN", "CT0", "TWITTER_AUTH_TOKEN", "TWITTER_CT0")
MAX_STDOUT = 32 * 1024 * 1024


class BirdError(RuntimeError):
    pass


class Bird:
    def __init__(self, config: dict, runner=subprocess.run):
        self.config = config
        self.runner = runner
        self.last_decode_repaired = False
        self.decode_repaired_count = 0

    def run(self, action: str, args: list[str], *, profile: str) -> str:
        self.last_decode_repaired = False
        if action not in ALLOWED:
            raise ValueError("Bird command is not read-only")
        env = os.environ.copy()
        for name in FORBIDDEN_ENV:
            env.pop(name, None)
        argv = self.config["argv"] + ["--cookie-source", "chrome", "--chrome-profile", profile]
        if action == "whoami":
            argv += ["--plain"]
        argv += [action] + args
        try:
            with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
                result = self.runner(argv, env=env, stdout=stdout, stderr=stderr, timeout=180, check=False)
                if result.returncode:
                    raise BirdError("BIRD_FAILED: Bird returned a failure")
                stdout.seek(0)
                data = stdout.read(MAX_STDOUT + 1)
                if len(data) > MAX_STDOUT:
                    raise BirdError("BIRD_FAILED: Bird stdout exceeds 32 MB")
                try:
                    return data.decode("utf-8")
                except UnicodeDecodeError:
                    self.last_decode_repaired = True
                    self.decode_repaired_count += 1
                    return data.decode("utf-8", errors="replace")
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BirdError("BIRD_FAILED: Bird invocation failed") from exc

    def json(self, action: str, args: list[str], *, profile: str):
        try:
            return json.loads(self.run(action, args, profile=profile))
        except json.JSONDecodeError as exc:
            raise BirdError("BIRD_FAILED: invalid Bird JSON") from exc
