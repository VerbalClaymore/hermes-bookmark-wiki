"""Bird subprocess adapter with an explicit gated-removal operation."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile

from .store import tweet_id

ALLOWED = {"whoami", "bookmarks", "read", "thread", "replies"}
FORBIDDEN_ENV = ("AUTH_TOKEN", "CT0", "TWITTER_AUTH_TOKEN", "TWITTER_CT0")
MAX_STDOUT = 32 * 1024 * 1024


class BirdError(RuntimeError):
    pass


def _repair_control_chars(value):
    if isinstance(value, str):
        return ''.join('\ufffd' if ord(char) < 32 and char not in '\n\r\t' else char
                       for char in value)
    if isinstance(value, list):
        return [_repair_control_chars(item) for item in value]
    if isinstance(value, dict):
        return {key: _repair_control_chars(item) for key, item in value.items()}
    return value


class Bird:
    def __init__(self, config: dict, runner=subprocess.run):
        self.config = config
        self.runner = runner
        self.last_decode_repaired = False
        self.decode_repaired_count = 0

    def run(self, action: str, args: list[str], *, profile: str) -> str:
        if action not in ALLOWED:
            raise ValueError("Bird command is not read-only")
        return self._invoke(action, args, profile=profile)

    def unbookmark(self, ident: str, *, profile: str) -> str:
        return self._invoke("unbookmark", [tweet_id(ident)], profile=profile)

    def _invoke(self, action: str, args: list[str], *, profile: str) -> str:
        self.last_decode_repaired = False
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
        text = self.run(action, args, profile=profile)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            try:
                result = json.loads(text, strict=False)
            except json.JSONDecodeError as exc:
                raise BirdError("BIRD_FAILED: invalid Bird JSON") from exc
            if not self.last_decode_repaired:
                self.last_decode_repaired = True
                self.decode_repaired_count += 1
            return _repair_control_chars(result)
