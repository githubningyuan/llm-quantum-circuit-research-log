#!/usr/bin/env python3
"""Rebuild archived circuit source and run the original task evaluator (no LLM)."""
import argparse, hashlib, importlib.util, json, os, subprocess, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
def load(p):return json.loads(p.read_text())
def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('task',choices=['hhl-angle','coulomb-potential','both']);ap.add_argument('--out',type=Path,default=ROOT/'replay-output');ap.add_argument('--jobs',type=int,default=2);a=ap.parse_args()
 for result in load(ROOT/'data/results.json'):
  task=result['task'];expected=result['best'];
  if a.task not in (task,'both'):continue
  target=ROOT/'tasks'/task/'candidates'/expected['id'];out=a.out.resolve()/task;out.mkdir(parents=True,exist_ok=True)
  # Load build() without executing the original __main__ epilogue (which may
  # contain an obsolete baseline cost pin); native grader decides validity.
  spec=importlib.util.spec_from_file_location('archived_'+task.replace('-','_'),target/'circuit.py');m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
  b,_=m.build();ops=out/'candidate.ops'
  with ops.open('wb') as f:b.emit(dest=f,meta=False)
  digest=hashlib.sha256(ops.read_bytes()).hexdigest()
  if task=='hhl-angle' and digest!=expected['ops_sha256']:raise SystemExit('HHL emitted stream differs from event-bound archive')
  env=os.environ.copy();env['UNCOMPUTE_KERNEL']='0'
  p=subprocess.run([sys.executable,str(ROOT/'tools/eval.py'),task,'--ops',str(ops),'--json','--jobs',str(a.jobs),'--out',str(out)],env=env,capture_output=True,text=True,check=True)
  report=json.loads(p.stdout);(out/'native-report.json').write_text(json.dumps(report,indent=2)+'\n');m=report['metrics']
  if report['verdict']!='OK' or report['h_ops']!=digest or (m['toffoli'],m['qubits'],m['tq_cost'])!=(expected['T'],expected['Q'],expected['TQ']):raise SystemExit(f'{task}: unexpected native result {report}')
  print(f'{task}: OK T={m["toffoli"]:.0f} Q={m["qubits"]} TQ={m["tq_cost"]} OPS={digest}')
if __name__=='__main__':main()
