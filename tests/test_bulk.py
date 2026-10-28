"""§13 six frozen acceptance groups; real CLI, archive, SQLite and files."""
import copy
from contextlib import closing
import datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from bookmark_wiki import archive, learning
import test_archive as fixtures
import test_learning as legacy

ROOT = Path(__file__).resolve().parents[1]
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(ROOT))
canon = learning.canonical
identity = learning.identity


def ref(p):
    return {'path': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}


def write(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(canon(obj))
    return ref(p)


def cli(*args):
    return subprocess.run([sys.executable, '-B', '-m', 'bookmark_wiki', *map(str, args)],
                          cwd=ROOT, env=ENV, capture_output=True, text=True)


def inventory(root):
    return {str(p.relative_to(root)): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
            for p in root.rglob('*') if p.is_file() and not p.is_symlink()}


class Fixture:
    def __init__(self, base, count=4, text_size=0):
        self.base = base
        self.vault = base / 'vault'
        self.home = base / 'home'
        self.source = base / 'bridge'
        self.runtime = self.vault / 'state/learning-process-v1'
        self.inputs = self.runtime / 'inputs/bulk-v1'
        self.evidence = self.runtime / 'evidence/bulk-v1'
        self.inputs.mkdir(parents=True)
        self.evidence.mkdir(parents=True)
        self.home.mkdir()
        write(self.runtime / 'inputs/roots.json', {'schema_version': 1, 'hermes_home': str(self.home)})
        write(self.inputs / 'settings.json', {'schema_version': 1, 'hermes_python': str(Path(sys.executable).resolve()),
              'renderer_python': str(Path(sys.executable).resolve()), 'hermes_source': str(self.source)})
        package = self.source / 'hermes_cli'
        package.mkdir(parents=True)
        (package / '__init__.py').write_text('')
        (package / 'profiles.py').write_text('import os\nfrom pathlib import Path\n'
            'def list_profile_names():\n return sorted(p.name for p in (Path(os.environ["HERMES_HOME"])/"profiles").iterdir() if p.is_dir())\n'
            'def get_profile_dir(name):\n return Path(os.environ["HERMES_HOME"])/"profiles"/name\n')
        self.add_profile('alpha')
        self.add_profile('beta')
        self.authority = self.evidence / 'authority.md'
        self.authority.write_text('Coordinator approval: BOOKMARK-INTAKE-PHASE2 v1.3.1; independent review PASS; first run at most 50.\nSettings-SHA256: '+ref(self.inputs / 'settings.json')['sha256']+'\n')
        bookmarks = [fixtures.bookmark(str(i), str(100+i), 'Synthetic useful reference '+str(i)+' with literal grounded quotation.'+'x'*text_size) for i in range(count)]
        snapshot, hashes = fixtures.write_inputs(self.vault, fixtures.make_snapshot(bookmarks,
            categories=[{'id': 'c1', 'name': 'References', 'slug': 'references'}],
            assignments=[{'bookmarkId': str(i), 'categoryId': 'c1'} for i in range(count)]),
            {'Reference - '+str(100+i)+'.md': 'Captured synthetic raw reference.' for i in range(count)})
        archive.update(self.vault, snapshot, hashes)
        wiki = self.vault / 'wiki'
        wiki.mkdir()
        (wiki / 'index.md').write_text('# BookmarkWiki\n\nExisting catalog sentinel.\n')
        (wiki / 'old.md').write_text('# Old page\n\nUnrelated page sentinel.\n')
        (self.vault / 'reading-view.html').write_text('<style>body{color:#123}</style><script>/* source reveal sentinel */</script>')
        learning.init(self.vault)
        self.serial = 0

    def add_profile(self, name):
        p = self.home / 'profiles' / name
        p.mkdir(parents=True, exist_ok=True)
        (p / 'SOUL.md').write_text('Synthetic '+name+' specialty: useful reference knowledge.')
        return p

    def input(self, obj):
        self.serial += 1
        p = self.inputs / ('input-%04d.json' % self.serial)
        write(p, obj)
        return p

    def command(self, *args):
        p = cli('bulk', *args, '--vault', self.vault)
        if p.returncode:
            raise AssertionError((p.returncode, p.stdout, p.stderr))
        obj = json.loads(p.stdout)
        assert set(obj) == {'schema_version', 'command', 'status', 'data'}, obj
        assert obj['command'] == 'bulk' and not p.stderr, obj
        return obj['data']

    def prepare(self, limit=50):
        return self.command('prepare', '--limit', limit)

    def apply(self, kind, payload):
        return self.command('apply', '--input', self.input({'schema_version': 1, 'kind': kind, 'payload': payload}))

    def batch(self, prepared=None, agent_ids=(), owner='alpha', blocked_ids=(), register_only=False):
        prepared = prepared or self.prepare()
        self.packet_ref = prepared['packet']
        self.packet = json.loads(Path(prepared['packet']['path']).read_text())
        bid = self.apply('bulk_batch', {'version': 1, 'authority': ref(self.authority),
              'packet': prepared['packet'], 'roster': prepared['roster']})['event_id']
        self.routes = [{'source_id': r['source_id'], 'revision': r['current_revision'],
                        'lane': 'blocked' if r['source_id'] in blocked_ids else 'agent' if r['source_id'] in agent_ids else 'wiki',
                        'profile': owner if r['source_id'] in agent_ids and r['source_id'] not in blocked_ids else None,
                        'topic': 'References', 'reason': 'Grounded role-relative routing.'} for r in self.packet['records']]
        if not register_only:
            self.apply('bulk_route', {'batch_id': bid, 'items': self.routes})
        return bid

    def observe(self, bid, dispositions=None, status='done', task='t_00000001', retain_outputs=True, transform=None):
        group = self.command('handoffs', '--batch-id', bid)['groups'][0]
        intent = self.apply('bulk_intent', {'batch_id': bid, 'profile': group['profile'], 'item_keys': group['item_keys'],
                  'body_sha256': group['body_sha256'], 'attempt': 0, 'authority': ref(self.authority)})['event_id']
        attachment_root = self.home / 'kanban/boards/bookmark-skills/attachments' / task
        attachment_root.mkdir(parents=True)
        items = []
        for route in self.routes:
            if route['lane'] != 'agent':
                continue
            disposition = (dispositions or {}).get(route['source_id'], 'reference_saved')
            coverage = None
            if disposition == 'already_covered':
                skill = self.home / 'profiles/alpha/skills/current/SKILL.md'
                skill.parent.mkdir(parents=True, exist_ok=True)
                skill.write_text('# Coverage\n\nConsult for matching synthetic reference questions.\n')
                coverage = ref(skill)
            items.append({'source_id': route['source_id'], 'revision': route['revision'], 'disposition': disposition,
                          'reason': 'Actual synthetic assessment.', 'note': self.note(route['source_id'])['note'] if disposition == 'reference_saved' else None,
                          'coverage': coverage, 'section': 'Coverage' if coverage else None,
                          'trigger': 'matching synthetic reference questions' if coverage else None})
        if transform is not None:
            items = transform(items)
        outputs = []
        if status == 'done' and retain_outputs:
            (attachment_root / 'assessment.md').write_text('# Assessment\nVerified fixture source coverage.\n')
            outputs = [ref(attachment_root / 'assessment.md'), write(attachment_root / 'learning.json', {'schema_version': 1, 'items': items})]
        o = {'task_id': task, 'board': 'bookmark-skills', 'assignee': 'alpha', 'status': status,
             'observed_at': '2026-01-02T03:04:05Z', 'body_sha256': group['body_sha256']}
        self.observation = {**o, 'evidence': write(self.evidence / (task+'-observation.json'), {**o, 'attachments': outputs})}
        self.apply('bulk_observed', {'batch_id': bid, 'intent_id': intent, 'observation': self.observation})
        self.worker = outputs
        self.worker_items = items
        return intent

    def note(self, sid):
        r = next(r for r in self.packet['records'] if r['source_id'] == sid)
        return {'schema_version': 1, 'updated': '2026-01-02', 'note': {'title': 'Reference '+sid,
                'short_answer': 'The archived author describes a synthetic reference.',
                'explanation': [{'text': 'The source supports this bounded reference, not unseen linked content.', 'source_ids': [sid]}],
                'limits': ['Archived text only.'],
                'sources': [{'source_id': sid, 'revision': r['current_revision'], 'quote': 'Synthetic useful reference'}]}}

    def plan(self, bid, specs):
        from bookmark_wiki import bulk, wiki_publish
        ctx, events, batch = bulk.context_for_batch(self.vault, bid)
        entries = []
        for sid, kind in specs:
            env = self.note(sid)
            h = identity(env)
            file = write(self.evidence / 'notes' / (h+'.json'), env)
            path = (self.vault / 'wiki/topics' / ('BookmarkWiki_Backlog_'+h+'.md') if kind == 'wiki' else
                    self.home / 'profiles/alpha/skills/bookmark-library/references' / (h+'.md'))
            entries.append({'file': file, 'kind': kind, 'profile': None if kind == 'wiki' else 'alpha', 'path': str(path)})
        entries.sort(key=lambda n: n['path'])
        ids = sorted(sid for sid, kind in specs)
        coverage = [{'source_id': sid, 'note_sha256': identity(self.note(sid))} for sid in ids]
        review = write(self.evidence / ('publication-review-'+identity(coverage)+'.json'),
                       {'schema_version': 1, 'verdict': 'accepted', 'reviewed_by': 'geoffrey', 'source_ids': ids,
                        'reason': 'Coordinator inspected literal fixture quotes and explanatory coverage.',
                        'coverage': coverage, 'language_status': 'oriented_not_certified'})
        plan = {'schema_version': 1, 'batch_id': bid, 'review': review, 'notes': entries, 'changes': [], 'source_ids': ids}
        outputs = wiki_publish.outputs(ctx, plan, events)
        for path, data in sorted(outputs.items()):
            p = Path(path)
            content = self.evidence / 'staged' / (hashlib.sha256(data).hexdigest()+'.txt')
            content.parent.mkdir(parents=True, exist_ok=True)
            if not content.exists():
                content.write_bytes(data)
            plan['changes'].append({'path': path, 'before_sha256': ref(p)['sha256'] if p.exists() else None, 'content': ref(content)})
        return plan, self.input(plan)

    def publish(self, bid, specs):
        plan, path = self.plan(bid, specs)
        data = self.command('publish', '--input', path)
        return plan, path, data['transaction_id']

    def result(self, bid, sid, outcome, txn=None, plan=None, task='t_00000001'):
        route = next(r for r in self.routes if r['source_id'] == sid)
        profile = route['profile'] if outcome != 'wiki_published' else None
        task = task if profile else None
        if outcome == 'blocked' and task is None:
            profile = route['profile']
        item = next((i for i in getattr(self, 'worker_items', []) if i['source_id'] == sid), None)
        entry = next((n for n in (plan or {}).get('notes', []) if sid in [s['source_id'] for s in json.loads(Path(n['file']['path']).read_text())['note']['sources']]), None)
        artifact = ref(Path(entry['path'])) if entry else (item['coverage'] if item and outcome == 'already_covered' else None)
        review = {'schema_version': 1, 'verdict': 'accepted', 'reviewed_by': 'geoffrey', 'source_id': sid,
                  'revision': route['revision'], 'outcome': outcome, 'profile': profile, 'task_id': task,
                  'reason': 'Inspected actual fixture evidence or concrete missing context.',
                  'worker_artifacts': getattr(self, 'worker', []) if task else [],
                  'coverage': item['coverage'] if item and outcome == 'already_covered' else None,
                  'section': item['section'] if item and outcome == 'already_covered' else None,
                  'trigger': item['trigger'] if item and outcome == 'already_covered' else None,
                  'note_sha256': entry['file']['sha256'] if entry else None, 'evidence': [self.packet_ref]}
        if outcome == 'blocked':
            diagnosis = self.evidence / ('diagnosis-'+sid.replace(':','')+'.md')
            diagnosis.write_text('Synthetic blocker for '+sid+': selected archive contains captured post text only; '
                                 'required independent linked/context evidence is not present. No successful learning is claimed.')
            review['reason'] = 'Required independent linked/context evidence is absent from this synthetic archive.'
            review['evidence'] = [ref(diagnosis), self.observation['evidence'] if task else self.packet_ref]
        rp = write(self.evidence / ('result-review-'+identity(review)+'.json'), review)
        payload = {k: review[k] for k in ('source_id', 'revision', 'outcome', 'profile', 'task_id')}
        payload.update(batch_id=bid, artifact=artifact, review=rp, transaction_id=txn)
        return payload


class BulkTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=os.environ['TMPDIR'])
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)

    def fixture(self, count=4):
        return Fixture(self.base / ('fixture-'+str(len(list(self.base.iterdir())))), count)

    def reject(self, f, kind, payload):
        path = f.input({'schema_version': 1, 'kind': kind, 'payload': payload})
        before = inventory(f.base)
        p = cli('bulk', 'apply', '--vault', f.vault, '--input', path)
        self.assertEqual(p.returncode, 2, p.stdout)
        self.assertEqual(json.loads(p.stdout)['command'], 'bulk')
        self.assertEqual(inventory(f.base), before)

    def reject_nullability(self, f, payload):
        """Mutate complete otherwise-valid reviews before terminal acceptance."""
        q = json.loads(Path(payload['review']['path']).read_text())
        outcome = payload['outcome']
        installed = outcome in ('reference_saved', 'wiki_published')
        covered = outcome == 'already_covered'
        agent = outcome in ('reference_saved', 'already_covered', 'not_useful')
        cases = [('artifact', None if installed or covered else ref(f.authority), False),
                 ('transaction_id', None if installed else '0'*64, False),
                 ('coverage', None if covered else ref(f.authority), True),
                 ('section', None if covered else 'forbidden', True),
                 ('trigger', None if covered else 'forbidden', True),
                 ('note_sha256', None if installed else '0'*64, True)]
        if agent:
            cases += [('profile', None, False), ('task_id', None, False), ('worker_artifacts', [], True)]
        if outcome == 'wiki_published':
            cases += [('profile', 'alpha', False), ('task_id', 't_99999999', False),
                      ('worker_artifacts', [ref(f.authority)], True)]
        if outcome == 'blocked' and payload['task_id'] is None:
            cases += [('worker_artifacts', [ref(f.authority)], True)]
        for key, value, review_only in cases:
            with self.subTest(outcome=outcome, field=key):
                bad, review = copy.deepcopy(payload), copy.deepcopy(q)
                if review_only:
                    review[key] = value
                else:
                    bad[key] = value
                    if key in review:
                        review[key] = value
                bad['review'] = write(f.evidence / ('nullability-'+identity(review)+'.json'), review)
                self.reject(f, 'bulk_result', bad)

    def test_dynamic_roster_and_pending_selection(self):
        f = self.fixture(51)
        prep = f.prepare()
        self.assertEqual((prep['selected_count'], prep['archive_count']), (50, 51))
        selected_ids = [row['source_id'] for row in json.loads(Path(prep['packet']['path']).read_text())['records']]
        self.assertEqual(selected_ids, sorted('x:'+str(i) for i in range(100,151))[:50])
        roster = json.loads(Path(prep['roster']['path']).read_text())
        self.assertEqual([p['id'] for p in roster['profiles']], ['alpha', 'beta'])
        bid = f.batch(prep, ('x:100',))
        f.add_profile('future')
        f.add_profile('bad')
        (f.home / 'profiles/bad/SOUL.md').write_text('')
        next_prep = f.prepare()
        self.assertEqual((next_prep['selected_count'], next_prep['resume_count']), (1, 50))
        rows = json.loads(Path(next_prep['roster']['path']).read_text())['profiles']
        self.assertTrue(next(p for p in rows if p['id'] == 'future')['eligible'])
        self.assertFalse(next(p for p in rows if p['id'] == 'bad')['eligible'])
        (f.home / 'profiles/alpha/SOUL.md').write_text('Changed role')
        p = cli('bulk', 'handoffs', '--vault', f.vault, '--batch-id', bid)
        self.assertEqual(p.returncode, 2)
        self.assertEqual(json.loads(p.stdout)['error']['code'], 'INTEGRITY_ERROR')
        (f.home / 'profiles/alpha/SOUL.md').unlink()
        self.assertEqual(cli('bulk', 'handoffs', '--vault', f.vault, '--batch-id', bid).returncode, 2)
        past = self.fixture(1)
        frozen = past.prepare()
        captured = json.loads(Path(frozen['packet']['path']).read_text())['records'][0]
        c = legacy.LearningContractTests('runTest')
        c.setUp()
        self.addCleanup(c.doCleanups)
        c.base, c.vault, c.runtime = past.base, past.vault, past.runtime
        c.inputs, c.evidence, c.home, c.db = past.runtime / 'inputs', past.runtime / 'evidence', past.home, past.runtime / 'ledger.sqlite3'
        authority = c.evidence / 'legacy-authority.md'
        authority.write_text('Synthetic historical import t_00000001.')
        c.authority_file = authority
        c.authority = {'record': ref(authority), 'scope': 'historical_import'}
        registration = c.registration(historical=True, revision=captured['current_revision'])
        registration['targets'][0]['scope_read'] = ['archived post text']
        tid = learning.target_id(registration['targets'][0])
        registration['links'][0]['target_id'] = tid
        registration['destinations'][0]['target_id'] = tid
        oldb = c.apply('register_batch', registration)['event_id']
        olddid = learning.destination_id(registration['destinations'][0])
        c.observe(oldb, [olddid], intent=None)
        receipt = c.receipt(oldb, olddid, disposition='already_covered')
        c.apply('receipt_accepted', receipt)
        self.assertEqual(past.prepare()['selected_count'], 0)
        self.assertEqual(past.prepare()['resume_count'], 0)
        self.reject(past, 'bulk_batch', {'version': 1, 'authority': ref(past.authority), 'packet': frozen['packet'], 'roster': frozen['roster']})
        Path(receipt['receipt']['path']).unlink()
        self.assertEqual(past.prepare()['selected_count'], 0)
        self.assertEqual(past.prepare()['resume_count'], 1)
        self.assertEqual(past.command('status')['counts']['needs_reconciliation'], 1)

    def test_routes_handoffs_and_receipts(self):
        f = self.fixture(13)
        bid = f.batch(agent_ids=tuple('x:'+str(i) for i in range(100, 113)), register_only=True)
        for invalid in (f.routes[:-1], f.routes[:-1]+[f.routes[0]],
                        [{**f.routes[0], 'source_id': 'x:999'}]+f.routes[1:],
                        [{**f.routes[0], 'profile': 'not-installed'}]+f.routes[1:]):
            self.reject(f, 'bulk_route', {'batch_id': bid, 'items': invalid})
        f.apply('bulk_route', {'batch_id': bid, 'items': f.routes})
        mixed = self.fixture(3)
        mixed_batch = mixed.batch(agent_ids=('x:100',), blocked_ids=('x:102',))
        self.assertEqual([row['lane'] for row in mixed.routes], ['agent', 'wiki', 'blocked'])
        mixed.apply('bulk_result', mixed.result(mixed_batch, 'x:102', 'blocked', task=None))
        self.assertEqual(mixed.command('status', '--batch-id', mixed_batch)['counts']['blocked'], 1)
        groups = f.command('handoffs', '--batch-id', bid)['groups']
        self.assertEqual([len(g['item_keys']) for g in groups], [10, 3])
        byte_fixture = Fixture(self.base / 'text-ceiling', 3, text_size=60000)
        byte_batch = byte_fixture.batch(agent_ids=('x:100', 'x:101', 'x:102'))
        byte_groups = byte_fixture.command('handoffs', '--batch-id', byte_batch)['groups']
        self.assertEqual([len(g['item_keys']) for g in byte_groups], [2, 1])
        concurrent = self.fixture(21)
        concurrent_batch = concurrent.batch(agent_ids=tuple('x:'+str(i) for i in range(100,121)))
        concurrent_groups = concurrent.command('handoffs', '--batch-id', concurrent_batch)['groups']
        for index, group in enumerate(concurrent_groups):
            event = {'batch_id': concurrent_batch, 'profile': group['profile'], 'item_keys': group['item_keys'],
                     'body_sha256': group['body_sha256'], 'attempt': 0, 'authority': ref(concurrent.authority)}
            if index < 2:
                concurrent.apply('bulk_intent', event)
            else:
                self.reject(concurrent, 'bulk_intent', event)
        self.reject(f, 'bulk_route', {'batch_id': bid, 'items': f.routes[:-1]})
        self.reject(f, 'bulk_route', {'batch_id': bid, 'items': [{**r, 'profile': 'not-installed'} for r in f.routes]})
        uncertain = self.fixture(1)
        ub = uncertain.batch(agent_ids=('x:100',))
        ug = uncertain.command('handoffs', '--batch-id', ub)['groups'][0]
        original_intent = {'batch_id': ub, 'profile': 'alpha', 'item_keys': ug['item_keys'],
                           'body_sha256': ug['body_sha256'], 'attempt': 0, 'authority': ref(uncertain.authority)}
        uncertain.apply('bulk_intent', original_intent)
        self.reject(uncertain, 'bulk_intent', {**original_intent, 'attempt': 1})
        (uncertain.home / 'profiles/alpha/SOUL.md').write_text('Changed before native create')
        self.assertEqual(cli('bulk', 'handoffs', '--vault', uncertain.vault, '--batch-id', ub).returncode, 2)
        f = self.fixture(4)
        bid = f.batch(agent_ids=('x:100', 'x:101', 'x:102', 'x:103'))
        intent = f.observe(bid, {'x:101': 'already_covered', 'x:102': 'not_useful', 'x:103': 'blocked'})
        group = f.command('handoffs', '--batch-id', bid)['groups'][0]
        self.assertEqual(group['intent_id'], intent)
        exact_intent = f.input({'schema_version': 1, 'kind': 'bulk_intent', 'payload': {
            'batch_id': bid, 'profile': 'alpha', 'item_keys': group['item_keys'],
            'body_sha256': group['body_sha256'], 'attempt': 0, 'authority': ref(f.authority)}})
        before = inventory(f.base)
        self.assertFalse(f.command('apply', '--input', exact_intent)['applied'])
        self.assertEqual(inventory(f.base), before)
        self.reject(f, 'bulk_intent', {'batch_id': bid, 'profile': 'alpha', 'item_keys': group['item_keys'],
                    'body_sha256': '0'*64, 'attempt': 0, 'authority': ref(f.authority)})
        for sid, outcome in [('x:101', 'already_covered'), ('x:102', 'not_useful'), ('x:103', 'blocked')]:
            payload = f.result(bid, sid, outcome)
            self.reject_nullability(f, payload)
            f.apply('bulk_result', payload)
            review = json.loads(Path(payload['review']['path']).read_text())
            review['trigger'] = None if outcome == 'already_covered' else 'forbidden'
            payload['review'] = write(f.evidence / ('bad-'+sid.replace(':', '')+'.json'), review)
            self.reject(f, 'bulk_result', payload)
        plan, path, txn = f.publish(bid, [('x:100', 'reference')])
        payload = f.result(bid, 'x:100', 'reference_saved', txn, plan)
        wrong = copy.deepcopy(payload)
        review = json.loads(Path(wrong['review']['path']).read_text())
        review['worker_artifacts'] = [ref(f.authority)]
        wrong['review'] = write(f.evidence / 'wrong-attachment.json', review)
        self.reject(f, 'bulk_result', wrong)
        self.reject_nullability(f, payload)
        f.apply('bulk_result', payload)
        plan2, path2, txn2 = f.publish(bid, [('x:102', 'wiki')])
        wiki_result = f.result(bid, 'x:102', 'wiki_published', txn2, plan2)
        self.reject_nullability(f, wiki_result)
        f.apply('bulk_result', wiki_result)
        Path(payload['review']['path']).unlink()
        states = {i['source_id']: i['state'] for i in f.command('status', '--batch-id', bid)['items']}
        self.assertEqual(states['x:100'], 'needs_reconciliation')
        self.assertEqual(states['x:102'], 'wiki_published')
        db_before = (f.runtime / 'ledger.sqlite3').read_bytes()
        Path(wiki_result['artifact']['path']).unlink()
        observed = {i['source_id']: i for i in f.command('status', '--batch-id', bid)['items']}
        self.assertEqual(observed['x:100']['recorded_outcome'], 'reference_saved')
        self.assertEqual(observed['x:102']['recorded_outcome'], 'wiki_published')
        self.assertEqual(observed['x:102']['state'], 'needs_reconciliation')
        self.assertEqual((f.runtime / 'ledger.sqlite3').read_bytes(), db_before)
        digest = f.command('digest', '--batch-id', bid)
        text = next(Path(a['path']).read_text() for a in digest['artifacts'] if a['path'].endswith('.md'))
        self.assertIn('| Source | Effective state | Recorded outcome |', text)
        self.assertIn('| x:100 | needs_reconciliation | reference_saved |', text)
        self.assertIn('| x:102 | needs_reconciliation | wiki_published |', text)
        self.assertEqual((f.runtime / 'ledger.sqlite3').read_bytes(), db_before)
        packet_path = next((f.evidence / 'packets').glob('*/packet.json'))
        packet_bytes = packet_path.read_bytes()
        packet_path.unlink()
        reopened = {i['source_id']: i for i in f.command('status', '--batch-id', bid)['items']}
        self.assertEqual(reopened['x:101']['recorded_outcome'], 'already_covered')
        self.assertEqual(reopened['x:101']['state'], 'needs_reconciliation')
        self.assertEqual((f.runtime / 'ledger.sqlite3').read_bytes(), db_before)
        packet_path.write_bytes(packet_bytes)
        self.assertEqual({i['source_id']: i['state'] for i in f.command('status', '--batch-id', bid)['items']}['x:101'], 'already_covered')
        blocker = self.fixture(1)
        b = blocker.batch(agent_ids=('x:100',))
        blocked_result = blocker.result(b, 'x:100', 'blocked', task=None)
        self.reject_nullability(blocker, blocked_result)
        blocker.apply('bulk_result', blocked_result)
        # F3: receipt drift blocks new dispatch; intact blocker progression stays permitted.
        saved_review = Path(blocked_result['review']['path']).read_bytes()
        Path(blocked_result['review']['path']).unlink()
        self.assertEqual(blocker.command('status', '--batch-id', b)['items'][0]['state'], 'needs_reconciliation')
        group = blocker.command('handoffs', '--batch-id', b)['groups'][0]
        event = {'batch_id': b, 'profile': group['profile'], 'item_keys': group['item_keys'],
                 'body_sha256': group['body_sha256'], 'attempt': 0, 'authority': ref(blocker.authority)}
        ei = blocker.input({'schema_version': 1, 'kind': 'bulk_intent', 'payload': event})
        before = inventory(blocker.base)
        denied = cli('bulk', 'apply', '--vault', blocker.vault, '--input', ei)
        self.assertEqual(denied.returncode, 2, denied.stdout)
        self.assertEqual(json.loads(denied.stdout)['error']['code'], 'INTEGRITY_ERROR')
        self.assertEqual(inventory(blocker.base), before)
        Path(blocked_result['review']['path']).write_bytes(saved_review)
        self.assertTrue(blocker.command('apply', '--input', ei)['applied'])
        before = inventory(blocker.base)
        self.assertFalse(blocker.command('apply', '--input', ei)['applied'])
        self.assertEqual(inventory(blocker.base), before)

        native = self.fixture(1)
        b = native.batch(agent_ids=('x:100',))
        native.observe(b, status='blocked')
        native.apply('bulk_result', native.result(b, 'x:100', 'blocked'))
        broken = self.fixture(1)
        bb = broken.batch(agent_ids=('x:100',))
        broken.observe(bb, {'x:100': 'blocked'}, transform=lambda rows: [{**row, 'revision': 'foreign-revision'} for row in rows])
        self.reject(broken, 'bulk_result', broken.result(bb, 'x:100', 'blocked'))

    def test_publish_and_reference_guards(self):
        f = self.fixture(2)
        bid = f.batch(agent_ids=('x:100',))
        f.observe(bid)
        foreign = f.home / 'profiles/beta/skills/foreign/SKILL.md'
        foreign.parent.mkdir(parents=True)
        foreign.write_text('Foreign manual skill sentinel.')
        foreign_before = inventory(foreign.parent)
        plan, path = f.plan(bid, [('x:100', 'reference'), ('x:101', 'wiki')])
        baseline = inventory(f.base)
        for mutate in ('wrapper', 'prose', 'note-prose', 'aggregate-prose', 'quote', 'date', 'owner', 'path', 'preimage', 'symlink', 'html', 'coverage', 'unsafe-link', 'digest', 'mapping', 'oversize'):
            with self.subTest(mutate=mutate):
                bad = copy.deepcopy(plan)
                if mutate == 'wrapper':
                    bad = {'schema_version': 1, 'kind': 'bulk_publish', 'payload': bad}
                elif mutate == 'prose':
                    bad['changes'][0]['content'] = write(f.evidence / 'evil-content.txt', 'Unbound prose')
                elif mutate in ('note-prose', 'aggregate-prose'):
                    selected_path = plan['notes'][0]['path'] if mutate == 'note-prose' else str(f.vault / 'wiki/index.md')
                    change = next(c for c in bad['changes'] if c['path'] == selected_path)
                    change['content'] = write(f.evidence / (mutate+'.txt'), 'Unbound staged prose')
                elif mutate in ('quote', 'date', 'html', 'coverage', 'unsafe-link'):
                    envelope = json.loads(Path(bad['notes'][0]['file']['path']).read_text())
                    if mutate == 'quote': envelope['note']['sources'][0]['quote'] = 'Not in captured source'
                    if mutate == 'date': envelope['updated'] = '2026-02-30'
                    if mutate == 'html': envelope['note']['explanation'][0]['text'] = '<script>alert(1)</script>'
                    if mutate == 'unsafe-link': envelope['note']['explanation'][0]['text'] = '[execute](javascript:alert(1))'
                    if mutate == 'coverage':
                        envelope['note']['sources'].append(f.note('x:101')['note']['sources'][0])
                    # Prove the actual inner consumer rejects this Note, not only a stale target hash.
                    from bookmark_wiki import bulk, wiki_publish
                    ctx, _, _ = bulk.context_for_batch(f.vault, bid)
                    with self.assertRaises(learning.LearningError):
                        wiki_publish.validate_note(ctx, envelope)
                    previous_path = bad['notes'][0]['path']
                    bad['notes'][0]['file'] = write(f.evidence / 'bad-note.json', envelope)
                    bad['notes'][0]['path'] = str(Path(previous_path).with_name(identity(envelope)+'.md'))
                    for change in bad['changes']:
                        if change['path'] == previous_path:
                            change['path'] = bad['notes'][0]['path']
                elif mutate == 'digest':
                    bad['notes'][0]['file']['sha256'] = '0'*64
                    bad['notes'][0]['path'] = str(Path(bad['notes'][0]['path']).with_name('0'*64+'.md'))
                elif mutate == 'mapping':
                    previous_path = bad['notes'][0]['path']
                    bad['notes'][0]['profile'] = 'beta'
                    bad['notes'][0]['path'] = str(f.home / 'profiles/beta/skills/bookmark-library/references' / Path(previous_path).name)
                    for change in bad['changes']:
                        if change['path'] == previous_path:
                            change['path'] = bad['notes'][0]['path']
                elif mutate == 'oversize':
                    large = f.evidence / 'oversize.txt'
                    with large.open('wb') as stream:
                        stream.truncate(8*1024*1024+1)
                    bad['changes'][0]['content'] = ref(large)
                elif mutate == 'owner': bad['notes'][0]['profile'] = 'beta'
                elif mutate == 'path': bad['changes'][0]['path'] = str(f.vault / 'unowned.md')
                elif mutate == 'preimage': bad['changes'][0]['before_sha256'] = '0'*64
                elif mutate == 'symlink':
                    link = f.evidence / 'link'
                    link.symlink_to(Path(plan['changes'][0]['content']['path']))
                    bad['changes'][0]['content'] = {'path': str(link), 'sha256': plan['changes'][0]['content']['sha256']}
                bp = f.input(bad)
                before = inventory(f.base)
                p = cli('bulk', 'publish', '--vault', f.vault, '--input', bp)
                self.assertEqual(p.returncode, 2, p.stdout)
                self.assertEqual(inventory(f.base), before)
        result = f.command('publish', '--input', path)
        self.assertTrue(result['applied'])
        html = (f.vault / 'reading-view.html').read_text()
        self.assertIn('Old page', html)
        self.assertIn('source reveal sentinel', html)
        self.assertIn('Sources and evidence', html)
        self.assertNotIn('/staged/', html)
        self.assertNotIn('<script src=', html)
        self.assertEqual(inventory(foreign.parent), foreign_before)
        from html.parser import HTMLParser
        class Navigation(HTMLParser):
            def __init__(self):
                super().__init__()
                self.ids, self.links = set(), []
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if 'id' in attrs:
                    self.ids.add(attrs['id'])
                if attrs.get('href', '').startswith('#'):
                    self.links.append(attrs['href'][1:])
        navigation = Navigation()
        navigation.feed(html)
        self.assertTrue(navigation.links)
        self.assertLessEqual(set(navigation.links), navigation.ids)
        parent = f.home / 'profiles/alpha/skills/bookmark-library/SKILL.md'
        reference = next(Path(n['path']) for n in plan['notes'] if n['kind'] == 'reference')
        self.assertIn('references/'+reference.name, parent.read_text())
        self.assertTrue(reference.is_file())
        self.assertEqual((f.vault / 'wiki/old.md').read_text(), '# Old page\n\nUnrelated page sentinel.\n')
        for entry in plan['notes']:
            data = Path(entry['path']).read_text()
            self.assertLess(len(data.splitlines()), 200)
            self.assertIn('## Explanation', data)
            self.assertIn('[1]', data)
        self.assertTrue(baseline)
        missing = self.fixture(1)
        mb = missing.batch(agent_ids=('x:100',))
        missing.observe(mb, retain_outputs=False)
        mp, mi = missing.plan(mb, [('x:100', 'reference')])
        before = inventory(missing.base)
        self.assertEqual(cli('bulk', 'publish', '--vault', missing.vault, '--input', mi).returncode, 2)
        self.assertEqual(inventory(missing.base), before)
        with self.subTest(repair='F1'):
            # F1: an unowned same-named catalog section cannot be truncated on first publication.
            collision = self.fixture(1)
            cb = collision.batch()
            cp, _ = collision.plan(cb, [('x:100', 'wiki')])
            index = collision.vault / 'wiki/index.md'
            index.write_text(index.read_text().rstrip()+'\n\n## Backlog topics\n\n- [Old page](old.md) - Manual catalog entry.\n\n## Other catalog\n\nTail sentinel.\n')
            next(c for c in cp['changes'] if c['path'] == str(index))['before_sha256'] = ref(index)['sha256']
            ci = collision.input(cp)
            before = inventory(collision.base)
            denied = cli('bulk', 'publish', '--vault', collision.vault, '--input', ci)
            self.assertEqual(denied.returncode, 2, denied.stdout)
            self.assertEqual(json.loads(denied.stdout)['error']['code'], 'INTEGRITY_ERROR')
            self.assertEqual(inventory(collision.base), before)
        with self.subTest(repair='F2'):
            # F2: eventual rejection is not enough; no read may cross the permitted roots.
            boundary = self.fixture(1)
            bb = boundary.batch()
            bp, _ = boundary.plan(bb, [('x:100', 'wiki')])
            foreign = boundary.base / 'outside-allowed-roots.json'
            foreign.write_bytes(Path(bp['notes'][0]['file']['path']).read_bytes())
            bp['notes'][0]['file'] = ref(foreign)
            bi = boundary.input(bp)
            before = inventory(boundary.base)
            reads, original_read = [], learning._read
            def tracked(path, *args, **kwargs):
                if Path(path) == foreign:
                    reads.append(str(path))
                return original_read(path, *args, **kwargs)
            from bookmark_wiki import bulk
            with patch.object(learning, '_read', tracked):
                with self.assertRaises(learning.LearningError) as rejected:
                    bulk.publish(boundary.vault, bi)
            self.assertEqual(rejected.exception.code, 'INVALID_ARGUMENT')
            self.assertEqual(reads, [])
            self.assertEqual(inventory(boundary.base), before)


    def test_replay_and_publish_resume(self):
        f = self.fixture(4)
        bid = f.batch(agent_ids=('x:100', 'x:102'))
        f.observe(bid)
        pa, filea = f.plan(bid, [('x:100', 'reference'), ('x:101', 'wiki')])
        script = 'from bookmark_wiki import bulk; import os; bulk.publish('+repr(str(f.vault))+','+repr(str(filea))+',fault=lambda p:os._exit(37) if p=="after_path:0" else None)'
        killed = subprocess.run([sys.executable, '-B', '-c', script], cwd=ROOT, env=ENV, capture_output=True)
        self.assertEqual(killed.returncode, 37, killed.stderr)
        ta = f.command('publish', '--input', filea)['transaction_id']
        for sid, outcome in [('x:100', 'reference_saved'), ('x:101', 'wiki_published')]:
            f.apply('bulk_result', f.result(bid, sid, outcome, ta, pa))
        pb, fileb, tb = f.publish(bid, [('x:102', 'reference'), ('x:103', 'wiki')])
        for sid, outcome in [('x:102', 'reference_saved'), ('x:103', 'wiki_published')]:
            f.apply('bulk_result', f.result(bid, sid, outcome, tb, pb))
        before = inventory(f.base)
        for _ in range(2):
            self.assertFalse(f.command('publish', '--input', filea)['applied'])
        self.assertEqual(inventory(f.base), before)
        states = f.command('status', '--batch-id', bid)
        self.assertEqual(states['counts']['reference_saved'], 2)
        self.assertEqual(states['counts']['wiki_published'], 2)
        index = f.vault / 'wiki/index.md'
        index.write_text('# Manual edit removed links\n')
        self.assertEqual(f.command('status', '--batch-id', bid)['counts']['needs_reconciliation'], 4)
        self.assertEqual(cli('bulk', 'publish', '--vault', f.vault, '--input', filea).returncode, 2)
        late = self.fixture(1)
        b = late.batch()
        p, fp = late.plan(b, [('x:100', 'wiki')])
        script = 'from bookmark_wiki import bulk; import os; bulk.publish('+repr(str(late.vault))+','+repr(str(fp))+',fault=lambda p:os._exit(38) if p=="after_files" else None)'
        done = subprocess.run([sys.executable, '-B', '-c', script], cwd=ROOT, env=ENV, capture_output=True)
        self.assertEqual(done.returncode, 38)
        targets = {c['path']: inventory(Path(c['path']).parent)[Path(c['path']).name] for c in p['changes']}
        late.command('publish', '--input', fp)
        self.assertEqual(targets, {c['path']: inventory(Path(c['path']).parent)[Path(c['path']).name] for c in p['changes']})
        partial = self.fixture(2)
        partial_batch = partial.batch()
        partial_plan, partial_input = partial.plan(partial_batch, [('x:100', 'wiki'), ('x:101', 'wiki')])
        script = 'from bookmark_wiki import bulk; import os; bulk.publish('+repr(str(partial.vault))+','+repr(str(partial_input))+',fault=lambda p:os._exit(39) if p=="after_path:0" else None)'
        killed = subprocess.run([sys.executable, '-B', '-c', script], cwd=ROOT, env=ENV, capture_output=True)
        self.assertEqual(killed.returncode, 39, killed.stderr)
        edited = Path(partial_plan['changes'][0]['path'])
        edited.write_text('Manual edit after first target; never overwrite.\n')
        before = inventory(partial.base)
        rejected = cli('bulk', 'publish', '--vault', partial.vault, '--input', partial_input)
        self.assertEqual(rejected.returncode, 2, rejected.stdout)
        self.assertEqual(json.loads(rejected.stdout)['error']['code'], 'INTEGRITY_ERROR')
        self.assertEqual(inventory(partial.base), before)
        self.assertEqual(partial.command('status', '--batch-id', partial_batch)['counts']['needs_reconciliation'], 2)
        self.assertEqual(inventory(partial.base), before)
        with closing(sqlite3.connect(partial.runtime / 'ledger.sqlite3')) as db:
            self.assertEqual(db.execute("select count(*) from events where kind='bulk_publish'").fetchone()[0], 0)


    def test_legacy_compatibility_and_bulk_cli(self):
        f = self.fixture(101)
        f.prepare()
        case = legacy.LearningContractTests('runTest')
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.base, case.vault, case.runtime = f.base, f.vault, f.runtime
        case.inputs, case.evidence, case.home, case.db = f.runtime / 'inputs', f.runtime / 'evidence', f.home, f.runtime / 'ledger.sqlite3'
        case.authority_file = case.evidence / 'same-db-authority.md'
        case.authority_file.write_text('Historical synthetic task t_00000001; legacy compatibility only.')
        case.authority = {'record': ref(case.authority_file), 'scope': 'historical_import'}
        old_batch, old_destinations, old_receipt = case.accepted('already_covered')
        old_status, old_check = case.status(), case.check(old_batch)
        with closing(sqlite3.connect(case.db)) as conn:
            old_rows = {table: conn.execute('select * from '+table+' order by rowid').fetchall()
                        for table in learning.COLUMNS if table != 'events'}
            old_events = conn.execute('select * from events order by seq').fetchall()
        with closing(sqlite3.connect(f.runtime / 'ledger.sqlite3')) as conn:
            before_meta = conn.execute('select * from meta order by key').fetchall()
        for count in (50, 50, 1):
            prepared = f.prepare()
            self.assertEqual(prepared['selected_count'], count)
            f.batch(prepared)
        status = f.command('status')
        self.assertEqual(status['items'], [])
        self.assertEqual(sum(status['counts'].values()), 101)
        with closing(sqlite3.connect(f.runtime / 'ledger.sqlite3')) as conn:
            self.assertEqual(conn.execute('select * from meta order by key').fetchall(), before_meta)
            self.assertEqual({r[0] for r in conn.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")}, set(learning.COLUMNS))
            self.assertEqual(conn.execute('select count(*) from batches').fetchone()[0], 1)
            for table, rows in old_rows.items():
                self.assertEqual(conn.execute('select * from '+table+' order by rowid').fetchall(), rows)
            self.assertEqual(conn.execute("select * from events where kind not like 'bulk_%' order by seq").fetchall(), old_events)
        self.assertEqual(case.status(), old_status)
        self.assertEqual(case.check(old_batch), old_check)
        self.assertEqual(learning.status(f.vault)['batches'], 1)
        case = legacy.LearningContractTests('runTest')
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.init()
        bid, ids, receipt = case.accepted('already_covered')
        self.assertTrue(case.check(bid)['locally_complete'])
        bad = f.inputs / 'malformed.json'
        bad.write_text('{"schema_version":1,"schema_version":1}')
        p = cli('bulk', 'apply', '--vault', f.vault, '--input', bad)
        self.assertEqual(p.returncode, 2)
        self.assertNotIn(str(f.base), p.stdout)
        error = json.loads(p.stdout)
        self.assertEqual(set(error), {'schema_version', 'command', 'status', 'error'})
        self.assertEqual(error['status'], 'error')
        self.assertEqual(set(error['error']), {'code', 'message'})
        oversized = f.inputs / 'oversized.json'
        oversized.write_text(' '*(1048576+1))
        before = inventory(f.base)
        rejected = cli('bulk', 'apply', '--vault', f.vault, '--input', oversized)
        self.assertEqual(rejected.returncode, 2)
        self.assertEqual(json.loads(rejected.stdout)['error']['code'], 'INPUT_TOO_LARGE')
        self.assertEqual(inventory(f.base), before)
        # Reverse cross-kind reuse is also forbidden: legacy cannot rebind a bulk task.
        cross = self.fixture(1)
        cb = cross.batch(agent_ids=('x:100',))
        cross.observe(cb)
        c = legacy.LearningContractTests('runTest')
        c.setUp()
        self.addCleanup(c.doCleanups)
        c.base, c.vault, c.runtime = cross.base, cross.vault, cross.runtime
        c.inputs, c.evidence, c.home, c.db = cross.runtime / 'inputs', cross.runtime / 'evidence', cross.home, cross.runtime / 'ledger.sqlite3'
        authority = c.evidence / 'legacy-authority.md'
        authority.write_text('Synthetic historical import, task t_00000001. No live authority.')
        c.authority_file = authority
        c.authority = {'record': ref(authority), 'scope': 'historical_import'}
        oldb, oldids = c.register(historical=True)
        c.observe(oldb, oldids, intent=None, profile='finn', task='t_00000001', ok=False, code='INTEGRITY_ERROR')

    def test_digest_is_outcome_based(self):
        f = self.fixture(4)
        bid = f.batch(agent_ids=('x:100',))
        f.observe(bid, {'x:100': 'not_useful'})
        decline = f.result(bid, 'x:100', 'not_useful')
        f.apply('bulk_result', decline)
        plan, path, txn = f.publish(bid, [('x:100', 'wiki'), ('x:101', 'wiki')])
        for sid in ('x:100', 'x:101'):
            payload = f.result(bid, sid, 'wiki_published', txn, plan)
            f.apply('bulk_result', payload)
            self.assertFalse(f.apply('bulk_result', payload)['applied'])
        f.apply('bulk_result', f.result(bid, 'x:102', 'blocked', task=None))
        digest = f.command('digest', '--batch-id', bid)
        self.assertEqual(digest['counts']['wiki_published'], 2)
        self.assertEqual(digest['counts']['blocked'], 1)
        self.assertEqual(sum(digest['counts'].values()), 4)
        self.assertEqual(digest['counts']['routed'], 1)
        self.assertEqual(len(digest['artifacts']), 2)
        for artifact in digest['artifacts']:
            self.assertEqual(ref(Path(artifact['path'])), artifact)
        before_prepare = (f.runtime / 'ledger.sqlite3').read_bytes()
        self.assertEqual(f.prepare()['selected_count'], 0)
        self.assertEqual(f.prepare()['resume_count'], 2)
        self.assertEqual((f.runtime / 'ledger.sqlite3').read_bytes(), before_prepare)
        pending = self.fixture(1)
        pending_batch = pending.batch(register_only=True)
        self.assertEqual(pending.command('digest', '--batch-id', pending_batch)['counts']['pending'], 1)
        text = next(Path(a['path']).read_text() for a in digest['artifacts'] if a['path'].endswith('.md'))
        self.assertIn('not_useful', text)
        self.assertNotIn('new skills learned', text)


if __name__ == '__main__':
    unittest.main()
