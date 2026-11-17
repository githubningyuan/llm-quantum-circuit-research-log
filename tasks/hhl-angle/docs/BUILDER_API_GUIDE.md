# Generic Builder API — permitted tool reference

Source basis: pinned `uncompute/builder.py`, SHA256 `8f850c36b04dd23c901c1029798fc96537f19922211eceab2fefc5d6fd8c91a5`.
This is generic API guidance, not a task solution. Availability of the Builder modules in a sandbox run must be confirmed by the approved research tool.

## Generic interface

- `from uncompute.builder import Builder`: import the generic circuit recorder when its pinned module bundle is included as sandbox input.
- `b = Builder()`: create a new recorder.
- `b.qubit()`, `b.qubits(n)`: allocate one or multiple qubits; `qubits(n)` returns an ordered `WireList`.
- `b.qubit_register(qubits)`: declare a qubit input/output register; declare registers in the order dictated by the task, outside repeat blocks.
- `b.x(q)`, `b.cx(ctrl, tgt)`, `b.ccx(c1, c2, tgt)`, `b.swap(a, b)`: primitive reversible gates.
- `b.checkpoint()` and `b.mirror(mark)`: generic inverse-region recording; unsupported or unbalanced sequences can raise an exception.
- `b.recycle(q)`: recycle a clean, unneeded scratch qubit. Recycling a dirty scratch qubit may fail evaluator checks.
- `b.peak_qubits`, `b.active_qubits`: recorder bookkeeping; the trusted evaluator, not local bookkeeping, determines accepted resource metrics.
- `b.emit(meta=False, dest=fh)`: serialize a compact op stream to a **writable binary file object**; `dest` is not a path string. The serialized format is `OPSTRM01`.

## Minimal API-shape example (intentionally NOT a correct HHL circuit)

```python
from uncompute.builder import Builder

b = Builder()
X = b.qubits(21)
k = b.qubits(33)
b.qubit_register(X)
b.qubit_register(k)
with open('/work/outputs/probe.ops', 'wb') as f:
    b.emit(meta=False, dest=f)
```

This example only shows register declaration and file emission. It does not implement the required map and will fail HHL correctness evaluation. Agent-authored operations must implement and preserve the actual task semantics.

## Bounds

A sandbox program writes output artifacts beneath `/work/outputs/` and returns their Scratch-relative artifact paths through `research_python`. A separate `quantum_evaluate` call evaluates the selected raw op stream. Do not infer correctness from `Builder.emit()` success alone.
