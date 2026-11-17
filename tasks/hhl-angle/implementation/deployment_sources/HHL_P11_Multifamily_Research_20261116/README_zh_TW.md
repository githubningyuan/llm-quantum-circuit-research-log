# HHL P11 — Research-first fork of frozen P9 R3 (2026-11-16)

**This is a NEW experimental controller, not a change to frozen R3.** Requires a fork of `HHL_P9_EVOLUTION_78ca6975`; it explicitly refuses to run paid mode on the original R3 branch. No paid trial has been performed, no native Evaluator re-execution has been performed in this environment. Native VM preflight is still required.

## Scientific changes

1. **Success-family continuation**: step 9 uses INDEPENDENT_B, 10 A, 11 B, 12 A, 13 LINEAGE_GEN1, then repeat. A and B are already validated 6+4 Gen0 seeds, not blank restarts. Select parents among the highest available native generation within each family until Gen3; periodically reopen the older retained native MAP-Elites cells afterward; use native MAP-Elites `db.sample()` if that sample belongs to the active family and highest generation, otherwise rotate over retained DB programs without the old Pareto-only filter. Gen0→Gen1→Gen2 possible if accepted children exist.
2. **Selective Inspiration**: choose separately from other verified source bundles, favor cross-family AST-distinct references and Coulomb-like TQ [0.75, 1.25] comparability, with a clearly identified distant reference up to 3× when it is cross-family. Supply the verified `circuit.py` source up to 14kB as read-only context. B is not an ancestry parent. Fall back to primary-only if no match.
3. **Research Memory**: aggregate actual native acceptance/rejection observations and up to 2400 characters of each relevant, SHA-verified, post-session `research_log.txt` into new bounded prompt context. Label unverified agent observations distinctly. No hidden reasoning fabricated.
4. **Unaccepted draft carryover**: ONLY when a family has no accepted seed, may restore SHA-verified `circuit.py`/`constants.json` from previous frozen session to a new workspace, explicitly labeled UNVERIFIED. Never make draft a formal DB parent. When seed exists, always stage the native verified parent instead.
5. **Experiment preserved**: P9's source-bound Broker/Evaluator, native score, duplicate OPS check and DB admission persist/reload logic unchanged. Legacy R3 original not edited.

## Main evidence

R3 audit 2026-11-16 PASS: 10 source-bound native OK, A 6 Gen0 + B 4 Gen0. Best B 5503×184=1,012,552. A best 9082×239=2,170,598. The distinction in B `circuit.py` is 2^11-aligned segments, bit-slice labels and stage-specific 15/20/25/30-bit widths vs A's adaptive Chebyshev/refined interval and label comparator architecture. Native sampled benchmark OK does not prove full-domain correctness. The audit report is *not* a new native replay nor proof of VM database reload; P11 VM preflight is pending; even if the free preflight succeeds, it is NOT evidence of a new generation or circuit improvement.

## Recommended VM operation (no paid calls)

1. Upload `HHL_P11_Multifamily_Research_20261116.zip` AND `HHL_P11_Evolution_VM.sh` to `/home/researcher/`.
2. Inspect the frozen original: `sudo bash /home/researcher/HHL_P11_Evolution_VM.sh --inspect-fork`
3. Make a **new private fork** only after inspection: `sudo bash /home/researcher/HHL_P11_Evolution_VM.sh --prepare-fork`
4. Note `P11_NEW_BRANCH=HHL_P9_EVOLUTION_...` from output.
5. Free runtime + native DB preload check: `sudo bash /home/researcher/HHL_P11_Evolution_VM.sh --preflight HHL_P9_EVOLUTION_新分支代號`. Success requires BOTH `P11_NATIVE_FORK_SOURCE_AND_DB_CHECK=PASS` and `P11_PREFLIGHT=PASS`.
6. **No paid run yet.** Later after human review of actual VM preflight + source and explicit permission, run `--resume-paid-evolution N SECS P11_FORK_BRANCH` with the VM script. No generic `--authorize-paid-evolution` blank-branch mode is supported.

## What still needs independent verification

- Full runtime integration on VM: `--preflight P11_FORK_BRANCH`; source-at-evaluation provenance and DB reload on the newly forked branch.
- Real A/B Gen1 acceptance, multiple generations, actual Inspiration influence, observed research-memory reuse, no family starvation. A plan or a simulated test is not this scientific result.
- HHL evaluator's benchmark uses numerical sampling. Known full-domain counterexamples must be disclosed, never hidden. Full-domain check is valuable where feasible but is not an extra mandatory paper-incorrect admission gate.
- P11 prototype has no autonomous reflection/compression beyond bound diary and fact aggregation. Future enhancements should be justified by measured research value.

## Safety

No modifications to `/srv/hhl-task2/research-records/HHL_P9_EVOLUTION_78ca6975`, evaluator, historical DB, P6 source, Coulomb DB or credential files. Separate fork only. `p11_fork_branch.py --inspect` is read only. `--fork --authorize-fork-write` creates the new branch. The launcher's `--preflight` performs the previous P7 environment tests and may start/stop temporary preflight services and write preflight logs; it is **not** equivalent to a no-side-effect read-only inspection, but starts no paid model. All paid execution requires the existing deepseek key and explicit flags and is not performed here.
