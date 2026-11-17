#!/usr/bin/env python3
"""P5 one-shot gen1 infrastructure acceptance, NEVER invokes DeepSeek.

Proves that a native OpenEvolve-sampled formal HHL parent can be staged as
multi-file editable source and built/evaluated over the P1 isolated services.
It is NOT a paid gen1 controller or a new child. Leave source-bound evidence
in private HHL records; only the original P1 event-time proof is authoritative.
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
import tempfile

RUN = Path('/run/hhl-task2')
STAGE = RUN / 'p1-review-code'
C = RUN / 'hhl-first-one-session'
HHL = Path('/srv/hhl-task2')
SCRATCH = HHL / 'agent/scratch'
DB = HHL / 'research-records/HHL_P4_FORMAL_GEN0_1q711wms'
EVAL_SPOOL = HHL / 'p1-review-evaluator-spool'
BROKER_SPOOL = HHL / 'p1-review-broker-spool'
BEST_OPS = '2c24272933b88b228fb664d43832eec5f625d0f2e03c214e0cef48991a428661'
PARENT_ID = 'b1d842e8ed28efef79b41d25125013694985c171cc9eb2778987393439c4c444'

for path in (str(C/'lib'),str(C/'scripts'),str(STAGE/'support'),str(STAGE/'support/ref'),
             str(STAGE/'broker'),str(STAGE/'eval')):
    sys.path.insert(0,path)

import hhl_p4_formal_seed as p4
import hhl_p3_stage_adapter as p3_stage
import hhl_p3_prompt_adapter as p3_prompt
from hhl_p5_bound_prompt import append_source_bound_contract
from hhl_p5_gen1_readiness import require_parent_plan
import hhl_seed_stage as seed
import hhl_builder_stage as builder
import hhl_session_plan as session
import hhl_live_config_gate as config
import one_session as c_one
import hhl_p1_broker_journal as journal
import hhl_p1_eval_binding as binding

class Stop(RuntimeError):
    pass

def digest(data:bytes)->str: return hashlib.sha256(data).hexdigest()

def ensure_tree():
    if os.geteuid()!=0:raise Stop('root required')
    if not SCRATCH.is_mount():raise Stop('scratch not mounted')
    st=SCRATCH.lstat()
    if (st.st_uid,st.st_gid,stat.S_IMODE(st.st_mode))!=(997,988,0o700):
        raise Stop('scratch owner/mode drift')
    # Exact mount flags are checked by the root shell wrapper.
    for p in (C, STAGE, STAGE/'clients', STAGE/'support', STAGE/'tools-overlay'):
        s=p.lstat()
        if s.st_uid!=0 or s.st_mode&0o022 or stat.S_ISLNK(s.st_mode):
            raise Stop('untrusted operator/code directory: '+str(p))
    for socket_name in ('p1-review-research.sock','p1-review-evaluator.sock'):
        p=RUN/socket_name
        s=p.lstat()
        if not stat.S_ISSOCK(s.st_mode) or (s.st_uid,s.st_gid,stat.S_IMODE(s.st_mode))!=(0,988,0o660):
            raise Stop('missing/unsafe native socket: '+socket_name)

def parent(run_tag:str):
    index=p4._parse_protected_json(DB, 'index.json', max_bytes=200000)
    if index.get('program_id') != PARENT_ID or index.get('ops_sha256')!=BEST_OPS:
        raise Stop('formal HHL DB changed')
    plan=p4.sample_audited(DB,run_tag=run_tag)
    require_parent_plan(plan,run_tag)
    raw=p4._parse_protected_json(DB/'bundles',PARENT_ID+'.json',max_bytes=3000000)
    full=p4._decode_source_record(raw)
    if full.sha256 != PARENT_ID:raise Stop('native parent full source differs')
    prompt=append_source_bound_contract(p3_prompt.build_verified_parent_prompt(
        run_tag,plan,parent_t=14378,parent_q=290))
    if prompt.manifest.get('parent_program_id')!=PARENT_ID:
        raise Stop('wrong final parent prompt')
    return plan,full,prompt

def accepted_env(prompt):
    env=dict(prompt.env_without_credentials)
    env['OPENCODE_CONFIG_DIR']=str(STAGE/'tools-overlay')
    env.pop('DEEPSEEK_API_KEY',None)
    cfg=json.loads((RUN/'hhl-v06-opencode.json').read_text())
    config.verify_session_contract(prompt,cfg,['research_python','quantum_evaluate'])
    if os.environ.get('DEEPSEEK_API_KEY'):
        raise Stop('no API key accepted in a zero-model canary')
    return env

def _canonical_permission(value):
    """Only two provably equivalent OpenCode rule representations."""
    if type(value) is str and value in ('allow','ask','deny'):
        return {'*': value}
    if (type(value) is dict and value and
        all(type(k) is str and type(v) is str and
            v in ('allow','ask','deny') for k,v in value.items())):
        return value
    return None


def _rule_summary(value):
    """Bounded, nonsecret diagnostics. Never print entire debug config."""
    if type(value) is str:
        return {'kind':'literal','action':value if value in ('allow','ask','deny') else 'INVALID'}
    if type(value) is dict:
        return {'kind':'map','entries':len(value),
                'wildcard':value.get('*') if value.get('*') in ('allow','ask','deny') else 'MISSING_OR_INVALID'}
    return {'kind':type(value).__name__}


def assert_effective_permissions(live:dict,expected:dict):
    """Fail closed if OpenCode effective permissions broaden C's exact policy."""
    perms=live.get('permission') if type(live) is dict else None
    if type(perms) is not dict or type(expected) is not dict:
        print('P5_EFFECTIVE_PERMISSION_DIAGNOSTIC='+json.dumps(
            {'permission_kind':type(perms).__name__},sort_keys=True),flush=True)
        raise Stop('OpenCode effective permission map absent')
    critical=('*','bash','external_directory','webfetch','websearch','task',
              'read','edit','glob','grep','list','research_python','quantum_evaluate')
    mismatches={}
    # The P5 overlay explicitly DENIES C's old broad path allowances.
    # OpenCode 1.19.4 deep-merges pattern maps rather than replacing them.
    # Read/edit must match this exact hardened merged surface; merely
    # accepting more rules or checking the wildcard would be unsafe.
    def hardened(original, narrow):
        if original.get('*')!='deny' or narrow.get('*')!='deny':
            raise Stop('C policy wildcard drift')
        old_allows={k for k,v in original.items() if k!='*' and v=='allow'}
        if any(k in narrow for k in old_allows):
            raise Stop('C legacy rule overlaps HHL narrow permission')
        rules={'*':'deny'}
        rules.update({k:'deny' for k in original if k in old_allows})
        rules.update((k,v) for k,v in narrow.items() if k!='*')
        return rules
    effective_expected=dict(expected)
    effective_expected['read']=hardened(config.ORIGINAL_READ,expected['read'])
    effective_expected['edit']=hardened(config.ORIGINAL_EDIT,expected['edit'])
    for name in critical:
        requested=_canonical_permission(effective_expected.get(name))
        observed=_canonical_permission(perms.get(name))
        if requested is None or observed != requested:
            mismatches[name]={'expected':_rule_summary(effective_expected.get(name)),
                              'observed':_rule_summary(perms.get(name))}
    # The last matching OpenCode rule wins. As a second guard, representative
    # C-approved paths must remain usable and broad paths must stay denied.
    from fnmatch import fnmatchcase
    def decision(rules,path):
        hit=None
        for pat,action in rules.items():
            if fnmatchcase(path,pat): hit=action
        return hit
    probes={
      'read':(
        ('scratch/runs/hhl-test/attempt-01/circuit.py',
         '/srv/hhl-task2/agent/docs/HHL_TASK_SPEC.md',
         '/srv/hhl-task2/agent/scratch/runs/hhl-test/attempt-01/circuit.py'),
        ('/srv/hhl-task2/agent/opencode.json',
         'scratch/.env',
         'scratch/runs/hhl-test/attempt-01/.env',
         'scratch/runs/hhl-test/attempt-01/secrets.env',
         '/srv/hhl-task2/agent/scratch/runs/hhl-test/attempt-01/.env',
         '/srv/hhl-task2/agent/private.txt')),
      'edit':(
        ('scratch/runs/hhl-test/attempt-01/circuit.py',
         '/srv/hhl-task2/agent/scratch/runs/hhl-test/attempt-01/circuit.py'),
        ('scratch/.env','scratch/other.txt',
         '/srv/hhl-task2/agent/scratch/other.txt')),
    }
    # Global OpenCode rule maps are content-checked above. Deep merges may
    # change their iteration order and mask ALLOW paths behind legacy DENY.
    # Such masking is safe (denial), but a broad global ALLOW is never safe.
    # Agent-specific C-approved narrow rules below must supply usable paths.
    for category,(_,denied_paths) in probes.items():
        rules=perms.get(category)
        if type(rules) is dict:
            for path in denied_paths:
                if decision(rules,path)!='deny':
                    mismatches[category+'.denied-path-probe']={'status':'not_deny'}
    # Agent-level overrides take precedence; an allowed bash/read/edit at
    # the build-agent level could undermine a global default-deny check.
    agents=live.get('agent')
    if type(agents) is not dict:
        mismatches['agent_structure']={'kind':type(agents).__name__}
    else:
        build=agents.get('build')
        if type(build) is not dict:
            mismatches['agent.build_structure']={'kind':type(build).__name__}
        else:
            overrides=build.get('permission')
            if type(overrides) is not dict:
                mismatches['agent.build.permission_structure']={'kind':type(overrides).__name__}
            else:
                # OpenCode applies build-agent rules after global rules.
                # Do not accept weaker/expanded paths: the agent override
                # must equal C's exact prior approved narrow policy.
                for name in ('read','edit'):
                    rules=overrides.get(name)
                    if _canonical_permission(rules)!=_canonical_permission(expected.get(name)):
                        mismatches['agent.build.'+name]={'observed':_rule_summary(rules)}
                    elif type(rules) is dict:
                        allowed_paths,denied_paths=probes[name]
                        for path in allowed_paths:
                            if decision(rules,path)!='allow':
                                mismatches['agent.build.'+name+'.allowed-path']={'status':'not_allow'}
                        for path in denied_paths:
                            if decision(rules,path)!='deny':
                                mismatches['agent.build.'+name+'.denied-path']={'status':'not_deny'}
                for name, value in overrides.items():
                    if name in critical and name not in ('read','edit') and (
                      _canonical_permission(value)!=_canonical_permission(expected.get(name))):
                        mismatches['agent.build.'+name]={'observed':_rule_summary(value)}
            legacy=build.get('tools')
            if type(legacy) is dict:
                for tool in ('bash','write','edit','apply_patch','task','webfetch','websearch'):
                    if legacy.get(tool) is True:
                        mismatches['agent.build.tools.'+tool]={'legacy_override':True}
    if mismatches:
        print('P5_EFFECTIVE_PERMISSION_DIAGNOSTIC='+json.dumps(
            {'mismatches':mismatches,'other_permission_keys':sorted(
                k for k in perms if k not in critical)[:20]},
            ensure_ascii=True,sort_keys=True,separators=(',',':')),flush=True)
        raise Stop('OpenCode live permission contract is not fail-closed')


def make_record():
    base=HHL/'research-records'
    st=base.lstat()
    if (st.st_uid,stat.S_IMODE(st.st_mode))!=(0,0o700):raise Stop('untrusted HHL records')
    r=Path(tempfile.mkdtemp(prefix='HHL_P5_GEN1_CANARY_',dir=base))
    r.chmod(0o700)
    return r

def save_json(dest,name,obj):
    c_one.durable_json(dest,name,obj)

def canary(run_tag:str)->dict:
    ensure_tree()
    plan,source,prompt=parent(run_tag)
    env=accepted_env(prompt)
    # Live OpenCode 1.19.4 config resolution, not a mock. No provider API call.
    cfgprobe=c_one.agent_cmd(['/srv/hhl-task2/bin/opencode','debug','config'],env,timeout=50)
    if not 0<len(cfgprobe.stdout)<1_000_000:
        raise Stop('OpenCode config could not be inspected')
    live=json.loads(cfgprobe.stdout)
    # OpenCode may normalize a literal permission to the equivalent
    # wildcard-only object. Compare the whole C-approved permission surface,
    # not merely three string-valued tool names. Never relax access on failure.
    expected=json.loads((RUN/'hhl-v06-opencode.json').read_bytes())['permission']
    assert_effective_permissions(live,expected)
    print('P5_OPENCODE_1_19_4_EFFECTIVE_CONFIG=PASS',flush=True)
    # The CLI's debug config proves settings, NOT which duplicate-named JS tool
    # it ultimately resolves. The first paid run must demand P1 evidence.
    receipt=make_record()
    save_json(receipt,'plan.json',dict(plan, source_bytes_identical=True))
    save_json(receipt,'parent.json',{'source_program_id':PARENT_ID,'parent_generation':0,
            'source_file_count':len(source.files),'prompt_sha256':digest(prompt.prompt_bytes),
            'model_calls':0,'is_offspring':False,'formal_db_writes':0})
    lockfd = c_one.exclusive_lock()
    try:
        code={f'uncompute/{n}':(C/'builder'/n).read_bytes() for n in ('builder.py','compact.py','ops.py')}
        builder.stage_builder(SCRATCH,code,expected_scratch_uid=997)
        library_staged=False
        try:
            seed.stage_library(SCRATCH,agent_uid=997)
            library_staged=True
            c_one.agent_run_root(run_tag,env)
            staged=p3_stage.stage_full_parent(source,PARENT_ID,run_tag,SCRATCH,
                                  agent_uid=997,agent_gid=988,parent_generation=0,
                                  verified_parent_tq=4169620)
            save_json(receipt,'staged-parent.json',staged)
            files=['hhl-uncompute-author.zip']+[x['new_path'] for x in plan['source_files']]
            entry=plan['target_entry']
            argv=['/usr/bin/python3','-I','-B',str(STAGE/'clients/p5_broker_client.py'),
                  '--entry',entry]
            for f in files:argv.extend(['--file',f])
            ans=c_one.agent_cmd(argv,env,timeout=190)
            reply=json.loads(ans.stdout)
            artifacts=reply.get('saved_artifacts')
            if (reply.get('status')!='completed' or reply.get('execution_ok') is not True or
                reply.get('cleanup_confirmed') is not True or type(artifacts) is not list or
                len(artifacts)!=1 or not artifacts[0].endswith('/candidate.ops')):
                raise Stop('native P1 parent canary build failed')
            bid=reply.get('broker_provenance_id')
            if not isinstance(bid,str) or not re.fullmatch('[0-9a-f]{32}',bid):
                raise Stop('no signed Broker journal ID')
            braw,bobj=journal.root_read_record(bid,directory=BROKER_SPOOL,runner_uid=995)
            if bobj['source_bundle_sha256']!=plan['materialized_broker_input_sha256'] or not bobj['execution_ok']:
                raise Stop('P1 source journal does not match DB-staged exact parent')
            if digest(__import__('base64').b64decode(bobj['ops_b64']['candidate.ops']))!=BEST_OPS:
                raise Stop('gen0 parent circuit not reproducible from exact staged source')
            c_one.durable_file(receipt,'parent-build-broker-raw.json',braw)
            eargv=['/usr/bin/python3','-I','-B',str(STAGE/'clients/p5_eval_client.py'),
                   '--ops',artifacts[0],'--broker-run-id',bid,'--broker-artifact','candidate.ops']
            response=c_one.agent_cmd(eargv,env,timeout=150)
            e=json.loads(response.stdout)
            if (e.get('valid') is not True or e.get('verdict')!='OK' or
                e.get('tq_cost')!=4169620 or e.get('source_binding_status')!='UNVERIFIED_BROKER_CLAIM'):
                raise Stop('P1 native source-bound parent evaluation failed')
            eid=e.get('trusted_evidence_id')
            eraw,evobj,ops=binding.root_read_claim_event(eid,directory=EVAL_SPOOL,
                   owner_uid=__import__('pwd').getpwnam('hhl-eval').pw_uid)
            corr=binding.audit_bound_pair(eraw,braw,broker_validator=journal.validate_record)
            if corr['status']!='BROKER_SOURCE_CLAIM_CORRELATED' or corr.get('formal_db_eligible') is not False:
                raise Stop('evaluator/broker source relation not attested')
            c_one.durable_file(receipt,'parent-evaluator-raw.json',eraw)
            # P7 free LIVE proof: exercise the newly introduced automatic
            # research_python -> Broker -> native Evaluator client path.
            # This is a repeat of the verified gen0 Parent, NOT a child.
            auto_argv=['/usr/bin/python3','-I','-B',str(STAGE/'clients/p5_broker_client.py'),
                       '--auto-native-evaluate','--entry',entry]
            for f in files:auto_argv.extend(['--file',f])
            auto_run=c_one.agent_cmd(auto_argv,env,timeout=325)
            auto=json.loads(auto_run.stdout)
            if (auto.get('status')!='completed' or auto.get('execution_ok') is not True or
                auto.get('cleanup_confirmed') is not True or auto.get('exit_code')!=0):
                raise Stop('P7 auto-native Broker build failed')
            auto_artifacts=auto.get('saved_artifacts')
            if (type(auto_artifacts) is not list or len(auto_artifacts)!=1 or
                not auto_artifacts[0].endswith('/candidate.ops')):
                raise Stop('P7 auto-native missing exact parent ops')
            auto_results=auto.get('auto_native_evaluations')
            if (type(auto_results) is not list or len(auto_results)!=1 or
                auto_results[0].get('artifact')!='candidate.ops' or
                auto_results[0].get('status')!='NATIVE_REPLY' or
                auto_results[0].get('formal_db_eligible') is not False):
                raise Stop('P7 automatic native submission absent or refused')
            native=auto_results[0].get('native')
            if (type(native) is not dict or native.get('valid') is not True or
                native.get('verdict')!='OK' or native.get('tq_cost')!=4169620 or
                native.get('source_binding_status')!='UNVERIFIED_BROKER_CLAIM'):
                raise Stop('P7 automatic native reply not exact gen0 native OK')
            auto_bid=auto.get('broker_provenance_id')
            auto_eid=native.get('trusted_evidence_id')
            auto_braw,auto_bobj=journal.root_read_record(auto_bid,directory=BROKER_SPOOL,runner_uid=995)
            auto_eraw,auto_eobj,auto_ops=binding.root_read_claim_event(
                auto_eid,directory=EVAL_SPOOL,owner_uid=__import__('pwd').getpwnam('hhl-eval').pw_uid)
            auto_corr=binding.audit_bound_pair(auto_eraw,auto_braw,
                                            broker_validator=journal.validate_record)
            if (auto_corr['status']!='BROKER_SOURCE_CLAIM_CORRELATED' or
                auto_corr.get('formal_db_eligible') is not False or
                auto_bobj['source_bundle_sha256']!=plan['materialized_broker_input_sha256'] or
                digest(auto_ops)!=BEST_OPS):
                raise Stop('P7 automatic source-bound protected receipt mismatch')
            c_one.durable_file(receipt,'p7-auto-native-broker-raw.json',auto_braw)
            c_one.durable_file(receipt,'p7-auto-native-evaluator-raw.json',auto_eraw)
            result={'status':'P5_FORMAL_PARENT_TO_NATIVE_SOURCE_BOUND_CANARY_PASS',
                    'task':'hhl-angle','parent_id':PARENT_ID,'parent_generation':0,
                    'materialized_source_bundle_sha256':bobj['source_bundle_sha256'],
                    'broker_run_id':bid,'evaluator_event_id':eid,'ops_sha256':digest(ops),
                    'TQ':e['tq_cost'],'native_verdict':e['verdict'],
                    'source_bound_correlation':corr['status'],
                    'historical_2026_11_06_source':'NOT_PROVEN','gen1_child_created':False,
                    'model_calls':0,'formal_db_writes':0,'receipt_directory':str(receipt),
                    'p7_auto_native_gate_status':'PASS',
                    'p7_auto_broker_run_id':auto_bid,
                    'p7_auto_evaluator_event_id':auto_eid}
            save_json(receipt,'result.json',result)
            print('P7_FREE_AUTO_NATIVE_SOURCE_BOUND=PASS',flush=True)
            print('P5_FORMAL_PARENT_TO_NATIVE_SOURCE_BOUND_CANARY=PASS',flush=True)
            print('P5_ROOT_ONLY_RECEIPT_DIR='+str(receipt),flush=True)
            print(json.dumps(result,ensure_ascii=False,sort_keys=True),flush=True)
            return result
        finally:
            # C's existing private staging lifecycle, no ownership relaxation.
            if library_staged: seed.cleanup_library(SCRATCH)
            builder.cleanup_builder(SCRATCH,code)
    finally:
        os.close(lockfd)

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--native-canary',action='store_true',required=True)
    p.add_argument('--run-tag',required=True)
    a=p.parse_args()
    if not re.fullmatch(r'hhl-[a-z0-9][a-z0-9-]{7,56}',a.run_tag):
        raise SystemExit('P5_STOP=INVALID_RUN_TAG')
    try:
        canary(a.run_tag)
        return 0
    except (ValueError,OSError,RuntimeError,PermissionError,KeyError) as exc:
        print('P5_STOP='+type(exc).__name__+':'+str(exc)[:200],file=sys.stderr)
        return 2

if __name__=='__main__':raise SystemExit(main())
