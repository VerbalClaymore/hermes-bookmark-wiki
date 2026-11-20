"""Offline tests for lean lane argument registration and dispatch."""
import argparse
import unittest
from unittest.mock import patch

from bookmark_wiki.lane import cli


class LaneCliTests(unittest.TestCase):
    def test_prune_orphans_defaults_to_preview(self):
        parser = argparse.ArgumentParser()
        cli.register(parser.add_subparsers(dest='command', required=True))
        for extra, expected in (([], False), (['--apply'], True)):
            args = parser.parse_args(['lane', 'prune-orphans', '--vault', '/v'] + extra)
            with patch.object(cli.pipeline, 'prune_orphans', return_value={}) as prune:
                cli.dispatch(args)
            prune.assert_called_once_with('/v', apply=expected)

    def test_newer_than_sync_only(self):
        parser = argparse.ArgumentParser()
        cli.register(parser.add_subparsers(dest='command', required=True))
        args = parser.parse_args(['lane', 'sync', '--vault', '/v', '--newer-than', '2026-11-16'])
        with patch.object(cli.pipeline, 'sync', return_value={}) as sync:
            cli.dispatch(args)
        self.assertEqual(sync.call_args.kwargs['newer_than'], '2026-11-16')
        with self.assertRaises(SystemExit):
            parser.parse_args(['lane', 'status', '--vault', '/v', '--newer-than', '2026-11-16'])

    def test_unbookmark_parser_and_dispatch(self):
        parser = argparse.ArgumentParser()
        cli.register(parser.add_subparsers(dest='command', required=True))
        args = parser.parse_args(['lane', 'unbookmark', '--vault', '/v', '--limit', '3',
                                  '--dry-run', '--include-skipped'])
        self.assertEqual((args.command, args.lane_action), ('lane', 'unbookmark'))
        with patch.object(cli.unbookmark, 'run', return_value={'ok': True}) as run:
            self.assertEqual(cli.dispatch(args), {'ok': True})
        run.assert_called_once_with('/v', limit=3, dry_run=True, include_skipped=True)


if __name__ == '__main__':
    unittest.main()
