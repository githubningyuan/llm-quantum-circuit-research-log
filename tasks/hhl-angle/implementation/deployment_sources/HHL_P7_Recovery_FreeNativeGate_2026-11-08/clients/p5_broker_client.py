#!/usr/bin/env python3
"""Read selected Agent-owned files safely and send them to the restricted v4.1 Broker.

Not yet an OpenCode integration. Run as hhl-agent, never as root.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import socket
import stat
import sys
from pathlib import Path

# -I intentionally removes the script directory from sys.path. Restore ONLY
# this root-owned deployment directory; never add the agent Scratch or CWD.
sys.path.insert(0, '/run/hhl-task2/p1-review-code/broker')
# -I excludes the script's own directory; use only this pinned root-owned path.
sys.path.insert(0, '/run/hhl-task2/p1-review-code/clients')

import rootless_core as core

SCRATCH = Path('/srv/hhl-task2/agent/scratch')
SOCKET = '/run/hhl-task2/p1-review-research.sock'
MAX_REPLY = 12 * 1024 * 1024
MAX_AUTO_NATIVE_EVALUATIONS = 1  # Avoid grading an unbounded batch of Agent artifacts.
OPS_NAME_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,74}\.ops\Z', re.ASCII)
RUN_ID_RE = re.compile(r'[a-f0-9]{32}\Z', re.ASCII)


def _open_beneath(root_fd: int, rel: str) -> int:
    """Reject traversal and symlinks at every level, including final file."""
    parts = core.path_parts(rel)
    parent = os.dup(root_fd)
    try:
        for component in parts[:-1]:
            nxt = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                          dir_fd=parent)
            os.close(parent)
            parent = nxt
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                     dir_fd=parent)
        return fd
    finally:
        os.close(parent)


def read_bundle(entry: str, paths: list[str], scratch: Path=SCRATCH) -> dict[str, bytes]:
    if os.geteuid() != 997:
        raise PermissionError('Agent client must run as hhl-agent')
    if not scratch.is_mount():
        raise RuntimeError('Scratch is not mounted')
    scratch_st = scratch.lstat()
    if (scratch_st.st_uid, stat.S_IMODE(scratch_st.st_mode)) != (997, 0o700):
        raise PermissionError('Unexpected scratch ownership/mode')
    files: dict[str, bytes] = {}
    if entry not in paths or len(paths) != len(set(paths)):
        raise ValueError('Entry must be included and paths unique')
    root_fd = os.open(scratch, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        for rel in paths:
            fd = _open_beneath(root_fd, rel)
            try:
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode) or st.st_size > core.MAX_FILE_BYTES:
                    raise ValueError('Not a regular bounded file')
                content = b''
                while len(content) <= core.MAX_FILE_BYTES:
                    part = os.read(fd, min(65536, core.MAX_FILE_BYTES + 1 - len(content)))
                    if not part: break
                    content += part
                if len(content) > core.MAX_FILE_BYTES:
                    raise ValueError('Input file exceeded limit during read')
                files[rel] = content
            finally:
                os.close(fd)
        core.encode_request(entry, files)
        return files
    finally:
        os.close(root_fd)


def save_artifacts(scratch: Path, artifacts: dict[str, bytes]) -> list[str]:
    """Write to a newly created private directory under scratch using dirfds/O_EXCL."""
    if not artifacts:
        return []
    if len(artifacts) > core.MAX_ARTIFACT_FILES or sum(len(v) for v in artifacts.values()) > core.MAX_ARTIFACT_TOTAL_BYTES:
        raise ValueError('Unbounded artifact response')
    root_fd = os.open(scratch, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        folder='result-' + secrets.token_hex(8)
        os.mkdir(folder, 0o700, dir_fd=root_fd)
        out_fd=os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=root_fd)
        try:
            saved=[]
            for rel,data in artifacts.items():
                parts=core.path_parts(rel)
                if len(data)>core.MAX_ARTIFACT_FILE_BYTES:
                    raise ValueError('Oversized artifact')
                parent=os.dup(out_fd)
                try:
                    for part in parts[:-1]:
                        try: os.mkdir(part, 0o700, dir_fd=parent)
                        except FileExistsError: pass
                        nxt=os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,dir_fd=parent)
                        os.close(parent); parent=nxt
                    fd=os.open(parts[-1],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=parent)
                    try:
                        with os.fdopen(fd,'wb') as f: f.write(data)
                    except Exception: raise
                    saved.append(f'{folder}/{rel}')
                finally:
                    os.close(parent)
            return saved
        finally:
            os.close(out_fd)
    finally:
        os.close(root_fd)




def auto_native_evaluations(reply: dict, outputs: dict[str, bytes], saved: list[str],
                            *, evaluator=None) -> list[dict]:
    """Grade at most one successful Broker .ops, without Agent-generated ID arguments.

    This is a convenience transport, NEVER an admission or trustworthy correlation
    decision. The protected P1 evaluator persists a provisional source claim; the
    independent root operator later reads and matches both immutable spools.
    """
    if not (reply.get('status') == 'completed' and
            reply.get('execution_ok') is True and reply.get('exit_code') == 0):
        return []
    ident = reply.get('broker_provenance_id')
    if type(ident) is not str or not RUN_ID_RE.fullmatch(ident):
        return [{'status': 'REFUSED_NO_BROKER_RECEIPT'}]
    if type(saved) is not list or len(saved) != len(outputs):
        raise ValueError('Broker artifact list changed before grading')
    # Saved artifact paths and Broker output names have corresponding insertion
    # order in save_artifacts(); no guessing from filesystem globbing.
    pairs = list(zip(outputs.items(), saved))
    ops_pairs = [(name, payload, path) for ((name, payload), path) in pairs
                 if OPS_NAME_RE.fullmatch(name)]
    if not ops_pairs:
        return []
    if evaluator is None:
        import p5_eval_client as evaluator
    results = []
    for name, data, path in ops_pairs[:MAX_AUTO_NATIVE_EVALUATIONS]:
        if (type(data) is not bytes or not data.startswith(b'OPSTRM01') or
            type(path) is not str or not path.endswith('/'+name)):
            results.append({'artifact': name, 'status': 'REFUSED_ARTIFACT_MISMATCH'})
            continue
        try:
            native = evaluator.submit(path, ident, name)
            if type(native) is not dict or type(native.get('valid')) is not bool:
                raise ValueError('Malformed native result')
            # Do not forward unrestricted stderr, source bytes, prompts or secrets.
            fields = ('valid','verdict','tq_cost','toffoli_count','peak_qubits',
                      'fail_code','trusted_evidence_id','source_binding_status')
            safe = {k:native[k] for k in fields if k in native and
                    type(native[k]) in (str, int, bool)}
            if native['valid'] and (
                native.get('source_binding_status') != 'UNVERIFIED_BROKER_CLAIM' or
                type(native.get('trusted_evidence_id')) is not str):
                raise ValueError('Native result missing protected provisional receipt')
            results.append({'artifact': name, 'status': 'NATIVE_REPLY',
                            'native': safe, 'formal_db_eligible': False})
        except (OSError, ValueError, RuntimeError, PermissionError, TimeoutError) as exc:
            results.append({'artifact': name, 'status': 'NATIVE_SUBMISSION_FAILED',
                            'error_class': type(exc).__name__,
                            'formal_db_eligible': False})
    if len(ops_pairs) > MAX_AUTO_NATIVE_EVALUATIONS:
        results.append({'status': 'EXTRA_OPS_NOT_AUTO_EVALUATED',
                        'count': len(ops_pairs) - MAX_AUTO_NATIVE_EVALUATIONS})
    return results

def request(entry: str, paths: list[str], *, auto_evaluate: bool=False) -> dict:
    files=read_bundle(entry, paths)
    payload=core.encode_request(entry,files)
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
        s.settimeout(160)
        s.connect(SOCKET)
        s.sendall(payload)
        response=bytearray()
        while len(response)<=MAX_REPLY:
            block=s.recv(min(65536,MAX_REPLY+1-len(response)))
            if not block: break
            response.extend(block)
            if b'\n' in block: break
    if not response.endswith(b'\n') or len(response)>MAX_REPLY:
        raise RuntimeError('Invalid broker response framing/size')
    obj=json.loads(response[:-1].decode('utf-8'))
    if not isinstance(obj,dict):
        raise RuntimeError('Invalid broker response')
    encoded=obj.pop('artifacts',{})
    if not isinstance(encoded,dict):
        raise ValueError('Invalid artifact response')
    outputs={}
    total=0
    for name,val in encoded.items():
        core.path_parts(name)
        if not isinstance(val,str): raise ValueError('Malformed artifact')
        data=base64.b64decode(val,validate=True)
        if len(data)>core.MAX_ARTIFACT_FILE_BYTES: raise ValueError('Oversized artifact')
        total +=len(data)
        if total>core.MAX_ARTIFACT_TOTAL_BYTES or len(outputs)>=core.MAX_ARTIFACT_FILES:
            raise ValueError('Artifact budget exceeded')
        outputs[name]=data
    obj['saved_artifacts']=save_artifacts(SCRATCH, outputs)
    if auto_evaluate:
        obj['auto_native_evaluations']=auto_native_evaluations(obj, outputs, obj['saved_artifacts'])
    return obj


def main() -> None:
    ap=argparse.ArgumentParser()
    ap.add_argument('--auto-native-evaluate',action='store_true')
    ap.add_argument('--entry',required=True)
    ap.add_argument('--file',action='append',required=True,help='Scratch-relative file path; repeat')
    args=ap.parse_args()
    print(json.dumps(request(args.entry,args.file,auto_evaluate=args.auto_native_evaluate),ensure_ascii=False))

if __name__ == '__main__':
    main()
