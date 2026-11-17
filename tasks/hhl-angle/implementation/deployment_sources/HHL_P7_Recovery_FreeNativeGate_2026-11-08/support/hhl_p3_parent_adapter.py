#!/usr/bin/env python3
"""P3: audit a genuine native OpenEvolve trial parent and plan a FULL HHL bundle stage.

Strictly read-only. Reuses P2's checked source/replay evidence and A's real
OpenEvolve ProgramDatabase.sample(); never interprets main.py as the full
quantum circuit. Does not launch, write DB, stage a workspace or call a model.

The P2 trial is not automatically promoted to formal research evidence.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / 'ref'))
import hhl_evolution_bridge as evol
import hhl_p2_trial_db as p2

TRIAL_ROOT = Path('/srv/hhl-task2/research-records')
TRIAL_PREFIX = re.compile(r'HHL_P2_NATIVE_TRIAL_[A-Za-z0-9]{8}\Z')
NEW_TAG = re.compile(r'hhl-[a-z0-9][a-z0-9-]{7,56}\Z')
OLD_PREFIX = re.compile(r'runs/(hhl-[a-z0-9][a-z0-9-]{7,56})/attempt-0[1-8]/\Z')
LIB_NAME = 'hhl-uncompute-author.zip'
BASELINE_MAIN_SHA = '705d838f3b7079fa4f7955197bb86f3fa111e151cdb2aeaffba3d2c2811290a3'

class Stop(ValueError):
    pass

def _dir_secure(path: Path, expected_mode=0o700) -> None:
    st = path.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != 0 or stat.S_IMODE(st.st_mode) != expected_mode:
        raise Stop('unsafe directory owner/mode: ' + str(path))

def _decode_b64_table(record: dict) -> tuple[str, dict[str, bytes]]:
    if (record.get('schema') != 'hhl-source-bundle-v1' or
        record.get('P1_new_source_bound') is not True or
        record.get('old_2026_11_06_source') != 'NOT_PROVEN' or
        type(record.get('entry')) is not str or type(record.get('files_b64')) is not dict):
        raise Stop('not a verified P1 trial source archive')
    b64map = record['files_b64']
    if not 1 <= len(b64map) <= evol.MAX_FILES:
        raise Stop('wrong archive file count')
    values = {}
    for name, val in b64map.items():
        evol.check_path(name)
        if type(val) is not str:
            raise Stop('non-string source contents')
        try:
            raw = base64.b64decode(val, validate=True)
        except Exception as ex:
            raise Stop('malformed base64 source') from ex
        if base64.b64encode(raw).decode('ascii') != val:
            raise Stop('noncanonical source encoding')
        values[name] = raw
    lib = values.pop(LIB_NAME, None)
    if lib is None:
        raise Stop('pinned library absent')
    bundle = evol.freeze_actual_bundle(record['entry'], values, lib)
    if bundle.sha256 != record.get('bundle_sha256'):
        raise Stop('stored bundle identity mismatch')
    return record['entry'], bundle.content()


def rebase_plan(bundle: evol.SourceBundle, original_program_id: str, run_tag: str,
                *, parent_generation: int = 0, verified_parent_tq: int | None = None) -> dict:
    """Preserve parent provenance while rebasing all source paths to a new attempt.

    Original Program ID != new path-dependent broker input SHA. A copied
    original parent is NOT a new offspring or newly discovered architecture.
    """
    evol.verify_bundle(bundle)
    if type(parent_generation) is not int or not 0 <= parent_generation <= 100000:
        raise Stop('invalid native parent generation')
    if (verified_parent_tq is not None and
        (type(verified_parent_tq) is not int or verified_parent_tq <= 0)):
        raise Stop('invalid native verified parent TQ')
    if parent_generation > 0 and verified_parent_tq is None:
        raise Stop('future parent generations require their own verified native TQ')
    if original_program_id != bundle.sha256 or type(run_tag) is not str or not NEW_TAG.fullmatch(run_tag):
        raise Stop('invalid source Program ID or next run tag')
    if not bundle.entry.endswith('/main.py'):
        raise Stop('unexpected HHL parent main entry')
    old_pre = bundle.entry[:-len('main.py')]
    m = OLD_PREFIX.fullmatch(old_pre)
    if m is None:
        raise Stop('parent files outside a single numbered attempt tree')
    if m.group(1) == run_tag:
        raise Stop('parent worktree must not be reused for a new generation')
    new_pre = f'runs/{run_tag}/attempt-01/'
    new_files = {}
    source_hashes = []
    for name, data in bundle.files:
        if name == LIB_NAME:
            continue
        if not name.startswith(old_pre):
            raise Stop('parent files span multiple attempt directories')
        leaf = name[len(old_pre):]
        # Do not freeze A/C's current 13 filenames as the only future design.
        # Later DeepSeek generations may legitimately add new one-level files.
        if '/' in leaf or not evol.PART_RE.fullmatch(leaf) or leaf.startswith('.'):
            raise Stop('unsafe or nested inherited file: '+leaf)
        dest = new_pre + leaf
        evol.check_path(dest)
        new_files[dest] = data
        source_hashes.append({'name':leaf,'sha256':evol.digest(data),'bytes':len(data),
                              'old_path':name,'new_path':dest})
    if not {'main.py','circuit.py','constants.json','meta.json','contract.json','grader.py'}.issubset(
            {x['name'] for x in source_hashes}):
        raise Stop('required parent source missing')
    lib = bundle.content()[LIB_NAME]
    stage = evol.freeze_actual_bundle(new_pre+'main.py', new_files, lib)
    if len(stage.files) != len(bundle.files):
        raise Stop('staged bundle dropped files')
    # Exact bytes preserved. Only paths may differ.
    if sorted((p.rsplit('/',1)[-1],evol.digest(b)) for p,b in stage.files if p != LIB_NAME) != \
       sorted((p.rsplit('/',1)[-1],evol.digest(b)) for p,b in bundle.files if p != LIB_NAME):
        raise Stop('parent contents changed during staging plan')
    return {
        'schema':'hhl-p3-next-generation-parent-plan-v1',
        'task':'hhl-angle','parent_original_program_id':original_program_id,
        'parent_program_generation':parent_generation,
        'parent_source_status':('P1_NEW_SOURCE_BOUND_TWO_NATIVE_REPLAYS' if parent_generation == 0
                                else 'LATER_GENERATION_NEW_SOURCE_BOUND_TWO_NATIVE_REPLAYS'),
        'historical_p0_source_attribution':'NOT_PROVEN',
        'run_tag':run_tag,'target_entry':stage.entry,
        'materialized_broker_input_sha256':stage.sha256,
        'materialized_is_new_offspring':False,
        'full_source_file_count':len(source_hashes),
        'source_files':sorted(source_hashes,key=lambda x:x['name']),
        'author_library_sha256':evol.AUTHOR_LIB_PIN,
        'source_bytes_identical':True,
        'verified_parent_tq':verified_parent_tq,
        'ready_for_paid_model':False,
        'research_db_admission':'TRIAL_ONLY',
        'external_stage_requires_root_operator':True,
        'note':'No staging/model/DB writes occurred. Must admit verified source to a dedicated HHL DB before paid parent->child.'
    }


def audit_trial_and_sample(trial: Path, run_tag: str) -> dict:
    if os.geteuid() != 0:
        raise Stop('root required')
    if trial.parent != TRIAL_ROOT or not TRIAL_PREFIX.fullmatch(trial.name):
        raise Stop('unexpected DB trial directory')
    _dir_secure(TRIAL_ROOT)
    _dir_secure(trial)
    dbpath = trial/'db'
    _dir_secure(dbpath)
    audit = p2.audit_receipts()
    raw = p2.protected_regular_bytes(trial, 'immutable-source-bundle.json', maximum=1_800_000)
    record = p2.parse_json(raw)
    entry, saved_files = _decode_b64_table(record)
    lib = saved_files[LIB_NAME]
    source = {k:v for k,v in saved_files.items() if k != LIB_NAME}
    bundle = evol.freeze_actual_bundle(entry, source, lib)
    if bundle != audit['source_bundle'] or bundle.sha256 != audit['bundle_sha256']:
        raise Stop('trial source does not equal protected P1 evidence')
    profile = p2.parse_json(p2.protected_regular_bytes(dbpath,'map_elites_profile.json',maximum=20_000))
    if (profile.get('task')!='hhl-angle' or profile.get('admission')!='TRIAL_ONLY' or
        profile.get('features')!=['complexity','diversity'] or
        profile.get('fitness')!='4940400/tq_cost'):
        raise Stop('native DB trial profile mismatch')
    from openevolve.config import DatabaseConfig
    from openevolve.database import Program, ProgramDatabase
    from openevolve_seed_adapter_A_original import load_database_with_verified_stats
    cfg = DatabaseConfig(db_path=str(dbpath), population_size=50, archive_size=50,
                         num_islands=1, feature_dimensions=['complexity','diversity'],
                         feature_bins=16, random_seed=42)
    db = load_database_with_verified_stats(cfg,ProgramDatabase)
    if len(db.programs)!=1:
        raise Stop('expected exactly one verified P1 trial parent')
    parent, _ = db.sample(num_inspirations=0)
    if (parent.id != bundle.sha256 or parent.code != saved_files[bundle.entry].decode('utf-8','strict') or
        parent.metadata.get('research_admission')!='TRIAL_ONLY' or
        parent.metadata.get('source_at_p1_replay')!='CORRELATED_NEW_P1_EVENT' or
        parent.metadata.get('original_historical_source')!='NOT_PROVEN' or
        parent.parent_id is not None or parent.generation != 0 or
        parent.metrics.get('tq_cost') != float(p2.TARGET_TQ)):
        raise Stop('native sampled parent is not the audited full P1 source')
    result = rebase_plan(bundle,parent.id,run_tag,verified_parent_tq=p2.TARGET_TQ)
    result.update({'native_openevolve_sample':'PASS',
                   'verified_parent_tq':p2.TARGET_TQ,
                   'p2_trial_directory':str(trial),
                   'p1_receipt_sha256':audit['receipt_sha256']})
    return result


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument('--audit-trial-parent', action='store_true',required=True)
    parser.add_argument('--trial-dir',type=Path,required=True)
    parser.add_argument('--run-tag',required=True)
    a=parser.parse_args()
    try:
        data=audit_trial_and_sample(a.trial_dir,a.run_tag)
        print('P3_FULL_BUNDLE_NATIVE_PARENT_SAMPLE=PASS')
        print('P3_PARENT_AND_MATERIALIZATION_IDS_DISTINCT='+('YES' if data['parent_original_program_id']!=data['materialized_broker_input_sha256'] else 'NO'))
        print('P3_FILE_CONTENTS_PRESERVED=PASS')
        print('P3_TRIAL_ONLY_NO_MODEL_NO_DB_WRITES=YES')
        print(json.dumps(data,sort_keys=True,separators=(',',':')))
        return 0
    except (OSError,KeyError,TypeError,ValueError,ImportError,RuntimeError,PermissionError) as ex:
        print('P3_STOP='+type(ex).__name__+':'+str(ex)[:240],file=sys.stderr)
        return 2

if __name__=='__main__':
    raise SystemExit(main())
