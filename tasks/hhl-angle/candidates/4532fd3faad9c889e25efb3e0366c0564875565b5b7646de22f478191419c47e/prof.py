import importlib.util
import sys
from pathlib import Path
sys.path.insert(0, '/input/hhl-uncompute-author.zip')
root = Path('/input')
src = Path('/input/runs/hhl-20261106-single-8bba06b072/attempt-01/circuit.py')
spec = importlib.util.spec_from_file_location('hhl_candidate', src)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
builder, regs = module.build()
ps = builder.phase_summary()
import json
print(json.dumps(ps, indent=1, default=str))
