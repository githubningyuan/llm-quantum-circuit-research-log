# Harmless comment: this file builds the coulomb-potential circuit.
#!/usr/bin/env python3
"""coulomb-potential piecewise-polynomial circuit.

Independent rewrite of the HRS piecewise-polynomial evaluator
(arXiv:1805.12445) with a reduced-width multiplier: the polynomial is fit in
the shifted basis v = u - 1 (so the leading coefficient collapses from
~1.875*2^24 down to ~2^24) and the coefficient word is stored at a coarser
LSB (scale 2^SCALE_Q), shrinking the multiplier word from 26 to 23 bits.  The
coefficients are fit at import time and the result is checked exhaustively
against the oracle before the stream is emitted.

Cost target: a smaller T*Q than the 26-bit baseline (5,787 Toffoli x 238
qubits = 1,377,306).
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
COORD_BITS = 8            # per-coordinate register width
ACC_BITS = 25             # accumulator width (mod 2^25)
OUT_SCALE_BITS = 24       # F(n2) = round(2^24 / sqrt(n2))
N2_BITS = 18              # n2 <= 3 * 255^2 = 195,075 < 2^18

MANT_BITS = 17            # u = n2 / 2^e is exact on the 2^-17 grid
EXP_MAX = N2_BITS - 1     # e in [0, 17]
HMAX = EXP_MAX >> 1       # h = floor(e/2) in [0, 8]
H_BITS = 4
assert HMAX < (1 << H_BITS)

INTERVALS = 16            # per parity; 32 coefficient banks
LABEL_BITS = 4
assert (1 << LABEL_BITS) >= INTERVALS > (1 << (LABEL_BITS - 1))

# Multiplier width reduction.  In the shifted basis v = u - 1 the leading
# coefficient a0 ~ 2^24, so a WORD_BITS of 25 (q=0) suffices against the 26
# of the monomial basis.  Storing the coefficients at LSB = 2^SCALE_Q then
# shrinks the word to WORD_BITS ~ 25 - SCALE_Q.  The extra intervals buy the
# error margin that the coarser coefficient grid spends.
SCALE_Q = 4
WORD_BITS = 26 - SCALE_Q  # 22
WORD_MASK = (1 << WORD_BITS) - 1
assert WORD_BITS >= 1

PAD_BITS = HMAX + SCALE_Q  # rescale extension: HMAX zero wires + scale headroom

_CONTRACT = load_contract(__file__)
ALLOWED_ULP = _CONTRACT["EPS"]
N2_MAX = _CONTRACT["N2_MAX"]
assert N2_MAX.bit_length() == N2_BITS

# The interval split (u-space thresholds on the 2^24 grid).  Geometric in u
# (uniform in log u), which matches the power-law curvature of 1/sqrt.
_THRESHOLDS = [round((1 << OUT_SCALE_BITS) * 2.0 ** (k / INTERVALS)) for k in range(1, INTERVALS)]


# ---------------------------------------------------------------------------
# Classical reference + fitting
# ---------------------------------------------------------------------------


def round_half_away(v: float) -> int:
    f = math.floor(v)
    return f + 1 if v - f >= 0.5 else f


def ideal_f(n2: int) -> int:
    return round_half_away((1 << OUT_SCALE_BITS) / math.sqrt(n2))


def _target(parity: int, u: float) -> float:
    """g_p(u) = 2^24 / sqrt(u) (p=0) or 2^24 / sqrt(2u) (p=1)."""
    if parity == 0:
        return (1 << OUT_SCALE_BITS) / math.sqrt(u)
    return (1 << OUT_SCALE_BITS) / math.sqrt(2.0 * u)


def _solve3(m):
    """Gaussian elimination for a 3x4 augmented matrix [[3x3 | b]]."""
    a = [row[:] for row in m]
    for col in range(3):
        piv = max(range(col, 3), key=lambda r: abs(a[r][col]))
        a[col], a[piv] = a[piv], a[col]
        pv = a[col][col]
        assert abs(pv) > 1e-12, "singular fit matrix"
        for j in range(col, 4):
            a[col][j] /= pv
        for r in range(3):
            if r != col:
                f = a[r][col]
                for j in range(col, 4):
                    a[r][j] -= f * a[col][j]
    return a[0][3], a[1][3], a[2][3]


def fit_quadratic(pts):
    """Interpolate a quadratic through three (v, y) points (Vandermonde)."""
    a = []
    for v, y in pts:
        a.append([1.0, v, v * v, y])
    return _solve3(a)


def fit_tables():
    """Fit degree-2 polynomials (shifted basis v = u - 1) per parity/interval,
    quantized to LSB = 2^SCALE_Q.  Returns (qtab, thresholds).
    """
    scale = 1 << SCALE_Q
    sign = 1 << (WORD_BITS - 1)
    qtab = {}
    # Chebyshev nodes (degree-2 interpolation points) in [-1, 1].
    import math as _m
    nodes = [_m.cos(_m.pi * (2 * j + 1) / 6) for j in range(3)]  # 0.866, 0, -0.866
    for parity in (0, 1):
        for i in range(INTERVALS):
            lo = 1.0 if i == 0 else _THRESHOLDS[i - 1] / (1 << OUT_SCALE_BITS)
            hi = _THRESHOLDS[i] / (1 << OUT_SCALE_BITS) if i < INTERVALS - 1 else 2.0
            va, vb = lo - 1.0, hi - 1.0
            mid = 0.5 * (va + vb)
            half = 0.5 * (vb - va)
            pts = []
            for t in nodes:
                v = mid + half * t
                pts.append((v, _target(parity, 1.0 + v)))
            a0, a1, a2 = fit_quadratic(pts)
            c0 = round(a0 / scale)
            c1 = round(a1 / scale)
            c2 = round(a2 / scale)
            row = [c0, c1, c2]
            assert all(-sign <= q < sign for q in row), (parity, i, row, WORD_BITS)
            qtab[(parity, i)] = row
    return qtab, list(_THRESHOLDS)


def check_tables(qtab, thresholds):
    """Exhaustive worst-case check of the (truncating) datapath model against
    the oracle.  Raises with the worst error if it exceeds the band."""
    max_err = 0
    arg = 0
    mant_mask = (1 << MANT_BITS) - 1
    sign = 1 << (WORD_BITS - 1)
    for n2 in range(1, N2_MAX + 1):
        ideal = ideal_f(n2)
        e = n2.bit_length() - 1
        h = e >> 1
        parity = e & 1
        vmant = (n2 << (MANT_BITS - e)) & mant_mask
        idx = 0
        for t in thresholds:
            bound = -(-(t - (1 << OUT_SCALE_BITS)) >> (OUT_SCALE_BITS - MANT_BITS))
            if vmant >= bound:
                idx += 1
            else:
                break
        c0, c1, c2 = qtab[(parity, idx)]
        # Horner in the shifted basis (truncating multiply, two's complement).
        v1 = c1 + ((c2 * vmant) >> MANT_BITS)
        v0 = c0 + ((v1 * vmant) >> MANT_BITS)
        assert -sign <= v1 < sign and -sign <= v0 < sign, (n2, c0, c1, c2, v1, v0)
        f = (v0 << SCALE_Q) >> h
        err = abs(f - ideal)
        if err > max_err:
            max_err, arg = err, n2
    if max_err > ALLOWED_ULP:
        e = arg.bit_length() - 1
        vmant = (arg << (MANT_BITS - e)) & mant_mask
        idx = 0
        for t in thresholds:
            bound = -(-(t - (1 << OUT_SCALE_BITS)) >> (OUT_SCALE_BITS - MANT_BITS))
            if vmant >= bound:
                idx += 1
            else:
                break
        raise AssertionError(
            f"fit worst error {max_err} > {ALLOWED_ULP} at n2={arg} "
            f"(WORD_BITS={WORD_BITS}, SCALE_Q={SCALE_Q}) e={e} h={e>>1} "
            f"parity={e&1} vmant={vmant} idx={idx} coef={qtab[(e&1, idx)]} "
            f"ideal={ideal_f(arg)}"
        )
    return max_err


# ---------------------------------------------------------------------------
# The circuit
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

    # ---- 1. dist: n2 = sum of squared signed differences ----------------
    with st.stage("dist", set_name="n2"):
        b.set_phase("dist/setup")
        n2b = b.qubits(N2_BITS)
        b.claim("n2", n2b)

    def square_rows(mag):
        for i in range(COORD_BITS):
            row, tmp = [], []
            for j in range(COORD_BITS):
                if j == i:
                    row.append(mag[i])
                else:
                    t = b.and_compute(mag[i], mag[j])
                    row.append(t)
                    tmp.append((t, j))
            gidney_add(b, row, n2b[i:])
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

    # ---- 2. scan: one-hot exponent flags + steering bits -----------------
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

    # ---- 3. normalize: M = n2 << (17 - e) --------------------------------
    def un_normalize():
        b.set_phase("un-normalize")
        b.mirror(mark_nclear, until=mark_normalize)
        b.claim("n2", n2b)
        b.release("mant", M)
        b.mirror(mark_scan, until=mark_nclear)

    with st.stage("normalize", unwind=un_normalize):
        b.set_phase("normalize/setup")
        M = b.qubits(MANT_BITS + 1)
        b.claim("mant", M)
        b.x(M[MANT_BITS])
        for e in range(N2_BITS):
            b.set_phase(f"normalize/e{e:02d}")
            for j in range(e):
                b.cswap(flags[e], n2b[j], M[MANT_BITS - e + j])
            b.cx(flags[e], n2b[e])
        mark_nclear = b.checkpoint()
        b.set_phase("normalize/clear")
        b.release_recycle("n2", n2b)
    mark_normalize = b.checkpoint()

    # ---- 4. label ---------------------------------------------------------
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
        bound = -(-(threshold - (1 << OUT_SCALE_BITS)) >> (OUT_SCALE_BITS - MANT_BITS))
        label_increment(b, M[:MANT_BITS], bound, i, lbl)

    # ---- coefficient selection -------------------------------------------
    match_wires = list(lbl) + [pv]

    def coef_load(deg, W):
        def body(ctrl, addr):
            xor_word(b, ctrl, banks[addr][deg], W)

        walk(
            b,
            match_wires,
            None,
            len(match_wires) - 1,
            0,
            1 << len(match_wires),
            0,
            1 << len(match_wires),
            body,
        )

    # ---- 5..7. lead/h1/h0: Horner ladder (shifted basis v = u - 1) --------
    stages = [
        ("lead", 2, "poly2"),  # V2 = c2[bank]
        ("h1", 1, "poly1"),    # V1 = c1[bank] + V2 * v
        ("h0", 0, "poly0"),    # V0 = c0[bank] + V1 * v
    ]
    V = None
    vmant = M[:MANT_BITS]  # v = (u - 1); its mantissa is M's low 17 bits
    for stage, deg, setname in stages:
        with st.stage(stage, set_name=setname):
            b.set_phase(f"{stage}/load")
            W = b.qubits(WORD_BITS)
            b.claim(setname, W)
        coef_load(deg, W)
        if V is not None:
            hrs_mul_rows(b, stage, V, W, vmant, MANT_BITS)
        V = W
    V0 = V

    # ---- 8. rescale: rotate [pad | V0] right by h -------------------------
    with st.stage("rescale", set_name="pad"):
        b.set_phase("rescale/pad")
        pad = b.qubits(PAD_BITS)
        b.claim("pad", pad)
    w = list(pad) + list(V0)

    def rot_right(ctrl, wires, s):
        n = len(wires)
        g = math.gcd(n, s)
        for c0 in range(g):
            cycle = [(c0 + s * t) % n for t in range(n // g)]
            for a, c in zip(cycle, cycle[1:]):
                b.cswap(ctrl, wires[a], wires[c])

    for s, hbit in ((1, hq[0]), (2, hq[1]), (4, hq[2]), (8, hq[3])):
        b.set_phase(f"rescale/s{s}")
        rot_right(hbit, w, s)

    mark_round = b.checkpoint()

    # ---- 9. accumulate: k += (V0 << SCALE_Q) >> h (not mirrored) ----------
    b.set_phase("accumulate/add")
    gidney_add(b, w[HMAX : HMAX + ACC_BITS], KQ)

    # ---- 10. un-*: Bennett mirror -----------------------------------------
    st.unwind(until=mark_round)

    return b, regs


def build():
    qtab, thresholds = fit_tables()
    check_tables(qtab, thresholds)
    return build_circuit(qtab, thresholds)


if __name__ == "__main__":
    b, _regs = build()
    assert_metrics(b, __file__)
    emit_and_report(b)
