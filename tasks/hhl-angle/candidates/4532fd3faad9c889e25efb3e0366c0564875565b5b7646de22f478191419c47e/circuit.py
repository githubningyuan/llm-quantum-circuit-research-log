#!/usr/bin/env python3
"""hhl-angle haner-piecewise: the direct
HRS piecewise-polynomial circuit (arXiv:1805.12445) — degree 3 on 35
Remez intervals in signed fixed point (44, 6), unpebbled Horner
ladder, full Bennett mirror.

Certified cost: 16,468 Toffoli x 300 qubits = 4,940,400
(assert_metrics in the epilogue).

RUN: uv run python circuit.py   (stream to $UNCOMPUTE_OPS_PATH, else ./ops.bin)
     uv run python checks.py   (the exhaustive datapath check)

README.md in this folder describes the circuit.
"""

from __future__ import annotations

import json
import pathlib

from uncompute import Builder
from uncompute.bennett import Stages
from uncompute.gadgets import (
    declare_registers,
    gidney_add,
    hrs_mul_rows,
    label_increment,
    walk,
    xor_word,
)
from uncompute.testing import assert_metrics, emit_and_report, load_contract

# ---------------------------------------------------------------------------
# Instance constants (semantic scalars; the task-defining derived
# constants come from the shared contract.json below)
# ---------------------------------------------------------------------------
DOMAIN_RAW_LO = 1 << 16
DOMAIN_RAW_HI = 1 << 21
INPUT_FRAC_BITS = 16
OUTPUT_FRAC_BITS = 32
OUTPUT_BITS = 33
X_BITS = 21

WORD_BITS = 42  # n: signed fixed-point word
INT_BITS = 4  # p: integer bits (max |coef| = 4.06, max |intermediate| = 2.10)
FRAC_BITS = WORD_BITS - INT_BITS
DEGREE = 3  # per-interval polynomial degree
INTERVALS = 35
WORST_ULP = 429  # exhaustive-check pin (checks.py)

LABEL_BITS = 6  # ceil(log2(INTERVALS))
assert (1 << LABEL_BITS) >= INTERVALS > (1 << (LABEL_BITS - 1))
ROUND_SHIFT = FRAC_BITS - OUTPUT_FRAC_BITS  # 6: bits dropped at round
ROUND_HALF = 1 << (ROUND_SHIFT - 1)  # +2^5, folded into c0
WORD_MASK = (1 << WORD_BITS) - 1

_CONTRACT = load_contract(__file__)
ALLOWED_ULP = _CONTRACT["eps_ulp"]  # the band the exhaustive check prices against
assert FRAC_BITS >= OUTPUT_FRAC_BITS


# ---------------------------------------------------------------------------
# The circuit.  Every helper closes over the live Builder; AND ancillas are
# the only anonymous transients (Gidney pairs).
# ---------------------------------------------------------------------------


def build_circuit(qtab, bounds):
    """Emit the full circuit for the given quantized tables and interval
    boundaries; return (Builder, register dict).

    This variant pebbles the leading coefficient word V3: it is unloaded
    (re-loaded, since the QROM load is self-inverse) and freed right after
    the h2 multiplier runs, then re-loaded before the h2 mirror.  Dropping
    one live 42-bit word at the peak (the h0 multiplier) cuts peak qubits
    from 290 to 248 for ~+2 QROM-walk Toffoli, a net T*Q reduction.
    """
    b = Builder()
    regs = declare_registers(b, [("X", X_BITS), ("k", OUTPUT_BITS)])
    XQ, KQ = regs["X"], regs["k"]
    b.claim("X", XQ)
    b.claim("k", KQ)

    # Coefficient words as loaded (two's complement on the 44-bit word);
    # the +2^5 rounding offset rides the degree-0 word.  The checked final
    # value stays below 2^43, so the offset never wraps the word.
    w3 = [q[3] & WORD_MASK for q in qtab]
    w2 = [q[2] & WORD_MASK for q in qtab]
    w1 = [q[1] & WORD_MASK for q in qtab]
    w0 = [(q[0] + ROUND_HALF) & WORD_MASK for q in qtab]
    assert [(w - ROUND_HALF) & WORD_MASK for w in w0] == [q[0] & WORD_MASK for q in qtab]

    def coef_load_all(words, W):
        def body(ctrl, addr):
            xor_word(b, ctrl, words[addr], W)

        walk(b, lbl, None, LABEL_BITS - 1, 0, 1 << LABEL_BITS, 0, len(words), body)

    # ---- 1. label: 34 comparators drive the label counter ---------------
    lbl = b.qubits(LABEL_BITS)
    b.claim("label", lbl)
    m_label = b.checkpoint()
    for i, bound in enumerate(bounds):
        b.set_phase(f"label/b{i:02d}")
        label_increment(b, XQ, bound, i, lbl)
    m_labelend = b.checkpoint()

    # ---- 2. lead: V3 = c3[label] ----------------------------------------
    b.set_phase("lead/load")
    V3 = b.qubits(WORD_BITS)
    b.claim("poly3", V3)
    m_v3 = b.checkpoint()
    coef_load_all(w3, V3)
    m_v3end = b.checkpoint()

    # ---- 3. h2: V2 = c2[label] + V3 (x) x_word --------------------------
    b.set_phase("h2/load")
    V2 = b.qubits(WORD_BITS)
    b.claim("poly2", V2)
    m_v2 = b.checkpoint()
    coef_load_all(w2, V2)
    hrs_mul_rows(b, "h2", V3, V2, XQ, INPUT_FRAC_BITS)
    m_v2end = b.checkpoint()

    # ---- pebble: unload V3 (self-inverse QROM re-load) and free it ------
    b.set_phase("lead/unload")
    coef_load_all(w3, V3)
    b.release("poly3", V3)
    for q in reversed(V3):
        b.recycle(q)

    # ---- 4. h1: V1 = c1[label] + V2 (x) x_word --------------------------
    b.set_phase("h1/load")
    V1 = b.qubits(WORD_BITS)
    b.claim("poly1", V1)
    m_v1 = b.checkpoint()
    coef_load_all(w1, V1)
    hrs_mul_rows(b, "h1", V2, V1, XQ, INPUT_FRAC_BITS)
    m_v1end = b.checkpoint()

    # ---- 5. h0: V0 = c0[label] + 2^5 + V1 (x) x_word --------------------
    b.set_phase("h0/load")
    V0 = b.qubits(WORD_BITS)
    b.claim("poly0", V0)
    m_v0 = b.checkpoint()
    coef_load_all(w0, V0)
    hrs_mul_rows(b, "h0", V1, V0, XQ, INPUT_FRAC_BITS)
    m_v0end = b.checkpoint()

    # ---- 6. round: k += V0[6:39) mod 2^33 -------------------------------
    b.set_phase("round/add")
    gidney_add(b, V0[ROUND_SHIFT : ROUND_SHIFT + OUTPUT_BITS], KQ)

    # ---- 7. un-h0 -------------------------------------------------------
    b.set_phase("un-h0")
    b.mirror(m_v0, until=m_v0end)
    b.release("poly0", V0)
    for q in reversed(V0):
        b.recycle(q)

    # ---- 8. un-h1 -------------------------------------------------------
    b.set_phase("un-h1")
    b.mirror(m_v1, until=m_v1end)
    b.release("poly1", V1)
    for q in reversed(V1):
        b.recycle(q)

    # ---- 9. reload V3 ---------------------------------------------------
    b.set_phase("lead/reload")
    V3 = b.qubits(WORD_BITS)
    b.claim("poly3", V3)
    coef_load_all(w3, V3)

    # ---- 10. un-h2 ------------------------------------------------------
    b.set_phase("un-h2")
    b.mirror(m_v2, until=m_v2end)
    b.release("poly2", V2)
    for q in reversed(V2):
        b.recycle(q)

    # ---- 11. un-lead (reloaded V3): self-inverse unload ------------------
    b.set_phase("un-lead")
    coef_load_all(w3, V3)
    b.release("poly3", V3)
    for q in reversed(V3):
        b.recycle(q)

    # ---- 12. un-label ---------------------------------------------------
    b.set_phase("un-label")
    b.mirror(m_label, until=m_labelend)
    b.release("label", lbl)
    for q in reversed(lbl):
        b.recycle(q)

    return b, regs


def build():
    """Run the raw build (also the profiler tools' entry): load the tables
    that gen_constants.py fits, check their shapes, and construct the
    circuit.  Returns (Builder, register dict).
    """
    tables = json.loads((pathlib.Path(__file__).resolve().parent / "constants.json").read_text())
    qtab, bounds = tables["qtab"], tables["bounds"]
    sign = 1 << (WORD_BITS - 1)
    assert len(qtab) == INTERVALS, len(qtab)
    assert all(len(row) == DEGREE + 1 and all(-sign <= q < sign for q in row) for row in qtab)
    assert len(bounds) == INTERVALS - 1, len(bounds)
    assert all(DOMAIN_RAW_LO < a < c < DOMAIN_RAW_HI for a, c in zip(bounds, bounds[1:]))
    return build_circuit(qtab, bounds)


if __name__ == "__main__":
    b, _regs = build()
    # The peak sits at the widest multiplier row (h0/b16) and decomposes
    # as the register file (X + k + label + 4 words = 236) + that row's
    # transients (43 chain ANDs + 21 addend ANDs = 64).
    assert_metrics(b, __file__)
    emit_and_report(b)
