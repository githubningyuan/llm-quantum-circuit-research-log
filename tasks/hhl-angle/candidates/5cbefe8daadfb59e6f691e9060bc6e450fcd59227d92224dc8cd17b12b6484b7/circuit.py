#!/usr/bin/env python3
"""Independent HHL-angle circuit: geometric-interval piecewise cubic.

Distinct architecture from the HRS/Remez baseline: a Chebyshev-interpolating
cubic on a *geometric* interval grid (80 intervals, ratio 2^(5/80)), each
interval's polynomial fitted in u = (X - base)/2^16 with 4 Chebyshev nodes
(exact float node positions, so no node-rounding floor).  Fixed-point word
36 bits (INT 2 + FRAC 34), 17-bit range-reduced operand, unpebbled Horner
ladder with the lead coefficient word pebbled (self-inverse QROM reload),
full Bennett mirror.  The constants are fitted at build time in pure Python
(no numpy), matching the exact emitted fixed-point semantics.
"""

from __future__ import annotations

import math

from uncompute import Builder
from uncompute.gadgets import (
    declare_registers,
    gidney_add,
    gidney_sub,
    hrs_mul_rows,
    label_increment,
    walk,
    xor_word,
)

DOMAIN_LO = 1 << 16
DOMAIN_HI = 1 << 21
INPUT_FRAC_BITS = 16
OUTPUT_FRAC_BITS = 32
OUTPUT_BITS = 33
X_BITS = 21

WORD_BITS = 36
INT_BITS = 2
FRAC_BITS = WORD_BITS - INT_BITS  # 34
DEGREE = 3
INTERVALS = 80
LABEL_BITS = 7
assert (1 << LABEL_BITS) >= INTERVALS > (1 << (LABEL_BITS - 1))
ROUND_SHIFT = FRAC_BITS - OUTPUT_FRAC_BITS  # 2
ROUND_HALF = 1 << (ROUND_SHIFT - 1)  # 2
WORD_MASK = (1 << WORD_BITS) - 1
XP_BITS = 17


# ---------------------------------------------------------------------------
# Fitting (pure Python): geometric grid + degree-3 Chebyshev interpolation.
# ---------------------------------------------------------------------------

def _gauss(a, b, n):
    for col in range(n):
        piv = col
        for r in range(col + 1, n):
            if abs(a[r][col]) > abs(a[piv][col]):
                piv = r
        a[col], a[piv] = a[piv], a[col]
        b[col], b[piv] = b[piv], b[col]
        d = a[col][col]
        for c in range(col, n):
            a[col][c] /= d
        b[col] /= d
        for r in range(n):
            if r != col:
                f = a[r][col]
                if f:
                    for c in range(col, n):
                        a[r][c] -= f * a[col][c]
                    b[r] -= f * b[col]
    return b


def _geometric_bases(R):
    ratio = (DOMAIN_HI / DOMAIN_LO) ** (1.0 / R)
    bases = [DOMAIN_LO]
    x = float(DOMAIN_LO)
    for _ in range(R):
        x = x * ratio
        xi = int(round(x))
        if xi <= bases[-1]:
            xi = bases[-1] + 1
        if xi >= (DOMAIN_HI - 1):
            break
        bases.append(xi)
    return bases


def _fit_cubic(base, width):
    U = width / (1 << INPUT_FRAC_BITS)
    ts = []
    ys = []
    for k in range(DEGREE + 1):
        t = 0.5 * (1.0 + math.cos((2 * k + 1) * math.pi / (2 * (DEGREE + 1))))
        ts.append(t)
        ys.append(math.asin((1 << 15) / (base + t * width)))
    A = [[ts[i] ** j for j in range(DEGREE + 1)] for i in range(DEGREE + 1)]
    d = _gauss([row[:] for row in A], ys[:], DEGREE + 1)
    return [d[i] / (U ** i) for i in range(DEGREE + 1)]


def _quantize(c):
    q = int(round(c * (1 << FRAC_BITS)))
    lim = 1 << (WORD_BITS - 1)
    if q <= -lim:
        q = -lim + 1
    if q >= lim:
        q = lim - 1
    return q


def fit_constants():
    bases = _geometric_bases(INTERVALS)
    ends = bases[1:] + [(DOMAIN_HI - 1)]
    qtab = []
    for i, base in enumerate(bases):
        end = ends[i]
        width = end - base + 1
        cs = _fit_cubic(base, width)
        c0 = _quantize(cs[0])
        c1 = _quantize(cs[1])
        c2 = _quantize(cs[2])
        c3 = _quantize(cs[3])
        qtab.append([c0, c1, c2, c3])
    bounds = bases[1:]
    return qtab, bounds, bases


# ---------------------------------------------------------------------------
# The circuit.
# ---------------------------------------------------------------------------

def build_circuit(qtab, bounds, bases):
    b = Builder()
    regs = declare_registers(b, [("X", X_BITS), ("k", OUTPUT_BITS)])
    XQ, KQ = regs["X"], regs["k"]
    b.claim("X", XQ)
    b.claim("k", KQ)

    w3 = [q[3] & WORD_MASK for q in qtab]
    w2 = [q[2] & WORD_MASK for q in qtab]
    w1 = [q[1] & WORD_MASK for q in qtab]
    w0 = [(q[0] + ROUND_HALF) & WORD_MASK for q in qtab]

    base_low = [base & ((1 << XP_BITS) - 1) for base in bases]

    def coef_load_all(words, W):
        def body(ctrl, addr):
            xor_word(b, ctrl, words[addr], W)

        walk(b, lbl, None, LABEL_BITS - 1, 0, 1 << LABEL_BITS, 0, len(words), body)

    def base_load_all(words, W):
        def body(ctrl, addr):
            xor_word(b, ctrl, words[addr], W)

        walk(b, lbl, None, LABEL_BITS - 1, 0, 1 << LABEL_BITS, 0, len(words), body)

    # ---- 1. label ----------------------------------------------------------
    lbl = b.qubits(LABEL_BITS)
    b.claim("label", lbl)
    m_label = b.checkpoint()
    for i, bound in enumerate(bounds):
        b.set_phase(f"label/b{i:02d}")
        label_increment(b, XQ, bound, i, lbl)
    m_labelend = b.checkpoint()

    # ---- 1b. range reduction -----------------------------------------------
    b.set_phase("base/load")
    B = b.qubits(XP_BITS)
    b.claim("base", B)
    base_load_all(base_low, B)
    Xp = b.qubits(XP_BITS)
    b.claim("xp", Xp)
    m_xp = b.checkpoint()
    for t in range(XP_BITS):
        b.cx(XQ[t], Xp[t])
    gidney_sub(b, B, Xp)
    m_xpend = b.checkpoint()

    # ---- 2. lead -----------------------------------------------------------
    b.set_phase("lead/load")
    V3 = b.qubits(WORD_BITS)
    b.claim("poly3", V3)
    m_v3 = b.checkpoint()
    coef_load_all(w3, V3)
    m_v3end = b.checkpoint()

    # ---- 3. h2 -------------------------------------------------------------
    b.set_phase("h2/load")
    V2 = b.qubits(WORD_BITS)
    b.claim("poly2", V2)
    m_v2 = b.checkpoint()
    coef_load_all(w2, V2)
    hrs_mul_rows(b, "h2", V3, V2, Xp, INPUT_FRAC_BITS)
    m_v2end = b.checkpoint()

    # ---- pebble: unload and free V3 ----------------------------------------
    b.set_phase("lead/unload")
    coef_load_all(w3, V3)
    b.release("poly3", V3)
    for q in reversed(V3):
        b.recycle(q)

    # ---- 4. h1 -------------------------------------------------------------
    b.set_phase("h1/load")
    V1 = b.qubits(WORD_BITS)
    b.claim("poly1", V1)
    m_v1 = b.checkpoint()
    coef_load_all(w1, V1)
    hrs_mul_rows(b, "h1", V2, V1, Xp, INPUT_FRAC_BITS)
    m_v1end = b.checkpoint()

    # ---- 5. h0 -------------------------------------------------------------
    b.set_phase("h0/load")
    V0 = b.qubits(WORD_BITS)
    b.claim("poly0", V0)
    m_v0 = b.checkpoint()
    coef_load_all(w0, V0)
    hrs_mul_rows(b, "h0", V1, V0, Xp, INPUT_FRAC_BITS)
    m_v0end = b.checkpoint()

    # ---- 6. round ----------------------------------------------------------
    b.set_phase("round/add")
    gidney_add(b, V0[ROUND_SHIFT : ROUND_SHIFT + OUTPUT_BITS], KQ)

    # ---- 7. un-h0 ----------------------------------------------------------
    b.set_phase("un-h0")
    b.mirror(m_v0, until=m_v0end)
    b.release("poly0", V0)
    for q in reversed(V0):
        b.recycle(q)

    # ---- 8. un-h1 ----------------------------------------------------------
    b.set_phase("un-h1")
    b.mirror(m_v1, until=m_v1end)
    b.release("poly1", V1)
    for q in reversed(V1):
        b.recycle(q)

    # ---- 9. reload V3 ------------------------------------------------------
    b.set_phase("lead/reload")
    V3 = b.qubits(WORD_BITS)
    b.claim("poly3", V3)
    coef_load_all(w3, V3)

    # ---- 10. un-h2 ---------------------------------------------------------
    b.set_phase("un-h2")
    b.mirror(m_v2, until=m_v2end)
    b.release("poly2", V2)
    for q in reversed(V2):
        b.recycle(q)

    # ---- 11. un-lead -------------------------------------------------------
    b.set_phase("un-lead")
    coef_load_all(w3, V3)
    b.release("poly3", V3)
    for q in reversed(V3):
        b.recycle(q)

    # ---- 12. un-range-reduction --------------------------------------------
    b.set_phase("un-xp")
    b.mirror(m_xp, until=m_xpend)
    b.release("xp", Xp)
    for q in reversed(Xp):
        b.recycle(q)
    b.set_phase("un-base")
    base_load_all(base_low, B)
    b.release("base", B)
    for q in reversed(B):
        b.recycle(q)

    # ---- 13. un-label ------------------------------------------------------
    b.set_phase("un-label")
    b.mirror(m_label, until=m_labelend)
    b.release("label", lbl)
    for q in reversed(lbl):
        b.recycle(q)

    return b, regs


def build():
    qtab, bounds, bases = fit_constants()
    assert len(qtab) == INTERVALS
    assert len(bounds) == INTERVALS - 1
    assert len(bases) == INTERVALS
    sign = 1 << (WORD_BITS - 1)
    assert all(len(row) == DEGREE + 1 and all(-sign <= q < sign for q in row) for row in qtab)
    return build_circuit(qtab, bounds, bases)


if __name__ == "__main__":
    b, _regs = build()
    print("T =", b.phase_summary()["total_emitted_toffoli"], "Q =", b.peak_qubits)
