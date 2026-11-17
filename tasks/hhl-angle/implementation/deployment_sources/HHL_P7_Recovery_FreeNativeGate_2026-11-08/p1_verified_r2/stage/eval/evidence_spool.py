#!/usr/bin/env python3
"""Staged HHL evaluator-side immutable op/report archive (NOT DEPLOYED).

Invoked only by the hhl-eval socket service AFTER evaluating the literal ops
bytes, BEFORE acknowledging a valid result to an agent. Records are intentionally
UNATTRIBUTED: an event_id alone never proves which research round/source made it.
A separate root operator ingress must establish that binding before using a
snapshot for MAP-Elites. Not an agent-accessible evaluation or host-execution API.
"""
from __future__ import annotations
import base64
import binascii
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import uuid

import bridge_core

SPOOL = Path('/srv/hhl-task2/p1-review-evaluator-spool')
MAX_OPS = 2*1024*1024
MAX_EVENT = 3_500_000
FIELDS = ('task','valid','verdict','submitted_sha256','h_ops','toffoli','qubits','tq_cost','shots')
IDENT = re.compile(r'[0-9a-f]{32}\Z', re.ASCII)

class EvidenceRefused(ValueError):
    pass


def verified_result(ops: bytes, result: dict) -> dict:
    """Check all metrics and every expected live shot before archiving."""
    if type(ops) is not bytes or not 0 < len(ops) <= MAX_OPS or type(result) is not dict:
        raise EvidenceRefused('invalid HHL evaluation input')
    digest = hashlib.sha256(ops).hexdigest()
    if (result.get('task') != 'hhl-angle' or result.get('valid') is not True or
        result.get('verdict') != 'OK' or
        result.get('submitted_sha256') != digest or result.get('h_ops') != digest):
        raise EvidenceRefused('no valid pinned HHL evaluation for the literal ops bytes')
    t=result.get('toffoli')
    if type(t) not in (int,float) or not math.isfinite(t) or t<0:
        raise EvidenceRefused('invalid HHL toffoli metric')
    metrics=[]
    for key in ('qubits','tq_cost','shots'):
        v=result.get(key)
        if type(v) not in (float,int) or not math.isfinite(v) or int(v)!=v:
            raise EvidenceRefused('non-integral HHL evaluator metric')
        metrics.append(int(v))
    q,tq,shots=metrics
    if q<=0 or tq<=0 or shots!=bridge_core.expected_live_shots(digest) or math.floor(t)*q!=tq:
        raise EvidenceRefused('incomplete shots or contradictory metrics')
    return {key: result[key] for key in FIELDS}


def _open_spool(directory: Path, *, owner_uid: int) -> int:
    """The service identity owns its own 0700 spool under root-owned parents."""
    directory=Path(directory)
    parent=directory.parent.lstat()
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid!=0 or parent.st_mode&0o022):
        raise PermissionError('unsafe evaluator event-spool parent')
    fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    st=os.fstat(fd)
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid!=owner_uid or
        stat.S_IMODE(st.st_mode)!=0o700):
        os.close(fd)
        raise PermissionError('unsafe evaluator event-spool owner or mode')
    return fd


def _unique_object(pairs):
    obj={}
    for key,value in pairs:
        if key in obj:raise EvidenceRefused('duplicate event JSON key')
        obj[key]=value
    return obj


def _publish_without_replace(dfd: int, temp_name: str, final_name: str) -> None:
    """Atomic single-link publish via Linux renameat2(RENAME_NOREPLACE).

    Hard-link-then-unlink leaves a recoverability window with nlink=2 when
    interrupted; plain os.rename can silently overwrite an old event. Fail
    closed if this Linux primitive is unavailable.
    """
    libc=ctypes.CDLL(None, use_errno=True)
    try:
        call=libc.renameat2
    except AttributeError as e:
        raise OSError('renameat2 required for immutable event publish') from e
    call.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint]
    call.restype=ctypes.c_int
    if call(dfd,temp_name.encode('ascii'),dfd,final_name.encode('ascii'),1)!=0:
        errno=ctypes.get_errno()
        raise OSError(errno,os.strerror(errno),final_name)


def record_valid_evaluation(ops: bytes, result: dict, *,
                            directory: Path=SPOOL, owner_uid: int|None=None) -> str:
    """No valid=true reply before a durable evidence event has been committed.

    Requires caller to be the pinned, non-agent hhl-eval service. A synthetic
    test may supply an explicit uid matching its own isolated fixture directory.
    """
    if owner_uid is None:
        import pwd
        owner_uid=pwd.getpwnam('hhl-eval').pw_uid
    if os.geteuid()!=owner_uid or os.geteuid()==0:
        # Tests of storage use an isolated test double for the syscall identity;
        # production must never run this service as root. See test helper below.
        raise PermissionError('hhl-eval service identity required')
    canonical=verified_result(ops,result)
    event_id=uuid.uuid4().hex
    payload={'schema':'hhl-eval-spool-v1','event_id':event_id,
             'unattributed':True, 'ops_b64':base64.b64encode(ops).decode('ascii'),
             'result':canonical}
    data=(json.dumps(payload,sort_keys=True,separators=(',',':'))+'\n').encode('ascii')
    if len(data)>MAX_EVENT:
        raise EvidenceRefused('trusted evaluator event too large')
    dfd=_open_spool(directory,owner_uid=owner_uid)
    tmp='.'+event_id+'.tmp'; final=event_id+'.json'
    try:
        out_fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,
                       0o600,dir_fd=dfd)
        try:
            with os.fdopen(out_fd,'wb') as stream:
                stream.write(data);stream.flush();os.fsync(stream.fileno())
            _publish_without_replace(dfd,tmp,final)
            os.fsync(dfd)
        finally:
            try:
                os.unlink(tmp,dir_fd=dfd)
            except FileNotFoundError:
                pass
        return event_id
    finally:
        os.close(dfd)


def read_event(event_id: str, *, directory: Path=SPOOL, owner_uid: int) -> tuple[bytes,dict]:
    """Root audit of immutable evaluators; still UNATTRIBUTED until root binds run."""
    if type(event_id) is not str or not IDENT.fullmatch(event_id):
        raise EvidenceRefused('invalid trusted event id')
    if os.geteuid()!=0:
        raise PermissionError('root required to import evaluator evidence')
    dfd=_open_spool(directory,owner_uid=owner_uid)
    try:
        fd=os.open(event_id+'.json',os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=dfd)
        try:
            st=os.fstat(fd)
            if (not stat.S_ISREG(st.st_mode) or st.st_uid!=owner_uid or
                st.st_nlink!=1 or stat.S_IMODE(st.st_mode)!=0o600 or
                not 0<st.st_size<=MAX_EVENT):
                raise EvidenceRefused('unsafe evaluator evidence inode')
            raw=os.read(fd,MAX_EVENT+1)
            if len(raw)!=st.st_size:
                raise EvidenceRefused('incomplete evaluator evidence')
        finally:
            os.close(fd)
    finally:
        os.close(dfd)
    if not raw.endswith(b'\n') or raw.count(b'\n')!=1:
        raise EvidenceRefused('invalid event framing')
    try:
        obj=json.loads(raw,object_pairs_hook=_unique_object)
        if (set(obj)!={'schema','event_id','unattributed','ops_b64','result'} or
            obj['schema']!='hhl-eval-spool-v1' or obj['event_id']!=event_id or
            obj['unattributed'] is not True):
            raise EvidenceRefused('event evidence envelope mismatch')
        ops=base64.b64decode(obj['ops_b64'],validate=True)
    except (ValueError,TypeError,KeyError,binascii.Error) as e:
        raise EvidenceRefused('invalid persisted evaluator event') from e
    return ops,verified_result(ops,obj['result'])
