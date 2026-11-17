#!/usr/bin/env python3
"""Make a NEW, optional test-controller copy from the verified deployed CapFix v2 source.

Strict SHA allowlist, no rewriting originals, no paid operations. Writes only into
an existing EMPTY destination directory. Never modifies the normal v3 controller.
"""
from __future__ import annotations
import argparse
import hashlib
from pathlib import Path
import shutil
import stat

DEPLOYED_V62_SHA = '6aad394d6d581ab972fe1d19747151a6c54691295d655a167c7dd191f8b029b6'
ORIGINAL_CONTROLLER = 'openevolve_opencode_loop.py'


def patch_text(src: str) -> str:
    # Only the new/first OpenCode message gets an addendum; same-session
    # continuation preserves that context and is NOT otherwise modified.
    anchor1 = '            title = "quantum-evolve-" + folder.name\n'
    addition1 = '''            # EXPERIMENTAL PROMPT-ONLY extension in CLONED CONTROLLER ONLY.
            trial_mode = os.environ.get("COULOMB_TRIAL_MODE", "off")
            if trial_mode not in ("off", "memory_inspiration"):
                raise RuntimeError("Invalid COULOMB_TRIAL_MODE; fail closed")
            trial_addendum = ""
            if trial_mode == "memory_inspiration":
                from coulomb_selective_sidecar import runtime_overlay
                memory_path = os.environ.get("COULOMB_TRIAL_MEMORY", "")
                if not memory_path:
                    raise RuntimeError("Missing COULOMB_TRIAL_MEMORY for experiment")
                trial_addendum, trial_decision = runtime_overlay(db, parent, Path(memory_path))
                summary["research_trial"] = {"mode": trial_mode, "decision": trial_decision,
                                             "overlay_bytes": len(trial_addendum.encode("utf-8"))}
            else:
                summary["research_trial"] = {"mode": "control_off", "overlay_bytes": 0}
            private_json(folder / "summary.json", summary)
'''
    anchor2 = '                           "--title", title, prompt(parent_tq)]\n'
    replacement2 = '                           "--title", title, prompt(parent_tq) + trial_addendum]\n'
    if src.count(anchor1) != 1 or src.count(anchor2) != 1:
        raise RuntimeError('Unexpected v6.2 layout; do not auto-patch the controller')
    result = src.replace(anchor1, anchor1 + addition1, 1).replace(anchor2, replacement2, 1)
    compile(result, 'trial_controller_copy.py', 'exec')
    if result.count('trial_addendum') != 4:
        raise RuntimeError('Unexpected overlay hook count')
    return result


def create(srcfile: Path, outdir: Path, sidecar: Path):
    srcfile = srcfile.expanduser().absolute()
    outdir = outdir.expanduser().absolute()
    if srcfile.name != ORIGINAL_CONTROLLER or not srcfile.is_file() or srcfile.is_symlink():
        raise ValueError('Source must be a regular deployed v6.2 controller')
    contents = srcfile.read_bytes()
    got = hashlib.sha256(contents).hexdigest()
    if got != DEPLOYED_V62_SHA:
        raise ValueError('STOP: deployed controller SHA changed; expected '+DEPLOYED_V62_SHA+' but got '+got)
    if not outdir.is_dir() or outdir.is_symlink() or any(outdir.iterdir()):
        raise ValueError('Output must be a separate EXISTING EMPTY, non-symlink directory')
    if srcfile.parent == outdir or srcfile in outdir.parents:
        raise ValueError('Output cannot overlap source')
    adapter = srcfile.parent / 'openevolve_seed_adapter.py'
    if not adapter.is_file() or adapter.is_symlink():
        raise ValueError('Missing original adjacent seed adapter')
    sidecar = sidecar.expanduser().absolute()
    if not sidecar.is_file() or sidecar.is_symlink():
        raise ValueError('Missing sidecar module')
    modified = patch_text(contents.decode('utf-8'))
    # Create without replacing any file; only cloned source has new behavior.
    artifacts = {ORIGINAL_CONTROLLER:modified.encode('utf-8'),
                 'openevolve_seed_adapter.py':adapter.read_bytes(),
                 'coulomb_selective_sidecar.py':sidecar.read_bytes()}
    for name,data in artifacts.items():
        p = outdir / name
        with p.open('xb') as f: f.write(data)
        p.chmod(0o600)
    return {'status':'CREATED_CLONED_PROMPT_ONLY_CONTROLLER_NO_API',
            'original_sha256':got, 'clone_sha256':hashlib.sha256(modified.encode()).hexdigest(),
            'directory':str(outdir), 'core_source_modified':False}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source',type=Path,required=True)
    ap.add_argument('--outdir',type=Path,required=True)
    ap.add_argument('--sidecar',type=Path,default=Path(__file__).with_name('coulomb_selective_sidecar.py'))
    args=ap.parse_args()
    import json
    print(json.dumps(create(args.source,args.outdir,args.sidecar),indent=2))

if __name__=='__main__':main()
