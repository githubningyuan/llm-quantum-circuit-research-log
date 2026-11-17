#!/usr/bin/env python3
"""P5 hhl-agent-only source-bound native evaluation client.

Fixed root-controlled P1 bridge and socket. No arbitrary code execution.  Requires
both source-proof fields; the independent root reader verifies them afterwards.
"""
from __future__ import annotations
import argparse
import json
import os
import socket
import sys

sys.path.insert(0, '/run/hhl-task2/p1-review-code/eval')
import bridge_core as core
import hhl_p1_eval_binding as binding

SOCKET = '/run/hhl-task2/p1-review-evaluator.sock'

def submit(ops_path: str, run_id: str, artifact: str) -> dict:
    if (os.geteuid(), os.getegid()) != (core.AGENT_UID, core.AGENT_GID):
        raise PermissionError('Expected hhl-agent identity')
    raw_ops = core.read_scratch_ops(ops_path)
    packet = binding.encode_bound_request(raw_ops, run_id, artifact)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(110)
        client.connect(SOCKET)
        client.sendall(packet)
        response = bytearray()
        while len(response) <= core.MAX_REPLY:
            data = client.recv(min(4096, core.MAX_REPLY + 1 - len(response)))
            if not data:
                break
            response.extend(data)
            if b'\n' in data:
                break
    if len(response) > core.MAX_REPLY or response.count(b'\n') != 1 or not response.endswith(b'\n'):
        raise ValueError('Invalid native evaluator reply framing')
    result = json.loads(response[:-1].decode('utf-8'))
    if not isinstance(result, dict) or type(result.get('valid')) is not bool:
        raise ValueError('Invalid native evaluator reply shape')
    # Refuse to tell Agent a positive result without a provisional event receipt.
    if result['valid'] and (result.get('source_binding_status') != 'UNVERIFIED_BROKER_CLAIM'
                            or not isinstance(result.get('trusted_evidence_id'), str)):
        raise ValueError('Source-bound evaluator did not issue a P1 receipt')
    return result

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument('--ops', required=True)
    p.add_argument('--broker-run-id', required=True)
    p.add_argument('--broker-artifact', required=True)
    args = p.parse_args()
    try:
        result = submit(args.ops, args.broker_run_id, args.broker_artifact)
        print(json.dumps(result, ensure_ascii=True, separators=(',', ':')))
        return 0
    except (OSError, ValueError, RuntimeError, TimeoutError, PermissionError) as e:
        print(json.dumps({'valid': False, 'fail_code': 'p5/client-refused',
                          'error_class': type(e).__name__}), flush=True)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
