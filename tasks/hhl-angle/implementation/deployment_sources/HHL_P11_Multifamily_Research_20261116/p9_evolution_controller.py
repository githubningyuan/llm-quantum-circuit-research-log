#!/usr/bin/env python3
"""P9: bounded autonomous HHL LLM -> native Evaluator -> OpenEvolve -> next generation.

Reuses the VM-pinned P7 P1 Broker/Evaluator and P6 OpenEvolve source provenance.
Each *accepted* candidate has an independent protected source-at-evaluation pair.
No exhaustive X sweep or extra replays are imposed as a paper-unjustified admission gate.
Runs in an isolated NEW HHL branch; P6 historic DB and Coulomb DB remain read-only.
"""
from __future__ import annotations
import argparse
import base64
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import time

# Python -I removes the script's directory from sys.path; keep imports
# explicitly pinned to the root-installed, verified P7 staging directory.
# This is the same bootstrap already used by the proven gen2_paid.py entrypoint.
sys.path.insert(0, str(Path(__file__).resolve(strict=True).parent))

from gen1_canary import (STAGE, SCRATCH, EVAL_SPOOL, BROKER_SPOOL, HHL, C,
                         ensure_tree, digest, seed, builder, c_one, journal, binding)
from gen2_paid import (accepted_env_with_key, snapshot_ids, find_unique_active_session)
import hhl_p3_stage_adapter as p3_stage
import hhl_p3_prompt_adapter as p3_prompt
import p6_hhl_nextgen as p6
import hhl_session_plan as session
from openevolve_seed_adapter_A_original import load_database_with_verified_stats
from openevolve.database import Program, ProgramDatabase
import p11_research_layer as p11

RECORDS=p6.RECORDS
P6BRANCH=RECORDS/'HHL_P6_FORMAL_BRANCH_dmqvzs3h'
GEN0=p6.GEN0_ID
GEN1='4532fd3faad9c889e25efb3e0366c0564875565b5b7646de22f478191419c47e'
FAMILIES=('LINEAGE_GEN1','INDEPENDENT_A','INDEPENDENT_B')
SCHEDULE=('LINEAGE_GEN1','LINEAGE_GEN1','INDEPENDENT_A','INDEPENDENT_B')
BASELINE=4_940_400
MAX_EVAL_EVENTS=80
NEW_SCHEMA='hhl-p9-autonomous-multifamily-v1'
P11_REV='P11_FAMILY_MEMORY_INSPIRATION_R1'
ID_RE=re.compile(r'[0-9a-f]{64}\Z')

E_CANDIDATE_FIRST_ORIGINAL = 'A-INSPIRED HHL CANDIDATE-FIRST RESEARCH CONTRACT:\nYour main task is to CREATE AND TEST actual distinct HHL quantum circuit offspring, not merely produce a numerical analysis or claim near-optimality. Make a substantive implementation attempt promptly; prioritize producing and testing actual candidate circuits rather than exhaustively studying the library. You decide the algorithm and may change circuit architecture. Use the existing Gen1 parent only as the independently verified baseline. Preserve the exact task contract and semantics: hhl-angle domain, precision and allowed ULP error, reversible compute/uncompute, clean ancillas, the protected source-to-evaluation provenance, and genuine measured T, Q, TQ. Use only the HHL interface that actually exists here: write complete Builder-compatible Python source files, then call research_python with entry and files to build the .ops artifact and obtain the independent native grader response. There is NO A-style quantum_evaluate shortcut for arbitrary source edits; do not fabricate one. research_python does NOT forward extra sys.argv arguments; encode parameters in your source. Every claimed offspring must have distinct emitted .ops and a native grading receipt; an analytic or sampled error estimate is NOT a native circuit verdict. Read the complete tool result, including stderr, exit code, Builder errors, native correctness verdict and measured cost. If a candidate fails, use the actual feedback to repair it and test again. If an approach fails, change methods autonomously rather than generalize a local failure into a proof that the parent is optimal. A near-miss deserves targeted debugging when scientifically justified. Do not claim success without measured evidence or suppress failed experiments. Native grading may sample input cases: a sampled OK result is not a proof of correct output over the complete HHL domain. The hard inclusive bound is 429 ULP: 430 ULP is a failure, never call it equal to 429. When claiming an exhaustively correct candidate, use a full-domain check that matches the actual emitted Builder arithmetic semantics (including row-by-row truncation), and distinguish independent simulator checks from analytic surrogates. Native-graded candidates with remaining correctness uncertainty must be labeled provisional, not admitted. Keep updating the existing visible research_log.txt with succinct observations, decisions, failed builds and grader verdicts. Do not rewrite formal DB records or alter the trusted evaluator.'
E_SOURCE_SHA256 = '3612d0f9129ed0770089800e30f8aa32f2c0fda6e7c1223d4ab3d671070aac50'
P10_PROMPT_REV = 'P10_E_CANDIDATE_FIRST_P9_MULTIGEN'

def p10_candidate_first_for(parent:dict,run_tag:str)->str:
    contract=E_CANDIDATE_FIRST_ORIGINAL
    if parent['kind']=='IDEATION_BLANK':
        contract=contract.replace(
            'Use the existing Gen1 parent only as the independently verified baseline.',
            'This is an independent family: there is no inherited verified parent circuit. '
            'Use only the task specification and Builder interface; develop a genuine distinct architecture.')
    else:
        contract=contract.replace(
            'Use the existing Gen1 parent only as the independently verified baseline.',
            'Use the native OpenEvolve selected parent as an independently verified baseline, '
            'not as a new offspring; its ID and generation are provided above.')
    # This is the only acceptance-policy adjustment: original paper uses native
    # benchmark sampled testing; exhaustive-domain checks are not mandatory admission.
    contract=contract.replace(
        'Native-graded candidates with remaining correctness uncertainty must be labeled provisional, not admitted.',
        'Paper-benchmark native OK and protected source-at-evaluation evidence are the '
        'admission basis. Report known counterexamples and uncertainty explicitly; '
        'do not call sampled validation a full-domain proof.')
    if 'not admitted.' in contract:
        raise Stop('legacy exhaustive admission restriction still present')
    return ('\n\n'+contract+'\n'
            'SESSION WORKING DIARY: Create and update '
            'scratch/runs/'+run_tag+'/attempt-01/research_log.txt early in the session; '
            'record concise observations, build failures, native grading, and the next '
            'concrete implementation step. Never substitute analysis for an emitted circuit.\n')

class Stop(RuntimeError): pass

def sha(data:bytes)->str:
    return hashlib.sha256(data).hexdigest()

def jbytes(data)->bytes:
    return p6.json_bytes(data)

def store(path:Path, data):
    """Root-only write + atomic replace for mutable branch index and progress."""
    raw=jbytes(data) if not isinstance(data,bytes) else data
    if path.is_symlink() or path.parent.is_symlink():raise Stop('unsafe output symlink')
    tmp=path.with_name('.'+path.name+'.'+secrets.token_hex(6))
    with tmp.open('xb') as f:
        os.chmod(tmp,0o600)
        f.write(raw);f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)
    dfd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(dfd)
    finally:os.close(dfd)

def mkdir(path:Path):
    path.mkdir(mode=0o700,parents=False,exist_ok=False)
    path.chmod(0o700)

def safe_branch(root:Path):
    if root.parent!=RECORDS or not re.fullmatch(r'HHL_P9_EVOLUTION_[a-z0-9]{8}',root.name):
        raise Stop('unexpected branch name')
    for p in (root,root/'db',root/'bundles',root/'sessions',root/'receipts'):
        p6.private_dir(p)

def get_seed_archives():
    """This is the previously source-proven Gen0/Gen1, not an invented seed."""
    preview,g1_bundle,_=p6.preview_native_parent_verified(
        P6BRANCH,'hhl-20261116-p9seed-probe',require_verified_gen1=True)
    if (preview['selected_parent_id']!=GEN1 or preview['selected_generation']!=1
        or preview['parent_tq']!=3582112):
        raise Stop('P6 Gen1 verified branch mismatch')
    archives={}
    for pid in (GEN0,GEN1):
        raw=p6.private_json(P6BRANCH/'bundles',pid+'.json',max_bytes=3_000_000)
        source=p6.p4._decode_source_record(raw) if pid==GEN0 else p6.read_child_archive(raw)
        if source.sha256!=pid:raise Stop('seed source identity mismatch')
        archives[pid]=(raw,source)
    if archives[GEN1][1]!=g1_bundle:raise Stop('P6 Gen1 source drift')
    return archives

def init(root:Path, archives):
    p6.private_dir(RECORDS)
    safe_branch_parent=RECORDS
    p6.private_dir(safe_branch_parent)
    old=os.umask(0o077)
    try:
        mkdir(root)
        for name in ('db','bundles','sessions','receipts'):mkdir(root/name)
        for pid,(raw,_) in archives.items():
            store(root/'bundles'/(pid+'.json'),raw)
        original=load_database_with_verified_stats(p6.p4._config(P6BRANCH/'db'),ProgramDatabase)
        db=ProgramDatabase(p6.p4._config(root/'db'))
        for i,pid in enumerate((GEN0,GEN1)):
            prog=original.get(pid)
            if prog is None or prog.code!=archives[pid][1].content()[archives[pid][1].entry].decode('utf8'):
                raise Stop('P6 native seed program does not match archive')
            if db.add(prog,iteration=i) is None:raise Stop('native DB declined known P6 seed')
        db.save(str(root/'db'),iteration=1)
        verify=load_database_with_verified_stats(p6.p4._config(root/'db'),ProgramDatabase)
        if any(verify.get(pid) is None for pid in (GEN0,GEN1)):
            raise Stop('native DB could not restore two seed programs')
        state={'schema':NEW_SCHEMA,'source':'P6_VERIFIED_GEN0_GEN1','created_utc':int(time.time()),
               'iteration':0,'accepted':0,'candidate_ids':[],
               'sessions':[],'families':{'LINEAGE_GEN1':[GEN1],
                                        'INDEPENDENT_A':[],'INDEPENDENT_B':[]},
               'prior_findings':[],'best_tq':3582112,
               'known_ops':[archives[GEN1][0]['ops_sha256'],archives[GEN0][0]['ops_sha256']],
               'native_db':'OpenEvolve_0.4.0','source_provenance':'P1_BROKER_AT_EVAL',
               'limitations':['native benchmark sampling is not exhaustive mathematical proof']}
        store(root/'state.json',state)
        return state
    finally:os.umask(old)

def load_branch(root:Path):
    safe_branch(root)
    state=p6.private_json(root,'state.json',max_bytes=3_000_000)
    if state.get('schema')!=NEW_SCHEMA:raise Stop('branch state schema mismatch')
    db=load_database_with_verified_stats(p6.p4._config(root/'db'),ProgramDatabase)
    for pid in (GEN0,GEN1):
        if db.get(pid) is None:raise Stop('historic seed missing in native DB')
    return state,db

def source_archive(root:Path,pid:str):
    if not ID_RE.fullmatch(pid):raise Stop('malformed program ID')
    row=p6.private_json(root/'bundles',pid+'.json',max_bytes=3_000_000)
    if pid==GEN0:return p6.p4._decode_source_record(row),row
    if pid==GEN1:return p6.read_child_archive(row),row
    if row.get('schema')!='hhl-p9-native-source-v1' or row.get('program_id')!=pid:
        raise Stop('unrecognized P9 source archive')
    if row.get('source_fingerprint')!=p6.path_independent_fingerprint(row['files_b64']):
        raise Stop('P9 source bytes drift')
    blob={k:base64.b64decode(v,validate=True) for k,v in row['files_b64'].items()}
    lib=blob.pop('hhl-uncompute-author.zip')
    src=p6.evolution.freeze_actual_bundle(row['entry'],blob,lib)
    if src.sha256!=pid or src.entry_sha256!=row['entry_sha256']:
        raise Stop('P9 source identity drift')
    return src,row

def pareto_candidates(progs):
    """Avoid discarding space/time trade-offs: keep nondominated T/Q programs."""
    out=[]
    for p in progs:
        t,q=p.metrics.get('toffoli'),p.metrics.get('qubits')
        if not isinstance(t,(float,int)) or not isinstance(q,(float,int)):continue
        if not any((other.metrics.get('toffoli',float('inf'))<=t and
                    other.metrics.get('qubits',float('inf'))<=q and
                    (other.metrics.get('toffoli',float('inf'))<t or
                     other.metrics.get('qubits',float('inf'))<q)) for other in progs):
            out.append(p)
    return sorted(out,key=lambda p:(p.metrics['tq_cost'],p.id))

def choose_parent(root:Path,state:dict,db,family:str,priority_parent_id:str|None=None):
    """Use native retained members of the family, not only Pareto points.

    Prefer latest native generation for genuine Gen0 -> Gen1 -> Gen2, while
    retaining the rest of the MAP-Elites archive for inspiration and later search.
    """
    if family not in FAMILIES:raise Stop('unknown family')
    ids=state['families'][family]
    if family.startswith('INDEPENDENT_') and not ids:
        return {'kind':'IDEATION_BLANK','family':family,'pid':GEN0,
                'parent_id':None,'generation':0,'T':14378,'Q':290,'TQ':4169620,
                'selection':'FROM_SPEC_BLANK_SCAFFOLD'}
    pool=[db.get(pid) for pid in ids]
    pool=[p for p in pool if p is not None]
    if not pool:raise Stop('family has no retained native OpenEvolve programs')
    if priority_parent_id is not None:
        selected=db.get(priority_parent_id)
        if selected is None or selected.id not in {p.id for p in pool}:
            raise Stop('requested parent absent from retained family archive')
        selection='P11_EXPLICIT_NATIVE_PARENT'
    else:
        max_generation=max(int(p.generation) for p in pool)
        # Build genuine generations first; after Gen3 periodically reopen
        # older MAP-Elites cells so early novel seeds remain evolvable.
        eligible=(pool if max_generation>=3 and state['iteration']%5==3
                  else [p for p in pool if int(p.generation)==max_generation])
        sampled=None
        try:
            sampled,_=db.sample(num_inspirations=min(3,max(0,len(db.programs)-1)))
        except (ValueError,RuntimeError,LookupError):pass
        selected,selection=p11.parent_choice(eligible,iteration=state['iteration'],sampled=sampled)
    src,rec=source_archive(root,selected.id)
    if selected.code!=src.content()[src.entry].decode('utf8'):
        raise Stop('selected native parent entry differs from source archive')
    return {'kind':'EVOLUTION','family':family,'pid':selected.id,
            'parent_id':selected.id,'generation':int(selected.generation)+1,
            'T':int(selected.metrics['toffoli']),'Q':int(selected.metrics['qubits']),
            'TQ':int(selected.metrics['tq_cost']),'selection':selection}

def p11_context(root:Path,parent:dict,state:dict,db):
    family=parent['family']
    mem=p11.research_memory(root,state,family)
    if parent['kind']=='IDEATION_BLANK':
        draft=p11.newest_unverified_checkpoint(root,state,family)
        info=('A previously frozen UNVERIFIED draft is available and will be staged: '
              +draft['record']+'; this is NOT a native parent. Continue debugging and native evaluation.'
              if draft else 'No working family seed and no reusable verified frozen draft available.')
        return '\nP11 UNVERIFIED FAMILY DRAFT: '+info+'\n'+mem, {'decision':'ideation_blank','draft':draft['record'] if draft else None}
    primary_source,_=source_archive(root,parent['pid'])
    primary={'id':parent['pid'],'family':family,'T':parent['T'],
             'Q':parent['Q'],'TQ':parent['TQ'],'code':p11.get_code(primary_source)}
    alternatives=[]
    for fam,ids in state['families'].items():
        for pid in ids:
            if pid==parent['pid']:continue
            prog=db.get(pid)
            if prog is None:continue
            try:
                src,arc=source_archive(root,pid)
                alternatives.append({'id':pid,'family':fam,'T':int(prog.metrics['toffoli']),
                  'Q':int(prog.metrics['qubits']),'TQ':int(prog.metrics['tq_cost']),
                  'code':p11.get_code(src)})
            except (ValueError,KeyError,OSError,p6.Refuse,Stop):continue
    insp,decision=p11.choose_inspiration(primary,alternatives)
    return p11.overlay(primary,insp,decision,mem),decision


def build_prompt(parent:dict,run_tag:str,stage_plan:dict,state:dict,root:Path,db):
    """Reuse proven C HHL tool/task contract; ideation does not inherit a circuit."""
    family=parent['family']; kind=parent['kind']
    if kind=='IDEATION_BLANK':
        # Direct task/spec ideation, not misrepresented as verified-parent mutation.
        base=session.build_verified_seed_plan(run_tag)
        text=base.prompt_bytes.decode('utf8')
        old=('You start with the ORIGINAL working Haener baseline (T=16468, '
             'Q=300, T×Q=4940400); it is not the published discovered optimum.')
        if text.count(old)!=1:raise Stop('base task prompt changed')
        text=text.replace(old,'You begin from a BLANK quantum circuit implementation. '
                          'The HHL task is specified but NO previous circuit is supplied. '
                          'Design and implement a new algorithm architecture independently.')
        new_entry=stage_plan['target_entry']
        allowed={'main.py','circuit.py','constants.json','meta.json','contract.json','grader.py'}
        paths=[x['new_path'] for x in stage_plan['source_files'] if x['name'] in allowed]
        if len(paths)!=6:raise Stop('blank ideation scaffold incomplete')
        invocation=json.dumps({'entry':new_entry,'files':['hhl-uncompute-author.zip']+paths},separators=(',',':'))
        text,num=re.subn(r'The initial working call is research_python\(\{[^\n]*\}\)\. ',
                         lambda _:('After you IMPLEMENT a working circuit, use '
                                    'research_python('+invocation+'). '),text,count=1)
        if num!=1:raise Stop('could not replace baseline tool invocation')
        text=text.replace('Call quantum_evaluate with the ACTUAL .ops path in saved_artifacts; '
                          'use its actual failure feedback to revise and test again as time allows. ',
                          'research_python automatically invokes the independent native evaluator '
                          'after producing actual .ops. Inspect the returned native feedback. ')
    else:
        plan=dict(stage_plan)
        plan['research_db_admission']='FORMAL_HHL_REPLAY_VERIFIED'
        base=p3_prompt.build_verified_parent_prompt(run_tag,plan,
                                                    parent_t=parent['T'],parent_q=parent['Q'])
        text=base.prompt_bytes.decode('utf8')
        # Later P9 parents are accepted by P1 source-bound original-paper benchmark,
        # without imposing P6's two-replay extra research gate.
        if parent['pid'] not in (GEN0,GEN1):
            text=text.replace('validated by source-bound native replays',
                              'accepted by protected source-bound native evaluation')
        text+=(f'\nThis iteration uses native OpenEvolve parent Program ID {parent["pid"]} '
               f'from family {family}, generation {parent["generation"]-1}.\n')
    history='\n'.join('- '+str(x)[:320] for x in state['prior_findings'][-6:]) or '- No prior findings yet.'
    extra=('\nP9 AUTOMATED EVOLUTION CONTROLLER (family='+family+'):\n'
           'The current program is YOUR source workspace; the root controller alone '
           'selects parents and adds approved children to OpenEvolve. You cannot edit '
           'the formal DB, Evaluator, Broker, author library or permissions. '
           'Work on actual HHL circuit-generation source, not just a cost estimate. '
           'Use research_python and inspect independent native grading: a successful '
           'Python build is not an accepted circuit. Repair native failures or explore '
           'another arithmetic architecture. Keep a short visible research_log.txt. '
           'The native benchmark is sampled, not mathematical exhaustive proof. '
           'Do not invent results or provenance.\n'
           'LIMITED OBSERVABLE SEARCH MEMORY (may include failed ideas):\n'+history+'\n')
    if kind=='IDEATION_BLANK':
        extra+=(f'This is independent family {family}. circuit.py is a deliberate '
                'non-working stub, and constants.json is empty. Do not copy Gen0/Gen1 '
                'polynomial implementation: derive a distinct implementable datapath '
                'from HHL specification. First valid circuit is family generation 0, '
                'parent_id=None; no prior family design is available.\n')
    research_extra,decision=p11_context(root,parent,state,db)
    combined=(text+extra+p10_candidate_first_for(parent,run_tag)+'\n'+research_extra).encode('utf8')
    manifest=dict(base.manifest)
    manifest.update({'p9_family':family,'p9_kind':kind,'prompt_sha256':sha(combined),
                     'p9_source_bound_native_paper_gate':True,
                     'p10_prompt_rev':P10_PROMPT_REV,'p10_e_appendix_sha256':E_SOURCE_SHA256,
                     'p11_revision':P11_REV,'p11_inspiration_decision':decision})
    return replace(base,prompt_bytes=combined,manifest=manifest,
                   argv=base.argv[:-1]+(combined.decode('utf8'),))

def stage_session(root,parent,state,tag):
    source,_=source_archive(root,parent['pid'])
    gen=max(0,parent['generation']-1)
    plan=p6.p3.rebase_plan(source,parent['pid'],tag,
                            parent_generation=gen,
                            verified_parent_tq=parent['TQ'])
    # Root computes prompt + accepted environment BEFORE creating the agent run.
    prompt=build_prompt(parent,tag,plan,state,root,load_branch(root)[1])
    env=accepted_env_with_key(prompt,os.environ['DEEPSEEK_API_KEY'])
    c_one.agent_run_root(tag,env)
    report=p3_stage.stage_full_parent(source,parent['pid'],tag,SCRATCH,
                      agent_uid=997,agent_gid=988,
                      parent_generation=gen,
                      verified_parent_tq=parent['TQ'])
    if report['materialized_broker_input_sha256']!=plan['materialized_broker_input_sha256']:
        raise Stop('staged source mismatch')
    if parent['kind']=='IDEATION_BLANK':
        folder=SCRATCH/'runs'/tag/'attempt-01'
        (folder/'circuit.py').write_text(
            '"""Independent HHL architecture ideation: no inherited algorithm."""\n'
            'def build():\n    raise NotImplementedError("Design your HHL circuit here")\n')
        (folder/'constants.json').write_text('{}\n')
        # Draft carryover is available ONLY when the family has no native seed.
        # Hash-validated post-session work is staged as UNVERIFIED research,
        # never inserted into native DB unless this session passes native grading.
        draft=p11.newest_unverified_checkpoint(root,state,parent['family'])
        if draft:
            for name,raw in draft['files'].items():
                (folder/name).write_bytes(raw)
        for fn in folder.iterdir():
            if fn.name not in ('main.py','circuit.py','constants.json',
                               'meta.json','contract.json','grader.py') and fn.is_file():
                fn.unlink()
    return plan,prompt,env

def _safe_session_step(root,session_step):
    path=root/'sessions'/f'step-{session_step:03d}'
    if path.exists():  # Previous stopped/failed attempt; retain forensic evidence.
        path=root/'sessions'/f'step-{session_step:03d}-retry-{secrets.token_hex(3)}'
    mkdir(path)
    return path

def run_model(prompt,env,session_secs:int,record:Path,step:int):
    """A new OpenCode session PER evolutionary iteration; continuations stay in it."""
    start=time.monotonic(); deadline=start+session_secs
    calls=0;ses=None;status='time_budget_exhausted';notes=[]
    while calls<12:
        remaining=deadline-time.monotonic()
        if remaining<30:break
        args=['/usr/sbin/runuser','-u','hhl-agent','-g','hhl-agent','--',
              '/srv/hhl-task2/bin/opencode','run','--model',
              'deepseek/deepseek-v4-pro','--agent','build']
        if ses is None:
            args.extend(['--title',prompt.title,prompt.prompt_bytes.decode('utf8')])
        else:
            args.extend(['--session',ses,
                'Continue the SAME Candidate-First HHL research iteration and existing workspace. '
                'FIRST implement and test distinct real Builder-compatible circuit offspring. '
                'Call research_python with entry and files and inspect stderr, exit code, '
                'native verdict and measured T/Q/TQ. Repair failures and retest; '
                'do not restart the candidate or replace circuit work with pure numerical analysis. '
                'Update research_log.txt with concise observable results.'])
        calls+=1; logfile=record/f'opencode-{calls:02d}.log'
        started=time.monotonic()
        try:
            rc=c_one.run_command(args,HHL/'agent',env,max(1,int(remaining)),logfile)
            outcome='normal_stop' if rc==0 else 'model_nonzero_exit'
        except TimeoutError:
            rc=None;outcome='time_budget_exhausted'
        except Exception as exc:
            rc=None;outcome='model_exception_'+type(exc).__name__
        notes.append({'call':calls,'elapsed_s':round(time.monotonic()-started,2),
                      'exit_code':rc,'outcome':outcome,'log':logfile.name})
        store(record/'calls.json',notes)
        if outcome!='normal_stop':status=outcome;break
        if ses is None:
            try:ses=find_unique_active_session(prompt.title,env)
            except Exception as exc:
                status='session_not_recoverable_'+type(exc).__name__;break
        if time.monotonic()>=deadline-30:status='time_budget_exhausted';break
    return {'status':status,'model_calls':calls,
            'elapsed_seconds':round(time.monotonic()-start,2),
            'continuations':notes,'session_id':ses}

def gather_events(before:set,record:Path,input_fp:str,known_ops:set):
    """Uses native source-at-evaluation protected Broker/Evaluator, not post-hoc worktree."""
    after=snapshot_ids(EVAL_SPOOL)
    ids=sorted(after-before)
    if len(ids)>MAX_EVAL_EVENTS:raise Stop('unbounded native event count')
    observations=[];accepted=[];seen_ops=set()
    for eid in ids:
        row={'event_id':eid,'status':'UNVERIFIED'}
        try:
            evraw,ev,ops=binding.root_read_claim_event(
                eid,directory=EVAL_SPOOL,owner_uid=pwd.getpwnam('hhl-eval').pw_uid)
            bid=ev['source_claim']['broker_run_id']
            brraw,br=journal.root_read_record(bid,directory=BROKER_SPOOL,runner_uid=995)
            proof=binding.audit_bound_pair(evraw,brraw,broker_validator=journal.validate_record)
            if proof['status']!='BROKER_SOURCE_CLAIM_CORRELATED':
                raise Stop('original event not correlated to frozen builder source')
            p6.p4._new_file(record,f'event-{eid}-broker.json',brraw)
            p6.p4._new_file(record,f'event-{eid}-evaluator.json',evraw)
            row['source_bound']=True
            row['verdict']=ev.get('result',{}).get('verdict','MISSING')
            row['ops_sha256']=sha(ops)
            row['broker_run_id']=bid
            if br.get('source_bundle_sha256') is None:raise Stop('Broker missing source')
            source=p6.source_from_broker(br)
            fingerprint=p6.path_independent_fingerprint(br['files_b64'])
            if fingerprint==input_fp:row['status']='UNCHANGED_PARENT';observations.append(row);continue
            if row['ops_sha256'] in known_ops or row['ops_sha256'] in seen_ops:
                row['status']='DUPLICATE_CIRCUIT';observations.append(row);continue
            # Native evaluator is sole acceptance ground truth; T/Q/TQ are exact receipts.
            t,q,tq=p6.metric_tuple(ev)
            if sha(ops)!=row['ops_sha256'] or source.sha256!=br['source_bundle_sha256']:
                raise Stop('native artifact digest mismatch')
            seen_ops.add(row['ops_sha256'])
            row.update({'status':'SOURCE_BOUND_NATIVE_OK','T':t,'Q':q,'TQ':tq,
                        'source_bundle_sha256':source.sha256})
            accepted.append((row,source,br))
        except (OSError,ValueError,KeyError,TypeError,Stop,p6.Refuse) as exc:
            row['status']='REJECTED_'+type(exc).__name__
            row['reason']=str(exc)[:150]
        observations.append(row)
    return observations,accepted

def add_child(root,state,parent,record,observed,items):
    """Real native OpenEvolve admission and immediate persist/reload for each accepted source."""
    db=load_database_with_verified_stats(p6.p4._config(root/'db'),ProgramDatabase)
    inserted=[]
    for row,source,br in sorted(items,key=lambda x:(x[0]['TQ'],x[0]['Q'])):
        pid=source.sha256
        if pid in (GEN0,GEN1) or db.get(pid) is not None or (root/'bundles'/(pid+'.json')).exists():
            row['db_admission']='DUPLICATE_PROGRAM';continue
        family=parent['family']
        ancestry=parent['parent_id'] if parent['kind']=='EVOLUTION' else None
        gen=parent['generation']
        prog=Program(id=pid,code=source.content()[source.entry].decode('utf8'),
                     language='python',generation=gen,parent_id=ancestry,
                     changes_description='P9 DeepSeek autonomous protected native HHL candidate',
                     metrics={'combined_score':BASELINE/row['TQ'],
                              'toffoli':float(row['T']),'qubits':float(row['Q']),
                              'tq_cost':float(row['TQ'])},
                     metadata={'task':'hhl-angle','bundle_sha256':pid,
                               'entry_sha256':source.entry_sha256,
                               'ops_sha256':row['ops_sha256'],
                               'source_at_evaluation':'BROKER_SOURCE_CLAIM_CORRELATED',
                               'native_benchmark_valid':True,
                               'paper_sampling_not_universal_proof':True,
                               'event_id':row['event_id'],'broker_id':row['broker_run_id'],
                               'family_id':family,'parent_id':ancestry,
                               'research_admission':'P9_SOURCE_BOUND_NATIVE_OK'})
        if db.add(prog,iteration=state['iteration']+2) is None:
            row['db_admission']='NATIVE_MAP_ELITES_NOT_RETAINED';continue
        archive={'schema':'hhl-p9-native-source-v1','task':'hhl-angle',
                 'program_id':pid,'entry':source.entry,'entry_sha256':source.entry_sha256,
                 'source_fingerprint':p6.path_independent_fingerprint(br['files_b64']),
                 'files_b64':br['files_b64'],'ops_sha256':row['ops_sha256'],
                 'family_id':family,'parent_id':ancestry,'generation':gen,
                 'T':row['T'],'Q':row['Q'],'TQ':row['TQ'],
                 'evaluator_event_id':row['event_id'],'source_record':record.name,
                 'human_modified':False,'independent_replays':0}
        store(root/'bundles'/(pid+'.json'),archive)
        db.save(str(root/'db'),iteration=state['iteration']+2)
        restored=load_database_with_verified_stats(p6.p4._config(root/'db'),ProgramDatabase)
        proof=restored.get(pid)
        if (proof is None or proof.parent_id!=ancestry or proof.generation!=gen or
            proof.metrics['tq_cost']!=float(row['TQ']) or proof.code!=prog.code):
            raise Stop('OpenEvolve did not retain source-bound child after save/reload')
        state['candidate_ids'].append(pid)
        state['families'][family].append(pid)
        state['known_ops'].append(row['ops_sha256'])
        state['accepted']+=1
        state['best_tq']=min(state['best_tq'],row['TQ'])
        row['db_admission']='NATIVE_OPENEVOLVE_SAVE_RELOAD_OK'
        inserted.append({'id':pid,'family':family,'generation':gen,'parent_id':ancestry,
                         'T':row['T'],'Q':row['Q'],'TQ':row['TQ'],
                         'event_id':row['event_id']})
        store(root/'state.json',state)
        print('P9_ACCEPTED_CHILD='+json.dumps(inserted[-1],sort_keys=True),flush=True)
    return inserted

def read_visible_diary(record:Path):
    """Only brief user-visible research notes, never model hidden reasoning."""
    frozen=record/'frozen-after-session'
    manifest=frozen/'manifest.json'
    if not manifest.is_file():return None
    try:
        data=json.loads(manifest.read_text())
        for entry in data.get('files',[]):
            if entry.get('name','').endswith('/research_log.txt'):
                filename=entry['stored_as']
                if not re.fullmatch(r'[0-9a-f]{64}\.source',filename):return None
                raw=(frozen/filename).read_bytes()
                if sha(raw)!=entry['sha256']:return None
                content=raw.decode('utf8','replace').strip()
                return ' '.join(content[-1000:].split())[:650] if content else None
    except (OSError,ValueError,KeyError,TypeError):
        return None
    return None

def export_progress(root:Path,state:dict):
    """Compact, nonsecret status in the SSH user's home; no private source code."""
    public={'schema':NEW_SCHEMA,'branch':str(root),'iterations_completed':state['iteration'],
            'valid_native_children':state['accepted'],'best_tq':state['best_tq'],
            'families':state['families'],'sessions':state['sessions'][-12:],
            'milestone_gen3_native':False,'model_calls':sum(x.get('model_calls',0)
                         for x in state['sessions'])}
    for pid in state['candidate_ids']:
        try:
            d=p6.private_json(root/'bundles',pid+'.json',max_bytes=3_000_000)
            if d.get('generation',-1)>=3:public['milestone_gen3_native']=True
        except (OSError,ValueError):pass
    dest=Path('/home/researcher/HHL_P11_progress.json')
    store(dest,public)
    owner=pwd.getpwnam('nylue2014')
    os.chown(dest,owner.pw_uid,owner.pw_gid)

def run_once(root,state,session_secs):
    state,db=load_branch(root)
    family=p11.family_round(state)
    priority=None
    parent=choose_parent(root,state,db,family,priority_parent_id=priority)
    if priority is not None:
        print('P10_NEXT_GENERATION_PRIORITY='+json.dumps(
              {'parent_id':priority,'parent_generation':parent['generation']-1,
               'target_generation':parent['generation']},sort_keys=True),flush=True)
    stepno=state['iteration']+1
    tag='hhl-'+time.strftime('%Y%m%d',time.gmtime())+'-p9-'+secrets.token_hex(4)
    record=_safe_session_step(root,stepno)
    store(record/'start.json',{'schema':NEW_SCHEMA,'run_tag':tag,'parent':parent,
                              'session_budget_seconds':session_secs,
                              'authoritative_source':'P1_PROTECTED_AT_EVALUATION'})
    scratch_env=None
    try:
        plan,prompt,env=stage_session(root,parent,state,tag)
        # The source fingerprint is independent of staging path: no-op detection.
        source,_=source_archive(root,parent['pid'])
        input_fp=p6.path_independent_fingerprint({
            name:base64.b64encode(raw).decode() for name,raw in source.content().items()})
        if parent['kind']=='IDEATION_BLANK':
            # A blank stub is explicitly not an accepted parent or a valid circuit.
            input_fp='IDEATION_EMPTY_SOURCE_NO_PARENT'
        store(record/'prompt.txt',prompt.prompt_bytes)
        store(record/'prompt_revision.json',{'revision':P10_PROMPT_REV,
              'e_original_appendix_sha256':E_SOURCE_SHA256,
              'actual_prompt_sha256':sha(prompt.prompt_bytes),
              'parent_id':parent['parent_id'],'family':family,'p11_revision':P11_REV})
        before=snapshot_ids(EVAL_SPOOL)
        print('P9_SESSION_START='+json.dumps({'step':stepno,'family':family,
              'parent_generation':parent['generation']-1 if parent['kind']=='EVOLUTION' else None,
              'target_generation':parent['generation'],'mode':parent['kind']},sort_keys=True),flush=True)
        result=run_model(prompt,env,session_secs,record,stepno)
        try:
            result['post_session_capture']=c_one.salvage_sources(tag,record)
        except Exception as e:
            result['post_session_capture']={'error':type(e).__name__}
        # Only native Evaluator events that occurred DURING this session count.
        observations,items=gather_events(before,record,input_fp,set(state['known_ops']))
        state,_=load_branch(root)
        children=add_child(root,state,parent,record,observations,items)
        result.update({'run_tag':tag,'parent':parent,'evaluations':observations,
                       'children_admitted':children,'native_events':len(observations)})
        store(record/'result.json',result)
        state['sessions'].append({'step':stepno,'family':family,'parent_id':parent['parent_id'],
                                  'child_generation':parent['generation'],
                                  'run_tag':tag,'status':result['status'],
                                  'model_calls':result['model_calls'],
                                  'native_events':len(observations),
                                  'admitted':len(children),'record':record.name})
        findings=[f'{family} step {stepno}: {len(observations)} native events, '
                  f'{len(children)} accepted.']
        for ev in observations[-3:]:
            findings.append(f"event {ev.get('status')}: {ev.get('verdict','unknown')} "
                            f"TQ {ev.get('TQ','N/A')}")
        diary=read_visible_diary(record)
        if diary:
            findings.append('VISIBLE_RESEARCH_DIARY: '+diary)
        # Only observable decisions; never reuse any hidden model reasoning.
        state['prior_findings']=(state['prior_findings']+findings)[-15:]
        state['iteration']+=1
        store(root/'state.json',state)
        export_progress(root,state)
        current_family_gens=[p6.private_json(root/'bundles',pid+'.json',max_bytes=3_000_000)['generation']
                             for pid in state['families'][family]]
        return {'step':stepno,'family':family,'status':result['status'],
                'events':len(observations),'accepted':len(children),
                'best_tq':state['best_tq'],
                'generation_attempted':parent['generation'],
                'admitted_generations':sorted(set(x['generation'] for x in children)),
                'next_generation':max(current_family_gens)+1 if current_family_gens else 0}
    except Exception as e:
        store(record/'failure.json',{'type':type(e).__name__,'reason':str(e)[:400]})
        raise

def execute(iterations:int,session_secs:int,resume_branch:str|None=None):
    if os.geteuid()!=0:raise Stop('root required')
    if not 1<=iterations<=12 or not 180<=session_secs<=2400:
        raise Stop('bounded evolution budget required')
    ensure_tree()
    if not 22<=len(os.environ.get('DEEPSEEK_API_KEY',''))<=256:
        raise Stop('approved ephemeral provider key required')
    lock=p6.acquire_session_lock()
    old=os.umask(0o077)
    root=None
    staged=False
    builder_files={f'uncompute/{name}':(C/'builder'/name).read_bytes()
                   for name in ('builder.py','compact.py','ops.py')}
    try:
        if resume_branch:
            if not re.fullmatch(r'HHL_P9_EVOLUTION_[a-z0-9]{8}',resume_branch):
                raise Stop('resume requires exact P9 branch name')
            if resume_branch=='HHL_P9_EVOLUTION_78ca6975':
                raise Stop('P11 will never mutate original frozen R3')
            root=RECORDS/resume_branch
            marker=p6.private_json(root,'P11_FORK_FROM_R3.json',max_bytes=5000)
            if (marker.get('schema')!='p11-frozen-fork-v1' or
                marker.get('source_branch')!='HHL_P9_EVOLUTION_78ca6975'):
                raise Stop('P11 refuses to run on R3 original or unmarked branch')
            init_state,_=load_branch(root)
            print('P11_RESUMED_ISOLATED_FORK='+str(root),flush=True)
        else:
            raise Stop('P11 requires forked R3 branch retaining the 10 valid Gen0 seeds; refuse blank reset')
        export_progress(root,init_state)
        # Existing P7 Stage sets up narrow Broker/Evaluator service sockets.
        builder.stage_builder(SCRATCH,builder_files,expected_scratch_uid=997)
        seed.stage_library(SCRATCH,agent_uid=997)
        staged=True
        results=[]
        for i in range(iterations):
            state,_=load_branch(root)
            out=run_once(root,state,session_secs)
            results.append(out)
            print('P9_ITERATION='+json.dumps(out,sort_keys=True),flush=True)
        state,_=load_branch(root)
        overall={'schema':NEW_SCHEMA,'status':'BOUNDED_AUTONOMOUS_EVOLUTION_COMPLETE',
                 'branch':str(root),'iterations':state['iteration'],
                 'native_admitted_children':state['accepted'],
                 'best_tq':state['best_tq'],
                 'lineages':state['families'],'steps':results,
                 'model_calls':sum(s['model_calls'] for s in state['sessions']),
                 'paid_sessions':len(state['sessions']),
                 'gen3_observed':any(x.get('generation',0)>=3 for x in (
                     p6.private_json(root/'bundles',pid+'.json')
                     for pid in state['candidate_ids']))}
        store(root/'result.json',overall)
        export_progress(root,state)
        print('P9_FINAL='+json.dumps(overall,sort_keys=True),flush=True)
        return overall
    finally:
        if staged:
            seed.cleanup_library(SCRATCH)
            builder.cleanup_builder(SCRATCH,builder_files)
        os.umask(old)
        os.close(lock)

def plan():
    archives=get_seed_archives()
    return {'schema':NEW_SCHEMA,'mode':'PREFLIGHT_NO_MODEL_NO_DB_WRITES',
            'verified_seed_ids':[GEN0,GEN1],
            'seed_tqs':[4169620,3582112],
            'families':list(FAMILIES),'schedule':list(SCHEDULE),
            'model':'deepseek/deepseek-v4-pro',
            'native_score_contract':'P1_PROTECTED_BROKER_EVALUATOR_VERDICT_OK',
            'parent_selection':'native OpenEvolve archive latest-generation with diversified inspiration',
            'next_generation':'parent.generation+1; independent family starts at 0',
            'manual_gen2_relaunch':False,'model_calls':0,'db_writes':0,
            'prompt_revision':P10_PROMPT_REV,
            'e_original_appendix_sha256':E_SOURCE_SHA256,
            'gen3_priority_after_accepted_gen2':True,
            'p11_revision':P11_REV,
            'p11_family_schedule':list(p11.SEQUENCE),
            'p11_memory':'frozen-visible-session-diary, fact-labelled',
            'p11_inspiration':'AST-diverse native source from archive',
            'requires_fork':'P11_FORK_FROM_R3.json',
            'limitations':['native validation uses benchmark sampling',
                           'real multi-family discovery depends on model output']}

def p11_check_native_fork(branch:str):
    if branch=='HHL_P9_EVOLUTION_78ca6975' or not re.fullmatch(
        r'HHL_P9_EVOLUTION_[a-z0-9]{8}',branch):
        raise Stop('P11 fork verification requires non-R3 fork name')
    root=RECORDS/branch
    marker=p6.private_json(root,'P11_FORK_FROM_R3.json',max_bytes=5000)
    if marker.get('schema')!='p11-frozen-fork-v1' or marker.get('source_branch')!='HHL_P9_EVOLUTION_78ca6975':
        raise Stop('invalid P11 fork provenance marker')
    state,db=load_branch(root)
    for family,count in [('INDEPENDENT_A',6),('INDEPENDENT_B',4)]:
        ids=state['families'][family]
        if len(ids)<count:raise Stop('original R3 accepted seed missing from fork')
        for pid in ids:
            p=db.get(pid)
            src,row=source_archive(root,pid)
            if (p is None or p.code!=src.content()[src.entry].decode('utf8')
                or float(p.metrics['tq_cost'])!=float(row['TQ'])
                or int(p.generation)!=int(row['generation'])):
                raise Stop('native DB vs source-bound bundle mismatch '+pid[:12])
    if state['iteration']<8 or state['best_tq']>1012552:
        raise Stop('R3 scientific baseline missing in fork')
    print('P11_NATIVE_FORK_SOURCE_AND_DB_CHECK=PASS')
    print('P11_FORK_BRANCH='+branch)
    print('P11_FORK_NATIVE_A='+str(len(state['families']['INDEPENDENT_A'])))
    print('P11_FORK_NATIVE_B='+str(len(state['families']['INDEPENDENT_B'])))
    print('P11_FORK_BEST_TQ='+str(state['best_tq']))
    print('P11_EVALUATOR_MODEL_CALLS=0')
    return 0

def main():
    ap=argparse.ArgumentParser()
    group=ap.add_mutually_exclusive_group(required=True)
    group.add_argument('--plan',action='store_true')
    group.add_argument('--run',action='store_true')
    group.add_argument('--check-fork',action='store_true')
    ap.add_argument('--authorize-paid-evolution',action='store_true')
    ap.add_argument('--iterations',type=int,default=4)
    ap.add_argument('--session-seconds',type=int,default=900)
    ap.add_argument('--resume-branch',default=None)
    args=ap.parse_args()
    try:
        if bool(args.run)!=bool(args.authorize_paid_evolution):
            raise Stop('explicit paid authorization required for run, forbidden for plan/check-fork')
        if args.check_fork:
            if not args.resume_branch:raise Stop('check-fork requires --resume-branch')
            return p11_check_native_fork(args.resume_branch)
        if args.plan:
            if args.resume_branch:raise Stop('plan cannot select a mutable branch')
            print(json.dumps(plan(),ensure_ascii=False,sort_keys=True))
        else:
            print(json.dumps(execute(args.iterations,args.session_seconds,args.resume_branch),ensure_ascii=False,sort_keys=True))
        return 0
    except (Stop,p6.Refuse,OSError,KeyError,ValueError,TypeError,RuntimeError) as e:
        print('P9_STOP='+type(e).__name__+':'+str(e)[:250],file=sys.stderr)
        return 2

if __name__=='__main__':raise SystemExit(main())
