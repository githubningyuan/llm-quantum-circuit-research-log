#!/usr/bin/env python3
"""Read-only Coulomb batch summary. No OpenCode, API, evaluator, or DB imports."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def summarize(report: dict) -> dict:
    rows = []
    for r in report.get('rounds', []):
        p = r.get('progress') or {}
        children = r.get('new_offspring') or []
        costs = [c.get('tq_cost') for c in children if isinstance(c, dict)
                 and isinstance(c.get('tq_cost'), (int, float)) and not isinstance(c.get('tq_cost'), bool)]
        before = (r.get('preflight') or {}).get('db_best_tq')
        after = (r.get('postflight') or {}).get('db_best_tq')
        parent_sha = p.get('parent_sha256')
        child_shas = [c.get('sha256') for c in children if isinstance(c, dict)
                      and isinstance(c.get('sha256'), str)]
        parent = p.get('parent_tq')
        best_child = p.get('best_offspring_tq', min(costs) if costs else None)
        round_best = p.get('db_best_tq_after', after)
        def usable_num(x):
            return isinstance(x, (int, float)) and not isinstance(x, bool) and 0 < x < float('inf')
        parent_gain = (100 * (parent - best_child) / parent
                       if usable_num(parent) and usable_num(best_child) else None)
        gap = (100 * (best_child - round_best) / round_best
               if usable_num(round_best) and usable_num(best_child) else None)
        rows.append({
            'round': r.get('number'),
            'status': r.get('status'),
            'inner_status': r.get('inner_status'),
            'parent_tq': p.get('parent_tq'),
            'parent_sha256': parent_sha,
            'offspring_sha256': child_shas,
            'offspring_count': p.get('offspring_count', len(children)),
            'best_offspring_tq': best_child,
            'parent_improvement_pct': parent_gain,
            'best_offspring_gap_to_global_pct': gap,
            'best_tq_before': p.get('db_best_tq_before', before),
            'best_tq_after': p.get('db_best_tq_after', after),
            'new_global_best': p.get('new_global_best',
                                      bool(before is not None and after is not None and after < before)),
            'parent_improved_by_child': p.get('parent_improved_by_child'),
            'retained_offspring_count': p.get('retained_offspring_count'),
            'best_retained_offspring_tq': p.get('best_retained_offspring_tq'),
            'retained_parent_improvement_pct': p.get('retained_parent_improvement_pct'),
            'meaningful_parent_progress': p.get('meaningful_parent_progress'),
            'made_research_progress': p.get('made_research_progress'),
            'parent_is_previous_round_offspring': p.get('parent_is_previous_round_offspring'),
            'restored_original': r.get('restored_original'),
        })
    # Older v2 reports did not have the parent-reuse boolean, but do have SHA
    # provenance. Compute exact cross-round reuse only from saved hashes.
    for i, row in enumerate(rows):
        if i and row['parent_is_previous_round_offspring'] is None and row['parent_sha256']:
            prev_shas = rows[i-1]['offspring_sha256']
            row['parent_is_previous_round_offspring'] = row['parent_sha256'] in prev_shas
    return {
        'status': report.get('status'),
        'stop_reason': report.get('stop_reason'),
        'error': report.get('error'),
        'requested_rounds': report.get('requested_rounds'),
        'max_no_best': report.get('max_no_best'),
        'max_no_progress': report.get('max_no_progress'),
        'min_parent_improvement_pct': report.get('min_parent_improvement_pct'),
        'completed_rounds': sum(row['status'] == 'verified_completed' for row in rows),
        'initial_best_tq': (report.get('initial_state') or {}).get('db_best_tq'),
        'latest_best_tq': next((row['best_tq_after'] for row in reversed(rows)
                                if row['best_tq_after'] is not None),
                               (report.get('initial_state') or {}).get('db_best_tq')),
        'rounds': rows,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--file', type=Path, help='Optional exact batch.json; default latest existing batch')
    args = ap.parse_args(argv)
    if args.file is None:
        root = Path.home() / 'quantum-agent/runs/coulomb-batches'
        files = sorted(root.glob('20*/batch.json'), key=lambda x: x.stat().st_mtime)
        if not files:
            ap.error('No completed or in-progress batch report found')
        path = files[-1]
    else:
        path = args.file
    output = summarize(json.loads(path.read_text()))
    print(json.dumps({'batch_report': str(path), **output}, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
