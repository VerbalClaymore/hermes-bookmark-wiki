"""Argument registration and dispatch for the lean lane."""
from . import pipeline


def register(subparsers):
    lane = subparsers.add_parser('lane')
    actions = lane.add_subparsers(dest='lane_action', required=True)
    for action in ('init', 'sync', 'seed', 'status', 'show', 'retry'):
        command = actions.add_parser(action)
        command.add_argument('--vault', required=True)
        if action == 'sync':
            command.add_argument('--limit', type=int)
            command.add_argument('--capture-count', type=int)
            command.add_argument('--no-capture', action='store_true')
            command.add_argument('--no-bot-runs', action='store_true')
            command.add_argument('--dry-run', action='store_true')
        if action == 'seed':
            choice = command.add_mutually_exclusive_group(required=True)
            choice.add_argument('--ids')
            choice.add_argument('--from-archive', action='store_true')
            command.add_argument('--limit', type=int)
            command.add_argument('--newest-first', action='store_true')
        if action in ('show', 'retry'):
            command.add_argument('--id', required=True)
        if action == 'retry':
            command.add_argument('--from-stage', choices=('capture', 'resolve', 'triage', 'write', 'deliver'), default='capture')


def dispatch(args):
    if args.lane_action == 'init':
        return pipeline.init(args.vault)
    if args.lane_action == 'sync':
        return pipeline.sync(args.vault, limit=args.limit, capture_count=args.capture_count, capture=not args.no_capture,
                             bot_runs=not args.no_bot_runs, dry_run=args.dry_run)
    if args.lane_action == 'seed':
        return pipeline.seed(args.vault, ids=args.ids, from_archive=args.from_archive,
                             limit=args.limit, newest_first=args.newest_first)
    if args.lane_action == 'status':
        return pipeline.status(args.vault)
    if args.lane_action == 'show':
        return pipeline.show(args.vault, args.id)
    return pipeline.retry(args.vault, args.id, from_stage=args.from_stage)
