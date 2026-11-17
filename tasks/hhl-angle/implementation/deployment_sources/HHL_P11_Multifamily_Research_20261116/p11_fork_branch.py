#!/usr/bin/env python3
"""P11: create an isolated copy of the R3 native branch. Never modify R3.

No API calls, evaluator calls, or original DB writes. Explicit --fork writes a
NEW private branch only. Designed for execution as root on the HHL VM.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import sys
from datetime import datetime,timezone

RECORDS=Path('/srv/hhl-task2/research-records')
SOURCE_NAME='HHL_P9_EVOLUTION_78ca6975'
EXPECTED_BEST=1012552
EXPECTED_ACCEPTED=10

def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda:f.read(1<<20),b''):h.update(chunk)
    return h.hexdigest()

def check_orig(src:Path):
    if src.is_symlink() or not src.is_dir():raise ValueError('source branch missing or symlinked')
    d=json.loads((src/'state.json').read_text())
    if (d.get('iteration')!=8 or d.get('accepted')!=EXPECTED_ACCEPTED or
        d.get('best_tq')!=EXPECTED_BEST):
        raise ValueError('R3 source state differs from audited eight-round branch')
    fam=d.get('families',{})
    if len(fam.get('INDEPENDENT_A',[]))!=6 or len(fam.get('INDEPENDENT_B',[]))!=4:
        raise ValueError('R3 A/B seeds changed from audited inventory')
    root_stat=src.stat()
    if not stat.S_ISDIR(root_stat.st_mode) or root_stat.st_uid!=0 or root_stat.st_mode&0o077:
        raise ValueError('source branch is not root-private')
    for pid in fam['INDEPENDENT_A']+fam['INDEPENDENT_B']:
        path=src/'bundles'/f'{pid}.json'
        r=json.loads(path.read_text())
        if r.get('program_id')!=pid or r.get('T',0)*r.get('Q',0)!=r.get('TQ'):
            raise ValueError('candidate bundle missing or metric mismatch')
    return d

def inspect(source):
    files=0;total=0
    for dirpath,dirs,filenames in os.walk(source,followlinks=False):
        for x in dirs:
            p=Path(dirpath)/x
            if p.is_symlink():raise ValueError('symlink directory in source')
        for x in filenames:
            p=Path(dirpath)/x
            if p.is_symlink() or not p.is_file():raise ValueError('unexpected source file')
            files+=1; total+=p.stat().st_size
    if files>30000 or total>750_000_000:raise ValueError('unbounded source tree')
    return files,total

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--inspect',action='store_true')
    ap.add_argument('--fork',action='store_true')
    ap.add_argument('--authorize-fork-write',action='store_true')
    args=ap.parse_args()
    if args.inspect == args.fork:ap.error('select exactly one of --inspect or --fork')
    if args.authorize_fork_write!=args.fork:ap.error('fork requires explicit new-branch write authorization')
    source=RECORDS/SOURCE_NAME
    state=check_orig(source)
    files,total=inspect(source)
    print('P11_SOURCE_R3_READONLY_PASS=YES')
    print('P11_R3_SOURCE_STATE_SHA256='+sha(source/'state.json'))
    print('P11_SOURCE_FILES='+str(files))
    print('P11_SOURCE_BYTES='+str(total))
    if args.inspect:
        print('P11_FORK_WRITES=0')
        return 0
    if os.geteuid()!=0:raise PermissionError('root required for private fork')
    old_umask=os.umask(0o077)
    try:
        name='HHL_P9_EVOLUTION_'+secrets.token_hex(4)
        dest=RECORDS/name
        if dest.exists() or dest.is_symlink():raise FileExistsError('fork collision')
        # copytree never rewrites the old path. Includes native OpenEvolve DB,
        # source bundles, R3 diaries, and every historical result.
        shutil.copytree(source,dest,symlinks=False,copy_function=shutil.copy2)
        os.chmod(dest,0o700)
        for x in ('state.json','db','bundles','sessions'):
            q=dest/x
            if (q.is_dir()!= (source/x).is_dir()):raise ValueError('incomplete copied fork')
        if sha(dest/'state.json')!=sha(source/'state.json'):
            raise ValueError('copy drift of state.json')
        for family in ('INDEPENDENT_A','INDEPENDENT_B'):
            for pid in state['families'][family]:
                if sha(dest/'bundles'/f'{pid}.json')!=sha(source/'bundles'/f'{pid}.json'):
                    raise ValueError('candidate byte mismatch after fork')
        marker={'schema':'p11-frozen-fork-v1','source_branch':SOURCE_NAME,
                'source_state_sha256':sha(source/'state.json'),
                'forked_utc':datetime.now(timezone.utc).isoformat(),
                'p11_revision':'P11_FAMILY_MEMORY_INSPIRATION_R1',
                'source_accepted':EXPECTED_ACCEPTED,'source_best_tq':EXPECTED_BEST,
                'no_original_db_writes':True}
        p=dest/'P11_FORK_FROM_R3.json'
        with p.open('x',encoding='utf8') as fh:
            os.chmod(p,0o600)
            json.dump(marker,fh,ensure_ascii=False,indent=2)
            fh.flush();os.fsync(fh.fileno())
        print('P11_NEW_BRANCH='+name)
        print('P11_NEW_BRANCH_PATH='+str(dest))
        print('P11_R3_PRESERVED=YES')
        print('P11_EVALUATOR_OR_MODEL_CALLS=0')
        return 0
    finally:os.umask(old_umask)

if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as exc:
        print('P11_FORK_STOP='+type(exc).__name__+':'+str(exc)[:250],file=sys.stderr)
        raise SystemExit(2)
