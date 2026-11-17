#!/usr/bin/env python3
"""P2 native OpenEvolve trial: C P1 protected provenance -> A's ProgramDatabase.

Modes:
  --audit: read existing P1 owner-protected records; no DB writes, no model, no Podman.
  --trial-native: repeat audit, create BRAND NEW root-only HHL trial DB, save/reload/sample.

Never inserts into a formal HHL research DB; never changes P1 proof status or
claims original 2026-11-06 source attribution. Native DB test is ONLY proven
when --trial-native succeeds on the VM with OpenEvolve 0.4.0.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import sys
import tempfile

PKG = Path(__file__).resolve().parent
REF = PKG / 'ref'
sys.path.insert(0, str(REF))

import rootless_core
import hhl_p1_broker_journal as broker
import hhl_p1_eval_binding as evaluator
import hhl_evolution_bridge as evol
from openevolve_seed_adapter_A_original import load_database_with_verified_stats

RECORDS = Path('/srv/hhl-task2/research-records')
P1_RECEIPT = RECORDS / 'HHL_P1_ISOLATED_ylvY5mMO'
P1_BROKER = Path('/srv/hhl-task2/p1-review-broker-spool')
P1_EVALUATOR = Path('/srv/hhl-task2/p1-review-evaluator-spool')
TARGET_BUNDLE = 'b1d842e8ed28efef79b41d25125013694985c171cc9eb2778987393439c4c444'
TARGET_OPS = '2c24272933b88b228fb664d43832eec5f625d0f2e03c214e0cef48991a428661'
TARGET_TQ = 4_169_620
TARGET_T = 14_378
TARGET_Q = 290
BASELINE_TQ = 4_940_400
HEX32 = re.compile(r'[a-f0-9]{32}\Z')


class Stop(ValueError):
    pass


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def unique_pairs(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise Stop('duplicate JSON key')
        out[k] = v
    return out


def parse_json(raw: bytes) -> dict:
    obj = json.loads(raw, object_pairs_hook=unique_pairs)
    if type(obj) is not dict:
        raise Stop('expected object')
    return obj


def protected_regular_bytes(root: Path, name: str, *, maximum=20_000_000) -> bytes:
    if '/' in name or name.startswith('.') or not re.fullmatch(r'[a-zA-Z0-9_.-]{1,100}', name):
        raise Stop('unsafe receipt filename')
    f = root / name
    st = f.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or st.st_nlink != 1 or stat.S_IMODE(st.st_mode) != 0o600:
        raise Stop(f'unsafe root-only receipt: {name}')
    if st.st_size <= 0 or st.st_size > maximum:
        raise Stop('receipt outside size bound')
    fd = os.open(f, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        fst = os.fstat(fd)
        if (fst.st_dev, fst.st_ino, fst.st_size) != (st.st_dev, st.st_ino, st.st_size):
            raise Stop('receipt inode changed')
        raw = os.read(fd, maximum + 1)
        if len(raw) != fst.st_size:
            raise Stop('short receipt read')
        return raw
    finally:
        os.close(fd)


def verify_protected_dirs(receipt_dir: Path = P1_RECEIPT) -> None:
    if os.geteuid() != 0:
        raise Stop('root-only operator required')
    for p in (RECORDS, receipt_dir):
        st = p.lstat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != 0 or stat.S_IMODE(st.st_mode) != 0o700:
            raise Stop(f'unsafe root-only directory: {p}')
    if receipt_dir.parent != RECORDS or receipt_dir.name != P1_RECEIPT.name:
        raise Stop('unexpected P1 receipt directory')


def native_metrics(record: dict) -> tuple[int, int, int]:
    if type(record) is not dict or record.get('valid') is not True or record.get('verdict') != 'OK':
        raise Stop('native grader did not return OK')
    vals = []
    for key in ('toffoli', 'qubits', 'tq_cost'):
        value = record.get(key)
        if type(value) not in (int, float) or not (0 < value < 2**53) or int(value) != value:
            raise Stop(f'invalid native metric {key}')
        vals.append(int(value))
    t, q, tq = vals
    if t*q != tq or (t, q, tq) != (TARGET_T, TARGET_Q, TARGET_TQ):
        raise Stop('native metrics do not match P1 validated circuit')
    return t, q, tq


def audit_receipts(receipt_dir: Path = P1_RECEIPT, *, check_original_spools: bool = True) -> dict:
    """Read exact raw P1 journal and event bytes, NEVER infer source from summary.

    Returns the canonical parsed source and raw hashes, but not an admission
    authorization. Root-only P1 raw spools are independently checked if enabled.
    """
    verify_protected_dirs(receipt_dir)
    summary = parse_json(protected_regular_bytes(receipt_dir, 'summary.json', maximum=100_000))
    if (summary.get('status') != 'P1_ISOLATED_TWO_NATIVE_REPLAYS_OK' or
        summary.get('source_at_evaluation') != 'CORRELATED_NEW_P1_EVENT' or
        summary.get('p0_historical_source') != 'NOT_PROVEN' or
        summary.get('model_calls') != 0 or summary.get('formal_db_writes') != 0 or
        summary.get('p1_tq') != TARGET_TQ or summary.get('source_bundle_sha256') != TARGET_BUNDLE or
        type(summary.get('runs')) is not list or len(summary['runs']) != 2):
        raise Stop('unexpected P1 summary evidence/attribution status')
    broker_u = pwd.getpwnam('hhl-runner').pw_uid
    eval_u = pwd.getpwnam('hhl-eval').pw_uid
    observed = []
    for index in (1, 2):
        receipt = parse_json(protected_regular_bytes(receipt_dir, f'run-{index}-receipt.json'))
        if receipt != summary['runs'][index-1] or receipt.get('run_index') != index or receipt.get('formal_db_eligible') is not False:
            raise Stop('P1 receipt/summary inconsistency or false admission')
        bid, eid = receipt.get('broker_run_id'), receipt.get('event_id')
        if type(bid) is not str or not HEX32.fullmatch(bid) or type(eid) is not str or not HEX32.fullmatch(eid):
            raise Stop('invalid native IDs')
        braw = protected_regular_bytes(receipt_dir, f'run-{index}-broker-raw.json')
        eraw = protected_regular_bytes(receipt_dir, f'run-{index}-evaluator-raw.json')
        ops = protected_regular_bytes(receipt_dir, f'run-{index}.ops', maximum=3_000_000)
        if (sha(braw) != receipt.get('broker_raw_sha256') or
            sha(eraw) != receipt.get('evaluator_raw_sha256') or
            sha(ops) != TARGET_OPS or receipt.get('ops_sha256') != TARGET_OPS or
            receipt.get('bundle_sha256') != TARGET_BUNDLE or receipt.get('TQ') != TARGET_TQ):
            raise Stop('P1 raw bytes/receipt hashes differ')
        bobj = broker.validate_record(braw)
        eobj, eops = evaluator.validate_provisional_event(eraw)
        if bobj['broker_run_id'] != bid or eobj['event_id'] != eid or eops != ops:
            raise Stop('P1 event identity or ops bytes mismatch')
        if native_metrics(eobj['result']) != (TARGET_T, TARGET_Q, TARGET_TQ):
            raise Stop('bad native metrics')
        correlated = evaluator.audit_bound_pair(eraw, braw, broker_validator=broker.validate_record)
        if (correlated.get('status') != 'BROKER_SOURCE_CLAIM_CORRELATED' or
            correlated.get('formal_db_eligible') is not False or
            correlated.get('source_bundle_sha256') != TARGET_BUNDLE or
            correlated.get('ops_sha256') != TARGET_OPS or
            receipt.get('correlation') != correlated):
            raise Stop('untrusted P1 correlation')
        if check_original_spools:
            rb, _ = broker.root_read_record(bid, directory=P1_BROKER, runner_uid=broker_u)
            re_, _, eo = evaluator.root_read_claim_event(eid, directory=P1_EVALUATOR, owner_uid=eval_u)
            if rb != braw or re_ != eraw or eo != ops:
                raise Stop('root-only archive differs from runner/evaluator original spools')
        files = {k: base64.b64decode(v, validate=True) for k,v in bobj['files_b64'].items()}
        if bobj['source_bundle_sha256'] != TARGET_BUNDLE or broker.bundle_sha(bobj['entry'],files) != TARGET_BUNDLE:
            raise Stop('full bundle differs from P1 source')
        lib = files.pop('hhl-uncompute-author.zip',None)
        if lib is None:
            raise Stop('missing pinned author library')
        bundle = evol.freeze_actual_bundle(bobj['entry'],files,lib)
        evol.verify_bundle(bundle)
        if bundle.sha256 != TARGET_BUNDLE:
            raise Stop('A×C source ID differs from Broker journal')
        observed.append((bid,eid,bundle,sha(braw),sha(eraw)))
    a,b = observed
    if a[0] == b[0] or a[1] == b[1] or a[2] != b[2]:
        raise Stop('replays not independent or source differs')
    return {'task': 'hhl-angle', 'admission': 'TRIAL_ONLY_NOT_FORMAL',
            'source_at_p1_replay': 'SOURCE_BOUND_AND_NATIVE_OK',
            'original_2026_11_06_source': 'NOT_PROVEN',
            'bundle_sha256': TARGET_BUNDLE,'ops_sha256': TARGET_OPS,
            'toffoli': TARGET_T,'qubits': TARGET_Q,'tq_cost': TARGET_TQ,
            'source_bundle': a[2],
            'broker_ids': [a[0],b[0]],'event_ids': [a[1],b[1]],
            'receipt_sha256': sha(protected_regular_bytes(receipt_dir,'summary.json')),
            'raw_spool_crosscheck': check_original_spools}


def _new_file(parent: Path, name: str, data: bytes) -> None:
    fd = os.open(parent/name, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(data);f.flush();os.fsync(f.fileno())
    finally:
        pass


def _summary_to_print(audit: dict) -> dict:
    return {k:v for k,v in audit.items() if k != 'source_bundle'}


def native_trial(audit: dict) -> dict:
    """First TRUE OpenEvolve save/reload/sample on VM, no model or formal admission."""
    # Lazy native import, fail closed; DO NOT install or fake an OpenEvolve package.
    from openevolve.config import DatabaseConfig
    from openevolve.database import Program,ProgramDatabase
    if os.geteuid() != 0:
        raise Stop('root required')
    bundle = audit['source_bundle']
    evol.verify_bundle(bundle)
    trial = Path(tempfile.mkdtemp(prefix='HHL_P2_NATIVE_TRIAL_',dir=RECORDS))
    os.chmod(trial,0o700)
    dbpath = trial / 'db'
    dbpath.mkdir(mode=0o700)
    old_umask = os.umask(0o077)
    cfg = DatabaseConfig(db_path=str(dbpath), population_size=50, archive_size=50,
                         num_islands=1, feature_dimensions=['complexity','diversity'],
                         feature_bins=16, random_seed=42)
    db = ProgramDatabase(cfg)
    entry = bundle.content()[bundle.entry].decode('utf-8','strict')
    data = {
        'id': bundle.sha256, 'code': entry, 'language':'python',
        'generation':0, 'parent_id':None,
        'changes_description':'P1 isolated event-time source-bound replay; trial DB only',
        'metrics': {'combined_score':BASELINE_TQ/TARGET_TQ,
                    'toffoli':float(TARGET_T),'qubits':float(TARGET_Q),'tq_cost':float(TARGET_TQ)},
        'metadata': {'task':'hhl-angle','bundle_sha256':bundle.sha256,
                     'entry_sha256':bundle.entry_sha256,'ops_sha256':TARGET_OPS,
                     'p1_summary_sha256':audit['receipt_sha256'],
                     'source_at_p1_replay':'CORRELATED_NEW_P1_EVENT',
                     'original_historical_source':'NOT_PROVEN',
                     'research_admission':'TRIAL_ONLY','bundle_kept_out_of_band':True}
    }
    added = db.add(Program(**data),iteration=0)
    if added is None:
        raise Stop('Native OpenEvolve rejected new P1 trial candidate')
    db.save(str(dbpath),iteration=0)
    profile = {'schema':1,'features':['complexity','diversity'],
               'fitness':'4940400/tq_cost','task':'hhl-angle','admission':'TRIAL_ONLY'}
    _new_file(dbpath,'map_elites_profile.json',(json.dumps(profile,sort_keys=True)+'\n').encode())
    # Save exact sealed FULL SOURCE as binary map outside native Program.code.
    bundle_record = {'schema':'hhl-source-bundle-v1','entry':bundle.entry,
                     'bundle_sha256':bundle.sha256,
                     'files_b64':{p:base64.b64encode(raw).decode('ascii') for p,raw in bundle.files},
                     'P1_new_source_bound':True,'old_2026_11_06_source':'NOT_PROVEN'}
    _new_file(trial,'immutable-source-bundle.json',
              (json.dumps(bundle_record,sort_keys=True,separators=(',',':'))+'\n').encode())
    restored = load_database_with_verified_stats(cfg,ProgramDatabase)
    p = restored.get(bundle.sha256)
    if (p is None or p.code != entry or p.id != bundle.sha256 or
        p.parent_id is not None or p.generation != 0 or
        p.metrics['tq_cost'] != float(TARGET_TQ) or
        p.metadata.get('research_admission') != 'TRIAL_ONLY'):
        raise Stop('Native OpenEvolve save/reload mismatch')
    chosen, _ = restored.sample(num_inspirations=0)
    if (chosen.id != bundle.sha256 or chosen.code != entry or
        chosen.metrics['tq_cost'] != float(TARGET_TQ)):
        raise Stop('Native MAP-Elites sample mismatched preserved source')
    os.umask(old_umask)
    result={'status':'P2_NATIVE_TRIAL_DB_SAVE_RELOAD_SAMPLE_PASS',
            'root_only_trial_dir':str(trial),'program_id':bundle.sha256,
            'source_code_sha256':bundle.entry_sha256,
            'sampled_parent_id':chosen.id,'trial_db_count':len(restored.programs),
            'lineage':'generation0_parent_none',
            'original_2026_11_06_source':'NOT_PROVEN',
            'native_openevolve_used':True,'model_calls':0,'formal_db_writes':0,
            'coulomb_db_writes':0}
    _new_file(trial,'TRIAL_RESULT.json',(json.dumps(result,sort_keys=True,indent=2)+'\n').encode())
    return result


def main() -> int:
    ap=argparse.ArgumentParser()
    g=ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--audit',action='store_true')
    g.add_argument('--trial-native',action='store_true')
    args=ap.parse_args()
    try:
        data=audit_receipts()
        print('P2_P1_PROTECTED_EVIDENCE_AUDIT=PASS',flush=True)
        print('P2_P1_SOURCE_BOUND_NEW_REPLAYS=2',flush=True)
        print('P2_20261106_HISTORICAL_ATTRIBUTION=NOT_PROVEN',flush=True)
        if args.audit:
            print('P2_NO_DB_WRITES=YES',flush=True)
            print(json.dumps(_summary_to_print(data),sort_keys=True))
        else:
            result=native_trial(data)
            print(json.dumps(result,sort_keys=True))
            print('P2_NATIVE_TRIAL_SAVE_RELOAD_SAMPLE=PASS',flush=True)
        return 0
    except (OSError,ValueError,ImportError,TypeError,KeyError,RuntimeError,PermissionError) as e:
        print('P2_STOP='+type(e).__name__+':'+str(e)[:300],file=sys.stderr,flush=True)
        return 2

if __name__=='__main__':
    raise SystemExit(main())
