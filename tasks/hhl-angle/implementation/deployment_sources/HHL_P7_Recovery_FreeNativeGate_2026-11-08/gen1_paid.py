#!/usr/bin/env python3
"""One explicitly authorized DeepSeek gen1 Session using C's proven runtime.

This is NOT a new evolution controller. One parent from native HHL P4 DB,
C's process-group timeout/recovery, P1 isolated build/evaluator services,
source-bound receipts, and up to two native replays of each distinct improved
candidate. Does not write any OpenEvolve DB; verified children require later
independent admission and native lineage registration.
"""
from __future__ import annotations
import base64
import hashlib
import json
import os
from pathlib import Path
import pwd
import secrets
import stat
import subprocess
import sys
import time

# -I excludes script directory; pin the root-controlled P5 staging directory.
sys.path.insert(0,str(Path(__file__).resolve(strict=True).parent))

from gen1_canary import (STAGE, SCRATCH, DB, BEST_OPS, PARENT_ID, EVAL_SPOOL,
                         BROKER_SPOOL, HHL, C, ensure_tree, parent, accepted_env,
                         make_record, save_json, Stop, digest, seed, builder,
                         c_one, journal, binding)
import hhl_p3_stage_adapter as p3_stage
import hhl_p4_formal_seed as p4

MAX_EVENTS=24
MAX_REPLAY_CANDIDATES=5

def write(dest:Path,name:str,data:bytes)->None:
    c_one.durable_file(dest,name,data)

def source_id(obj:dict)->str:
    """Path-independent content digest for comparison ONLY, never Program ID."""
    table={}
    for path,encoded in obj['files_b64'].items():
        if path == 'hhl-uncompute-author.zip':continue
        leaf=path.rsplit('/',1)[-1]
        if leaf in table:raise Stop('duplicate basename in executable source bundle')
        table[leaf]=base64.b64decode(encoded,validate=True)
    h=hashlib.sha256(b'HHL_P5_SAME_FILES_NOT_SOURCE_ID\0')
    for n,raw in sorted(table.items()):
        h.update(n.encode()+b'\0'+len(raw).to_bytes(8,'big')+raw)
    return h.hexdigest()

def snapshot_ids(dir:Path):
    st=dir.lstat()
    if not stat.S_ISDIR(st.st_mode) or stat.S_IMODE(st.st_mode)!=0o700:
        raise Stop('unsafe P1 event spool')
    return {p.stem for p in dir.iterdir() if p.is_file() and not p.is_symlink()
            and len(p.stem)==32 and all(c in '0123456789abcdef' for c in p.stem)}

def exchange(mode:str, raw:bytes,env:dict)->dict:
    # Independent hhl-agent process per build/evaluation; relay performs no
    # code execution and P1 socket checks Linux peer credentials.
    cmd=['/usr/bin/python3','-I','-B',str(STAGE/'client.py'),mode]
    args=['/usr/sbin/runuser','-u','hhl-agent','-g','hhl-agent','--',*cmd]
    run=subprocess.run(args,input=raw,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                       cwd='/',env=env,timeout=210 if mode=='broker' else 145)
    if run.returncode!=0 or not 0<len(run.stdout)<=12_000_000:
        raise Stop('P1 native relay failed, no candidate admitted')
    return json.loads(run.stdout)

def replay_twice(evidence:dict,record:Path,env:dict)->dict:
    """Construct same frozen Broker input twice in fresh rootless containers."""
    braw=evidence['broker_raw']; bobj=evidence['broker']
    original=evidence['ops']; original_tq=evidence['TQ']
    artifact=evidence['artifact']
    wire=base64.b64decode(bobj['request_b64'],validate=True)
    receipts=[]
    for iteration in (1,2):
        b_reply=exchange('broker',wire,env)
        rid=b_reply.get('broker_provenance_id')
        if not isinstance(rid,str):raise Stop('Replay missing Broker receipt ID')
        b_r,recorded_b=journal.root_read_record(rid,directory=BROKER_SPOOL,runner_uid=995)
        if (recorded_b['source_bundle_sha256']!=bobj['source_bundle_sha256'] or
            not recorded_b['execution_ok'] or recorded_b['entry']!=bobj['entry'] or
            recorded_b['request_wire_sha256']!=bobj['request_wire_sha256'] or
            recorded_b['ops_b64'].get(artifact) is None):
            raise Stop('Independent replay did not build the same verified source')
        built_ops=base64.b64decode(recorded_b['ops_b64'][artifact],validate=True)
        if built_ops!=original:raise Stop('Independent replay emitted different .ops')
        write(record,f'replay-{iteration}-{evidence["event_id"]}-broker.json',b_r)
        request=binding.encode_bound_request(built_ops,rid,artifact)
        evaluated=exchange('evaluator',request,env)
        if evaluated.get('valid') is not True or evaluated.get('verdict')!='OK' or evaluated.get('tq_cost')!=original_tq:
            raise Stop('Independent native replay failed scoring')
        eid=evaluated.get('trusted_evidence_id')
        e_r,e_obj,e_ops=binding.root_read_claim_event(eid,directory=EVAL_SPOOL,
                          owner_uid=pwd.getpwnam('hhl-eval').pw_uid)
        audit=binding.audit_bound_pair(e_r,b_r,broker_validator=journal.validate_record)
        if e_ops!=original or audit['status']!='BROKER_SOURCE_CLAIM_CORRELATED':
            raise Stop('Replay did not produce paired protected proof')
        write(record,f'replay-{iteration}-{evidence["event_id"]}-evaluator.json',e_r)
        receipts.append({'replay_index':iteration,'broker_run_id':rid,'event_id':eid,
                         'ops_sha256':digest(built_ops),'tq_cost':evaluated['tq_cost']})
    return {'status':'TWO_INDEPENDENT_P1_NATIVE_REPLAYS_OK',
            'program_source_bundle_sha256':bobj['source_bundle_sha256'],
            'ops_sha256':digest(original),'source_replays':receipts}

def collect(before:set[str],root:Path,env:dict,parent_files_hash:str)->list[dict]:
    ids=sorted(snapshot_ids(EVAL_SPOOL)-before)
    if len(ids)>MAX_EVENTS:
        # Refuse truncated acceptance; all raw spool remains protected.
        raise Stop('more than the bounded event count; archive spool before proceeding')
    unique=set()
    accepted=[]
    for eid in ids:
        try:
            evraw,eobj,ops=binding.root_read_claim_event(eid,directory=EVAL_SPOOL,
                          owner_uid=pwd.getpwnam('hhl-eval').pw_uid)
            rid=eobj['source_claim']['broker_run_id']
            braw,bobj=journal.root_read_record(rid,directory=BROKER_SPOOL,runner_uid=995)
            corr=binding.audit_bound_pair(evraw,braw,broker_validator=journal.validate_record)
            if corr['status']!='BROKER_SOURCE_CLAIM_CORRELATED':
                raise Stop('P1 evidence mismatch')
            # Archive *both* literal protected evidence envelopes BEFORE replay.
            write(root,'agent-'+eid+'-broker.json',braw)
            write(root,'agent-'+eid+'-evaluator.json',evraw)
            row={'event_id':eid,'broker_run_id':rid,'source_bundle_sha256':bobj['source_bundle_sha256'],
                 'ops_sha256':digest(ops),'TQ':eobj['result']['tq_cost'],
                 'source_changed_from_parent':source_id(bobj)!=parent_files_hash,
                 'source_binding':'SOURCE_AT_EVAL_CORRELATED',
                 'raw_evidence_saved':True,'replay':'NOT_YET_ATTEMPTED',
                 'formal_db_writes':0}
            if (row['source_changed_from_parent'] and row['ops_sha256'] not in unique and
                len(unique)<MAX_REPLAY_CANDIDATES):
                unique.add(row['ops_sha256'])
                try:
                    row['replay']=replay_twice({'event_id':eid,'broker_raw':braw,
                        'broker':bobj,'ops':ops,'TQ':row['TQ'],
                        'artifact':eobj['source_claim']['broker_artifact']},root,env)
                except (Stop,ValueError,OSError,subprocess.TimeoutExpired) as exc:
                    row['replay']={'status':'FAILED_NOT_ADMITTED','error_type':type(exc).__name__}
            accepted.append(row)
        except (Stop,ValueError,OSError,KeyError) as exc:
            accepted.append({'event_id':eid,'status':'REJECTED_UNVERIFIED',
                             'reason':type(exc).__name__})
    return accepted

def execute(timeout:int)->dict:
    if not 180<=timeout<=1800:raise Stop('paid wall timeout must be 180..1800')
    secret=os.environ.get('DEEPSEEK_API_KEY','')
    if len(secret)<22:raise Stop('ephemeral model credential required')
    ensure_tree()
    tag='hhl-'+time.strftime('%Y%m%d',time.gmtime())+'-gen1-'+secrets.token_hex(5)
    plan,source,prompt=parent(tag)
    env=accepted_env_with_key(prompt,secret)
    lockfd=c_one.exclusive_lock()
    record=None
    try:
        before=snapshot_ids(EVAL_SPOOL)
        record=make_record()
        write(record,'prompt.txt',prompt.prompt_bytes)
        start={'schema':'hhl-p5-single-paid-gen1-v1','task':'hhl-angle',
               'run_tag':tag,'parent_id':PARENT_ID,'parent_generation':0,
               'parent_tq':4169620,'model':'deepseek/deepseek-v4-pro',
               'timeout_seconds':timeout,'max_continuations':0,
               'api_usd_hard_cap_verified':False,'event_time_source_binding_required':True,
               'model_calls':0,'paid_attempt_requires_explicit_authorization':True,
               'formal_db_writes':0,'historical_2026_11_06_source':'NOT_PROVEN'}
        save_json(record,'run-start.json',start)
        code={f'uncompute/{name}':(C/'builder'/name).read_bytes()
              for name in ('builder.py','compact.py','ops.py')}
        builder.stage_builder(SCRATCH,code,expected_scratch_uid=997)
        library_staged=False
        try:
            seed.stage_library(SCRATCH,agent_uid=997)
            library_staged=True
            c_one.agent_run_root(tag,env)
            stage=p3_stage.stage_full_parent(source,PARENT_ID,tag,SCRATCH,
                              agent_uid=997,agent_gid=988,parent_generation=0,
                              verified_parent_tq=4169620)
            save_json(record,'staged-parent.json',stage)
            # Native gen0 build canary from full parent; no need to grade again.
            files=['hhl-uncompute-author.zip']+[row['new_path'] for row in plan['source_files']]
            cmd=['/usr/bin/python3','-I','-B',str(STAGE/'clients/p5_broker_client.py'),
                 '--entry',plan['target_entry']]
            for f in files:cmd.extend(['--file',f])
            built=c_one.agent_cmd(cmd,env,timeout=190)
            broker_reply=json.loads(built.stdout)
            r_id=broker_reply.get('broker_provenance_id')
            braw,bobj=journal.root_read_record(r_id,directory=BROKER_SPOOL,runner_uid=995)
            if (broker_reply.get('status')!='completed' or not bobj['execution_ok'] or
                bobj['source_bundle_sha256']!=plan['materialized_broker_input_sha256'] or
                digest(base64.b64decode(bobj['ops_b64']['candidate.ops']))!=BEST_OPS):
                raise Stop('verified full gen0 parent canary failed')
            write(record,'initial-parent-broker-raw.json',braw)
            parent_digest=source_id(bobj)
            # No new controller: identical C process-group runner/one-turn policy.
            argv=['/usr/sbin/runuser','-u','hhl-agent','-g','hhl-agent','--',
                  '/srv/hhl-task2/bin/opencode','run','--model','deepseek/deepseek-v4-pro',
                  '--agent','build','--title',prompt.title,prompt.prompt_bytes.decode('utf-8')]
            status='started'
            start_sec=time.monotonic()
            try:
                rc=c_one.run_command(argv,HHL/'agent',env,timeout,record/'opencode-gen1.log')
                status='model_exited' if rc==0 else 'model_nonzero_exit'
            except TimeoutError:
                status='model_timeout_recovered'
            except Exception as exc:
                status='model_process_exception:'+type(exc).__name__
            result={'schema':'hhl-p5-single-paid-gen1-v1','status':status,
                    'run_tag':tag,'parent_id':PARENT_ID,
                    'model_elapsed_seconds':round(time.monotonic()-start_sec,2),
                    'source_recovery':'P1_BROKER_OWNED_EVAL_TIME_JOURNAL',
                    'candidate_events':[], 'model_calls':1,'formal_db_writes':0,
                    'historical_2026_11_06_source':'NOT_PROVEN'}
            try:
                result['session']=c_one.inspect_session(prompt,env)
            except Exception as exc:
                result['session_inspection']='unverified_'+type(exc).__name__
            try:
                result['post_session_capture']=c_one.salvage_sources(tag,record)
            except Exception as exc:
                result['post_session_capture']={'status':'blocked_'+type(exc).__name__}
            try:
                result['candidate_events']=collect(before,record,{k:v for k,v in env.items() if k!='DEEPSEEK_API_KEY'},parent_digest)
                result['source_bound_events']=sum(x.get('source_binding')=='SOURCE_AT_EVAL_CORRELATED'
                                                  for x in result['candidate_events'])
                result['independent_replayed_candidates']=sum(
                    isinstance(x.get('replay'),dict) and x['replay'].get('status')=='TWO_INDEPENDENT_P1_NATIVE_REPLAYS_OK'
                    for x in result['candidate_events'])
            except Exception as exc:
                result['recovery_error']=type(exc).__name__
                result['status']='recovery_incomplete'
            save_json(record,'result.json',result)
            print('P5_GEN1_RECORD='+str(record),flush=True)
            print('P5_MODEL_RUN_STATUS='+result['status'],flush=True)
            print('P5_SOURCE_BOUND_EVENTS='+str(result.get('source_bound_events',0)),flush=True)
            print('P5_REPLAYED_CHILDREN='+str(result.get('independent_replayed_candidates',0)),flush=True)
            print('P5_FORMAL_DB_WRITES=0',flush=True)
            return result
        finally:
            if library_staged:seed.cleanup_library(SCRATCH)
            builder.cleanup_builder(SCRATCH,code)
    finally:
        os.close(lockfd)

def accepted_env_with_key(prompt,secret):
    # The root-only base verifier does not permit credentials, so validate
    # fixed configuration first, then inject only into this one child process.
    from gen1_canary import accepted_env
    orig=os.environ.pop('DEEPSEEK_API_KEY',None)
    try:
        env=accepted_env(prompt)
    finally:
        if orig is not None:os.environ['DEEPSEEK_API_KEY']=orig
    env['DEEPSEEK_API_KEY']=secret
    return env

def main():
    import argparse
    p=argparse.ArgumentParser()
    p.add_argument('--explicit-paid-gen1',action='store_true',required=True)
    p.add_argument('--timeout',type=int,default=900)
    args=p.parse_args()
    try:
        execute(args.timeout)
        return 0
    except (Stop,ValueError,KeyError,OSError,RuntimeError,PermissionError) as e:
        print('P5_PAID_STOP='+type(e).__name__+':'+str(e)[:160],file=sys.stderr)
        return 2

if __name__=='__main__':raise SystemExit(main())
