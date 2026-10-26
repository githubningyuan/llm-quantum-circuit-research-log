# LLM-Guided Quantum Circuit Optimization — Independent Research Notes

**Public research note v0.3 · Coulomb potential · 2026-10-27 (Asia/Taipei)**

> **Work in progress; an early result report, not a reproducible software release.** The latest candidate passed the **original reference evaluator**, and its evaluation result and source snapshot were archived in the private research environment according to dated records. The candidate has **not yet been independently re-run or incorporated into the evolutionary database**. Neither its circuit source nor the evaluator log is included in this public note, so third parties cannot reproduce or verify the result from this repository alone.

## Research question

Can a resource-conscious, independently assembled LLM-assisted workflow discover valid reductions in the resource cost of reversible quantum circuits?

This personal AI-for-Science experiment was inspired by **Gu, Kemeny, Yelin, and Lukin (2026)**, [*Discovering state-of-the-art quantum circuits with large language models*](https://arxiv.org/abs/2610.22367). It uses existing publicly available tools and the original research evaluator. **It does not claim to reproduce the authors' full system, invent LLM-and-evaluator-guided search, or match the published best result.**

## Early result: Coulomb potential

The objective is **TQ = T × Q**, where T is the reference evaluator's Toffoli count and Q is its peak live-qubit count.

| Comparison | Toffoli T | Peak qubits Q | TQ cost | Relative baseline gain |
| --- | ---: | ---: | ---: | ---: |
| Reference baseline | 6,355 | 252 | **1,601,460** | 1.00× |
| Previously confirmed internal database best | — | — | **559,493** | 2.86× |
| **Latest original-evaluator-passed candidate (2026-10-27)** | **2,197** | **205** | **450,385** | **3.56×** |
| Published result of Gu *et al.* | 1,140 | 142 | **161,880** | 9.89× |

The latest evaluator-passed candidate represents a **71.9% reduction in TQ** versus the reference baseline. This is a quantum-circuit **resource-product metric**, not a measured runtime speedup. The original paper reported a substantially stronger result.

![Coulomb resource-product comparison](assets/tq_comparison.png)

### What the reported result means

- In the latest dated experiment, the original evaluator produced a `valid=true` result for a candidate with **T=2,197** and **Q=205**; the saved source and result snapshot were preserved and checked for file consistency, according to the internal handoff.
- **An additional rerun, integration into the evolutionary database, and a post-import database check remain pending.** The latest *database* best in the available record is **559,493**. Thus **450,385 is the latest evaluator-passed candidate, not a confirmed database-best state**.
- The reference evaluator uses specified tests, rather than an exhaustive formal proof of functional equivalence. A successful evaluator result is evidence under that test protocol; it is not a universal correctness guarantee.
- The result is based on dated internal experiment records, **not independently audited by this public repository**.

The broader original study contains **eight benchmarks**; this experiment has currently explored **one**, Coulomb potential.

## Scope and unpublished implementation

At a high level, the experiment combines **an LLM coding agent, evolutionary search, and the original reference evaluator**. The full prompts, agent workflow, model settings, search policy, orchestration code, circuit sources, and search logs remain **unpublished research know-how**. Therefore, this is **not a runnable replication package**; neither a software release nor a complete reproducibility claim is implied.

## Attribution and research status

The project uses DeepSeek, OpenCode, OpenEvolve/MAP-Elites, and the original [uncompute evaluator](https://github.com/andigu/uncompute). See [references and attribution](REFERENCES.md), [result and evidence details](RESULTS.md), and [rights notice](RIGHTS.md).

The experiment is ongoing. Future releases may update the results or add methods and reproducibility materials after the research and disclosure strategy is settled. Publication of this summary does not promise when or whether the unpublished implementation will be released.

**Language:** [繁體中文](README_zh-TW.md)
