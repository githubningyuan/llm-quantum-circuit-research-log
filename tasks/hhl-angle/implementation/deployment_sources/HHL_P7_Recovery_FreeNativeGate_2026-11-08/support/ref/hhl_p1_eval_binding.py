#!/usr/bin/env python3
"""HHL P1 STAGING ONLY: opt-in evaluator source-claim protocol and audit.

This is NOT a replacement for the pinned native evaluator, and not a DB
admission authority. V2 records capture an untrusted Agent-selected Broker
run reference at EVALUATION REQUEST RECEIPT time. Only a root operator
reading both independently protected spools may correlate it to frozen bytes.

The old v1 evaluator request and evidence spool remain unchanged and working.
Installing this module is NOT sufficient; a separately reviewed eval client,
service and manifest update are required before enabling v2.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid

import bridge_core as evalcore
import evidence_spool as evspool

HEX32 = re.compile(r'[0-9a-f]{32}\Z', re.ASCII)
HEX64 = re.compile(r'[0-9a-f]{64}\Z', re.ASCII)
OPS_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,74}\.ops\Z', re.ASCII)
EVENT_SCHEMA = 'hhl-eval-spool-v2-broker-claim'
MAX_EVENT = evspool.MAX_EVENT + 512


class BindingRefused(ValueError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _unique_pairs(pairs):
    obj = {}
    for k, v in pairs:
        if k in obj:
            raise BindingRefused('duplicate JSON key')
        obj[k] = v
    return obj


def check_claim(claim: dict) -> dict:
    if type(claim) is not dict or set(claim) != {'broker_run_id', 'broker_artifact', 'ops_sha256'}:
        raise BindingRefused('invalid source claim shape')
    rid, artifact, ops_sha = (claim[k] for k in ('broker_run_id','broker_artifact','ops_sha256'))
    if (type(rid) is not str or not HEX32.fullmatch(rid) or
        type(artifact) is not str or not OPS_NAME.fullmatch(artifact) or
        type(ops_sha) is not str or not HEX64.fullmatch(ops_sha)):
        raise BindingRefused('invalid source claim fields')
    return dict(claim)


def _decode_ops(encoded: str) -> bytes:
    if type(encoded) is not str or len(encoded) > 4 * ((evalcore.MAX_OPS + 2)//3):
        raise BindingRefused('oversized encoded ops')
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as e:
        raise BindingRefused('invalid ops base64') from e
    if not 0 < len(data) <= evalcore.MAX_OPS:
        raise BindingRefused('invalid ops length')
    return data


def encode_bound_request(ops: bytes, broker_run_id: str, broker_artifact: str) -> bytes:
    """Called by a future hhl-agent evaluator CLIENT after it reads literal .ops.

    The client only forwards a CLAIM, never marks it verified. The trusted
    evaluator later computes its own hash of the literal submitted bytes.
    """
    if type(ops) is not bytes or not 0 < len(ops) <= evalcore.MAX_OPS:
        raise BindingRefused('invalid raw ops')
    claim = check_claim({'broker_run_id':broker_run_id,
                         'broker_artifact':broker_artifact, 'ops_sha256':sha(ops)})
    obj = {'schema':2, 'ops_b64':base64.b64encode(ops).decode('ascii'),
           'source_claim':claim}
    raw = (json.dumps(obj, sort_keys=True, separators=(',',':'))+'\n').encode('ascii')
    if len(raw) > evalcore.MAX_REQUEST:
        raise BindingRefused('request budget exceeded')
    return raw


def decode_eval_request(raw: bytes) -> tuple[bytes, dict | None]:
    """Accept C's exact v1 format or strict opt-in v2. No default attribution.

    The evaluator service must call this INSTEAD of core.decode_request only
    after its root-owned code and dependencies have been reviewed and pinned.
    """
    if type(raw) is not bytes or not 0 < len(raw) <= evalcore.MAX_REQUEST or (
        not raw.endswith(b'\n') or raw.count(b'\n') != 1):
        raise BindingRefused('invalid request framing')
    try:
        obj = json.loads(raw[:-1].decode('ascii'), object_pairs_hook=_unique_pairs)
    except (UnicodeError, ValueError) as e:
        raise BindingRefused('malformed request') from e
    if type(obj) is not dict:
        raise BindingRefused('request must be object')
    if obj.get('schema') == 1:
        # Preserve C's current proven v1 validation and scorer unchanged.
        return evalcore.decode_request(raw), None
    if (type(obj.get('schema')) is not int or obj['schema'] != 2 or
        set(obj) != {'schema','ops_b64','source_claim'}):
        raise BindingRefused('unsupported request schema')
    ops = _decode_ops(obj['ops_b64'])
    claim = check_claim(obj['source_claim'])
    if claim['ops_sha256'] != sha(ops):
        raise BindingRefused('source claim does not name submitted raw ops')
    return ops, claim


def provisional_event(ops: bytes, scorer_result: dict, claim: dict, *,
                      event_id: str, received_unix_ns: int,
                      evaluated_unix_ns: int) -> bytes:
    """Build a verifiable CLAIM-ONLY event; native correctness remains C's job.

    received_unix_ns must be sampled immediately after socket receipt and
    BEFORE native scoring. The result is not trusted until persisted by the
    independent hhl-eval process on its 0700 immutable spool.
    """
    if type(event_id) is not str or not HEX32.fullmatch(event_id):
        raise BindingRefused('invalid event id')
    if not (type(received_unix_ns) is int and type(evaluated_unix_ns) is int and
            0 < received_unix_ns <= evaluated_unix_ns):
        raise BindingRefused('invalid timestamp sequence')
    claim = check_claim(claim)
    if claim['ops_sha256'] != sha(ops):
        raise BindingRefused('claim/ops mismatch')
    valid_result = evspool.verified_result(ops, scorer_result)
    obj = {'schema':EVENT_SCHEMA, 'event_id':event_id,
           'binding_status':'UNVERIFIED_BROKER_CLAIM',
           'received_unix_ns':received_unix_ns,
           'evaluated_unix_ns':evaluated_unix_ns,
           'ops_b64':base64.b64encode(ops).decode('ascii'),
           'ops_sha256':sha(ops), 'source_claim':claim,
           'result':valid_result}
    raw = (json.dumps(obj, sort_keys=True, separators=(',',':'))+'\n').encode('ascii')
    if len(raw) > MAX_EVENT:
        raise BindingRefused('oversized evaluator event')
    validate_provisional_event(raw)
    return raw


def validate_provisional_event(raw: bytes) -> tuple[dict, bytes]:
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_EVENT or (
        not raw.endswith(b'\n') or raw.count(b'\n') != 1):
        raise BindingRefused('bad evaluator event framing')
    try:
        obj = json.loads(raw[:-1].decode('ascii'), object_pairs_hook=_unique_pairs)
    except (ValueError, UnicodeError) as e:
        raise BindingRefused('invalid evaluator event JSON') from e
    wanted = {'schema','event_id','binding_status','received_unix_ns',
              'evaluated_unix_ns','ops_b64','ops_sha256','source_claim','result'}
    if type(obj) is not dict or set(obj) != wanted or (
        obj['schema'] != EVENT_SCHEMA or obj['binding_status'] != 'UNVERIFIED_BROKER_CLAIM' or
        type(obj['event_id']) is not str or not HEX32.fullmatch(obj['event_id'])):
        raise BindingRefused('untrusted evaluator event envelope')
    r,e = obj['received_unix_ns'],obj['evaluated_unix_ns']
    if not (type(r) is int and type(e) is int and 0 < r <= e):
        raise BindingRefused('invalid event clock')
    ops = _decode_ops(obj['ops_b64'])
    if type(obj['ops_sha256']) is not str or obj['ops_sha256'] != sha(ops):
        raise BindingRefused('event ops digest mismatch')
    if check_claim(obj['source_claim'])['ops_sha256'] != sha(ops):
        raise BindingRefused('event claim ops mismatch')
    evspool.verified_result(ops, obj['result'])
    return obj,ops


def persist_provisional_event(raw: bytes, *,
                              directory: Path = evspool.SPOOL,
                              owner_uid: int | None = None) -> str:
    """Only hhl-eval may write; same no-replace fsync strategy as C v1.

    Does NOT switch v1 events over; this is for an opt-in v2 socket/service.
    """
    if owner_uid is None:
        import pwd
        owner_uid = pwd.getpwnam('hhl-eval').pw_uid
    if os.geteuid() != owner_uid or owner_uid == 0:
        raise PermissionError('only nonroot hhl-eval may persist claim event')
    obj,_ = validate_provisional_event(raw)
    dfd = evspool._open_spool(directory,owner_uid=owner_uid)
    event_id = obj['event_id']; tmp='.'+event_id+'.tmp'; final=event_id+'.json'
    try:
        fd = os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,
                     0o600,dir_fd=dfd)
        try:
            with os.fdopen(fd,'wb') as f:
                f.write(raw); f.flush(); os.fsync(f.fileno())
            evspool._publish_without_replace(dfd,tmp,final)
            os.fsync(dfd)
        finally:
            try:os.unlink(tmp,dir_fd=dfd)
            except FileNotFoundError:pass
    finally:
        os.close(dfd)
    return event_id


def root_read_claim_event(event_id: str, *, directory: Path = evspool.SPOOL,
                          owner_uid: int) -> tuple[bytes, dict, bytes]:
    """Root-only secure intake of v2 events; v1 C reader remains unchanged."""
    if os.geteuid() != 0:
        raise PermissionError('only root operator may import evaluator events')
    if type(event_id) is not str or not HEX32.fullmatch(event_id):
        raise BindingRefused('bad event id')
    dfd=evspool._open_spool(directory,owner_uid=owner_uid)
    try:
        fd=os.open(event_id+'.json',os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=dfd)
        try:
            st=os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid != owner_uid or st.st_nlink!=1 or (
                stat.S_IMODE(st.st_mode)!=0o600 or not 0<st.st_size<=MAX_EVENT):
                raise BindingRefused('unsafe v2 event inode')
            raw=os.read(fd, MAX_EVENT+1)
            if len(raw)!=st.st_size:
                raise BindingRefused('incomplete v2 event')
        finally: os.close(fd)
    finally: os.close(dfd)
    obj,ops=validate_provisional_event(raw)
    if obj['event_id']!=event_id:
        raise BindingRefused('v2 event filename/id mismatch')
    return raw,obj,ops


def audit_bound_pair(event_raw: bytes, broker_raw: bytes, *,
                     broker_validator) -> dict:
    """Pure correlation, NOT an authenticated file read or formal DB admission.

    The operator MUST first read both records via their respective root-only
    checked inode readers; a caller-provided JSON string is never sufficient.
    """
    event,ops=validate_provisional_event(event_raw)
    broker=broker_validator(broker_raw)
    claim=event['source_claim']
    ok = (broker['broker_run_id'] == claim['broker_run_id'] and
          broker['execution_ok'] is True and
          broker['finished_unix_ns'] <= event['received_unix_ns'] and
          claim['broker_artifact'] in broker['ops_b64'])
    if ok:
        bops=base64.b64decode(broker['ops_b64'][claim['broker_artifact']],validate=True)
        ok = (bops==ops and claim['ops_sha256']==sha(bops))
    if not ok:
        return {'status':'NO_TRUSTED_SOURCE_MATCH',
                'event_id':event['event_id'],
                'ops_sha256':sha(ops),
                'formal_db_eligible':False}
    return {'status':'BROKER_SOURCE_CLAIM_CORRELATED',
            'event_id':event['event_id'],
            'broker_run_id':broker['broker_run_id'],
            'source_bundle_sha256':broker['source_bundle_sha256'],
            'ops_sha256':sha(ops),
            'native_tq':event['result']['tq_cost'],
            'raw_receipt_and_replay_required':True,
            'formal_db_eligible':False}
