#!/usr/bin/env python3
"""HHL P6: audited gen1 -> NEW native OpenEvolve branch -> gen2 parent preview.

No model calls, Podman, grader calls or writes to P4 gen0. A valid P5 result
is only an index: every event/replay must be reread from the owner-protected
Broker/Evaluator spools and matched against the root-owned archive copies.
The branch is deliberately NEW rather than mutating the sole P4 gen0 root.
"""
from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import pwd
import re
import stat
import sys
import tempfile

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'support'))
sys.path.insert(0,str(HERE/'support/ref'))
import hhl_p2_trial_db as p2
import hhl_p4_formal_seed as p4
import hhl_p3_parent_adapter as p3
import hhl_p1_broker_journal as broker
import hhl_p1_eval_binding as evaluator
import hhl_evolution_bridge as evolution
from openevolve_seed_adapter_A_original import load_database_with_verified_stats

RECORDS=Path('/srv/hhl-task2/research-records')
GEN0=RECORDS/'HHL_P4_FORMAL_GEN0_1q711wms'
GEN0_ID=p2.TARGET_BUNDLE
HEX32=re.compile(r'[a-f0-9]{32}\Z')
HEX64=re.compile(r'[a-f0-9]{64}\Z')
RECORD_RE=re.compile(r'HHL_P5_GEN1_CANARY_[A-Za-z0-9_]{5,25}\Z')
BRANCH_RE=re.compile(r'HHL_P6_FORMAL_BRANCH_[A-Za-z0-9_]{8}\Z')
SCHEMA='hhl-p6-gen1-branch-v1'
MAX_EVENTS=24
MAX_RESULT=500_000
BASELINE_TQ=4_940_400

class Refuse(ValueError): pass

def sha(data:bytes)->str:return hashlib.sha256(data).hexdigest()

def json_bytes(data:dict)->bytes:
    return (json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=True)+'\n').encode('ascii')

def private_dir(p:Path)->None:
    s=p.lstat()
    if not stat.S_ISDIR(s.st_mode) or s.st_uid!=0 or stat.S_IMODE(s.st_mode)!=0o700:
        raise Refuse('root-only directory validation failed')

def private_json(directory:Path,filename:str,max_bytes:int=MAX_RESULT)->dict:
    return p2.parse_json(p2.protected_regular_bytes(directory,filename,maximum=max_bytes))

def _as_int(value)->int:
    if type(value) not in (int,float) or not math.isfinite(value) or value<=0 or int(value)!=value:
        raise Refuse('invalid native metric')
    return int(value)

def metric_tuple(event:dict)->tuple[int,int,int]:
    r=event['result']
    if r.get('task')!='hhl-angle' or r.get('valid') is not True or r.get('verdict')!='OK':
        raise Refuse('native grader not OK')
    t,q,tq=(_as_int(r[x]) for x in ('toffoli','qubits','tq_cost'))
    if t*q!=tq:raise Refuse('native metrics inconsistent')
    return (t,q,tq)

def path_independent_fingerprint(file_map:dict)->str:
    """Match P5's exact path-invariant comparison. NOT a Program ID."""
    leaves={}
    if type(file_map) is not dict or len(file_map)<7:
        raise Refuse('incomplete multi-file source')
    for name,encoded in file_map.items():
        evolution.check_path(name)
        if name=='hhl-uncompute-author.zip':continue
        leaf=name.rsplit('/',1)[-1]
        if leaf in leaves:raise Refuse('duplicate source basename')
        if type(encoded) is not str:raise Refuse('source base64 malformed')
        leaves[leaf]=base64.b64decode(encoded,validate=True)
    h=hashlib.sha256(b'HHL_P5_SAME_FILES_NOT_SOURCE_ID\0')
    for name,raw in sorted(leaves.items()):
        h.update(name.encode()+b'\0'+len(raw).to_bytes(8,'big')+raw)
    return h.hexdigest()

def source_from_broker(b:dict):
    files={name:base64.b64decode(value,validate=True)
           for name,value in b['files_b64'].items()}
    library=files.pop('hhl-uncompute-author.zip',None)
    if library is None:raise Refuse('pinned library missing')
    bundle=evolution.freeze_actual_bundle(b['entry'],files,library)
    if bundle.sha256!=b['source_bundle_sha256'] or bundle.entry_sha256!=b['entry_sha256']:
        raise Refuse('broker source identity differs from exact bundle')
    # P3 requires one numbered attempt directory with flat source files.
    if not re.fullmatch(r'runs/hhl-[a-z0-9][a-z0-9-]{7,56}/attempt-0[1-8]/main\.py',bundle.entry):
        raise Refuse('candidate worktree cannot be next-generation parent')
    prefix=bundle.entry[:-len('main.py')]
    if any(not name.startswith(prefix) for name in files):
        raise Refuse('source files outside single agent attempt')
    if any('/' in name[len(prefix):] for name in files):
        raise Refuse('nested file not stageable by P3 adapter')
    names=set(name[len(prefix):] for name in files)
    needed={'main.py','circuit.py','constants.json','meta.json','contract.json','grader.py'}
    if not needed <= names:raise Refuse('required parent files missing')
    return bundle

def checked_pair(record:Path,basename:str,event_id:str,broker_id:str):
    """Check private copy byte-for-byte against independent protected service spools."""
    if not HEX32.fullmatch(event_id) or not HEX32.fullmatch(broker_id):
        raise Refuse('bad event/broker identity')
    ev_file=basename+'-evaluator.json'
    br_file=basename+'-broker.json'
    saved_ev=p2.protected_regular_bytes(record,ev_file,maximum=4_000_000)
    saved_br=p2.protected_regular_bytes(record,br_file,maximum=16_000_000)
    live_ev,e,ops=evaluator.root_read_claim_event(
        event_id,directory=p2.P1_EVALUATOR,
        owner_uid=pwd.getpwnam('hhl-eval').pw_uid)
    live_br,b=broker.root_read_record(broker_id,directory=p2.P1_BROKER,runner_uid=995)
    if (saved_ev,saved_br)!=(live_ev,live_br):
        raise Refuse('P5 archived receipt not identical to independent protected spool')
    proof=evaluator.audit_bound_pair(live_ev,live_br,broker_validator=broker.validate_record)
    if (proof['status']!='BROKER_SOURCE_CLAIM_CORRELATED' or
        b['broker_run_id']!=broker_id or e['event_id']!=event_id):
        raise Refuse('source/evaluation binding not corroborated')
    return (b,e,ops,{'broker_filename':br_file,'broker_sha256':sha(saved_br),
                     'evaluator_filename':ev_file,'evaluator_sha256':sha(saved_ev)})

def verify_candidate(row:dict,record:Path,parent_fingerprint:str,parent_id:str,parent_tq:int)->dict:
    if (type(row) is not dict or row.get('source_binding')!='SOURCE_AT_EVAL_CORRELATED' or
        row.get('source_changed_from_parent') is not True or row.get('formal_db_writes')!=0 or
        type(row.get('replay')) is not dict or
        row['replay'].get('status')!='TWO_INDEPENDENT_P1_NATIVE_REPLAYS_OK'):
        raise Refuse('candidate has not passed the P5 source/replay gate')
    eid=row['event_id']; bid=row['broker_run_id']
    b,e,ops,primary=checked_pair(record,'agent-'+eid,eid,bid)
    source=source_from_broker(b)
    source_fingerprint=path_independent_fingerprint(b['files_b64'])
    if source_fingerprint==parent_fingerprint or source.sha256==parent_id:
        raise Refuse('copied parent is not a new source candidate')
    if (row.get('source_bundle_sha256')!=source.sha256 or
        row.get('ops_sha256')!=sha(ops) or row.get('TQ')!=e['result']['tq_cost']):
        raise Refuse('P5 summary disagrees with original protected event')
    t,q,tq=metric_tuple(e)
    replay=row['replay']
    if (replay.get('ops_sha256')!=sha(ops) or
        replay.get('program_source_bundle_sha256')!=source.sha256 or
        type(replay.get('source_replays')) is not list or len(replay['source_replays'])!=2):
        raise Refuse('replay summary does not describe original source')
    evidence=[primary]
    ids_e={eid};ids_b={bid}
    for step,item in enumerate(replay['source_replays'],1):
        if type(item) is not dict or item.get('replay_index')!=step:
            raise Refuse('wrong independent replay order')
        reid=item['event_id']; rbid=item['broker_run_id']
        rb,re,rops,meta=checked_pair(record,f'replay-{step}-{eid}',reid,rbid)
        if reid in ids_e or rbid in ids_b:
            raise Refuse('replay reused event or broker ID')
        ids_e.add(reid);ids_b.add(rbid)
        if (rb['source_bundle_sha256']!=source.sha256 or
            rb['request_wire_sha256']!=b['request_wire_sha256'] or
            rb['files_b64']!=b['files_b64'] or rb['entry']!=b['entry'] or
            rops!=ops or metric_tuple(re)!=(t,q,tq) or
            item.get('tq_cost')!=tq or item.get('ops_sha256')!=sha(ops)):
            raise Refuse('replay differs from exact source, ops or native metrics')
        evidence.append(meta)
    return {'source':source,'program_id':source.sha256,'parent_id':parent_id,
            'event_id':eid,'ops_sha256':sha(ops),'T':t,'Q':q,'TQ':tq,
            'improves_parent':tq<parent_tq,'source_fingerprint':source_fingerprint,
            'evidence':evidence,'all_event_ids':sorted(ids_e),'all_broker_ids':sorted(ids_b)}

def parent_proof(formal_gen0:Path):
    # Native DB sampling + original two independent P1 receipts are checked by P4.
    p4.sample_audited(formal_gen0,run_tag='hhl-20261108-p6audit-3333')
    index=private_json(formal_gen0,'index.json',200_000)
    if index.get('program_id')!=GEN0_ID or index.get('ops_sha256')!=p2.TARGET_OPS:
        raise Refuse('unexpected formal HHL gen0 parent')
    archived=private_json(formal_gen0/'bundles',GEN0_ID+'.json',3_000_000)
    source=p4._decode_source_record(archived)
    fingerprint=path_independent_fingerprint(archived['files_b64'])
    return source,fingerprint,index

def audit(record:Path,formal_gen0:Path)->dict:
    if os.geteuid()!=0:raise Refuse('operator root required')
    private_dir(RECORDS)
    if record.parent!=RECORDS or not RECORD_RE.fullmatch(record.name):
        raise Refuse('unexpected P5 record directory')
    if formal_gen0!=GEN0:raise Refuse('unexpected formal gen0 root')
    for folder in (record,formal_gen0,formal_gen0/'db',formal_gen0/'bundles',formal_gen0/'evidence'):
        private_dir(folder)
    parent,parent_fingerprint,_=parent_proof(formal_gen0)
    start=private_json(record,'run-start.json')
    result=private_json(record,'result.json')
    if (start.get('schema')!='hhl-p5-single-paid-gen1-v1' or
        result.get('schema')!='hhl-p5-single-paid-gen1-v1' or
        start.get('parent_id')!=GEN0_ID or result.get('parent_id')!=GEN0_ID or
        start.get('parent_generation')!=0 or start.get('parent_tq')!=p2.TARGET_TQ or
        start.get('model')!='deepseek/deepseek-v4-pro' or
        start.get('formal_db_writes')!=0 or result.get('formal_db_writes')!=0 or
        start.get('historical_2026_11_06_source')!='NOT_PROVEN' or
        result.get('historical_2026_11_06_source')!='NOT_PROVEN' or
        start.get('run_tag')!=result.get('run_tag')):
        raise Refuse('P5 gen1 lineage/result identity inconsistent')
    if result.get('status') not in ('model_exited','model_timeout_recovered','model_nonzero_exit','recovery_incomplete'):
        raise Refuse('result not in recognizable final state')
    rows=result.get('candidate_events')
    if type(rows) is not list or len(rows)>MAX_EVENTS:
        raise Refuse('P5 event list missing or outside limit')
    if (result.get('source_bound_events')!=sum(
        type(r) is dict and r.get('source_binding')=='SOURCE_AT_EVAL_CORRELATED' for r in rows) or
        result.get('independent_replayed_candidates')!=sum(
        type(r) is dict and type(r.get('replay')) is dict and
        r['replay'].get('status')=='TWO_INDEPENDENT_P1_NATIVE_REPLAYS_OK' for r in rows)):
        raise Refuse('P5 outcome counters inconsistent')
    candidates=[];rejections=[];ids=set();ops_ids=set()
    for row in rows:
        if type(row) is not dict or row.get('source_binding')!='SOURCE_AT_EVAL_CORRELATED':
            rejections.append('unbound_or_failed_event');continue
        try:
            item=verify_candidate(row,record,parent_fingerprint,GEN0_ID,p2.TARGET_TQ)
            if item['program_id'] in ids or item['ops_sha256'] in ops_ids:
                raise Refuse('duplicate source or ops in P5 evidence')
            ids.add(item['program_id']);ops_ids.add(item['ops_sha256'])
            candidates.append(item)
        except (Refuse,ValueError,KeyError,TypeError,UnicodeError,OSError) as exc:
            rejections.append(type(exc).__name__)
    improved=sorted((x for x in candidates if x['improves_parent']),key=lambda x:(x['TQ'],x['program_id']))
    return {'record':record,'parent_source':parent,
            'parent_id':GEN0_ID,'parent_tq':p2.TARGET_TQ,
            'evaluated_events':len(rows),'fully_verified_candidates':len(candidates),
            'improved_candidates':improved,'other_verified_candidates':len(candidates)-len(improved),
            'rejected_event_reasons':rejections,'result_status':result['status'],
            'result_sha256':sha(p2.protected_regular_bytes(record,'result.json',maximum=MAX_RESULT))}

def public_audit(data:dict)->dict:
    best=data['improved_candidates'][:1]
    return {'schema':'hhl-p6-operator-audit-v1','source_record':data['record'].name,
            'parent_id':data['parent_id'],'parent_tq':data['parent_tq'],
            'p5_status':data['result_status'],'candidate_event_rows':data['evaluated_events'],
            'fully_verified_distinct_candidates':data['fully_verified_candidates'],
            'verified_improvements':len(data['improved_candidates']),
            'other_verified_candidates':data['other_verified_candidates'],
            'best_eligible': None if not best else {
                'program_id':best[0]['program_id'],'tq':best[0]['TQ'],
                'toffoli':best[0]['T'],'qubits':best[0]['Q'],
                'parent_id':best[0]['parent_id'],'generation':1},
            'rejected_row_count':len(data['rejected_event_reasons']),
            'result_sha256':data['result_sha256'],
            'formal_db_writes':0,'model_calls':0,
            'historical_2026_11_06_source':'NOT_PROVEN'}

def serialize_child(child:dict)->dict:
    b=child['source']
    return {'schema':'hhl-p6-full-source-archive-v1','task':'hhl-angle',
            'program_id':b.sha256,'entry':b.entry,'entry_sha256':b.entry_sha256,
            'ops_sha256':child['ops_sha256'],'parent_id':child['parent_id'],
            'generation':1,'T':child['T'],'Q':child['Q'],'TQ':child['TQ'],
            'origin_event_id':child['event_id'],
            'source_fingerprint':child['source_fingerprint'],
            'files_b64':{n:base64.b64encode(raw).decode('ascii') for n,raw in b.files}}

def read_child_archive(raw:dict):
    if raw.get('schema')!='hhl-p6-full-source-archive-v1' or raw.get('task')!='hhl-angle':
        raise Refuse('untrusted child archive schema')
    files=raw.get('files_b64')
    if type(files) is not dict:raise Refuse('missing archived source')
    items={n:base64.b64decode(x,validate=True) for n,x in files.items()}
    library=items.pop('hhl-uncompute-author.zip',None)
    if library is None:raise Refuse('missing HHL library')
    b=evolution.freeze_actual_bundle(raw['entry'],items,library)
    if (raw.get('program_id')!=b.sha256 or raw.get('entry_sha256')!=b.entry_sha256 or
        raw.get('source_fingerprint')!=path_independent_fingerprint(files) or
        raw.get('parent_id')!=GEN0_ID or raw.get('generation')!=1):
        raise Refuse('child archive identity mismatch')
    return b

def acquire_session_lock():
    p=Path('/run/hhl-task2/hhl-first-single.lock')
    fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    st=os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_uid!=0 or st.st_nlink!=1 or stat.S_IMODE(st.st_mode)!=0o600:
        os.close(fd);raise Refuse('unsafe C session lock')
    try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except (BlockingIOError,OSError):os.close(fd);raise Refuse('HHL session still active')
    return fd

def admit_new_branch(data:dict)->dict:
    """Create NEW root-only branch, never touch gen0 root / Coulomb / C controller."""
    if not data['improved_candidates']:raise Refuse('no source-bound, twice replayed improvement')
    child=data['improved_candidates'][0]
    from openevolve.database import Program,ProgramDatabase
    from openevolve.config import DatabaseConfig
    lock=acquire_session_lock()
    old_umask=os.umask(0o077)
    try:
        root=Path(tempfile.mkdtemp(prefix='HHL_P6_FORMAL_BRANCH_',dir=RECORDS))
        os.chmod(root,0o700)
        for sub in ('db','bundles','evidence'):(root/sub).mkdir(mode=0o700)
        # Native ProgramDatabase API from A and P4, with lineage explicitly gen0 -> gen1.
        parent_db=load_database_with_verified_stats(p4._config(GEN0/'db'),ProgramDatabase)
        orig=parent_db.get(GEN0_ID)
        if (orig is None or orig.generation!=0 or orig.parent_id is not None or
            orig.code!=data['parent_source'].content()[data['parent_source'].entry].decode('utf-8') or
            orig.metrics.get('tq_cost')!=float(p2.TARGET_TQ)):
            raise Refuse('formal gen0 has lost native parent')
        dbdir=root/'db'
        db=ProgramDatabase(p4._config(dbdir))
        if db.add(orig,iteration=0) is None:
            raise Refuse('native DB rejected formal gen0')
        bundle=child['source']
        new=Program(id=bundle.sha256,
                    code=bundle.content()[bundle.entry].decode('utf-8','strict'),language='python',
                    generation=1,parent_id=GEN0_ID,
                    changes_description='P5 native source-bound, two independent P1 replay improvement',
                    metrics={'combined_score':BASELINE_TQ/child['TQ'],'toffoli':float(child['T']),
                             'qubits':float(child['Q']),'tq_cost':float(child['TQ'])},
                    metadata={'task':'hhl-angle','bundle_sha256':bundle.sha256,
                              'entry_sha256':bundle.entry_sha256,'ops_sha256':child['ops_sha256'],
                              'research_admission':'FORMAL_HHL_REPLAY_VERIFIED',
                              'source_at_evaluation':'BROKER_SOURCE_CLAIM_CORRELATED',
                              'evidence_event_id':child['event_id'],
                              'source_bundle_stored_out_of_band':True,
                              'original_2026_11_06_source':'NOT_PROVEN'})
        if db.add(new,iteration=1) is None:
            raise Refuse('native DB rejected twice-replayed gen1')
        db.save(str(dbdir),iteration=1)
        restored=load_database_with_verified_stats(p4._config(dbdir),ProgramDatabase)
        child_program=restored.get(new.id)
        if (child_program is None or child_program.generation!=1 or
            child_program.parent_id!=GEN0_ID or child_program.code!=new.code or
            child_program.metrics['tq_cost']!=float(child['TQ'])):
            raise Refuse('native save/reload did not retain gen1')
        child_archive=serialize_child(child)
        parent_archive=private_json(GEN0/'bundles',GEN0_ID+'.json',3_000_000)
        p4._new_file(root/'bundles',GEN0_ID+'.json',p4._json_bytes(parent_archive))
        p4._new_file(root/'bundles',new.id+'.json',json_bytes(child_archive))
        evidence_index={}
        # Also preserve the signed-off outcome context; never assume that
        # transient service spools or the original P5 run folder live forever.
        for name in ('run-start.json','result.json'):
            blob=p2.protected_regular_bytes(data['record'],name,maximum=MAX_RESULT)
            p4._new_file(root/'evidence',name,blob)
            evidence_index[name]=sha(blob)
        for entry in child['evidence']:
            for label in ('broker','evaluator'):
                name=entry[label+'_filename']
                blob=p2.protected_regular_bytes(data['record'],name,maximum=16_000_000)
                if sha(blob)!=entry[label+'_sha256']:
                    raise Refuse('receipt changed during admission')
                p4._new_file(root/'evidence',name,blob)
                evidence_index[name]=sha(blob)
        evidence_manifest={'schema':'hhl-p6-evidence-index-v1','source_record':data['record'].name,
                           'result_sha256':data['result_sha256'],
                           'original_2026_11_06_source':'NOT_PROVEN','files':evidence_index}
        p4._new_file(root/'evidence','evidence-index.json',json_bytes(evidence_manifest))
        p4._new_file(dbdir,p4.PROFILE,p4._json_bytes({
            'schema':1,'features':['complexity','diversity'],
            'fitness':'4940400/tq_cost','task':'hhl-angle',
            'admission':'FORMAL_HHL_REPLAY_VERIFIED',
            'source_archive':'bundles/<program_id>.json'}))
        index={'schema':SCHEMA,'task':'hhl-angle','parent_id':GEN0_ID,
               'child_id':new.id,'parent_generation':0,'child_generation':1,
               'parent_tq':p2.TARGET_TQ,'child_tq':child['TQ'],
               'parent_source_archive_sha256':sha(p4._json_bytes(parent_archive)),
               'child_source_archive_sha256':sha(json_bytes(child_archive)),
               'evidence_manifest_sha256':sha(json_bytes(evidence_manifest)),
               'result_sha256':data['result_sha256'],'source_record':data['record'].name,
               'model_calls_in_admission':0,'original_2026_11_06_source':'NOT_PROVEN'}
        p4._new_file(root,'index.json',json_bytes(index))
        return {'schema':SCHEMA,'new_branch_root':str(root),
                'native_gen1_save_reload':'PASS','parent_id':GEN0_ID,
                'child_id':new.id,'TQ':child['TQ'],'native_population':len(restored.programs),
                'gen0_db_mutated':False,'coulomb_mutated':False,'model_calls':0}
    finally:
        os.umask(old_umask)
        os.close(lock)

def preview_native_parent(branch_root:Path,run_tag:str,*,_return_verified_source:bool=False,_require_verified_gen1:bool=False)->dict:
    if branch_root.parent!=RECORDS or not BRANCH_RE.fullmatch(branch_root.name):
        raise Refuse('unexpected branch path')
    for p in (branch_root,branch_root/'db',branch_root/'bundles',branch_root/'evidence'):
        private_dir(p)
    index=private_json(branch_root,'index.json')
    if index.get('schema')!=SCHEMA or index.get('parent_id')!=GEN0_ID or index.get('child_generation')!=1:
        raise Refuse('untrusted branch index')
    child_id=index['child_id']
    if type(child_id) is not str or not HEX64.fullmatch(child_id):
        raise Refuse('malformed branch program identity')
    child_archive=private_json(branch_root/'bundles',child_id+'.json',3_000_000)
    if sha(json_bytes(child_archive))!=index['child_source_archive_sha256']:
        raise Refuse('child archive hash drift')
    child_bundle=read_child_archive(child_archive)
    if child_bundle.sha256!=child_id or child_archive['TQ']!=index['child_tq']:
        raise Refuse('admitted child identity/score drift')
    orig=private_json(branch_root/'bundles',GEN0_ID+'.json',3_000_000)
    if sha(p4._json_bytes(orig))!=index['parent_source_archive_sha256']:
        raise Refuse('parent archive hash drift')
    parent_bundle=p4._decode_source_record(orig)
    m=private_json(branch_root/'evidence','evidence-index.json',200_000)
    if sha(json_bytes(m))!=index['evidence_manifest_sha256']:
        raise Refuse('archived evidence index drift')
    for name,digest in m['files'].items():
        if sha(p2.protected_regular_bytes(branch_root/'evidence',name,maximum=16_000_000))!=digest:
            raise Refuse('archived event evidence drift')
    from openevolve.database import ProgramDatabase
    db=load_database_with_verified_stats(p4._config(branch_root/'db'),ProgramDatabase)
    if db.get(child_id) is None:raise Refuse('native DB missing improved child')
    # Gen2 specifically requires the already admitted, replay-verified Gen1.
    # Do not repeatedly rebuild a deterministically seeded DB and call sample().
    # Native DB.get() is used only after verifying child source and protected evidence.
    if _require_verified_gen1:
        selected=db.get(child_id)
        if selected is None:raise Refuse('native DB missing required Gen1')
    else:
        selected,_=db.sample(num_inspirations=0)
    if selected.id==child_id:
        chosen=child_bundle;admission='FORMAL_HHL_REPLAY_VERIFIED'
        expected_tq=index['child_tq'];generation=1
    elif selected.id==GEN0_ID:
        chosen=parent_bundle;admission=p4.ADMISSION
        expected_tq=p2.TARGET_TQ;generation=0
    else:raise Refuse('native DB selected unknown source')
    if (selected.generation!=generation or selected.metrics.get('tq_cost')!=float(expected_tq) or
        selected.code!=chosen.content()[chosen.entry].decode('utf-8','strict')):
        raise Refuse('native sampled program differs from protected bundle')
    plan=p3.rebase_plan(chosen,selected.id,run_tag,
                         parent_generation=generation,verified_parent_tq=expected_tq)
    plan['research_db_admission']=admission
    plan['native_branch_parent_sample']='PASS'
    outcome = {'schema':'hhl-p6-native-parent-preview-v1','branch':branch_root.name,
            'selected_parent_id':selected.id,'selected_generation':generation,
            'next_generation':generation+1,'parent_tq':expected_tq,
            'source_files':plan['full_source_file_count'],
            'new_materialized_source_sha256':plan['materialized_broker_input_sha256'],
            'materialized_is_offspring':False,'native_sampler':'OpenEvolve_0.4.0_ProgramDatabase.sample',
            'staged_to_agent':False,'model_calls':0,'formal_db_writes':0}
    if _return_verified_source:
        archive = child_archive if generation == 1 else orig
        return outcome, chosen, archive
    return outcome

def preview_native_parent_verified(branch_root:Path,run_tag:str,*,require_verified_gen1:bool=False):
    """Read-only source return after protected checks; Gen2 can pin an admitted Gen1."""
    return preview_native_parent(branch_root,run_tag,_return_verified_source=True,
                                 _require_verified_gen1=require_verified_gen1)

def main()->int:
    p=argparse.ArgumentParser(description=__doc__)
    modes=p.add_mutually_exclusive_group(required=True)
    modes.add_argument('--audit-record',type=Path)
    modes.add_argument('--admit-new-branch',type=Path)
    modes.add_argument('--sample-branch',type=Path)
    p.add_argument('--approve-new-branch',action='store_true')
    p.add_argument('--run-tag',default='hhl-20261108-p6next-4444')
    a=p.parse_args()
    try:
        if os.geteuid()!=0:raise Refuse('root operator required')
        if a.audit_record:
            if a.approve_new_branch:raise Refuse('audit cannot authorize writes')
            outcome=public_audit(audit(a.audit_record,GEN0))
            print('P6_READ_ONLY_AUDIT=PASS')
        elif a.admit_new_branch:
            if not a.approve_new_branch:raise Refuse('new branch requires explicit approval flag')
            data=audit(a.admit_new_branch,GEN0)
            outcome=admit_new_branch(data)
            print('P6_NEW_ISOLATED_BRANCH_SAVE_RELOAD=PASS')
        else:
            if a.approve_new_branch:raise Refuse('sample cannot authorize writes')
            private_dir(RECORDS)
            outcome=preview_native_parent(a.sample_branch,a.run_tag)
            print('P6_NATIVE_BRANCH_PARENT_PREVIEW=PASS')
        print(json.dumps(outcome,sort_keys=True,separators=(',',':')))
        return 0
    except (Refuse,ValueError,KeyError,TypeError,UnicodeError,OSError,RuntimeError,ImportError,PermissionError) as exc:
        # Never print attacker-controlled source, prompt, or raw exception content.
        print('P6_STOP='+type(exc).__name__,file=sys.stderr)
        return 2

if __name__=='__main__':raise SystemExit(main())
