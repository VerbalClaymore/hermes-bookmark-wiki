"""§12 C1–C10: synthetic fixtures, defined before implementation.

No live IDs or evidence. The public CLI is the principal entry point; private
fault/clock seams exercise real SQLite persistence in terminated subprocesses.
"""
import copy
from contextlib import closing
import datetime
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(ROOT))
NOW = '2026-01-02T03:04:05Z'

def canon(obj):
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'))+'\n').encode()

def digest(obj):
    return hashlib.sha256(canon(obj)).hexdigest()

def ref(path):
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}

def cli(*args):
    return subprocess.run([sys.executable, '-B', '-m', 'bookmark_wiki', *map(str, args)],
                          cwd=ROOT, env=ENV, capture_output=True, text=True)

def inventory(root):
    result = {}
    for p in root.rglob('*'):
        if p.is_symlink(): result[str(p.relative_to(root))] = ('link', os.readlink(p))
        elif p.is_file(): result[str(p.relative_to(root))] = ('file', hashlib.sha256(p.read_bytes()).hexdigest())
        elif p.is_dir(): result[str(p.relative_to(root))] = ('dir',)
    return result

class LearningContractTests(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(dir=os.environ['TMPDIR'])
        self.addCleanup(td.cleanup)
        self.base = Path(td.name)
        self.vault = self.base/'vault'
        self.runtime = self.vault/'state/learning-process-v1'
        self.inputs = self.runtime/'inputs'; self.inputs.mkdir(parents=True)
        self.evidence = self.runtime/'evidence'; self.evidence.mkdir()
        self.home = self.base/'synthetic-hermes'; self.home.mkdir()
        self.write(self.inputs/'roots.json', {'schema_version':1, 'hermes_home':str(self.home)})
        self.authority_file = self.evidence/'authority.md'
        self.authority_file.write_text('Coordinator-approved synthetic batch. Guarded installation; failed acceptance property: wording. Historical tasks t_00000001 t_00000002 t_00000003 t_00000004 t_00000005 t_00000006. Synthetic only.\n')
        self.authority = {'record':ref(self.authority_file), 'scope':'approved_batch'}
        self.db = self.runtime/'ledger.sqlite3'
        self.serial = 0

    def write(self, path, obj):
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(canon(obj)); return ref(path)

    def result(self, result, ok=True, code=None):
        self.assertEqual(result.stderr, '')
        self.assertEqual(result.returncode, 0 if ok else 2, result.stdout)
        obj=json.loads(result.stdout)
        self.assertEqual(set(obj), {'schema_version','command','status', 'data' if ok else 'error'})
        self.assertEqual(obj['command'], 'learning'); self.assertEqual(obj['schema_version'], 1)
        self.assertEqual(obj['status'], 'ok' if ok else 'error')
        self.assertNotIn(str(self.base), result.stdout)
        if code: self.assertEqual(obj['error']['code'], code)
        return obj['data'] if ok else obj['error']

    def init(self):
        return self.result(cli('learning','init','--vault',self.vault))

    def event(self, kind, payload):
        self.serial+=1
        p=self.inputs/('event-%03d.json'%self.serial)
        self.write(p, {'schema_version':1,'kind':kind,'payload':payload})
        return p

    def apply(self, kind, payload, ok=True, code=None):
        p=self.event(kind,payload)
        before=inventory(self.base)
        data=self.result(cli('learning','apply','--vault',self.vault,'--input',p),ok,code)
        if not ok: self.assertEqual(inventory(self.base),before)
        return data

    def packet(self, count=1, generation='a', revision='capture-1'):
        records=[]
        for i in range(count):
            records.append({'source_id':'x:'+str(100+i),'tweet_id':str(100+i),'current_revision':revision,
                'revision_count':1,'content':{'text':'Synthetic capture Ω'},'metadata':{},'evidence_path':'synthetic',
                'raw_path':'synthetic','context_status':'pending','integration_status':'pending',
                'wiki_pages':[], 'removal_eligible':False,'warnings':[]})
        obj={'schema_version':1,'adapter_version':'1.0','manifest_sha256':generation*64,'records':records}
        pid=digest(obj)
        return self.write(self.vault/'state/library-intake-pilot-v1/packets'/pid/'packet.json',obj)

    def registration(self, profiles=('finn',), wiki=False, count=1, generation='a', historical=False, revision='capture-1'):
        packet=self.packet(count,generation,revision)
        targets=[]; links=[]; destinations=[]
        for i in range(count):
            t={'identity_url':'https://example.test/post/'+str(i),'revision':revision,
               'scope_read':['captured text'], 'scope_unchecked':['media'], 'evidence':[packet]}
            tid=digest({k:t[k] for k in ('identity_url','revision','scope_read')})
            targets.append(t); links.append({'source_id':'x:'+str(100+i),'archive_revision':revision,'target_id':tid})
            for profile in profiles:
                destinations.append({'target_id':tid,'destination':'specialist','profile':profile,'reason':'Relevant synthetic role.'})
            if wiki: destinations.append({'target_id':tid,'destination':'wiki','profile':None,'reason':'Deferred publication.'})
        return {'authority':{**self.authority,'scope':'historical_import' if historical else 'approved_batch'},
                'packet':packet,'targets':targets,'links':links,'destinations':destinations}

    def register(self, **kwargs):
        payload=self.registration(**kwargs); data=self.apply('register_batch',payload)
        ids=[digest({k:d[k] for k in ('target_id','destination','profile')}) for d in payload['destinations']]
        return data['event_id'], ids

    def intent(self, batch, ids, stage='reading', ordinal=0, ok=True, code=None):
        return self.apply('dispatch_intent',{'batch_id':batch,'destination_ids':sorted(ids),'stage':stage,
                          'ordinal':ordinal,'authority':self.authority},ok,code)

    def observation(self, task='t_00000001', profile='finn', status='done', when=NOW, body='body'):
        art=self.evidence/(task+'-artifact.txt')
        if not art.exists(): art.write_text('Synthetic retained output')
        observation={'task_id':task,'board':'bookmark-skills','assignee':profile,'status':status,
                     'observed_at':when,'body_sha256':hashlib.sha256(body.encode()).hexdigest()}
        self.serial+=1
        evidence=self.write(self.evidence/('observation-%03d.json'%self.serial),{**observation,'attachments':[ref(art)]})
        return {**observation,'evidence':evidence}

    def observe(self,batch,ids,intent=None,stage='reading',ordinal=0,task='t_00000001',profile='finn',status='done',when=NOW,**kwargs):
        payload={'batch_id':batch,'destination_ids':sorted(ids),'intent_id':intent,'stage':stage,'ordinal':ordinal,
                 'observation':self.observation(task,profile,status,when)}
        return self.apply('task_observed',payload,**kwargs)

    def receipt(self,batch,did,profile='finn',disposition='adopted',task='t_00000001',app='t_00000002'):
        rows=json.loads(self._register_payload(batch))
        target=next(t for t in rows['targets'] if digest({k:t[k] for k in ('identity_url','revision','scope_read')})==next(d['target_id'] for d in rows['destinations'] if digest({k:d[k] for k in ('target_id','destination','profile')})==did))
        link=next(x for x in rows['links'] if x['target_id']==digest({k:target[k] for k in ('identity_url','revision','scope_read')}))
        skill=self.home/('profiles/finn/skills/creative/visual-style-research/SKILL.md' if profile=='finn' else 'profiles/vidya/skills/product-video-workflow/SKILL.md')
        skill.parent.mkdir(parents=True,exist_ok=True); skill.write_text('Synthetic installed coverage')
        pre=self.evidence/(profile+'-preimage.md'); pre.write_text('Synthetic prior coverage')
        art=self.evidence/('application-'+profile+'.txt'); art.write_text('Synthetic application output')
        r={'schema_version':1,'profile':profile,'verification':'accepted','verified_by':'geoffrey','verified_at':'2026-01-02T03:04:05+00:00',
           'task_ids':[task,app] if disposition=='adopted' else [task],
           'source_decisions':[{'source_id':link['source_id'],'revision':link['archive_revision'],'disposition':disposition,
             'reason':'Concrete role-relative synthetic reason','learning_target':{'identity':target['identity_url'],'revision':target['revision'],'scope':'explanation only'},
             'scope_read':target['scope_read'],'evidence':{**target['evidence'][0],'size':Path(target['evidence'][0]['path']).stat().st_size}}],
           'changes':[{'path':str(skill),'action':'modify','before_sha256':ref(pre)['sha256'],'after_sha256':ref(skill)['sha256']}] if disposition=='adopted' else [],
           'application':{'status':'passed' if disposition=='adopted' else 'not_run','task_id':app if disposition=='adopted' else None,
                          'artifacts':[{**ref(art),'size':art.stat().st_size,'native_path':'inert provenance'}] if disposition=='adopted' else []}}
        receipt_path=self.evidence/(profile+'-receipt.json'); self.write(receipt_path,r)
        review={'schema_version':1,'receipt_sha256':ref(receipt_path)['sha256'],'destination_id':did,'source_id':link['source_id'],
                'verdict':'accepted','reason':'Direct coordinator review of synthetic evidence.',
                'preimages':[{'installed_path':str(skill),'file':ref(pre)}] if disposition=='adopted' else [],
                'coverage':[{'file':ref(skill),'section':'Coverage','trigger':'Relevant question'}] if disposition in ('reference_saved','already_covered') else [],
                'evidence':[ref(self.authority_file)]}
        review_path=self.evidence/(profile+'-review.json'); self.write(review_path,review)
        return {'batch_id':batch,'destination_id':did,'disposition':disposition,'receipt':ref(receipt_path),'review':ref(review_path)}

    def _register_payload(self,batch):
        with closing(sqlite3.connect(self.db)) as conn:
            return conn.execute('select register_json from batches where batch_id=?',(batch,)).fetchone()[0]

    def accepted(self,disposition='adopted',profile='finn',wiki=False):
        batch,ids=self.register(profiles=(profile,),wiki=wiki,historical=True)
        did=ids[0]
        self.observe(batch,[did],profile=profile,status='blocked' if disposition=='blocked' else 'done')
        if disposition=='adopted': self.observe(batch,[did],stage='application',task='t_00000002',profile=profile)
        payload=self.receipt(batch,did,profile,disposition)
        self.apply('receipt_accepted',payload)
        return batch,ids,payload

    def check(self,batch): return self.result(cli('learning','check','--vault',self.vault,'--batch-id',batch))
    def status(self): return self.result(cli('learning','status','--vault',self.vault))

    def test_learning_init_paths_and_schema(self):
        d=self.init(); self.assertEqual(d,{'action':'init','schema_version':1,'created':True})
        before=(self.db.read_bytes(),self.db.stat().st_mtime_ns)
        self.assertFalse(self.init()['created']); self.assertEqual(before,(self.db.read_bytes(),self.db.stat().st_mtime_ns))
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(c.execute('pragma user_version').fetchone()[0],1)
            self.assertEqual({x[0] for x in c.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")},
                {'meta','events','batches','destinations','batch_destinations','task_bindings'})
            self.assertEqual({x[0] for x in c.execute('select key from meta')},{'schema_version','roots_sha256','spec_id','spec_version'})
            c.execute('pragma user_version=2')
        before=inventory(self.base); self.result(cli('learning','init','--vault',self.vault),False,'UNSUPPORTED_SCHEMA'); self.assertEqual(before,inventory(self.base))

    def test_c1_paths_reject_before_writes_and_read_never_initializes(self):
        missing=self.base/'absent'
        before=inventory(self.base)
        self.result(cli('learning','status','--vault',missing),False,'NOT_INITIALIZED')
        self.assertEqual(before,inventory(self.base))
        self.result(cli('learning','init','--vault',self.vault/'..'/'escape'),False,'INVALID_ARGUMENT')
        for relative in ('vault','root','db','journal','lock','input'):
            with self.subTest(relative=relative):
                target=self.base/('outside-'+relative); target.write_text('sentinel')
                path={'vault':self.base/'alias','root':self.vault/'state/alias',
                      'db':self.db,'journal':self.runtime/'ledger.sqlite3-journal','lock':self.runtime/'.learning.lock',
                      'input':self.inputs/'alias.json'}[relative]
                path.symlink_to(self.vault if relative=='vault' else target)
                before=inventory(self.base)
                if relative=='vault': args=('init','--vault',path)
                elif relative=='root':
                    path.unlink(); path=self.runtime; backup=self.vault/'state/saved'; path.rename(backup); path.symlink_to(backup)
                    before=inventory(self.base); args=('init','--vault',self.vault)
                elif relative=='input': args=('apply','--vault',self.vault,'--input',path)
                else: args=('init','--vault',self.vault)
                self.result(cli('learning',*args),False,'INVALID_ARGUMENT'); self.assertEqual(before,inventory(self.base))
                path.unlink()
                if relative=='root': backup.rename(self.runtime)
        self.assertFalse(self.db.exists())
        before=inventory(self.base); (self.inputs/'roots.json').unlink(); before=inventory(self.base)
        self.result(cli('learning','init','--vault',self.vault),False,'NOT_FOUND'); self.assertEqual(before,inventory(self.base))

    def test_learning_identity_and_bounds(self):
        self.init(); b,ids=self.register(); b2,ids2=self.register(generation='b')
        self.assertEqual(ids,ids2); self.assertNotEqual(b,b2)
        _,other=self.register(profiles=('vidya',)); self.assertNotEqual(ids,other)
        _,revised=self.register(revision='capture-2'); self.assertNotEqual(ids,revised)
        payload=self.registration(); payload['targets'][0]['scope_read']=['b','a']
        payload['links'][0]['target_id']=digest({k:payload['targets'][0][k] for k in ('identity_url','revision','scope_read')}); payload['destinations'][0]['target_id']=payload['links'][0]['target_id']
        new=self.apply('register_batch',payload); self.assertNotEqual(new['event_id'],b)
        p=self.event('register_batch',self.registration()); obj=json.loads(p.read_bytes()); p.write_text(json.dumps(obj,sort_keys=False,indent=4))
        old=self.db.read_bytes(); d=self.result(cli('learning','apply','--vault',self.vault,'--input',p)); self.assertFalse(d['applied']); self.assertEqual(old,self.db.read_bytes())
        self.register(count=5)
        for count in (0,6):
            invalid=self.registration()
            invalid['targets']=[] if count==0 else invalid['targets']*6
            self.apply('register_batch',invalid,False,'INVALID_ARGUMENT')

    def test_c2_strict_json_literals_and_byte_boundaries(self):
        self.init(); valid={'schema_version':1,'kind':'register_batch','payload':self.registration()}
        raws=['{"schema_version":1,"schema_version":1,"kind":"x","payload":{}}',
              '{"schema_version":NaN}', '{"schema_version":Infinity}',json.dumps({**valid,'schema_version':True}),json.dumps({**valid,'extra':1})]
        for raw in raws:
            p=self.inputs/'bad.json'; p.write_text(raw); before=inventory(self.base)
            self.result(cli('learning','apply','--vault',self.vault,'--input',p),False,'INVALID_ARGUMENT'); self.assertEqual(before,inventory(self.base))
        for size in (1048575,1048576,1048577):
            p=self.inputs/'ceiling.json'; raw=canon(valid); p.write_bytes(raw+b' '*(size-len(raw))); old=self.db.read_bytes()
            d=self.result(cli('learning','apply','--vault',self.vault,'--input',p),size<=1048576,None if size<=1048576 else 'INPUT_TOO_LARGE')
            if size>1048576: self.assertEqual(old,self.db.read_bytes())
        cases=[]
        for key,value in [('identity_url','https://user@example.test/x'),('revision',''),('scope_read',[]),('scope_read',['a','a']),('scope_read',['x'*257]),('scope_read',['a']*21)]:
            x=copy.deepcopy(valid['payload']); x['targets'][0][key]=value; cases.append(x)
        for value in ('x:١','x:1junk','100','x:+100'):
            x=copy.deepcopy(valid['payload']); x['links'][0]['source_id']=value; cases.append(x)
        x=copy.deepcopy(valid['payload']); x['links']*=2; cases.append(x)
        for x in cases: self.apply('register_batch',x,False)

    def test_register_batch_packet_and_roots(self):
        self.init(); self.register()
        for mutation in ('digest','revision','source','foreign','profile','outside','symlink','duplicate_destination'):
            x=self.registration(generation='b')
            if mutation=='digest': x['packet']['sha256']='0'*64
            elif mutation=='revision': x['links'][0]['archive_revision']='wrong'
            elif mutation=='source': x['links'][0]['source_id']='x:999'
            elif mutation=='foreign': x['links'][0]['target_id']='0'*64
            elif mutation=='profile': x['destinations'][0]['profile']='other'
            elif mutation=='duplicate_destination': x['destinations']*=2
            else:
                path=self.base/'outside.txt'; path.write_text('outside')
                if mutation=='symlink': alias=self.evidence/'alias.txt'; alias.symlink_to(path); path=alias
                x['authority']['record']=ref(path)
            self.apply('register_batch',x,False)
        self.write(self.inputs/'roots.json',{'schema_version':1,'hermes_home':str(self.base)})
        before=inventory(self.base); self.result(cli('learning','status','--vault',self.vault),False,'INTEGRITY_ERROR'); self.assertEqual(before,inventory(self.base))

    def test_dispatch_intent_guards_and_caps(self):
        self.init(); b,ids=self.register(); i=self.intent(b,ids)['event_id']
        # Exact event replay must be a no-op. A distinct authority record tests
        # a second create intent rather than accidentally asserting against replay.
        self.assertFalse(self.intent(b,ids)['applied'])
        other=self.evidence/'second-authority.md'; other.write_text('Second intent, same approved scope.')
        self.authority={'record':ref(other),'scope':'approved_batch'}
        self.intent(b,ids,ok=False,code='INVALID_TRANSITION')
        self.intent(b,ids,'application',ok=False,code='INVALID_TRANSITION')
        self.observe(b,ids,i,status='blocked')
        self.intent(b,ids,ordinal=1)
        b2,same=self.register(generation='b'); self.intent(b2,same,ok=False,code='INVALID_TRANSITION')
        self.intent(b2,same,ordinal=1,ok=False,code='INVALID_TRANSITION')
        self.intent(b2,same,ordinal=2,ok=False,code='INVALID_ARGUMENT')
        bh,dh=self.register(profiles=('vidya',),historical=True); self.intent(bh,dh,ok=False,code='INVALID_TRANSITION')
        bw,dw=self.register(profiles=(),wiki=True); self.intent(bw,dw,ok=False,code='INVALID_TRANSITION')
        bm,dm=self.register(profiles=('finn','vidya'),generation='c'); self.intent(bm,dm,ok=False,code='INVALID_TRANSITION')

    def test_c4_imported_attempts_consume_caps_and_application_order(self):
        self.init(); b,ids=self.register(historical=True)
        self.observe(b,ids,status='blocked'); b2,ids2=self.register(generation='b')
        self.intent(b2,ids2,ok=False,code='INVALID_TRANSITION')
        corr=self.intent(b2,ids2,ordinal=1)['event_id']; self.observe(b2,ids2,corr,ordinal=1,task='t_00000003')
        app=self.intent(b2,ids2,stage='application')['event_id']; self.observe(b2,ids2,app,stage='application',task='t_00000002')
        self.intent(b2,ids2,stage='application',ordinal=1)

    def test_task_binding_and_native_observation(self):
        self.init(); b,ids=self.register(); intent=self.intent(b,ids)['event_id']
        observation=self.observation(status='running')
        payload={'batch_id':b,'intent_id':intent,'destination_ids':ids,'stage':'reading','ordinal':0,'observation':observation}
        d=self.apply('task_observed',payload); before=self.db.read_bytes(); self.assertFalse(self.apply('task_observed',payload)['applied']); self.assertEqual(before,self.db.read_bytes())
        self.observe(b,ids,intent,status='done',when='2026-01-02T03:05:05Z')
        self.observe(b,ids,intent,status='running',when='2026-01-02T03:06:05Z')
        for change in ('board','assignee','body','intent','task','time','duplicate'):
            x=copy.deepcopy(payload)
            if change=='intent': x['intent_id']='f'*64
            elif change=='duplicate': x['destination_ids']*=2
            else:
                key={'body':'body_sha256','task':'task_id','time':'observed_at'}.get(change,change)
                x['observation'][key]={'board':'default','assignee':'vidya','body':'f'*64,'task':'t_00000009','time':'2026-01-01T00:00:00Z'}[change]
                obj={k:v for k,v in x['observation'].items() if k!='evidence'}; obj['attachments']=[]
                x['observation']['evidence']=self.write(self.evidence/('invalid-'+change+'.json'),obj)
            self.apply('task_observed',x,False)
        self.assertEqual(self.status()['destinations'][0]['observed_task_status'],'running')
        self.assertEqual(self.status()['destinations'][0]['recorded_state'],'awaiting_result')

    def test_receipt_acceptance_is_evidence_gated(self):
        self.init(); b,ids=self.register(historical=True); self.observe(b,ids)
        r=self.receipt(b,ids[0])
        self.apply('receipt_accepted',r,False,'INTEGRITY_ERROR') # application unobserved
        self.observe(b,ids,stage='application',task='t_00000002')
        original=json.loads(Path(r['receipt']['path']).read_bytes()); review_original=json.loads(Path(r['review']['path']).read_bytes())
        for mutation in ('owner','revision','artifacts','scope','duplicate_decision','preimage','review','after','size','task','bool'):
            receipt=copy.deepcopy(original); review=copy.deepcopy(review_original)
            if mutation=='owner': receipt['profile']='vidya'
            elif mutation=='revision': receipt['source_decisions'][0]['learning_target']['revision']='wrong'
            elif mutation=='scope': receipt['source_decisions'][0]['scope_read']=['summary paraphrase']
            elif mutation=='artifacts': receipt['application']['artifacts']=[]
            elif mutation=='duplicate_decision': receipt['source_decisions']*=2
            elif mutation=='preimage': review['preimages']=[]
            elif mutation=='review': review['unexpected']=True
            elif mutation=='after': receipt['changes'][0]['after_sha256']='0'*64
            elif mutation=='size': receipt['application']['artifacts'][0]['size']+=1
            elif mutation=='task': receipt['task_ids'].append('t_00000099')
            elif mutation=='bool': receipt['schema_version']=True
            rp=self.evidence/('bad-receipt-'+mutation+'.json'); rr=self.write(rp,receipt); review['receipt_sha256']=rr['sha256']
            vr=self.write(self.evidence/('bad-review-'+mutation+'.json'),review)
            self.apply('receipt_accepted',{**r,'receipt':rr,'review':vr},False,'INTEGRITY_ERROR')
        self.apply('receipt_accepted',r)
        self.assertTrue(self.check(b)['locally_complete'])

    def test_c6_all_dispositions_and_coverage_requirements(self):
        self.init()
        for disposition in ('reference_saved','already_covered','not_useful','blocked'):
            # Independent synthetic vault per decision avoids task-ID rebinding.
            with self.subTest(disposition=disposition):
                case=LearningContractTests(); case.setUp()
                try:
                    case.init(); b,ids,p=case.accepted(disposition)
                    check=case.check(b); case.assertEqual(check['locally_complete'],disposition!='blocked')
                    case.assertEqual(check['results'][0]['effective_state'],'blocked' if disposition=='blocked' else 'known_noop')
                    if disposition in ('reference_saved','already_covered'):
                        review=json.loads(Path(p['review']['path']).read_bytes()); review['coverage']=[]
                        bad=case.write(case.evidence/'bad-coverage.json',review)
                        case.apply('receipt_accepted',{**p,'review':bad},False,'INTEGRITY_ERROR')
                finally: case.doCleanups()
        b,ids=self.register(profiles=(),wiki=True,historical=True)
        fake=self.write(self.evidence/'empty.json',{})
        self.apply('receipt_accepted',{'batch_id':b,'destination_id':ids[0],'disposition':'not_useful','receipt':fake,'review':fake},False,'INVALID_TRANSITION')

    def test_learning_replay_and_drift(self):
        self.init(); b,ids,p=self.accepted(); before=self.db.read_bytes(); old_receipt=Path(p['receipt']['path']).read_bytes()
        self.assertFalse(self.apply('receipt_accepted',p)['applied']); self.assertEqual(before,self.db.read_bytes())
        b2,ids2=self.register(generation='b'); self.assertEqual(ids,ids2)
        self.assertEqual(self.check(b2)['results'][0]['effective_state'],'known_noop')
        self.intent(b2,ids2,ok=False,code='INVALID_TRANSITION')
        installed=Path(json.loads(old_receipt)['changes'][0]['path']); installed.write_text('Drifted current file')
        before=self.db.read_bytes(); c=self.check(b2); self.assertEqual(c['results'][0]['reason_code'],'HASH_MISMATCH'); self.assertFalse(c['locally_complete']); self.assertEqual(before,self.db.read_bytes())
        self.intent(b2,ids2,ordinal=1,ok=False,code='INTEGRITY_ERROR')
        installed.write_text('Synthetic installed coverage'); artifact=Path(json.loads(old_receipt)['application']['artifacts'][0]['path']); artifact.unlink()
        before=inventory(self.base); c=self.check(b); self.assertEqual(c['results'][0]['reason_code'],'MISSING_EVIDENCE'); self.assertEqual(before,inventory(self.base))
        self.assertEqual(old_receipt,Path(p['receipt']['path']).read_bytes())
        self.apply('receipt_accepted',p,False,'INTEGRITY_ERROR')

    def test_c7_recorded_decline_survives_missing_or_changed_receipt(self):
        """Concrete persistence gap: §12 stores a receipt hash, not disposition.

        Lost evidence changes effective reuse, never the historical decision.
        A coordinator input must not relabel declined as accepted after restart.
        """
        self.init(); batch,ids,payload=self.accepted(disposition='not_useful')
        self.assertEqual(self.status()['destinations'][0]['recorded_state'],'declined')
        receipt=Path(payload['receipt']['path']); original=receipt.read_bytes()
        for mode in ('missing','changed'):
            with self.subTest(mode=mode):
                if mode=='missing': receipt.unlink()
                else:
                    altered=json.loads(original)
                    altered['source_decisions'][0]['disposition']='blocked'
                    receipt.write_bytes(canon(altered))
                before=inventory(self.base)
                self.assertEqual(self.status()['destinations'][0]['recorded_state'],'declined')
                result=self.check(batch)['results'][0]
                self.assertEqual(result['recorded_state'],'declined')
                self.assertEqual(result['effective_state'],'needs_reconciliation')
                self.assertEqual(before,inventory(self.base))
                receipt.write_bytes(original)

    def test_destination_completion_is_conjunctive(self):
        self.init(); b,ids,p=self.accepted(wiki=True); c=self.check(b)
        self.assertFalse(c['locally_complete']); self.assertEqual({x['effective_state'] for x in c['results']},{'known_noop','deferred'})
        b2,ids2=self.register(profiles=('finn','vidya'),historical=True,generation='b')
        vid=ids2[1]; self.observe(b2,[vid],profile='vidya',task='t_00000003',status='blocked')
        self.assertFalse(self.check(b2)['locally_complete'])
        self.observe(b2,[vid],profile='vidya',task='t_00000003',status='done',when='2026-01-02T04:00:00Z')
        r=self.receipt(b2,vid,profile='vidya',disposition='not_useful',task='t_00000003'); self.apply('receipt_accepted',r)
        c=self.check(b2); self.assertTrue(c['locally_complete']); self.assertIn('declined',{x['recorded_state'] for x in c['results']})

    def test_learning_crash_and_lock_recovery(self):
        self.init(); payload=self.registration(); p=self.event('register_batch',payload)
        child=subprocess.Popen([sys.executable,'-B','-c',"import fcntl,sys; f=open(sys.argv[1],'a+b'); fcntl.flock(f,fcntl.LOCK_EX); print('held',flush=True); sys.stdin.read()",str(self.runtime/'.learning.lock')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,env=ENV)
        try:
            self.assertEqual(child.stdout.readline().strip(),'held')
            before=self.db.read_bytes(); self.result(cli('learning','apply','--vault',self.vault,'--input',p),False,'LOCKED'); self.assertEqual(before,self.db.read_bytes())
        finally: child.communicate('release',timeout=10)
        for seam in ('before_commit','after_commit'):
            with self.subTest(seam=seam):
                code="from bookmark_wiki import learning; import os,sys; learning.apply(sys.argv[1],sys.argv[2],fault=lambda point: os._exit(73) if point==sys.argv[3] else None)"
                result=subprocess.run([sys.executable,'-B','-c',code,str(self.vault),str(p),seam],cwd=ROOT,env=ENV,capture_output=True,text=True)
                self.assertEqual(result.returncode,73,result.stderr)
                status=self.status(); self.assertEqual(status['batches'],0 if seam=='before_commit' else 1)
        self.assertFalse(self.result(cli('learning','apply','--vault',self.vault,'--input',p))['applied'])
        with closing(sqlite3.connect(self.db)) as c: self.assertEqual(c.execute('select count(*) from events').fetchone()[0],1)

    def test_c9_init_crash_ignored_stage_and_precommit_hash_change(self):
        code="from bookmark_wiki import learning; import os,sys; learning.init(sys.argv[1],fault=lambda point: os._exit(73) if point=='init_before_rename' else None)"
        result=subprocess.run([sys.executable,'-B','-c',code,str(self.vault)],cwd=ROOT,env=ENV,capture_output=True,text=True)
        self.assertEqual(result.returncode,73,result.stderr); self.assertFalse(self.db.exists()); stages=list(self.runtime.glob('.ledger-stage-*.sqlite3')); self.assertEqual(len(stages),1)
        self.init(); self.assertTrue(stages[0].exists())
        from bookmark_wiki import learning
        p=self.event('register_batch',self.registration()); old=self.db.read_bytes()
        def fault(point):
            if point=='before_revalidate': self.authority_file.write_text('changed evidence')
        with self.assertRaises(learning.LearningError) as e: learning.apply(self.vault,p,fault=fault)
        self.assertEqual(e.exception.code,'INTEGRITY_ERROR'); self.assertEqual(old,self.db.read_bytes())

    def test_learning_cli_and_offline_boundary(self):
        for args in [(),('wat',),('status',),('apply','--vault',self.vault),('check','--vault',self.vault,'--batch-id','bad'),('init','--vault',self.vault,'--force'),('--help',)]:
            r=cli('learning',*args)
            if args==('--help',): self.assertEqual(r.returncode,0)
            else: self.result(r,False,'INVALID_ARGUMENT')
        self.init(); self.result(cli('learning','check','--vault',self.vault,'--batch-id','0'*64),False,'NOT_FOUND')
        from bookmark_wiki import learning
        p=self.event('register_batch',self.registration())
        with patch.object(socket,'socket',side_effect=AssertionError('network')),patch.object(subprocess,'Popen',side_effect=AssertionError('provider/subprocess')),patch.object(os,'system',side_effect=AssertionError('shell')):
            data=learning.apply(self.vault,p,clock=lambda: datetime.datetime(2026,1,2,3,4,5,tzinfo=datetime.timezone.utc))
            self.assertTrue(data['applied']); self.assertEqual(learning.status(self.vault)['batches'],1)
            learning.check(self.vault,data['event_id'])
        with closing(sqlite3.connect(self.db)) as c: self.assertEqual(c.execute('select recorded_at from events').fetchone()[0],NOW)
        self.db.write_bytes(b'corrupt'); before=inventory(self.base); self.result(cli('learning','status','--vault',self.vault),False,'INTEGRITY_ERROR'); self.assertEqual(before,inventory(self.base))

    def test_c10_status_volume_limit(self):
        self.init()
        from bookmark_wiki import learning
        # Build real registered batches, two profiles per target = ten each.
        for i in range(11):
            p=self.registration(profiles=('finn','vidya'),count=5,revision='revision-'+str(i))
            learning.apply(self.vault,self.event('register_batch',p))
        before=inventory(self.base); self.result(cli('learning','status','--vault',self.vault),False,'INPUT_TOO_LARGE'); self.assertEqual(before,inventory(self.base))

    def test_c7_disposition_snapshot_for_all_outcomes(self):
        for disposition in ('adopted','reference_saved','already_covered','not_useful','blocked'):
            with self.subTest(disposition=disposition):
                case=LearningContractTests(); case.setUp()
                try:
                    case.init(); batch,ids,payload=case.accepted(disposition)
                    expected='declined' if disposition=='not_useful' else 'blocked' if disposition=='blocked' else 'accepted'
                    case.apply('receipt_accepted',{**payload,'disposition':'not_useful' if disposition!='not_useful' else 'adopted'},False,'INTEGRITY_ERROR')
                    receipt=Path(payload['receipt']['path']); original=receipt.read_bytes()
                    for mode in ('missing','changed'):
                        with case.subTest(mode=mode):
                            try:
                                if mode=='missing': receipt.unlink()
                                else:
                                    changed=json.loads(original); changed['source_decisions'][0]['disposition']='blocked' if disposition!='blocked' else 'adopted'
                                    receipt.write_bytes(canon(changed))
                                before=inventory(case.base)
                                case.assertEqual(case.status()['destinations'][0]['recorded_state'],expected)
                                row=case.check(batch)['results'][0]
                                case.assertEqual(row['recorded_state'],expected)
                                case.assertEqual(row['effective_state'],'needs_reconciliation')
                                case.assertEqual(row['reason_code'],'MISSING_EVIDENCE' if mode=='missing' else 'HASH_MISMATCH')
                                case.assertEqual(before,inventory(case.base))
                            finally: receipt.write_bytes(original)
                finally: case.doCleanups()

    def test_c5_replay_old_events_after_later_observations(self):
        self.init(); b,ids=self.register(); intent=self.intent(b,ids)['event_id']
        self.observe(b,ids,intent,status='running')
        self.observe(b,ids,intent,status='done',when='2026-01-02T04:00:00Z')
        correction=self.intent(b,ids,ordinal=1)['event_id']
        self.observe(b,ids,correction,ordinal=1,task='t_00000003',status='done',when='2026-01-02T05:00:00Z')
        before=(self.db.read_bytes(),self.db.stat().st_mtime_ns)
        for path in sorted(self.inputs.glob('event-*.json')):
            self.assertFalse(self.result(cli('learning','apply','--vault',self.vault,'--input',path))['applied'])
        self.assertEqual(before,(self.db.read_bytes(),self.db.stat().st_mtime_ns))
        self.assertEqual(self.status()['destinations'][0]['observed_task_status'],'done')

    def test_c1_corrupt_existing_init_never_creates_lock(self):
        self.db.write_bytes(b'corrupt'); before=inventory(self.base)
        self.result(cli('learning','init','--vault',self.vault),False,'INTEGRITY_ERROR')
        self.assertEqual(before,inventory(self.base))

    def test_c1_nonregular_and_lexical_input_rejections(self):
        self.init()
        bad=self.inputs/'directory'; bad.mkdir()
        for path in (bad,self.inputs/'..'/'evidence'/'outside.json',self.evidence/'outside.json'):
            before=inventory(self.base)
            self.result(cli('learning','apply','--vault',self.vault,'--input',path),False,'INVALID_ARGUMENT')
            self.assertEqual(before,inventory(self.base))
        fifo=self.inputs/'fifo'; os.mkfifo(fifo)
        self.result(cli('learning','apply','--vault',self.vault,'--input',fifo),False,'INVALID_ARGUMENT')

    def test_c2_inclusive_strings_arrays_and_literal_hashes(self):
        self.init()
        def remap(payload):
            tid=digest({k:payload['targets'][0][k] for k in ('identity_url','revision','scope_read')})
            payload['links'][0]['target_id']=tid; payload['destinations'][0]['target_id']=tid
            return payload
        for length in (128,129):
            payload=self.registration(); payload['targets'][0]['revision']='r'*length
            self.apply('register_batch',remap(payload),length==128,None if length==128 else 'INVALID_ARGUMENT')
        for length in (256,257):
            payload=self.registration(); payload['targets'][0]['scope_read']=['s'*length]
            self.apply('register_batch',remap(payload),length==256,None if length==256 else 'INVALID_ARGUMENT')
        for length in (20,21):
            payload=self.registration(); payload['targets'][0]['scope_read']=['scope-'+str(i) for i in range(length)]
            self.apply('register_batch',remap(payload),length==20,None if length==20 else 'INVALID_ARGUMENT')
        payload=self.registration(); payload['targets'][0]['scope_read']=['b','a']; remap(payload)
        self.apply('register_batch',payload); first=payload['links'][0]['target_id']
        payload['targets'][0]['scope_read']=['a','b']; remap(payload)
        self.assertNotEqual(first,payload['links'][0]['target_id']); self.apply('register_batch',payload)
        for value in ('A'*64,'a'*63,'a'*65,'é'*64,True):
            payload=self.registration(); payload['packet']['sha256']=value
            self.apply('register_batch',payload,False,'INVALID_ARGUMENT')

    def test_c3_canonical_packet_and_all_reference_roots(self):
        self.init()
        payload=self.registration(); path=Path(payload['packet']['path']); original=path.read_bytes()
        path.write_text(json.dumps(json.loads(original),indent=2)); payload['packet']=ref(path)
        self.apply('register_batch',payload,False,'INTEGRITY_ERROR'); path.write_bytes(original)
        capture=self.evidence/'bounded-capture.txt'; capture.write_text('independently retained synthetic capture')
        payload=self.registration(); payload['targets'][0]['evidence']=[ref(capture)]
        self.apply('register_batch',payload)
        for changes in ({'extra':True},{'sha256':'0'*64}):
            bad=self.registration(); bad['targets'][0]['evidence']=[{**ref(capture),**changes}]
            self.apply('register_batch',bad,False)
        for key in ('targets','links','destinations'):
            bad=self.registration(); bad[key][0]['unexpected']=True
            self.apply('register_batch',bad,False,'INVALID_ARGUMENT')

    def test_c4_cross_batch_binding_and_grouping_caps(self):
        self.init(); batch,ids=self.register(count=2)
        intent=self.intent(batch,ids)['event_id']
        self.assertEqual({d['recorded_state'] for d in self.status()['destinations']},{'dispatch_uncertain'})
        other,other_ids=self.register(count=2,generation='b')
        self.observe(other,other_ids,intent,ok=False,code='INVALID_TRANSITION')
        self.observe(batch,ids,intent)
        # An overlap consumes a destination's attempt even when regrouped.
        self.intent(other,[other_ids[0]],ok=False,code='INVALID_TRANSITION')
        self.observe(batch,ids,intent,task='t_00000002',ok=False,code='INTEGRITY_ERROR')

    def test_c5_precise_timestamps_and_binding_invariants(self):
        self.init(); b,ids=self.register(); intent=self.intent(b,ids)['event_id']
        self.observe(b,ids,intent,status='running',when='2026-01-02T03:04:05.1Z')
        self.observe(b,ids,intent,status='done',when='2026-01-02T03:04:05.10Z')
        self.observe(b,ids,intent,status='running',when='2026-01-02T03:04:05.09Z',ok=False,code='INTEGRITY_ERROR')
        for task in ('t_0000000A','t_00000001junk','00000001'):
            self.observe(b,ids,intent,task=task,ok=False,code='INVALID_ARGUMENT')

    def test_c6_approved_batch_requires_done_reading(self):
        self.init(); b,ids=self.register(); intent=self.intent(b,ids)['event_id']
        self.observe(b,ids,intent,status='running')
        payload=self.receipt(b,ids[0],disposition='not_useful')
        self.apply('receipt_accepted',payload,False,'INTEGRITY_ERROR')
        self.observe(b,ids,intent,status='done',when='2026-01-02T04:00:00Z')
        self.apply('receipt_accepted',payload)
        self.assertEqual(self.check(b)['results'][0]['effective_state'],'known_noop')

    def test_c6_every_changed_path_and_unselected_artifact_is_checked(self):
        self.init(); b,ids=self.register(historical=True); self.observe(b,ids)
        self.observe(b,ids,stage='application',task='t_00000002')
        payload=self.receipt(b,ids[0]); receipt=json.loads(Path(payload['receipt']['path']).read_bytes())
        reference=self.home/'profiles/finn/skills/creative/visual-style-research/references/new.md'
        reference.parent.mkdir(); reference.write_text('extra installed reference')
        receipt['changes'].append({'path':str(reference),'action':'create','before_sha256':None,'after_sha256':'0'*64})
        rr=self.write(self.evidence/'multi-path.json',receipt)
        review=json.loads(Path(payload['review']['path']).read_bytes()); review['receipt_sha256']=rr['sha256']
        vr=self.write(self.evidence/'multi-review.json',review)
        self.apply('receipt_accepted',{**payload,'receipt':rr,'review':vr},False,'INTEGRITY_ERROR')
        receipt['changes'][-1]['after_sha256']=ref(reference)['sha256']
        outside=self.evidence/'other-artifact.txt'; outside.write_text('other source, same application')
        receipt['application']['artifacts'].append(ref(outside))
        rr=self.write(self.evidence/'multi-path.json',receipt); review['receipt_sha256']=rr['sha256']
        vr=self.write(self.evidence/'multi-review.json',review)
        self.apply('receipt_accepted',{**payload,'receipt':rr,'review':vr})
        outside.write_text('changed other artifact')
        before=inventory(self.base); self.assertEqual(self.check(b)['results'][0]['reason_code'],'HASH_MISMATCH'); self.assertEqual(before,inventory(self.base))

    def test_c3_file_size_and_per_event_reference_ceilings(self):
        self.init()
        # Sparse files exercise actual byte ceilings, not a patched constant.
        capture=self.evidence/'large.bin'
        for size in (67108864,67108865):
            with capture.open('wb') as f: f.truncate(size)
            payload=self.registration(); payload['targets'][0]['evidence']=[ref(capture)]
            self.apply('register_batch',payload,size==67108864,None if size==67108864 else 'INPUT_TOO_LARGE')
        small=self.evidence/'small.txt'; small.write_text('capture')
        for count in (98,99):
            payload=self.registration(); payload['targets'][0]['evidence']=[ref(small)]*count
            self.apply('register_batch',payload,count==98,None if count==98 else 'INPUT_TOO_LARGE')

    def test_c6_installed_size_ceiling_and_native_correction(self):
        self.init(); b,ids=self.register(historical=True); self.observe(b,ids)
        self.observe(b,ids,stage='application',task='t_00000002')
        self.observe(b,ids,stage='application',ordinal=1,task='t_00000003')
        payload=self.receipt(b,ids[0]); receipt=json.loads(Path(payload['receipt']['path']).read_bytes())
        receipt['task_ids'].append('t_00000003'); receipt['application']['correction_task_id']='t_00000003'
        installed=Path(receipt['changes'][0]['path'])
        for size in (1048576,1048577):
            installed.write_bytes(b'a'*size); receipt['changes'][0]['after_sha256']=ref(installed)['sha256']
            rr=self.write(self.evidence/('size-'+str(size)+'.json'),receipt)
            review=json.loads(Path(payload['review']['path']).read_bytes()); review['receipt_sha256']=rr['sha256']
            vr=self.write(self.evidence/('review-size-'+str(size)+'.json'),review)
            self.apply('receipt_accepted',{**payload,'receipt':rr,'review':vr},size==1048576,None if size==1048576 else 'INPUT_TOO_LARGE')

    def test_c7_native_retry_does_not_invalidate_prior_acceptance(self):
        self.init(); batch,ids,payload=self.accepted()
        self.observe(batch,ids,stage='application',task='t_00000002',status='running',when='2026-01-02T06:00:00Z')
        row=self.status()['destinations'][0]
        self.assertEqual(row['recorded_state'],'accepted'); self.assertEqual(row['observed_task_status'],'running')
        before=inventory(self.base)
        self.assertEqual(self.check(batch)['results'][0]['effective_state'],'known_noop')
        self.assertEqual(before,inventory(self.base))

    def test_c4_blocked_account_can_take_its_single_correction(self):
        self.init(); batch,ids=self.register(); intent=self.intent(batch,ids)['event_id']
        self.observe(batch,ids,intent,status='blocked')
        payload=self.receipt(batch,ids[0],disposition='blocked'); self.apply('receipt_accepted',payload)
        self.assertFalse(self.check(batch)['locally_complete'])
        self.intent(batch,ids,ordinal=1)
        self.assertEqual(self.status()['destinations'][0]['recorded_state'],'dispatch_uncertain')
        self.assertEqual(self.check(batch)['results'][0]['effective_state'],'dispatch_uncertain')

    def test_c4_unresolved_imported_task_blocks_overlapping_stage(self):
        self.init(); batch,ids=self.register(historical=True)
        self.observe(batch,ids,status='done')
        self.observe(batch,ids,stage='application',task='t_00000002',status='running')
        other,other_ids=self.register(generation='b')
        self.intent(other,other_ids,ordinal=1,ok=False,code='INVALID_TRANSITION')

    def test_c9_same_event_committed_between_validation_and_lock(self):
        from contextlib import contextmanager
        from bookmark_wiki import learning
        self.init(); path=self.event('register_batch',self.registration())
        real_lock=learning._lock
        @contextmanager
        def intervening_commit(ctx):
            # Real competing process commits after caller's prevalidation.
            self.assertTrue(self.result(cli('learning','apply','--vault',self.vault,'--input',path))['applied'])
            with real_lock(ctx): yield
        with patch.object(learning,'_lock',intervening_commit):
            self.assertFalse(learning.apply(self.vault,path)['applied'])
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(c.execute('select count(*) from events').fetchone()[0],1)

if __name__=='__main__': unittest.main()

