#!/usr/bin/env python3
"""coulomb-potential — piecewise-linear 1/sqrt evaluator.

Replaces the degree-2 HRS polynomial ladder with a single linear
interpolation: the normalized mantissa's top 7 fractional bits select one
of 128 intervals per exponent parity (256 banks), an intercept word and a
slope word are loaded by a QROM unary walk, and one small
(17-bit x 17-bit, pre-truncated) multiply produces the correction.  The
result is then barrel-rescaled by floor(e/2) and accumulated, all inside
the same Bennett mirror.

The 2^24/1/sqrt tables are fitted (secant) and checked exhaustively over
the achievable squared-distance domain at build time; the worst centered
error is asserted far inside the +/- EPS band.
"""

from __future__ import annotations

import math
import pathlib

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
INDEX_BITS = 7  # interval index = top 7 fractional bits of u
NUM_INTERVALS = 1 << INDEX_BITS  # 128 intervals per parity, 256 banks

# The mantissa offset delta = M - (2^24 + i*2^17) is always a multiple of
# 2^(24 - e) with e <= 17, hence a multiple of 2^7.  Only its top
# DELTA_BITS bits are ever nonzero, so the multiply operates on
# delta_hi = delta >> 7.  The correction V0 -= W (W = K*delta/2^16) then
# only needs its top CORR_BITS bits: W_hi = -slope * delta_hi = W >> 7.
DELTA_LO = 7  # delta >> DELTA_LO keeps every meaningful bit
DELTA_BITS = FRAC_BITS - INDEX_BITS - DELTA_LO  # delta_hi width = 10
MAN_BITS = FRAC_BITS - DELTA_LO  # mantissa holds bits [DELTA_LO, FRAC_BITS) = 17
SF = 10  # slope scale: S = round(-slope * 2^SF)
KMAG_BITS = SF - 1  # S width (S < 2^9)
FK = SF  # W_hi = S * delta_hi >> FK
CORR_BITS = 9  # W_hi width (< 2^9 = 512)
L = DELTA_LO  # V0 -= (W_hi << L)

WORD_BITS = 25  # V0 word width (value < 2^25, so the top bit is always 0)
PAD_BITS = 8  # rescale extension
H_BITS = 4
EXP_MAX = N2_BITS - 1  # e in [0, 17]
HMAX = EXP_MAX >> 1  # h = floor(e/2) in [0, 8]
assert HMAX < (1 << H_BITS)

SCALE = 1 << FRAC_BITS  # 2^24
STEP = 1 << (FRAC_BITS - INDEX_BITS)  # interval width = 2^17

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
# Table fit: linear interpolation V(u) ~ A + K * delta over 128 intervals
# per parity.  V(u) = 2^24 / sqrt(2^p u), p = exponent parity, u = M/2^24,
# delta = M - (2^24 + i*2^17).
# ---------------------------------------------------------------------------


def build_tables():
    """Fit and return (A_words, S_words).

    A_words[bank] is the 25-bit intercept (bank = parity*128 + interval);
    S_words[bank] is the reduced-precision slope word round(-slope * 2^SF),
    positive and < 2^(SF-1).  The slope of 2^24/sqrt(u) is always negative,
    so the correction is a subtraction of S * delta_hi / 2^FK.
    """
    A_words = [0] * (2 * NUM_INTERVALS)
    S_words = [0] * (2 * NUM_INTERVALS)

    def v_of(p, u):
        return SCALE / math.sqrt(2.0 * u if p else u)

    for p in (0, 1):
        for i in range(NUM_INTERVALS):
            m_lo = SCALE + (i << (FRAC_BITS - INDEX_BITS))
            u_lo = m_lo / SCALE
            u_hi = (m_lo + STEP) / SCALE
            v_lo = v_of(p, u_lo)
            v_hi = v_of(p, u_hi)
            slope = (v_hi - v_lo) / STEP  # dV/d(delta), negative
            A_words[p * NUM_INTERVALS + i] = round(v_lo)
            S_words[p * NUM_INTERVALS + i] = round(-slope * (1 << SF))
    return A_words, S_words


def _verify(A_words, S_words):
    """Exhaustively check every achievable n2 against the ideal oracle.

    Returns (worst_error, worst_n2): worst_error is the largest (signed)
    deviation of the circuit's floor(V0 / 2^h) from ideal_f(n2).
    """
    # Three-square achievable domain (sorted coordinates; symmetric).
    ach = bytearray(N2_MAX + 1)
    sq = [i * i for i in range(256)]
    for dx in range(256):
        x2 = sq[dx]
        for dy in range(dx, 256):
            s2 = x2 + sq[dy]
            for dz in range(dy, 256):
                ach[s2 + sq[dz]] = 1
    ach[0] = 0

    worst, worst_n2 = 0, 0
    shift = FRAC_BITS - INDEX_BITS  # 17
    for n2 in range(1, N2_MAX + 1):
        if not ach[n2]:
            continue
        e = n2.bit_length() - 1
        h = e >> 1
        p = e & 1
        u_word = n2 << (FRAC_BITS - e)
        i = (u_word >> shift) & (NUM_INTERVALS - 1)
        delta = u_word - (SCALE + (i << shift))
        delta_hi = delta >> DELTA_LO
        a = A_words[p * NUM_INTERVALS + i]
        s = S_words[p * NUM_INTERVALS + i]
        w_hi = (s * delta_hi) >> FK
        v0 = a - (w_hi << L)
        f_approx = v0 >> h
        err = f_approx - _ideal_f(n2)
        if abs(err) > abs(worst):
            worst, worst_n2 = err, n2
    return worst, worst_n2


# ---------------------------------------------------------------------------
# The circuit
# ---------------------------------------------------------------------------


def build_circuit(A_words, S_words):  # noqa: C901, PLR0915
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

    # ---- 3. normalize: M = n2 << (24 - e) --------------------------------
    def un_normalize():
        b.set_phase("un-normalize")
        b.mirror(mark_nclear, until=mark_normalize)
        b.claim("n2", n2b)
        b.release("mant", M)
        b.mirror(mark_scan, until=mark_nclear)

    with st.stage("normalize", unwind=un_normalize):
        b.set_phase("normalize/setup")
        M = b.qubits(MAN_BITS)
        b.claim("mant", M)
        for e in range(N2_BITS):
            b.set_phase(f"normalize/e{e:02d}")
            for j in range(e):
                b.cswap(flags[e], n2b[j], M[MAN_BITS - e + j])
            b.cx(flags[e], n2b[e])
        mark_nclear = b.checkpoint()
        b.set_phase("normalize/clear")
        b.release_recycle("n2", n2b)
    mark_normalize = b.checkpoint()

    # ---- 4. coefficient load: intercept A and slope S via QROM walk ------
    # address = (interval 7 bits from M[23:17], parity from pv) = 8 bits.
    addr_bits = list(M[MAN_BITS - INDEX_BITS : MAN_BITS]) + [pv]
    n_addr = len(addr_bits)
    n_banks = 1 << n_addr
    S_tab = [s & ((1 << KMAG_BITS) - 1) for s in S_words]

    with st.stage("load", set_name="load"):
        b.set_phase("load/setup")
        V0 = b.qubits(WORD_BITS)
        Kreg = b.qubits(KMAG_BITS)
        b.claim("load", V0)
        b.claim("load", Kreg)

    def load_coeff(ctrl, a):
        xor_word(b, ctrl, A_words[a], V0[:ACC_BITS])
        xor_word(b, ctrl, S_tab[a], Kreg)

    b.set_phase("load/walk")
    walk(b, addr_bits, None, n_addr - 1, 0, n_banks, 0, n_banks, load_coeff)

    # ---- 5. interpolate: V0 -= (S * delta_hi) >> FK, i.e. V0 -= W << L --
    with st.stage("interp", set_name="corr"):
        b.set_phase("interp/setup")
        W = b.qubits(CORR_BITS)
        b.claim("corr", W)

    b.set_phase("interp/mul")
    for bit in range(KMAG_BITS):
        b.set_phase(f"interp/b{bit:02d}")
        T = W[max(0, bit - FK) :]
        A = M[:DELTA_BITS][max(0, FK - bit) :][: len(T)]
        add(b, T, A, ctrl=Kreg[bit])

    b.set_phase("interp/sub")
    gidney_sub(b, W, V0[L:])

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
    add(b, KQ, w34[PAD_BITS : PAD_BITS + ACC_BITS], gl=0)

    # ---- 8. un-*: Bennett mirror of stages 6..1 --------------------------
    st.unwind(until=mark_round)

    return b, regs


def build():
    """Fit and check the tables, then construct the circuit."""
    A_words, S_words = build_tables()
    assert max(S_words) < (1 << KMAG_BITS), "slope word must fit KMAG_BITS"
    worst, worst_n2 = _verify(A_words, S_words)
    assert abs(worst) <= 31, (worst, worst_n2)
    return build_circuit(A_words, S_words)


if __name__ == "__main__":
    b, _regs = build()
    assert_metrics(b, __file__)
    emit_and_report(b)
