#!/usr/bin/env python3
"""HHL-only gen0 seed admission from *new* P1 source-bound native replays.

A original ProgramDatabase / C P1+P2 evidence, no alternate scorer. Audit is
read-only; admission requires an explicit acknowledgement and writes only a
new root-private HHL directory. 2026-11-06 P0 historical attribution remains
NOT_PROVEN. This is NOT a paid model/session controller.
"""
from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import stat
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / 'ref'))

import hhl_p2_trial_db as p2
import hhl_p3_parent_adapter as p3
import hhl_evolution_bridge as evol
from openevolve_seed_adapter_A_original import load_database_with_verified_stats

RECORDS = Path('/srv/hhl-task2/research-records')
PREFIX = re.compile(r'HHL_P4_FORMAL_GEN0_[A-Za-z0-9]{8}\Z')
ID = re.compile(r'[a-f0-9]{64}\Z')
PROFILE = 'map_elites_profile.json'
P1_EVIDENCE_FILES = (
    'summary.json',
    'run-1-receipt.json', 'run-1-broker-raw.json', 'run-1-evaluator-raw.json', 'run-1.ops',
    'run-2-receipt.json', 'run-2-broker-raw.json', 'run-2-evaluator-raw.json', 'run-2.ops',
)
ADMISSION = 'FORMAL_HHL_GEN0_P1_NEW_REPLAY_ONLY'
SCHEMA = 'hhl-p4-protected-program-archive-v1'

class Stop(ValueError):
    pass


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def path_invariant_source_digest(bundle: evol.SourceBundle) -> str:
    """Dedup EXACT same source bytes after path rebasing; not an algorithm-family classifier."""
    evol.verify_bundle(bundle)
    leaves = [(name.rsplit('/', 1)[-1], raw) for name, raw in bundle.files]
    if len(set(name for name, _ in leaves)) != len(leaves):
        raise Stop('ambiguous same-basename files in source bundle')
    h = hashlib.sha256(b'HHL_PATH_INVARIANT_BYTES_V1\0')
    for name, raw in sorted(leaves):
        nb = name.encode('ascii')
        h.update(struct.pack('>I', len(nb)))
        h.update(nb)
        h.update(struct.pack('>Q', len(raw)))
        h.update(raw)
    return h.hexdigest()


def _root_directory(path: Path, *, mode: int = 0o700):
    st = path.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != 0 or stat.S_IMODE(st.st_mode) != mode:
        raise Stop(f'unsafe root-only directory: {path}')


def _new_file(root: Path, name: str, raw: bytes) -> None:
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,95}', name):
        raise Stop('unsafe file basename')
    fd = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _json_bytes(obj: dict) -> bytes:
    return (json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode('ascii')


def make_gen0_spec(audit: dict) -> tuple[dict, dict]:
    """Pure conversion of fully audited P1 replays, *not* self-attestation."""
    if type(audit) is not dict or (audit.get('admission') != 'TRIAL_ONLY_NOT_FORMAL' or
        audit.get('source_at_p1_replay') != 'SOURCE_BOUND_AND_NATIVE_OK' or
        audit.get('original_2026_11_06_source') != 'NOT_PROVEN' or
        audit.get('raw_spool_crosscheck') is not True or
        type(audit.get('broker_ids')) is not list or len(set(audit['broker_ids'])) != 2 or
        type(audit.get('event_ids')) is not list or len(set(audit['event_ids'])) != 2):
        raise Stop('P1 source-bound receipt audit not satisfied')
    if (audit.get('bundle_sha256') != p2.TARGET_BUNDLE or
        audit.get('ops_sha256') != p2.TARGET_OPS or
        (audit.get('toffoli'), audit.get('qubits'), audit.get('tq_cost')) !=
        (p2.TARGET_T, p2.TARGET_Q, p2.TARGET_TQ)):
        raise Stop('candidate metrics/source not admitted by this pinned gen0 protocol')
    bundle = audit.get('source_bundle')
    evol.verify_bundle(bundle)
    if bundle.sha256 != audit['bundle_sha256'] or bundle.entry not in bundle.content():
        raise Stop('frozen full bundle does not match verified P1 evidence')
    if not bundle.entry.endswith('/main.py') or len(bundle.files) < 7:
        raise Stop('incomplete initial source bundle')
    entry = bundle.content()[bundle.entry].decode('utf-8', 'strict')
    if _sha(entry.encode('utf-8')) != bundle.entry_sha256:
        raise Stop('entry digest drift')
    desc = {
        'schema': SCHEMA, 'task': 'hhl-angle', 'program_id': bundle.sha256,
        'entry': bundle.entry, 'entry_sha256': bundle.entry_sha256,
        'ops_sha256': audit['ops_sha256'],
        'exact_bytes_path_invariant_sha256': path_invariant_source_digest(bundle),
        'source_evidence': 'P1_NEW_BOUND_BROKER_EVALUATOR_TWO_NATIVE_REPLAYS',
        'original_2026_11_06_source': 'NOT_PROVEN',
        'p1_summary_sha256': audit['receipt_sha256'],
        'broker_run_ids': list(audit['broker_ids']),
        'evaluator_event_ids': list(audit['event_ids']),
        'files_b64': {name: base64.b64encode(raw).decode('ascii') for name, raw in bundle.files},
    }
    data = {
        'id': bundle.sha256, 'code': entry, 'language': 'python',
        'generation': 0, 'parent_id': None,
        'changes_description': 'P1 independently reproduced source-bound HHL gen0 seed; P0 historical source unproven',
        'metrics': {'combined_score': p2.BASELINE_TQ / p2.TARGET_TQ,
                    'toffoli': float(p2.TARGET_T), 'qubits': float(p2.TARGET_Q),
                    'tq_cost': float(p2.TARGET_TQ)},
        'metadata': {
            'task': 'hhl-angle', 'bundle_sha256': bundle.sha256,
            'entry_sha256': bundle.entry_sha256, 'ops_sha256': audit['ops_sha256'],
            'exact_bytes_path_invariant_sha256': path_invariant_source_digest(bundle),
            'evidence_summary_sha256': audit['receipt_sha256'],
            'source_at_p1_replay': 'CORRELATED_NEW_P1_EVENT',
            'original_historical_source': 'NOT_PROVEN',
            'research_admission': ADMISSION, 'full_bundle_stored_out_of_band': True,
            'lineage_semantics': 'gen0_seed_not_proof_of_independent_algorithm_family',
            'seed_origin': 'baseline_descendant_P1_reproduced_from_post_session_source',
            'independent_bootstrap_branch': False,
        },
    }
    return data, desc


def _parse_protected_json(folder: Path, name: str, *, max_bytes: int = 3_000_000) -> dict:
    return p2.parse_json(p2.protected_regular_bytes(folder, name, maximum=max_bytes))


def _decode_source_record(obj: dict) -> evol.SourceBundle:
    if type(obj) is not dict or obj.get('schema') != SCHEMA or obj.get('task') != 'hhl-angle':
        raise Stop('untrusted HHL full source archive schema')
    table = obj.get('files_b64')
    if type(table) is not dict or len(table) < 7 or len(table) > evol.MAX_FILES:
        raise Stop('incomplete HHL source archive')
    raw = {}
    for name, value in table.items():
        evol.check_path(name)
        if type(value) is not str:
            raise Stop('malformed source bytes')
        data = base64.b64decode(value, validate=True)
        if base64.b64encode(data).decode('ascii') != value:
            raise Stop('noncanonical source base64')
        raw[name] = data
    lib = raw.pop(p3.LIB_NAME, None)
    if lib is None:
        raise Stop('missing pinned author library')
    bundle = evol.freeze_actual_bundle(obj.get('entry'), raw, lib)
    if (bundle.sha256 != obj.get('program_id') or
        bundle.entry_sha256 != obj.get('entry_sha256') or
        path_invariant_source_digest(bundle) != obj.get('exact_bytes_path_invariant_sha256') or
        obj.get('ops_sha256') != p2.TARGET_OPS or
        obj.get('original_2026_11_06_source') != 'NOT_PROVEN'):
        raise Stop('source archive inconsistent with HHL program identity')
    return bundle


def _config(path: Path):
    from openevolve.config import DatabaseConfig
    return DatabaseConfig(db_path=str(path), population_size=50, archive_size=50,
                          num_islands=1, feature_dimensions=['complexity', 'diversity'],
                          feature_bins=16, random_seed=42)


def sample_audited(db_root: Path, *, run_tag: str) -> dict:
    """Use A's native DB sampler, then fetch EXACT full source by Program ID."""
    if os.geteuid() != 0 or db_root.parent != RECORDS or not PREFIX.fullmatch(db_root.name):
        raise Stop('unexpected formal HHL DB path or identity')
    for root in (RECORDS, db_root, db_root / 'db', db_root / 'bundles', db_root / 'evidence'):
        _root_directory(root)
    index = _parse_protected_json(db_root, 'index.json', max_bytes=200_000)
    if index.get('schema') != 'hhl-p4-gen0-index-v1' or index.get('admission') != ADMISSION or (
        index.get('original_2026_11_06_source') != 'NOT_PROVEN'):
        raise Stop('untrusted HHL gen0 index')
    bid = index['program_id']
    if type(bid) is not str or not ID.fullmatch(bid):
        raise Stop('invalid native program ID')
    rec = _parse_protected_json(db_root / 'bundles', bid + '.json', max_bytes=3_000_000)
    if _sha(_json_bytes(rec)) != index.get('archive_sha256'):
        raise Stop('immutable bundle archive changed')
    bundle = _decode_source_record(rec)
    if (bundle.sha256 != bid or index.get('ops_sha256') != rec.get('ops_sha256') or
        index.get('exact_bytes_path_invariant_sha256') != path_invariant_source_digest(bundle)):
        raise Stop('gen0 archive identity changed')
    em = _parse_protected_json(db_root / 'evidence', 'evidence-manifest.json', max_bytes=20_000)
    if (em.get('schema') != 'hhl-p4-copied-p1-evidence-v1' or
        type(em.get('files')) is not dict or set(em['files']) != set(P1_EVIDENCE_FILES) or
        _sha(_json_bytes(em)) != index.get('evidence_manifest_sha256')):
        raise Stop('immutable P1 evidence manifest missing or changed')
    for basename in P1_EVIDENCE_FILES:
        raw = p2.protected_regular_bytes(db_root / 'evidence', basename, maximum=20_000_000)
        if _sha(raw) != em['files'][basename]:
            raise Stop('archived original P1 evidence changed: ' + basename)
    if em['files']['summary.json'] != index.get('p1_summary_sha256'):
        raise Stop('archived P1 summary no longer matches admitted proof')
    if (rec.get('broker_run_ids') != index.get('broker_run_ids') or
        rec.get('evaluator_event_ids') != index.get('evaluator_event_ids') or
        type(index.get('broker_run_ids')) is not list or
        len(set(index['broker_run_ids'])) != 2 or
        type(index.get('evaluator_event_ids')) is not list or
        len(set(index['evaluator_event_ids'])) != 2):
        raise Stop('formal gen0 provenance IDs disagree')
    for i in (1,2):
        x = _parse_protected_json(db_root / 'evidence', f'run-{i}-receipt.json', max_bytes=200_000)
        if (x.get('broker_run_id') != index['broker_run_ids'][i-1] or
            x.get('event_id') != index['evaluator_event_ids'][i-1]):
            raise Stop('copied P1 per-replay receipt identity changed')
    profile = _parse_protected_json(db_root / 'db', PROFILE, max_bytes=20_000)
    if profile != {
        'schema': 1, 'features': ['complexity','diversity'],
        'fitness': '4940400/tq_cost', 'task': 'hhl-angle',
        'admission': ADMISSION, 'source_archive': 'bundles/<program_id>.json',
    }:
        raise Stop('unexpected HHL MAP-Elites DB profile')
    from openevolve.database import ProgramDatabase
    db = load_database_with_verified_stats(_config(db_root / 'db'), ProgramDatabase)
    if len(db.programs) != 1:
        raise Stop('expected one formal HHL gen0 seed')
    parent, _ = db.sample(num_inspirations=0)
    if (parent.id != bid or parent.parent_id is not None or parent.generation != 0 or
        parent.code.encode('utf-8') != bundle.content()[bundle.entry] or
        parent.metrics.get('tq_cost') != float(p2.TARGET_TQ) or
        parent.metadata.get('research_admission') != ADMISSION or
        parent.metadata.get('exact_bytes_path_invariant_sha256') != path_invariant_source_digest(bundle) or
        parent.metadata.get('original_historical_source') != 'NOT_PROVEN'):
        raise Stop('native DB sampled parent cannot be linked to exact full source')
    plan = p3.rebase_plan(bundle, parent.id, run_tag,
                          parent_generation=parent.generation,
                          verified_parent_tq=p2.TARGET_TQ)
    plan.update({'research_db_admission': ADMISSION, 'native_formal_parent_sample': 'PASS', 'formal_hhl_root': str(db_root),
                 'parent_source_archive_sha256': index['archive_sha256'],
                 'parent_tq': p2.TARGET_TQ, 'formal_gen0_eligible': True,
                 'paid_model_authorized': False})
    return plan


def admit_once(audit: dict) -> dict:
    """Explicit gen0 admission into new private HHL path. Never modify trial DB."""
    if os.geteuid() != 0:
        raise Stop('root required')
    _root_directory(RECORDS)
    for existing in RECORDS.iterdir():
        if existing.name.startswith('HHL_P4_FORMAL_GEN0_'):
            raise Stop('existing HHL formal gen0 or incomplete attempt; inspect before any duplicate admission')
    program, archive = make_gen0_spec(audit)
    from openevolve.database import Program, ProgramDatabase
    old_umask = os.umask(0o077)
    root = Path(tempfile.mkdtemp(prefix='HHL_P4_FORMAL_GEN0_', dir=RECORDS))
    os.chmod(root, 0o700)
    db_dir = root / 'db'
    bundles_dir = root / 'bundles'
    evidence_dir = root / 'evidence'
    db_dir.mkdir(mode=0o700)
    bundles_dir.mkdir(mode=0o700)
    evidence_dir.mkdir(mode=0o700)
    try:
        db = ProgramDatabase(_config(db_dir))
        if db.add(Program(**program), iteration=0) is None:
            raise Stop('native OpenEvolve rejected verified HHL seed')
        db.save(str(db_dir), iteration=0)
        _new_file(db_dir, PROFILE, _json_bytes({
            'schema': 1, 'features': ['complexity','diversity'],
            'fitness': '4940400/tq_cost', 'task': 'hhl-angle',
            'admission': ADMISSION, 'source_archive': 'bundles/<program_id>.json',
        }))
        blob = _json_bytes(archive)
        _new_file(bundles_dir, program['id'] + '.json', blob)
        evidence_hashes = {}
        for basename in P1_EVIDENCE_FILES:
            raw = p2.protected_regular_bytes(p2.P1_RECEIPT, basename, maximum=20_000_000)
            evidence_hashes[basename] = _sha(raw)
            _new_file(evidence_dir, basename, raw)
        if evidence_hashes['summary.json'] != audit['receipt_sha256']:
            raise Stop('P1 summary changed between audit and evidence copy')
        evidence_manifest = _json_bytes({
            'schema':'hhl-p4-copied-p1-evidence-v1',
            'source':'P1_NEW_SOURCE_BOUND_TWO_NATIVE_REPLAYS',
            'files':evidence_hashes,
        })
        _new_file(evidence_dir, 'evidence-manifest.json', evidence_manifest)
        _new_file(root, 'index.json', _json_bytes({
            'schema': 'hhl-p4-gen0-index-v1', 'task': 'hhl-angle',
            'admission': ADMISSION, 'program_id': program['id'],
            'entry_sha256': program['metadata']['entry_sha256'],
            'exact_bytes_path_invariant_sha256': program['metadata']['exact_bytes_path_invariant_sha256'],
            'ops_sha256': program['metadata']['ops_sha256'],
            'archive_sha256': _sha(blob),
            'evidence_manifest_sha256': _sha(evidence_manifest),
            'p1_summary_sha256': audit['receipt_sha256'],
            'broker_run_ids': list(audit['broker_ids']),
            'evaluator_event_ids': list(audit['event_ids']),
            'source_at_p1_replay': 'CORRELATED_NEW_P1_EVENT',
            'original_2026_11_06_source': 'NOT_PROVEN',
            'generation': 0, 'parent_id': None, 'source_replays': 2,
            'seed_origin': 'baseline_descendant_P1_reproduced_from_post_session_source',
            'independent_bootstrap_branch': False,
        }))
        sample = sample_audited(root, run_tag='hhl-20261107-next-1111')
        _new_file(root, 'admission-result.json', _json_bytes({
            'schema': 'hhl-p4-admission-result-v1', 'status': 'FORMAL_HHL_GEN0_SAVE_RELOAD_SAMPLE_PASS',
            'program_id': program['id'], 'source_archive_sha256': _sha(blob),
            'evidence_manifest_sha256': _sha(evidence_manifest),
            'native_parent_sample': sample['native_formal_parent_sample'],
            'original_2026_11_06_source': 'NOT_PROVEN',
            'model_calls': 0, 'coulomb_db_writes': 0, 'formal_hhl_gen0_count': 1,
        }))
    except Exception:
        # Keep all evidence for forensic inspection, never silently delete it.
        sys.stderr.write('P4_INCOMPLETE_ROOT_PRIVATE_DIR=' + str(root) + '\n')
        raise
    finally:
        os.umask(old_umask)
    return {'status': 'FORMAL_HHL_GEN0_SAVE_RELOAD_SAMPLE_PASS',
            'db_root': str(root), 'program_id': program['id'],
            'native_parent_sample': sample['native_formal_parent_sample'],
            'full_source_files': len(archive['files_b64']),
            'original_2026_11_06_source': 'NOT_PROVEN',
            'model_calls': 0, 'coulomb_writes': 0, 'generation': 0}


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--audit', action='store_true')
    g.add_argument('--admit-gen0', action='store_true')
    g.add_argument('--sample', type=Path)
    ap.add_argument('--approve-new-hhl-db', action='store_true')
    ap.add_argument('--run-tag', default='hhl-20261107-next-1111')
    args = ap.parse_args()
    try:
        if os.geteuid() != 0:
            raise Stop('root operator required')
        if args.sample:
            if args.approve_new_hhl_db:
                raise Stop('sample does not permit write authorization')
            result = sample_audited(args.sample, run_tag=args.run_tag)
            print('P4_FORMAL_GEN0_NATIVE_SAMPLE=PASS')
            print(json.dumps(result, sort_keys=True, separators=(',', ':')))
            return 0
        if args.admit_gen0 != args.approve_new_hhl_db:
            raise Stop('admission requires BOTH --admit-gen0 --approve-new-hhl-db; no implicit writes')
        audit = p2.audit_receipts(check_original_spools=True)
        _program, desc = make_gen0_spec(audit)
        if args.audit:
            print('P4_P1_NATIVE_PROOF_AUDIT=PASS')
            print('P4_ELIGIBLE_NEW_SOURCE_BOUND_GEN0=YES')
            print('P4_20261106_HISTORICAL_SOURCE=NOT_PROVEN')
            print('P4_NO_DB_WRITES=YES')
            print(json.dumps({'task': desc['task'], 'program_id': desc['program_id'],
                              'source_file_count': len(desc['files_b64']),
                              'p1_summary_sha256': desc['p1_summary_sha256']}, sort_keys=True))
            return 0
        result = admit_once(audit)
        print('P4_FORMAL_GEN0_SAVE_RELOAD_SAMPLE=PASS')
        print(json.dumps(result, sort_keys=True, separators=(',', ':')))
        return 0
    except (OSError, KeyError, TypeError, ValueError, ImportError, RuntimeError,
            PermissionError, AssertionError) as ex:
        print('P4_STOP=' + type(ex).__name__ + ':' + str(ex)[:300], file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
