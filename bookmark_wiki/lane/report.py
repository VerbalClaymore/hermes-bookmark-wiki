"""Append human-readable per-item sync reports."""

from __future__ import annotations

from pathlib import Path

from . import store


def _text(value):
    return " ".join(str(value).split())


def append(vault: Path, entries: list[dict], clock=store.utc_now) -> Path:
    now = clock()
    path = Path(vault) / "lane" / "reports" / (now.date().isoformat() + ".md")
    blocks = []
    for entry in entries:
        triage = entry.get("triage") or {}
        note = entry.get("note") or {}
        bundle = entry.get("bundle") or {}
        delivery = entry.get("delivery") or {}
        title = entry.get("title") or note.get("title") or triage.get("core", {}).get("title") or entry.get("source_id") or "Untitled"
        lines = ["## " + _text(title)]
        if entry.get("source_id"):
            lines.append("- Source: " + _text(entry["source_id"]))
        value = entry.get("value") or triage.get("value")
        if value:
            lines.append("- Value: " + _text(value))
        found = entry.get("where_found") or triage.get("core", {}).get("where_found")
        if found:
            lines.append("- Found in: " + _text(found))
        wiki = delivery.get("wiki")
        if wiki:
            lines.append("- Wiki: " + _text(wiki.get("path", "")))
        for bot in delivery.get("bots", []):
            line = "- Bot: " + _text(bot.get("profile", "")) + " / " + _text(bot.get("topic_slug", ""))
            if bot.get("reference_path"):
                line += " (" + _text(bot["reference_path"]) + ")"
            run = bot.get("bot_run")
            if run is not None:
                outcome = run.get("error") or run.get("answer") or "no answer"
                line += "; run: " + _text(outcome)
                if run.get("memory_reverted"):
                    line += "; memory restored"
                if run.get("changed_paths"):
                    line += "; changed: " + ", ".join(map(_text, run["changed_paths"]))
            else:
                line += "; run: not run"
            lines.append(line)
        limits = entry.get("limits")
        if limits is None:
            limits = note.get("limits", bundle.get("limits", []))
        for limit in limits or []:
            if isinstance(limit, dict):
                limit = ": ".join(_text(limit[key]) for key in ("scope", "reason") if limit.get(key))
            lines.append("- Limit: " + _text(limit))
        reason = entry.get("error") or entry.get("skip_reason") or triage.get("skip_reason")
        if reason:
            lines.append("- Reason: " + _text(reason))
        if entry.get("status"):
            lines.append("- Status: " + _text(entry["status"]))
        blocks.append("\n".join(lines) + "\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.resolve() != path.absolute():
        raise ValueError("unsafe symlink in report path")
    with path.open("a", encoding="utf-8", newline="") as stream:
        if path.stat().st_size == 0:
            stream.write("# Lean lane report " + now.date().isoformat() + "\n")
        for block in blocks:
            stream.write("\n" + block)
    return path
