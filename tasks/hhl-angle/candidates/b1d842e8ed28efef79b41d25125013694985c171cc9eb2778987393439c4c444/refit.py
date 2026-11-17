import math, json

XMIN = 1 << 16
XMAX = (1 << 21) - 1

def ideal_h(x):
    v = math.asin((1 << 15) / x) * 4294967296.0
    f = math.floor(v)
    return f + 1 if v - f >= 0.5 else f

def solve(A, b):
    n = len(b)
    M = [list(row) + [b[i]] for i, row in enumerate(A)]
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

def remez(lo, hi, deg):
    # minimax fit of asin(0.5/x) on [lo,hi], degree deg
    f = lambda x: math.asin(0.5 / x)
    n = deg + 2  # reference points
    # chebyshev init
    pts = []
    for i in range(n):
        t = 0.5 - 0.5 * math.cos(math.pi * i / (n - 1))
        pts.append(lo + (hi - lo) * t)
    for it in range(60):
        # build system
        A = [[pts[i]**j for j in range(deg+1)] + [(-1)**i] for i in range(n)]
        b = [f(pts[i]) for i in range(n)]
        sol = solve(A, b)
        coef = sol[:deg+1]
        E = sol[-1]
        # error function
        def err(x):
            p = 0.0
            for c in reversed(coef):
                p = p * x + c
            return p - f(x)
        # find extrema on dense grid
        M = 400
        xs = [lo + (hi - lo) * i / M for i in range(M+1)]
        es = [err(x) for x in xs]
        # find local extrema
        extrema = []
        for i in range(1, M):
            if (es[i] >= es[i-1] and es[i] >= es[i+1]) or (es[i] <= es[i-1] and es[i] <= es[i+1]):
                extrema.append((xs[i], es[i]))
        # also endpoints
        extrema.append((xs[0], es[0]))
        extrema.append((xs[M], es[M]))
        # select n alternation points
        # sort by x
        extrema.sort()
        if len(extrema) < n:
            break
        # pick n best (largest |err| with alternating sign)
        # simple: greedily pick
        newpts = []
        # use largest-magnitude alternating set
        cand = sorted(extrema, key=lambda t: -abs(t[1]))
        # but need alternating signs and increasing x -> take sign of largest, then search
        # fallback: evenly pick from extrema
        if len(extrema) >= n:
            # choose points with alternating signs of maximal magnitude
            best = None
            for start in range(len(extrema) - n + 1):
                seg = extrema[start:start+n]
                if all(s != 0 for _, s in seg):
                    signs = [1 if s > 0 else -1 for _, s in seg]
                    ok = all(signs[i] != signs[i+1] for i in range(n-1))
                    if ok:
                        mag = sum(abs(s) for _, s in seg)
                        if best is None or mag > best[0]:
                            best = (mag, [p for p, _ in seg])
            if best is not None:
                newpts = best[1]
        if not newpts:
            break
        newpts = sorted(newpts)
        if max(abs(a-b) for a, b in zip(newpts, pts)) < 1e-12 * (hi - lo):
            pts = newpts
            break
        pts = newpts
    return coef

def evalp(coef, x):
    r = 0.0
    for c in reversed(coef):
        r = r * x + c
    return r

def fit_interval(lo, hi, deg, FRAC, loX, hiX):
    a = remez(lo, hi, deg)
    c = [round(a_i * (1 << FRAC)) for a_i in a]
    # greedy refine
    def maxerr(cs):
        me = 0
        for X in range(loX, hiX+1, (hiX-loX)//400 + 1):
            x = X / 65536.0
            p = evalp(cs, x)
            out = math.floor((p + (1 << (FRAC-32-1))) / (1 << (FRAC-32)))
            e = abs(out - ideal_h(X))
            if e > me: me = e
        return me
    best = maxerr(c)
    for _ in range(3):
        improved = False
        for k in range(len(c)):
            for d in (-2, -1, 1, 2):
                cc = list(c); cc[k] += d
                e = maxerr(cc)
                if e < best:
                    c = cc; best = e; improved = True
        if not improved:
            break
    return c, best

def run(FRAC, INT, deg=3):
    tables = json.load(open('/input/runs/hhl-20261106-single-8bba06b072/attempt-01/constants.json'))
    bounds = tables["bounds"]
    edges = [XMIN] + bounds + [XMAX]
    qtab = []
    worst = 0
    WH = 1 << (INT + FRAC - 1)
    for i in range(len(edges)-1):
        loX, hiX = edges[i], edges[i+1]
        lo, hi = loX/65536.0, hiX/65536.0
        c, me = fit_interval(lo, hi, deg, FRAC, loX, hiX)
        worst = max(worst, me)
        for cc in c:
            if abs(cc) >= WH:
                print(f"  overflow interval {i}: |c|={abs(cc)} >= {WH}")
        qtab.append(c)
    # full scan
    S = FRAC - 32
    fullworst = 0
    maxint = 0
    for i in range(len(edges)-1):
        loX, hiX = edges[i], edges[i+1]
        c = qtab[i]
        for X in range(loX, hiX+1):
            x = X / 65536.0
            p = evalp(c, x)
            out = math.floor((p + (1 << (S-1))) / (1 << S))
            e = abs(out - ideal_h(X))
            if e > fullworst: fullworst = e
    return fullworst, qtab

if __name__ == "__main__":
    for FRAC in [38, 36, 34]:
        for INT in [4, 3]:
            fullworst, qtab = run(FRAC, INT)
            print(f"FRAC={FRAC} INT={INT} worst={fullworst} {'OK' if fullworst<=429 else 'FAIL'}")
