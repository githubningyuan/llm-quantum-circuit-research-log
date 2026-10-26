# Coulomb potential — results and evidence status

**Dated snapshot:** 2026-10-27, Asia/Taipei. **Work in progress.** This is a public progress report, not a runnable replication package.

| Metric | Value | Status |
| --- | ---: | --- |
| Baseline TQ | 1,601,460 | Reference baseline |
| Previous confirmed internal database best | 559,493 | Earlier database/preflight record |
| **Latest original-evaluator-passed candidate** | **450,385** | **Original evaluator `valid=true`; preserved result/source snapshot** |
| Latest candidate T / Q | 2,197 / 205 | Original evaluator measurements |
| Relative improvement against baseline | 3.556× | Baseline / latest evaluated candidate |
| Relative TQ cost reduction | 71.877% | `(1 - 450385 / 1601460) × 100` |
| Published reference-paper result | 161,880 | Gu et al. (2026) |
| Original benchmark tasks explored | 1 of 8 | Coulomb only |

## Two different statuses, neither concealed nor conflated

1. **Original-evaluator-passed result (450,385):** According to the dated internal handoff, a test on the original evaluator yielded `valid=true`, with an archived, file-consistency-checked circuit and result snapshot. This is **the strongest evaluated candidate reported so far**.
2. **Database acceptance (last known best 559,493):** A subsequent validation rerun and import into the MAP-Elites database have not occurred for the 450,385 candidate as of this handoff. This is a **workflow/storage status**, not evidence that the reference evaluator rejected the new candidate.

The evaluator checks specified behaviors on selected inputs and computes resource counts. **Evaluator acceptance is not formal proof of circuit equivalence**. Because neither the candidate source nor original evaluator report is supplied in this public note, an external reader cannot audit or reproduce the result from this repository alone.

The internal experiment may continue; these are dated, non-live figures. Full agent instructions, evolutionary search policies, orchestration code, candidate circuit sources, and detailed logs remain unpublished.

The structured public summary is in [data/public-results-2026-10-27.json](data/public-results-2026-10-27.json).
