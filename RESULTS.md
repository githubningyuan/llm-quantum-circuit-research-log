# Coulomb Potential — Results and Evidence Status

**Public snapshot: 2026-11-02 (Asia/Taipei).** Private-VM audit and handoff summary, not an independent execution of the quantum evaluator by this public repository.

## Benchmark and achieved progress

| Stage | T×Q | Baseline / T×Q | State |
|---|---:|---:|---|
| Reference baseline | 1,601,460 | 1.000× | From original benchmark |
| 2026-10-27 previously published candidate | 450,385 | 3.556× | Earlier original-evaluator-passed candidate; now historical |
| 2026-11-01 production database | 348,775 | 4.592× | Validated historical best, 79 programs |
| **2026-11-02 experimental treatment database** | **346,035** | **4.628×** | **Evaluator-validated, recorded in isolated DB of 117 programs** |
| Gu *et al.* (2026) reported result | 161,880 | 9.893× | Original study |

The latest result represents a 78.4% T×Q reduction relative to the baseline, versus 71.9% at the 2026-10-27 public snapshot. The original paper remains substantially ahead.

**Evidence level:** The handoff records report the treatment database's best as T×Q=346,035, with a latest read-only safety preflight marked PASS. They do **not** contain the full latest VM database or the newly best candidate's exported code and original evaluator log; accordingly, the public report cannot be independently reproduced from this repository. The separate production database remains at 348,775 by design. No T or Q breakdown is asserted for the latest candidate.

## Exploratory five-round comparison (two configurations)

| Metric | Configuration A | Configuration B |
|---|---:|---:|
| Initial database size | 79 | 79 |
| Completed rounds | 5 | 5 |
| Retained, SHA-unique validated offspring | 9 | **38** |
| Mean retained offspring per round | 1.8 | **7.6** |
| Best newly produced candidate T×Q | 415,529 | **346,035** |
| Final database count | 88 | **117** |
| Final overall best T×Q | 348,775 | **346,035** |

The two configurations differed in multiple undisclosed implementation choices, and independent ablations have not been performed. The 38:9 count concerns SHA-unique retained candidate snapshots, **not** 38 vs 9 independent sessions. Only the initial parent was matched; later selections diverged. These observations **do not establish a causal advantage**, statistical significance, or per-dollar efficiency.

**Comparison discipline:** best *new* candidate differs by 16.72%; final *database-wide* best differs by 0.786%. It would be misleading to report the first percentage as the overall DB improvement.

## Cost snapshot (accounts, not per-trial costs)

- GCP usage after shown discount: **$96.00 before tax**, plus **$5.00 displayed tax**. The underlying pre-discount usage is $109.06 and the applied discount is $13.06.
- DeepSeek recorded cumulative charges: **$17.15**.
- Combined **$113.15 before GCP tax**, or **$118.15 including the displayed tax**.
- Author estimates **$15–$20** of GCP charges were due to an idle VM over several days, not active research computation.
- No detailed 5+5 run-specific GPU/CPU/API spend ledger or attribution of fixed/idle costs is available. This is **not** a measured cost per valid candidate or evidence of a controlled price/performance advantage over the original study.

## Status and reproducibility

The research covers one of eight original benchmarks. Validation uses the original benchmark's test protocol rather than a formal proof for all inputs. Full agent instructions, orchestration source, experimental selection policies, the latest circuit, and evaluation logs are not released. See [RIGHTS.md](RIGHTS.md) and [REFERENCES.md](REFERENCES.md).

Structured data: [2026-11-02 snapshot](data/public-results-2026-11-02.json); [2026-10-27 historical snapshot](data/public-results-2026-10-27.json).
