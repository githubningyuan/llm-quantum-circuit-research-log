#!/usr/bin/env python3
"""coulomb-potential — linear-interpolation 1/sqrt evaluator.

Replace the degree-2 HRS polynomial ladder with a single linear
interpolation: the normalized mantissa's top INDEX_BITS fractional bits
select one of 2^INDEX_BITS intervals per exponent parity, an intercept
word and a slope word are loaded by a QROM unary walk, and one small
pre-truncated multiply produces the correction.  The result is then
barrel-rescaled by floor(e/2) and accumulated.

The 2^24 / 1/sqrt tables are fitted (minimax via discrete coordinate
descent) and checked exhaustively over the achievable squared-distance
domain at build time; the worst centered error is asserted inside the
+/- EPS band.
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
    walk,
    xor_word,
)
from uncompute.testing import assert_metrics, emit_and_report, load_contract

# ---------------------------------------------------------------------------
# Instance constants
# ---------------------------------------------------------------------------
COORD_BITS = 8  # per-coordinate register width
ACC_BITS = 25  # accumulator width (mod 2^25)
OUT_SCALE_BITS = 24  # F(n2) = round(2^24 / sqrt(n2))
N2_BITS = 18  # n2 <= 3 * 255^2 = 195,075 < 2^18

FRAC_BITS = 20  # mantissa fractional bits (u = M / 2^FRAC_BITS in [1, 2))
INDEX_BITS = 7  # interval index = top INDEX_BITS fractional bits of u
NUM_INTERVALS = 1 << INDEX_BITS  # intervals per parity
STEP = 1 << (FRAC_BITS - INDEX_BITS)  # interval width in mantissa units
FK = 13  # slope fixed-point fractional bits
CORR_BITS = FRAC_BITS - INDEX_BITS  # correction register width (delta width)
WORD_BITS = FRAC_BITS + 2  # V0 word width (value < 2^25, top bit always 0)

EXP_MAX = N2_BITS - 1  # e in [0, 17]
HMAX = EXP_MAX >> 1  # h = floor(e/2) in [0, 8]
H_BITS = 4
assert HMAX < (1 << H_BITS)
PAD_BITS = HMAX + (OUT_SCALE_BITS - FRAC_BITS)  # rescale extension
assert PAD_BITS >= HMAX

SCALE = 1 << FRAC_BITS
WORD_MASK = (1 << WORD_BITS) - 1
CORR_MASK = (1 << CORR_BITS) - 1

_CONTRACT = load_contract(__file__)
EPS = _CONTRACT["EPS"]
N2_MAX = _CONTRACT["N2_MAX"]
assert N2_MAX.bit_length() == N2_BITS


def _round_half_away(v: float) -> int:
    f = math.floor(v)
    return f + 1 if v - f >= 0.5 else f


def _ideal_f(n2: int) -> int:
    return _round_half_away((1 << OUT_SCALE_BITS) / math.sqrt(n2))


def _achievable(n2_max: int) -> bytearray:
    ach = bytearray(n2_max + 1)
    sq = [i * i for i in range(256)]
    for dx in range(256):
        x2 = sq[dx]
        for dy in range(dx, 256):
            s2 = x2 + sq[dy]
            for dz in range(dy, 256):
                ach[s2 + sq[dz]] = 1
    ach[0] = 0
    return ach


# ---------------------------------------------------------------------------
# Exact datapath model (mirrors hrs_mul_rows's pre-truncated signed multiply).
# ---------------------------------------------------------------------------


def _hrs_value(V: int, X: int, shift: int, n: int) -> int:
    """Value of hrs_mul_rows: W += V * X * 2^-shift mod 2^n, per-row
    pre-truncation, V an n-bit two's-complement word, X unsigned."""
    mask = (1 << n) - 1
    W = 0
    for bit in range(n):
        if not ((V >> bit) & 1):
            continue
        lo = max(0, bit - shift)
        a_lo = max(0, shift - bit)
        a_len = n - lo
        aval = (X >> a_lo) & ((1 << a_len) - 1)
        contrib = aval << lo
        if bit == n - 1:
            W = (W - contrib) & mask
        else:
            W = (W + contrib) & mask
    return W


def _signed(v: int, bits: int) -> int:
    v &= (1 << bits) - 1
    return v - (1 << bits) if v & (1 << (bits - 1)) else v


def _circuit_f(n2: int, A_words, K_signed) -> int:
    e = n2.bit_length() - 1
    h = e >> 1
    p = e & 1
    u_word = n2 << (FRAC_BITS - e)
    i = (u_word >> (FRAC_BITS - INDEX_BITS)) & (NUM_INTERVALS - 1)
    delta = u_word & CORR_MASK
    a = A_words[p * NUM_INTERVALS + i]
    k = K_signed[p * NUM_INTERVALS + i]
    corr = _signed(_hrs_value(k & CORR_MASK, delta, FK, CORR_BITS), CORR_BITS)
    v0 = a + corr
    sh = OUT_SCALE_BITS - FRAC_BITS - h
    return (v0 << sh) if sh >= 0 else (v0 >> -sh)


# ---------------------------------------------------------------------------
# Table fit: linear interpolation V(u) ~ A + K * delta, minimax per interval.
# ---------------------------------------------------------------------------


def build_tables():
    ach = _achievable(N2_MAX)
    groups = {}
    for n2 in range(1, N2_MAX + 1):
        if not ach[n2]:
            continue
        e = n2.bit_length() - 1
        p = e & 1
        h = e >> 1
        u_word = n2 << (FRAC_BITS - e)
        i = (u_word >> (FRAC_BITS - INDEX_BITS)) & (NUM_INTERVALS - 1)
        delta = u_word & CORR_MASK
        groups.setdefault((p, i), []).append((delta, h, _ideal_f(n2)))

    A_words = [0] * (2 * NUM_INTERVALS)
    K_signed = [0] * (2 * NUM_INTERVALS)

    for (p, i), items in groups.items():
        m_lo = SCALE + (i << (FRAC_BITS - INDEX_BITS))
        u_lo = m_lo / SCALE
        u_hi = (m_lo + STEP) / SCALE
        v_lo = SCALE / math.sqrt(2.0 * u_lo if p else u_lo)
        v_hi = SCALE / math.sqrt(2.0 * u_hi if p else u_hi)
        slope = (v_hi - v_lo) / STEP
        A = round(v_lo)
        K = round(slope * (1 << FK))

        def gerr(A, K, items):
            me = 0
            for delta, h, ideal in items:
                corr = _signed(_hrs_value(K & CORR_MASK, delta, FK, CORR_BITS), CORR_BITS)
                v0 = A + corr
                sh = OUT_SCALE_BITS - FRAC_BITS - h
                f = (v0 << sh) if sh >= 0 else (v0 >> -sh)
                err = abs(f - ideal)
                if err > me:
                    me = err
            return me

        best = [A, K]
        be = gerr(best[0], best[1], items)
        for _ in range(300):
            improved = False
            for j in range(2):
                for d in (-3, -2, -1, 1, 2, 3):
                    cand = best[:]
                    cand[j] += d
                    ce = gerr(cand[0], cand[1], items)
                    if ce < be:
                        best, be = cand, ce
                        improved = True
            if not improved:
                break
        A_words[p * NUM_INTERVALS + i] = best[0]
        K_signed[p * NUM_INTERVALS + i] = best[1]

    return A_words, K_signed


def _verify(A_words, K_signed):
    ach = _achievable(N2_MAX)
    worst, arg = 0, 0
    for n2 in range(1, N2_MAX + 1):
        if not ach[n2]:
            continue
        err = abs(_circuit_f(n2, A_words, K_signed) - _ideal_f(n2))
        if err > worst:
            worst, arg = err, n2
    return worst, arg


# ---------------------------------------------------------------------------
# The circuit
# ---------------------------------------------------------------------------


def build_circuit(A_words, K_signed):  # noqa: C901, PLR0915
    b = Builder()
    st = Stages(b)
    regs = declare_registers(
        b,
        [(n, COORD_BITS) for n in ("rix", "riy", "riz", "rjx", "rjy", "rjz")]
        + [("k", ACC_BITS)],
    )
    RI = [regs["rix"], regs["riy"], regs["riz"]]
    RJ = [regs["rjx"], regs["rjy"], regs["rjz"]]
    KQ = regs["k"]
    b.claim("ri", RI[0] + RI[1] + RI[2])
    b.claim("rj", RJ[0] + RJ[1] + RJ[2])
    b.claim("k", KQ)

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

    # ---- 3. normalize: M = n2 << (FRAC_BITS - e) -------------------------
    def un_normalize():
        b.set_phase("un-normalize")
        b.mirror(mark_nclear, until=mark_normalize)
        b.claim("n2", n2b)
        b.release("mant", M)
        b.mirror(mark_scan, until=mark_nclear)

    with st.stage("normalize", unwind=un_normalize):
        b.set_phase("normalize/setup")
        M = b.qubits(FRAC_BITS + 1)
        b.claim("mant", M)
        b.x(M[FRAC_BITS])
        for e in range(N2_BITS):
            b.set_phase(f"normalize/e{e:02d}")
            for j in range(e):
                b.cswap(flags[e], n2b[j], M[FRAC_BITS - e + j])
            b.cx(flags[e], n2b[e])
        mark_nclear = b.checkpoint()
        b.set_phase("normalize/clear")
        b.release_recycle("n2", n2b)
    mark_normalize = b.checkpoint()

    # ---- 4. coefficient load: intercept A and slope K via QROM walk ------
    addr_bits = [M[FRAC_BITS - INDEX_BITS + j] for j in range(INDEX_BITS)] + [pv]
    n_addr = len(addr_bits)
    n_banks = 1 << n_addr
    assert n_banks == 2 * NUM_INTERVALS

    with st.stage("load", set_name="v0"):
        b.set_phase("load/setup")
        V0 = b.qubits(WORD_BITS)
        b.claim("v0", V0)
    Kreg = b.qubits(CORR_BITS)
    b.claim("kreg", Kreg)

    def load_coef(ctrl, a):
        xor_word(b, ctrl, A_words[a], V0[:ACC_BITS])
        xor_word(b, ctrl, K_signed[a] & CORR_MASK, Kreg)

    b.set_phase("load/walk")
    walk(b, addr_bits, None, n_addr - 1, 0, n_banks, 0, n_banks, load_coef)

    # ---- 5. interpolate: V0 += (Kreg * delta) >> FK ----------------------
    with st.stage("interp", set_name="corr"):
        b.set_phase("interp/setup")
        W = b.qubits(WORD_BITS)
        b.claim("corr", W)

    b.set_phase("interp/mul")
    hrs_mul_rows(b, "interp", Kreg, W[:CORR_BITS], M[:CORR_BITS], FK)

    b.set_phase("interp/sext")
    for j in range(CORR_BITS, WORD_BITS):
        b.cx(W[CORR_BITS - 1], W[j])

    b.set_phase("interp/add")
    gidney_add(b, W, V0)

    # ---- 6. rescale: barrel-rotate [pad | V0] right by h -----------------
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

    # ---- 7. accumulate: k += (V0 >> h) mod 2^25 --------------------------
    b.set_phase("accumulate/add")
    gidney_add(b, w34[HMAX : HMAX + ACC_BITS], KQ)

    # ---- 8. un-*: Bennett mirror of stages 6..1 --------------------------
    st.unwind(until=mark_round)

    return b, regs


def build():
    A_words, K_signed = build_tables()
    worst, worst_n2 = _verify(A_words, K_signed)
    assert abs(worst) <= EPS, (worst, worst_n2)
    return build_circuit(A_words, K_signed)


if __name__ == "__main__":
    b, _regs = build()
    assert_metrics(b, __file__)
    emit_and_report(b)
