#!/usr/bin/env python3
"""OFFLINE integration adapter for original OpenCode evaluator archives.

Default --check: read-only audit, no OpenCode/DeepSeek/Docker/OpenEvolve import.
--seed-db: create a BRAND-NEW isolated OpenEvolve database from verified archives.
--sample: read a seeded database and show a proposed parent (never launch LLM).

A validated snapshot is historical evidence, NOT a re-execution or a
cryptographic proof of its OpenCode session provenance.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys

BASELINE_TQ = 1_601_460.0
DEFAULT_FEATURES = ("complexity", "diversity")
PROFILE_FILE = "map_elites_profile.json"


def require_profile(db_path: Path) -> None:
    """Refuse database previously seeded with unknown/different MAP-Elites dimensions."""
    profile = db_path / PROFILE_FILE
    if profile.is_symlink() or not profile.is_file():
        raise RuntimeError("Unknown OpenEvolve DB feature profile; use a BRAND-NEW v4 database")
    data = json.loads(profile.read_text(encoding="utf-8"))
    if (type(data) is not dict or data.get("schema") != 1 or
            data.get("features") != list(DEFAULT_FEATURES) or
            data.get("fitness") != "1601460/tq_cost"):
        raise RuntimeError("MAP-Elites feature profile differs from verified v4 defaults")

MAX_SOURCE_BYTES = 400_000
# This is a guardrail for this particular validated historical dataset.
EXPECTED_BEST_SHA = "d242f66d35d7fc8b78dfe5a1e76178ca155f6c575df78e1e53ed5062d7157625"

@dataclass(frozen=True)
class Seed:
    digest: str
    code: str
    toffoli: int
    qubits: int
    tq: int
    snapshot_names: tuple[str, ...]

    @property
    def fitness(self) -> float:
        return BASELINE_TQ / self.tq


def _metric(m: dict, key: str) -> int:
    value = m.get(key)
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        raise ValueError(f"Unsafe/non-numeric metric: {key}")
    if value != int(value):
        raise ValueError(f"Expected integer metric: {key}")
    return int(value)


def _read_regular(path: Path, max_size: int | None = None) -> bytes:
    # lstat prevents a symlink at the leaf; path parents are verified separately.
    mode = path.lstat().st_mode
    if not stat.S_ISREG(mode):
        raise ValueError(f"Not a regular file: {path}")
    size = path.stat().st_size
    if max_size is not None and (size <= 0 or size > max_size):
        raise ValueError(f"Unexpected file size: {path}")
    data = path.read_bytes()
    if max_size is not None and (not data or len(data) > max_size):
        raise ValueError(f"Invalid file data length: {path}")
    return data


def audit(archive: Path) -> tuple[list[Seed], dict]:
    if archive.is_symlink() or not archive.is_dir():
        raise ValueError("Archive must be an actual directory")
    by_sha: dict[str, dict] = {}
    count = 0
    for folder in sorted(archive.iterdir()):
        if folder.is_symlink() or not folder.is_dir():
            raise ValueError(f"Unexpected archive entry: {folder}")
        payload = _read_regular(folder / "circuit.py", MAX_SOURCE_BYTES)
        result_raw = _read_regular(folder / "result.json", 5_000_000)
        sha = hashlib.sha256(payload).hexdigest()
        result = json.loads(result_raw)
        if type(result) is not dict or result.get("valid") is not True or result.get("sha256") != sha:
            raise ValueError(f"Invalid result or SHA mismatch in {folder.name}")
        m = result.get("metrics")
        if type(m) is not dict:
            raise ValueError(f"Missing metrics: {folder.name}")
        t, q, tq = (_metric(m, k) for k in ("toffoli", "qubits", "tq_cost"))
        if t <= 0 or q <= 0 or tq <= 0 or t*q != tq:
            raise ValueError(f"Resource metrics inconsistent: {folder.name}")
        source = payload.decode("utf-8")  # never execute source
        count += 1
        old = by_sha.get(sha)
        if old:
            if old["code"] != source or old["toffoli"] != t or old["qubits"] != q or old["tq"] != tq:
                raise ValueError(f"Inconsistent repeated digest: {sha}")
            old["snapshots"].append(folder.name)
        else:
            by_sha[sha] = dict(digest=sha, code=source, toffoli=t, qubits=q,
                              tq=tq, snapshots=[folder.name])
    if not count:
        raise ValueError("Archive is empty")
    seeds = sorted(
        (Seed(digest=x["digest"], code=x["code"], toffoli=x["toffoli"],
              qubits=x["qubits"], tq=x["tq"], snapshot_names=tuple(x["snapshots"]))
         for x in by_sha.values()),
        key=lambda x: (x.tq, x.digest),
    )
    report = {
        "snapshot_count": count, "unique_program_count": len(seeds),
        "best_sha256": seeds[0].digest, "best_tq": seeds[0].tq,
        "fitness_rule": "combined_score=1601460/tq_cost (higher is better)",
        "programs": [{"sha256": s.digest, "toffoli": s.toffoli,
                      "qubits": s.qubits, "tq_cost": s.tq,
                      "combined_score": s.fitness,
                      "snapshots": list(s.snapshot_names)} for s in seeds],
        "caveat": "Saved evaluator claims + hashes verified; not an independent re-execution or session proof.",
    }
    return seeds, report


def _db_guard(db_path: Path, archive: Path) -> Path:
    """Do not allow this program to write inside preserved research projects."""
    db_path = db_path.expanduser().absolute()
    archive = archive.expanduser().resolve()
    home = Path.home().resolve()
    protected = [archive, home / "opencode-lab", home / "quantum-agent", home / "uncompute"]
    for directory in protected:
        directory = directory.resolve()
        if db_path == directory or directory in db_path.parents or db_path in directory.parents:
            raise ValueError(f"Database must be outside protected project/archive: {db_path}")
    if db_path.is_symlink() or db_path.exists():
        raise ValueError("Database path already exists or is a symlink: refusing overwrite")
    if db_path.parent.is_symlink() or not db_path.parent.is_dir():
        raise ValueError("Database parent must already exist and not be a symlink")
    return db_path


def _openevolve_api():
    try:
        from openevolve.config import DatabaseConfig
        from openevolve.database import Program, ProgramDatabase
    except ImportError as exc:
        raise RuntimeError("OpenEvolve is not installed in this Python environment; --check still works") from exc
    return DatabaseConfig, Program, ProgramDatabase


def load_database_with_verified_stats(config, database_class):
    """OpenEvolve 0.4.0 resume guard (do not change the installed package).

    v0.4.0 can reset checkpoint feature_stats after __init__ calls load().
    If that happened, explicitly reload ONCE after constructor completes,
    then fail closed unless statistics exactly match the saved metadata.
    Applies to seed reload, preview, and the paid evolution path.
    """
    db = database_class(config)
    path = Path(config.db_path)
    metadata_path = path / "metadata.json"
    if not metadata_path.is_file():
        return db
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise RuntimeError("OpenEvolve metadata must be an object")
    expected = metadata.get("feature_stats", {})
    if not isinstance(expected, dict):
        raise RuntimeError("OpenEvolve feature_stats metadata is malformed")
    serializer = getattr(db, "_serialize_feature_stats", None)
    if not callable(serializer):
        raise RuntimeError("Cannot verify MAP-Elites feature statistics")
    current = serializer()
    if current != expected:
        db.load(str(path))
    if serializer() != expected:
        raise RuntimeError("MAP-Elites feature stats still inconsistent after explicit reload")
    return db


def seed_database(seeds: list[Seed], db_path: Path, archive: Path) -> dict:
    """Insert into the REAL ProgramDatabase, only in a brand-new separate directory."""
    db_path = _db_guard(db_path, archive)
    DatabaseConfig, Program, ProgramDatabase = _openevolve_api()
    # One island, generous slots; no autonomous LLM/evaluator execution.
    cfg = DatabaseConfig(db_path=str(db_path), population_size=50,
                         archive_size=50, num_islands=1,
                         feature_dimensions=["complexity", "diversity"], feature_bins=16,
                         random_seed=42)
    old_umask = os.umask(0o077)
    try:
        db_path.mkdir(mode=0o700)
    finally:
        os.umask(old_umask)
    db = ProgramDatabase(cfg)
    for i, s in enumerate(sorted(seeds, key=lambda x: (-x.tq, x.digest))):
        program = Program(id=s.digest, code=s.code, language="python",
                          generation=0, parent_id=None,
                          changes_description="Verified historical OpenCode snapshot (not new LLM output)",
                          metrics={"combined_score": s.fitness,
                                   "toffoli": float(s.toffoli),
                                   "qubits": float(s.qubits),
                                   "tq_cost": float(s.tq)},
                          metadata={"sha256": s.digest,
                                    "historical_snapshot_names": list(s.snapshot_names),
                                    "provenance": "historical_bridge_archive_unbound_session"})
        added = db.add(program, iteration=i)
        if added is None:
            raise RuntimeError(f"OpenEvolve rejected seed {s.digest}")
    db.save(str(db_path))
    profile_path = db_path / PROFILE_FILE
    if profile_path.exists() or profile_path.is_symlink():
        raise RuntimeError("Feature profile file unexpectedly exists: fail closed")
    profile_path.write_text(json.dumps({"schema": 1, "features": list(DEFAULT_FEATURES),
                                        "fitness": "1601460/tq_cost"}), encoding="utf-8")
    os.chmod(profile_path, 0o600)
    require_profile(db_path)
    # Native MAP-Elites may evict programs competing in the same cell.
    # In this dataset two distinct baseline SHA256s have IDENTICAL features,
    # so requiring every seed to remain in MAP-Elites is incorrect.
    restored = load_database_with_verified_stats(cfg, ProgramDatabase)
    retained = []
    for s in seeds:
        p = restored.get(s.digest)
        if p is None:
            continue
        if hashlib.sha256(p.code.encode("utf-8")).hexdigest() != s.digest:
            raise RuntimeError(f"Program damaged in native database: {s.digest}")
        if not math.isclose(p.metrics["combined_score"], s.fitness, rel_tol=1e-12):
            raise RuntimeError(f"Fitness mismatch after native database reload: {s.digest}")
        retained.append(s.digest)
    best_program = restored.get(seeds[0].digest)
    if best_program is None:
        raise RuntimeError("OpenEvolve discarded the global best verified circuit")
    ranked = sorted(restored.programs.values(),
                    key=lambda p: p.metrics["combined_score"], reverse=True)
    if not ranked or ranked[0].id != seeds[0].digest:
        raise RuntimeError("OpenEvolve database fitness direction is reversed")
    return {"db_path": str(db_path),
            "original_unique_sha256_count": len(seeds),
            "native_population_count": len(restored.programs),
            "retained_sha256": retained,
            "best_sha256": ranked[0].id,
            "best_fitness": ranked[0].metrics["combined_score"],
            "note": "Native MAP-Elites may discard same-cell rivals; original archive retains every seed. No DeepSeek or Docker used."}


def sample_database(db_path: Path) -> dict:
    # Only reads; the OpenEvolve DB API may cache metadata in memory.
    if db_path.is_symlink() or not db_path.is_dir():
        raise ValueError("Expected existing regular OpenEvolve database directory")
    require_profile(db_path)
    DatabaseConfig, _Program, ProgramDatabase = _openevolve_api()
    cfg = DatabaseConfig(db_path=str(db_path), population_size=50,
                         archive_size=50, num_islands=1,
                         feature_dimensions=["complexity", "diversity"], feature_bins=16, random_seed=42)
    db = load_database_with_verified_stats(cfg, ProgramDatabase)
    if not db.programs:
        raise ValueError("OpenEvolve database is empty")
    parent, _inspirations = db.sample(num_inspirations=0)
    h = hashlib.sha256(parent.code.encode("utf-8")).hexdigest()
    if h != parent.id:
        raise ValueError("Sampled parent SHA mismatch")
    return {"parent_sha256": h,
            "parent_tq": parent.metrics.get("tq_cost"),
            "parent_combined_score": parent.metrics.get("combined_score"),
            "note": "Selection only; NO worktree replacement, OpenCode or API calls"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True,
                        help="Original read-only snapshot directory")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="Audit existing snapshots (read-only)")
    group.add_argument("--seed-db", type=Path, metavar="NEW_DIRECTORY",
                       help="Create an isolated NEW OpenEvolve database; never edit existing DB")
    group.add_argument("--sample", type=Path, metavar="DATABASE_DIRECTORY",
                       help="Select parent from existing OpenEvolve DB; read-only")
    args = parser.parse_args()
    try:
        if args.sample is not None:
            result = sample_database(args.sample)
        else:
            seeds, report = audit(args.archive)
            result = report if args.check else seed_database(seeds, args.seed_db, args.archive)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as e:
        print(f"STOP: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
