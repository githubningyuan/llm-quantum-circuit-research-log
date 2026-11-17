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
    """
    b = Builder()
    st = Stages(b)
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

    # ---- coefficient selection: unary QROM walk + CX loads ---------------
    # A single qrom.walk iterates all 35 addresses at ~33 Toffoli (one per
    # two-sided internal node) and CX-loads the selected 44-bit coefficient
    # word under each address's control wire — a factor ~5 cheaper than the
    # per-interval and_ladder match (5 Toffoli x 35).
    def coef_load_all(words, W):
        def body(ctrl, addr):
            xor_word(b, ctrl, words[addr], W)

        walk(b, lbl, None, LABEL_BITS - 1, 0, 1 << LABEL_BITS, 0, len(words), body)

    # ---- 1. label: 34 comparators drive the label counter ---------------
    with st.stage("label/setup", set_name="label"):
        b.set_phase("label/setup")
        lbl = b.qubits(LABEL_BITS)
        b.claim("label", lbl)
    for i, bound in enumerate(bounds):
        b.set_phase(f"label/b{i:02d}")
        # The bounds ascend strictly (asserted), so the label counts
        # the bounds <= X at 0 Toffoli beyond each comparator.
        label_increment(b, XQ, bound, i, lbl)

    # ---- 2..5. lead/h2/h1/h0: the unpebbled Horner ladder ----------------
    # One core, four stages: the stage head allocates the stage word,
    # then the body CX-loads the selected coefficient and (h-stages only)
    # runs the multiplier rows against the previous word.  The h0 word
    # table (w0) carries the folded +2^5.
    stages = [
        ("lead", "poly3", w3),  # V3 = c3[label]
        ("h2", "poly2", w2),  # V2 = c2[label] + V3 (x) x_word
        ("h1", "poly1", w1),  # V1 = c1[label] + V2 (x) x_word
        ("h0", "poly0", w0),
    ]  # V0 = c0[label] + 2^5 + V1 (x) x_word
    V = None
    for stage, setname, words in stages:
        with st.stage(f"{stage}/load", set_name=setname):
            b.set_phase(f"{stage}/load")
            W = b.qubits(WORD_BITS)
            b.claim(setname, W)
        coef_load_all(words, W)
        if V is not None:
            hrs_mul_rows(b, stage, V, W, XQ, INPUT_FRAC_BITS)
        V = W

    mark_round = b.checkpoint()
    V0 = V  # the poly0 word: Horner value + 2^5

    # ---- 6. round: k += V0[6:39) mod 2^33 -------------------------------
    b.set_phase("round/add")
    gidney_add(b, V0[ROUND_SHIFT : ROUND_SHIFT + OUTPUT_BITS], KQ)  # ty: ignore[not-subscriptable]  # stages is non-empty, so V is set

    # ---- 7. un-*: Bennett mirror of stages 5..1 -------------------------
    # st.unwind replays every recorded stage in reverse — per stage:
    # set_phase(un-<name>); mirror the body (the stage's wires return to
    # |0>); release the ownership set; mirror the head (replaying the
    # frees) — so every set is released exactly when its wires are
    # provably |0>.  The segment mirrors concatenate to the reversal of
    # the whole region, so the op stream equals the single-call mirror.
    st.unwind(until=mark_round)

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
