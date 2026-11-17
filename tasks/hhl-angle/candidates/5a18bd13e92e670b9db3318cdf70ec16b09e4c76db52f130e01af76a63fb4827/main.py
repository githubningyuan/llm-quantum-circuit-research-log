"""HHL seeded circuit entry for the restricted Research Broker.

Source `circuit.py` remains editable. The author's baseline fixed-cost
assertions are intentionally not used here: all changed candidates must be
scored by the independent native grader, not the original cost pin.
"""
import importlib.util
import os
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root / 'hhl-uncompute-author.zip'))
src = Path(__file__).resolve().parent / 'circuit.py'
spec = importlib.util.spec_from_file_location('hhl_candidate', src)
if spec is None or spec.loader is None:
    raise ImportError('HHL candidate cannot be loaded')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
builder, _registers = module.build()
output = Path(os.environ.get('HHL_RESEARCH_OUTPUT', '/work/outputs/candidate.ops'))
output.parent.mkdir(parents=True, exist_ok=True)
with output.open('wb') as fh:
    builder.emit(dest=fh, meta=False)
print('HHL_CANDIDATE_EMITTED', builder.phase_summary()['total_emitted_toffoli'], builder.peak_qubits)
