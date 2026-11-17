#!/usr/bin/env python3
"""HHL Rootless research execution CORE -- OFFLINE PROTOTYPE, NOT DEPLOYABLE AS A SERVICE.

This does not expose Docker/Podman to the agent. A future restricted broker accepts
only bounded file bundles from an agent peer whose UID has been verified by SO_PEERCRED.
Run under hhl-runner (unprivileged) with rootless Podman, not as root.
"""
from __future__ import annotations

import base64
import binascii
import fcntl
import hashlib
import json
import io
import tarfile
import os
import re
import secrets
import selectors
import signal
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

MAX_REQUEST_BYTES = 4 * 1024 * 1024
MAX_FILES = 64
MAX_FILE_BYTES = 1 * 1024 * 1024
MAX_TOTAL_FILE_BYTES = 2 * 1024 * 1024
MAX_STDOUT_BYTES = 32 * 1024
MAX_DURATION = 120
MAX_DEPTH = 8
PART = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$')
IMAGE = re.compile(r'^(?:[A-Za-z0-9._/:-]+@sha256:|sha256:)[0-9a-f]{64}$')
CID = re.compile(r'^[0-9a-f]{64}$')
LABEL = 'hhl.research.rootless=v4'
PREFIX = 'hhl-rootless-v4-'
MAX_OUTPUT_ARCHIVE_BYTES = 24 * 1024 * 1024
MAX_ARTIFACT_FILES = 16
MAX_ARTIFACT_FILE_BYTES = 2 * 1024 * 1024
MAX_ARTIFACT_TOTAL_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class Policy:
    runner_uid: int
    runner_gid: int
    agent_uid: int
    home: Path
    runtime: Path
    image: str
    timeout_s: int = 60
    memory_mb: int = 256
    cpus: float = 1.0
    pids: int = 64


def path_parts(path: str, *, script: bool = False) -> tuple[str, ...]:
    if not isinstance(path, str) or not path or len(path) > 240 or '\\' in path or '\x00' in path:
        raise ValueError('Noncanonical relative path')
    parts = path.split('/')
    if len(parts) > MAX_DEPTH or any(p in {'', '.', '..'} or p.startswith('.') or not PART.fullmatch(p) for p in parts):
        raise ValueError('Path contains forbidden component')
    if script and not path.endswith('.py'):
        raise ValueError('Entry point must be a Python file')
    return tuple(parts)


def decode_request(payload: bytes) -> tuple[str, dict[str, bytes]]:
    """Accept exact JSON schema; file contents are base64, not commands or mount options."""
    if not isinstance(payload, bytes) or len(payload) > MAX_REQUEST_BYTES or not payload.endswith(b'\n'):
        raise ValueError('Invalid request framing/size')
    obj = json.loads(payload[:-1].decode('utf-8'))
    if not isinstance(obj, dict) or set(obj) != {'version', 'operation', 'entry', 'files'}:
        raise ValueError('Unexpected request fields')
    if type(obj['version']) is not int or obj['version'] != 1 or obj['operation'] != 'run_python':
        raise ValueError('Unsupported protocol')
    entry = obj['entry']
    path_parts(entry, script=True)
    files = obj['files']
    if not isinstance(files, dict) or not 1 <= len(files) <= MAX_FILES or entry not in files:
        raise ValueError('Missing files or entry')
    out: dict[str, bytes] = {}
    total = 0
    for path, encoded in files.items():
        path_parts(path)
        if not isinstance(encoded, str) or len(encoded) > 4 * (MAX_FILE_BYTES // 3 + 2):
            raise ValueError('Oversized/invalid file encoding')
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError('Invalid base64 data') from exc
        if len(raw) > MAX_FILE_BYTES:
            raise ValueError('Per-file size exceeded')
        total += len(raw)
        if total > MAX_TOTAL_FILE_BYTES:
            raise ValueError('Total file size exceeded')
        out[path] = raw
    return entry, out


def encode_request(entry: str, files: dict[str, bytes]) -> bytes:
    """Client-side helper; same strict checks also run server-side."""
    obj = {'version': 1, 'operation': 'run_python', 'entry': entry,
           'files': {k: base64.b64encode(v).decode('ascii') for k, v in files.items()}}
    payload = (json.dumps(obj, separators=(',', ':')) + '\n').encode('utf-8')
    decode_request(payload)
    return payload


def _assert_rootless_policy(policy: Policy) -> None:
    if os.geteuid() != policy.runner_uid or policy.runner_uid in {0, policy.agent_uid}:
        raise PermissionError('Must run as distinct, unprivileged hhl-runner')
    if os.getegid() != policy.runner_gid:
        raise PermissionError('Unexpected runner GID')
    if not IMAGE.fullmatch(policy.image):
        raise ValueError('Image MUST be pinned by digest')
    if not (1 <= policy.timeout_s <= MAX_DURATION and 128 <= policy.memory_mb <= 2048 and
            0 < policy.cpus <= 2 and 8 <= policy.pids <= 64):
        raise ValueError('Resource limits out of range')
    for path in (policy.home, policy.runtime):
        if not path.is_absolute() or path.is_symlink():
            raise ValueError('Untrusted runner path')
        st = path.stat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != policy.runner_uid or st.st_mode & 0o077:
            raise ValueError('Runner-owned private directory 0700 required')
    if policy.runtime == policy.home or policy.home in policy.runtime.parents:
        raise ValueError('Runtime must be separately managed')


def rootless_env(policy: Policy) -> dict[str, str]:
    return {
        'HOME': str(policy.home),
        'XDG_RUNTIME_DIR': f'/run/user/{policy.runner_uid}',
        'DBUS_SESSION_BUS_ADDRESS': f'unix:path=/run/user/{policy.runner_uid}/bus',
        'PATH': '/usr/bin:/bin', 'LANG': 'C',
    }


def materialize_bundle(target: Path, files: dict[str, bytes]) -> None:
    """Write validated files to fresh private runtime; no symlinks supplied by agent.

    target is a newly created 0700 directory under runner's private runtime.
    """
    if target.is_symlink() or target.stat().st_mode & 0o077:
        raise ValueError('Bundle directory must be private')
    for path, data in files.items():
        parts = path_parts(path)
        dest = target.joinpath(*parts)
        dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if dest.parent.is_symlink() or dest.exists():
            raise ValueError('Duplicated or symlink bundle path')
        # All names and directories came from the validated request and this
        # directory is private to the runner identity.
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(data)
        except Exception:
            raise
    # Within rootless Podman keep-id, host runner UID may read these 0600 files.


def podman_create(policy: Policy, *, container: str, bundle_dir: Path) -> list[str]:
    """No host shell or dynamic mounts/flags from LLM. Agent code runs in container."""
    return [
        '/usr/bin/podman', 'create', '--pull=never', '--name', container,
        '--label', LABEL, '--network=none', '--read-only', '--cap-drop=all',
        '--security-opt=no-new-privileges', '--pids-limit', str(policy.pids),
        '--memory', f'{policy.memory_mb}m', '--memory-swap', f'{policy.memory_mb}m',
        '--cpus', str(policy.cpus), '--userns=keep-id',
        '--user', f'{policy.runner_uid}:{policy.runner_gid}',
        '--ipc=private', '--workdir=/work', '--log-driver=none',
        '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=32m,mode=1777',
        '--tmpfs', '/work:rw,nosuid,nodev,noexec,size=64m,mode=1777',
        '--mount', f'type=bind,src={bundle_dir},dst=/input,readonly',
        '--env', 'HOME=/tmp', '--env', 'PYTHONDONTWRITEBYTECODE=1',
        '--env', 'PYTHONNOUSERSITE=1', policy.image,
        # Keep the container running while the agent's program executes via
        # podman exec. A stopped container loses its /work tmpfs, so copying
        # outputs after start -a cannot work (v4.1 regression).
        'python', '-I', '-B', '-c', 'import time;time.sleep(3600)',
    ]


def podman_execute(policy: Policy, *, container: str, entry: str) -> list[str]:
    """Fixed exec command; entry is a validated relative .py path, not a shell."""
    path_parts(entry, script=True)
    return [
        '/usr/bin/podman', 'exec',
        '--user', f'{policy.runner_uid}:{policy.runner_gid}',
        container, 'python', '-I', '-B', '-c',
        'import runpy,sys,os;os.makedirs("/work/outputs",exist_ok=True);sys.path.insert(0,"/input");'
        'p="/input/"+sys.argv[1];runpy.run_path(p,run_name="__main__")',
        entry,
    ]


def _bounded(cmd: list[str], env: dict[str, str], *, seconds: float) -> tuple[int, str, str, bool]:
    """Concurrent pipe draining, time limit, process-group kill."""
    p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         start_new_session=True)
    sel = selectors.DefaultSelector()
    streams = {'stdout': bytearray(), 'stderr': bytearray()}
    timed_out = False
    try:
        for name, pipe in (('stdout', p.stdout), ('stderr', p.stderr)):
            sel.register(pipe, selectors.EVENT_READ, name)
        deadline = time.monotonic() + seconds
        while sel.get_map():
            remain = deadline - time.monotonic()
            if remain <= 0:
                timed_out = True
                break
            for key, _ in sel.select(min(remain, 0.2)):
                chunk = os.read(key.fileobj.fileno(), 8192)
                if not chunk:
                    sel.unregister(key.fileobj)
                else:
                    buf = streams[key.data]
                    buf.extend(chunk[:max(0, MAX_STDOUT_BYTES - len(buf))])
        if not timed_out:
            try:
                rc = p.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                rc, timed_out = -1, True
        else:
            rc = -1
    finally:
        sel.close()
        if timed_out or p.poll() is None:
            try: os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError: pass
        try: p.wait(timeout=3)
        except subprocess.TimeoutExpired: pass
        for pipe in (p.stdout, p.stderr):
            if pipe: pipe.close()
    return rc, streams['stdout'].decode('utf-8', 'replace'), streams['stderr'].decode('utf-8', 'replace'), timed_out


def _simple(cmd: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=10, check=False)


def existing_containers(env: dict[str, str]) -> list[str]:
    result = _simple(['/usr/bin/podman', 'ps', '-aq', '--filter', f'label={LABEL}'], env)
    if result.returncode != 0:
        raise RuntimeError('Rootless Podman state unavailable')
    return result.stdout.split()


def cleanup(container: str, env: dict[str, str]) -> bool:
    """Remove only the named container; verify absence even if removal errors.

    Podman 4.9 `rm -f` defaults to waiting up to 10 s for graceful stop.
    With a live PID 1 (our artifact-export holder), a 10-s subprocess
    timeout races Podman's own stop timeout. `--time=0` requests an
    immediate SIGKILL for THIS container, not a global prune.

    Do not treat command success or a timeout as proof of cleanup. The
    independent `container exists` exit status is authoritative here:
    1=absent; 0=still present; 125=indeterminate/failure.
    """
    for attempt in range(3):
        try:
            _simple(['/usr/bin/podman', 'rm', '-f', '--time=0', container], env)
        except subprocess.TimeoutExpired:
            # The command may have completed removal just before our timeout.
            pass
        try:
            result = _simple(['/usr/bin/podman', 'container', 'exists', container], env)
        except subprocess.TimeoutExpired:
            result = None
        if result is not None and result.returncode == 1:
            return True
        if attempt < 2:
            time.sleep(0.2)
    return False


def _capture_tar_bytes(cmd: list[str], env: dict[str, str], seconds: int = 20) -> bytes:
    """Run Podman cp, accept at most 24 MiB, and kill the process group on failure."""
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=True)
    sel = selectors.DefaultSelector()
    streams = {'stdout': bytearray(), 'stderr': bytearray()}
    deadline = time.monotonic() + seconds
    try:
        for name, pipe in (('stdout', proc.stdout), ('stderr', proc.stderr)):
            sel.register(pipe, selectors.EVENT_READ, name)
        while sel.get_map():
            if time.monotonic() >= deadline:
                raise RuntimeError('Podman artifact export timeout')
            for key, _ in sel.select(min(0.2, max(0.0, deadline - time.monotonic()))):
                chunk = os.read(key.fileobj.fileno(), 8192)
                if not chunk:
                    sel.unregister(key.fileobj)
                    continue
                buf = streams[key.data]
                limit = MAX_OUTPUT_ARCHIVE_BYTES if key.data == 'stdout' else 4096
                if len(buf) + len(chunk) > limit:
                    raise RuntimeError('Podman artifact archive/output oversized')
                buf.extend(chunk)
        rc = proc.wait(timeout=max(0.001, deadline - time.monotonic()))
        if rc != 0:
            raise RuntimeError('Podman artifact copy failed: ' + streams['stderr'].decode('utf-8','replace')[:200])
        return bytes(streams['stdout'])
    finally:
        sel.close()
        if proc.poll() is None:
            try: os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError: pass
        try: proc.wait(timeout=3)
        except subprocess.TimeoutExpired: pass
        for pipe in (proc.stdout, proc.stderr):
            if pipe: pipe.close()


def parse_output_archive(blob: bytes) -> dict[str, bytes]:
    """Parse Podman cp archive without extracting anything to host filesystem.

    Only regular files in /work/outputs are returned; links/devices/traversal fail closed.
    """
    if len(blob) > MAX_OUTPUT_ARCHIVE_BYTES:
        raise ValueError('Output archive too large')
    outputs: dict[str, bytes] = {}
    total = 0
    with tarfile.open(fileobj=io.BytesIO(blob), mode='r:') as archive:
        for member in archive:
            name = member.name
            if name in {'.', './'}:
                if not member.isdir():
                    raise ValueError('Invalid output archive root')
                continue
            if name.startswith('./'):
                name = name[2:]
            if member.isdir():
                path_parts(name.rstrip('/'))
                continue
            path_parts(name)
            if not member.isfile() or name in outputs or member.size > MAX_ARTIFACT_FILE_BYTES:
                raise ValueError('Unsafe output artifact')
            total += member.size
            if len(outputs) >= MAX_ARTIFACT_FILES or total > MAX_ARTIFACT_TOTAL_BYTES:
                raise ValueError('Output artifact budget exceeded')
            source = archive.extractfile(member)
            if source is None:
                raise ValueError('Unreadable output artifact')
            data = source.read(MAX_ARTIFACT_FILE_BYTES + 1)
            if len(data) != member.size:
                raise ValueError('Output artifact size mismatch')
            outputs[name] = data
    return outputs


def collect_outputs(container: str, env: dict[str, str]) -> dict[str, bytes]:
    tar_bytes = _capture_tar_bytes(
        ['/usr/bin/podman', 'cp', f'{container}:/work/outputs/.', '-'], env)
    return parse_output_archive(tar_bytes)


def run_bundle(policy: Policy, entry: str, files: dict[str, bytes]) -> dict[str, object]:
    """Operator pilot runner; next phase must add a secure Unix socket service.

    Podman is explicitly rootless. No host-root runner, Docker daemon or socket.
    """
    _assert_rootless_policy(policy)
    # Apply the same input checks even if called without JSON transport.
    decode_request(encode_request(entry, files))
    env = rootless_env(policy)
    started = time.monotonic()
    lock = policy.runtime / '.run.lock'
    lfd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(lfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as e:
        os.close(lfd)
        raise RuntimeError('Another tool execution is already active') from e
    try:
        if existing_containers(env):
            raise RuntimeError('Orphan container requires operator inspection')
        with tempfile.TemporaryDirectory(prefix='bundle-', dir=policy.runtime) as work:
            target = Path(work)
            materialize_bundle(target, files)
            # Agent files are immutable in snapshot after input bytes have arrived.
            name = PREFIX + secrets.token_hex(12)
            cmd = podman_create(policy, container=name, bundle_dir=target)
            status, stdout, stderr, rc = 'create_failed', '', '', -1
            timed_out = False
            create_timed_out = False
            artifacts: dict[str, bytes] = {}
            artifact_error = None
            try:
                c_rc, c_out, c_err, create_timed_out = _bounded(cmd, env, seconds=20)
                if create_timed_out:
                    status, stderr = 'create_timeout', 'Podman create timed out; operator must check for late container'
                elif c_rc or not CID.fullmatch(c_out.strip()):
                    status, stderr = 'create_failed', c_err
                else:
                    # Start detached; the container's PID 1 remains alive while
                    # the program is executed via a separate Podman exec.
                    start_rc, _start_out, start_err, start_timed_out = _bounded(
                        ['/usr/bin/podman', 'start', c_out.strip()], env, seconds=20)
                    if start_timed_out:
                        status, stderr = 'start_timeout', 'Podman start timed out'
                    elif start_rc:
                        status, stderr = 'start_failed', start_err
                    else:
                        rc, stdout, stderr, timed_out = _bounded(
                            podman_execute(policy, container=name, entry=entry), env,
                            seconds=policy.timeout_s + 5)
                        status = 'timeout' if timed_out else ('completed' if rc == 0 else 'failed')
                        if not timed_out:
                            # Podman cp can access tmpfs only while running.
                            # If untrusted code killed PID 1, export fails closed.
                            try:
                                artifacts = collect_outputs(name, env)
                            except (ValueError, RuntimeError, OSError, tarfile.TarError) as exc:
                                artifact_error = type(exc).__name__ + ': ' + str(exc)[:200]
                                status = 'artifact_rejected'
            finally:
                cleaned = cleanup(name, env)
            if create_timed_out:
                cleaned = False
            if not cleaned and status != 'create_timeout':
                status = 'cleanup_unverified'
            return {
                'status': status,
                'execution_ok': status == 'completed' and cleaned,
                'cleanup_confirmed': cleaned, 'exit_code': rc,
                'stdout': stdout, 'stderr': stderr, 'elapsed_s': round(time.monotonic()-started, 3),
                'main_sha256': hashlib.sha256(files[entry]).hexdigest(),
                'snapshot_inputs': True, 'network_disabled': True,
                'output_artifacts_supported': True, 'artifacts': artifacts,
                'artifact_error': artifact_error,
            }
    finally:
        os.close(lfd)
