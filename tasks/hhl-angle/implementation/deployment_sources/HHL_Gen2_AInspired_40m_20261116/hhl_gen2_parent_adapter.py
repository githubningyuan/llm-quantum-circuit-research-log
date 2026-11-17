#!/usr/bin/env python3
"""Read-only, fail-closed P6 Gen1 -> P7 parent adapter.

This is intentionally NOT a model runner and does not stage anything.
Use *only* with P6's source-returning variant, whose native database and
protected source/evidence checks are unchanged.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BRANCH_RE = re.compile(r'HHL_P6_FORMAL_BRANCH_[A-Za-z0-9_]{8}\Z')
ID_RE = re.compile(r'[a-f0-9]{64}\Z')
TAG_RE = re.compile(r'hhl-[a-z0-9][a-z0-9-]{7,56}\Z')

class Refuse(ValueError):
    pass

@dataclass(frozen=True)
class VerifiedParent:
    # Data only. No file writes or API/model calls.
    source: Any
    plan: dict
    program_id: str
    generation: int
    t: int
    q: int
    tq: int
    ops_sha256: str


def prepare_gen2_parent(branch: Path, run_tag: str, p6_module) -> VerifiedParent:
    """Require exact admitted Gen1 from native DB; do not silently use Gen0.

    p6_module.preview_native_parent_verified() must return the verified P6
    preview, its exact selected SourceBundle, and checked source archive.
    It MUST perform the existing P6 private receipt/archive/native DB checks.
    """
    if branch.parent != p6_module.RECORDS or not BRANCH_RE.fullmatch(branch.name):
        raise Refuse('unexpected HHL branch path')
    if not TAG_RE.fullmatch(run_tag):
        raise Refuse('invalid generation run tag')
    # Gen2's experimental parent is the fixed, formally admitted Gen1.
    # P6 still validates the protected archive, event index and native DB.get().
    preview, bundle, archive = p6_module.preview_native_parent_verified(
        branch, run_tag, require_verified_gen1=True)
    if not (preview['schema'] == 'hhl-p6-native-parent-preview-v1'
            and preview['selected_parent_id'] == bundle.sha256
            and preview['staged_to_agent'] is False
            and preview['formal_db_writes'] == 0
            and preview['model_calls'] == 0
            and preview['selected_generation'] == 1
            and preview['next_generation'] == 2):
        raise Refuse('untrusted or non-Gen1 native parent')
    pid = preview['selected_parent_id']
    if not ID_RE.fullmatch(pid) or pid != archive.get('program_id'):
        raise Refuse('Gen1 source identity mismatch')
    t, q, tq = (archive.get('T'), archive.get('Q'), archive.get('TQ'))
    if any(type(n) is not int or n <= 0 for n in (t,q,tq)) or t*q != tq or tq != preview['parent_tq']:
        raise Refuse('native Gen1 resource metrics mismatch')
    ops_sha = archive.get('ops_sha256')
    if not isinstance(ops_sha, str) or not ID_RE.fullmatch(ops_sha):
        raise Refuse('Gen1 ops hash missing')
    if archive.get('generation') != 1 or archive.get('parent_id') != p6_module.GEN0_ID:
        raise Refuse('incorrect verified parent lineage')
    # Rebase via P6's already-tested P3 adapter. It will verify all source bytes.
    plan = p6_module.p3.rebase_plan(bundle, pid, run_tag,
                                    parent_generation=1, verified_parent_tq=tq)
    plan['research_db_admission'] = 'FORMAL_HHL_REPLAY_VERIFIED'
    plan['native_branch_parent_sample'] = 'PASS'
    if (plan['materialized_broker_input_sha256'] != preview['new_materialized_source_sha256']
            or plan['full_source_file_count'] != preview['source_files']
            or plan['parent_original_program_id'] != pid):
        raise Refuse('staging plan differs from native source preview')
    return VerifiedParent(bundle, plan, pid, 1, t, q, tq, ops_sha)
