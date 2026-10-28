import hashlib, json, os, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from test_archive import make_snapshot, bookmark, write_inputs, run_cli, ROOT

class IntakeTests(unittest.TestCase):
    def fixture(self):
        td=tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")); self.addCleanup(td.cleanup)
        base=Path(td.name); vault=base/"vault"; vault.mkdir()
        snap=make_snapshot(bookmarks=[bookmark("b1","1001","Alpha Ω"),bookmark("b2","1002","Beta")],categories=[{"id":"c1","name":"Research","slug":"research"}],assignments=[{"bookmarkId":"b1","categoryId":"c1"},{"bookmarkId":"b2","categoryId":"c1"}])
        s,h=write_inputs(vault,snap,{"A - 1001.md":"a","B - 1002.md":"b"})
        self.assertEqual(run_cli("update","--vault",vault,"--snapshot",s,"--raw-hashes",h).returncode,0)
        root=vault/"state/library-intake-pilot-v1"; root.mkdir(parents=True)
        sel=root/"selection.json"; sel.write_text(json.dumps({"schema_version":1,"source_ids":["x:1002","x:1001"]}))
        return vault,root,sel,base
    def run_intake(self,vault,root,sel): return run_cli("intake","--vault",vault,"--selection",sel,"--output-root",root)
    def test_intake_exports_selected_revision(self):
        v,r,sel,_=self.fixture()
        out=self.run_intake(v,r,sel); self.assertEqual(out.returncode,0,out.stdout)
        d=json.loads(out.stdout)["data"]; packet=(r/"packets"/d["packet_id"]/"packet.json").read_bytes(); obj=json.loads(packet)
        self.assertEqual([x["source_id"] for x in obj["records"]],["x:1001","x:1002"])
        self.assertEqual(d["packet_id"],hashlib.sha256(packet).hexdigest())
        self.assertEqual(set(obj),{"schema_version","adapter_version","manifest_sha256","records"})
        self.assertEqual(set(obj["records"][0]),{"source_id","tweet_id","current_revision","revision_count","content","metadata","evidence_path","raw_path","context_status","integration_status","wiki_pages","removal_eligible","warnings"})
    def test_selection_rejects_invalid_bounds(self):
        v,r,sel,_=self.fixture()
        for raw in ['{"schema_version":1,"source_ids":[]}', '{"schema_version":1,"source_ids":["x:1","x:1"]}', '{"schema_version":1,"source_ids":["1"]}', '{"schema_version":true,"source_ids":["x:1"]}', '{"schema_version":1,"source_ids":["x:1"],"extra":0}', '{"schema_version":1,"schema_version":1,"source_ids":["x:1"]}']:
            sel.write_text(raw); out=self.run_intake(v,r,sel); self.assertEqual(json.loads(out.stdout)["error"]["code"],"INVALID_ARGUMENT"); self.assertFalse((r/"packets").exists())
    def test_intake_fails_closed_on_reader_change(self):
        v,r,sel,_=self.fixture(); sel.write_text('{"schema_version":1,"source_ids":["x:999"]}')
        out=self.run_intake(v,r,sel); self.assertEqual(json.loads(out.stdout)["error"]["code"],"NOT_FOUND"); self.assertFalse((r/"packets").exists())
    def test_intake_replay_and_conflict(self):
        v,r,sel,_=self.fixture(); a=self.run_intake(v,r,sel); d=json.loads(a.stdout)["data"]; path=r/"packets"/d["packet_id"]/"packet.json"; before=path.stat().st_mtime_ns
        sel.write_text('{"schema_version":1,"source_ids":["x:1001","x:1002"]}'); b=self.run_intake(v,r,sel); self.assertFalse(json.loads(b.stdout)["data"]["created"]); self.assertEqual(path.stat().st_mtime_ns,before)
        path.write_bytes(b"bad"); c=self.run_intake(v,r,sel); self.assertEqual(json.loads(c.stdout)["error"]["code"],"INTEGRITY_ERROR"); self.assertEqual(path.read_bytes(),b"bad")
    def test_output_boundary_and_crash_publication(self):
        v,r,sel,_=self.fixture(); alias=r.parent/"alias"; alias.symlink_to(r,target_is_directory=True)
        out=self.run_intake(v,alias,sel); self.assertEqual(json.loads(out.stdout)["error"]["code"],"INVALID_ARGUMENT"); self.assertFalse((r/"packets").exists())
        out=self.run_intake(v,r,sel); self.assertEqual(out.returncode,0,out.stdout)
    def test_intake_is_offline_and_read_only(self):
        v,r,sel,_=self.fixture(); protected={p.relative_to(v).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in v.rglob('*') if p.is_file() and 'state/' not in p.as_posix()}
        out=self.run_intake(v,r,sel); self.assertEqual(out.returncode,0,out.stdout)
        after={p.relative_to(v).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in v.rglob('*') if p.is_file() and 'state/' not in p.as_posix()}; self.assertEqual(protected,after)
    def test_intake_cli_json_contract(self):
        v,r,sel,_=self.fixture(); out=self.run_intake(v,r,sel); self.assertEqual(out.returncode,0); self.assertEqual(set(json.loads(out.stdout)["data"]),{"packet_id","record_count","created"})
        sel.write_text('{bad'); out=self.run_intake(v,r,sel); self.assertEqual(out.returncode,2); self.assertNotIn(str(v),out.stdout); self.assertNotIn("Alpha",out.stdout)

    def test_selection_may_be_nested_beneath_dedicated_root(self):
        v,r,sel,_=self.fixture(); nested=r/"operator"; nested.mkdir(); ns=nested/"selection.json"; ns.write_text(sel.read_text())
        out=self.run_intake(v,r,ns); self.assertEqual(out.returncode,0,out.stdout)

    def test_cli_rejects_final_packet_symlink_without_following_it(self):
        v,r,sel,base=self.fixture(); out=self.run_intake(v,r,sel); pid=json.loads(out.stdout)["data"]["packet_id"]
        target=r/"packets"/pid/"packet.json"; outside=base/"outside.json"; outside.write_bytes(target.read_bytes()); target.unlink(); target.symlink_to(outside)
        before=outside.read_bytes(); out=self.run_intake(v,r,sel)
        self.assertEqual(json.loads(out.stdout)["error"]["code"],"INTEGRITY_ERROR"); self.assertEqual(outside.read_bytes(),before)

    def test_cli_rejects_symlinked_selection_and_child_before_any_write(self):
        v,r,sel,base=self.fixture(); alt=base/"real-selection.json"; alt.write_text(sel.read_text()); sel.unlink(); sel.symlink_to(alt)
        out=self.run_intake(v,r,sel); self.assertEqual(json.loads(out.stdout)["error"]["code"],"INVALID_ARGUMENT"); self.assertFalse((r/".intake.lock").exists())
        sel.unlink(); sel.write_text('{"schema_version":1,"source_ids":["x:1001"]}')
        (r/"packets").symlink_to(base/"outside",target_is_directory=True)
        out=self.run_intake(v,r,sel); self.assertEqual(json.loads(out.stdout)["error"]["code"],"INVALID_ARGUMENT"); self.assertFalse((r/".intake.lock").exists())

    def test_real_lock_contention_and_crash_replay(self):
        import fcntl
        from bookmark_wiki import library_intake
        v,r,sel,_=self.fixture(); lockpath=r/".intake.lock"
        with lockpath.open('a+b') as lock:
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            out=self.run_intake(v,r,sel); self.assertEqual(json.loads(out.stdout)["error"]["code"],"LOCKED"); fcntl.flock(lock.fileno(),fcntl.LOCK_UN)
        for fault in ('before_rename','after_rename'):
            v,r,sel,_=self.fixture()
            with self.assertRaises(library_intake.IntakeError) as caught: library_intake.intake(v,sel,r,fault_at=fault)
            self.assertEqual(caught.exception.code,'IO_ERROR')
            out=self.run_intake(v,r,sel)
            if fault=='before_rename': self.assertEqual(out.returncode,0,out.stdout)
            else: self.assertFalse(json.loads(out.stdout)["data"]["created"])

    def inventory(self, root):
        return {str(p.relative_to(root)): ('link', os.readlink(p)) if p.is_symlink()
                else ('file', hashlib.sha256(p.read_bytes()).hexdigest())
                for p in root.rglob('*') if p.is_file() or p.is_symlink()}

    def test_one_five_six_ids_and_exact_selection_byte_limit_cli(self):
        v, r, sel, base = self.fixture()
        v = base/'six-record-vault'; v.mkdir()
        r = v/'state/library-intake-pilot-v1'; r.mkdir(parents=True)
        sel = r/'selection.json'
        snapshot = make_snapshot(
            bookmarks=[bookmark('b'+str(i), str(1000+i), 'record '+str(i)) for i in range(1, 7)],
            categories=[{'id':'c1','name':'Research','slug':'research'}],
            assignments=[{'bookmarkId':'b'+str(i),'categoryId':'c1'} for i in range(1,7)])
        sp, hp = write_inputs(v, snapshot, {'N - '+str(1000+i)+'.md':str(i) for i in range(1,7)}, wave='second')
        self.assertEqual(run_cli('update', '--vault', v, '--snapshot', sp, '--raw-hashes', hp).returncode, 0)
        from bookmark_wiki import retrieval
        for count in (1, 5):
            ids = ['x:'+str(1000+i) for i in range(1,count+1)]
            raw = json.dumps({'schema_version':1, 'source_ids':ids}).encode()
            sel.write_bytes(raw + b' '*(16384-len(raw)))
            result = self.run_intake(v,r,sel)
            self.assertEqual(result.returncode,0,result.stdout)
            data = json.loads(result.stdout)['data']
            obj = json.loads((r/'packets'/data['packet_id']/'packet.json').read_bytes())
            self.assertEqual(obj['records'], [retrieval.show(v, sid) for sid in ids])
            self.assertEqual(obj['manifest_sha256'], retrieval.status(v)['manifest_sha256'])
            self.assertEqual(data['record_count'],count)
            before = self.inventory(r)
            sel.write_bytes(sel.read_bytes()+b' ')
            before = self.inventory(r)
            result = self.run_intake(v,r,sel)
            self.assertEqual(json.loads(result.stdout)['error']['code'],'INPUT_TOO_LARGE')
            self.assertEqual(before,self.inventory(r))
        sel.write_text(json.dumps({'schema_version':1,'source_ids':['x:'+str(i) for i in range(6)]}))
        before=self.inventory(r)
        result=self.run_intake(v,r,sel)
        self.assertEqual(json.loads(result.stdout)['error']['code'],'INVALID_ARGUMENT')
        self.assertEqual(before,self.inventory(r))

    def test_real_stale_and_corrupt_readers_preserve_archive(self):
        for kind in ('stale', 'corrupt'):
            v,r,sel,_=self.fixture()
            if kind=='stale': (v/'derived/search.sqlite3').unlink()
            else: (v/'manifest.json').write_bytes(b'{invalid')
            before=self.inventory(v)
            result=self.run_intake(v,r,sel)
            self.assertEqual(result.returncode,2)
            self.assertEqual(json.loads(result.stdout)['error']['code'],
                             'STALE_INDEX' if kind=='stale' else 'CORRUPT_MANIFEST')
            after=self.inventory(v)
            after.pop('state/library-intake-pilot-v1/.intake.lock',None)
            self.assertEqual(before,after)

    def test_generation_change_aborts_without_packet(self):
        from bookmark_wiki import library_intake as li
        v,r,sel,_=self.fixture()
        status=li.retrieval.status(v)
        with patch.object(li.retrieval,'status',side_effect=[status,{**status,'manifest_sha256':'0'*64}]):
            with self.assertRaises(li.IntakeError) as caught: li.intake(v,sel,r)
        self.assertEqual(caught.exception.code,'SOURCE_CHANGED')
        self.assertFalse((r/'packets').exists())

    def test_offline_tripwires_and_protected_inventory(self):
        import socket, subprocess, urllib.request
        from contextlib import ExitStack
        from bookmark_wiki import library_intake as li
        v,r,sel,_=self.fixture()
        before=self.inventory(v)
        with ExitStack() as stack:
            for obj,name in ((socket,'socket'),(socket,'create_connection'),(urllib.request,'urlopen'),(subprocess,'Popen'),(os,'system')):
                stack.enter_context(patch.object(obj,name,side_effect=AssertionError('offline boundary violated')))
            data=li.intake(v,sel,r)
        self.assertTrue(data['created'])
        after={k:val for k,val in self.inventory(v).items() if not k.startswith('state/library-intake-pilot-v1/')}
        self.assertEqual(after,{k:val for k,val in before.items() if not k.startswith('state/library-intake-pilot-v1/')})

    def test_record_exact_ceiling_and_packet_guard(self):
        from bookmark_wiki import library_intake as li
        v,r,sel,_=self.fixture()
        sel.write_text('{"schema_version":1,"source_ids":["x:1001"]}')
        record=li.retrieval.show(v,'x:1001')
        record['content']['text']=''
        remaining=131072-len(li._canon(record))
        record['content']['text']='é'*(remaining//2)+'a'*(remaining%2)
        self.assertEqual(len(li._canon(record)),131072)
        with patch.object(li.retrieval,'show',return_value=record):
            self.assertTrue(li.intake(v,sel,r)['created'])
        record['content']['text']+='a'
        with patch.object(li.retrieval,'show',return_value=record):
            with self.assertRaises(li.IntakeError) as caught: li.intake(v,sel,r)
        self.assertEqual(caught.exception.code,'INPUT_TOO_LARGE')
        # Whole-packet ceiling cannot be reached by five valid capped records.
        # Inject serializer bytes at that boundary, leaving real record checks intact.
        original=li._canon
        for size in (1048576,1048577):
            v,r,sel,_=self.fixture()
            def serialize(obj):
                return b' '*size if 'adapter_version' in obj else original(obj)
            with patch.object(li,'_canon',side_effect=serialize):
                if size==1048576: self.assertTrue(li.intake(v,sel,r)['created'])
                else:
                    with self.assertRaises(li.IntakeError) as caught: li.intake(v,sel,r)
                    self.assertEqual(caught.exception.code,'INPUT_TOO_LARGE')
                    self.assertFalse((r/'packets').exists())

    def test_state_symlink_is_rejected_before_missing_root_creation(self):
        v,r,sel,base=self.fixture()
        # Keep a real selection under an outside target to exercise the review claim.
        outside=base/'outside-state'; outside.mkdir()
        import shutil
        shutil.rmtree(v/'state')
        (v/'state').symlink_to(outside, target_is_directory=True)
        before=self.inventory(base)
        result=self.run_intake(v,r,sel)
        self.assertEqual(result.returncode,2)
        self.assertEqual(json.loads(result.stdout)['error']['code'],'INVALID_ARGUMENT')
        self.assertFalse((outside/'library-intake-pilot-v1').exists())
        self.assertEqual(before,self.inventory(base))

    def test_lock_symlink_and_incomplete_replay_cli(self):
        v,r,sel,base=self.fixture()
        outside=base/'outside'; outside.write_bytes(b'unchanged')
        (r/'.intake.lock').symlink_to(outside)
        before=self.inventory(base)
        result=self.run_intake(v,r,sel)
        self.assertEqual(json.loads(result.stdout)['error']['code'],'INVALID_ARGUMENT')
        self.assertEqual(before,self.inventory(base))
        (r/'.intake.lock').unlink()
        result=self.run_intake(v,r,sel)
        target=r/'packets'/json.loads(result.stdout)['data']['packet_id']/'packet.json'
        target.unlink()
        before=self.inventory(base)
        result=self.run_intake(v,r,sel)
        self.assertEqual(json.loads(result.stdout)['error']['code'],'INTEGRITY_ERROR')
        self.assertEqual(before,self.inventory(base))

    def test_relative_paths_normalize_before_comparison_without_alias_loss(self):
        v,r,sel,_=self.fixture()
        result=self.run_intake(os.path.relpath(v,ROOT),os.path.relpath(r,ROOT),os.path.relpath(sel,ROOT))
        # Parent traversal is explicitly forbidden, so demonstrate safe relative paths
        # from the vault rather than ../ paths from the source checkout.
        from bookmark_wiki import library_intake as li
        old=Path.cwd()
        try:
            os.chdir(v)
            result=li.intake('.', 'state/library-intake-pilot-v1/selection.json','state/library-intake-pilot-v1')
            self.assertTrue(result['created'])
        finally: os.chdir(old)

    def test_record_limit_helper_boundaries(self):
        from bookmark_wiki import library_intake
        self.assertTrue(hasattr(library_intake,'_canon'))
        record={'content':'é'*10}
        self.assertEqual(len(library_intake._canon(record)),35)
        self.assertFalse(library_intake._record_too_large({'content':'é'*65000}))
        self.assertTrue(library_intake._record_too_large({'content':'é'*65540}))
