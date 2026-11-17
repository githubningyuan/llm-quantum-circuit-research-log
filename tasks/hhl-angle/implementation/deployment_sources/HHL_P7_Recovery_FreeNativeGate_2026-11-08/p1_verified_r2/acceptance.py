#!/usr/bin/env python3
"""Single-purpose root acceptance of isolated P1 HHL evidence provenance.
No OpenCode, model, Coulomb DB, or formal HHL ProgramDatabase operations.
Runs only a pinned old third-round source that already passed two native replays.
"""
from __future__ import annotations
import argparse, base64, binascii, hashlib, importlib.util, json, os, re
from pathlib import Path
import stat, subprocess, sys, time

STAGE=Path('/run/hhl-task2/p1-review-code')
BROKER=STAGE/'broker'
EVAL=STAGE/'eval'
CLIENT=STAGE/'client.py'
RECORDS=Path('/srv/hhl-task2/research-records')
P0=Path('/root/hhl_p0_native_replay.py')
BEST='2c24272933b88b228fb664d43832eec5f625d0f2e03c214e0cef48991a428661'
BUNDLE='b1d842e8ed28efef79b41d25125013694985c171cc9eb2778987393439c4c444'
BROKER_SPOOL=Path('/srv/hhl-task2/p1-review-broker-spool')
EVAL_SPOOL=Path('/srv/hhl-task2/p1-review-evaluator-spool')
HEX32=re.compile(r'[0-9a-f]{32}\Z')

class Stop(Exception):pass

def sha(data:bytes)->str:return hashlib.sha256(data).hexdigest()

def pairs(p):
    o={}
    for k,v in p:
        if k in o:raise Stop('Duplicate JSON key')
        o[k]=v
    return o

def read_manifest():
    for line in (STAGE/'SHA256SUMS.txt').read_text().splitlines():
        m=re.fullmatch(r'([0-9a-f]{64})  ([a-zA-Z0-9_/.-]+)',line)
        if not m:raise Stop('Invalid stage manifest')
        p=STAGE/m[2]
        if not p.is_relative_to(STAGE) or '..' in Path(m[2]).parts or p.is_symlink() or not p.is_file() or sha(p.read_bytes())!=m[1]:
            raise Stop('Stage file SHA mismatch: '+m[2])

def load():
    if os.geteuid()!=0:raise Stop('root operator required')
    read_manifest()
    if sha(P0.read_bytes())!='c44f560d71fdb6c9fc8ef428b0e56ac56a1e70e9ab694ebb47feb4b52d35216f':
        raise Stop('P0 source verifier changed')
    spec=importlib.util.spec_from_file_location('hhl_p0_verified',P0)
    p0=importlib.util.module_from_spec(spec);spec.loader.exec_module(p0)
    arc=p0.load_snapshot()
    entry,files,manifest=p0.load_frozen_source(arc['root_archive'])
    files['hhl-uncompute-author.zip']=p0.verify_runtime()
    wire=p0.validate_rootless_wire(entry,files)
    sys.path.insert(0,str(BROKER));sys.path.insert(0,str(EVAL))
    import hhl_p1_broker_journal as journal
    import hhl_p1_eval_binding as binding
    import evidence_spool as evspool
    from rootless_core import decode_request
    ent,contents=decode_request(wire)
    if ent!=entry or contents!=files or journal.bundle_sha(entry,files)!=BUNDLE:
        raise Stop('Bundle identity mismatch')
    return p0,wire,entry,files,manifest,journal,binding,evspool

def exchange(mode:str,packet:bytes)->dict:
    argv=['/usr/sbin/runuser','-u','hhl-agent','--','/usr/bin/env','-i',
          'HOME=/nonexistent','PATH=/usr/bin:/bin','PYTHONDONTWRITEBYTECODE=1',
          '/usr/bin/python3','-I','-B',str(CLIENT),mode]
    try:
        result=subprocess.run(argv,input=packet,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                              cwd='/',timeout=200 if mode=='broker' else 130,check=False)
    except subprocess.TimeoutExpired as exc:raise Stop('Socket relay timeout') from exc
    max_size=12*1024*1024 if mode=='broker' else 16*1024
    if result.returncode or not 0<len(result.stdout)<=max_size:
        raise Stop(f'{mode} socket relay failed rc={result.returncode}; stderr={result.stderr[:300]!r}')
    try:return json.loads(result.stdout,object_pairs_hook=pairs)
    except (ValueError,UnicodeError) as exc:raise Stop('Bad reply JSON') from exc

def create(dest:Path,name:str,payload:bytes):
    if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,80}',name):raise Stop('Bad receipt name')
    fd=os.open(dest/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    with os.fdopen(fd,'wb') as f:
        f.write(payload);f.flush();os.fsync(f.fileno())
    dfd=os.open(dest,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(dfd)
    finally:os.close(dfd)

def pretty(o):return (json.dumps(o,sort_keys=True,indent=2,ensure_ascii=False)+'\n').encode()

def checkstage():
    p0,wire,entry,files,manifest,journal,binding,spool=load()
    for root,owner in ((BROKER_SPOOL,995),(EVAL_SPOOL,int(subprocess.check_output(['id','-u','hhl-eval']).strip()))):
        s=root.lstat()
        if not stat.S_ISDIR(s.st_mode) or (s.st_uid,stat.S_IMODE(s.st_mode))!=(owner,0o700):
            raise Stop('Untrusted stage spool: '+str(root))
    print('P1_STAGE_SOURCE_SHA=PASS',flush=True)
    print('P1_BASELINE_P0_SOURCE_PIN=PASS',flush=True)
    print('P1_BUNDLE_SHA='+BUNDLE,flush=True)
    return p0,wire,entry,files,manifest,journal,binding,spool

def read_legacy_v1_raw(event_id: str, expected_ops: bytes, *, spool, directory: Path, owner_uid: int) -> bytes:
    """Persist exact C v1 event bytes without confusing read_event's return contract.

    C's read_event returns (literal_ops, canonical_result), NOT (raw, envelope).
    First validate using C's original secure reader; then retrieve the same
    immutable, ownership-checked spool item for the root-only audit receipt.
    """
    legacy_ops, canonical_result = spool.read_event(event_id, directory=directory, owner_uid=owner_uid)
    if legacy_ops != expected_ops or canonical_result.get('tq_cost') != 4169620:
        raise Stop('Legacy v1 evaluated bytes or resource cost differ')

    dfd=spool._open_spool(directory, owner_uid=owner_uid)
    try:
        fd=os.open(event_id+'.json',os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=dfd)
        try:
            st=os.fstat(fd)
            if (not stat.S_ISREG(st.st_mode) or st.st_uid!=owner_uid or st.st_nlink!=1 or
                stat.S_IMODE(st.st_mode)!=0o600 or not 0<st.st_size<=spool.MAX_EVENT):
                raise Stop('Unsafe legacy v1 raw event inode')
            raw=os.read(fd,spool.MAX_EVENT+1)
            if len(raw)!=st.st_size:raise Stop('Incomplete legacy v1 raw event')
        finally:os.close(fd)
    finally:os.close(dfd)

    if not raw.endswith(b'\n') or raw.count(b'\n')!=1:
        raise Stop('Invalid legacy v1 raw event framing')
    envelope=json.loads(raw,object_pairs_hook=pairs)
    if (type(envelope) is not dict or
        set(envelope)!={'schema','event_id','unattributed','ops_b64','result'} or
        envelope['schema']!='hhl-eval-spool-v1' or
        envelope['event_id']!=event_id or envelope['unattributed'] is not True):
        raise Stop('Legacy v1 evidence must remain unattributed')
    raw_ops=base64.b64decode(envelope['ops_b64'],validate=True)
    if raw_ops!=legacy_ops or envelope['result']!=canonical_result:
        raise Stop('Legacy v1 raw evidence differs from independently validated record')
    return raw

def run(dest:Path):
    p0,wire,entry,files,manifest,journal,binding,spool=checkstage()
    if dest.parent!=RECORDS or dest.is_symlink() or not dest.is_dir() or (dest.stat().st_uid,stat.S_IMODE(dest.stat().st_mode))!=(0,0o700):
        raise Stop('Unsafe root-only receipt destination')
    create(dest,'source-post-session-manifest.json',pretty(manifest))
    create(dest,'README.txt',b'ISOLATED P1 STAGING: new event-time correlations only. P0 historical attribution NOT_PROVEN. Formal DB writes ZERO.\n')
    receipts=[]
    for idx in (1,2):
        broker_reply=exchange('broker',wire)
        ops=p0.parse_broker(broker_reply,sha(files[entry]))
        ops_sha=sha(ops)
        rid=broker_reply.get('broker_provenance_id')
        if ops_sha!=BEST or type(rid) is not str or not HEX32.fullmatch(rid) or broker_reply.get('broker_source_bundle_sha256')!=BUNDLE:
            raise Stop('Broker output does not match verified P0 circuit or source journal')
        record_raw,record=journal.root_read_record(rid,directory=BROKER_SPOOL,runner_uid=995)
        if record['source_bundle_sha256']!=BUNDLE or record['execution_ok'] is not True:
            raise Stop('Runner-owned Broker record mismatch')
        create(dest,f'run-{idx}-broker-raw.json',record_raw)
        create(dest,f'run-{idx}.ops',ops)
        request=binding.encode_bound_request(ops,rid,'candidate.ops')
        eval_reply=exchange('evaluator',request)
        native=p0.parse_native_response if hasattr(p0,'parse_native_response') else None
        if (eval_reply.get('valid') is not True or eval_reply.get('verdict')!='OK' or
            eval_reply.get('h_ops')!=BEST or eval_reply.get('tq_cost')!=4169620 or
            eval_reply.get('source_binding_status')!='UNVERIFIED_BROKER_CLAIM'):
            raise Stop('Evaluator v2 native results do not match expected HHL result')
        eid=eval_reply.get('trusted_evidence_id')
        if type(eid) is not str or not HEX32.fullmatch(eid):raise Stop('Missing evaluator evidence id')
        eval_uid=int(subprocess.check_output(['id','-u','hhl-eval']).strip())
        ev_raw,ev_obj,ev_ops=binding.root_read_claim_event(eid,directory=EVAL_SPOOL,owner_uid=eval_uid)
        if ev_ops!=ops:raise Stop('Event output bytes mismatch')
        audit=binding.audit_bound_pair(ev_raw,record_raw,broker_validator=journal.validate_record)
        if audit.get('status')!='BROKER_SOURCE_CLAIM_CORRELATED' or audit.get('formal_db_eligible') is True:
            raise Stop('Evidence chain mismatch or premature admission')
        create(dest,f'run-{idx}-evaluator-raw.json',ev_raw)
        receipt={'run_index':idx,'broker_run_id':rid,'event_id':eid,
                 'broker_raw_sha256':sha(record_raw),'evaluator_raw_sha256':sha(ev_raw),
                 'bundle_sha256':BUNDLE,'ops_sha256':ops_sha,
                 'T':eval_reply.get('toffoli'),'Q':eval_reply.get('qubits'),
                 'TQ':eval_reply.get('tq_cost'),'shots':eval_reply.get('shots'),
                 'correlation':audit,'formal_db_eligible':False}
        create(dest,f'run-{idx}-receipt.json',pretty(receipt))
        receipts.append(receipt)
        print(f'P1_RUN_{idx}_SOURCE_BOUND_NATIVE_OK=T{receipt["T"]}/Q{receipt["Q"]}/TQ{receipt["TQ"]}',flush=True)
        if idx==1:
            # The same isolated native service must still accept C's original v1 format.
            legacy_req=json.dumps({'schema':1,'ops_b64':base64.b64encode(ops).decode('ascii')},separators=(',',':')).encode()+b'\n'
            legacy=exchange('evaluator',legacy_req)
            legacy_id=legacy.get('trusted_evidence_id')
            if (legacy.get('valid') is not True or legacy.get('tq_cost')!=4169620 or
                type(legacy_id) is not str or not HEX32.fullmatch(legacy_id)):
                raise Stop('Legacy v1 evaluator path failed in isolated P1 stage')
            legacy_raw=read_legacy_v1_raw(legacy_id,ops,spool=spool,
                                          directory=EVAL_SPOOL,owner_uid=eval_uid)
            create(dest,'legacy-v1-event-raw.json',legacy_raw)
            print('P1_LEGACY_V1_COMPATIBILITY=PASS',flush=True)
    if len({r['broker_run_id'] for r in receipts})!=2 or len({r['event_id'] for r in receipts})!=2:
        raise Stop('Non-independent run identifiers')
    if len({r['TQ'] for r in receipts})!=1 or len({r['ops_sha256'] for r in receipts})!=1:
        raise Stop('Independent native results diverged')
    summary={'status':'P1_ISOLATED_TWO_NATIVE_REPLAYS_OK','source_at_evaluation':'CORRELATED_NEW_P1_EVENT',
             'p0_historical_source':'NOT_PROVEN','formal_db_writes':0,'model_calls':0,
             'p1_tq':4169620,'source_bundle_sha256':BUNDLE,'runs':receipts,
             'not_yet_done':['formal_source_archive_admission','HHL-only OpenEvolve DB save/reload/sample','multi-generation agent research']}
    create(dest,'summary.json',pretty(summary))
    print('P1_TWO_NEW_SOURCE_CORRELATED_REPLAYS=PASS',flush=True)
    print('P1_P0_HISTORICAL_SOURCE_ATTRIBUTION=NOT_PROVEN',flush=True)
    print('P1_FORMAL_DB_WRITES=0',flush=True)

if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--check',action='store_true')
    ap.add_argument('--receipt-dir',type=Path)
    a=ap.parse_args()
    try:
        if a.check:checkstage()
        elif a.receipt_dir:run(a.receipt_dir)
        else:raise Stop('Select --check or --receipt-dir')
    except (OSError,ValueError,Stop,subprocess.SubprocessError,KeyError,binascii.Error,TypeError) as e:
        print(f'P1_ACCEPTANCE_STOP={type(e).__name__}:{str(e)[:400]}',file=sys.stderr)
        raise SystemExit(2)
