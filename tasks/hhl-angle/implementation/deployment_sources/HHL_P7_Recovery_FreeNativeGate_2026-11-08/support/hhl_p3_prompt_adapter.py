#!/usr/bin/env python3
"""C's existing OpenCode plan, with FULL validated parent instead of Baseline.

Review-only compiler; building this prompt never authorizes a paid request.
Requires the root operator to attest that the complete new attempt worktree
was staged using stage_full_parent and that P1 native evidence is still valid.
"""
from __future__ import annotations
from dataclasses import replace
import hashlib
import json

from hhl_p3_parent_adapter import Stop


def build_verified_parent_prompt(run_tag: str, stage_plan: dict, *, parent_t: int,
                                 parent_q: int):
    # Prefer C's actually successful, detailed HHL prompt and its identical
    # OpenCode model/environment contract over a newly invented prompt.
    from hhl_session_plan import build_verified_seed_plan
    if (type(stage_plan) is not dict or
        stage_plan.get('run_tag') != run_tag or
        stage_plan.get('schema') != 'hhl-p3-next-generation-parent-plan-v1' or
        stage_plan.get('research_db_admission') not in ('TRIAL_ONLY', 'FORMAL_HHL_GEN0_P1_NEW_REPLAY_ONLY',
                                                         'FORMAL_HHL_REPLAY_VERIFIED') or
        stage_plan.get('source_bytes_identical') is not True or
        stage_plan.get('materialized_is_new_offspring') is not False or
        stage_plan.get('full_source_file_count',0)<6 or
        type(parent_t) is not int or type(parent_q) is not int or
        parent_t <= 0 or parent_q <= 0):
        raise Stop('unverified or inconsistent parent prompt plan')
    parent_tq = parent_t * parent_q
    known_tq = stage_plan.get('verified_parent_tq')
    if known_tq is None and stage_plan.get('parent_program_generation') == 0:
        known_tq = 4_169_620  # Legacy P3 trial compatibility; cannot authorize gen1+.
    if (type(known_tq) is not int or parent_tq != known_tq or
        (stage_plan.get('parent_program_generation', 0) > 0 and
         stage_plan.get('research_db_admission') == 'TRIAL_ONLY')):
        raise Stop('parent TQ not verified against the native DB')
    base = build_verified_seed_plan(run_tag)  # C's actual proven seeded plan
    files=['hhl-uncompute-author.zip']+[row['new_path'] for row in stage_plan['source_files']]
    broker_entry=stage_plan['target_entry']
    if broker_entry not in files or len(files)!=stage_plan['full_source_file_count']+1:
        raise Stop('incomplete broker files for full parent')
    invocation=json.dumps({'entry':broker_entry,'files':files},separators=(',',':'))
    old = ('You start with the ORIGINAL working Haener baseline (T=16468, '
           'Q=300, T×Q=4940400); it is not the published discovered optimum.')
    new = (f'You start with an independently native-verified HHL parent '
           f'(T={parent_t}, Q={parent_q}, T×Q={parent_tq}). This parent was '
           'validated by source-bound native replays; historical P0 source '
           'attribution remains NOT_PROVEN. It is not the published optimum. '
           f'Exact parent Program ID: {stage_plan["parent_original_program_id"]}.')
    text=base.prompt_bytes.decode('utf-8')
    if text.count(old)!=1:
        raise Stop('C successful seeded prompt changed unexpectedly')
    text=text.replace(old,new)
    import re
    pattern=r'The initial working call is research_python\(\{[^\n]*\}\)\. '
    if len(re.findall(pattern,text))!=1:
        raise Stop('C original research_python instruction changed')
    text=re.sub(pattern,lambda _: 'The initial working call is research_python('+invocation+'). ',text,count=1)
    if 'T=16468' in text or 'T×Q=4940400' in text:
        raise Stop('Original Baseline mistaken for current verified parent')
    legacy=('Call quantum_evaluate with the ACTUAL .ops path in saved_artifacts; '
            'use its actual failure feedback to revise and test again as time allows. ')
    revised=('research_python automatically submits a successfully built .ops to the '
             'native evaluator; inspect auto_native_evaluations for actual grading '
             'feedback, revise the circuit and rerun as time allows. ')
    if text.count(legacy)!=1:
        raise Stop('C original evaluation instruction changed unexpectedly')
    text=text.replace(legacy,revised)
    text += ('\nThis parent comes from a FULL immutable multi-file bundle. '
             'The staged copy is editable and has a DIFFERENT path-dependent bundle hash; '
             'it is not a newly discovered candidate by itself. '
             'Preserve and modify all necessary source files rather than merely main.py.\n')
    raw=text.encode('utf-8')
    manifest=dict(base.manifest)
    manifest.update({'phase':'evolution','arm':'verified_parent',
                     'handoff_version':'hhl-p3-full-parent-v1',
                     'parent_program_id':stage_plan['parent_original_program_id'],
                     'materialized_input_sha256':stage_plan['materialized_broker_input_sha256'],
                     'parent_tq':parent_tq,
                     'parent_generation':stage_plan.get('parent_program_generation',0),
                     'prompt_sha256':hashlib.sha256(raw).hexdigest(),
                     'prompt_bytes':len(raw),
                     'paid_authorization':False,'model_session_executed':False,
                     'parent_staging_proof_required':True,
                     'formal_db_admission':stage_plan.get('research_db_admission'),
                     'source_bound_evaluator_required':True})
    return replace(base,prompt_bytes=raw,manifest=manifest,
                   argv=base.argv[:-1]+(text,))
