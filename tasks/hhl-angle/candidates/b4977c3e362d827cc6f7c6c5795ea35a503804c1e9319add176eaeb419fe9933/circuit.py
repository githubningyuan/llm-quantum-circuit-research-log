"""hhl-angle — quadratic octave+mantissa, NON-UNIFORM width, ALL-UNSIGNED.

Interval label = octave s = floor(log2 X) in [16,20] (priority-encoded) + a
per-octave coarse mantissa width M_s.  Degree-2 fit, FRAC=28, output left-shifted 4.

All three Horner words are stored UNSIGNED: c0 = asin > 0, c2 = convexity > 0,
and |c1| (c1 < 0 for every interval).  The two Horner steps become full-width
subtractions (mul_add_sub), dropping the two's-complement sign bits and the
slice-truncated sign row from both multipliers.

h(X) = round_half_away(asin(2^15/X)*2^32), X in [2^16, 2^21-1].
"""

from __future__ import annotations

import math

from uncompute import Builder
from uncompute.gadgets import add, declare_registers, gidney_add, release_chain, walk, xor_word

X_MIN = 1 << 16
X_MAX = (1 << 21) - 1
X_BITS = 21
OUTPUT_BITS = 33
OUTPUT_FRAC = 32

SOCT = 16               # first octave
NOCT = 5                # octaves 16..20
M_LIST = [7, 6, 6, 6, 6]  # per-octave coarse mantissa width
OFFSETS = [0] * NOCT
for _i in range(1, NOCT):
    OFFSETS[_i] = OFFSETS[_i - 1] + (1 << M_LIST[_i - 1])
NINT = OFFSETS[-1] + (1 << M_LIST[-1])      # total intervals (384)
SHIFT = SOCT + NOCT - 1 - min(M_LIST)       # 14 (fixed multiplier shift)

DEGREE = 2
FRAC_BITS = 28
ROUND_SHIFT = FRAC_BITS - OUTPUT_FRAC  # -4
OUT_SHIFT = OUTPUT_FRAC - FRAC_BITS    # 4

ADDR_BITS = (NINT - 1).bit_length()     # 9


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
    Mt = [A[i][:] + [B[i]] for i in range(n)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(Mt[r][col]))
        Mt[col], Mt[piv] = Mt[piv], Mt[col]
        d = Mt[col][col]
        for j in range(col, n + 1):
            Mt[col][j] /= d
        for r in range(n):
            if r != col:
                f = Mt[r][col]
                for j in range(col, n + 1):
                    Mt[r][j] -= f * Mt[col][j]
    return [Mt[i][n] for i in range(n)]


def _uwidth(vals):
    mx = max(v for v in vals)
    return max(1, mx.bit_length())


def _coefficients():
    scale = 1 << FRAC_BITS
    raw0 = [0] * NINT
    raw1 = [0] * NINT
    raw2 = [0] * NINT
    for si in range(NOCT):
        s = SOCT + si
        Ms = M_LIST[si]
        for uc in range(1 << Ms):
            xlo = (1 << s) + uc * (1 << (s - Ms))
            width = 1 << (s - Ms)
            xs, ys = [], []
            np = 12
            for k in range(np):
                tv = (1 - math.cos(math.pi * (k + 0.5) / np)) / 2
                x = min(xlo + tv * width, X_MAX)
                xs.append(tv)
                ys.append(math.asin((1 << 15) / x))
            c = _fit_poly(xs, ys, DEGREE)
            j = OFFSETS[si] + uc
            raw0[j] = int(round(c[0] * scale))
            raw1[j] = -int(round(c[1] * scale))
            raw2[j] = int(round(c[2] * scale))
    W0 = _uwidth(raw0)   # c0 < 2^28; final add extends 1 bit to keep the carry
    W1 = _uwidth(raw1)
    W2 = _uwidth(raw2)
    m0 = (1 << W0) - 1
    m1 = (1 << W1) - 1
    m2 = (1 << W2) - 1
    w0 = [v & m0 for v in raw0]
    w1 = [v & m1 for v in raw1]
    w2 = [v & m2 for v in raw2]
    return (w0, w1, w2), (W0, W1, W2)


def mul_add_sub(b, stage, V, W, u, shift):
    for bit in range(len(V)):
        b.set_phase(f"{stage}/b{bit:02d}")
        T = W[max(0, bit - shift):]
        A = u[max(0, shift - bit):][: len(T)]
        for q in T:
            b.x(q)
        add(b, T, A, ctrl=V[bit])
        for q in T:
            b.x(q)


def _mux_xor(b, e_s, sel, nbits, target, XQ):
    for k in range(nbits):
        for si, e in enumerate(e_s):
            xb = sel(si, k)
            if xb is None or xb < 0 or xb >= X_BITS:
                continue
            t = b.and_compute(e, XQ[xb])
            b.cx(t, target[k])
            b.and_release(t, e, XQ[xb])


def _mantissa_sel(si, k):
    if k >= M_LIST[si]:
        return None
    return SOCT + si - M_LIST[si] + k


def _fine_sel(si, k):
    return k - SHIFT + SOCT + si - M_LIST[si]


def build():
    (w0, w1, w2), (W0W, W1W, W2W) = _coefficients()

    b = Builder()
    regs = declare_registers(b, [("X", X_BITS), ("k", OUTPUT_BITS)])
    XQ, KQ = regs["X"], regs["k"]

    m_oct = b.checkpoint()

    # ---- one-hot octave encode e_s (s=16..20), prefix chain (6 ANDs) -----
    e20 = XQ[20]
    e19 = b.and_compute(XQ[20], XQ[19], pol=(False, True))
    n1819 = b.and_compute(XQ[20], XQ[19], pol=(False, False))
    e18 = b.and_compute(n1819, XQ[18], pol=(True, True))
    n171819 = b.and_compute(n1819, XQ[18], pol=(True, False))
    e17 = b.and_compute(n171819, XQ[17], pol=(True, True))
    e16 = b.and_compute(n171819, XQ[17], pol=(True, False))
    e_s = [e16, e17, e18, e19, e20]
    octave_trail = [
        (e19, XQ[20], XQ[19], (False, True)),
        (n1819, XQ[20], XQ[19], (False, False)),
        (e18, n1819, XQ[18], (True, True)),
        (n171819, n1819, XQ[18], (True, False)),
        (e17, n171819, XQ[17], (True, True)),
        (e16, n171819, XQ[17], (True, False)),
    ]

    # ---- address = per-octave offset (high bits) + coarse mantissa -------
    addr = b.qubits(ADDR_BITS)
    for si, e in enumerate(e_s):
        off = OFFSETS[si]
        for k in range(M_LIST[si], ADDR_BITS):
            if (off >> k) & 1:
                b.cx(e, addr[k])
    _mux_xor(b, e_s, _mantissa_sel, max(M_LIST), addr[0:max(M_LIST)], XQ)

    # ---- fine mantissa u_fine' (left-aligned to SHIFT bits) ---------------
    u = b.qubits(SHIFT)
    _mux_xor(b, e_s, _fine_sel, SHIFT, u, XQ)

    m_mux = b.checkpoint()

    # ---- pebble the octave ANDs: release before the Horner peak -----------
    release_chain(b, octave_trail)

    # ---- QROM load 3 coefficient words (one walk) ------------------------
    W2 = b.qubits(W2W)
    W1 = b.qubits(W1W)
    W0 = b.qubits(W0W)

    def body(ctrl, j):
        xor_word(b, ctrl, w2[j], W2)
        xor_word(b, ctrl, w1[j], W1)
        xor_word(b, ctrl, w0[j], W0)

    b.set_phase("label/load")
    walk(b, addr, None, ADDR_BITS - 1, 0, 1 << ADDR_BITS, 0, NINT, body)

    mul_add_sub(b, "h1", W2, W1, u, SHIFT)
    mul_add_sub(b, "h0", W1, W0, u, SHIFT)

    # ---- round/add (modifies k; not uncomputed) --------------------------
    m_add = b.checkpoint()
    b.set_phase("round/add")
    if ROUND_SHIFT >= 0:
        gidney_add(b, W0[ROUND_SHIFT : ROUND_SHIFT + OUTPUT_BITS], KQ)
    else:
        gidney_add(b, W0[0:W0W], KQ[OUT_SHIFT : OUT_SHIFT + W0W + 1])

    # ---- uncompute Horner+QROM (recomputes the pebbled octave ANDs) ------
    b.mirror(m_mux, until=m_add)
    # ---- uncompute muxes + octave encode ---------------------------------
    b.mirror(m_oct, until=m_mux)

    return b, regs
