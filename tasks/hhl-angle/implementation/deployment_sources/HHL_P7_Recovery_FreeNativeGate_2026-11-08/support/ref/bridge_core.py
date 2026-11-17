#!/usr/bin/env python3
"""HHL-only isolated op-stream evaluator core; review prototype, not deployed.

No agent-authored Python is executed here. Evaluates raw op-stream bytes only.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import os
import pathlib
import re
import resource
import signal
import stat
import subprocess
import tempfile

TASK = 'hhl-angle'
AGENT_UID = 997
AGENT_GID = 988
MAX_OPS = 2 * 1024 * 1024  # V4.4.1 single returned-artifact limit
MAX_REQUEST = 4 * ((MAX_OPS + 2) // 3) + 512
MAX_REPLY = 16384
EVAL_TIMEOUT = 90
EVAL_OUTPUT_LIMIT = 65536
REPO = pathlib.Path('/srv/hhl-task2/hhl-evaluator')
EVAL_PY = REPO / 'tools/eval.py'
SOCKET = '/run/hhl-task2/p1-review-evaluator.sock'
SCRATCH = pathlib.Path('/srv/hhl-task2/agent/scratch')
PART_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z', re.ASCII)
FAIL_RE = re.compile(r'[A-Za-z0-9_/-]{1,80}\Z', re.ASCII)

# Pinned source digests from the uploaded HHL evaluator source and the live VM
# native kernel digest supplied on 2026-11-05. A mismatch is a hard stop.
PINNED_EVAL_FILES = {
    'tasks/hhl-angle/contract.json': '7a26017e2f3a48d21007990f9bd215ce1d238c4b4147e00eb4826337b9795ffb',
    'tasks/hhl-angle/grader.py': '10ea83037889414c59ab0d406f317b989fe1f4da9a69c2a52de918aff15dc50f',
    'tools/eval.py': '297fd3f0e17f85b1120dd3daacf3ea87d8e5a680e59baeb188a0d48f0d514a62',
    'uncompute/__init__.py': '746fa96c6e127dcf1ec01c6390271eb522248cc3e0d8bda12371ccb916f3d39e',
    'uncompute/_simfast.py': '174f2039daaff11434f59a536ab980ffa6e0093e6ab133ba783e56fd2a8ad01c',
    'uncompute/_tapefast.py': '23178b4b0be62df5a77b62aee44805e228b36f54df467847bcac90acb5188c9a',
    'uncompute/bennett.py': 'd82a243a2c183cd7ace211d13804445fcde0ff5213b6908247053035201c8570',
    'uncompute/builder.py': '8f850c36b04dd23c901c1029798fc96537f19922211eceab2fefc5d6fd8c91a5',
    'uncompute/classical.py': 'c05c9e7a84a69e3ad0590b972642dcb30c2c29ca2613a821c82d09d53556ba62',
    'uncompute/compact.py': 'bb35392be0e557a7819acac2f29673049304b84997183602adeb5b794b21a3c5',
    'uncompute/evalcore/__init__.py': '1ea037df00ecd37efba4d15dbf864373e58d75b5b9169da175171b307805d51f',
    'uncompute/evalcore/compact_reader.py': 'ba4e531c7c7ff4dc55166107fadb6c7947ff439c98028d71973dc405397b5520',
    'uncompute/evalcore/executor.py': '9825c41d690f31ce6b3f17ce7f95665d48f245f9daea5eb6568659907ce8e17b',
    'uncompute/evalcore/kernel.py': '5e026f15b482f5e212ea189ab90295e7db5dec28144839bb9c1c6381f2dcd64a',
    'uncompute/evalcore/loader.py': '39c9914f6778dab28de45deee499e942bf1fa4951d19077ac2bd501362a20324',
    'uncompute/evalcore/protocol.py': '6526ba2551dc93f13da58a2e35d0b9acf929aa3e35a4603254e3352caa89d7cf',
    'uncompute/evalcore/report.py': '2d7558b21440443188528f142114d570b3b231232780b28c5afa97aff276ad8d',
    'uncompute/evalcore/task.py': '507844009d66e74c191ddce510d4cb71c3939dbc2311dd01279fdd0fc284a68f',
    'uncompute/evalcore/util.py': '8f2c4cb7e12023469a1fabdd0b5ca16e6bae370d91e509f88ae8212bd6c1d8ac',
    'uncompute/gadgets/__init__.py': 'ce5ccbcf60126a58e48284d09af4a13179faea408ab760cf41a284f029202841',
    'uncompute/gadgets/arith.py': '1141bd12c0712c5cc2da56a7201e6b42a7ce7ca110bbaf55e94b9d99b467c30e',
    'uncompute/gadgets/gf_tower.py': 'ebc8fb73be560576d32a9ea502992ee5ab42370e847e631fcc1d192bf18ba072',
    'uncompute/gadgets/linear.py': 'd0b89d494ae63ec28e8bac6f33b344c11aaaab378d0cec81683b1a5743ed25d3',
    'uncompute/gadgets/qrom.py': 'c2d6bea7178cd0fbe5744fbea33801a41f336725a9d85619b871ed7ea7ba972c',
    'uncompute/gadgets/wires.py': '4c0e7a979e884ee1d5c3ef2e74100868348b0dd212212df3ad72c3d60d0f9aa3',
    'uncompute/ops.py': '2f4862c9cbafa5e3b51b5df36007674d5939ad81250a15310da3413c36d6c5af',
    'uncompute/sim.py': '3437096bea97fae75d34eb9b96373fb9febc4b8ecac9635ae6469b3ce93cb848',
    'uncompute/testing/__init__.py': '3ef7e750b6f7f41fb7aa750a7e0efc2e421c31405e76c7edb1fd9631d16e8f3f',
    'uncompute/evalcore/_kernel.so': 'bfe09d35c6c61b4ea832595e5ed3d48cb6635ac1f62a521a2251b856990b689e',
}


def verify_eval_pins(root: pathlib.Path = REPO, pins: dict[str,str] | None = None) -> None:
    """Check ALL expected evaluator bytes, and reject unapproved extra files.

    This checks a root-controlled immutable snapshot, never the user-writable
    working repository. Called at service startup after trust_code_tree.
    """
    if pins is None:
        pins = PINNED_EVAL_FILES
    expected = set(pins)
    if len(expected) != len(pins) or not expected:
        raise Refused("invalid pinned manifest")
    for rel, want in pins.items():
        if rel.startswith("/") or any(v in ("", ".", "..") for v in rel.split("/")):
            raise Refused("invalid manifest path")
        p = root / rel
        st = p.lstat()
        if not stat.S_ISREG(st.st_mode):
            raise Refused(f"unexpected file type: {rel}")
        h = hashlib.sha256()
        with p.open("rb") as f:
            for block in iter(lambda: f.read(65536), b""):
                h.update(block)
        if h.hexdigest() != want:
            raise Refused(f"SHA256 mismatch: {rel}")
    for sub in ("tools", "uncompute", "tasks/hhl-angle"):
        folder = root / sub
        for parent, _dirs, files in os.walk(folder, followlinks=False):
            for name in files:
                rel = (pathlib.Path(parent) / name).relative_to(root).as_posix()
                if rel not in expected:
                    raise Refused(f"unapproved evaluator file: {rel}")



class Refused(ValueError):
    pass


def validate_relpath(value: str) -> list[str]:
    if not isinstance(value, str) or not (1 <= len(value) <= 240) or '\\' in value:
        raise Refused('invalid relative path')
    parts = value.split('/')
    if len(parts) > 8 or any(not PART_RE.fullmatch(p) or p in ('.', '..') or p.startswith('.') for p in parts):
        raise Refused('invalid path component')
    if not (parts[-1].endswith('.ops') or parts[-1].endswith('.bin')):
        raise Refused('raw .ops or .bin required; compressed streams are not accepted')
    return parts


def open_beneath(root_fd: int, rel: str) -> int:
    """Open exact inode below a supplied directory FD; reject all symlinks.

    Inode content is copied into memory before communication; subsequent source
    mutation does not affect the submitted snapshot.
    """
    parts = validate_relpath(rel)
    parent = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                          dir_fd=parent)
            os.close(parent)
            parent = nxt
        return os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                       dir_fd=parent)
    finally:
        os.close(parent)


def read_scratch_ops(rel: str, scratch: pathlib.Path = SCRATCH, *, require_identity=True) -> bytes:
    if require_identity and (os.geteuid(), os.getegid()) != (AGENT_UID, AGENT_GID):
        raise Refused('wrong agent identity')
    if not scratch.is_mount():
        raise Refused('Scratch is not mounted')
    st = scratch.lstat()
    if not stat.S_ISDIR(st.st_mode) or (st.st_uid, stat.S_IMODE(st.st_mode)) != (AGENT_UID, 0o700):
        raise Refused('Scratch ownership/mode mismatch')
    root_fd = os.open(scratch, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        fd = open_beneath(root_fd, rel)
        try:
            s = os.fstat(fd)
            if not stat.S_ISREG(s.st_mode) or not (0 < s.st_size <= MAX_OPS):
                raise Refused('not a nonempty, bounded regular file')
            data = bytearray()
            while len(data) <= MAX_OPS:
                block = os.read(fd, min(65536, MAX_OPS + 1 - len(data)))
                if not block:
                    break
                data.extend(block)
            if not (0 < len(data) <= MAX_OPS):
                raise Refused('op stream exceeds size budget')
            return bytes(data)
        finally:
            os.close(fd)
    finally:
        os.close(root_fd)


def encode_request(ops: bytes) -> bytes:
    if not isinstance(ops, bytes) or not 0 < len(ops) <= MAX_OPS:
        raise Refused('invalid stream size')
    raw = (json.dumps({'schema': 1, 'ops_b64': base64.b64encode(ops).decode('ascii')},
                      separators=(',', ':')) + '\n').encode('ascii')
    if len(raw) > MAX_REQUEST:
        raise Refused('request exceeds budget')
    return raw


def decode_request(raw: bytes) -> bytes:
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_REQUEST or not raw.endswith(b'\n'):
        raise Refused('invalid framing/size')
    try:
        obj = json.loads(raw[:-1].decode('utf-8'))
    except (ValueError, UnicodeError) as e:
        raise Refused('invalid request JSON') from e
    if not isinstance(obj, dict) or set(obj) != {'schema', 'ops_b64'} or type(obj['schema']) is not int or obj['schema'] != 1:
        raise Refused('unsupported request schema')
    encoded = obj['ops_b64']
    if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_OPS + 2) // 3):
        raise Refused('encoded payload exceeds limit')
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as e:
        raise Refused('invalid base64 payload') from e
    if not (0 < len(data) <= MAX_OPS):
        raise Refused('decoded payload exceeds limit')
    return data


def trust_path(path: pathlib.Path) -> None:
    """For deployment: refuse paths whose tree can be replaced by non-root.

    A root-owned parent with group/other write permission is NOT trusted.
    """
    p = path.absolute()
    for node in (p, *p.parents):
        st = node.lstat()
        if stat.S_ISLNK(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
            raise Refused(f'untrusted evaluator path: {node}')


def trust_code_tree(root: pathlib.Path) -> None:
    """Audit *every* entry in a deployed dependency subtree, not just the
    top-level executable. Never follow symlinks during the walk; reject them.
    The snapshot must omit writable caches/venvs and be root-owned throughout.
    """
    trust_path(root)
    if not root.is_dir():
        raise Refused('missing evaluator code directory')
    for parent, dirs, files in os.walk(root, topdown=True, followlinks=False):
        for name in dirs + files:
            node = pathlib.Path(parent) / name
            trust_path(node)
            st = node.lstat()
            if not (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode)):
                raise Refused(f'nonregular evaluator tree entry: {node}')


def verify_trusted_deployment(service_script: pathlib.Path) -> None:
    # Imported Python modules can be different from the entry script. Audit
    # the entire deployed Python package and CLI tree to prevent module swaps.
    for p in (service_script, service_script.parent / 'bridge_core.py',
              service_script.parent / 'agent_client.py',
              EVAL_PY, REPO / 'tasks/hhl-angle/grader.py',
              REPO / 'tasks/hhl-angle/contract.json',
              REPO / 'uncompute/evalcore/_kernel.so'):
        trust_path(p)
    for subtree in (REPO / 'tools', REPO / 'uncompute'):
        trust_code_tree(subtree)
    trust_code_tree(REPO / "tasks/hhl-angle")
    verify_eval_pins(REPO)


def expected_live_shots(ops_sha256: str) -> int:
    """Live HHL shots, not the 9024 attempted draws (1/32 may be skipped).

    The original grader draws 3 bytes for X via per-shot SHAKE256 and skips
    values below 2**16; both seed and per-shot draws depend on raw ops SHA256.
    Do not substitute a rough statistical band for this deterministic check.
    """
    if (type(ops_sha256) is not str or
        re.fullmatch(r'[0-9a-f]{64}', ops_sha256, re.ASCII) is None):
        raise Refused('invalid HHL op digest')
    seed = hashlib.shake_256(b'hhl-angle/tests/' + bytes.fromhex(ops_sha256)).digest(32)
    return sum((int.from_bytes(
        hashlib.shake_256(seed + i.to_bytes(8, 'little')).digest(3), 'little'
    ) & ((1 << 21) - 1)) >= (1 << 16) for i in range(9024))


def _safe_error(code: object, fallback: str) -> str:
    return code if isinstance(code, str) and FAIL_RE.fullmatch(code) else fallback


def _preexec() -> None:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))


def evaluate_ops(ops: bytes, *, evaluator: pathlib.Path = EVAL_PY, timeout: int = EVAL_TIMEOUT,
                 python: str = '/usr/bin/python3') -> dict:
    """Evaluate a byte snapshot with a fixed CLI. Caller must enforce trusted deployment.

    This is an offline-testable core. The production service must run as a
    distinct non-agent UID under systemd MemoryMax/TasksMax/CPUQuota.
    """
    if not isinstance(ops, bytes) or not 0 < len(ops) <= MAX_OPS:
        raise Refused('stream exceeds limit')
    digest = hashlib.sha256(ops).hexdigest()
    common = {'valid': False, 'task': TASK, 'submitted_sha256': digest}
    with tempfile.TemporaryDirectory(prefix='hhl-eval-') as tmp:
        td = pathlib.Path(tmp)
        src = td / 'candidate.ops'  # fixed raw name: never trigger .xz decompression
        src.write_bytes(ops)
        (td / 'out').mkdir()
        env = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'HOME': '/nonexistent',
               'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1',
               'UNCOMPUTE_KERNEL': '1', 'TMPDIR': str(td)}
        argv = [python, '-I', '-B', str(evaluator), TASK, '--ops', str(src),
                '--json', '--jobs', '1', '--out', str(td / 'out')]
        try:
            with (td / 'stdout').open('wb') as fout, (td / 'stderr').open('wb') as ferr:
                proc = subprocess.Popen(argv, cwd=tmp, env=env, stdin=subprocess.DEVNULL,
                                        stdout=fout, stderr=ferr, close_fds=True,
                                        start_new_session=True, preexec_fn=_preexec)
                try:
                    rc = proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
                    return {**common, 'fail_code': 'bridge/evaluator-timeout'}
            if (td / 'stdout').stat().st_size > EVAL_OUTPUT_LIMIT:
                return {**common, 'fail_code': 'bridge/output-too-large'}
            try:
                obj = json.loads((td / 'stdout').read_text('utf-8'))
            except (ValueError, UnicodeError):
                return {**common, 'fail_code': 'bridge/malformed-report'}
            if not isinstance(obj, dict):
                return {**common, 'fail_code': 'bridge/malformed-report'}
            if rc != 0 or obj.get('verdict') != 'OK':
                fail = obj.get('fail')
                code = fail.get('code') if isinstance(fail, dict) else None
                return {**common, 'fail_code': _safe_error(code, 'bridge/evaluator-failed')}
            metrics = obj.get('metrics')
            if not isinstance(metrics, dict) or obj.get('h_ops') != digest:
                return {**common, 'fail_code': 'bridge/report-integrity'}
            tof = metrics.get('toffoli')
            qubits = metrics.get('qubits')
            tq = metrics.get('tq_cost')
            shots = metrics.get('shots')
            if (type(tof) not in (int, float) or type(qubits) is not int or
                type(tq) not in (int, float) or type(shots) is not int or
                shots != expected_live_shots(digest) or not math.isfinite(tof) or
                not math.isfinite(tq) or tof < 0 or qubits < 1 or tq < 0 or
                math.floor(tof) * qubits != tq):
                return {**common, 'fail_code': 'bridge/metrics-integrity'}
            return {**common, 'valid': True, 'verdict': 'OK', 'toffoli': tof,
                    'qubits': qubits, 'tq_cost': tq, 'h_ops': digest,
                    'shots': metrics.get('shots')}
        except (OSError, subprocess.SubprocessError) as e:
            return {**common, 'fail_code': 'bridge/evaluator-error',
                    'error_class': type(e).__name__}
