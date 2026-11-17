"""hhl-angle — independent B: power-of-two-aligned cubic piecewise polynomial.

Distinct from the 35-Remez-interval baseline: intervals are aligned to
width 2^11 so the interval label is the raw high bits of X (no
comparators), the polynomial variable is the low 11 bits of X (u-basis),
all four coefficients are QROM-loaded in one unary walk, and the Horner
ladder runs three pre-truncated signed multiplies into the degree-0 word.

Variable-width words (the coefficient magnitudes differ by ~2^15):
c3~20b, c2~25b, c1~30b, c0~35b -> the three multiplies are narrower than
a uniform 35-bit ladder.

h(X) = round_half_away(asin(2^15/X) * 2^32), X in [2^16, 2^21-1].
"""

from __future__ import annotations

import math

from uncompute import Builder
from uncompute.bennett import Stages
from uncompute.gadgets import add, declare_registers, gidney_add, walk, xor_word

X_MIN = 1 << 16
X_MAX = (1 << 21) - 1
X_BITS = 21
OUTPUT_BITS = 33
OUTPUT_FRAC = 32

WIDTH = 2048          # interval width (2^11)
SHIFT = 11            # log2(WIDTH); polynomial variable v = u / 2^11
INT_BITS = 1
FRAC_BITS = 32
DEGREE = 3
LABEL_BITS = 10
QLO = X_MIN // WIDTH   # 32
QHI = X_MAX // WIDTH   # 1023
NINTERVALS = QHI - QLO + 1

ROUND_SHIFT = FRAC_BITS - OUTPUT_FRAC
ROUND_HALF = (1 << (ROUND_SHIFT - 1)) if ROUND_SHIFT >= 1 else 0

# per-stage word widths (signed): value ranges verified by design_vw.py / fraccheck.py
W3W = 18
W2W = 23
W1W = 28
W0W = 33
M3 = (1 << W3W) - 1
M2 = (1 << W2W) - 1
M1 = (1 << W1W) - 1
M0 = (1 << W0W) - 1


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
    M = [A[i][:] + [B[i]] for i in range(n)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        M[col], M[piv] = M[piv], M[col]
        d = M[col][col]
        for j in range(col, n + 1):
            M[col][j] /= d
        for r in range(n):
            if r != col:
                f = M[r][col]
                for j in range(col, n + 1):
                    M[r][j] -= f * M[col][j]
    return [M[i][n] for i in range(n)]


def _fit_interval(xlo):
    xs, ys = [], []
    np = 16
    for k in range(np):
        tv = (1 - math.cos(math.pi * (k + 0.5) / np)) / 2
        x = min(xlo + tv * WIDTH, X_MAX)
        xs.append(tv)
        ys.append(math.asin((1 << 15) / x))
    return _fit_poly(xs, ys, DEGREE)


def _coefficients():
    scale = 1 << FRAC_BITS
    w0 = [0] * NINTERVALS
    w1 = [0] * NINTERVALS
    w2 = [0] * NINTERVALS
    w3 = [0] * NINTERVALS
    for q in range(QLO, QHI + 1):
        c = _fit_interval(q * WIDTH)
        j = q - QLO
        w0[j] = (int(round(c[0] * scale)) + ROUND_HALF) & M0
        w1[j] = int(round(c[1] * scale)) & M1
        w2[j] = int(round(c[2] * scale)) & M2
        w3[j] = int(round(c[3] * scale)) & M3
    return w0, w1, w2, w3


def mul_add(b, stage, V, W, u, shift):
    """W += V * u >> shift, V signed two's complement (len(V) <= len(W))."""
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


def build():
    w0, w1, w2, w3 = _coefficients()

    b = Builder()
    st = Stages(b)
    regs = declare_registers(b, [("X", X_BITS), ("k", OUTPUT_BITS)])
    XQ, KQ = regs["X"], regs["k"]
    lbl = XQ[SHIFT : SHIFT + LABEL_BITS]
    u = XQ[0:SHIFT]

    with st.stage("poly/load", set_name="poly"):
        W3 = b.qubits(W3W)
        W2 = b.qubits(W2W)
        W1 = b.qubits(W1W)
        W0 = b.qubits(W0W)
        b.claim("poly", W3)
        b.claim("poly", W2)
        b.claim("poly", W1)
        b.claim("poly", W0)

    def body(ctrl, addr):
        j = addr - QLO
        xor_word(b, ctrl, w3[j], W3)
        xor_word(b, ctrl, w2[j], W2)
        xor_word(b, ctrl, w1[j], W1)
        xor_word(b, ctrl, w0[j], W0)

    b.set_phase("label/load")
    walk(b, lbl, None, LABEL_BITS - 1, 0, 1 << LABEL_BITS, QLO, QHI + 1, body)

    mul_add(b, "h2", W3, W2, u, SHIFT)
    mul_add(b, "h1", W2, W1, u, SHIFT)
    mul_add(b, "h0", W1, W0, u, SHIFT)

    mark_round = b.checkpoint()
    b.set_phase("round/add")
    gidney_add(b, W0[ROUND_SHIFT : ROUND_SHIFT + OUTPUT_BITS], KQ)
    st.unwind(until=mark_round)

    return b, regs
