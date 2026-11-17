"""Configuration for the lean lane."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

DEFAULTS = {
    "bird": {
        "argv": ["/Users/vernon/.bun/bin/bun", "/Users/vernon/.nvm/versions/node/v24.1.0/lib/node_modules/@steipete/bird/dist/cli.js"],
        "capture_profile": "Profile 1", "capture_user_id": "84692934", "read_profile": "Profile 2",
    },
    "llm": {
        "argv": ["hermes", "--ignore-rules", "-t", "", "-m", "gpt-6.1-terra", "-z"],
        "timeout_s": 180,
        "vision_argv": ["hermes", "--ignore-rules", "-t", "", "-m", "gpt-6.1-terra", "chat", "-Q", "--oneshot", "--image", "{image}", "-q"],
    },
    "hermes": {"home": "/Users/vernon/.hermes", "exclude_profiles": ["default", "geoffrey"], "bot_runs": True, "bot_run_timeout_s": 600},
    "limits": {"capture_count": 40, "links_per_item": 5, "link_bytes": 1500000, "link_chars_for_llm": 20000, "images_per_item": 4, "image_bytes": 8000000, "max_attempts": 3, "items_per_sync": 25},
    "media": {"max_video_seconds": 1200, "videos_per_item": 2, "frames_per_video": 4,
              "whisper_argv": ["/Users/vernon/whisper-venv/bin/whisper"],
              "whisper_model": "large-v3-turbo", "ffmpeg": "ffmpeg"},
}


def validate(config: dict) -> dict:
    """Reject malformed configuration before starting any external operation."""
    if not isinstance(config, dict) or set(config) != set(DEFAULTS):
        raise ValueError("invalid config sections")
    for section, defaults in DEFAULTS.items():
        values = config[section]
        if not isinstance(values, dict) or set(values) != set(defaults):
            raise ValueError(f"invalid {section} configuration")
        for key, default in defaults.items():
            value = values[key]
            if isinstance(default, bool):
                valid = isinstance(value, bool)
            elif isinstance(default, int):
                valid = type(value) is int and value > 0
            elif isinstance(default, list):
                valid = isinstance(value, list) and all(isinstance(v, str) for v in value)
                if key in {"argv", "vision_argv", "whisper_argv"}:
                    valid = valid and bool(value) and bool(value[0].strip())
            else:
                valid = isinstance(value, str) and bool(value.strip())
            if not valid:
                raise ValueError(f"invalid {section}.{key}")
    if config["limits"]["link_bytes"] > 2 * 1024 * 1024 or config["limits"]["image_bytes"] > 16 * 1024 * 1024:
        raise ValueError("fetch size exceeds safe_fetch bounds")
    return config


def init(vault: str | Path) -> Path:
    path = Path(vault) / "lane" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(copy.deepcopy(DEFAULTS), stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        pass
    return path


def load(vault: str | Path) -> dict:
    with (Path(vault) / "lane" / "config.json").open(encoding="utf-8") as stream:
        supplied = json.load(stream)
    if not isinstance(supplied, dict) or set(supplied) - set(DEFAULTS):
        raise ValueError("invalid config sections")
    merged = copy.deepcopy(DEFAULTS)
    for section, values in supplied.items():
        if not isinstance(values, dict) or set(values) - set(DEFAULTS[section]):
            raise ValueError(f"invalid {section} configuration")
        merged[section].update(values)
    return validate(merged)
