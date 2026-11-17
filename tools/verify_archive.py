#!/usr/bin/env python3
"""Verify public archive integrity without evaluating or executing candidate code."""
import csv, hashlib, json, lzma, struct
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def read(p):return json.loads(p.read_text())
def sha(b):return hashlib.sha256(b).hexdigest()
def rows(task):return list(csv.DictReader((ROOT/'tasks'/task/'data/candidates.csv').open()))
def check():
 h=rows('hhl-angle');c=rows('coulomb-potential');base=ROOT/'tasks/hhl-angle/candidates';new=[r for r in h if r['source_kind']=='P11_or_R3_admitted'];assert len(h)==39 and len(new)==37
 for r in h:
  d=base/r['id'];s=read(d/'source.json');hash=hashlib.sha256(bytes.fromhex(s['bundle_domain_hex']));e=s['entry'].encode('ascii');hash.update(struct.pack('>I',len(e))+e)
  for k,v in sorted(s['files'].items()):
   n=k.encode('ascii');b=(d/v['file']).read_bytes();assert sha(b)==v['sha256'];hash.update(struct.pack('>I',len(n))+n+struct.pack('>Q',len(b))+b)
  assert hash.hexdigest()==r['id']==s['bundle_sha256']
  assert int(r['T'])*int(r['Q'])==int(r['TQ'])
  if r in new:
   p=read(d/'receipt.json');assert p['native_result']['verdict']=='OK';assert p['admission']['source_bound'];assert p['admission']['db_admission']=='NATIVE_OPENEVOLVE_SAVE_RELOAD_OK';assert sha(lzma.decompress((d/'candidate.ops.xz').read_bytes()))==r['ops_sha256']==p['native_result']['h_ops'];assert int(r['TQ'])==p['native_result']['tq_cost']
  if r['parent_id']:assert r['parent_id'] in {x['id'] for x in h}
 assert min(int(r['TQ']) for r in new)==476952
 for archive,count in [('production79',79),('control88',88),('treatment117',117)]:assert sum(x['archive']==archive for x in c)==count
 for r in c:
  d=ROOT/'tasks/coulomb-potential/candidates'/r['id'];assert sha((d/'circuit.py').read_bytes())==r['source_sha256'];assert int(r['T'])*int(r['Q'])==int(r['TQ']);p=d/'receipt.json'
  if p.exists():a=read(p);assert a['valid'] and a['sha256']==r['source_sha256'] and int(a['metrics']['tq_cost'])==int(r['TQ'])
 assert len({r['id'] for r in c})==126 and min(int(r['TQ']) for r in c)==346035
 print('Archive integrity OK: HHL 37 new + 2 prior seeds; Coulomb 126 unique / 284 memberships')
if __name__=='__main__':check()
