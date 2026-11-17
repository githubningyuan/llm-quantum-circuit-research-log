#!/usr/bin/env python3
"""Create TWO byte-for-byte independent DB copies in a NEW experiment folder.

Preview-only by default. The only supported write mode is --apply; it writes
NEW files under a new experiment root and NEVER changes source DB. Does not
read API keys, execute a circuit, or start DeepSeek/OpenCode.

Run existing v3 --check against original *before* using --apply; run checks
against both copies afterward. Use only while all VM research processes idle.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat


def regular_tree(root: Path):
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError('DB source must be a real directory, not symlink')
    rows=[]
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise RuntimeError('Unexpected symlink inside DB: '+str(path))
        if not (path.is_dir() or path.is_file()):
            raise RuntimeError('Unexpected special file in DB: '+str(path))
        if path.is_file():
            h=hashlib.sha256()
            with path.open('rb') as f:
                for b in iter(lambda:f.read(1<<20),b''):h.update(b)
            rows.append((str(path.relative_to(root)),path.stat().st_size,h.hexdigest()))
    if not rows:
        raise RuntimeError('Empty DB tree')
    return rows


def assert_idle(home: Path):
    marker = home/'quantum-agent/runs/openevolve-opencode/active.json'
    if marker.exists() or marker.is_symlink():
        raise RuntimeError('STOP: unresolved v6.2 active.json')
    # Defensive Linux-only /proc scan, fail closed if unavailable.
    proc=Path('/proc')
    if not proc.is_dir():
        raise RuntimeError('Cannot verify active processes')
    active=[]
    for p in proc.iterdir():
        if not p.name.isdigit():continue
        try:
            args=(p/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace')
            if ('opencode run' in args or 'openevolve_opencode_loop.py --paid-step' in args
                or 'coulomb_batch.py --run' in args):
                active.append((p.name,args[:120]))
        except (OSError,PermissionError):continue
    if active:
        raise RuntimeError('STOP: active experiment process exists: '+str(active[:3]))


def snapshot(home:Path,source:Path,dest:Path,apply:bool):
    home=home.expanduser().resolve();source=source.expanduser().absolute();dest=dest.expanduser().absolute()
    assert_idle(home)
    if source.is_symlink() or not source.is_dir():
        raise RuntimeError('Missing source DB')
    if not dest.parent.is_dir() or dest.parent.is_symlink():
        raise RuntimeError('Create a new experiment parent directory first; no symlinks')
    if dest.exists() or dest.is_symlink():
        raise RuntimeError('Destination exists; this operation never overwrites it')
    for blocked in (home/'quantum-agent', home/'opencode-lab', home/'openevolve-native-check'):
        if dest == blocked or blocked in dest.parents:
            raise RuntimeError('Experiment cannot live inside production directories')
    srcfiles=regular_tree(source)
    report={'source':str(source),'destination':str(dest),'files':len(srcfiles),
            'source_bytes':sum(e[1] for e in srcfiles),
            'mode':'PREVIEW_0_API_NO_WRITES' if not apply else 'CREATED_2_INDEPENDENT_DB_COPIES_0_API'}
    if not apply:return report
    dest.mkdir(mode=0o700)
    for name in ('db-control','db-treatment'):
        target=dest/name
        shutil.copytree(source,target,symlinks=False)
        if regular_tree(target)!=srcfiles:
            raise RuntimeError('Cloned DB does not match original manifest; STOP')
    if regular_tree(source)!=srcfiles:
        raise RuntimeError('Source DB changed during clone; STOP, do not use either copy')
    report['db_control']=str(dest/'db-control')
    report['db_treatment']=str(dest/'db-treatment')
    (dest/'clone_manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return report


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--home',type=Path,default=Path.home())
    ap.add_argument('--source',type=Path,default=Path.home()/'openevolve-native-check/evolution-seeds-v5')
    ap.add_argument('--destination',type=Path,default=Path.home()/'coulomb-ab-v1/db-clones')
    ap.add_argument('--apply',action='store_true',help='Write ONLY new experiment DB copies, never run a model')
    args=ap.parse_args()
    print(json.dumps(snapshot(args.home,args.source,args.destination,args.apply),indent=2,ensure_ascii=False))

if __name__=='__main__':main()
