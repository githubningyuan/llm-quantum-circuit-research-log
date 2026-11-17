#!/usr/bin/env python3
"""Review-only systemd socket-activated evaluator service. NO root execution."""
from __future__ import annotations
import json
import hashlib
import os
import pwd
import socket
import stat
import struct
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve(strict=True).parent))
import bridge_core as core
import evidence_spool
import hhl_p1_eval_binding as p1_binding

# Independently pinned from this staged review package; must be re-reviewed
# with the deployed root-owned runtime before enabling the socket service.
SPOOL_MODULE_SHA256 = "d244d3cd36606e032a37a04dad399f2f51bdd76e8d8a3b37e67bd45ac76452b0"
P1_BINDING_MODULE_SHA256 = "bc47b5ca3417b6fd0c25745112ad889ca2c0a96d92c8d9249a736d5394fbc973"


def peer_identity(conn: socket.socket) -> tuple[int, int, int]:
    return struct.unpack('3i', conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))


def recv_line(conn: socket.socket) -> bytes:
    deadline = time.monotonic() + 10
    data = bytearray()
    while len(data) <= core.MAX_REQUEST:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise core.Refused('request deadline exceeded')
        conn.settimeout(remaining)
        block = conn.recv(min(65536, core.MAX_REQUEST + 1 - len(data)))
        if not block:
            break
        data.extend(block)
        if b'\n' in block:
            break
    if len(data) > core.MAX_REQUEST or not data.endswith(b'\n') or data.count(b'\n') != 1:
        raise core.Refused('bad request framing')
    return bytes(data)


def handle(conn: socket.socket) -> dict:
    _pid, uid, gid = peer_identity(conn)
    if (uid, gid) != (core.AGENT_UID, core.AGENT_GID):
        raise PermissionError('wrong peer identity')
    request = recv_line(conn)
    received_unix_ns = time.time_ns()  # BEFORE evaluating requested bytes
    ops, source_claim = p1_binding.decode_eval_request(request)
    result = core.evaluate_ops(ops)
    if result.get('valid') is not True:
        return result
    # This is BEFORE a valid reply is sent. A spool failure MUST reject this
    # otherwise-good result, not allow a nonrecoverable PASS to escape.
    if source_claim is None:
        # Exact old C path: legacy events remain UNATTRIBUTED.
        event_id = evidence_spool.record_valid_evaluation(ops, result)
        return dict(result, trusted_evidence_id=event_id)
    raw = p1_binding.provisional_event(
        ops, result, source_claim, event_id=uuid.uuid4().hex,
        received_unix_ns=received_unix_ns,
        evaluated_unix_ns=max(received_unix_ns, time.time_ns()))
    event_id = p1_binding.persist_provisional_event(raw)
    # A claim is NEVER equivalent to verified source attribution.
    return dict(result, trusted_evidence_id=event_id,
                source_binding_status='UNVERIFIED_BROKER_CLAIM')


def activated_socket() -> socket.socket:
    if os.environ.get('LISTEN_PID') != str(os.getpid()) or os.environ.get('LISTEN_FDS') != '1':
        raise RuntimeError('systemd socket activation required')
    parent = Path(core.SOCKET).parent
    st = parent.lstat()
    if (st.st_uid, stat.S_IMODE(st.st_mode)) != (0, 0o755):
        raise RuntimeError('unsafe socket parent')
    sst = Path(core.SOCKET).lstat()
    if (sst.st_uid, sst.st_gid, stat.S_IMODE(sst.st_mode)) != (0, core.AGENT_GID, 0o660):
        raise RuntimeError('unsafe socket ownership')
    sock = socket.socket(fileno=3)
    if (sock.family != socket.AF_UNIX or sock.type != socket.SOCK_STREAM or
        sock.getsockname() != core.SOCKET or not sock.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN)):
        raise RuntimeError('unexpected activated socket')
    return sock


def serve() -> None:
    user = pwd.getpwnam('hhl-eval')
    if os.geteuid() != user.pw_uid or os.geteuid() in (0, core.AGENT_UID):
        raise PermissionError('wrong evaluator service identity')
    core.verify_trusted_deployment(Path(__file__).resolve(strict=True))
    spool_module = Path(evidence_spool.__file__).resolve(strict=True)
    if spool_module.parent != Path(__file__).resolve(strict=True).parent:
        raise RuntimeError('Evaluator evidence spool module not in pinned service directory')
    core.trust_path(spool_module)
    if hashlib.sha256(spool_module.read_bytes()).hexdigest() != SPOOL_MODULE_SHA256:
        raise RuntimeError('Evaluator evidence spool source digest mismatch')
    # Stage-only new dependency: must be root-controlled and SHA-pinned.
    p1_module = Path(p1_binding.__file__).resolve(strict=True)
    if p1_module.parent != Path(__file__).resolve(strict=True).parent:
        raise RuntimeError('Unexpected P1 evaluator module directory')
    core.trust_path(p1_module)
    if hashlib.sha256(p1_module.read_bytes()).hexdigest() != P1_BINDING_MODULE_SHA256:
        raise RuntimeError('P1 evaluator binding module digest mismatch')
    with activated_socket() as server:
        while True:
            conn, _ = server.accept()
            with conn:
                try:
                    result = handle(conn)
                except (OSError, ValueError, RuntimeError, PermissionError) as e:
                    result = {'valid': False, 'fail_code': 'bridge/refused',
                              'error_class': type(e).__name__}
                raw = (json.dumps(result, separators=(',', ':')) + '\n').encode('ascii')
                if len(raw) <= core.MAX_REPLY:
                    try:
                        conn.settimeout(5)
                        conn.sendall(raw)
                    except (OSError, TimeoutError):
                        pass


if __name__ == '__main__':
    serve()
