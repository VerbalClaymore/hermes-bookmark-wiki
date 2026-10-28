"""JSON-only command line interface for bookmark_wiki."""

from __future__ import annotations

import argparse
import json
import sys

from . import archive, retrieval


class ParseFailure(Exception):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ParseFailure(message)


def _parser():
    parser = Parser(prog="python3 -m bookmark_wiki", add_help=True)
    sub = parser.add_subparsers(dest="command")
    update = sub.add_parser("update")
    update.add_argument("--vault", required=True)
    update.add_argument("--snapshot", required=True)
    update.add_argument("--raw-hashes", required=True)
    status = sub.add_parser("status")
    status.add_argument("--vault", required=True)
    search = sub.add_parser("search")
    search.add_argument("--vault", required=True)
    search.add_argument("--query", required=True)
    search.add_argument("--limit", default="8")
    search.add_argument("--excerpt-chars", default="600")
    intake = sub.add_parser("intake")
    intake.add_argument("--vault", required=True)
    intake.add_argument("--selection", required=True)
    intake.add_argument("--output-root", required=True)
    show = sub.add_parser("show")
    show.add_argument("--vault", required=True)
    show.add_argument("--id", required=True)
    learning = sub.add_parser("learning")
    actions = learning.add_subparsers(dest="learning_action", required=True)
    for action in ("init", "apply", "status", "check"):
        command = actions.add_parser(action)
        command.add_argument("--vault", required=True)
        if action == "apply": command.add_argument("--input", required=True)
        if action == "check": command.add_argument("--batch-id", required=True)
    bulk = sub.add_parser("bulk")
    bulk_actions = bulk.add_subparsers(dest="bulk_action", required=True)
    for action in ("prepare", "apply", "handoffs", "publish", "status", "digest"):
        command = bulk_actions.add_parser(action)
        command.add_argument("--vault", required=True)
        if action == "prepare": command.add_argument("--limit", default="50")
        if action in ("apply", "publish"): command.add_argument("--input", required=True)
        if action in ("handoffs", "digest"): command.add_argument("--batch-id", required=True)
        if action == "status": command.add_argument("--batch-id")
    return parser


def _int_arg(value):
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise retrieval.InvalidArgument("invalid integer") from exc


def _envelope(status, command, data=None, error=None):
    body = {"schema_version": 1, "status": status, "command": command}
    if data is not None:
        body["data"] = data
    if error is not None:
        body["error"] = {"code": error, "message": _fixed_message(error)}
    return body


def _fixed_message(code):
    return {
        "INPUT_TOO_LARGE": "Input exceeds the permitted size.",
        "UNSUPPORTED_SCHEMA": "Unsupported learning schema.",
        "INVALID_TRANSITION": "Learning transition is not permitted.",
        "SOURCE_CHANGED": "Source changed during export.",
        "INVALID_ARGUMENT": "Invalid argument.",
        "INVALID_INPUT": "Invalid input.",
        "INTEGRITY_ERROR": "Input integrity check failed.",
        "LOCKED": "Update is already in progress.",
        "NOT_INITIALIZED": "BookmarkWiki is not initialized.",
        "MISSING_MANIFEST": "Authoritative manifest is missing.",
        "CORRUPT_MANIFEST": "Authoritative manifest is corrupt.",
        "STALE_INDEX": "Search index is stale.",
        "NOT_FOUND": "Requested source was not found.",
        "IO_ERROR": "I/O operation failed.",
    }.get(code, "Operation failed.")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    known_commands = {"update", "status", "search", "show", "intake", "learning", "bulk"}
    command = argv[0] if argv and argv[0] in known_commands else "unknown"
    try:
        args = _parser().parse_args(argv)
        if not args.command:
            raise ParseFailure("command required")
        command = args.command
        if args.command == "update":
            data = archive.update(args.vault, args.snapshot, args.raw_hashes)
            result = _envelope("ok", command, data=data)
            exit_code = 0
        elif args.command == "status":
            result = _envelope("ok", command, data=retrieval.status(args.vault))
            exit_code = 0
        elif args.command == "search":
            data = retrieval.search(args.vault, args.query, _int_arg(args.limit), _int_arg(args.excerpt_chars))
            result = _envelope("not_found" if not data["results"] else "ok", command, data=data)
            exit_code = 0
        elif args.command == "intake":
            from . import library_intake
            data = library_intake.intake(args.vault, args.selection, args.output_root)
            result = _envelope("ok", command, data=data)
            exit_code = 0
        elif args.command == "show":
            result = _envelope("ok", command, data=retrieval.show(args.vault, args.id))
            exit_code = 0
        elif args.command == "bulk":
            from . import bulk
            action = args.bulk_action
            if action == "prepare": data = bulk.prepare(args.vault, _int_arg(args.limit))
            elif action == "apply": data = bulk.apply(args.vault, args.input)
            elif action == "handoffs": data = bulk.handoffs(args.vault, args.batch_id)
            elif action == "publish": data = bulk.publish(args.vault, args.input)
            elif action == "status": data = bulk.status(args.vault, args.batch_id)
            else: data = bulk.digest_report(args.vault, args.batch_id)
            result = _envelope("ok", command, data=data)
            exit_code = 0
        elif args.command == "learning":
            from . import learning
            action = args.learning_action
            if action == "init": data = learning.init(args.vault)
            elif action == "apply": data = learning.apply(args.vault, args.input)
            elif action == "status": data = learning.status(args.vault)
            else: data = learning.check(args.vault, args.batch_id)
            result = _envelope("ok", command, data=data)
            exit_code = 0
        else:
            raise ParseFailure("unknown command")
    except ParseFailure:
        result = _envelope("error", command, error="INVALID_ARGUMENT")
        exit_code = 2
    except __import__("bookmark_wiki.learning", fromlist=["LearningError"]).LearningError as exc:
        result = _envelope("error", command, error=exc.code)
        exit_code = 2
    except __import__("bookmark_wiki.library_intake", fromlist=["IntakeError"]).IntakeError as exc:
        result = _envelope("error", command, error=exc.code)
        exit_code = 2
    except archive.BookmarkWikiError as exc:
        result = _envelope("error", command, error=exc.code)
        exit_code = 2
    except retrieval.InvalidArgument as exc:
        result = _envelope("error", command, error=exc.code)
        exit_code = 2
    except Exception:
        # The public boundary intentionally emits no exception text or paths.
        result = _envelope("error", command, error="IO_ERROR")
        exit_code = 2
    sys.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    return exit_code
