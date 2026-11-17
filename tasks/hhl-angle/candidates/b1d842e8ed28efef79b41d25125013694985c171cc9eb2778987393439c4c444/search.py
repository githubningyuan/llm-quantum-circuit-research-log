import math, json, sys

XMIN = 1 << 16
XMAX = (1 << 21) - 1

def ideal_h(x):
    v = math.asin((1 << 15) / x) * 4294967296.0
    f = math.floor(v)
    return f + 1 if v - f >= 0.5 else f

def round_half_away(v):
    f = math.floor(v)
    return f + 1 if v - f >= 0.5 else f

def trailing_zeros(n):
    if n == 0:
        return 0
    c = 0
    while n & 1 == 0:
        n >>= 1
        c += 1
    return c

def label_cost(bounds):
    total = 0
    for bnd in bounds:
        addend = (1 << 21) - bnd
        total += 20 - trailing_zeros(addend)
    return total

def mul_cost(WORD_BITS, XBITS=21, shift=16):
    total = 0
    for bit in range(WORD_BITS):
        T_len = WORD_BITS - max(0, bit - shift)
        if bit < shift:
            A_len = XBITS - (shift - bit)
            if A_len < 0: A_len = 0
            if A_len > T_len: A_len = T_len
        else:
            A_len = min(XBITS, T_len)
        total += (T_len - 1) + A_len
    return total

def total_cost(DEGREE, INTERVALS, INT_BITS, FRAC_BITS, bounds):
    WORD_BITS = INT_BITS + FRAC_BITS
    LABEL_BITS = max(1, (INTERVALS - 1).bit_length())
    coef_loads = INTERVALS * (DEGREE + 1) * (LABEL_BITS - 1)
    label = label_cost(bounds)
    mul = mul_cost(WORD_BITS)
    forward = label + coef_loads + DEGREE * mul
    return 2 * forward + 32

def fit_poly(xs, ys, deg):
    # least squares fit deg in x
    m = deg + 1
    A = [[0.0]*(m+1) for _ in range(m)]
    for x, y in zip(xs, ys):
        row = [x**j for j in range(m)] + [y]
        for i in range(m):
            ai = row[i]
            for j in range(m):
                A[i][j] += ai * row[j]
            A[i][m] += ai * y
    # gaussian elimination
    n = m
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(A[r][col]))
        A[col], A[piv] = A[piv], A[col]
        d = A[col][col]
        for j in range(col, n+1):
            A[col][j] /= d
        for r in range(n):
            if r != col:
                f = A[r][col]
                for j in range(col, n+1):
                    A[r][j] -= f * A[col][j]
    return [A[i][n] for i in range(n)]

def eval_poly(coef, x):
    r = 0.0
    for c in reversed(coef):
        r = r * x + c
    return r

def fit_interval(lo, hi, deg, FRAC):
    # fit poly to asin(0.5/x) on [lo,hi], return integer coefs (units 2^-FRAC)
    pts = []
    N = 40
    for i in range(N):
        t = 0.5 - 0.5*math.cos(math.pi*(i+0.5)/N)
        x = lo + (hi - lo)*t
        pts.append(x)
    pts = sorted(set(pts))
    xs = pts
    ys = [math.asin(0.5/x) for x in xs]
    coef = fit_poly(xs, ys, deg)
    coef = [round(c * (1 << FRAC)) for c in coef]
    return coef

def check_interval(coef, lo, loX, hiX, FRAC, S, INT_BITS, step=1):
    # coef = [c0,c1,...] integers, evaluate exact-ish via float
    WORD_HALF = 1 << (INT_BITS + FRAC - 1)
    maxerr = 0
    maxv = 0
    # sample every X
    for X in range(loX, hiX+1, step):
        x = X / 65536.0
        # horner intermediates
        vals = []
        acc = 0.0
        for c in reversed(coef):
            acc = acc * x + c
            vals.append(acc)
        vals.append(acc)  # final V0
        for v in vals:
            a = abs(v)
            if a > maxv:
                maxv = a
        p = acc
        out = math.floor((p + (1 << (S-1))) / (1 << S))
        ideal = ideal_h(X)
        e = abs(out - ideal)
        if e > maxerr:
            maxerr = e
    return maxerr, maxv, WORD_HALF

def build_bounds_geom(INTERVALS):
    # x-space [1, 32], geometric ratio
    lo = 1.0
    hi = 32.0
    ratio = (hi/lo) ** (1.0/INTERVALS)
    edges = [lo * (ratio**i) for i in range(INTERVALS+1)]
    edges[-1] = hi
    # convert to X (round to int, ensure ascending & within domain)
    bounds = []
    for e in edges[1:-1]:
        X = int(round(e * 65536))
        bounds.append(X)
    # fix duplicates / ordering
    prev = XMIN
    out = []
    for b in bounds:
        if b <= prev:
            continue
        if b >= XMAX:
            break
        out.append(b)
        prev = b
    return out

def fit_all(DEGREE, INTERVALS, INT_BITS, FRAC_BITS, bounds, step=1):
    S = FRAC_BITS - 32
    edges = [XMIN] + bounds + [XMAX]
    qtab = []
    allok = True
    worst = 0
    for i in range(len(edges)-1):
        loX, hiX = edges[i], edges[i+1]
        lo = loX/65536.0
        hi = hiX/65536.0
        coef = fit_interval(lo, hi, DEGREE, FRAC_BITS)
        me, mv, wh = check_interval(coef, lo, loX, hiX, FRAC_BITS, S, INT_BITS, step)
        worst = max(worst, me)
        if me > 429 or mv >= wh:
            allok = False
        qtab.append(coef)
    return allok, worst, qtab

def search():
    results = []
    for DEGREE in [3, 2]:
        for INTERVALS in [35, 28, 24, 20, 16, 40]:
            if INTERVALS < 2: continue
            bounds = build_bounds_geom(INTERVALS)
            if len(bounds) < INTERVALS - 1:
                continue
            for INT_BITS in [6, 5, 4]:
                for FRAC_BITS in [38, 36]:
                    if FRAC_BITS < 33: continue
                    ok, worst, qtab = fit_all(DEGREE, INTERVALS, INT_BITS, FRAC_BITS, bounds, step=512)
                    cost = total_cost(DEGREE, INTERVALS, INT_BITS, FRAC_BITS, bounds)
                    results.append((cost, DEGREE, INTERVALS, INT_BITS, FRAC_BITS, ok, worst))
                    tag = "OK" if ok else "NO"
                    print(f"{tag}  deg={DEGREE} n={INTERVALS} int={INT_BITS} frac={FRAC_BITS} worst={worst} cost={cost}")
    print("---- best valid ----")
    valid = [r for r in results if r[5]]
    valid.sort()
    for r in valid[:10]:
        print(r)

if __name__ == "__main__":
    search()
