#!/usr/bin/env python3
"""coulomb-potential — piecewise-linear 1/sqrt evaluator.

The normalized mantissa's top fractional bits select one interval per
exponent parity, an intercept word and a slope-magnitude word are loaded by
a QROM unary walk, and one small unsigned multiply produces the correction.
The result is then barrel-rescaled by floor(e/2) and accumulated, all inside
the same Bennett mirror.

Key structural optimization: the mantissa M = (n2 - 2^e) << (24 - e) is a
multiple of 2^(24 - e) >= 2^7, so its low 7 fractional bits are always zero.
The circuit therefore keeps only the significant mantissa bits (interval-index
bits + delta bits), and the correction multiply runs on the delta word with a
reduced shift.
"""

from __future__ import annotations

import math
import pathlib

from uncompute import Builder
from uncompute.bennett import Stages
from uncompute.gadgets import (
    add,
    berry_ku,
    declare_registers,
    erase_register,
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
MAN_BITS = 17  # significant mantissa bits (bits 7..23 of u)
INDEX_BITS = 6  # interval index = top INDEX_BITS of the significant mantissa
NUM_INTERVALS = 1 << INDEX_BITS  # 64 intervals per parity, 128 banks
DELTA_BITS = MAN_BITS - INDEX_BITS  # 11 significant delta bits
FK = 12  # slope fixed-point fractional bits
WORD_BITS = 25  # V0 word width (value < 2^25, so the top bit is always 0)
KMAG_BITS = 11  # slope magnitude |K| width (< 2^11, positive)
CORR_BITS = 17  # correction register width (unsigned, < 2^17)
PAD_BITS = 8  # rescale extension
H_BITS = 4
EXP_MAX = N2_BITS - 1  # e in [0, 17]
HMAX = EXP_MAX >> 1  # h = floor(e/2) in [0, 8]
assert HMAX < (1 << H_BITS)

SCALE = 1 << FRAC_BITS  # 2^24
STEP = 1 << (FRAC_BITS - INDEX_BITS)  # interval width = 2^17
SHIFT_MUL = FK - (FRAC_BITS - MAN_BITS)  # 9: correction = K * d10 >> 9

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
# Table fit: linear interpolation V(u) ~ A - K_mag * delta over NUM_INTERVALS
# intervals per parity.  V(u) = 2^24 / sqrt(2^p u), p = exponent parity.
# ---------------------------------------------------------------------------


def _achievable():
    """Return a bytearray marking every achievable squared distance."""
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


def build_tables():
    """Fit (A_words, K_mag) with a minimax coordinate descent per interval.

    Each (parity, interval) pair is fitted with an intercept A and a slope
    magnitude K, then A/K are nudged to minimise the worst centered error
    over the achievable squared distances that land in that interval.
    """
    A_words = [0] * (2 * NUM_INTERVALS)
    K_mag = [0] * (2 * NUM_INTERVALS)

    def v_of(p, u):
        return SCALE / math.sqrt(2.0 * u if p else u)

    ach = _achievable()
    shift = FRAC_BITS - INDEX_BITS
    groups = {}
    for n2 in range(1, N2_MAX + 1):
        if not ach[n2]:
            continue
        e = n2.bit_length() - 1
        h = e >> 1
        p = e & 1
        u_word = n2 << (FRAC_BITS - e)
        i = (u_word >> shift) & (NUM_INTERVALS - 1)
        delta = u_word - (SCALE + (i << shift))
        groups.setdefault((p, i), []).append((delta, h, _ideal_f(n2)))

    for (p, i), items in groups.items():
        m_lo = SCALE + (i << shift)
        u_lo = m_lo / SCALE
        u_hi = (m_lo + STEP) / SCALE
        v_lo = v_of(p, u_lo)
        v_hi = v_of(p, u_hi)
        slope = (v_hi - v_lo) / STEP
        A = round(v_lo)
        K = round(-slope * (1 << FK))

        def gerr(a, km, items):
            me = 0
            for delta, h, ideal in items:
                v0 = a - ((km * delta) >> FK)
                f = v0 >> h
                err = f - ideal
                if abs(err) > abs(me):
                    me = err
            return me

        best = [A, K]
        be = gerr(best[0], best[1], items)
        for _ in range(200):
            improved = False
            for j in range(2):
                for d in (-3, -2, -1, 1, 2, 3):
                    cand = best[:]
                    cand[j] += d
                    if cand[1] < 0:
                        continue
                    ce = gerr(cand[0], cand[1], items)
                    if abs(ce) < abs(be):
                        best, be = cand, ce
                        improved = True
            if not improved:
                break
        A_words[p * NUM_INTERVALS + i] = best[0]
        K_mag[p * NUM_INTERVALS + i] = best[1]

    return A_words, K_mag


def _verify(A_words, K_mag):
    """Exhaustively check every achievable n2 against the ideal oracle."""
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
    shift = FRAC_BITS - INDEX_BITS
    for n2 in range(1, N2_MAX + 1):
        if not ach[n2]:
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


def unsigned_mul_rows(b, stage, V, W, X, shift):
    """W += V * X * 2^-shift for unsigned V, unsigned X (no sign row)."""
    for k in range(len(V)):
        b.set_phase(f"{stage}/b{k:02d}")
        T = W[max(0, k - shift):]
        A = X[max(0, shift - k):][: len(T)]
        if A:
            add(b, T, A, ctrl=V[k])


def build_circuit(A_words, K_mag):  # noqa: C901, PLR0915
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

    def square_into(mag):
        # Symmetric squaring: mag^2 = sum_i mag[i] 2^(2i)
        #   + sum_{i<j} mag[i] mag[j] 2^(i+j+1).
        # Row i carries mag[i] at 2i (diagonal), a carry-only gap at 2i+1,
        # and mag[i]*mag[j] (j > i) at i+j+1; one ripple add per row with the
        # per-row ANDs released immediately (low transient width, 28 ANDs).
        n = COORD_BITS
        for i in range(n):
            row = [mag[i], None]
            tmp = []
            for j in range(i + 1, n):
                t = b.and_compute(mag[i], mag[j])
                row.append(t)
                tmp.append((t, j))
            gidney_add(b, row, n2b[2 * i:])
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
        square_into(mag)
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

    # ---- 3. normalize: significant mantissa bits -------------------------
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
                b.cswap(flags[e], n2b[j], M[j + MAN_BITS - e])
            b.cx(flags[e], n2b[e])
        mark_nclear = b.checkpoint()
        b.set_phase("normalize/clear")
        b.release_recycle("n2", n2b)
    mark_normalize = b.checkpoint()

    # ---- 4. coefficient load: intercept A and slope K via QROM walk ------
    addr_bits = [M[DELTA_BITS + j] for j in range(INDEX_BITS)] + [pv]
    n_addr = len(addr_bits)
    n_banks = 1 << n_addr
    K_tab = [k & ((1 << KMAG_BITS) - 1) for k in K_mag]

    comb_word = [A_words[a] | (K_tab[a] << WORD_BITS) for a in range(n_banks)]
    KU = berry_ku(n_banks)
    valid_addrs = list(range(n_banks))

    def unload():
        b.set_phase("un-load")
        reg = list(V0) + list(Kreg)
        erase_register(
            b, reg, addr_bits, n_banks, comb_word, KU, valid_addrs, "un-load", free=False
        )
        b.release("load", reg)
        for q in reversed(reg):
            b.recycle(q)

    with st.stage("load", set_name="load", unwind=unload):
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

    # ---- 5. interpolate: V0 -= (Kreg * d10) >> SHIFT_MUL ---------------
    with st.stage("interp", set_name="corr"):
        b.set_phase("interp/setup")
        W = b.qubits(CORR_BITS)
        b.claim("corr", W)

    b.set_phase("interp/mul")
    unsigned_mul_rows(b, "interp", Kreg, W, M[:DELTA_BITS], SHIFT_MUL)

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
    add(b, KQ, w34[PAD_BITS : PAD_BITS + ACC_BITS], gl=0)

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
