"""Offline, bounded export of selected BookmarkWiki records."""
from __future__ import annotations
import fcntl, hashlib, json, os, re, shutil, tempfile
from pathlib import Path
from . import retrieval, archive

FIELDS=("source_id","tweet_id","current_revision","revision_count","content","metadata","evidence_path","raw_path","context_status","integration_status","wiki_pages","removal_eligible","warnings")
ID=re.compile(r"x:[0-9]+\Z")
class IntakeError(Exception):
    def __init__(self, code): self.code=code
class SimulatedFault(Exception): pass
def _canon(obj): return (json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n").encode("utf-8")
def _within(path, root):
    try: Path(path).relative_to(root); return True
    except ValueError: return False
def _record_too_large(record, limit=131072): return len(_canon(record)) > limit
def _lex(path):
    original = Path(os.fspath(path))
    if not original.is_absolute():
        original = Path.cwd() / original
    if ".." in original.parts:
        raise IntakeError("INVALID_ARGUMENT")
    p = original
    if ".." in p.parts: raise IntakeError("INVALID_ARGUMENT")
    cur=Path(p.anchor)
    for part in p.parts[1:]:
        cur=cur/part
        if cur.is_symlink(): raise IntakeError("INVALID_ARGUMENT")
    return p
def _json_no_dupes(pairs):
    d={}
    for k,v in pairs:
        if k in d: raise ValueError()
        d[k]=v
    return d
def intake(vault, selection, output_root, *, fault_at=None):
    try:
        v=_lex(vault); s=_lex(selection); o=_lex(output_root)
        expected=v/"state/library-intake-pilot-v1"
        if o != expected or not _within(s, expected): raise IntakeError("INVALID_ARGUMENT")
        if not v.is_dir() or not s.is_file(): raise IntakeError("NOT_FOUND")
        if s.stat().st_size>16384: raise IntakeError("INPUT_TOO_LARGE")
        try: sel=json.loads(s.read_text(encoding="utf-8"),object_pairs_hook=_json_no_dupes)
        except Exception: raise IntakeError("INVALID_ARGUMENT")
        if type(sel) is not dict or set(sel)!={"schema_version","source_ids"} or type(sel["schema_version"]) is not int or sel["schema_version"]!=1: raise IntakeError("INVALID_ARGUMENT")
        ids=sel["source_ids"]
        if type(ids) is not list or not 1<=len(ids)<=5 or any(type(x) is not str or not ID.fullmatch(x) for x in ids) or len(set(ids))!=len(ids): raise IntakeError("INVALID_ARGUMENT")
        if not expected.exists(): expected.mkdir(parents=True)
        _lex(expected); lockpath=expected/".intake.lock"; _lex(lockpath)
        packets=expected/"packets"; _lex(packets)
        with lockpath.open("a+b") as lock:
            try: fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: raise IntakeError("LOCKED")
            before=retrieval.status(v); records=[]
            for sid in sorted(ids):
                rec=retrieval.show(v,sid)
                # show canonicalizes bare numeric IDs; input contract intentionally does not.
                records.append({k:rec[k] for k in FIELDS})
            after=retrieval.status(v)
            if before["manifest_sha256"]!=after["manifest_sha256"]: raise IntakeError("SOURCE_CHANGED")
            packet={"schema_version":1,"adapter_version":"1.0","manifest_sha256":before["manifest_sha256"],"records":records}
            data=_canon(packet)
            if any(_record_too_large(r) for r in records) or len(data)>1048576: raise IntakeError("INPUT_TOO_LARGE")
            pid=hashlib.sha256(data).hexdigest()
            if packets.exists() and not packets.is_dir(): raise IntakeError("INTEGRITY_ERROR")
            packets.mkdir(exist_ok=True); target=packets/pid; _lex(target)
            if target.exists():
                packet_file=target/"packet.json"
                if packet_file.is_symlink() or not packet_file.is_file() or not target.is_dir() or {p.name for p in target.iterdir()}!={"packet.json"} or packet_file.read_bytes()!=data: raise IntakeError("INTEGRITY_ERROR")
                return {"packet_id":pid,"record_count":len(records),"created":False}
            stage=Path(tempfile.mkdtemp(prefix=".intake-stage-",dir=packets)); _lex(stage)
            try:
                f=stage/"packet.json"; _lex(f)
                with f.open("xb") as out: out.write(data); out.flush(); os.fsync(out.fileno())
                dfd=os.open(stage,os.O_RDONLY); os.fsync(dfd); os.close(dfd)
                if fault_at=="before_rename": raise SimulatedFault()
                os.rename(stage,target)
                dfd=os.open(packets,os.O_RDONLY); os.fsync(dfd); os.close(dfd)
                if fault_at=="after_rename": raise SimulatedFault()
            except SimulatedFault: raise
            except Exception: raise IntakeError("IO_ERROR")
            return {"packet_id":pid,"record_count":len(records),"created":True}
    except IntakeError: raise
    except archive.BookmarkWikiError as e: raise IntakeError(e.code) from e
    except OSError as e: raise IntakeError("IO_ERROR") from e
    except Exception as e: raise IntakeError("IO_ERROR") from e
