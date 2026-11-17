#!/usr/bin/env python3
"""Real-package OpenEvolve 0.4.0 native MAP-Elites acceptance test.

OFFLINE: does NOT access OpenCode, DeepSeek, Docker, remote services or real GCP
worktrees. Uses read-only historical fixture snapshots and a disposable
TemporaryDirectory for ALL OpenEvolve database writes. It refuses mock/stub
packages by checking installed distribution metadata and the imported module.

Usage: python3 -B native_map_elites_gate.py
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import tempfile

from openevolve_seed_adapter import (
    audit, seed_database, load_database_with_verified_stats, require_profile, DEFAULT_FEATURES, BASELINE_TQ, EXPECTED_BEST_SHA,
)

VERSION = "0.4.0"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "opencode_evaluations"


def require(ok: bool, desc: str) -> None:
    if not ok:
        raise RuntimeError("NATIVE GATE FAILED: " + desc)


def verify_actual_package() -> tuple[str, str]:
    try:
        dist = importlib.metadata.distribution("openevolve")
        spec = importlib.util.find_spec("openevolve")
    except (ModuleNotFoundError, importlib.metadata.PackageNotFoundError) as e:
        raise RuntimeError("OpenEvolve distribution missing; NATIVE gate NOT RUN") from e
    require(dist.version == VERSION,
            f"Expected pinned openevolve=={VERSION}; installed {dist.version}")
    require(spec is not None and spec.origin is not None,
            "Real openevolve module location missing")
    modfile = Path(spec.origin).resolve()
    # Prevent a same-named local module or fake module shadowing the installed wheel.
    require("site-packages" in modfile.parts, f"Non-installed/shadowed package: {modfile}")
    from openevolve.config import DatabaseConfig
    from openevolve.database import Program, ProgramDatabase
    require(all(callable(x) for x in (DatabaseConfig, Program, ProgramDatabase)),
            "Native API exports missing")
    return dist.version, str(modfile)


def integrity(db, known: dict[str, tuple[int, int, int]]) -> None:
    require(bool(db.programs), "No programs in native DB")
    for program in db.programs.values():
        code = program.code.encode("utf-8")
        require(hashlib.sha256(code).hexdigest() == program.id,
                f"SHA mismatch: {program.id}")
        t = int(program.metrics["toffoli"])
        q = int(program.metrics["qubits"])
        tq = int(program.metrics["tq_cost"])
        require(t > 0 and q > 0 and t*q == tq,
                f"Metric mismatch: {program.id}")
        require(abs(program.metrics["combined_score"] - BASELINE_TQ / tq) < 1e-10,
                f"Inverted or corrupted fitness: {program.id}")
        if program.id in known:
            require(known[program.id] == (t, q, tq),
                    f"Historical metrics changed: {program.id}")
    for feature_map in db.island_feature_maps:
        require(bool(feature_map), "MAP-Elites feature grid empty")
        for cell, pid in feature_map.items():
            require(pid in db.programs, f"MAP-Elites stale cell {cell} -> {pid}")


def native_state(db):
    return {
        "ids": set(db.programs),
        "grids": [dict(x) for x in db.island_feature_maps],
        "islands": [set(x) for x in db.islands],
        "best": db.best_program_id,
        "stats": db._serialize_feature_stats(),
    }


def require_stable_reload(cfg, db, db_type, path):
    before = native_state(db)
    db.save(str(path), iteration=db.last_iteration)
    reloaded = load_database_with_verified_stats(cfg, db_type)
    after = native_state(reloaded)
    require(before == after,
            "MAP-Elites reload changed IDs / grid / island members / best / feature stats")
    return reloaded


def run() -> dict:
    version, module_path = verify_actual_package()
    seeds, report = audit(FIXTURES)
    require(report["snapshot_count"] == 7 and len(seeds) == 5,
            "Historical fixture inventory unexpected")
    require(seeds[0].digest == EXPECTED_BEST_SHA and seeds[0].tq == 680430,
            "Protected best baseline changed")
    expected = {s.digest: (s.toffoli, s.qubits, s.tq) for s in seeds}
    from openevolve.config import DatabaseConfig
    from openevolve.database import Program, ProgramDatabase

    # tempfile forces the real package to operate away from any preserved circuit.
    with tempfile.TemporaryDirectory(prefix="oe-native-gate-") as td:
        dest = Path(td) / "isolated-native-map"
        initial = seed_database(seeds, dest, FIXTURES)
        require_profile(dest)
        require(initial["best_sha256"] == EXPECTED_BEST_SHA,
                "Native seeding lost global best")
        cfg = DatabaseConfig(db_path=str(dest), population_size=50, archive_size=50,
                             num_islands=1, feature_dimensions=["complexity", "diversity"],
                             feature_bins=16, random_seed=42)
        db = load_database_with_verified_stats(cfg, ProgramDatabase)
        # MAP-Elites' numeric feature scaling must survive a save/reload;
        # otherwise a later insertion may map an existing feature to a new cell.
        saved_stats = json.loads((dest / "metadata.json").read_text()).get("feature_stats", {})
        if saved_stats:
            require(hasattr(db, "_serialize_feature_stats"),
                    "Native feature-stat serialization API unavailable")
            current_stats = db._serialize_feature_stats()
            require(current_stats == saved_stats,
                    "MAP-Elites feature scales changed on reload; unsafe to evolve")
        integrity(db, expected)
        db = require_stable_reload(cfg, db, ProgramDatabase, dest)
        require(db.get(EXPECTED_BEST_SHA) is not None,
                "Native reload lost verified best")
        native_best = db.get_best_program()
        require(native_best is not None and native_best.id == EXPECTED_BEST_SHA,
                "Native highest-fitness selection not aligned with min T×Q")
        known_parents = set(db.programs)
        drawn: set[str] = set()
        for _ in range(20):
            parent, inspirations = db.sample(num_inspirations=0)
            require(parent.id in known_parents, "Unknown parent sampled")
            require(hashlib.sha256(parent.code.encode()).hexdigest() == parent.id,
                    "Sampled parent code SHA mismatch")
            require(len(inspirations) == 0, "Unexpected inspirations when disabled")
            drawn.add(parent.id)

        # A FAKE scoring record is inserted in TEMP DB ONLY to exercise the
        # native parent->offspring API and persistence. It must NEVER enter any
        # real evaluator archive / research database or be called a valid circuit.
        parent = db.get(EXPECTED_BEST_SHA)
        fake = parent.code + "\n# TEMPORARY API SMOKE: NOT EVALUATED / NOT VALIDATED\n"
        fake_id = hashlib.sha256(fake.encode("utf-8")).hexdigest()
        fake_t, fake_q = 2500, 200
        fake_tq = fake_t * fake_q
        child = Program(
            id=fake_id, code=fake, language="python", parent_id=parent.id,
            generation=1, changes_description="TEMP API test; metrics are synthetic",
            metrics={"combined_score": BASELINE_TQ / fake_tq,
                     "toffoli": float(fake_t), "qubits": float(fake_q),
                     "tq_cost": float(fake_tq)},
            metadata={"sha256": fake_id, "test_only": True,
                      "never_independently_evaluated": True},
        )
        result = db.add(child, iteration=db.last_iteration + 1)
        # add() can reject a synthetic child because of MAP-Elites admission policy.
        # We require the API to complete and grid/population invariants to hold.
        require(result is not None, "Native add() rejected temp offspring")
        integrity(db, {**expected, fake_id: (fake_t, fake_q, fake_tq)})
        before_save = (len(db.programs), len(db.island_feature_maps[0]))
        restored = require_stable_reload(cfg, db, ProgramDatabase, dest)
        if saved_stats:
            final_saved_stats = json.loads((dest / "metadata.json").read_text()).get("feature_stats", {})
            require(restored._serialize_feature_stats() == final_saved_stats,
                    "MAP-Elites feature scales lost after offspring save/reload")
        integrity(restored, {**expected, fake_id: (fake_t, fake_q, fake_tq)})
        after_save = (len(restored.programs), len(restored.island_feature_maps[0]))
        require(before_save == after_save, "Native MAP-Elites population/grid changed on reload")
        require(restored.get(EXPECTED_BEST_SHA) is not None,
                "Verified global best lost after offspring insertion")
        # Do not depend on temporary child's survival: native replacement may evict it.
        return {
            "status": "NATIVE_GATE_PASS", "openevolve_distribution_version": version,
            "native_module": module_path, "snapshot_count": 7,
            "resume_guard": "explicit_reload_if_feature_stats_lost; verified_against_metadata",
            "unique_historical_sha256": 5, "protected_best_sha256": EXPECTED_BEST_SHA,
            "protected_best_tq": 680430, "native_population_after_seed": initial["native_population_count"],
            "map_elites_dimensions": list(DEFAULT_FEATURES),
            "round_trip_check": "exact_ids_grid_island_best_feature_stats",
            "distinct_parents_sampled_in_20": len(drawn),
            "after_synthetic_add_before_save": list(before_save),
            "after_reload": list(after_save),
            "synthetic_child_is_not_validated": True,
            "database_scope": "temporary_directory_removed_after_test",
            "deepseek_api_calls": 0, "original_worktree_edits": 0,
        }


if __name__ == "__main__":
    try:
        print(json.dumps(run(), indent=2, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"status":"NATIVE_GATE_NOT_PASSED",
                          "error":type(exc).__name__+": "+str(exc)},
                         indent=2, ensure_ascii=False))
        raise SystemExit(2)
