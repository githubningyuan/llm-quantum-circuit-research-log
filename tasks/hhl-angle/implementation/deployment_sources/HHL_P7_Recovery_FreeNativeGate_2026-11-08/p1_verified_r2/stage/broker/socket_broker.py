#!/usr/bin/env python3
"""V4.1 review prototype. Run ONLY as hhl-runner via a systemd-activated socket.

No host shell command and no Docker/Podman API is offered to the agent.
No service installation is performed by this file.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import stat
import struct
import sys
from pathlib import Path

# -I intentionally removes the script directory from sys.path. Restore ONLY
# this root-owned deployment directory; never add the agent Scratch or CWD.
sys.path.insert(0, str(Path(__file__).resolve(strict=True).parent))

import rootless_core as core
import hhl_p1_broker_journal as p1_journal

RUNNER_UID = 995
RUNNER_GID = 987
AGENT_UID = 997
AGENT_GID = 988
SOCKET_PATH = Path('/run/hhl-task2/p1-review-research.sock')
IMAGE_ID = 'sha256:a33c10a97f11005523814c6eee013986119fe41f3122f444518117536675c8e1'
MAX_REPLY = 12 * 1024 * 1024


def policy() -> core.Policy:
    return core.Policy(
        runner_uid=RUNNER_UID, runner_gid=RUNNER_GID, agent_uid=AGENT_UID,
        home=Path('/srv/hhl-task2/rootless/home'),
        runtime=Path('/srv/hhl-task2/rootless/runtime'),
        image=IMAGE_ID,
    )


def peer_identity(conn: socket.socket) -> tuple[int, int, int]:
    return struct.unpack('3i', conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                              struct.calcsize('3i')))


def reply_payload(result: dict) -> bytes:
    data = dict(result)
    data['artifacts'] = {name: base64.b64encode(data_bytes).decode('ascii')
                         for name, data_bytes in result.get('artifacts', {}).items()}
    raw = (json.dumps(data, separators=(',', ':'), ensure_ascii=False) + '\n').encode('utf-8')
    if len(raw) > MAX_REPLY:
        raise ValueError('Response exceeds hard limit')
    return raw


def receive_request(conn: socket.socket) -> bytes:
    conn.settimeout(10)
    buffer = bytearray()
    while len(buffer) <= core.MAX_REQUEST_BYTES:
        chunk = conn.recv(min(32768, core.MAX_REQUEST_BYTES + 1 - len(buffer)))
        if not chunk:
            break
        buffer.extend(chunk)
        if b'\n' in chunk:
            break
    if len(buffer) > core.MAX_REQUEST_BYTES or not buffer.endswith(b'\n'):
        raise ValueError('Invalid request length/framing')
    return bytes(buffer)


def handle_connection(conn: socket.socket, *, runner=core.run_bundle) -> dict:
    # Check identity BEFORE reading any attacker-controlled data.
    _pid, uid, gid = peer_identity(conn)
    if (uid, gid) != (AGENT_UID, AGENT_GID):
        raise PermissionError('Unauthorized caller')
    wire = receive_request(conn)
    entry, files = core.decode_request(wire)
    # Production path: journal the actual execution before returning to the client.
    if runner is core.run_bundle:
        return p1_journal.run_with_journal(policy(), entry, files, wire)
    return runner(policy(), entry, files)


def activated_socket() -> socket.socket:
    if os.environ.get('LISTEN_PID') != str(os.getpid()) or os.environ.get('LISTEN_FDS') != '1':
        raise RuntimeError('Only systemd socket activation is supported')
    if not SOCKET_PATH.parent.is_dir():
        raise RuntimeError('Missing socket directory')
    parent = SOCKET_PATH.parent.lstat()
    if (parent.st_uid, stat.S_IMODE(parent.st_mode)) != (0, 0o755):
        raise RuntimeError('Socket directory must be root-owned 0755')
    socket_stat = SOCKET_PATH.lstat()
    if (socket_stat.st_uid, socket_stat.st_gid, stat.S_IMODE(socket_stat.st_mode)) != (0, AGENT_GID, 0o660):
        raise RuntimeError('Socket ownership/mode mismatch')
    s = socket.socket(fileno=3)
    if s.family != socket.AF_UNIX or s.type != socket.SOCK_STREAM or s.getsockname() != str(SOCKET_PATH):
        raise RuntimeError('Unexpected activated listening socket')
    if not s.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN):
        raise RuntimeError('Socket is not listening')
    return s


def serve() -> None:
    # hhl-runner must NOT have root privileges or share Agent identity.
    core._assert_rootless_policy(policy())
    server = activated_socket()
    with server:
        while True:
            conn, _ = server.accept()
            with conn:
                try:
                    result = handle_connection(conn)
                    response = reply_payload(result)
                except (PermissionError, ValueError, RuntimeError, OSError,
                        json.JSONDecodeError) as exc:
                    response = (json.dumps({'status':'REFUSED','reason':type(exc).__name__},
                                           separators=(',', ':')) + '\n').encode('utf-8')
                try:
                    conn.settimeout(20)
                    conn.sendall(response)
                except (OSError, TimeoutError):
                    pass


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true')
    args=ap.parse_args()
    core._assert_rootless_policy(policy())
    if args.check:
        print(json.dumps({'status':'ROOTLESS_BROKER_PRECHECK_PASS',
                          'runner_uid':RUNNER_UID, 'agent_uid':AGENT_UID,
                          'socket_active':False}))
        return 0
    serve()
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, PermissionError, OSError) as e:
        print(f'REFUSED: {type(e).__name__}: {str(e)[:200]}', file=sys.stderr)
        raise SystemExit(2)
