import json, math

tables = json.load(open('/input/runs/hhl-20261106-single-8bba06b072/attempt-01/constants.json'))
qtab = tables["qtab"]
bounds = tables["bounds"]
XMIN = 1<<16; XMAX = (1<<21)-1
FRAC = 38
S = 6
edges = [XMIN] + bounds + [XMAX]

def ideal_h(x):
    v = math.asin((1<<15)/x)*4294967296.0
    f = math.floor(v)
    return f+1 if v-f>=0.5 else f

maxcoef = 0
maxint = 0
worst_err = 0
for i in range(len(edges)-1):
    loX, hiX = edges[i], edges[i+1]
    c = qtab[i]
    for cc in c:
        maxcoef = max(maxcoef, abs(cc))
    for X in range(loX, hiX+1):
        x = X/65536.0
        V3 = c[3]
        V2 = c[2] + (c[3]*X)//(1<<16)
        V1 = c[1] + (V2*X)//(1<<16)
        V0 = c[0] + (V1*X)//(1<<16)
        for v in (V3,V2,V1,V0):
            maxint = max(maxint, abs(v))
        out = math.floor((V0 + (1<<(S-1)))/(1<<S))
        e = abs(out - ideal_h(X))
        worst_err = max(worst_err, e)

print("max coeff abs =", maxcoef, "= value", maxcoef/(1<<FRAC), "-> int_bits need", (maxcoef.bit_length()+1))
print("max intermediate abs =", maxint, "= value", maxint/(1<<FRAC), "-> int_bits need", (maxint.bit_length()+1))
print("worst error ULP =", worst_err)
print("min INT_BITS for coeff:", (maxcoef.bit_length()+1))
print("min INT_BITS for interm:", (maxint.bit_length()+1))
