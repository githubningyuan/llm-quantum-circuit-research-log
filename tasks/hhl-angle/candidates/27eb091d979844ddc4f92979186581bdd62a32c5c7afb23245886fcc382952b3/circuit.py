"""hhl-angle — independent B: octave+mantissa piecewise cubic (160 intervals).

Refactor of the power-of-two-aligned parent: the interval label is split into
an octave s = floor(log2 X) in [16,20] (priority-encoded from the leading bit)
and a 5-bit coarse mantissa u_c.  This collapses 992 uniform-width intervals to
5*2^5 = 160 intervals (QROM ~6x cheaper) while keeping the polynomial variable
as the fine mantissa left-aligned to a fixed 15-bit shift.

h(X) = round_half_away(asin(2^15/X)*2^32), X in [2^16, 2^21-1].
"""

from __future__ import annotations

import math

from uncompute import Builder
from uncompute.gadgets import add, and_chain, declare_registers, gidney_add, walk, xor_word

X_MIN = 1 << 16
X_MAX = (1 << 21) - 1
X_BITS = 21
OUTPUT_BITS = 33
OUTPUT_FRAC = 32

M = 5                   # mantissa bits
SOCT = 16               # first octave
NOCT = 5                # octaves 16..20
NINT = NOCT << M        # 160 intervals
SHIFT = 15              # fixed multiplier shift (max s-m = 20-5)

FRAC_BITS = 29
ROUND_SHIFT = FRAC_BITS - OUTPUT_FRAC
ROUND_HALF = (1 << (ROUND_SHIFT - 1)) if ROUND_SHIFT >= 1 else 0
OUT_SHIFT = OUTPUT_FRAC - FRAC_BITS

W3W = 15
W2W = 20
W1W = 25
W0W = 30
M3 = (1 << W3W) - 1
M2 = (1 << W2W) - 1
M1 = (1 << W1W) - 1
M0 = (1 << W0W) - 1

ADDR_BITS = 8  # (octave 3 bits) | (mantissa 5 bits)


def _fit_poly(xs, ys, deg):
    n = deg + 1
    A = [[0.0] * n for _ in range(n)]
    B = [0.0] * n
    for x, y in zip(xs, ys):
        p = [x ** k for k in range(n)]
        for i in range(n):
            B[i] += p[i] * y
            for j in range(n):
                A[i][j] += p[i] * p[j]
    M_ = [A[i][:] + [B[i]] for i in range(n)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M_[r][col]))
        M_[col], M_[piv] = M_[piv], M_[col]
        d = M_[col][col]
        for j in range(col, n + 1):
            M_[col][j] /= d
        for r in range(n):
            if r != col:
                f = M_[r][col]
                for j in range(col, n + 1):
                    M_[r][j] -= f * M_[col][j]
    return [M_[i][n] for i in range(n)]


def _coefficients():
    scale = 1 << FRAC_BITS
    w0 = [0] * NINT
    w1 = [0] * NINT
    w2 = [0] * NINT
    w3 = [0] * NINT
    for si in range(NOCT):
        s = SOCT + si
        for uc in range(1 << M):
            xlo = (1 << s) + uc * (1 << (s - M))
            width = 1 << (s - M)
            xs, ys = [], []
            for k in range(12):
                tv = (1 - math.cos(math.pi * (k + 0.5) / 12)) / 2
                x = min(xlo + tv * width, X_MAX)
                xs.append(tv)
                ys.append(math.asin((1 << 15) / x))
            c = _fit_poly(xs, ys, 3)
            j = (si << M) | uc
            w0[j] = (int(round(c[0] * scale)) + ROUND_HALF) & M0
            w1[j] = int(round(c[1] * scale)) & M1
            w2[j] = int(round(c[2] * scale)) & M2
            w3[j] = int(round(c[3] * scale)) & M3
    return w0, w1, w2, w3


def mul_add(b, stage, V, W, u, shift):
    top = len(V) - 1
    for bit in range(len(V)):
        b.set_phase(f"{stage}/b{bit:02d}")
        T = W[max(0, bit - shift):]
        A = u[max(0, shift - bit):][: len(T)]
        if bit == top:
            for q in T:
                b.x(q)
            add(b, T, A, ctrl=V[bit])
            for q in T:
                b.x(q)
        else:
            add(b, T, A, ctrl=V[bit])


def _mux_xor(b, e_s, sel, nbits, target, XQ):
    for k in range(nbits):
        for si, e in enumerate(e_s):
            xb = sel(si, k)
            if xb is None or xb < 0 or xb >= X_BITS:
                continue
            t = b.and_compute(e, XQ[xb])
            b.cx(t, target[k])
            b.and_release(t, e, XQ[xb])


def build():
    w0, w1, w2, w3 = _coefficients()

    b = Builder()
    regs = declare_registers(b, [("X", X_BITS), ("k", OUTPUT_BITS)])
    XQ, KQ = regs["X"], regs["k"]

    m_start = b.checkpoint()

    # ---- one-hot octave encode e_s (s=16..20) ---------------------------
    e20 = XQ[20]
    e19, _t19 = and_chain(b, [(XQ[20], False), (XQ[19], True)])
    e18, _t18 = and_chain(b, [(XQ[20], False), (XQ[19], False), (XQ[18], True)])
    e17, _t17 = and_chain(b, [(XQ[20], False), (XQ[19], False), (XQ[18], False), (XQ[17], True)])
    e16, _t16 = and_chain(b, [(XQ[20], False), (XQ[19], False), (XQ[18], False), (XQ[17], False)])
    e_s = [e16, e17, e18, e19, e20]

    # ---- address (8 bits) = (octave 3b) | (mantissa 5b) -----------------
    addr = b.qubits(ADDR_BITS)
    b.cx(e20, addr[7])
    b.cx(e18, addr[6])
    b.cx(e19, addr[6])
    b.cx(e17, addr[5])
    b.cx(e19, addr[5])
    # coarse mantissa u_c -> addr[4:0]
    _mux_xor(b, e_s, lambda si, k: 11 + si + k, 5, addr[0:5], XQ)

    # ---- fine mantissa u_fine' (left-aligned to 15 bits) -----------------
    u = b.qubits(SHIFT)
    _mux_xor(b, e_s, lambda si, k: k + si - 4, SHIFT, u, XQ)

    # ---- QROM load 4 coefficient words (one walk) ------------------------
    W3 = b.qubits(W3W)
    W2 = b.qubits(W2W)
    W1 = b.qubits(W1W)
    W0 = b.qubits(W0W)

    def body(ctrl, j):
        xor_word(b, ctrl, w3[j], W3)
        xor_word(b, ctrl, w2[j], W2)
        xor_word(b, ctrl, w1[j], W1)
        xor_word(b, ctrl, w0[j], W0)

    b.set_phase("label/load")
    walk(b, addr, None, ADDR_BITS - 1, 0, 1 << ADDR_BITS, 0, NINT, body)

    mul_add(b, "h2", W3, W2, u, SHIFT)
    mul_add(b, "h1", W2, W1, u, SHIFT)
    mul_add(b, "h0", W1, W0, u, SHIFT)

    m_out = b.checkpoint()
    b.set_phase("round/add")
    if ROUND_SHIFT >= 0:
        gidney_add(b, W0[ROUND_SHIFT:ROUND_SHIFT + OUTPUT_BITS], KQ)
    else:
        gidney_add(b, W0[0:W0W], KQ[OUT_SHIFT:OUT_SHIFT + W0W])

    b.mirror(m_start, until=m_out)

    return b, regs
