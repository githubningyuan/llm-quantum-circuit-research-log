#!/usr/bin/env python3
"""Minimal source-bound evaluator addendum for C's proven HHL OpenCode plan.

The A/P3 verified-parent prompt is authoritative for model behavior and parent
identity; this function modifies only the tool call instructions. It never
starts OpenCode, reads credentials, writes source, or alters a DB.
"""
from __future__ import annotations
from dataclasses import replace
import hashlib

class PromptRefused(ValueError):pass

BOUND_ADDENDUM = (
    "\nThe restricted research_python tool automatically submits the FIRST "
    "successfully built .ops to the protected native HHL evaluator and returns "
    "auto_native_evaluations. Read the native verdict and TQ and use failure "
    "feedback to revise the circuit. Do not confuse a successful Python exit "
    "with a new .ops or a valid circuit. Work on a concrete circuit change early; "
    "avoid prolonged environment diagnostics. The initial parent build is NOT "
    "a newly discovered child. If automatic grading is unavailable, the "
    "manual quantum_evaluate tool requires the ACTUAL saved_artifacts ops path, "
    "broker_run_id equal to research_python broker_provenance_id, and "
    "broker_artifact equal to its exact .ops basename. Never invent IDs or "
    "claim provenance; root independently audits immutable receipts and replays.\n"
)

def append_source_bound_contract(plan):
    """Return a new SessionPlan; reject missing verified P3 parent evidence."""
    if (plan.manifest.get('handoff_version')!='hhl-p3-full-parent-v1' or
        plan.manifest.get('source_bound_evaluator_required') is not True or
        plan.manifest.get('paid_authorization') is not False or
        plan.manifest.get('parent_program_id') is None):
        raise PromptRefused('must begin with audited C/P3 parent plan')
    text=plan.prompt_bytes.decode('utf-8', 'strict')
    if (text.count('The initial working call is research_python(')!=1 or
        'broker_provenance_id' in text or
        len(text)>50000):
        raise PromptRefused('unexpected parent prompt content')
    combined=(text+BOUND_ADDENDUM).encode('utf-8')
    meta=dict(plan.manifest)
    meta.update({'handoff_version':'hhl-p5-source-bound-parent-prompt-v1',
                 'prompt_sha256':hashlib.sha256(combined).hexdigest(),
                 'prompt_bytes':len(combined),
                 'paid_authorization':False,
                 'evaluator_binding_fields':['ops','broker_run_id','broker_artifact'],
                 'require_root_pair_audit_and_two_replays':True})
    if plan.argv[-1] != text:
        raise PromptRefused('C launch argv did not contain the exact source prompt')
    return replace(plan,prompt_bytes=combined,manifest=meta,
                   argv=plan.argv[:-1]+(combined.decode('utf-8'),))
