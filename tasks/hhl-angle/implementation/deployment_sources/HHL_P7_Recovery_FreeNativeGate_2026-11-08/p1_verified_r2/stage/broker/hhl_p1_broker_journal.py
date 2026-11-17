#!/usr/bin/env python3
"""Opt-in HHL P1 broker-side provenance journal.

The existing hhl-runner Broker has already decoded an authenticated hhl-agent
request to immutable in-memory bytes. This module records THOSE exact inputs,
with any .ops output copied back by the pinned rootless Podman execution.
A successful Broker response MUST NOT be emitted if journal persistence fails.

IMPORTANT: This does NOT change the hhl-eval socket, prove which Broker result
an historical evaluation used, or authorize native DB insertion. A trusted
operator must later link distinct provenance/evaluator receipts and replay.
"""
from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import time
import uuid
from typing import Callable

import rootless_core as core

SCHEMA = 'hhl-p1-broker-run-v1'
SPOOL = Path('/srv/hhl-task2/p1-review-broker-spool')
OPS_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,74}\.ops\Z', re.ASCII)
HEX64 = re.compile(r'[0-9a-f]{64}\Z', re.ASCII)
HEX32 = re.compile(r'[0-9a-f]{32}\Z', re.ASCII)
MAX_RECORD_BYTES = 16 * 1024 * 1024
MAX_OPS_BYTES = 2 * 1024 * 1024
DOMAIN = b'HHL_BROKER_EXECUTED_INPUT_BUNDLE_V1\0'


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def bundle_sha(entry: str, files: dict[str, bytes]) -> str:
    """Full input identity including entry name and all bytes, not just main.py."""
    core.decode_request(core.encode_request(entry, files))
    h = hashlib.sha256(DOMAIN)
    e = entry.encode('ascii')
    h.update(struct.pack('>I', len(e))); h.update(e)
    for name, payload in sorted(files.items()):
        n = name.encode('ascii')
        h.update(struct.pack('>I', len(n))); h.update(n)
        h.update(struct.pack('>Q', len(payload))); h.update(payload)
    return h.hexdigest()


def encode_record(*, run_id: str, started_ns: int, finished_ns: int,
                  request: bytes, entry: str, files: dict[str, bytes],
                  result: dict) -> bytes:
    if not HEX32.fullmatch(run_id):
        raise ValueError('Invalid broker run id')
    if not (type(started_ns) is int and type(finished_ns) is int and
            0 < started_ns <= finished_ns):
        raise ValueError('Invalid clock samples')
    if type(result) is not dict:
        raise ValueError('Invalid Broker result')
    actual_entry, actual_files = core.decode_request(request)
    if actual_entry != entry or actual_files != files:
        raise ValueError('Broker request changed before archiving')
    artifacts = result.get('artifacts', {})
    if type(artifacts) is not dict:
        raise ValueError('Invalid output manifest')
    manifest = []
    ops_b64 = {}
    total = 0
    for name, data in sorted(artifacts.items()):
        core.path_parts(name)
        if type(data) is not bytes or len(data) > core.MAX_ARTIFACT_FILE_BYTES:
            raise ValueError('Unsafe Broker artifact')
        total += len(data)
        if total > core.MAX_ARTIFACT_TOTAL_BYTES or len(manifest) >= core.MAX_ARTIFACT_FILES:
            raise ValueError('Artifacts outside Broker budget')
        manifest.append({'path': name, 'sha256': sha(data), 'bytes': len(data)})
        if OPS_RE.fullmatch(name):
            if not 0 < len(data) <= MAX_OPS_BYTES or not data.startswith(b'OPSTRM01'):
                raise ValueError('Bad .ops output from Broker')
            ops_b64[name] = base64.b64encode(data).decode('ascii')
    status = result.get('status')
    successful = (status == 'completed' and result.get('execution_ok') is True and
                  result.get('cleanup_confirmed') is True and result.get('exit_code') == 0 and
                  result.get('snapshot_inputs') is True and result.get('network_disabled') is True and
                  result.get('output_artifacts_supported') is True and
                  result.get('artifact_error') is None and
                  result.get('main_sha256') == sha(files[entry]))
    if not successful and ops_b64:
        # We still retain the result for diagnosis, but these artifacts cannot
        # become admissible evidence from a failed Broker run.
        pass
    obj = {
        'schema': SCHEMA,
        'broker_run_id': run_id,
        'started_unix_ns': started_ns,
        'finished_unix_ns': finished_ns,
        'request_wire_sha256': sha(request),
        'request_b64': base64.b64encode(request).decode('ascii'),
        'source_bundle_sha256': bundle_sha(entry, files),
        'entry': entry,
        'entry_sha256': sha(files[entry]),
        'files_b64': {name: base64.b64encode(data).decode('ascii')
                      for name, data in sorted(files.items())},
        'artifact_manifest': manifest,
        'ops_b64': ops_b64,
        'broker_status': status,
        'execution_ok': bool(successful),
        # This schema does NOT assert an association with an evaluator event.
        'evaluator_event_binding': 'NOT_ESTABLISHED',
        'source_captured_at_broker_execution': True,
    }
    data = (json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode('ascii')
    if len(data) > MAX_RECORD_BYTES:
        raise ValueError('Broker provenance record oversized')
    validate_record(data)
    return data


def validate_record(raw: bytes) -> dict:
    """Content integrity only. Never equate parsed JSON with trusted filesystem provenance."""
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_RECORD_BYTES or raw.count(b'\n') != 1 or not raw.endswith(b'\n'):
        raise ValueError('Invalid record framing')
    def unique(pairs):
        out = {}
        for k, v in pairs:
            if k in out: raise ValueError('Duplicate JSON key')
            out[k] = v
        return out
    obj = json.loads(raw[:-1].decode('ascii'), object_pairs_hook=unique)
    required = {'schema', 'broker_run_id', 'started_unix_ns', 'finished_unix_ns',
                'request_wire_sha256', 'request_b64', 'source_bundle_sha256', 'entry', 'entry_sha256',
                'files_b64', 'artifact_manifest', 'ops_b64', 'broker_status',
                'execution_ok', 'evaluator_event_binding', 'source_captured_at_broker_execution'}
    if type(obj) is not dict or set(obj) != required or obj['schema'] != SCHEMA or (
        type(obj['broker_run_id']) is not str or not HEX32.fullmatch(obj['broker_run_id'])
    ):
        raise ValueError('Wrong provenance envelope')
    files = {}
    if type(obj['files_b64']) is not dict:
        raise ValueError('Malformed source map')
    for name, value in obj['files_b64'].items():
        core.path_parts(name)
        if type(value) is not str:
            raise ValueError('Invalid source base64')
        files[name] = base64.b64decode(value, validate=True)
    if obj['entry'] not in files or bundle_sha(obj['entry'], files) != obj['source_bundle_sha256'] or (
        sha(files[obj['entry']]) != obj['entry_sha256']):
        raise ValueError('Source bundle SHA mismatch')
    if not HEX64.fullmatch(obj['request_wire_sha256']):
        raise ValueError('Bad wire digest')
    if type(obj['request_b64']) is not str:
        raise ValueError('Missing original request')
    wire = base64.b64decode(obj['request_b64'], validate=True)
    if sha(wire) != obj['request_wire_sha256']:
        raise ValueError('Stored request wire digest mismatch')
    e_again, files_again = core.decode_request(wire)
    if e_again != obj['entry'] or files_again != files:
        raise ValueError('Stored request wire differs from source archive')
    if type(obj['ops_b64']) is not dict or type(obj['artifact_manifest']) is not list:
        raise ValueError('Malformed Broker outputs')
    seen = set()
    for row in obj['artifact_manifest']:
        if type(row) is not dict or set(row) != {'path', 'sha256', 'bytes'}:
            raise ValueError('Malformed artifact manifest row')
        path = row['path']
        core.path_parts(path)
        if path in seen or type(row['bytes']) is not int or row['bytes'] < 0 or not HEX64.fullmatch(row['sha256']):
            raise ValueError('Invalid artifact identity')
        seen.add(path)
    missing_ops = {r['path'] for r in obj['artifact_manifest'] if OPS_RE.fullmatch(r['path'])} - set(obj['ops_b64'])
    if missing_ops:
        raise ValueError('Original .ops bytes missing from journal')
    for name, val in obj['ops_b64'].items():
        if name not in seen or not OPS_RE.fullmatch(name) or type(val) is not str:
            raise ValueError('Unexpected ops artifact')
        ops = base64.b64decode(val, validate=True)
        matching = next(x for x in obj['artifact_manifest'] if x['path'] == name)
        if not ops.startswith(b'OPSTRM01') or not 0 < len(ops) <= MAX_OPS_BYTES or (
            sha(ops) != matching['sha256'] or len(ops) != matching['bytes']):
            raise ValueError('Ops hash or length mismatch')
    if (obj['evaluator_event_binding'] != 'NOT_ESTABLISHED' or
        obj['source_captured_at_broker_execution'] is not True or
        type(obj['execution_ok']) is not bool or
        type(obj['started_unix_ns']) is not int or
        type(obj['finished_unix_ns']) is not int or
        not 0 < obj['started_unix_ns'] <= obj['finished_unix_ns']):
        raise ValueError('Unfounded evidence claim or clock')
    return obj


def _open_spool(directory: Path, *, runner_uid: int, parent_uid: int = 0) -> int:
    """Private, sticky-proof directory. No agent can read or alter these records."""
    directory = Path(directory)
    if directory.is_symlink() or directory.parent.is_symlink():
        raise PermissionError('Symlink provenance path')
    st = directory.parent.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != parent_uid or st.st_mode & 0o022:
        raise PermissionError('Unsafe provenance spool parent')
    dfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    s = os.fstat(dfd)
    if not stat.S_ISDIR(s.st_mode) or s.st_uid != runner_uid or stat.S_IMODE(s.st_mode) != 0o700:
        os.close(dfd)
        raise PermissionError('Unsafe provenance spool mode or owner')
    return dfd


def _publish_no_replace(dfd: int, source: str, target: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, 'renameat2', None)
    if rename is None:
        raise OSError('renameat2 required')
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(dfd, source.encode('ascii'), dfd, target.encode('ascii'), 1) != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))


def persist_record(raw: bytes, *, directory: Path, runner_uid: int, parent_uid: int = 0) -> str:
    obj = validate_record(raw)
    if os.geteuid() != runner_uid or runner_uid == 0:
        raise PermissionError('Provenance write must run as nonroot hhl-runner')
    dfd = _open_spool(directory, runner_uid=runner_uid, parent_uid=parent_uid)
    free = os.fstatvfs(dfd)
    if free.f_bavail * free.f_frsize < len(raw) + 512 * 1024 * 1024:
        os.close(dfd)
        raise OSError('Insufficient provenance disk headroom; refusing unarchived run')
    ident = obj['broker_run_id']
    tmp = '.' + ident + '.tmp'
    final = ident + '.json'
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=dfd)
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(raw); f.flush(); os.fsync(f.fileno())
            _publish_no_replace(dfd, tmp, final)
            os.fsync(dfd)
        finally:
            try: os.unlink(tmp, dir_fd=dfd)
            except FileNotFoundError: pass
    finally:
        os.close(dfd)
    return ident


def run_with_journal(policy: core.Policy, entry: str, files: dict[str, bytes],
                     wire: bytes, *, journal: Path = SPOOL,
                     runner: Callable = core.run_bundle) -> dict:
    """Existing core.run_bundle unchanged; durable evidence before any return."""
    if os.geteuid() != policy.runner_uid or policy.runner_uid == 0:
        raise PermissionError('Wrong broker journal identity')
    # Validate once again BEFORE execution; the exact in-memory bytes form the input.
    checked_entry, checked_files = core.decode_request(wire)
    if (entry, files) != (checked_entry, checked_files):
        raise ValueError('Source changed since socket decoding')
    began = time.time_ns()
    result = runner(policy, entry, files)
    finished = time.time_ns()
    raw = encode_record(run_id=uuid.uuid4().hex, started_ns=began,
                        finished_ns=max(began, finished), request=wire,
                        entry=entry, files=files, result=result)
    ident = persist_record(raw, directory=journal, runner_uid=policy.runner_uid)
    # Metadata is downstream diagnostic; operator trusts only the private record.
    return dict(result, broker_provenance_id=ident,
                broker_source_bundle_sha256=bundle_sha(entry, files),
                broker_provenance_status='BROKER_SOURCE_SNAPSHOT_ONLY')


def root_read_record(run_id: str, *, directory: Path = SPOOL,
                     runner_uid: int = 995) -> tuple[bytes, dict]:
    """Root-only verification of an existing Broker record, not a source-event link.

    Requires separate root-owned intake before admission into any HHL DB.
    No creation, deletion, or mutation of spool files. Forbids symlink/hardlink
    substitution and refuses unbounded reads.
    """
    if os.geteuid() != 0:
        raise PermissionError('Operator root required for Broker evidence ingestion')
    if type(run_id) is not str or not HEX32.fullmatch(run_id):
        raise ValueError('Invalid broker run id')
    dfd = _open_spool(directory, runner_uid=runner_uid, parent_uid=0)
    try:
        fd = os.open(run_id + '.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                     dir_fd=dfd)
        try:
            st = os.fstat(fd)
            if (not stat.S_ISREG(st.st_mode) or st.st_uid != runner_uid or
                    st.st_nlink != 1 or stat.S_IMODE(st.st_mode) != 0o600 or
                    not 0 < st.st_size <= MAX_RECORD_BYTES):
                raise PermissionError('Unsafe runner evidence inode')
            raw = os.read(fd, MAX_RECORD_BYTES + 1)
            if len(raw) != st.st_size:
                raise ValueError('Truncated Broker evidence read')
        finally:
            os.close(fd)
    finally:
        os.close(dfd)
    record = validate_record(raw)
    if record['broker_run_id'] != run_id:
        raise ValueError('Broker filename/id mismatch')
    return raw, record
