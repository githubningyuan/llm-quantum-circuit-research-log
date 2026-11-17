import sys
print("py", sys.version)
for m in ["numpy", "scipy", "mpmath"]:
    try:
        __import__(m)
        print(m, "available")
    except ImportError as e:
        print(m, "MISSING")
