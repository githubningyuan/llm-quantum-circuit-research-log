#!/usr/bin/env python3
"""Free Gen2-PARENT proof, using existing P6 identity and P7/C native clients.

No paid model calls. No native evolution DB modifications. Staging writes a
fresh scratch attempt and protected evidence receipts, as in existing P7 canary.
"""
from __future__ import annotations
import argparse
import base64
import json
import math
import os
from pathlib import Path
import pwd
import re
import sys

# -I ignores script directory; explicitly pin the checked extraction directory.
HERE=Path(__file__).resolve(strict=True).parent
sys.path.insert(0,str(HERE))
# Reuse VM-installed P7 code rather than copying its operator/runtime.
P7=Path('/run/hhl-task2/p1-review-code')
sys.path.insert(0,str(P7))
from gen1_canary import (ensure_tree, accepted_env, digest, RUN, STAGE, C,
                         SCRATCH, EVAL_SPOOL, BROKER_SPOOL, HHL, Stop,
                         builder, seed, c_one, journal, binding)
import hhl_p3_stage_adapter as p3_stage
import hhl_p3_prompt_adapter as p3_prompt
from hhl_p5_bound_prompt import append_source_bound_contract
from hhl_gen2_parent_adapter import prepare_gen2_parent
import p6_hhl_nextgen as p6

BRANCH=Path('/srv/hhl-task2/research-records/HHL_P6_FORMAL_BRANCH_dmqvzs3h')

def verify_native_metrics(native:dict, protected_result:dict, t:int, q:int, tq:int) -> None:
    """P7's auto-native reply guarantees TQ, NOT individual T/Q metrics.

    The independent protected evaluator event is the authoritative source for
    complete (T, Q, TQ). Reject contradictions if the client does include T/Q.
    """
    if (type(native) is not dict or native.get('valid') is not True or
        native.get('verdict') != 'OK' or
        native.get('source_binding_status') != 'UNVERIFIED_BROKER_CLAIM' or
        type(native.get('tq_cost')) is not int or native['tq_cost'] != tq):
        raise Stop('P7 auto-native verdict or TQ mismatch')
    for key, expected in (('toffoli_count', t), ('peak_qubits', q)):
        if key in native and (type(native[key]) is not int or native[key] != expected):
            raise Stop('P7 optional native metric mismatch: '+key)
    if (type(protected_result) is not dict or
        protected_result.get('valid') is not True or
        protected_result.get('verdict') != 'OK' or
        any(type(protected_result.get(k)) not in (int,float) or
            not math.isfinite(protected_result[k]) or
            int(protected_result[k]) != protected_result[k] for k in
            ('toffoli','qubits','tq_cost')) or
        (protected_result['toffoli'],protected_result['qubits'],protected_result['tq_cost']) != (t,q,tq)):
        raise Stop('protected evaluator T/Q/TQ mismatch')


def check_native_parent(branch:Path,tag:str)->dict:
    ensure_tree()
    if branch != BRANCH:raise Stop('unapproved HHL branch')
    checked=prepare_gen2_parent(branch,tag,p6)
    prompt=append_source_bound_contract(p3_prompt.build_verified_parent_prompt(
        tag,checked.plan,parent_t=checked.t,parent_q=checked.q))
    if prompt.manifest.get('parent_program_id')!=checked.program_id:
        raise Stop('prompt parent ID mismatch')
    env=accepted_env(prompt)
    lockfd=c_one.exclusive_lock()
    try:
        code={f'uncompute/{n}':(C/'builder'/n).read_bytes()
              for n in ('builder.py','compact.py','ops.py')}
        builder.stage_builder(SCRATCH,code,expected_scratch_uid=997)
        library_staged=False
        try:
            seed.stage_library(SCRATCH,agent_uid=997)
            library_staged=True
            c_one.agent_run_root(tag,env)
            staged=p3_stage.stage_full_parent(
                checked.source,checked.program_id,tag,SCRATCH,
                agent_uid=997,agent_gid=988,parent_generation=1,
                verified_parent_tq=checked.tq)
            if (staged['materialized_broker_input_sha256'] !=
                    checked.plan['materialized_broker_input_sha256']):
                raise Stop('P7 stage different from P6 verified source')
            files=['hhl-uncompute-author.zip'] + [row['new_path'] for row in checked.plan['source_files']]
            argv=['/usr/bin/python3','-I','-B',str(STAGE/'clients/p5_broker_client.py'),
                  '--auto-native-evaluate','--entry',checked.plan['target_entry']]
            for f in files:argv.extend(['--file',f])
            run=c_one.agent_cmd(argv,env,timeout=325)
            reply=json.loads(run.stdout)
            if (reply.get('status')!='completed' or reply.get('execution_ok') is not True or
                reply.get('cleanup_confirmed') is not True or reply.get('exit_code')!=0):
                raise Stop('Gen1 parent native build failed')
            bid=reply.get('broker_provenance_id')
            braw,bobj=journal.root_read_record(bid,directory=BROKER_SPOOL,runner_uid=995)
            if (not bobj['execution_ok'] or
                bobj['source_bundle_sha256']!=checked.plan['materialized_broker_input_sha256']):
                raise Stop('native Broker source mismatch')
            ops=base64.b64decode(bobj['ops_b64']['candidate.ops'],validate=True)
            if digest(ops)!=checked.ops_sha256:
                raise Stop('native-built ops differs from admitted Gen1')
            ar=reply.get('auto_native_evaluations')
            if (not isinstance(ar,list) or len(ar)!=1 or
                ar[0].get('artifact')!='candidate.ops' or
                ar[0].get('status')!='NATIVE_REPLY' or
                ar[0].get('formal_db_eligible') is not False):
                raise Stop('automatic native evaluation response invalid')
            native=ar[0].get('native')
            if type(native) is not dict:
                raise Stop('missing P7 native reply')
            eid=native.get('trusted_evidence_id')
            eraw,eobj,eops=binding.root_read_claim_event(
                eid,directory=EVAL_SPOOL,owner_uid=pwd.getpwnam('hhl-eval').pw_uid)
            corr=binding.audit_bound_pair(eraw,braw,broker_validator=journal.validate_record)
            verify_native_metrics(native,eobj.get('result'),checked.t,checked.q,checked.tq)
            if (eops!=ops or corr['status']!='BROKER_SOURCE_CLAIM_CORRELATED' or
                corr.get('formal_db_eligible') is not False):
                raise Stop('protected source-bound native proof invalid')
            return {'status':'P6_P7_GEN2_PARENT_FREE_NATIVE_GATE_PASS',
                    'parent_id':checked.program_id,'parent_generation':1,
                    'next_generation':2,'T':checked.t,'Q':checked.q,'TQ':checked.tq,
                    'ops_sha256':checked.ops_sha256,'source_files':len(checked.plan['source_files']),
                    'model_calls':0,'formal_db_writes':0,
                    'native_event_id':eid,'broker_run_id':bid,
                    'staged_to_agent':True,'offspring_created':False}
        finally:
            if library_staged:seed.cleanup_library(SCRATCH)
            builder.cleanup_builder(SCRATCH,code)
    finally:os.close(lockfd)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--free-gen2-parent-gate',action='store_true',required=True)
    ap.add_argument('--run-tag',required=True)
    args=ap.parse_args()
    if not re.fullmatch(r'hhl-[a-z0-9][a-z0-9-]{7,56}',args.run_tag):
        raise SystemExit('GEN2_FREE_GATE_STOP=INVALID_RUN_TAG')
    try:
        result=check_native_parent(BRANCH,args.run_tag)
        print(result['status'])
        print(json.dumps(result,sort_keys=True))
        return 0
    except (ValueError,OSError,RuntimeError,PermissionError,KeyError,TypeError) as exc:
        print('GEN2_FREE_GATE_STOP='+type(exc).__name__+':'+str(exc)[:180],file=sys.stderr)
        return 2

if __name__=='__main__':raise SystemExit(main())
