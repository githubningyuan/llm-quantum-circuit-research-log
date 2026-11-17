#!/usr/bin/env python3
"""Small, fail-closed paid batch wrapper around the *unchanged* verified v6.2 Coulomb controller.

Read-only --check by default. --run requires --ack-paid, and runs a finite number
of unchanged v6.2 --paid-step subprocesses sequentially. No OpenEvolve internals,
OpenCode settings, quantum evaluator, or existing circuits are edited here.
"""
from __future__ import annotations

import argparse
import fcntl
import math
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from contextlib import contextmanager

EXPECTED_WORKTREE_SHA = "d242f66d35d7fc8b78dfe5a1e76178ca155f6c575df78e1e53ed5062d7157625"
EXPECTED_NEW_BEST_SHA = "404581820f42e015e9b86e68c02301916f30b519dc1f303bd597315df97bc847"
EXPECTED_NEW_BEST_TQ = 577700
PROVEN_OFFSPRING = (
    "64ffddfe4c0156d407546c7876be17cb04ff9fc82476f60ad031b641d1616392",
    "5ca8e805d20d0fc564fd1f947a71f76eec312cef024e79dd9f28b44ad374e98b",
    EXPECTED_NEW_BEST_SHA,
)
OK_STATUSES = frozenset((
    "completed", "completed_no_new_offspring", "model_timeout_with_verified_offspring",
    "model_timeout_no_new_offspring",
))


def timestamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sha_file(path: Path):
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"Protected circuit not a regular file: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def append_event(path: Path, data: dict):
    """Tiny append-only event log. Never reads model state, calls an API, or polls files."""
    if path.is_symlink():
        raise RuntimeError("STOP: event log cannot be a symlink")
    encoded = (json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "ab") as f:
        f.write(encoded)
        f.flush()
        os.fsync(f.fileno())


def positive_metric(value, name: str):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise RuntimeError(f"STOP: invalid {name} in verified controller report: {value!r}")
    return value


def round_progress(inner: dict, before: dict, after: dict,
                   min_parent_improvement_pct: float = 1.0) -> dict:
    """Derive cheap per-round research stats solely from saved controller + DB reports."""
    prev = positive_metric(before.get("db_best_tq"), "pre-round best TQ")
    now = positive_metric(after.get("db_best_tq"), "post-round best TQ")
    if now > prev:
        raise RuntimeError(f"STOP: MAP-Elites best regressed during round: {prev} -> {now}")
    children = inner.get("returned_to_database")
    if not isinstance(children, list):
        raise RuntimeError("STOP: missing returned_to_database array in v6.2 summary")
    costs = []
    for child in children:
        if not isinstance(child, dict) or not isinstance(child.get("sha256"), str) or len(child["sha256"]) != 64:
            raise RuntimeError("STOP: malformed verified offspring in v6.2 summary")
        costs.append(positive_metric(child.get("tq_cost"), "offspring TQ"))
    parent = inner.get("parent_tq")
    if parent is not None:
        positive_metric(parent, "parent TQ")
    if not isinstance(inner.get("parent_sha256"), str):
        raise RuntimeError("STOP: parent SHA256 missing in v6.2 summary")
    best_child = min(costs) if costs else None
    retained_shas = after.get("retained_new_offspring_sha256")
    if retained_shas is None:
        # Old report readers can still derive the raw improvement metrics, but
        # progress-based stop logic MUST NOT assume persistence without DB evidence.
        retained_costs = []
        persistence_checked = False
    else:
        if not isinstance(retained_shas, list) or any(not isinstance(s, str) for s in retained_shas):
            raise RuntimeError("STOP: invalid retained-offspring audit in DB report")
        submitted_shas = {c["sha256"] for c in children}
        if len(set(retained_shas)) != len(retained_shas) or not set(retained_shas).issubset(submitted_shas):
            raise RuntimeError("STOP: retained-offspring audit has unexpected hashes")
        retained_costs = [positive_metric(c["tq_cost"], "retained offspring TQ")
                          for c in children if c["sha256"] in retained_shas]
        persistence_checked = True
    best_retained = min(retained_costs) if retained_costs else None
    improvement_pct = (100.0 * (parent - best_retained) / parent
                       if parent is not None and best_retained is not None else None)
    parent_progress = bool(improvement_pct is not None
                           and improvement_pct > 0
                           and improvement_pct >= min_parent_improvement_pct)
    new_best = now < prev
    return {
        "parent_sha256": inner["parent_sha256"],
        "parent_tq": parent,
        "offspring_count": len(costs),
        "best_offspring_tq": best_child,
        "parent_improved_by_child": bool(best_child is not None and parent is not None and best_child < parent),
        "retained_offspring_audited": persistence_checked,
        "retained_offspring_count": len(retained_costs) if persistence_checked else None,
        "best_retained_offspring_tq": best_retained,
        "retained_parent_improvement_pct": improvement_pct,
        "min_parent_improvement_pct": min_parent_improvement_pct,
        "meaningful_parent_progress": parent_progress,
        "made_research_progress": new_best or parent_progress,
        "db_best_tq_before": prev,
        "db_best_tq_after": now,
        "new_global_best": new_best,
        "global_best_tq_reduction": prev - now,
        "model_turns": inner.get("model_turns"),
        "successful_snapshots": inner.get("successful_snapshots"),
    }


def load_v62(path: Path):
    if path.is_symlink() or not path.is_file() or path.name != "openevolve_opencode_loop.py":
        raise RuntimeError("The existing v6.2 controller must be a regular source file")
    # The adapter is beside v6.2 and is imported by its controller.
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("coulomb_unchanged_v62", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load existing v6.2 controller")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def read_preflight(mod, home: Path, dbpath: Path, require_historical: bool = True,
                   offspring_hashes: tuple[str, ...] = ()) -> dict:
    p = mod.Paths(home, dbpath)
    # Do not edit protected files or overwrite an already running job.
    original, _mode = mod._assert_security(p)
    if hashlib.sha256(original).hexdigest() != EXPECTED_WORKTREE_SHA:
        raise RuntimeError("Protected worktree unexpectedly changed")
    if (p.runroot / "active.json").exists() or (p.runroot / "active.json").is_symlink():
        raise RuntimeError("STOP: v6.2 active run/recovery marker present")
    if mod.orphaned_opencode_process():
        raise RuntimeError("STOP: another OpenCode run is active")
    # Prevent preflight proceeding when a different paid-step owns the old lock.
    if p.lock.is_symlink():
        raise RuntimeError("STOP: controller lock is symlinked")
    if p.lock.exists():
        with p.lock.open("a+") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as e:
                raise RuntimeError("STOP: v6.2 controller already holds its lock") from e
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    _program, db = mod.database(p)
    if require_historical and len(db.programs) < 9:
        raise RuntimeError(f"STOP: expected at least nine post-v6.2 DB programs; found {len(db.programs)}")
    missing = [sha for sha in PROVEN_OFFSPRING if db.get(sha) is None]
    if require_historical and missing:
        raise RuntimeError(f"STOP: latest verified offspring absent from DB: {missing}")
    # This check piggybacks on the native DB reload already required by the
    # safety preflight. It does not call the evaluator or make another DB load.
    if not isinstance(offspring_hashes, tuple) or any(not isinstance(h, str) or len(h) != 64
                                                      for h in offspring_hashes):
        raise RuntimeError("STOP: invalid offspring hash audit request")
    retained = [h for h in offspring_hashes if db.get(h) is not None]
    best = db.get_best_program()
    tq = best.metrics.get("tq_cost")
    if best.id != EXPECTED_NEW_BEST_SHA or tq != EXPECTED_NEW_BEST_TQ:
        # Allow future BETTER best after subsequent rounds, but not a regression.
        if not isinstance(tq, (int, float)) or not (0 < tq < EXPECTED_NEW_BEST_TQ):
            raise RuntimeError(f"STOP: expected 577700-or-better best, found {best.id} TQ={tq}")
        if not isinstance(best.id, str) or hashlib.sha256(best.code.encode("utf-8")).hexdigest() != best.id:
            raise RuntimeError("STOP: improved best hash mismatch")
    return {
        "status": "COULOMB_BATCH_PREFLIGHT_PASS",
        "db_program_count": len(db.programs),
        "db_best_sha256": best.id,
        "db_best_tq": tq,
        "three_v62_children_present": not missing,
        "missing_historical_offspring": missing,
        "protected_worktree_sha256": EXPECTED_WORKTREE_SHA,
        "v62_active_run": False,
        "opencode_running": False,
        "deepseek_api_calls": 0,
        "retained_new_offspring_sha256": retained,
        "missing_new_offspring_sha256": [h for h in offspring_hashes if h not in retained],
    }


@contextmanager
def interrupt_signals():
    """Don't leave the paid v6.2 subprocess running on TERM/HUP."""
    handled = (signal.SIGTERM, signal.SIGHUP)
    old = {s: signal.getsignal(s) for s in handled}
    def stop(signum, _frame):
        raise KeyboardInterrupt(f"batch received {signal.Signals(signum).name}")
    for s in handled:
        signal.signal(s, stop)
    try:
        yield
    finally:
        for s, previous in old.items():
            signal.signal(s, previous)


@contextmanager
def exclusive_batch_lock(path: Path):
    if path.is_symlink():
        raise RuntimeError("Batch lock is symlinked")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a+") as f:
        os.chmod(path, 0o600)
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise RuntimeError("Another Coulomb batch is already running") from e
        yield


def new_run_summaries(root: Path, before: set[str]) -> list[Path]:
    return sorted(x for x in root.iterdir() if x.is_dir() and x.name not in before and (x / "summary.json").is_file())


def execute_step(controller: Path, dbpath: Path, timeout: int, max_grades: int,
                 max_continuations: int, logfile: Path) -> int:
    """Run unchanged v6.2; relay CTRL+C / TERM, giving its restore handler time."""
    argv = [sys.executable, "-B", str(controller), "--paid-step", "--ack-paid",
            "--db", str(dbpath), "--timeout", str(timeout),
            "--max-grades", str(max_grades), "--max-continuations", str(max_continuations)]
    env = os.environ.copy()
    with logfile.open("wb") as out:
        os.chmod(logfile, 0o600)
        proc = subprocess.Popen(argv, cwd=controller.parent, env=env, stdout=out,
                                stderr=subprocess.STDOUT, start_new_session=True)
        try:
            return proc.wait()  # Inner v6.2 owns model timeout, regrading, and restoration.
        except (KeyboardInterrupt, SystemExit):
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=120)
            except subprocess.TimeoutExpired:
                # Do not claim restored if shutdown stalled. active.json is the recovery guard.
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait(timeout=10)
            raise


def run_batch(mod, controller: Path, dbpath: Path, home: Path, rounds: int,
              timeout: int, max_grades: int, max_continuations: int,
              max_empty: int, batchroot: Path, max_no_best: int | None = None,
              max_no_progress: int | None = None,
              min_parent_improvement_pct: float = 1.0) -> dict:
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise RuntimeError("STOP: DeepSeek key missing from THIS shell")
    if os.environ.get("OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX") != "65536":
        raise RuntimeError("STOP: expected the tested 65536 OpenCode cap in this shell")
    try:
        configured_effort = (json.loads(os.environ["OPENCODE_CONFIG_CONTENT"])
                             ["provider"]["deepseek"]["models"]["deepseek-v4-pro"]
                             ["options"]["reasoningEffort"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("STOP: DeepSeek reasoning override missing/malformed") from exc
    if configured_effort != "low":
        raise RuntimeError("STOP: expected tested DeepSeek reasoningEffort=low")
    if not (1 <= rounds <= 10 and 1 <= max_empty <= rounds):
        raise ValueError("Bounded batch requires 1..10 rounds and max-empty <= rounds")
    if max_no_best is not None and not (1 <= max_no_best <= rounds):
        raise ValueError("--max-no-best must be 1..rounds (or omitted)")
    if max_no_progress is not None and not (1 <= max_no_progress <= rounds):
        raise ValueError("--max-no-progress must be 1..rounds (or omitted)")
    if max_no_best is not None and max_no_progress is not None:
        raise ValueError("Choose only ONE stagnation rule: --max-no-best OR --max-no-progress")
    if (isinstance(min_parent_improvement_pct, bool) or
            not isinstance(min_parent_improvement_pct, (int, float)) or
            not math.isfinite(min_parent_improvement_pct) or
            not 0 < min_parent_improvement_pct < 100):
        raise ValueError("--min-parent-improvement-pct must be strictly between 0 and 100")
    if not (120 <= timeout <= 3600 and 1 <= max_grades <= 12 and 0 <= max_continuations <= 2):
        raise ValueError("Requested budget outside v6.2 supported limits")
    with exclusive_batch_lock(batchroot / "batch.lock"):
        initial = read_preflight(mod, home, dbpath)
        work = batchroot / (timestamp() + "-" + uuid.uuid4().hex[:8])
        work.mkdir(parents=True, exist_ok=False, mode=0o700)
        report = {"status": "running", "initial_state": initial, "requested_rounds": rounds,
                  "max_no_best": max_no_best,
                  "max_no_progress": max_no_progress,
                  "min_parent_improvement_pct": min_parent_improvement_pct,
                  "reasoning_effort": "low", "model_timeout_seconds": timeout,
                  "max_grades": max_grades, "max_continuations": max_continuations,
                  "rounds": [],
                  "note": "Paid executions; no enforced USD cap; verify billing separately"}
        events = work / "events.jsonl"
        atomic_json(work / "batch.json", report)
        append_event(events, {"event": "batch_started", "time_utc": datetime.now(timezone.utc).isoformat(),
                              "requested_rounds": rounds, "initial_best_tq": initial["db_best_tq"]})
        empty = 0
        no_best = 0
        no_progress = 0
        try:
            for i in range(1, rounds + 1):
                # Re-check restoration, absence of active task, and live MAP-Elites best.
                check = read_preflight(mod, home, dbpath, require_historical=False)
                before = {x.name for x in mod.Paths(home, dbpath).runroot.iterdir()
                          if x.is_dir()} if mod.Paths(home, dbpath).runroot.is_dir() else set()
                log = work / f"step-{i:02d}.log"
                row = {"number": i, "status": "running", "preflight": check,
                       "controller_log": str(log)}
                report["rounds"].append(row)
                atomic_json(work / "batch.json", report)
                append_event(events, {"event": "round_started", "round": i,
                                      "time_utc": datetime.now(timezone.utc).isoformat(),
                                      "best_tq_before": check["db_best_tq"]})
                rc = execute_step(controller, dbpath, timeout, max_grades,
                                  max_continuations, log)
                newly = new_run_summaries(mod.Paths(home, dbpath).runroot, before)
                row["controller_exit_code"] = rc
                if len(newly) != 1:
                    raise RuntimeError(f"STOP: expected one new v6.2 run summary, found {len(newly)}")
                inner = json.loads((newly[0] / "summary.json").read_text())
                row["inner_run"] = newly[0].name
                row["inner_status"] = inner.get("status")
                row["new_offspring"] = inner.get("returned_to_database")
                row["restored_original"] = inner.get("restored_original")
                if rc != 0 or inner.get("status") not in OK_STATUSES or inner.get("restored_original") is not True:
                    raise RuntimeError(f"STOP: v6.2 run not cleanly accepted: rc={rc} status={inner.get('status')}")
                # Postcondition after update: all previously validated improvements retained.
                child_shas = tuple(c.get("sha256", "") if isinstance(c, dict) else ""
                                   for c in (row["new_offspring"] or []))
                post = read_preflight(mod, home, dbpath, require_historical=False,
                                      offspring_hashes=child_shas)
                row["postflight"] = post
                row["progress"] = round_progress(inner, check, post,
                                                  min_parent_improvement_pct)
                if i > 1:
                    prior = report["rounds"][-2].get("new_offspring") or []
                    prev_child_hashes = {c.get("sha256") for c in prior if isinstance(c, dict)}
                    row["progress"]["parent_is_previous_round_offspring"] = (
                        row["progress"]["parent_sha256"] in prev_child_hashes)
                else:
                    row["progress"]["parent_is_previous_round_offspring"] = None
                if max_no_progress is not None and not row["progress"]["retained_offspring_audited"]:
                    raise RuntimeError("STOP: DB persistence audit unavailable for progress-based stopping")
                row["status"] = "verified_completed"
                report["latest_progress"] = row["progress"]
                atomic_json(work / "batch.json", report)
                append_event(events, {"event": "round_completed", "round": i,
                                      "time_utc": datetime.now(timezone.utc).isoformat(),
                                      "inner_status": row["inner_status"], **row["progress"]})
                empty = empty + 1 if not row["new_offspring"] else 0
                no_best = 0 if row["progress"]["new_global_best"] else no_best + 1
                no_progress = (0 if row["progress"]["made_research_progress"]
                               else no_progress + 1)
                if empty >= max_empty:
                    report["stop_reason"] = f"{max_empty} consecutive rounds without new DB offspring"
                    break
                if max_no_best is not None and no_best >= max_no_best:
                    report["stop_reason"] = f"{max_no_best} consecutive rounds without a new global best"
                    break
                if max_no_progress is not None and no_progress >= max_no_progress:
                    report["stop_reason"] = (
                        f"{max_no_progress} consecutive rounds without global-best improvement "
                        f"or >= {min_parent_improvement_pct:g}% retained parent improvement")
                    break
            report["status"] = "finished"
        except BaseException as exc:
            report["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
            report["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            report["finished_utc"] = datetime.now(timezone.utc).isoformat()
            atomic_json(work / "batch.json", report)
            append_event(events, {"event": "batch_finished", "time_utc": report["finished_utc"],
                                  "status": report["status"], "stop_reason": report.get("stop_reason"),
                                  "error": report.get("error"),
                                  "completed_rounds": sum(x.get("status") == "verified_completed" for x in report["rounds"])})
            print(json.dumps({"batch_report": str(work / 'batch.json'), "status": report["status"],
                              "completed_rounds": sum(x.get("status") == "verified_completed" for x in report["rounds"]),
                              "stop_reason": report.get("stop_reason"),
                              "error": report.get("error")}, indent=2, ensure_ascii=False), flush=True)
        return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="Read-only v6.2 + real DB preflight; 0 API")
    group.add_argument("--run", action="store_true", help="Start bounded paid batch")
    ap.add_argument("--ack-paid", action="store_true", help="Required before all paid steps")
    ap.add_argument("--rounds", type=int, default=None, help="1..10; required for paid batch; never unlimited")
    ap.add_argument("--max-empty", type=int, default=1, help="Stop after consecutive no-offspring rounds")
    stagnation = ap.add_mutually_exclusive_group()
    stagnation.add_argument("--max-no-best", type=int, default=None,
                    help="Legacy v2 stop: N rounds without a new GLOBAL best; disabled by default")
    stagnation.add_argument("--max-no-progress", type=int, default=None,
                    help="New v3 stop: N rounds without a global best OR meaningful retained parent improvement")
    ap.add_argument("--min-parent-improvement-pct", type=float, default=1.0,
                    help="Minimal parent improvement in percent (default 1); only used by --max-no-progress")
    ap.add_argument("--timeout", type=int, default=1200, help="Per v6.2 model total seconds")
    ap.add_argument("--max-grades", type=int, default=8)
    ap.add_argument("--max-continuations", type=int, default=1)
    ap.add_argument("--home", type=Path, default=Path.home())
    ap.add_argument("--db", type=Path, default=Path.home() / "openevolve-native-check/evolution-seeds-v5")
    ap.add_argument("--controller", type=Path, default=Path.home() / "openevolve-timeout-guard-v6-2/openevolve_timeout_recovery_v6_2/openevolve_opencode_loop.py")
    args = ap.parse_args(argv)
    if args.ack_paid and not args.run:
        ap.error("--ack-paid is only for --run")
    if args.run and not args.ack_paid:
        ap.error("Paid batch disabled without --ack-paid")
    if args.run and args.rounds is None:
        ap.error("Paid batch disabled without explicit --rounds N")
    try:
        mod = load_v62(args.controller)
        if args.check:
            report = read_preflight(mod, args.home, args.db)
        else:
            with interrupt_signals():
                report = run_batch(mod, args.controller, args.db, args.home, args.rounds,
                                   args.timeout, args.max_grades, args.max_continuations,
                                   args.max_empty, args.home / "quantum-agent/runs/coulomb-batches",
                                   args.max_no_best, args.max_no_progress,
                                   args.min_parent_improvement_pct)
        if args.check:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    except KeyboardInterrupt:
        print("STOP: interrupted; inspect v6.2 active.json and best circuit before any retry", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"STOP: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
