#!/usr/bin/env python3
"""P5: audited HHL gen1 parent/prompt readiness. NO model, DB writes or staging.

This is not a new evolution controller. It invokes the already VM-proven P4
native OpenEvolve sampler and C's P3 parent/prompt adapters, and *refuses* to
claim paid readiness until C's active Session uses P1 source-bound components.

Do not pass credentials to this program. Its output is metadata only.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
sys.path.insert(0,str(HERE/'ref'))

DB_ROOT=Path('/srv/hhl-task2/research-records/HHL_P4_FORMAL_GEN0_1q711wms')
OPERATOR=Path('/run/hhl-task2/hhl-first-one-session')
SCRATCH=Path('/srv/hhl-task2/agent/scratch')
TOOLS=Path('/srv/hhl-task2/agent/.opencode/tools')
TAG_RE=re.compile(r'hhl-[a-z0-9][a-z0-9-]{7,56}\Z')
PARENT='b1d842e8ed28efef79b41d25125013694985c171cc9eb2778987393439c4c444'
OLD_BOUND_TOOL_SHA='2c4f2351a42a3df87b79b576b269b7852c6eb48fb5195ae0438950c623095b38'
NEW_BOUND_TOOL_SHA=''
# No authorization is inferred from any evidence read by this script.

class Stop(ValueError): pass

def sha(path:Path)->str:
    if path.is_symlink() or not path.is_file():raise Stop('missing or symlinked file: '+str(path))
    st=path.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1:raise Stop('unsafe file inode')
    return hashlib.sha256(path.read_bytes()).hexdigest()

def require_parent_plan(plan:dict, run_tag:str)->dict:
    if type(plan) is not dict or not TAG_RE.fullmatch(run_tag):raise Stop('invalid plan / run tag')
    if not (plan.get('schema')=='hhl-p3-next-generation-parent-plan-v1' and
        plan.get('native_formal_parent_sample')=='PASS' and
        plan.get('research_db_admission')=='FORMAL_HHL_GEN0_P1_NEW_REPLAY_ONLY' and
        plan.get('formal_gen0_eligible') is True and
        plan.get('parent_original_program_id')==PARENT and
        plan.get('parent_program_generation')==0 and
        plan.get('verified_parent_tq')==4169620 and
        plan.get('full_source_file_count')==13 and
        plan.get('run_tag')==run_tag and
        plan.get('source_bytes_identical') is True and
        plan.get('materialized_is_new_offspring') is False and
        plan.get('ready_for_paid_model') is False and
        plan.get('historical_p0_source_attribution')=='NOT_PROVEN'):
        raise Stop('not the exact native sampled P4 full gen0 parent')
    expected=f'runs/{run_tag}/attempt-01/'
    if plan.get('target_entry')!=expected+'main.py':raise Stop('incorrect gen1 main entry')
    names=plan.get('source_files')
    if type(names) is not list or len(names)!=13:raise Stop('incomplete full parent')
    suffixes=set()
    for item in names:
        if type(item) is not dict:raise Stop('invalid source entry')
        name=item.get('name')
        if type(name) is not str or '/' in name or name.startswith('.') or name in suffixes:
            raise Stop('invalid source basename')
        suffixes.add(name)
        if (item.get('new_path')!=expected+name or
            type(item.get('sha256')) is not str or len(item['sha256'])!=64 or
            not re.fullmatch(r'[0-9a-f]{64}',item['sha256']) or
            type(item.get('bytes')) is not int or item['bytes']<=0):
            raise Stop('incorrect source identity')
    if not {'main.py','circuit.py','constants.json','meta.json','contract.json','grader.py'} <= suffixes:
        raise Stop('necessary source missing')
    h=plan.get('materialized_broker_input_sha256')
    if type(h) is not str or not re.fullmatch(r'[0-9a-f]{64}',h) or h==PARENT:
        raise Stop('invalid rebased input SHA')
    return {'parent_program_id':PARENT,'parent_generation':0,'parent_tq':4169620,
            'materialized_input_sha256':h,'source_files':len(names),
            'source_names':sorted(suffixes),'target_entry':expected+'main.py'}

def audit(run_tag:str, db_root:Path=DB_ROOT, *, check_live:bool=True)->dict:
    if os.geteuid()!=0:raise Stop('root operator required')
    if not TAG_RE.fullmatch(run_tag):raise Stop('bad new run tag')
    if db_root!=DB_ROOT:raise Stop('unapproved HHL DB path')
    import hhl_p4_formal_seed as p4
    import hhl_p3_prompt_adapter as prompt_adapter
    p4_sample=p4.sample_audited(db_root,run_tag=run_tag)
    plan=require_parent_plan(p4_sample,run_tag)
    # Freshly sampled and fully archived P4 parent; do not accept self-reported
    # Program.code or a partial main.py as proof of source identity.
    old_path=sys.path[:]
    try:
        sys.path.insert(0,str(OPERATOR/'lib'))
        from hhl_p5_bound_prompt import append_source_bound_contract
        p=append_source_bound_contract(
            prompt_adapter.build_verified_parent_prompt(run_tag,p4_sample,parent_t=14378,parent_q=290))
    finally:
        sys.path[:]=old_path
    prompt=p.prompt_bytes
    if (p.manifest.get('parent_program_id')!=PARENT or
        p.manifest.get('materialized_input_sha256')!=plan['materialized_input_sha256'] or
        p.manifest.get('source_bound_evaluator_required') is not True or
        p.manifest.get('paid_authorization') is not False):
        raise Stop('parent prompt provenance mismatch')
    p_text=prompt.decode('utf-8')
    if 'T=16468' in p_text or 'T×Q=4940400' in p_text:
        raise Stop('original baseline substituted for verified parent')
    if f'runs/{run_tag}/attempt-01/circuit.py' not in p_text:
        raise Stop('prompt does not point at the newly staged parent')
    checks=[]
    if check_live:
        if not os.path.ismount(SCRATCH):checks.append('SCRATCH_NOT_MOUNTED')
        else:
            st=SCRATCH.lstat()
            if (st.st_uid,st.st_gid,stat.S_IMODE(st.st_mode))!=(997,988,0o700):
                checks.append('SCRATCH_IDENTITY_MISMATCH')
        # The VM operator currently *always* stages the original Baseline.
        # A distinct, signed gen1 root controller branch must replace that
        # behaviour before any true Parent -> Child call.
        from pathlib import Path
        operator_src=(OPERATOR/'scripts/one_session.py')
        code=operator_src.read_text() if operator_src.is_file() and not operator_src.is_symlink() else ''
        if 'seed_receipt=stage_parent(SCRATCH,tag' in code:
            checks.append('C_OPERATOR_STILL_BASELINE_FIRST')
        if not code:checks.append('OPERATOR_SOURCE_MISSING')
        # P1 v2 validation was done in ISOLATED services, not installed here.
        # Do NOT infer it is active merely from historical PASS records.
        eval_tool=TOOLS/'quantum_evaluate.js'
        if sha(eval_tool)==OLD_BOUND_TOOL_SHA:
            checks.append('LIVE_EVALUATOR_TOOL_STILL_V1')
        elif not ('broker_run_id' in eval_tool.read_text() and 'broker_artifact' in eval_tool.read_text()):
            checks.append('LIVE_EVALUATOR_SOURCE_BINDING_NOT_PROVEN')
        broker_s=Path('/run/hhl-task2/research-v4.sock')
        if broker_s.exists():checks.append('RESEARCH_SOCKET_ALREADY_ACTIVE')
        attempt=SCRATCH/'runs'/run_tag
        if attempt.exists() or attempt.is_symlink():checks.append('RUN_TAG_COLLISION')
    return {'schema':'hhl-p5-gen1-readiness-v1','task':'hhl-angle',
            'formal_gen0_db':str(db_root),'native_parent_sample':'PASS',
            'parent':plan,'prompt_sha256':hashlib.sha256(prompt).hexdigest(),
            'prompt_bytes':len(prompt),'historical_2026_11_06_source':'NOT_PROVEN',
            'staged_parent':False,'gen1_child_created':False,
            'live_source_bound_model_chain_ready':False,
            'blocking_checks':checks,'paid_model_authorized':False,
            'model_calls':0,'formal_db_writes':0,'coulomb_writes':0}

def main()->int:
    ap=argparse.ArgumentParser()
    ap.add_argument('--audit',action='store_true',required=True)
    ap.add_argument('--run-tag',required=True)
    args=ap.parse_args()
    try:
        result=audit(args.run_tag)
        print('P5_NATIVE_FORMAL_GEN0_PARENT_AND_PROMPT=PASS')
        print('P5_LIVE_GEN1_PAID_READY=NO')
        print(json.dumps(result,ensure_ascii=False,sort_keys=True,separators=(',',':')))
        return 0
    except (RuntimeError,ValueError,TypeError,KeyError,OSError,ImportError,AssertionError) as exc:
        print('P5_STOP='+type(exc).__name__+':'+str(exc)[:240],file=sys.stderr)
        return 2

if __name__=='__main__':raise SystemExit(main())
