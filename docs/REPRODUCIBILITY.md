# Evidence and reproduction boundaries

## Native results

Run `python tools/verify_archive.py` (stdlib only), then `python tools/reproduce.py both` after installing dependencies. Reproduction uses a new wrapper around the original candidate `build()` functions. It intentionally does not execute the historical `__main__` epilogue with fixed-cost baseline pins; the original native evaluator independently checks actual outputs, sign, cleanliness and T/Q. Historical Coulomb instrumented evaluator_input.py is archived separately for inspection, not executed by this wrapper. No historical source circuit was modified. Python CLI and library are exactly the exported native copies; compiled C kernel source/binary was not in the VM export, so reproduction selects the Python fallback.

| Task | Native verdict | Tested live shots | Wrong output/sign/dirty | OPS SHA256 comparison |
|---|---|---:|---|---|
| HHL | OK | 8,765 | 0 / 0 / 0 | emitted source stream exactly matches VM event |
| Coulomb | OK | 9,017 | 0 / 0 / 0 | newly generated stream; historical independent receipt did not supply an OPS hash |

The original grader planned 9,024 shots; invalid/out-of-domain draws are skipped. Live shot counts above are from the replay reports, not exhaustive domain sizes. TQ is exact for the accepted native stream; numerical correctness is within the grader's sampling protocol.

## Archive structure

HHL `candidates.csv` includes 39 stored programs, 37 new and 2 prior seeds. Every new candidate has exact source files, a source map with original submission paths, compressed raw original OPS, native result and normalized source-binding receipt. Original library ZIP is stored once (exact bytes, needed to reconstruct source identity). `verify_archive.py` recomputes the original bundle identity and OPS digest. Two historical seeds are not counted as new and do not have this same event-level receipt. The source correlation is the controller's original Broker/Evaluator correlation, not a signed attestation; ZIP contents cannot establish original OS ownership. Session data includes counters and ancestry, not raw model transcripts. Controller source snapshots preserve relevant runtime code and historical dependencies, with original package hashes in deployment-source-hashes.json. Account home path was replaced with `/home/researcher`; test files and original package manifests were omitted. These edited archival source trees are NOT bitwise original deployable packages; originals remain in the private VM exports.

Coulomb `candidates.csv` has 284 archive membership records across production79, control88, treatment117. They reference 126 unique source circuits, kept once per ID. 120 distinct programs have matching independently graded source-hash receipts in the export; six do not have such a matching receipt and should not be described as independently reverified here. MAP-Elites archive metadata retained separately. Historical best has exact source and receipt; fresh OPS compressed separately. The normalized datasets are analysis-ready, not direct OpenEvolve database checkpoints. Historical VM source uses original VM-specific services and paths; missing Coulomb OpenCode tools and infrastructure are not reconstructed. The actual full paid workflow was not replayed, nor are paid model credentials or private prompts distributed.

## Public and private separation

Only selected source, structured metrics, source-linked evidence, public-note history, and original task license are included. Entire private export archives, system journals, temporary workspaces, model-session logs and credentials are excluded. Original private exports are retained by the owner for deeper research and audit. The new repo does not prove a causal advantage for cross-family inspiration or cross-task transfer. Detailed analysis is deferred.
