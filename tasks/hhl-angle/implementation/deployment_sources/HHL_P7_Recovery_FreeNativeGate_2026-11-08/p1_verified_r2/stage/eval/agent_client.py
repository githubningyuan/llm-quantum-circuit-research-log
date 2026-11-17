#!/usr/bin/env python3
"""Agent-side HHL submit client. Run only as hhl-agent via fixed OpenCode tool."""
from __future__ import annotations
import argparse
import json
import os
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve(strict=True).parent))
import bridge_core as core


def submit(path: str) -> dict:
    data = core.read_scratch_ops(path)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(110)
        s.connect(core.SOCKET)
        s.sendall(core.encode_request(data))
        response = bytearray()
        while len(response) <= core.MAX_REPLY:
            block = s.recv(min(4096, core.MAX_REPLY + 1 - len(response)))
            if not block:
                break
            response.extend(block)
            if b'\n' in block:
                break
    if len(response) > core.MAX_REPLY or not response.endswith(b'\n') or response.count(b'\n') != 1:
        raise core.Refused('invalid evaluator response framing')
    result = json.loads(response[:-1].decode('utf-8'))
    if not isinstance(result, dict) or type(result.get('valid')) is not bool:
        raise core.Refused('invalid evaluator response shape')
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--ops', required=True)
    args = ap.parse_args()
    try:
        print(json.dumps(submit(args.ops), separators=(',', ':')))
        return 0
    except (OSError, ValueError, RuntimeError, TimeoutError) as e:
        print(json.dumps({'valid': False, 'fail_code': 'bridge/client-refused',
                          'error_class': type(e).__name__}), flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
