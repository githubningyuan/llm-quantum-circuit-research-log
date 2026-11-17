#!/usr/bin/env python3
"""coulomb-potential — piecewise-linear 1/sqrt evaluator.

Replaces the degree-2 HRS polynomial ladder with a single linear
interpolation: the normalized mantissa's top 6 fractional bits select one
of 64 intervals per exponent parity (128 banks), an intercept word and a
slope word are loaded by a QROM unary walk, and one small
(15-bit x 11-bit, pre-truncated) multiply produces the correction.  The
result is then barrel-rescaled by floor(e/2) and accumulated, all inside
the same Bennett mirror.

The mantissa is normalized *in place*: n2 = 2^e + L (leading bit at e,
L = low e bits) is barrel-rotated left by (EXP_MAX - e), which moves L to
the top MAN_BITS bits and the leading 1 to bit 17; bits above e are all
zero, so the rotation's wraparound is exactly a left shift.  Because the
mantissa M = (n2 - 2^e) << (24 - e) is a multiple of 2^(24 - e) >=
2^(24 - EXP_MAX), its low ZERO_LOW fractional bits are always zero, so only
the MAN_BITS significant bits are kept.  This saves qubits on the mantissa
and shortens the multiply.
"""

from __future__ import annotations

import math

from uncompute import Builder
from uncompute.bennett import Stages
from uncompute.gadgets import (
    add,
    declare_registers,
    gidney_add,
    gidney_sub,
    walk,
    xor_word,
)
from uncompute.testing import assert_metrics, emit_and_report, load_contract

# ---------------------------------------------------------------------------
# Instance constants
# ---------------------------------------------------------------------------
COORD_BITS = 8  # per-coordinate register width
ACC_BITS = 25  # accumulator width (mod 2^25)
N2_BITS = 18  # n2 <= 3 * 255^2 = 195,075 < 2^18
FRAC_BITS = 24  # mantissa fractional bits (u = M / 2^24 in [1, 2))
INDEX_BITS = 6  # interval index = top INDEX_BITS fractional bits of u
NUM_INTERVALS = 1 << INDEX_BITS  # intervals per parity, 2*NUM_INTERVALS banks
FK = 8  # slope fixed-point fractional bits
WORD_BITS = 25  # V0 word width (value < 2^25, so the top bit is always 0)
KMAG_BITS = 7  # slope magnitude |K| width (< 2^7, positive)
CORR_BITS = 17  # correction register width (unsigned)
EXP_MAX = N2_BITS - 1  # e in [0, 17]
ZERO_LOW = FRAC_BITS - EXP_MAX  # guaranteed trailing zero mantissa bits (7)
MAN_BITS = FRAC_BITS - ZERO_LOW  # significant mantissa bits (17)
DELTA_BITS = MAN_BITS - INDEX_BITS  # significant delta width (11)
PAD_BITS = 8  # rescale extension
H_BITS = 4
HMAX = EXP_MAX >> 1  # h = floor(e/2) in [0, 8]
assert HMAX < (1 << H_BITS)

SCALE = 1 << FRAC_BITS  # 2^24
STEP = 1 << (FRAC_BITS - INDEX_BITS)  # interval width = 2^18
SHIFT_MUL = FK - ZERO_LOW  # 9: correction = K * d >> SHIFT_MUL

_CONTRACT = load_contract(__file__)
EPS = _CONTRACT["EPS"]
N2_MAX = _CONTRACT["N2_MAX"]
assert N2_MAX.bit_length() == N2_BITS


def _round_half_away(v: float) -> int:
    f = math.floor(v)
    return f + 1 if v - f >= 0.5 else f


def _ideal_f(n2: int) -> int:
    return _round_half_away(SCALE / math.sqrt(n2))


# ---------------------------------------------------------------------------
# Table fit: linear interpolation V(u) ~ A + K * delta over NUM_INTERVALS
# intervals per parity.  V(u) = 2^24 / sqrt(2^p u), p = exponent parity,
# u = M/2^24, delta = M - (2^24 + i*STEP).
# ---------------------------------------------------------------------------


def _achievable():
    ach = bytearray(N2_MAX + 1)
    sq = [i * i for i in range(256)]
    for dx in range(256):
        x2 = sq[dx]
        for dy in range(dx, 256):
            s2 = x2 + sq[dy]
            for dz in range(dy, 256):
                ach[s2 + sq[dz]] = 1
    ach[0] = 0
    return ach


_ACH = _achievable()


def build_tables():
    """Fit and return (A_words, K_mag) with a per-interval minimax polish.

    Each interval is fit by an endpoint linear model, then the intercept A
    and slope K are tuned by alternating 1-D coordinate descent so that the
    max |f_approx - ideal| over the interval's achievable points is centered
    (roughly minimax).  This halves the systematic chord error of a naive
    endpoint fit.
    """
    A_words = [0] * (2 * NUM_INTERVALS)
    K_mag = [0] * (2 * NUM_INTERVALS)

    def v_of(p, u):
        return SCALE / math.sqrt(2.0 * u if p else u)

    shift = FRAC_BITS - INDEX_BITS

    for p in (0, 1):
        for i in range(NUM_INTERVALS):
            m_lo = SCALE + (i << shift)
            u_lo = m_lo / SCALE
            u_hi = (m_lo + STEP) / SCALE
            v_lo = v_of(p, u_lo)
            v_hi = v_of(p, u_hi)
            slope = (v_hi - v_lo) / STEP  # dV/d(delta), negative
            A_words[p * NUM_INTERVALS + i] = round(v_lo)
            K_mag[p * NUM_INTERVALS + i] = round(-slope * (1 << FK))

    # Group the achievable points by (parity, interval) for the polish.
    groups = {}
    for n2 in range(1, N2_MAX + 1):
        if not _ACH[n2]:
            continue
        e = n2.bit_length() - 1
        h = e >> 1
        p = e & 1
        u_word = n2 << (FRAC_BITS - e)
        i = (u_word >> shift) & (NUM_INTERVALS - 1)
        delta = u_word - (SCALE + (i << shift))
        groups.setdefault((p, i), []).append((delta, h, _ideal_f(n2)))

    for (p, i), items in groups.items():
        idx = p * NUM_INTERVALS + i
        A = A_words[idx]
        K = K_mag[idx]

        def gerr(Av, Kv):
            me = 0
            for d, h, idl in items:
                e = ((Av - ((Kv * d) >> FK)) >> h) - idl
                if e > me:
                    me = e
                if -e > me:
                    me = -e
            return me

        best = (A, K)
        be = gerr(A, K)
        for _ in range(20):
            improved = False
            for dA in (-2, -1, 1, 2):
                ce = gerr(A + dA, K)
                if ce < be:
                    best, be, improved = (A + dA, K), ce, True
            A = best[0]
            for dK in (-2, -1, 1, 2):
                ce = gerr(A, K + dK)
                if ce < be:
                    best, be, improved = (A, K + dK), ce, True
            K = best[1]
            if not improved:
                break
        A_words[idx], K_mag[idx] = best

    return A_words, K_mag


def _verify(A_words, K_mag):
    """Exhaustively check every achievable n2 against the ideal oracle."""
    worst, worst_n2 = 0, 0
    shift = FRAC_BITS - INDEX_BITS
    for n2 in range(1, N2_MAX + 1):
        if not _ACH[n2]:
            continue
        e = n2.bit_length() - 1
        h = e >> 1
        p = e & 1
        u_word = n2 << (FRAC_BITS - e)
        i = (u_word >> shift) & (NUM_INTERVALS - 1)
        delta = u_word - (SCALE + (i << shift))
        a = A_words[p * NUM_INTERVALS + i]
        km = K_mag[p * NUM_INTERVALS + i]
        v0 = a - ((km * delta) >> FK)
        f_approx = v0 >> h
        err = f_approx - _ideal_f(n2)
        if abs(err) > abs(worst):
            worst, worst_n2 = err, n2
    return worst, worst_n2


# ---------------------------------------------------------------------------
# The circuit
# ---------------------------------------------------------------------------


def rot_left(b, ctrl, wires, s):
    n = len(wires)
    g = math.gcd(n, s)
    for c0 in range(g):
        cycle = [(c0 - s * t) % n for t in range(n // g)]
        for a, c in zip(cycle, cycle[1:]):
            b.cswap(ctrl, wires[a], wires[c])


def build_circuit(A_words, K_mag):  # noqa: C901
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
            row = [mag[i], None]
            tmp = []
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

    # ---- 2. scan: leading-one exponent -> steering bits ------------------
    with st.stage("scan", set_name="scan"):
        b.set_phase("scan/setup")
        pv = b.qubit()
        hq = b.qubits(H_BITS)
        sbits = b.qubits(5)
        b.claim("scan", [pv] + hq + sbits)

    b.set_phase("scan/flags")
    flags = [None] * N2_BITS
    f_top = b.qubit()
    b.cx(n2b[EXP_MAX], f_top)
    flags[EXP_MAX] = f_top
    z = b.qubit()
    b.x(z)
    b.cx(n2b[EXP_MAX], z)
    for e in range(EXP_MAX - 1, -1, -1):
        fe = b.and_compute(z, n2b[e])
        b.cx(fe, z)
        flags[e] = fe

    b.set_phase("scan/steer")
    for e in range(N2_BITS):
        if e & 1:
            b.cx(flags[e], pv)
        for j in range(H_BITS):
            if ((e >> 1) >> j) & 1:
                b.cx(flags[e], hq[j])
        s = EXP_MAX - e
        for j in range(5):
            if (s >> j) & 1:
                b.cx(flags[e], sbits[j])

    b.set_phase("scan/release")
    for e in range(0, EXP_MAX):
        b.cx(flags[e], z)
        b.and_release(flags[e], z, n2b[e])
    b.cx(n2b[EXP_MAX], z)
    b.x(z)
    b.recycle(z)
    b.cx(n2b[EXP_MAX], f_top)
    b.recycle(f_top)

    # ---- 3. normalize: in-place barrel-rotate n2 -> mantissa -------------
    # n2 = 2^e + L (leading bit at e, L = low e bits).  Rotate n2 left by
    # (EXP_MAX - e) moves L to the top MAN_BITS bits and the leading 1 to bit
    # 17; bits above e are all zero, so the rotation's wraparound is exactly a
    # left shift.  The mantissa is then n2[0:MAN_BITS] with n2[17] = 1 unused.
    for k, sb in ((1, sbits[0]), (2, sbits[1]), (4, sbits[2]), (8, sbits[3]), (16, sbits[4])):
        b.set_phase(f"normalize/r{k}")
        rot_left(b, sb, n2b, k)
    M = n2b  # the mantissa lives in the low MAN_BITS bits of n2

    # ---- 4. coefficient load: intercept A and slope K via QROM walk ------
    addr_bits = [M[DELTA_BITS + j] for j in range(INDEX_BITS)] + [pv]
    n_addr = len(addr_bits)
    n_banks = 1 << n_addr
    K_tab = [k & ((1 << KMAG_BITS) - 1) for k in K_mag]

    with st.stage("load", set_name="load"):
        b.set_phase("load/setup")
        V0 = b.qubits(WORD_BITS)
        Kreg = b.qubits(KMAG_BITS)
        b.claim("load", V0)
        b.claim("load", Kreg)

    def load_coeff(ctrl, a):
        xor_word(b, ctrl, A_words[a], V0[:ACC_BITS])
        xor_word(b, ctrl, K_tab[a], Kreg)

    b.set_phase("load/walk")
    walk(b, addr_bits, None, n_addr - 1, 0, n_banks, 0, n_banks, load_coeff)

    # ---- 5. interpolate: V0 -= (Kreg * d) >> SHIFT_MUL ------------------
    with st.stage("interp", set_name="corr"):
        b.set_phase("interp/setup")
        W = b.qubits(CORR_BITS)
        b.claim("corr", W)

    b.set_phase("interp/mul")
    for bit in range(KMAG_BITS):
        b.set_phase(f"interp/b{bit:02d}")
        T = W[max(0, bit - SHIFT_MUL):]
        A = M[:DELTA_BITS][max(0, SHIFT_MUL - bit):][: len(T)]
        if A:
            add(b, T, A, ctrl=Kreg[bit])

    b.set_phase("interp/sub")
    gidney_sub(b, W, V0)

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
    gidney_add(b, w34[PAD_BITS : PAD_BITS + ACC_BITS], KQ)

    # ---- 8. un-*: Bennett mirror of stages 6..1 --------------------------
    st.unwind(until=mark_round)

    return b, regs


def build():
    """Fit and check the tables, then construct the circuit."""
    A_words, K_mag = build_tables()
    assert max(K_mag) < (1 << KMAG_BITS), "slope magnitude must fit KMAG_BITS"
    assert (max(K_mag) * (STEP - 1) >> FK) < (1 << CORR_BITS), "correction must fit CORR_BITS"
    worst, worst_n2 = _verify(A_words, K_mag)
    assert abs(worst) <= EPS, (worst, worst_n2)
    return build_circuit(A_words, K_mag)


if __name__ == "__main__":
    b, _regs = build()
    assert_metrics(b, __file__)
    emit_and_report(b)
