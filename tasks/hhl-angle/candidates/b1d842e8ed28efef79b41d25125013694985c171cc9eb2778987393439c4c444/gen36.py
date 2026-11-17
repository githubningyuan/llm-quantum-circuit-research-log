import math, json

XMIN = 1 << 16
XMAX = (1 << 21) - 1
FRAC = 36
INT = 4

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
    f = lambda x: math.asin(0.5 / x)
    n = deg + 2
    pts = [lo + (hi - lo) * (0.5 - 0.5 * math.cos(math.pi * i / (n - 1))) for i in range(n)]
    coef = None
    for it in range(80):
        A = [[pts[i]**j for j in range(deg+1)] + [(-1)**i] for i in range(n)]
        b = [f(pts[i]) for i in range(n)]
        sol = solve(A, b)
        coef = sol[:deg+1]
        def err(x):
            p = 0.0
            for c in reversed(coef):
                p = p * x + c
            return p - f(x)
        M = 500
        xs = [lo + (hi - lo) * i / M for i in range(M+1)]
        es = [err(x) for x in xs]
        extrema = []
        for i in range(1, M):
            if (es[i] >= es[i-1] and es[i] >= es[i+1]) or (es[i] <= es[i-1] and es[i] <= es[i+1]):
                extrema.append((xs[i], es[i]))
        extrema.append((xs[0], es[0])); extrema.append((xs[M], es[M]))
        extrema.sort()
        best = None
        for start in range(len(extrema) - n + 1):
            seg = extrema[start:start+n]
            signs = [1 if s > 0 else -1 for _, s in seg]
            if all(s != 0 for _, s in seg) and all(signs[i] != signs[i+1] for i in range(n-1)):
                mag = sum(abs(s) for _, s in seg)
                if best is None or mag > best[0]:
                    best = (mag, [p for p, _ in seg])
        if best is None:
            break
        newpts = sorted(best[1])
        if max(abs(a-b) for a, b in zip(newpts, pts)) < 1e-13 * (hi - lo):
            pts = newpts
            break
        pts = newpts
    return coef

def evalp(coef, x):
    r = 0.0
    for c in reversed(coef):
        r = r * x + c
    return r

def fit_interval(lo, hi, loX, hiX):
    a = remez(lo, hi, 3)
    c = [round(ai * (1 << FRAC)) for ai in a]
    S = FRAC - 32
    def maxerr(cs, step):
        me = 0
        for X in range(loX, hiX+1, step):
            x = X / 65536.0
            p = evalp(cs, x)
            out = math.floor((p + (1 << (S-1))) / (1 << S))
            e = abs(out - ideal_h(X))
            if e > me: me = e
        return me
    best = maxerr(c, (hiX-loX)//300 + 1)
    for _ in range(4):
        improved = False
        for k in range(len(c)):
            for d in (-2, -1, 1, 2):
                cc = list(c); cc[k] += d
                e = maxerr(cc, (hiX-loX)//300 + 1)
                if e < best:
                    c = cc; best = e; improved = True
        if not improved:
            break
    return c, best

tables = json.load(open('/input/runs/hhl-20261106-single-8bba06b072/attempt-01/constants.json'))
bounds = tables["bounds"]
edges = [XMIN] + bounds + [XMAX]
qtab = []
worst = 0
for i in range(len(edges)-1):
    loX, hiX = edges[i], edges[i+1]
    c, me = fit_interval(loX/65536.0, hiX/65536.0, loX, hiX)
    worst = max(worst, me)
    qtab.append(c)
print("worst sampled ULP:", worst)

# full exact scan (integer arithmetic, two's complement truncation)
S = FRAC - 32
ROUND_HALF = 1 << (S - 1)
WORDMASK = (1 << (INT + FRAC)) - 1
def trunc_div(a, q):
    # arithmetic shift right by q on two's-complement word
    return a >> q
fullworst = 0
maxint = 0
for i in range(len(edges)-1):
    loX, hiX = edges[i], edges[i+1]
    c0, c1, c2, c3 = qtab[i]
    for X in range(loX, hiX+1):
        V3 = c3 & WORDMASK
        if V3 >= (1 << (INT+FRAC-1)): V3 -= (1 << (INT+FRAC))
        V2 = (c2 + trunc_div(V3 * X, 16)) & WORDMASK
        if V2 >= (1 << (INT+FRAC-1)): V2 -= (1 << (INT+FRAC))
        V1 = (c1 + trunc_div(V2 * X, 16)) & WORDMASK
        if V1 >= (1 << (INT+FRAC-1)): V1 -= (1 << (INT+FRAC))
        V0 = (c0 + ROUND_HALF + trunc_div(V1 * X, 16)) & WORDMASK
        if V0 >= (1 << (INT+FRAC-1)): V0 -= (1 << (INT+FRAC))
        out = V0 >> S
        ideal = ideal_h(X)
        e = abs(out - ideal)
        if e > fullworst: fullworst = e
print("full exact worst ULP:", fullworst)

out = {"qtab": qtab, "bounds": bounds}
import os
os.makedirs('/work/outputs', exist_ok=True)
with open('/work/outputs/constants36.json', 'w') as fh:
    json.dump(out, fh)
print("WROTE constants36.json")
print(json.dumps(out))
