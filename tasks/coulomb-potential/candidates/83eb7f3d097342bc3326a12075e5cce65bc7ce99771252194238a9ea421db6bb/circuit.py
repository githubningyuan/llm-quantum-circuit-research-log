# Harmless comment: this file builds the coulomb-potential circuit.
#!/usr/bin/env python3
"""coulomb-potential haner-piecewise: the normalized
HRS piecewise-polynomial circuit (arXiv:1805.12445) — exact
squared-distance front end, first-one normalization, degree 2 on 16
parity banks in signed fixed point (26, 2), barrel rescale,
segmented Bennett mirror.

Certified cost: 6,355 Toffoli x 252 qubits = 1,601,460
(assert_metrics in the epilogue).

RUN: uv run python circuit.py          (stream to $UNCOMPUTE_OPS_PATH, else ./ops.bin)
     uv run python gen_constants.py   (refit the tables into constants.json)
     uv run python checks.py          (the exhaustive datapath check)

README.md in this folder describes the circuit.
"""

from __future__ import annotations

import json
import math
import pathlib

from uncompute import Builder
from uncompute.bennett import Stages
from uncompute.gadgets import (
    add,
    declare_registers,
    gidney_add,
    gidney_sub,
    hrs_mul_rows,
    walk,
    xor_word,
)
from uncompute.testing import assert_metrics, emit_and_report, load_contract

# ---------------------------------------------------------------------------
# Instance constants (semantic scalars; the task-defining derived
# constants come from the shared contract.json below)
# ---------------------------------------------------------------------------
COORD_BITS = 8  # per-coordinate register width
ACC_BITS = 25  # accumulator width (mod 2^25)
OUT_SCALE_BITS = 24  # F(n2) = round(2^24 / sqrt(n2))
N2_BITS = 18  # n2 <= 3 * 255^2 = 195,075 < 2^18

FRAC_BITS = 19  # normalized mantissa fraction bits (reduced from 24)
WORD_BITS = FRAC_BITS + 2  # signed fixed-point word
INT_BITS = 2  # p: integer bits (fixed by 1 <= u < 2)
INTERVALS = 32  # per parity; 2*INTERVALS coefficient banks (uniform, bit-extracted label)

LABEL_BITS = (INTERVALS - 1).bit_length()  # ceil(log2(INTERVALS))
assert (1 << LABEL_BITS) >= INTERVALS > (1 << (LABEL_BITS - 1))
EXP_MAX = N2_BITS - 1  # e in [0, 17]
HMAX = EXP_MAX >> 1  # h = floor(e/2) in [0, 8]
H_BITS = 4
assert HMAX < (1 << H_BITS)  # the deepest shift fits the shift word
# rescale extension: HMAX rotation slack + the 2^24 vs 2^F scale offset
PAD_BITS = HMAX + (OUT_SCALE_BITS - FRAC_BITS)
WORD_MASK = (1 << WORD_BITS) - 1

_CONTRACT = load_contract(__file__)
ALLOWED_ULP = _CONTRACT["EPS"]  # the band the exhaustive check prices against
N2_MAX = _CONTRACT["N2_MAX"]
assert N2_MAX.bit_length() == N2_BITS
N_ACHIEVABLE = _CONTRACT["N_ACHIEVABLE"]
TBL_DIGEST = _CONTRACT["TBL_DIGEST"]
DOM_DIGEST = _CONTRACT["DOM_DIGEST"]


# ---------------------------------------------------------------------------
# The tables: gen_constants.py fits them (interval search + quantization)
# and writes constants.json; checks.py proves their worst error.
# ---------------------------------------------------------------------------


def _round_half_away(v):
    f = math.floor(v)
    return f + 1 if v - f >= 0.5 else f


def _ideal_f(n2):
    return _round_half_away((1 << OUT_SCALE_BITS) / math.sqrt(n2))


def _achievable(n2_max):
    ach = bytearray(n2_max + 1)
    for dx in range(256):
        x2 = dx * dx
        for dy in range(dx, 256):
            s2 = x2 + dy * dy
            for dz in range(dy, 256):
                ach[s2 + dz * dz] = 1
    ach[0] = 0
    return ach


def _solve3(a, b):
    m = [a[i][:] + [b[i]] for i in range(3)]
    for c in range(3):
        piv = max(range(c, 3), key=lambda r: abs(m[r][c]))
        m[c], m[piv] = m[piv], m[c]
        for r in range(c + 1, 3):
            f = m[r][c] / m[c][c]
            for k in range(c, 4):
                m[r][k] -= f * m[c][k]
    x = [0.0] * 3
    for r in range(2, -1, -1):
        s = m[r][3]
        for k in range(r + 1, 3):
            s -= m[r][k] * x[k]
        x[r] = s / m[r][r]
    return x


def _wls(us, ys, w):
    S = [0.0] * 5
    Sy = [0.0] * 3
    for u, y, wi in zip(us, ys, w):
        p = wi
        for i in range(5):
            S[i] += p
            p *= u
        p = wi
        for i in range(3):
            Sy[i] += y * p
            p *= u
    return _solve3(
        [[S[0], S[1], S[2]], [S[1], S[2], S[3]], [S[2], S[3], S[4]]], Sy
    )


def _irls(us, ys, iters=200):
    n = len(us)
    w = [1.0 / n] * n
    a = [0.0, 0.0, 0.0]
    for _ in range(iters):
        a = _wls(us, ys, w)
        errs = [abs(a[0] + a[1] * u + a[2] * u * u - y) for u, y in zip(us, ys)]
        me = max(errs)
        if me < 1e-18:
            break
        for k in range(n):
            w[k] = errs[k] / me + 1e-3
        s = sum(w)
        w = [x / s for x in w]
    return a


def _gen_thresholds(F, intervals):
    step = 1 << (F - (intervals.bit_length() - 1))
    return [(1 << F) + i * step for i in range(1, intervals)]


def _mul_trunc(V, M, F, W):
    """Exact value the pre-truncated signed multiplier emits: W += V*M*2^-F
    with each row's low fractional bits dropped individually (the HRS
    pre-truncation), V a two's-complement W-bit word."""
    Vw = V & ((1 << W) - 1)
    r = 0
    for b in range(F):
        if (Vw >> b) & 1:
            r += M >> (F - b)
    if (Vw >> F) & 1:
        r += M
    if (Vw >> (F + 1)) & 1:
        r -= M << 1
    return r


def _v0(A, M, F, W):
    mask = (1 << W) - 1
    A0, A1, A2 = A
    V1 = (A1 + _mul_trunc(A2, M, F, W)) & mask
    V0 = (A0 + _mul_trunc(V1, M, F, W)) & mask
    return V0 - (1 << W) if V0 >= (1 << (W - 1)) else V0


def _circuit_f(n2, qtab, thresholds):
    F, W = FRAC_BITS, WORD_BITS
    e = n2.bit_length() - 1
    h = e >> 1
    p = e & 1
    M = n2 << (F - e)
    i = 0
    for t in thresholds:
        if M >= t:
            i += 1
    v = _v0(qtab[(p, i)], M, F, W)
    sh = OUT_SCALE_BITS - F - h
    return (v << sh) if sh >= 0 else (v >> -sh)


def load_tables():
    """Fit (and exhaustively certify) the quantized banks and thresholds.

    Returns (qtab, thresholds) exactly as before, but the tables are
    derived here for the reduced FRAC_BITS instead of read from a file:
    the polynomial coefficients are normalized to 1/sqrt(u·2^p) and then
    the exact datapath (Horner truncation + rescale floor) is checked
    against round(2^24/sqrt(n2)) for every achievable n2.
    """
    F, W = FRAC_BITS, WORD_BITS
    sign = 1 << (W - 1)
    thresholds = _gen_thresholds(F, INTERVALS)
    assert len(thresholds) == INTERVALS - 1
    assert all((1 << F) < t < (1 << (F + 1)) for t in thresholds)
    assert all(a < c for a, c in zip(thresholds, thresholds[1:]))
    boundaries = [1 << F] + thresholds + [1 << (F + 1)]

    qtab = {}
    for p in (0, 1):
        for i in range(INTERVALS):
            lo, hi = boundaries[i], boundaries[i + 1]
            n = 192
            Ms = [lo + (hi - lo) * s // n for s in range(n)]
            us = [M / (1 << F) for M in Ms]
            ys = [1.0 / math.sqrt(u * (1 << p)) for u in us]
            a = _irls(us, ys)
            qtab[(p, i)] = [round(aj * (1 << F)) for aj in a]

    ach = _achievable(N2_MAX)
    groups = {}
    for n2 in range(1, N2_MAX + 1):
        if not ach[n2]:
            continue
        e = n2.bit_length() - 1
        p = e & 1
        h = e >> 1
        M = n2 << (F - e)
        i = 0
        for t in thresholds:
            if M >= t:
                i += 1
        groups.setdefault((p, i), []).append((M, h, _ideal_f(n2)))

    # exact discrete coordinate descent per interval (near-minimax)
    for (p, i), items in groups.items():
        best = list(qtab[(p, i)])

        def gerr(A):
            me = 0
            for M, h, ideal in items:
                v = _v0(A, M, F, W)
                sh = OUT_SCALE_BITS - F - h
                f = (v << sh) if sh >= 0 else (v >> -sh)
                e = abs(f - ideal)
                if e > me:
                    me = e
            return me

        be = gerr(best)
        for _ in range(200):
            improved = False
            for j in range(3):
                for d in (-2, -1, 1, 2):
                    cand = best[:]
                    cand[j] += d
                    ce = gerr(cand)
                    if ce < be:
                        best, be = cand, ce
                        improved = True
            if not improved:
                break
        qtab[(p, i)] = best

    maxerr, arg = 0, 0
    for n2 in range(1, N2_MAX + 1):
        if not ach[n2]:
            continue
        err = abs(_circuit_f(n2, qtab, thresholds) - _ideal_f(n2))
        if err > maxerr:
            maxerr, arg = err, n2
    assert maxerr <= ALLOWED_ULP, (maxerr, arg)
    for p in (0, 1):
        for i in range(INTERVALS):
            row = qtab[(p, i)]
            assert len(row) == 3 and all(-sign <= q < sign for q in row), row
    # The quadratic coefficient c2 is the half second derivative of a convex
    # fit (f'' > 0 throughout), so it is non-negative and < 2^(FRAC_BITS-1):
    # the first Horner multiply can drop its top three zero rows and the
    # subtract row.  Bound it explicitly so a changed fit fails loudly here
    # instead of silently emitting a wrong stream.
    C2_BITS = FRAC_BITS - 1
    for p in (0, 1):
        for i in range(INTERVALS):
            c2 = qtab[(p, i)][2]
            assert 0 <= c2 < (1 << C2_BITS), (p, i, c2)
    # Bound the Horner intermediate V1 = c1 + trunc(c2*u): if |V1| < 2^FRAC_BITS
    # then its top two bits are equal sign extension and the second multiply
    # can drop the highest (sign-extension) row.
    v1max = 0
    for n2 in range(1, N2_MAX + 1):
        if not ach[n2]:
            continue
        e = n2.bit_length() - 1
        p = e & 1
        M = n2 << (F - e)
        i = 0
        for t in thresholds:
            if M >= t:
                i += 1
        A = qtab[(p, i)]
        V1 = (A[1] + _mul_trunc(A[2], M, F, W)) & WORD_MASK
        V1s = V1 - (1 << W) if V1 >= (1 << (W - 1)) else V1
        if abs(V1s) > v1max:
            v1max = abs(V1s)
    assert v1max < (1 << F), v1max
    return qtab, thresholds


# ---------------------------------------------------------------------------
# The circuit.  Every helper closes over the live Builder; AND ancillas are
# the only anonymous transients (Gidney pairs).
# ---------------------------------------------------------------------------


def mul_rows(b, stage, V, W, X, shift, nbits, signed):
    """W += V * X * 2^-shift where V is an ``nbits``-wide word, two's
    complement when ``signed`` (the sign sits at bit ``nbits - 1``), else
    unsigned.  Identical to ``hrs_mul_rows`` except the multiplicand is
    narrower than the target: the rows for V's always-zero high bits (e.g.
    a non-negative quadratic coefficient, or a sign-extended intermediate)
    are never emitted, and an unsigned V has no subtract row at all.
    """
    for bit in range(nbits):
        b.set_phase(f"{stage}/b{bit:02d}")
        T = W[max(0, bit - shift):]
        A = X[max(0, shift - bit):][: len(T)]
        if signed and bit == nbits - 1:
            for q in T:
                b.x(q)
            add(b, T, A, ctrl=V[bit])
            for q in T:
                b.x(q)
        else:
            add(b, T, A, ctrl=V[bit])


def build_circuit(qtab, thresholds):  # noqa: C901, PLR0915 - straight-line circuit emitter
    """Emit the full circuit for the given quantized banks and interval
    thresholds; return (Builder, register dict).
    """
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

    # Coefficient words as loaded (two's complement on the 26-bit word),
    # bank pattern = interval | parity << LABEL_BITS.
    banks = {}
    for parity in (0, 1):
        for i in range(INTERVALS):
            pattern = i | (parity << LABEL_BITS)
            banks[pattern] = [q & WORD_MASK for q in qtab[(parity, i)]]

    # ---- 1. dist: n2 = sum of squared signed differences ----------------
    # Per axis (short recomputable-difference lifetime): 9-bit difference,
    # sign-conditioned negation, schoolbook square rows, immediate clear.
    with st.stage("dist", set_name="n2"):
        b.set_phase("dist/setup")
        n2b = b.qubits(N2_BITS)
        b.claim("n2", n2b)

    def square_rows(mag):
        """n2b += mag^2: schoolbook row i (bit j = mag_i AND mag_j, the
        diagonal bit mag_i itself) Gidney-added at offset i; partial
        products released right after each row.
        """
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
        for j in range(COORD_BITS):  # D = r_i,c
            b.cx(RI[ax][j], D[j])
        gidney_sub(b, RJ[ax], D)  # D = r_i,c - r_j,c (9-bit 2c)
        for q in mag:  # sign-conditioned negation
            b.cx(sgn, q)
        gidney_add(b, [sgn], mag)  # mag = |d|, sgn parked
        square_rows(mag)
        gidney_sub(b, [sgn], mag)  # immediate clear: un-negate,
        for q in mag:
            b.cx(sgn, q)
        gidney_add(b, RJ[ax], D)  # un-subtract,
        for j in range(COORD_BITS):
            b.cx(RI[ax][j], D[j])
        b.release_recycle("diff", D)  # the mirror recreates them

    # ---- 2. scan: one-hot exponent flags + steering bits -----------------
    # HRS first-one scan: z = "no set bit above e yet"; f_e = z AND n2_e,
    # then z ^= f_e keeps z running.  z ends [n2 == 0] = 0 on every graded
    # shot (equal points are domain-rejected) and is freed; the flags stay
    # live to steer normalize, the bank match, and the rescale.  Two
    # ownership sets survive the stage, so its unwind is the un_scan
    # callback: both releases, then one whole-stage mirror.
    def un_scan():
        b.set_phase("un-scan")
        b.release("exp", flags)  # the mirrored chain and_releases free them
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
        # Any function of e is a free CX-load from the one-hot flags.
        b.set_phase("scan/steer")
        pv = b.qubit()  # parity: e mod 2
        hq = b.qubits(H_BITS)  # shift word: h = floor(e/2)
        b.claim("steer", [pv] + hq)
        for e in range(N2_BITS):
            if e & 1:
                b.cx(flags[e], pv)
            for j in range(H_BITS):
                if ((e >> 1) >> j) & 1:
                    b.cx(flags[e], hq[j])
    mark_scan = b.checkpoint()

    # ---- 3. normalize: M = n2 << (24 - e) --------------------------------
    # Exactly one flag fires, so M's leading bit is one unconditional X;
    # per e, e Fredkins move n2's low bits (the swapped-in M bits are |0>,
    # so the moves also clear n2) and one CX clears the leading n2 bit.
    # The stage frees n2 mid-body and its inverse must re-claim the
    # recreated word, so its unwind is the un_normalize callback: the
    # clear segment first (recreates n2), the claim, the mant release,
    # then the setup segment (frees M).
    def un_normalize():
        b.set_phase("un-normalize")
        b.mirror(mark_nclear, until=mark_normalize)  # recreates the n2 word
        b.claim("n2", n2b)  # owned from its recreation here until the
        # dist unwind releases and frees it
        b.release("mant", M)
        b.mirror(mark_scan, until=mark_nclear)  # frees M

    with st.stage("normalize", unwind=un_normalize):
        b.set_phase("normalize/setup")
        M = b.qubits(FRAC_BITS + 1)  # bits 0..24; bit 25 of the
        b.claim("mant", M)  # (26,2) word is always 0 and
        b.x(M[FRAC_BITS])  # never allocated
        for e in range(N2_BITS):
            b.set_phase(f"normalize/e{e:02d}")
            for j in range(e):
                b.cswap(flags[e], n2b[j], M[FRAC_BITS - e + j])
            b.cx(flags[e], n2b[e])
        mark_nclear = b.checkpoint()
        b.set_phase("normalize/clear")
        b.release_recycle("n2", n2b)  # all 18 wires provably |0>
    mark_normalize = b.checkpoint()

    # ---- 4. label: 7 comparators drive the label counter -----------------
    # ---- 4. label: uniform interval -> M's top fractional bits -------------
    # With uniform thresholds the interval label is the top LABEL_BITS
    # fractional bits of M directly — no comparators, no counter, and no
    # label register (the QROM walk reads M's own wires).
    match_wires = list(M[FRAC_BITS - LABEL_BITS : FRAC_BITS]) + [pv]  # label | parity

    def coef_load_all(deg, W):
        """CX-load bank[addr][deg] into W under a one-hot walk flag."""
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
            len(banks),
            body,
        )

    # ---- 5..7. lead/h1/h0: the unpebbled Horner ladder --------------------
    # All three coefficient words are allocated together and filled by ONE
    # sawtooth walk (2^n - 2 Toffoli instead of three), then the two Horner
    # multiplies run back-to-back.  V2 = c2, V1 = c1 + V2 (x) u, V0 = c0 + V1 (x) u.
    with st.stage("coef", set_name="coef"):
        b.set_phase("coef/alloc")
        V2 = b.qubits(WORD_BITS)
        V1 = b.qubits(WORD_BITS)
        V0 = b.qubits(WORD_BITS)
        b.claim("coef", V2 + V1 + V0)

    def coef_load3(ctrl, addr):
        xor_word(b, ctrl, banks[addr][2], V2)
        xor_word(b, ctrl, banks[addr][1], V1)
        xor_word(b, ctrl, banks[addr][0], V0)

    b.set_phase("coef/load")
    walk(
        b,
        match_wires,
        None,
        len(match_wires) - 1,
        0,
        1 << len(match_wires),
        0,
        len(banks),
        coef_load3,
    )
    # u's always-zero bit 25 is not allocated: M holds bits 0..24
    b.set_phase("h1")
    mul_rows(b, "h1", V2, V1, M, FRAC_BITS, FRAC_BITS - 1, False)  # V2 = c2 >= 0
    b.set_phase("h0")
    mul_rows(b, "h0", V1, V0, M, FRAC_BITS, WORD_BITS - 1, True)  # |V1| < 2^FRAC_BITS
    # V0 = c0[bank] + V1 (x) u : the poly0 word; V0 < 2^25 (checked)

    # ---- 8. rescale: barrel-rotate [pad | V0] right by h ------------------
    # Four controlled cyclic rotations (s = 1, 2, 4, 8 under h's bits);
    # each cycle of length L costs L - 1 Fredkins, 34 - gcd(34, s) total.
    # Afterwards bits [8:33) hold V0 >> h and the truncated low bits sit
    # in the pad wires (reversible; the mirror unwinds them).
    with st.stage("rescale", set_name="pad"):
        b.set_phase("rescale/pad")
        pad = b.qubits(PAD_BITS)
        b.claim("pad", pad)
    w34 = list(pad) + list(V0)  # ty: ignore[invalid-argument-type]  # stages is non-empty, so V is set

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

    # ---- 9. accumulate: k += (V0 >> h) mod 2^25 (not mirrored) -----------
    # Bit 33 of the rotated word is provably 0 (V0 < 2^25 and the wrapped
    # positions are pad zeros), so the 25-bit window [8:33) is the whole
    # value; truncation is the checked rescale rule.
    b.set_phase("accumulate/add")
    gidney_add(b, w34[HMAX : HMAX + ACC_BITS], KQ)

    # ---- 10. un-*: Bennett mirror of stages 8..1 --------------------------
    # st.unwind replays the recorded stages in reverse.  Per standard
    # stage: set_phase(un-<name>); mirror the body (the stage's wires
    # return to |0>); release the ownership set; mirror the head (the
    # frees) — so release precedes free through the whole teardown, and
    # the reflected sub-phase labels come from each segment's own forward
    # marks.  The scan and normalize stages contribute their unwind=
    # callbacks instead (two surviving sets; the n2 re-claim).  The
    # segment boundaries partition the forward item range exactly, and
    # set_phase/claim/release/checkpoint emit no ops, so together the
    # calls emit the same reversed item range as one whole-region mirror
    # and the op stream is byte-identical to it.
    st.unwind(until=mark_round)

    return b, regs


def build():
    """Run the raw build (also the profiler tools' entry): load the tables
    and construct the circuit.  Returns (Builder, register dict).
    """
    return build_circuit(*load_tables())


if __name__ == "__main__":
    b, _regs = build()
    # The peak sits at the widest multiplier row (h0/b24) and decomposes
    # as the persistent file (I/O + flags + steer + mantissa + label + 3
    # words) + that row's transients (25 chain ANDs + 25 addend ANDs = 50).
    assert_metrics(b, __file__)
    emit_and_report(b)
