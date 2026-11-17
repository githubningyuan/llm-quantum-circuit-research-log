#!/usr/bin/env python3
"""Create evidence-labeled Research Memory v1 from a read-only Coulomb audit ZIP.

No model calls, no candidate execution, no source/DB edits.
Writes only a new JSON in a caller-selected empty output location.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from zipfile import ZipFile

from coulomb_selective_sidecar import catalog_from_zip


def make_memory(archive: Path) -> dict:
    m, catalog = catalog_from_zip(archive)
    with ZipFile(archive) as z:
        sessions = json.loads(z.read('summaries.json'))
        batches = json.loads(z.read('batches.json'))
    if len(sessions) != 39 or len(catalog) != 7:
        raise ValueError('This v1 research pack expects the verified 39-session / 7-circuit export')
    bysha = {p['id']: p for p in catalog}
    bylabel = {p['label']: p for p in catalog}
    records = [c for s in sessions for c in s.get('returned_to_database', [])]
    if not all(isinstance(c,dict) and 'sha256' in c for c in records):
        raise ValueError('Malformed returned offspring list')
    for parent_label,child_label in [('latest_parent_423735','db79_best_348775'),
                                     ('db35_best_450385','db71_best_420865'),
                                     ('db26_best_559493','latest_parent_423735')]:
        parent, child = bylabel[parent_label], bylabel[child_label]
        if not any(s.get('parent_sha256') == parent['id'] and any(c.get('sha256') == child['id'] for c in s.get('returned_to_database',[])) for s in sessions):
            raise ValueError('Missing verified direct parent-child edge '+parent_label+' -> '+child_label)
    aa, bb = bylabel['latest_parent_423735'], bylabel['db79_best_348775']
    p450, c420 = bylabel['db35_best_450385'], bylabel['db71_best_420865']
    def pair(a,b):
        return {'parent_sha256': a['id'], 'child_sha256':b['id'],
                'parent_T':int(a['toffoli']), 'parent_Q':int(a['qubits']),
                'parent_TQ':int(a['tq_cost']), 'child_T':int(b['toffoli']),
                'child_Q':int(b['qubits']), 'child_TQ':int(b['tq_cost']),
                'tq_reduction_pct':round(100*(a['tq_cost']-b['tq_cost'])/a['tq_cost'],4)}
    def note(nid, title, obs, hyp, evidence, confidence):
        return {'id':nid,'title':title,'observation':obs,'hypothesis':hyp,
                'evidence':evidence,'interpretation_confidence':confidence,
                'verification_rule':'Only archived valid snapshots / recorded parent-child edges; no circuit re-execution'}
    notes = [
        note('N01','精度與暫存器寬度協同縮減',
             '已驗證的 423735→348775 子代：T 2173→1993，Q 195→175，TQ 降低 17.6903%；程式差異包括 INDEX_BITS 7→6、normalize 的 M 分配由 FRAC_BITS 改為 MAN_BITS，累加器由 gidney_add 換成 add。',
             '減少索引精度及算術暫存寬度可能共同降低成本；各修改的獨立因果貢獻尚未經消融驗證。',
             pair(aa,bb),'verified metrics and code diff; causal claim untested'),
        note('N02','原位正規化分支',
             '已驗證的 450385→420865 子代：T 2197→2053，Q 保持 205；原始碼增加 shift_left() 及依 exponent flags 進行受控原位位移，替代分配 M 暫存器的 normalize 實作。',
             '原位 normalize 可能在某些位寬條件下有利；與其他家族的相容性未驗證。',
             pair(p450,c420),'verified metrics and code diff; causal claim untested'),
        note('N03','同一輪可產生多個可驗證子代',
             '最新第 1 輪從 Parent 423735 保存 3 個子代，TQ 分別為 408524、380081、348775。',
             '一次 Session 內迭代修改與工具回饋可能比只保留最後結果更有效，但目前沒有消融對照。',
             {'session':'20261101T050400Z-8a6b3f5ae2','parent_sha256':aa['id'],
              'offspring_TQ':[408524,380081,348775]},'recorded session summary'),
        note('N04','Timeout 快照救援仍有效',
             '39 Session 摘要中 22 次 model_timeout_with_verified_offspring、7 次 model_timeout_no_new_offspring；此數據只能顯示保存結果，不代表模型原本能完成任務。',
             '保留已驗證快照可避免 Timeout 導致全部成果遺失；需另以執行成本與可驗證新增數衡量效率。',
             {'status_counts':dict(Counter(s['status'] for s in sessions))},'archived summaries'),
        note('N05','同一任務內保留不同算術結構',
             '保留的代表原始碼包含使用 carry-save/unsigned_mul_rows/shift_left 的 420865 分支，也包含不同 normalize 與加法器設計的 348775 分支；七份程式不能代表完整 DB79。',
             '這些結構可能存在可交換的技巧，亦可能互不相容；需要獨立驗證後才可稱為知識轉移。',
             {'examples':[p450['id'],c420['id'],bb['id']], 'audited_source_count':len(catalog)},'source-visible features, family unproven'),
    ]
    first = min(s['run'] for s in sessions)
    last = max(s['run'] for s in sessions)
    return {
        'schema':'coulomb-research-memory-v1',
        'created_from':'coulomb_research_audit_20261101T092158Z.zip',
        'archive_generated_at_utc':m['generated_at_utc'],
        'data_scope':{'sessions':len(sessions),'batches':len(batches),'archived_valid_snapshots_count':m['valid_snapshot_count'],
                      'other_or_invalid_snapshot_count':m['other_or_invalid_snapshot_count'],
                      'audited_source_circuits':len(catalog),'entire_DB79_exported':False,
                      'full_failure_transcripts_exported':False,'source_regraded_in_this_analysis':False},
        'summary':{'first_run':first,'last_run':last,'status_counts':dict(Counter(s.get('status','unknown') for s in sessions)),
                   'returned_offspring_records':len(records),
                   'distinct_returned_offspring':len({c['sha256'] for c in records}),
                   'restored_original_sessions':sum(s.get('restored_original') is True for s in sessions),
                   'best_sha256':bb['id'],'best_TQ':bb['tq_cost'],
                   'baseline_TQ':1601460,'improvement_factor':round(1601460/bb['tq_cost'],5),
                   'archive_best_events':m['best_history_from_batch']},
        'representative_circuits':[{'label':p['label'],'sha256':p['id'],
                                    'toffoli':p['toffoli'],'qubits':p['qubits'],
                                    'tq_cost':p['tq_cost']} for p in catalog],
        'notes':notes,
        'gaps':['No complete 79-program DB or archived source for each offspring',
                'No full OpenCode tool-error transcripts; cannot produce reliable failure taxonomy',
                'Parent-child edges before seed ingestion often missing',
                'No isolated ablation tests to attribute T/Q gains to individual code changes'],
        'policy':'Never promote hypotheses to facts; every proposed transfer must be independently validated.'
    }


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--audit-zip',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()
    if args.output.exists() or args.output.is_symlink():
        raise SystemExit('Refusing to overwrite existing memory JSON')
    memory=make_memory(args.audit_zip)
    with args.output.open('x',encoding='utf-8') as out:
        json.dump(memory,out,indent=2,ensure_ascii=False)
        out.write('\n')
    print(json.dumps({'status':'MEMORY_V1_BUILT_NO_API','output':str(args.output),
                      'notes':len(memory['notes']),'summary':memory['summary']},indent=2,ensure_ascii=False))

if __name__=='__main__':
    main()
