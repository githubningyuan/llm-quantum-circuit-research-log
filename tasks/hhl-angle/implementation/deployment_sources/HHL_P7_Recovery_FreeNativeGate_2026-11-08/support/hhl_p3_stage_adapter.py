#!/usr/bin/env python3
"""C's existing seed-stage primitives, adapted for a P1-vetted full HHL parent.

Called ONLY by a trusted root operator after DB/evidence/lineage approval.
Uses C hhl_seed_stage._dir/_regular rather than reimplementing its file rules.
No agent, evaluator, database or API calls here.
"""
from __future__ import annotations
import os
from pathlib import Path
import stat
import hashlib
from hhl_p3_parent_adapter import rebase_plan, Stop
import hhl_evolution_bridge as evol


def stage_full_parent(parent_bundle: evol.SourceBundle, source_program_id: str,
                      run_tag: str, scratch: Path, *, agent_uid: int = 997,
                      agent_gid: int = 988, parent_generation: int = 0,
                      verified_parent_tq: int | None = None) -> dict:
    """Create ONE new attempt-01 atomically per file, without overwriting anything.

    A failed stage is QUARANTINED (left for a root operator), not silently
    deleted. The operator must not launch OpenCode when this function fails.
    """
    import hhl_seed_stage as c_seed  # Existing, VM-pinned C module.
    if os.geteuid() != 0:
        raise PermissionError('root required')
    if not os.path.ismount(scratch):
        raise Stop('scratch is not mounted')
    info = rebase_plan(parent_bundle, source_program_id, run_tag,
                       parent_generation=parent_generation,verified_parent_tq=verified_parent_tq)
    if info['ready_for_paid_model'] is not False:
        raise Stop('unsafe trial admission state')
    old_prefix = parent_bundle.entry[:-len('main.py')]
    content = parent_bundle.content()
    files_by_name = {p[len(old_prefix):]:v for p,v in content.items() if p != 'hhl-uncompute-author.zip'}
    # Parent is the *full* candidate worktree; the ZIP is staged separately by
    # C's already-tested stage_library(), remaining root-owned read-only.
    root = os.open(scratch, os.O_DIRECTORY|os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        st = os.fstat(root)
        if st.st_uid != agent_uid or stat.S_IMODE(st.st_mode) != 0o700:
            raise PermissionError('unexpected scratch owner/mode')
        runs = c_seed._dir(root,'runs',agent_uid,0o700)
        try:
            run = c_seed._dir(runs,run_tag,agent_uid,0o700)
            try:
                # Forbid staging on top of an existing attempt; retry requires
                # a new run_tag so child lineage never becomes ambiguous.
                os.mkdir('attempt-01',mode=0o700,dir_fd=run)
                at = os.open('attempt-01',os.O_DIRECTORY|os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=run)
                try:
                    os.fchown(at,agent_uid,agent_gid)
                    os.fchmod(at,0o700)
                    for name,raw in sorted(files_by_name.items()):
                        c_seed._regular(at,name,raw,uid=agent_uid,gid=agent_gid,mode=0o600)
                    os.fsync(at)
                    # Re-read through the retained directory fd; only this
                    # exact snapshot is the initial editable child worktree.
                    for name,raw in files_by_name.items():
                        fd = os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=at)
                        try:
                            fst=os.fstat(fd)
                            if not stat.S_ISREG(fst.st_mode) or fst.st_uid != agent_uid or \
                               fst.st_nlink != 1 or stat.S_IMODE(fst.st_mode)!=0o600 or \
                               fst.st_size != len(raw) or hashlib.sha256(os.read(fd,len(raw)+1)).digest() != hashlib.sha256(raw).digest():
                                raise Stop('staged source mismatch')
                        finally:os.close(fd)
                finally:os.close(at)
                os.fsync(run)
            finally:os.close(run)
        finally:os.close(runs)
    finally:os.close(root)
    return dict(info, stage_completed=True, full_source_files_staged=len(files_by_name),
                pending_native_parent_recheck=True,
                immutable_proof_is_not_editable_stage=True)
