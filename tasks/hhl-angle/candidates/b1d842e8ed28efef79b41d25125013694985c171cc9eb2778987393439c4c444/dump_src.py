import inspect
import sys
sys.path.insert(0, '/input/hhl-uncompute-author.zip')
import uncompute.gadgets as g
import uncompute
print("gadgets file:", g.__file__)
import uncompute
import os
print("uncompute path:", list(uncompute.__path__))

import importlib
mods = {}
for name in ['arith','qrom','linear','wires']:
    m = importlib.import_module('uncompute.gadgets.'+name)
    mods[name] = m
    print("----", name, "----")
    print(inspect.getsource(m))
