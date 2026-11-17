#!/usr/bin/env python3
"""coulomb-potential: reduced-precision HRS piecewise-polynomial circuit.

Degree-2 polynomial on 8 parity banks in signed fixed point, but with a
narrower mantissa (FRAC_BITS=13 instead of 17): the coefficient banks and
interval thresholds are fitted at build time (classical) and the worst-case
error is asserted exhaustively over the full achievable-n2 domain before any
gate is emitted.

The front end (squared distance), first-one scan, mantissa normalization
(with low-bit truncation for e > FRAC_BITS), interval label, QROM coefficient
load, two Horner multiplications, barrel rescale, and Bennett mirror are the
published HRS structure (arXiv:1805.12445).
"""

from __future__ import annotations

import math

from uncompute import Builder
from uncompute.bennett import Stages
from uncompute.gadgets import (
    declare_registers,
    gidney_add,
    gidney_sub,
    hrs_mul_rows,
    label_increment,
    walk,
    xor_word,
)
from uncompute.testing import assert_metrics, emit_and_report, load_contract

# ---------------------------------------------------------------------------
# Instance constants
# ---------------------------------------------------------------------------
COORD_BITS = 8
ACC_BITS = 25
N2_BITS = 18
WORD_BITS = 26          # coefficient / accumulator word width (signed)
FRAC_FULL = 17          # normalize mantissa precision (exact, no truncation)
FRAC_BITS = 13          # polynomial mantissa precision (truncated)
DROP = FRAC_FULL - FRAC_BITS
INT_BITS = WORD_BITS - FRAC_BITS
INTERVALS = 8
LABEL_BITS = 3
assert (1 << LABEL_BITS) >= INTERVALS > (1 << (LABEL_BITS - 1))
EXP_MAX = N2_BITS - 1   # e in [0, 17]
HMAX = EXP_MAX >> 1     # h = floor(e/2) in [0, 8]
H_BITS = 4
assert HMAX < (1 << H_BITS)
PAD_BITS = HMAX
WORD_MASK = (1 << WORD_BITS) - 1
SIGN = 1 << (WORD_BITS - 1)

_CONTRACT = load_contract(__file__)
EPS = _CONTRACT["EPS"]
N2_MAX = _CONTRACT["N2_MAX"]
C_SCALE = 1 << 24


# ---------------------------------------------------------------------------
# Classical reference (self-contained; matches grader semantics)
# ---------------------------------------------------------------------------
def _achievable_n2():
    ach = bytearray(N2_MAX + 1)
    for dx in range(256):
        x2 = dx * dx
        for dy in range(dx, 256):
            s2 = x2 + dy * dy
            for dz in range(dy, 256):
                ach[s2 + dz * dz] = 1
    ach[0] = 0
    return [v for v in range(1, N2_MAX + 1) if ach[v]]


def _ideal_f(n2):
    t = C_SCALE / math.sqrt(n2)
    f = math.floor(t)
    return f + 1 if t - f >= 0.5 else f


def _signed(v, wbits):
    v &= (1 << wbits) - 1
    if v >= (1 << (wbits - 1)):
        v -= (1 << wbits)
    return v


def _mul_word(V, X, shift, wbits):
    """Exact integer model of ``hrs_mul_rows``'s per-term pre-truncated
    signed multiply: result = (V * X) >> shift, low bits dropped per row,
    all mod 2^wbits, returned as a signed integer.
    """
    r = 0
    for b in range(wbits):
        vb = (V >> b) & 1
        term = (X >> (shift - b)) if b < shift else (X << (b - shift))
        if b == wbits - 1:
            r -= vb * term
        else:
            r += vb * term
    return _signed(r, wbits)


def _mantissa(n2):
    e = n2.bit_length() - 1
    if e <= FRAC_BITS:
        M = n2 << (FRAC_BITS - e)
    else:
        M = n2 >> (e - FRAC_BITS)
    return e, M


def _g_p(M, p):
    u = M / (1 << FRAC_BITS)
    return C_SCALE * ((1.0 / math.sqrt(2.0)) if p else 1.0) / math.sqrt(u)


# ---------------------------------------------------------------------------
# Runtime piecewise-polynomial fit (classical; runs once at build)
# ---------------------------------------------------------------------------
def _quad_fit(xs, ys):
    """Fit y = c2 x^2 + c1 x + c0 through three points (Gaussian elimination)."""
    x0, x1, x2 = xs
    y0, y1, y2 = ys
    A = [[x0 * x0, x0, 1.0, y0], [x1 * x1, x1, 1.0, y1], [x2 * x2, x2, 1.0, y2]]
    for col in range(3):
        piv = max(range(col, 3), key=lambda r: abs(A[r][col]))
        A[col], A[piv] = A[piv], A[col]
        for r in range(col + 1, 3):
            f = A[r][col] / A[col][col]
            for c in range(col, 4):
                A[r][c] -= f * A[col][c]
    c0 = A[2][3] / A[2][2]
    c1 = (A[1][3] - A[1][2] * c0) / A[1][1]
    c2 = (A[0][3] - A[0][2] * c0 - A[0][1] * c1) / A[0][0]
    return c2, c1, c0


def _gauss(aug):
    n = len(aug)
    m = [row[:] for row in aug]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[piv] = m[piv], m[col]
        for r in range(col + 1, n):
            f = m[r][col] / m[col][col]
            for c in range(col, n + 1):
                m[r][c] -= f * m[col][c]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = (m[i][n] - sum(m[i][j] * x[j] for j in range(i + 1, n))) / m[i][i]
    return x


def _remez_quad(f, a, b, iters=80, grid=512):
    """Degree-2 minimax (Remez exchange) of f over [a, b]; returns (c2, c1, c0)."""
    n = 4
    xs = [a + (b - a) * i / (n - 1) for i in range(n)]
    for _ in range(iters):
        aug = [[1.0, x, x * x, (1.0 if i % 2 == 0 else -1.0), f(x)] for i, x in enumerate(xs)]
        c0, c1, c2, E = _gauss(aug)

        def poly(x):
            return c0 + c1 * x + c2 * x * x

        best_x, best_mag, best_sign = None, -1.0, 0.0
        for k in range(grid + 1):
            x = a + (b - a) * k / grid
            r = f(x) - poly(x)
            if abs(r) > best_mag:
                best_mag, best_x, best_sign = abs(r), x, r
        if best_mag - abs(E) < 1e-6 * max(1.0, abs(E)):
            break
        replaced = False
        for i in range(n):
            if (f(xs[i]) - poly(xs[i])) * best_sign > 0:
                xs[i] = best_x
                replaced = True
                break
        if not replaced:
            xs[0] = best_x
        xs.sort()
    return c2, c1, c0


def load_tables():
    """Fit and quantize the coefficient banks and interval thresholds.

    Returns (qtab, thresholds) exactly as the original load_tables: qtab maps
    (parity, interval) -> [q0, q1, q2] signed 26-bit words; thresholds are the
    ascending mantissa words (on the 2^FRAC_BITS grid) where intervals 1.. start.
    """
    domain = _achievable_n2()

    # Non-uniform interval boundaries in u = M / 2^FRAC_BITS (denser near u=1
    # where 1/sqrt is most curved), inherited from the published fit.
    u_bounds = [1.0600, 1.1805, 1.2734, 1.3959, 1.5156, 1.6555, 1.8440]
    thresholds = [round(ub * (1 << FRAC_BITS)) for ub in u_bounds]
    assert len(thresholds) == INTERVALS - 1
    assert all((1 << FRAC_BITS) < t < (1 << (FRAC_BITS + 1)) for t in thresholds)
    assert all(a < c for a, c in zip(thresholds, thresholds[1:]))

    # Collect the achievable points grouped by (parity, interval) for the
    # quantization search and the exhaustive proof.
    pts = {(p, i): [] for p in (0, 1) for i in range(INTERVALS)}
    for n2 in domain:
        e, M = _mantissa(n2)
        p = e & 1
        h = e >> 1
        i = sum(1 for t in thresholds if M >= t)
        pts[(p, i)].append((M, h, _ideal_f(n2)))

    def eval_v0(c0, c1, c2, M):
        V1 = _signed(c1 + _mul_word(c2, M, FRAC_BITS, WORD_BITS), WORD_BITS)
        return _signed(c0 + _mul_word(V1, M, FRAC_BITS, WORD_BITS), WORD_BITS)

    def interval_err(c0, c1, c2, ps):
        w = 0
        for M, h, F in ps:
            w = max(w, abs((eval_v0(c0, c1, c2, M) >> h) - F))
        return w

    qtab = {}
    for p in (0, 1):
        for i in range(INTERVALS):
            lo = thresholds[i - 1] if i > 0 else (1 << FRAC_BITS)
            hi = thresholds[i] if i < INTERVALS - 1 else (1 << (FRAC_BITS + 1))
            lo_u, hi_u = lo / (1 << FRAC_BITS), hi / (1 << FRAC_BITS)
            scale = 1.0 / math.sqrt(2.0) if p else 1.0

            def target(u):
                return C_SCALE * scale / math.sqrt(u)

            c2, c1, c0 = _remez_quad(target, lo_u, hi_u)

            # Smart quantization: try floor/ceil of each coefficient and pick
            # the integer triple minimizing this interval's true max error.
            ps = pts[(p, i)]
            cands = []
            for d0 in (math.floor(c0), math.ceil(c0)):
                for d1 in (math.floor(c1), math.ceil(c1)):
                    for d2 in (math.floor(c2), math.ceil(c2)):
                        cands.append((d0, d1, d2))
            c0, c1, c2 = min(cands, key=lambda t: interval_err(t[0], t[1], t[2], ps))

            # Fast local search over small integer nudges (greedy, few rounds).
            for _ in range(4):
                cur = interval_err(c0, c1, c2, ps)
                best = (c0, c1, c2)
                for dc0, dc1, dc2 in (
                    (1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0),
                    (0, 0, 1), (0, 0, -1),
                ):
                    e2 = interval_err(c0 + dc0, c1 + dc1, c2 + dc2, ps)
                    if e2 < cur:
                        cur = e2
                        best = (c0 + dc0, c1 + dc1, c2 + dc2)
                if best == (c0, c1, c2):
                    break
                c0, c1, c2 = best

            qtab[(p, i)] = [c0, c1, c2]

    # Exhaustive correctness proof over the full achievable domain.
    worst = 0
    worst_arg = None
    for n2 in domain:
        e, M = _mantissa(n2)
        p = e & 1
        h = e >> 1
        i = sum(1 for t in thresholds if M >= t)
        c0, c1, c2 = qtab[(p, i)]
        out = eval_v0(c0, c1, c2, M) >> h
        err = abs(out - _ideal_f(n2))
        if err > worst:
            worst = err
            worst_arg = n2
    assert worst <= EPS, f"fit error {worst} > EPS {EPS} at n2={worst_arg}"

    return qtab, thresholds


# ---------------------------------------------------------------------------
# The circuit (unchanged structure; narrower mantissa)
# ---------------------------------------------------------------------------
def build_circuit(qtab, thresholds):  # noqa: C901, PLR0915
    b = Builder()
    st = Stages(b)
    regs = declare_registers(
        b, [(n, COORD_BITS) for n in ("rix", "riy", "riz", "rjx", "rjy", "rjz")] + [("k", ACC_BITS)]
    )
    RI = [regs["rix"], regs["riy"], regs["riz"]]
    RJ = [regs["rjx"], regs["rjy"], regs["rjz"]]
    KQ = regs["k"]
    b.claim("ri", RI[0] + RI[1] + RI[2])
    b.claim("rj", RJ[0] + RJ[1] + RJ[2])
    b.claim("k", KQ)

    banks = {}
    for parity in (0, 1):
        for i in range(INTERVALS):
            pattern = i | (parity << LABEL_BITS)
            banks[pattern] = [q & WORD_MASK for q in qtab[(parity, i)]]

    with st.stage("dist", set_name="n2"):
        b.set_phase("dist/setup")
        n2b = b.qubits(N2_BITS)
        b.claim("n2", n2b)

    def square_rows(mag):
        for i in range(COORD_BITS):
            row, tmp = [mag[i], None], []
            for j in range(i + 1, COORD_BITS):
                t = b.and_compute(mag[i], mag[j])
                row.append(t)
                tmp.append((t, j))
            gidney_add(b, row, n2b[2 * i :])
            for t, j in reversed(tmp):
                b.and_release(t, mag[i], mag[j])

    for ax in range(3):
        b.set_phase(f"dist/ax{ax}")
        D = b.qubits(COORD_BITS + 1)
        b.claim("diff", D)
        sgn, mag = D[COORD_BITS], D[:COORD_BITS]
        for j in range(COORD_BITS):
            b.cx(RI[ax][j], D[j])
        gidney_sub(b, RJ[ax], D)
        for q in mag:
            b.cx(sgn, q)
        gidney_add(b, [sgn], mag)
        square_rows(mag)
        gidney_sub(b, [sgn], mag)
        for q in mag:
            b.cx(sgn, q)
        gidney_add(b, RJ[ax], D)
        for j in range(COORD_BITS):
            b.cx(RI[ax][j], D[j])
        b.release_recycle("diff", D)

    def un_scan():
        b.set_phase("un-scan")
        b.release("exp", flags)
        b.release("steer", [pv] + hq)
        b.mirror(mark_dist, until=mark_scan)

    mark_dist = b.checkpoint()
    with st.stage("scan", unwind=un_scan):
        b.set_phase("scan/flags")
        flags = [None] * N2_BITS
        f_top = b.qubit()
        b.cx(n2b[EXP_MAX], f_top)
        flags[EXP_MAX] = f_top
        z = b.qubit()
        b.claim("scan-z", [z])
        b.x(z)
        b.cx(n2b[EXP_MAX], z)
        for e in range(EXP_MAX - 1, -1, -1):
            fe = b.and_compute(z, n2b[e])
            b.cx(fe, z)
            flags[e] = fe
        b.release_recycle("scan-z", [z])
        b.claim("exp", flags)
        b.set_phase("scan/steer")
        pv = b.qubit()
        hq = b.qubits(H_BITS)
        b.claim("steer", [pv] + hq)
        for e in range(N2_BITS):
            if e & 1:
                b.cx(flags[e], pv)
            for j in range(H_BITS):
                if ((e >> 1) >> j) & 1:
                    b.cx(flags[e], hq[j])
    mark_scan = b.checkpoint()

    def un_normalize():
        b.set_phase("un-normalize")
        b.mirror(mark_nclear, until=mark_normalize)
        b.claim("n2", n2b)
        b.release("mant", M)
        b.mirror(mark_scan, until=mark_nclear)

    with st.stage("normalize", unwind=un_normalize):
        b.set_phase("normalize/setup")
        M = b.qubits(FRAC_FULL + 1)
        b.claim("mant", M)
        b.x(M[FRAC_FULL])
        for e in range(N2_BITS):
            b.set_phase(f"normalize/e{e:02d}")
            for j in range(e):
                b.cswap(flags[e], n2b[j], M[FRAC_FULL - e + j])
            b.cx(flags[e], n2b[e])
        mark_nclear = b.checkpoint()
        b.set_phase("normalize/clear")
        b.release_recycle("n2", n2b)
    mark_normalize = b.checkpoint()

    with st.stage("label", set_name="label"):
        b.set_phase("label/setup")
        lbl = b.qubits(LABEL_BITS)
        b.claim("label", lbl)

    prefix_xor = 0
    for m_fired in range(INTERVALS):
        assert prefix_xor == m_fired
        prefix_xor ^= m_fired ^ (m_fired + 1)
    for i, threshold in enumerate(thresholds):
        b.set_phase(f"label/b{i + 1:02d}")
        label_increment(b, M[DROP : DROP + FRAC_BITS], threshold - (1 << FRAC_BITS), i, lbl)

    match_wires = list(lbl) + [pv]
    addr_n = len(match_wires)

    def coef_load_all(Ws):
        def body(ctrl, addr):
            for deg, W in enumerate(Ws):
                xor_word(b, ctrl, banks[addr][deg], W)

        walk(b, match_wires, None, addr_n - 1, 0, 1 << addr_n, 0, 1 << addr_n, body)

    with st.stage("lead", set_name="poly"):
        b.set_phase("lead/load")
        W2 = b.qubits(WORD_BITS)
        W1 = b.qubits(WORD_BITS)
        W0 = b.qubits(WORD_BITS)
        b.claim("poly", W2 + W1 + W0)
    coef_load_all([W0, W1, W2])

    with st.stage("h1"):
        hrs_mul_rows(b, "h1", W2, W1, M[DROP:], FRAC_BITS)

    with st.stage("h0"):
        hrs_mul_rows(b, "h0", W1, W0, M[DROP:], FRAC_BITS)

    V0 = W0

    with st.stage("rescale", set_name="pad"):
        b.set_phase("rescale/pad")
        pad = b.qubits(PAD_BITS)
        b.claim("pad", pad)
    w34 = list(pad) + list(V0)

    def rot_right(ctrl, wires, s):
        n = len(wires)
        g = math.gcd(n, s)
        for c0 in range(g):
            cycle = [(c0 + s * t) % n for t in range(n // g)]
            for a, c in zip(cycle, cycle[1:]):
                b.cswap(ctrl, wires[a], wires[c])

    for s, hbit in ((1, hq[0]), (2, hq[1]), (4, hq[2]), (8, hq[3])):
        b.set_phase(f"rescale/s{s}")
        rot_right(hbit, w34, s)

    mark_round = b.checkpoint()

    b.set_phase("accumulate/add")
    gidney_add(b, w34[PAD_BITS : PAD_BITS + ACC_BITS], KQ)

    st.unwind(until=mark_round)

    return b, regs


def build():
    return build_circuit(*load_tables())


if __name__ == "__main__":
    b, _regs = build()
    assert_metrics(b, __file__)
    emit_and_report(b)
