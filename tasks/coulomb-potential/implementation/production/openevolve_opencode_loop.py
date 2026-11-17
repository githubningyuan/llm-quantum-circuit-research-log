#!/usr/bin/env python3
"""Conservative OpenEvolve MAP-Elites -> OpenCode/DeepSeek -> original bridge loop.

No API calls unless BOTH --paid-step and --ack-paid are supplied. This script
never changes opencode.json, the grader, tools, or the original source tree.
Live runs temporarily stage a sampled parent in the *fixed* OpenCode worktree
required by the original quantum_evaluate bridge and ALWAYS restore the prior
working circuit. Parents/offspring live in a separate OpenEvolve database.

The source of truth for validity is the existing Docker-based bridge. The
first-party OpenEvolve database is used for MAP-Elites selection and storage;
its built-in LLM generation is intentionally NOT used.
"""
from __future__ import annotations

import argparse
import re
from contextlib import contextmanager
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid

from openevolve_seed_adapter import audit, seed_database, load_database_with_verified_stats, BASELINE_TQ, EXPECTED_BEST_SHA, MAX_SOURCE_BYTES, require_profile, DEFAULT_FEATURES

MODEL = "deepseek/deepseek-v4-pro"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def private_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def regular_bytes(path: Path, limit: int = MAX_SOURCE_BYTES) -> bytes:
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f"Not a regular file: {path}")
    data = path.read_bytes()
    if not data or len(data) > limit:
        raise ValueError(f"Invalid candidate size: {path}")
    return data


def safe_stage(path: Path, data: bytes, original_mode: int) -> None:
    # Never follow a symlink at the target. Replace the directory entry.
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise RuntimeError("Unsafe candidate parent directory")
    fd, tmpname = tempfile.mkstemp(prefix=".evo-circuit-", dir=path.parent)
    tmp = Path(tmpname)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(tmp, original_mode)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def prompt(parent_tq: int) -> str:
    """Minimal modification of the recovered SUCCESSFUL original OpenCode prompt."""
    return (
        "You are optimizing a reversible quantum circuit for the Coulomb-potential task.\n\n"
        "The working program is quantum-trial/circuit.py.\n\n"
        f"The starting circuit is already verified correct with T×Q = {parent_tq}.\n\n"
        "Your objective is to independently discover and implement a correct circuit with a lower T×Q cost.\n\n"
        "You may inspect the source code and task references under quantum-trial/reference/. "
        "Choose your own algorithm and architecture. You may refactor or completely rewrite circuit.py.\n\n"
        "Make a substantive implementation attempt, then call quantum_evaluate to check correctness and cost. "
        "If it fails, use the feedback to repair the circuit and test again.\n\n"
        "Only edit quantum-trial/circuit.py. The evaluator receives this file together with the "
        "original task data; any new constants should be embedded in circuit.py. "
        "Do not edit reference files, use shell commands, or access published discovered circuits.\n\n"
        "Prioritize producing and testing actual candidate circuits rather than exhaustively "
        "studying the library. You decide the algorithm."
    )


def continuation_prompt(parent_tq: int) -> str:
    """The tested original follow-up prompt, with this run's verified parent cost."""
    return (
        "Continue the unfinished Coulomb circuit optimization. "
        f"The starting circuit is already correct with T×Q = {parent_tq}. "
        "Choose your own algorithm. Implement a substantive change to "
        "quantum-trial/circuit.py and use quantum_evaluate to test it. "
        "Do not edit other files, use shell commands, or investigate unrelated OpenCode files."
    )


def inspect_opencode_session(executable: str, lab: Path, env: dict,
                             title: str, session_id: str | None = None) -> dict:
    """Use the saved, sanitized session as source of truth (NOT stdout or rc=0).

    The unique CLI --title binds a new run to one session. Subsequent runs use
    that exact session ID, avoiding --continue (which can target another run).
    """
    if session_id is None:
        ls = subprocess.run([executable, "session", "list", "--max-count", "50",
                             "--format", "json"], cwd=lab, env=env,
                            capture_output=True, text=True, timeout=35, check=True)
        entries = json.loads(ls.stdout)
        if not isinstance(entries, list):
            raise RuntimeError("Unexpected OpenCode session list format")
        matches = [x for x in entries if isinstance(x, dict) and x.get("title") == title]
        if len(matches) != 1:
            raise RuntimeError(f"Cannot uniquely identify session title {title!r}: {len(matches)} matches")
        item = matches[0]
        directory = item.get("directory")
        if not isinstance(directory, str) or Path(directory).absolute() != lab.absolute():
            raise RuntimeError("Session does not belong to the protected OpenCode worktree")
        session_id = item.get("id")
    if not isinstance(session_id, str) or re.fullmatch(r"ses_[A-Za-z0-9]+", session_id) is None:
        raise RuntimeError("Invalid OpenCode session ID")
    out = subprocess.run([executable, "export", session_id, "--sanitize"],
                         cwd=lab, env=env, capture_output=True, text=True,
                         timeout=60, check=True)
    exported = json.loads(out.stdout)
    messages = exported.get("messages")
    if not isinstance(messages, list):
        raise RuntimeError("Session export missing message list")
    finishes, tools, errors = [], Counter(), []
    for message in messages:
        if not isinstance(message, dict):
            continue
        info = message.get("info") or {}
        if not isinstance(info, dict):
            continue
        if info.get("role") == "assistant":
            if info.get("finish"):
                finishes.append(info["finish"])
            if info.get("error"):
                errors.append(str(info["error"])[:120])
        for part in message.get("parts") or []:
            if isinstance(part, dict) and part.get("type") == "tool":
                tools[str(part.get("tool", "unknown"))] += 1
    if not finishes:
        raise RuntimeError("No final assistant finish reason in exported session")
    return {"session_id": session_id, "last_finish": finishes[-1],
            "tool_counts": dict(tools), "assistant_errors": errors[-3:]}


@dataclass(frozen=True)
class Paths:
    home: Path
    db: Path
    @property
    def lab(self) -> Path: return self.home / "opencode-lab"
    @property
    def candidate(self) -> Path: return self.lab / "quantum-trial/circuit.py"
    @property
    def archive(self) -> Path: return self.home / "quantum-agent/runs/opencode_evaluations"
    @property
    def bridge(self) -> Path: return self.home / "quantum-agent/opencode_eval_bridge.py"
    @property
    def python(self) -> Path: return self.home / "quantum-agent/.venv/bin/python"
    @property
    def lock(self) -> Path: return self.home / "quantum-agent/runs/evolution-pilot/pilot.lock"
    @property
    def runroot(self) -> Path: return self.home / "quantum-agent/runs/openevolve-opencode"


def ensure_db_separate(p: Paths) -> None:
    db = p.db.expanduser().absolute()
    if p.db.is_symlink() or not p.db.is_dir():
        raise ValueError("OpenEvolve database must be a real existing directory")
    for protected in (p.lab, p.home / "quantum-agent", p.home / "uncompute"):
        protected = protected.resolve()
        if db == protected or protected in db.parents or db in protected.parents:
            raise ValueError("OpenEvolve DB overlaps protected project")
    if db.is_symlink():
        raise ValueError("Symlink database not permitted")


def database(p: Paths):
    ensure_db_separate(p)
    require_profile(p.db)
    from openevolve.config import DatabaseConfig
    from openevolve.database import Program, ProgramDatabase
    cfg = DatabaseConfig(db_path=str(p.db), population_size=50, archive_size=50,
                         num_islands=1, feature_dimensions=["complexity", "diversity"],
                         feature_bins=16, random_seed=42)
    db = load_database_with_verified_stats(cfg, ProgramDatabase)
    if not db.programs:
        raise RuntimeError("Empty OpenEvolve database; seed first")
    return Program, db


def validated_metrics(result: dict, code: bytes) -> tuple[int, int, int]:
    if type(result) is not dict or result.get("valid") is not True:
        raise ValueError("Bridge did not validate candidate")
    if result.get("sha256") != digest(code):
        raise ValueError("Bridge SHA mismatch")
    metrics = result.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError("Missing evaluation metrics")
    values = []
    for key in ("toffoli", "qubits", "tq_cost"):
        v = metrics.get(key)
        if type(v) not in (int, float) or not (0 < v < 1e18) or int(v) != v:
            raise ValueError(f"Invalid evaluation metric: {key}")
        values.append(int(v))
    t, q, tq = values
    if t * q != tq:
        raise ValueError("T×Q metric inconsistency")
    return t, q, tq


def snapshot_codes(archive: Path, before: set[str], max_new: int = 24) -> list[tuple[str, bytes, dict]]:
    """Recover saved successful intermediate attempts from this run window."""
    after = [x for x in archive.iterdir() if x.name not in before]
    if len(after) > max_new:
        raise RuntimeError("Unexpectedly many new evaluation snapshots; refuse import")
    seen = set()
    rows = []
    for folder in sorted(after):
        if folder.is_symlink() or not folder.is_dir():
            raise ValueError("Unexpected archive entry")
        rpath, cpath = folder / "result.json", folder / "circuit.py"
        if not rpath.exists() or not cpath.exists():
            continue  # An invalid tool call need not snapshot code.
        result = json.loads(regular_bytes(rpath, 5_000_000))
        if result.get("valid") is not True:
            continue
        code = regular_bytes(cpath)
        validated_metrics(result, code)
        h = digest(code)
        if h not in seen:
            seen.add(h)
            rows.append((folder.name, code, result))
    return rows


def _assert_security(p: Paths) -> tuple[bytes, int]:
    for q in (p.lab / "opencode.json", p.bridge, p.candidate):
        if q.is_symlink() or not q.is_file():
            raise RuntimeError(f"Required file absent or symlinked: {q}")
    # A valid virtualenv/bin/python is often a symlink to the Python binary.
    # Only the interpreter may be a symlink; the protected files above may not.
    if not p.python.is_file():
        raise RuntimeError(f"Evaluator Python missing or broken symlink: {p.python}")
    if p.lab.resolve() != p.lab or p.candidate.parent.resolve() != p.candidate.parent:
        raise RuntimeError("Working directory must not be redirected")
    rawcfg = json.loads((p.lab / "opencode.json").read_text())
    permissions = rawcfg.get("permission", {})
    if (permissions.get("bash") != "deny" or
        permissions.get("external_directory") != "deny" or
        permissions.get("quantum_evaluate") != "allow" or
        permissions.get("read") != "allow"):
        raise RuntimeError("Original OpenCode permission contract changed")
    edit = permissions.get("edit")
    if not isinstance(edit, dict) or edit.get("quantum-trial/reference/**") != "deny":
        raise RuntimeError("Original reference edit boundary changed")
    # Reject unexpected agent permission overrides in project config.
    build = rawcfg.get("agent", {}).get("build", {})
    if isinstance(build, dict) and "permission" in build:
        raise RuntimeError("Agent permission overrides require review")
    code = regular_bytes(p.candidate)
    if digest(code) != EXPECTED_BEST_SHA:
        raise RuntimeError("Live worktree does not match the preserved best circuit SHA; refuse")
    return code, stat.S_IMODE(p.candidate.stat().st_mode)


def orphaned_opencode_process() -> bool:
    """Fail closed for another OpenCode run under this user (not a full system lock)."""
    procroot = Path("/proc")
    if not procroot.is_dir():
        raise RuntimeError("/proc unavailable; cannot exclude concurrent OpenCode")
    for folder in procroot.iterdir():
        if not folder.name.isdigit():
            continue
        try:
            if folder.stat().st_uid != os.getuid():
                continue
            cmdline = (folder / "cmdline").read_bytes().split(b"\0")
        except (PermissionError, FileNotFoundError, ProcessLookupError):
            continue
        if len(cmdline) < 2:
            continue
        exe = os.fsdecode(cmdline[0]).lower()
        if (exe.endswith("/opencode") or exe == "opencode") and b"run" in cmdline[1:]:
            return True
    return False


@contextmanager
def exclusive_lock(lockpath: Path):
    # Existing lock of original controller: prevent two controllers sharing circuit.py.
    if lockpath.is_symlink():
        raise ValueError("Lock file is symlinked")
    lockpath.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lockpath.open("a+") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise RuntimeError("Another evolution controller owns pilot.lock") from e
        yield


def terminate_group(proc: subprocess.Popen, grace: float = 5.0) -> None:
    # Used for normal termination too: a detached descendant in this process
    # group must NOT outlive the controller.
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("Child process group could not be reaped") from e


def run_command(argv: list[str], cwd: Path, env: dict, timeout: int,
                stdout_path: Path, capture_json: bool = False) -> dict | int:
    """Stream OpenCode to private file; terminate its group on interrupt or timeout."""
    with stdout_path.open("wb") as out:
        os.chmod(stdout_path, 0o600)
        proc = subprocess.Popen(argv, cwd=cwd, env=env, start_new_session=True,
                                stdout=subprocess.PIPE if capture_json else out,
                                stderr=subprocess.PIPE if capture_json else subprocess.STDOUT)
        try:
            if capture_json:
                stdout, stderr = proc.communicate(timeout=timeout)
                out.write(stderr[-20000:])
                out.flush()
                if proc.returncode:
                    raise RuntimeError(f"Bridge failed: {stderr[-2000:]!r}")
                return json.loads(stdout)
            rc = proc.wait(timeout=timeout)
            return rc
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"Child process exceeded {timeout}s; process group terminated") from exc
        finally:
            # This executes even on KeyboardInterrupt and timeout. A CLI which
            # exits early may still have descendants; clean up its whole group.
            terminate_group(proc)


def independent_eval(p: Paths, runfolder: Path, idx: int, seconds: int = 180) -> dict:
    env = os.environ.copy()
    env["PWD"] = str(p.home / "quantum-agent")
    return run_command([str(p.python), str(p.bridge)], p.home / "quantum-agent", env,
                       seconds, runfolder / f"independent-{idx}.log", capture_json=True)


def insert_offspring(Program, db, parent_id: str, rows: list[tuple[bytes, dict, str]],
                     generation: int) -> list[dict]:
    accepted = []
    for code, report, snapshot_name in rows:
        t, q, tq = validated_metrics(report, code)
        h = digest(code)
        if h == parent_id or db.get(h) is not None:
            continue
        prog = Program(id=h, code=code.decode("utf-8"), language="python",
                       generation=generation, parent_id=parent_id,
                       changes_description="OpenCode/DeepSeek independently validated offspring",
                       metrics={"combined_score": BASELINE_TQ / tq,
                                "toffoli": float(t), "qubits": float(q), "tq_cost": float(tq)},
                       metadata={"sha256": h, "snapshot": snapshot_name,
                                 "provenance": "exclusive_run_window_plus_independent_regrade"})
        db.add(prog, iteration=generation)
        accepted.append({"sha256": h, "tq_cost": tq, "parent": parent_id})
    if accepted:
        db.save(str(db.config.db_path), iteration=generation)
    return accepted


@contextmanager
def sigterm_as_interrupt():
    old = signal.getsignal(signal.SIGTERM)
    def interrupt(signum, frame):
        raise KeyboardInterrupt("SIGTERM received")
    signal.signal(signal.SIGTERM, interrupt)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, old)


def paid_step(p: Paths, timeout: int = 900, max_grades: int = 8,
              max_continuations: int = 1) -> dict:
    """Explicitly paid. Always recover the original best worktree after the run."""
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise RuntimeError("DEEPSEEK_API_KEY is required")
    if not 120 <= timeout <= 3600:
        raise ValueError("timeout must be 120..3600 seconds")
    if not 1 <= max_grades <= 12:
        raise ValueError("max-grades must be 1..12")
    if not 0 <= max_continuations <= 2:
        raise ValueError("max-continuations must be 0..2")
    with exclusive_lock(p.lock):
        if orphaned_opencode_process():
            raise RuntimeError("An OpenCode run still exists; stop it before this step")
        original, original_mode = _assert_security(p)
        Program, db = database(p)
        parent, _ = db.sample(num_inspirations=0)
        parent_code = parent.code.encode("utf-8")
        if digest(parent_code) != parent.id:
            raise RuntimeError("Selected parent SHA mismatch")
        if not (0 < len(parent_code) <= MAX_SOURCE_BYTES):
            raise RuntimeError("Parent exceeds candidate size limit")
        parent_tq = int(parent.metrics["tq_cost"])
        if float(parent.metrics["combined_score"]) != BASELINE_TQ / parent_tq:
            raise RuntimeError("Parent score does not match T×Q")
        p.runroot.mkdir(parents=True, exist_ok=True, mode=0o700)
        folder = p.runroot / f"{stamp()}-{uuid.uuid4().hex[:10]}"
        folder.mkdir(mode=0o700)
        (folder / "original-circuit.py").write_bytes(original)
        os.chmod(folder / "original-circuit.py", 0o600)
        before = {x.name for x in p.archive.iterdir()} if p.archive.exists() else set()
        summary = {"run": folder.name, "parent_sha256": parent.id,
                   "parent_tq": parent_tq, "original_sha256": digest(original),
                   "returned_to_database": [], "status": "started",
                   "notice": "Snapshot attribution by exclusive run window; not cryptographic OpenCode session proof"}
        private_json(folder / "summary.json", summary)
        # Durable recovery copy lives in folder. A SIGKILL/power failure needs
        # explicit operator-led recovery using --recover; never silently resume.
        active = p.runroot / "active.json"
        if active.exists() or active.is_symlink():
            raise RuntimeError("Unresolved prior active run; no new worktree writes allowed")
        private_json(active, {"folder": str(folder), "sha256": digest(original)})
        try:
            safe_stage(p.candidate, parent_code, original_mode)
            executable = shutil.which("opencode")
            if not executable:
                raise RuntimeError("OpenCode missing from PATH")
            env = os.environ.copy()
            env["PWD"] = str(p.lab)
            # A unique session title makes the saved transcript attributable
            # to this run without exposing its contents or trusting stdout.
            title = "quantum-evolve-" + folder.name
            session_id = None
            final_inspection = None
            started = time.monotonic()
            for attempt in range(max_continuations + 1):
                remaining = timeout - (time.monotonic() - started)
                if remaining < (120 if attempt else 1):
                    summary["continuation_stop_reason"] = "model_time_budget_exhausted"
                    break
                if attempt == 0:
                    cmd = [executable, "run", "--model", MODEL, "--agent", "build",
                           "--title", title, prompt(parent_tq)]
                    logpath = folder / "opencode.log"
                else:
                    cmd = [executable, "run", "--model", MODEL, "--agent", "build",
                           "--session", session_id, continuation_prompt(parent_tq)]
                    logpath = folder / f"opencode-continue-{attempt}.log"
                try:
                    rc = run_command(cmd, p.lab, env, int(remaining), logpath)
                except TimeoutError as model_timeout:
                    # OpenCode has been reaped by run_command. Do NOT discard
                    # previously archived quantum_evaluate snapshots. They are
                    # still individually regraded below before database import.
                    # An interrupted model response has no trustworthy finish
                    # reason, so NEVER attempt an automatic continuation here.
                    summary["model_timeout"] = str(model_timeout)
                    summary["model_turns"] = attempt + 1
                    summary["model_time_budget_exhausted"] = True
                    summary["status"] = "model_timeout_collecting_snapshots"
                    private_json(folder / "summary.json", summary)
                    break
                summary["opencode_exit_code"] = rc
                summary["model_turns"] = attempt + 1
                if rc != 0:
                    summary["status"] = "opencode_failed"
                    return summary
                final_inspection = inspect_opencode_session(executable, p.lab, env,
                                                            title, session_id)
                session_id = final_inspection["session_id"]
                summary["opencode_session_id"] = session_id
                summary["model_last_finish"] = final_inspection["last_finish"]
                summary["model_tool_counts"] = final_inspection["tool_counts"]
                summary["model_errors"] = final_inspection["assistant_errors"]
                private_json(folder / "summary.json", summary)
                if final_inspection["last_finish"] != "length":
                    break
                if attempt == max_continuations:
                    summary["continuation_stop_reason"] = "continuation_cap_reached"
                    break
            if final_inspection is None and "model_timeout" not in summary:
                # No model was started, for example because of a depleted budget.
                summary["status"] = "model_not_started"
                return summary
            # Worktree may contain additional ungraded final edits. Capture it
            # separately, but ONLY import bridge snapshots independently regraded.
            if p.candidate.is_file() and not p.candidate.is_symlink():
                try:
                    final = regular_bytes(p.candidate)
                except ValueError:
                    summary["final_worktree_oversized_or_empty"] = True
                else:
                    (folder / "last-ungraded-candidate.py").write_bytes(final)
                    os.chmod(folder / "last-ungraded-candidate.py", 0o600)
                    summary["final_worktree_sha256"] = digest(final)
            rows = snapshot_codes(p.archive, before)
            summary["successful_snapshots"] = len(rows)
            # Process every unique validated snapshot, in groups bounded by
            # --max-grades. snapshot_codes independently limits new archive
            # entries to 24; never silently truncate or skip candidates.
            # Insert only after ALL independent checks pass (no partial DB save).
            summary["regrade_batches"] = (len(rows) + max_grades - 1) // max_grades
            summary["regrade_batch_limit"] = max_grades
            verified = []
            for offset in range(0, len(rows), max_grades):
                for idx, (snapshot_name, code, _report) in enumerate(
                        rows[offset:offset + max_grades], offset + 1):
                    if digest(code) == parent.id or db.get(digest(code)) is not None:
                        continue
                    safe_stage(p.candidate, code, original_mode)
                    independently_graded = independent_eval(p, folder, idx)
                    validated_metrics(independently_graded, code)
                    verified.append((code, independently_graded, snapshot_name))
            summary["returned_to_database"] = insert_offspring(
                Program, db, parent.id, verified, generation=db.last_iteration + 1)
            # Zero exit code indicates only that the CLI process exited;
            # it does NOT mean that the assistant completed its work.
            if "model_timeout" in summary:
                summary["status"] = ("model_timeout_with_verified_offspring"
                                     if summary["returned_to_database"]
                                     else "model_timeout_no_new_offspring")
            elif summary["model_last_finish"] == "length":
                summary["status"] = "model_length_unfinished"
            elif summary["model_last_finish"] not in ("stop", "tool-calls"):
                summary["status"] = "model_finish_unverified"
            elif not summary["returned_to_database"]:
                summary["status"] = "completed_no_new_offspring"
            else:
                summary["status"] = "completed"
            return summary
        except BaseException as e:
            summary["status"] = "interrupted" if isinstance(e, KeyboardInterrupt) else "failed"
            summary["error"] = type(e).__name__ + ": " + str(e)
            raise
        finally:
            try:
                safe_stage(p.candidate, original, original_mode)
                if digest(regular_bytes(p.candidate)) != digest(original):
                    raise RuntimeError("Failed to restore the original worktree")
                summary["restored_original"] = True
                active.unlink(missing_ok=True)
            except Exception as restore_exc:
                summary["restored_original"] = False
                summary["restoration_error"] = str(restore_exc)
                # Keep active.json and original-circuit.py for manual recovery.
                raise
            finally:
                private_json(folder / "summary.json", summary)


def recover(p: Paths) -> dict:
    """Explicit recovery of original saved worktree after abnormal hard termination."""
    with exclusive_lock(p.lock):
        if orphaned_opencode_process():
            raise RuntimeError("OpenCode process still exists: refuse to restore over it")
        active = p.runroot / "active.json"
        if not active.is_file() or active.is_symlink():
            return {"status": "no_recovery_required"}
        data = json.loads(active.read_text())
        folder = Path(data["folder"])
        if folder.parent != p.runroot or folder.is_symlink():
            raise RuntimeError("Recovery path invalid")
        original = regular_bytes(folder / "original-circuit.py")
        if digest(original) != data["sha256"] or digest(original) != EXPECTED_BEST_SHA:
            raise RuntimeError("Recovery backup hash is not the known best")
        mode = stat.S_IMODE((p.candidate if p.candidate.exists() and not p.candidate.is_symlink() else folder / "original-circuit.py").stat().st_mode)
        safe_stage(p.candidate, original, mode)
        if digest(regular_bytes(p.candidate)) != EXPECTED_BEST_SHA:
            raise RuntimeError("Recovery verification failed")
        active.unlink()
        return {"status": "restored", "sha256": digest(original)}


def cli() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--audit", action="store_true", help="Read-only check of original saved evaluator snapshots")
    mode.add_argument("--seed-db", action="store_true", help="Create a new isolated native OpenEvolve DB; no model calls")
    mode.add_argument("--preview", action="store_true", help="Read native MAP-Elites DB and show sampled parent; no model calls")
    mode.add_argument("--paid-step", action="store_true", help="One actual OpenCode/DeepSeek research session")
    mode.add_argument("--recover", action="store_true", help="Recover preserved best circuit after hard kill")
    ap.add_argument("--db", type=Path, required=True, help="Isolated DB path outside both original project dirs")
    ap.add_argument("--home", type=Path, default=Path.home(), help="Home containing the existing original projects")
    ap.add_argument("--ack-paid", action="store_true", help="Required only for --paid-step; acknowledges API calls and temporary candidate edits")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--max-grades", type=int, default=8)
    ap.add_argument("--max-continuations", type=int, default=1,
                    help="Maximum SAME-SESSION continuations on finish=length (0..2); all share --timeout budget")
    args = ap.parse_args()
    if args.ack_paid and not args.paid_step:
        ap.error("--ack-paid is only valid with --paid-step")
    p = Paths(args.home, args.db)
    try:
        if args.audit:
            _seeds, report = audit(p.archive)
        elif args.seed_db:
            seeds, audit_result = audit(p.archive)
            report = seed_database(seeds, p.db, p.archive)
            report["historical_snapshot_count"] = audit_result["snapshot_count"]
        elif args.preview:
            _Program, db = database(p)
            par, _ = db.sample(num_inspirations=0)
            if digest(par.code.encode()) != par.id:
                raise RuntimeError("MAP-Elites sampled parent integrity failure")
            report = {"model_to_receive_parent": MODEL, "parent_sha256": par.id,
                      "parent_tq": par.metrics.get("tq_cost"),
                      "prompt_first_line": prompt(int(par.metrics["tq_cost"])).splitlines()[0],
                      "island_cells": [len(x) for x in db.island_feature_maps],
                      "program_count": len(db.programs), "mode": "preview_no_api"}
        elif args.recover:
            report = recover(p)
        else:
            if not args.ack_paid:
                ap.error("Paid mode disabled without the explicit --ack-paid flag")
            with sigterm_as_interrupt():
                report = paid_step(p, args.timeout, args.max_grades, args.max_continuations)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except KeyboardInterrupt:
        print("STOP: interrupted and child cleanup requested", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"STOP: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(cli())
