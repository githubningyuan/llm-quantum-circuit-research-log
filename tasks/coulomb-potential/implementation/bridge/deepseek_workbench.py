#!/usr/bin/env python3
"""DeepSeek quantum-circuit workbench (v0.3.8).

LLM chooses algorithms and edits. Host provides versioned, scoped file tools and
calls the existing Docker evaluator. No candidate code is run on the host.

No OpenEvolve integration yet. This is the isolated agent-loop building block.
"""
from __future__ import annotations

import argparse
import copy
import ast
import hashlib
import importlib
import inspect
import json
import os
import re
import shutil
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

MAX_SOURCE_BYTES = 1_000_000
MAX_CIRCUIT_BYTES = 400_000
MAX_TOOL_TEXT = 13_000
BASELINE_TQ = 1_601_460
# Fingerprints of the actually evaluated, original Haner seed and its table.
# Never assert a modified or unrelated seed is known-valid merely because it
# runs in seed mode. Neither of these is a GPT-generated candidate.
VERIFIED_SEED_SHA256 = '02ea1d59b31ebc036c391aec3450d5d56431d95da3affcd32c7eccb316f9cd79'
VERIFIED_CONSTANTS_SHA256 = 'dada247f99109b94fa9a64c0894c968f29ee72f53e2334e15061dbed36e6aba0'


def stamp() -> str:
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_safe(obj: Any) -> Any:
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    if hasattr(obj, 'model_dump'):
        return json_safe(obj.model_dump())
    if hasattr(obj, '__dict__'):
        return json_safe(vars(obj))
    return repr(obj)


def write_json(path: Path, data: Any) -> None:
    # Atomic replacement: a crash must not leave a truncated conversation.json.
    tmp = path.with_name(path.name + f'.{uuid.uuid4().hex[:8]}.tmp')
    try:
        tmp.write_text(json.dumps(json_safe(data), ensure_ascii=False, indent=2) + '\n')
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def task_brief(repo: Path) -> str:
    """Only the task contract, not published discovered-algorithm hints."""
    task = repo / 'tasks/coulomb-potential/README.md'
    body = task.read_text()
    match = re.search(r'^## 1\. Operation', body, flags=re.M)
    if not match:
        raise ValueError('Cannot locate task contract start in task README')
    body = body[match.start():]
    end = re.search(r'^## 4\.', body, flags=re.M)
    if not end:
        raise ValueError('Cannot locate discovered-algorithm boundary in task README')
    return body[:end.start()].strip()


def _norm_eval_result(raw: Any) -> dict[str, Any]:
    """Accept existing OpenEvolve EvaluationResult or tools/eval flat dict.

    Fail closed if no explicit validity is available. Never guess correctness
    from a score or a plausible looking TQ value.
    """
    obj = json_safe(raw)
    if not isinstance(obj, dict):
        return {'valid': False, 'error': 'Evaluator returned non-dict result', 'raw': str(obj)[:4000]}
    metrics = obj.get('metrics') if isinstance(obj.get('metrics'), dict) else obj
    artifacts = obj.get('artifacts') if isinstance(obj.get('artifacts'), dict) else {}
    valid = None
    for d in (metrics, obj):
        for key in ('valid', 'is_valid'):
            v = d.get(key)
            if isinstance(v, bool):
                valid = v
                break
            if isinstance(v, (int, float)) and v in (0, 1):
                valid = bool(v)
                break
        if valid is not None:
            break
    # Some existing evaluator implementations report 1.0 correctness.
    if valid is None and isinstance(metrics.get('correctness'), (int, float)):
        valid = metrics['correctness'] == 1.0
    out = {'valid': bool(valid), 'validity_explicit': valid is not None, 'metrics': metrics,
           'artifacts': artifacts}
    if not out['validity_explicit']:
        out['warning'] = 'Evaluator did not expose explicit validity; not accepted as a valid circuit.'
    for key in ('toffoli', 'qubits', 'tq_cost', 'score', 'fail_reason', 'stage'):
        if key in metrics:
            out[key] = metrics[key]
        elif key in obj:
            out[key] = obj[key]
    if 'tq_cost' not in out and all(isinstance(out.get(k), (int, float)) for k in ('toffoli', 'qubits')):
        out['tq_cost'] = out['toffoli'] * out['qubits']
    if out.get('valid') and isinstance(out.get('tq_cost'), (int, float)):
        out['improvement_pct'] = round(100 * (1 - out['tq_cost'] / BASELINE_TQ), 5)
    return out



def prepare_evaluator_input(source: str) -> str:
    """Add an EVALUATION-ONLY guard at every Builder.emit() call.

    The existing evaluator requires exactly one `    assert_metrics(b, __file__)`
    line, which it replaces with its ancilla-leak guard. Put that marker
    in a wrapper around Builder.emit(), NOT in model-authored source, so a
    fresh program can be validated without a fixed template. All saved
    DeepSeek candidate revisions remain unmodified.
    """
    # The evaluator tests resources itself; replace the known legacy pin.
    unpinned = re.sub(
        r'(?m)^([ \t]*)assert_metrics\s*\(\s*b\s*,\s*__file__\s*\)[ \t]*(?:#.*)?$',
        lambda m: m.group(1) + 'pass  # old fixed-cost pin suppressed for evaluation',
        source,
    )
    tree = ast.parse(unpinned, filename='circuit.py')
    prelude_end = 0
    for node in tree.body:
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)) or (
                isinstance(node, ast.ImportFrom) and node.module == '__future__'):
            prelude_end = node.end_lineno
        else:
            break
    shim = """
# --- WORKBENCH EVALUATION INSTRUMENTATION (not model-authored) ---
from uncompute.builder import Builder as _WBBuilder
_wb_original_emit = _WBBuilder.emit

def _wb_checked_emit(self, *args, **kwargs):
    b = self
    assert_metrics(b, __file__)
    return _wb_original_emit(self, *args, **kwargs)

_WBBuilder.emit = _wb_checked_emit
# --- END WORKBENCH EVALUATION INSTRUMENTATION ---
"""
    # Future imports must remain before the instrumentation code.
    lines = unpinned.splitlines(keepends=True)
    return ''.join(lines[:prelude_end]) + shim + ''.join(lines[prelude_end:])

def _evaluator_callable(module: str = 'evaluator') -> Callable[[str], Any]:
    try:
        mod = importlib.import_module(module)
    except Exception as e:
        raise RuntimeError(f'Cannot import {module}.py: {e}') from e
    if not callable(getattr(mod, 'evaluate', None)):
        raise RuntimeError(f'{module}.evaluate(...) is missing')
    return mod.evaluate


class Workbench:
    """A dedicated mutable circuit.py with immutable numbered revisions."""

    def __init__(self, *, repo: Path, seed: Path, runs: Path, mode: str,
                 max_evals: int, evaluate_fn: Callable[[str], Any] | None = None):
        self.repo = repo.resolve()
        self.seed = seed.resolve()
        self.mode = mode
        self.max_evals = max_evals
        self.eval_fn = evaluate_fn
        if mode not in ('seed', 'blank'):
            raise ValueError('mode must be seed or blank')
        if not self.seed.is_file():
            raise FileNotFoundError(f'Missing seed: {self.seed}')
        self.session = (runs.resolve() / f'{stamp()}-{uuid.uuid4().hex[:8]}')
        self.session.mkdir(parents=True, exist_ok=False)
        (self.session / 'revisions').mkdir()
        (self.session / 'evaluations').mkdir()
        self.circuit = self.session / 'circuit.py'
        self.evals = 0
        self.rev = 0
        self.best: dict[str, Any] | None = None
        self.trials: list[dict[str, Any]] = []
        initial = self.seed.read_text() if mode == 'seed' else (
            '# Coulomb-potential task: implement a reversible circuit using uncompute.\n'
            '# Read the provided task contract and library API.\n'
        )
        self._save(initial, note=f'initial ({mode})')
        self._init_source_access()
        meta = {
            'mode': mode, 'seed_sha256': sha256(self.seed.read_bytes()),
            'repo_root': str(self.repo), 'seed': str(self.seed),
            'session': str(self.session), 'max_evals': max_evals,
            'baseline_tq': BASELINE_TQ, 'created_utc': stamp(),
            'algorithm_provenance': 'DeepSeek-generated, no GPT candidate seeded',
            'published_discovered_solution_excluded_from_model_reads': True,
        }
        write_json(self.session / 'manifest.json', meta)
        self._log('session_created', meta)

    def _init_source_access(self) -> None:
        self.source_aliases = {
            # A model may reasonably use the basename mentioned in the task.
            # Resolve only explicit, allowlisted aliases; never basename-search
            # the whole repository (that could expose published circuits).
            'reference.py': 'tasks/coulomb-potential/reference.py',
            'contract.json': 'tasks/coulomb-potential/contract.json',
        }
        self.source_allowed_files = {
            (self.repo / 'tasks/coulomb-potential/contract.json').resolve(),
            (self.repo / 'tasks/coulomb-potential/reference.py').resolve(),
            (self.repo / 'tools/eval.py').resolve(),
        }
        # Seed experiments may inspect the constants consumed by their seed;
        # blank-start experiments should not see this baseline-specific table.
        if self.mode == 'seed':
            seed_constants = 'tasks/coulomb-potential/circuits/haner-piecewise/constants.json'
            self.source_aliases['constants.json'] = seed_constants
            self.source_allowed_files.add((self.repo / seed_constants).resolve())
        self.source_root = self.repo / 'uncompute'

    @classmethod
    def resume(cls, *, session: Path, max_evals: int,
               evaluate_fn: Callable[[str], Any] | None = None) -> 'Workbench':
        """Reattach to an existing v0.2/v0.3 session without replacing files."""
        session = session.expanduser().resolve()
        manifest = json.loads((session / 'manifest.json').read_text())
        if session != Path(manifest['session']).resolve():
            raise ValueError('Session path differs from its manifest; refusing resume')
        repo = Path(manifest['repo_root']).resolve()
        seed = Path(manifest['seed']).resolve()
        if not repo.is_dir() or not seed.is_file():
            raise FileNotFoundError('Original repository or seed unavailable')
        if sha256(seed.read_bytes()) != manifest['seed_sha256']:
            raise ValueError('Seed checksum changed since session creation')
        if manifest['mode'] not in ('seed', 'blank'):
            raise ValueError('Invalid stored mode')
        wb = cls.__new__(cls)
        wb.repo, wb.seed, wb.mode = repo, seed, manifest['mode']
        wb.session, wb.circuit, wb.max_evals = session, session / 'circuit.py', max_evals
        wb.eval_fn = evaluate_fn
        versions = sorted((session / 'revisions').glob('r[0-9][0-9][0-9][0-9].py'))
        if not versions or [p.name for p in versions] != [f'r{i:04d}.py' for i in range(1, len(versions)+1)]:
            raise ValueError('Revision numbering is missing/noncontiguous')
        wb.rev = len(versions)
        if not wb.circuit.is_file() or wb.circuit.read_bytes() != versions[-1].read_bytes():
            raise ValueError('Current circuit differs from last revision; refusing resume')
        wb.trials = []
        for i, folder in enumerate(sorted((session / 'evaluations').glob('trial_*')), 1):
            if folder.name != f'trial_{i:03d}' or not (folder / 'result.json').is_file():
                raise ValueError('Incomplete evaluation history; refusing resume')
            wb.trials.append(json.loads((folder / 'result.json').read_text()))
        wb.evals = len(wb.trials)
        if max_evals < wb.evals:
            raise ValueError('Cannot reduce --max-evals below already consumed evaluations')
        best = session / 'best_valid.json'
        wb.best = json.loads(best.read_text()) if best.is_file() else None
        if wb.best is not None and (not wb.best.get('valid') or not (session / 'best_valid.py').is_file()):
            raise ValueError('Invalid best_valid checkpoint')
        wb._init_source_access()
        return wb

    def _log(self, event: str, data: Any) -> None:
        with (self.session / 'events.jsonl').open('a') as f:
            f.write(json.dumps({'ts_utc': stamp(), 'event': event,
                                'data': json_safe(data)}, ensure_ascii=False) + '\n')

    def _save(self, content: str, *, note: str) -> dict[str, Any]:
        if not isinstance(content, str):
            raise ValueError('content must be a string')
        payload = content.encode('utf-8')
        if len(payload) > MAX_CIRCUIT_BYTES:
            raise ValueError('Circuit file too large')
        self.rev += 1
        version = self.session / 'revisions' / f'r{self.rev:04d}.py'
        version.write_bytes(payload)
        tmp = self.circuit.with_suffix('.py.new')
        tmp.write_bytes(payload)
        tmp.replace(self.circuit)
        info = {'revision': self.rev, 'sha256': sha256(payload), 'bytes': len(payload), 'note': note}
        self._log('revision', info)
        return info

    def _revision_file(self, revision: int) -> Path:
        path = self.session / 'revisions' / f'r{revision:04d}.py'
        if not path.exists() or revision < 1 or revision > self.rev:
            raise ValueError(f'Unknown revision {revision}')
        return path

    def read_circuit(self, start: int = 1, count: int = 120) -> dict[str, Any]:
        return self._read_numbered(self.circuit, start, count)

    def write_circuit(self, content: str) -> dict[str, Any]:
        return self._save(content, note='full rewrite')

    def edit_circuit(self, edits: list[dict[str, str]]) -> dict[str, Any]:
        if not isinstance(edits, list) or not (1 <= len(edits) <= 20):
            raise ValueError('edits must contain 1–20 replacements')
        current = self.circuit.read_text()
        candidate = current
        for ix, edit in enumerate(edits, 1):
            if not isinstance(edit, dict) or not isinstance(edit.get('old'), str) or not isinstance(edit.get('new'), str):
                raise ValueError(f'Edit {ix} requires old/new strings')
            old, new = edit['old'], edit['new']
            if not old:
                raise ValueError('Empty old string forbidden; use write_circuit')
            matches = candidate.count(old)
            if matches != 1:
                raise ValueError(f'Edit {ix} matches {matches} locations (must match exactly one); no changes made')
            candidate = candidate.replace(old, new, 1)
        if candidate == current:
            raise ValueError('Edits made no change')
        return self._save(candidate, note=f'{len(edits)} exact edits')

    def revert_circuit(self, revision: int) -> dict[str, Any]:
        return self._save(self._revision_file(revision).read_text(), note=f'reverted from r{revision:04d}')

    def _safe_source(self, path: str) -> Path:
        if not isinstance(path, str) or not path or '\x00' in path:
            raise ValueError('Invalid path')
        # Friendly aliases for contract/reference and seed assets. Full
        # repository-relative paths continue to work as before.
        canonical = self.source_aliases.get(path, path)
        requested = (self.repo / canonical).resolve()
        allowed_lib = requested.is_relative_to(self.source_root.resolve())
        allowed_one = requested in self.source_allowed_files
        if not (allowed_lib or allowed_one):
            raise PermissionError('Model source access limited to uncompute API + task contract/reference; no grader or discovered circuits')
        if requested.suffix not in ('.py', '.md', '.json', '.txt'):
            raise PermissionError('Source must be text')
        if not requested.is_file() or requested.stat().st_size > MAX_SOURCE_BYTES:
            raise FileNotFoundError('Source file unavailable or too large')
        return requested

    @staticmethod
    def _read_numbered(path: Path, start: int, count: int) -> dict[str, Any]:
        start = max(1, int(start))
        count = max(1, min(180, int(count)))
        lines = path.read_text().splitlines()
        requested_end = min(len(lines), start + count - 1)
        # Always return whole numbered lines.  A character-sliced excerpt
        # silently split function definitions in the previous versions.
        numbered = [f'{i + 1:5d}: {lines[i]}' for i in range(start - 1, requested_end)]
        included: list[str] = []
        chars = 0
        for line in numbered:
            length = len(line) + (1 if included else 0)
            if included and chars + length > MAX_TOOL_TEXT:
                break
            # Exceptional single long lines are truncated visibly.
            if not included and length > MAX_TOOL_TEXT:
                included.append(line[:MAX_TOOL_TEXT])
                chars = MAX_TOOL_TEXT
                break
            included.append(line)
            chars += length
        end_line = start + len(included) - 1
        return {'path': path.name, 'total_lines': len(lines), 'start': start,
                'end_line': end_line, 'returned_lines': len(included),
                'has_more': end_line < len(lines),
                'content': '\n'.join(included)}

    def source_outline(self, path: str) -> dict[str, Any]:
        """Discover allowlisted library modules OR inspect one Python module.

        No code is executed, no parent/task directory can be listed, and
        symlinks are never followed during directory discovery.
        """
        if not isinstance(path, str) or not path or '\x00' in path:
            raise ValueError('Invalid path')
        requested = (self.repo / self.source_aliases.get(path, path)).resolve()
        library_root = self.source_root.resolve()
        if requested.is_dir():
            if not requested.is_relative_to(library_root):
                raise PermissionError('Directory listing limited to uncompute library')
            entries = []
            for item in sorted(requested.iterdir(), key=lambda p: (not p.is_dir(), p.name)):
                if item.is_symlink() or not item.resolve().is_relative_to(library_root):
                    continue
                if item.is_dir():
                    if item.name.startswith('.') or item.name == '__pycache__':
                        continue
                    kind = 'directory'
                elif item.is_file() and item.suffix in ('.py', '.md'):
                    kind = 'python' if item.suffix == '.py' else 'documentation'
                else:
                    continue
                entries.append({'path': str(item.relative_to(self.repo)),
                                'kind': kind})
            return {'path': str(requested.relative_to(self.repo)),
                    'kind': 'directory', 'entries': entries[:100],
                    'entries_total': len(entries), 'truncated': len(entries) > 100,
                    'hint': 'Call source_outline with a Python file path for its symbols; use read_source for exact lines.'}
        requested = self._safe_source(path)
        if requested.suffix != '.py':
            raise ValueError('source_outline requires a Python .py file or an uncompute library directory')
        tree = ast.parse(requested.read_text(), filename=str(requested))
        symbols: list[dict[str, Any]] = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                prefix = 'async def' if isinstance(node, ast.AsyncFunctionDef) else 'def'
                signature = f'{prefix} {node.name}({ast.unparse(node.args)})'
                if node.returns:
                    signature += f' -> {ast.unparse(node.returns)}'
            elif isinstance(node, ast.ClassDef):
                signature = f'class {node.name}'
            else:
                continue
            doc = ast.get_docstring(node) or ''
            symbols.append({'name': node.name, 'signature': signature[:240],
                            'start': node.lineno, 'end': node.end_lineno,
                            'summary': doc.strip().splitlines()[0][:180] if doc else ''})
        return {'path': str(requested.relative_to(self.repo)),
                'total_lines': len(requested.read_text().splitlines()),
                'symbols': symbols[:100], 'symbols_total': len(symbols),
                'truncated': len(symbols) > 100}

    def read_source(self, path: str, start: int = 1, count: int = 100) -> dict[str, Any]:
        requested = self._safe_source(path)
        result = self._read_numbered(requested, start, count)
        result['path'] = str(requested.relative_to(self.repo))
        return result

    def search_source(self, query: str, limit: int = 20) -> dict[str, Any]:
        if not isinstance(query, str) or not 2 <= len(query) <= 150:
            raise ValueError('query must be 2–150 characters')
        limit = max(1, min(40, int(limit)))
        available = sorted(self.source_root.rglob('*.py'))
        available += sorted(f for f in self.source_root.rglob('*.md') if f.is_file())
        available += [f for f in sorted(self.source_allowed_files) if f.is_file()]
        matches = []
        for path in available:
            if path.stat().st_size > MAX_SOURCE_BYTES:
                continue
            for i, line in enumerate(path.read_text(errors='replace').splitlines(), 1):
                if query.lower() in line.lower():
                    matches.append({'path': str(path.relative_to(self.repo)), 'line': i, 'text': line[:220]})
                    if len(matches) >= limit:
                        return {'matches': matches, 'limited_to': limit}
        return {'matches': matches, 'limited_to': limit}

    def run_evaluator(self) -> dict[str, Any]:
        if self.evals >= self.max_evals:
            return {'error': f'Evaluation budget exhausted ({self.max_evals})', 'revision': self.rev}
        if self.eval_fn is None:
            return {'error': 'No evaluator configured', 'revision': self.rev}
        self.evals += 1
        trial_no = self.evals
        snapshot_dir = self.session / 'evaluations' / f'trial_{trial_no:03d}'
        snapshot_dir.mkdir()
        candidate = snapshot_dir / 'circuit.py'
        shutil.copy2(self.circuit, candidate)
        contents_hash = sha256(candidate.read_bytes())
        start = time.monotonic()
        try:
            # Keep the model-authored candidate untouched; the current Docker
            # evaluator's marker and guard go in a separate copy only.
            instrumented = snapshot_dir / 'evaluator_input.py'
            instrumented.write_text(prepare_evaluator_input(candidate.read_text()))
            raw = self.eval_fn(str(instrumented))
            if inspect.isawaitable(raw):
                import asyncio
                raw = asyncio.run(raw)
            result = _norm_eval_result(raw)
        except Exception as e:
            result = {'valid': False, 'validity_explicit': True,
                      'error': f'Evaluator exception: {type(e).__name__}: {e}'}
        result.update({'revision': self.rev, 'trial': trial_no,
                       'sha256': contents_hash, 'elapsed_s': round(time.monotonic() - start, 3)})
        write_json(snapshot_dir / 'result.json', result)
        self.trials.append(result)
        self._log('evaluation', result)
        if result['valid']:
            if self.best is None or self._better(result, self.best):
                self.best = result
                shutil.copy2(candidate, self.session / 'best_valid.py')
                write_json(self.session / 'best_valid.json', result)
                self._log('new_best_valid', result)
        # Full raw evidence is on disk; return a compact diagnostic to the model.
        concise = {k: v for k, v in result.items() if k not in ('artifacts', 'metrics')}
        concise['metrics'] = result.get('metrics', {})
        artifacts = result.get('artifacts', {})
        if artifacts:
            concise['artifacts'] = {k: str(v)[:3500] for k, v in artifacts.items()}
        concise['remaining_evaluations'] = self.max_evals - self.evals
        return concise

    @staticmethod
    def _better(new: dict[str, Any], best: dict[str, Any]) -> bool:
        def value(result: dict[str, Any]) -> tuple[float, float]:
            metrics = result.get('metrics') or {}
            tq = result.get('tq_cost', metrics.get('tq_cost'))
            if isinstance(tq, (int, float)) and tq > 0:
                return (1.0, -float(tq))
            score = result.get('score', metrics.get('score'))
            if isinstance(score, (int, float)):
                return (0.0, float(score))
            return (-1.0, 0.0)
        return value(new) > value(best)

    def status(self) -> dict[str, Any]:
        known_seed = verified_original_seed(self)
        return {'session': str(self.session), 'revision': self.rev, 'evaluations': self.evals,
                'remaining_evaluations': self.max_evals - self.evals,
                'best_valid': self.best, 'current_sha256': sha256(self.circuit.read_bytes()),
                'original_seed_verified': known_seed,
                'original_seed_metrics': ({'toffoli': 6355, 'qubits': 252,
                                           'tq_cost': BASELINE_TQ, 'revision': 1}
                                          if known_seed else None)}

    def execute(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        handlers = {
            'read_circuit': self.read_circuit,
            'write_circuit': self.write_circuit,
            'edit_circuit': self.edit_circuit,
            'revert_circuit': self.revert_circuit,
            'read_source': self.read_source,
            'source_outline': self.source_outline,
            'search_source': self.search_source,
            'run_evaluator': self.run_evaluator,
            'get_status': self.status,
        }
        try:
            if tool not in handlers:
                raise ValueError(f'Unknown tool: {tool}')
            return json_safe(handlers[tool](**args))
        except Exception as e:
            self._log('tool_error', {'tool': tool, 'error': str(e)})
            return {'error': f'{type(e).__name__}: {e}'}


def function_tool(name: str, desc: str, properties: dict, required: list[str] | None = None) -> dict:
    return {'type': 'function', 'function': {
        'name': name, 'description': desc,
        'parameters': {'type': 'object', 'properties': properties,
                       'required': required or [], 'additionalProperties': False}}}


def tool_specs() -> list[dict]:
    S = {'type': 'string'}
    N = {'type': 'integer'}
    return [
        function_tool('read_circuit', 'Read numbered lines in the current editable circuit.py.',
                      {'start': N, 'count': N}),
        function_tool('write_circuit', 'Replace ENTIRE working circuit.py. Use for new algorithm families or large rewrites.',
                      {'content': S}, ['content']),
        function_tool('edit_circuit', 'Apply 1 to 20 exact old/new replacements ATOMICALLY to current circuit.py. Edits accumulate across calls.',
                      {'edits': {'type': 'array', 'items': {'type': 'object', 'properties': {'old': S, 'new': S},
                                                         'required': ['old', 'new']}}}, ['edits']),
        function_tool('revert_circuit', 'Restore any earlier version as a NEW revision. Past versions stay untouched.',
                      {'revision': N}, ['revision']),
        function_tool('read_source', 'Read at most 180 numbered source lines; result reports end_line and has_more. Provide path relative to repository root; reference.py and contract.json are aliases; constants.json is a seed-only alias. Cannot read discovered circuits/grader.',
                      {'path': S, 'start': N, 'count': N}, ['path']),
        function_tool('source_outline', 'Give path="uncompute" to discover the library file tree, path="uncompute/gadgets" to list its files, or a .py file path to see function/class signatures and line ranges. Does not execute code; cannot access task grader or discovered circuits.',
                      {'path': S}, ['path']),
        function_tool('search_source', 'Search allowlisted API/task sources (including reference.py and seed-mode constants.json) for a case-insensitive substring; no discovered circuits/grader.',
                      {'query': S, 'limit': N}, ['query']),
        function_tool('run_evaluator', 'Evaluate CURRENT revision through the user-provided isolated Docker evaluator. A snapshot is preserved even for failures.', {}),
        function_tool('get_status', 'Inspect remaining tests, working revision, and best previously VALID circuit.', {}),
    ]


def verified_original_seed(wb: Workbench) -> bool:
    """Only advertise known-good evidence for the byte-identical tested seed/assets."""
    if wb.mode != 'seed' or sha256(wb.seed.read_bytes()) != VERIFIED_SEED_SHA256:
        return False
    constants = wb.repo / 'tasks/coulomb-potential/circuits/haner-piecewise/constants.json'
    return constants.is_file() and sha256(constants.read_bytes()) == VERIFIED_CONSTANTS_SHA256


def research_status_notice(wb: Workbench) -> str:
    """Concise, auditable facts about evidence and *current* test allowance."""
    status = wb.status()
    if verified_original_seed(wb):
        evidence = (
            'The ORIGINAL Haner-piecewise seed at revision 1 was already independently '
            'verified as CORRECT with the original uncompute evaluator: '
            'Toffoli=6355, qubits=252, T×Q=1601460 (verdict OK). '
            'You do NOT need to spend an evaluation merely to establish that '
            'this unchanged seed works. Your research objective is to improve it. '
            'That evidence applies to revision 1 only; any edits require their own tests. '
            'The field best_valid=null only means no candidate has been verified '
            'during THIS workbench session; it does not mean the original seed is invalid.'
        )
    elif wb.mode == 'seed':
        evidence = (
            'You started from a supplied seed, but its exact code/assets do not '
            'match the independently verified original baseline fingerprints. '
            'Do not assume the supplied seed is valid without testing.'
        )
    else:
        evidence = ('You started from a blank program. No known-valid circuit is '
                    'supplied or claimed for this session.')
    return (
        '[WORKBENCH VERIFIED CONTEXT AND CURRENT BUDGET]\n'
        + evidence + '\n'
        + f'Current revision: {status["revision"]}. '
        + f'Evaluations already used: {status["evaluations"]}. '
        + f'Evaluations available NOW: {status["remaining_evaluations"]} '
        + f'(session maximum {wb.max_evals}). '
        + 'A failed candidate consumes one evaluation, but preserves your working '
        + 'file and earlier revisions so you can edit or revert and retry while '
        + 'evaluations remain. A revised evaluation limit on resume replaces any '
        + 'older budget shown in the conversation. '
        + 'You decide the algorithm, edits, and when a candidate is ready to test.'
    )


STATUS_NOTICE_MARKER = '[WORKBENCH VERIFIED CONTEXT AND CURRENT BUDGET]'


def append_resume_status(wb: Workbench, messages: list[dict]) -> bool:
    """Append updated facts without rewriting old turns or duplicating a notice."""
    notice = research_status_notice(wb)
    if messages and messages[-1].get('role') == 'user' and messages[-1].get('content') == notice:
        return False
    messages.append({'role': 'user', 'content': notice})
    write_json(wb.session / 'conversation.json', messages)
    wb._log('research_status_update', {
        'revision': wb.rev, 'evaluations': wb.evals,
        'remaining_evaluations': wb.max_evals - wb.evals,
        'verified_original_seed': verified_original_seed(wb),
    })
    return True


def system_prompt(mode: str) -> str:
    part = ('Optimize the baseline if helpful, or fully replace it with your own approach.'
            if mode == 'seed' else 'You are starting with a BLANK program and must invent a circuit implementation.')
    return f'''You are DeepSeek, acting as an independent quantum-circuit research coding agent.
Your task is to design, implement, debug, and optimize a reversible circuit for the supplied Coulomb task.
{part}
You alone choose algorithms, representations, and resource tradeoffs; the harness does not prescribe an algorithm.
Your primary deliverable is a concrete, independently evaluated circuit candidate, not a literature review.
Source reading is OPTIONAL and should be targeted to a concrete implementation question; you do not need to inspect the entire library before editing.
When you have a plausible improvement idea, implement it as a testable revision rather than waiting for perfect confidence.
Use write_circuit for whole-program edits, edit_circuit for multiple exact edits, and run_evaluator to test.
If a test fails, inspect the feedback, repair, and retry when budget permits; an invalid attempt does not destroy earlier revisions.
Tool edits persist in the same working file. Earlier revisions and best verified candidate are preserved automatically.
A rejected circuit is not evidence of success. Only explicit evaluator valid=true counts. Prioritize correctness then T×Q.
Do not alter validation/test files or exploit the grader. Model source access deliberately excludes the published discovered solution.
Read the latest workbench status update for your CURRENT remaining evaluation budget.
Evaluation failures consume a trial, not your saved circuit; choose when to test and revise as needed.
When finished, report what YOU tested and measured; never claim unverified results.'''


def session_usage(session: Path) -> dict[str, int]:
    """Existing v0.2 and v0.3 sessions both write model_call events."""
    calls, tokens = 0, 0
    events = session / 'events.jsonl'
    if events.exists():
        for line in events.read_text().splitlines():
            event = json.loads(line)
            if event.get('event') == 'model_call':
                calls += 1
                data = event['data']
                # v0.2 logs cumulative tokens for every model response.
                tokens = int(data.get('cumulative_tokens', tokens + data.get('total_tokens', 0)))
    return {'model_calls': calls, 'total_tokens': tokens}


def verify_conversation(messages: list[dict]) -> None:
    if not isinstance(messages, list) or len(messages) < 2 or messages[0].get('role') != 'system':
        raise ValueError('Unexpected conversation format; refusing resume')
    pending: set[str] = set()
    for msg in messages:
        if msg['role'] == 'assistant':
            if pending:
                raise ValueError(f'Unanswered prior tool calls {pending}; refusing unsafe resume')
            pending = {str(t['id']) for t in msg.get('tool_calls', [])}
        elif msg['role'] == 'tool':
            if msg.get('tool_call_id') not in pending:
                raise ValueError('Unexpected tool reply; refusing unsafe resume')
            pending.remove(msg['tool_call_id'])
        elif pending:
            raise ValueError('Message inserted before tool replies; refusing unsafe resume')
    if pending:
        raise ValueError('Conversation ended with unanswered tools; refusing unsafe resume')


def context_for_api(messages: list[dict], max_chars: int) -> tuple[list[dict], dict[str, int]]:
    """Compact only previously CONSUMED large source-read replies in the API copy.

    Full original outputs and every action remain in conversation.json. Search
    hits are retained. Never summarize the most recent unseen tool results.
    """
    api = copy.deepcopy(messages)
    chars_before = len(json.dumps(api, ensure_ascii=False))
    compacted = 0
    if chars_before > max_chars:
        # First identify tool results that a subsequent assistant response has
        # ALREADY consumed. The newest tool batch must remain intact for the
        # model's next call. Crucially, compact OLDEST consumed reads first,
        # so a just-read module is not removed before the model can combine it
        # with the next page of the same file.
        assistant_after = False
        eligible = []
        for i in range(len(api) - 1, -1, -1):
            msg = api[i]
            if msg.get('role') == 'assistant':
                assistant_after = True
            elif msg.get('role') == 'tool' and assistant_after:
                eligible.append(i)
        for i in reversed(eligible):
            msg = api[i]
            if len(json.dumps(api, ensure_ascii=False)) <= max_chars:
                break
            try:
                content = json.loads(msg.get('content') or '{}')
            except (TypeError, ValueError):
                continue
            if not isinstance(content, dict) or not isinstance(content.get('content'), str):
                continue
            # Search results and evaluator verdicts do not contain a 'content'
            # source excerpt and must never be compacted.
            numbered = content['content'].splitlines()
            headings = [ln[:180] for ln in numbered if re.match(
                r'^\s*\d+:\s*(?:async\s+def|def|class|from|import|#\s*(?:---|===))', ln)]
            short = {'compacted_prior_source_read': True,
                     'path': content.get('path'), 'start': content.get('start'),
                     'total_lines': content.get('total_lines'),
                     'symbols_and_imports': headings[:32],
                     'note': 'Previously read; source body omitted only from API context to control cost. '
                             'Use read_circuit/read_source again if exact lines are needed.'}
            replacement = json.dumps(short, ensure_ascii=False)
            if len(replacement) < len(msg['content']):
                msg['content'] = replacement
                compacted += 1
    return api, {'chars_before': chars_before,
                 'chars_sent': len(json.dumps(api, ensure_ascii=False)),
                 'source_reads_compacted': compacted}


IMPLEMENTATION_CHECKPOINT_MARKER = '[WORKBENCH CHECKPOINT: FIRST TESTABLE CANDIDATE]'


def add_implementation_checkpoint(wb: Workbench, messages: list[dict]) -> bool:
    """Log an algorithm-neutral implementation milestone once, auditable on disk.

    The original messages and source reads remain intact. A checkpoint is a
    user-level research workflow instruction, not an algorithm recommendation.
    """
    if any(m.get('role') == 'user' and IMPLEMENTATION_CHECKPOINT_MARKER in str(m.get('content', ''))
           for m in messages):
        return False
    checkpoint = (IMPLEMENTATION_CHECKPOINT_MARKER + '\n'
        'You have already inspected the baseline and explored the library APIs. '
        'For this next stage, please choose your own approach and aim to produce '
        'a concrete, testable circuit revision using write_circuit or edit_circuit, '
        'then call run_evaluator if the revision is ready. '
        'You may make targeted source reads needed for exact edits, but avoid '
        'restarting a broad API survey unless you find a specific blocker. '
        'You alone determine the algorithm and may replace the entire circuit. '
        'If you cannot safely implement a candidate, report the specific blocker '
        'rather than inventing a validation result. The budget is finite.')
    messages.append({'role': 'user', 'content': checkpoint})
    write_json(wb.session / 'conversation.json', messages)
    wb._log('research_checkpoint', {'name': 'first_testable_candidate',
                                     'content': checkpoint, 'automated': False})
    return True


def run_agent(wb: Workbench, *, model: str, max_calls: int, max_tokens: int, max_output_tokens: int,
              base_url: str, resumed: bool = False, max_context_chars: int = 42000,
              first_candidate_checkpoint: bool = False) -> dict[str, Any]:
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError('Missing OpenAI-compatible SDK; uv add openai') from e
    key = os.environ.get('DEEPSEEK_API_KEY')
    if not key:
        raise RuntimeError('DEEPSEEK_API_KEY not set; no API call attempted')
    client = OpenAI(api_key=key, base_url=base_url, timeout=180.0, max_retries=1)
    if resumed:
        messages = json.loads((wb.session / 'conversation.json').read_text())
        verify_conversation(messages)
        old_summary = wb.session / 'summary.json'
        if old_summary.is_file():
            old_model = json.loads(old_summary.read_text()).get('model')
            if old_model and old_model != model:
                raise ValueError(f'Model mismatch: session used {old_model}, requested {model}')
        usage = session_usage(wb.session)
        tokens, calls = usage['total_tokens'], usage['model_calls']
        if calls < max_calls and tokens < max_tokens:
            append_resume_status(wb, messages)
        if first_candidate_checkpoint:
            if calls >= max_calls or tokens >= max_tokens:
                raise ValueError('Increase cumulative call/token budgets before inserting a checkpoint; no changes made')
            add_implementation_checkpoint(wb, messages)
        wb._log('resume_started', {**usage, 'max_calls_total': max_calls,
                                   'max_tokens_total': max_tokens,
                                   'max_evals_total': wb.max_evals})
    else:
        messages: list[dict] = [
            {'role': 'system', 'content': system_prompt(wb.mode)},
            {'role': 'user', 'content': 'Task contract (excluding published discovered solution):\n\n'
             + task_brief(wb.repo) + '\n\n' + research_status_notice(wb)
             + '\n\nChoose your own algorithmic direction and work toward a concrete circuit revision and evaluator feedback. You may edit or test as soon as you have a plausible hypothesis; targeted source lookup remains available whenever needed.'},
        ]
        tokens, calls = 0, 0
    summary: dict[str, Any] = {}
    while calls < max_calls and tokens < max_tokens:
        api_messages, context = context_for_api(messages, max_context_chars)
        if context['source_reads_compacted']:
            wb._log('context_compacted', context)
        # Limits are cumulative ACROSS resumes and enforced BETWEEN calls.
        response = client.chat.completions.create(model=model, messages=api_messages,
            tools=tool_specs(), tool_choice='auto', max_tokens=max_output_tokens)
        calls += 1
        used = response.usage.total_tokens if response.usage else 0
        tokens += used
        choice = response.choices[0]
        msg = choice.message
        assistant_message: dict = {'role': 'assistant', 'content': msg.content}
        if msg.tool_calls:
            assistant_message['tool_calls'] = [tc.model_dump(exclude_none=True) for tc in msg.tool_calls]
        # DeepSeek Thinking Mode requires reasoning_content from assistant tool
        # turns to be included in subsequent chat completions requests.
        # OpenAI's SDK may expose this server-specific field as model_extra.
        reasoning = getattr(msg, 'reasoning_content', None)
        if reasoning is None:
            model_extra = getattr(msg, 'model_extra', None)
            if isinstance(model_extra, dict):
                reasoning = model_extra.get('reasoning_content')
        if isinstance(reasoning, str):
            assistant_message['reasoning_content'] = reasoning
        messages.append(assistant_message)
        usage = response.usage
        details = getattr(usage, 'completion_tokens_details', None) if usage else None
        wb._log('model_call', {'call': calls, 'total_tokens': used, 'cumulative_tokens': tokens,
                               'prompt_tokens': getattr(usage, 'prompt_tokens', None),
                               'completion_tokens': getattr(usage, 'completion_tokens', None),
                               'reasoning_tokens': getattr(details, 'reasoning_tokens', None),
                               'reasoning_chars_preserved': len(reasoning) if isinstance(reasoning, str) else None,
                               'finish_reason': choice.finish_reason,
                               'tool_names': [t.function.name for t in (msg.tool_calls or [])]})
        write_json(wb.session / 'conversation.json', messages)
        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or '{}')
                    if not isinstance(args, dict):
                        raise ValueError('Tool arguments must be object')
                    res = wb.execute(tc.function.name, args)
                except Exception as exc:
                    res = {'error': f'{type(exc).__name__}: {exc}'}
                payload = json.dumps(res, ensure_ascii=False)
                if len(payload) > MAX_TOOL_TEXT:
                    payload = payload[:MAX_TOOL_TEXT] + '\n[Truncated; use targeted read tools]'
                messages.append({'role': 'tool', 'tool_call_id': tc.id, 'content': payload})
            write_json(wb.session / 'conversation.json', messages)
            continue
        if choice.finish_reason == 'length':
            messages.append({'role': 'user', 'content': 'Output was truncated. Your prior workspace edits were preserved. '
                             'Continue with short tool calls; avoid reprinting the entire circuit in prose.'})
            continue
        summary = {'model_final': msg.content or '', 'finish_reason': choice.finish_reason}
        break
    result = {**summary, **wb.status(), 'model_calls': calls, 'total_tokens': tokens,
              'model': model, 'max_calls': max_calls, 'max_tokens': max_tokens,
              'budget_stopped': calls >= max_calls or tokens >= max_tokens}
    write_json(wb.session / 'summary.json', result)
    write_json(wb.session / 'conversation.json', messages)
    return result

def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo', type=Path, default=Path.home() / 'uncompute')
    ap.add_argument('--seed', type=Path, default=Path('seed/circuit.py'))
    ap.add_argument('--runs', type=Path, default=Path('runs/workbench'))
    ap.add_argument('--mode', choices=['seed', 'blank'], default='blank')
    ap.add_argument('--evaluator-module', default='evaluator')
    ap.add_argument('--max-evals', type=int, default=2)
    ap.add_argument('--max-calls', type=int, default=8)
    ap.add_argument('--max-tokens', type=int, default=45000)
    ap.add_argument('--max-output-tokens', type=int, default=8192)
    ap.add_argument('--model', default=os.environ.get('DEEPSEEK_MODEL', 'deepseek-v4-pro'))
    ap.add_argument('--base-url', default=os.environ.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com'))
    ap.add_argument('--self-test', action='store_true')
    ap.add_argument('--resume', type=Path, help='Existing v0.2/v0.3 session path; budgets are TOTAL including past usage')
    ap.add_argument('--inspect-only', action='store_true', help='Inspect existing session without API or Docker')
    ap.add_argument('--first-candidate-checkpoint', action='store_true',
                    help='On resume, append an auditable algorithm-neutral implementation milestone (once)')
    ap.add_argument('--max-context-chars', type=int, default=42000,
                    help='Compact older, already-consumed source reads in API context above this size')
    ap.add_argument('--integration-test', action='store_true',
                    help='Test seed through the real Docker evaluator once; no model API.')
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.self_test:
        from test_workbench import run_tests
        run_tests()
        return 0
    if args.inspect_only and args.resume is None:
        raise ValueError('--inspect-only requires --resume')
    if args.first_candidate_checkpoint and args.resume is None:
        raise ValueError('--first-candidate-checkpoint requires --resume')
    if args.first_candidate_checkpoint and args.inspect_only:
        raise ValueError('--first-candidate-checkpoint cannot be used with --inspect-only')
    if args.resume is not None and (args.integration_test or args.self_test):
        raise ValueError('--resume is incompatible with --integration-test or --self-test')
    if min(args.max_evals, args.max_calls, args.max_tokens, args.max_output_tokens, args.max_context_chars) < 1:
        raise ValueError('All budgets must be positive')
    if args.inspect_only:
        wb = Workbench.resume(session=args.resume, max_evals=args.max_evals)
        messages = json.loads((wb.session / 'conversation.json').read_text())
        verify_conversation(messages)
        usage = session_usage(wb.session)
        compacted = context_for_api(messages, args.max_context_chars)[1]
        print(json.dumps({'inspection': 'PASS', **wb.status(), **usage,
                          'mode': wb.mode, 'context_preview': compacted,
                          'additional_calls_available': max(0, args.max_calls - usage['model_calls']),
                          'additional_tokens_available_soft': max(0, args.max_tokens - usage['total_tokens'])},
                         ensure_ascii=False, indent=2))
        return 0
    # Resolve the evaluator BEFORE spending money. It must be the existing
    # Docker-wrapped evaluator on the GCP VM, not tools/eval.py directly.
    evaluator = _evaluator_callable(args.evaluator_module)
    if args.resume is not None:
        wb = Workbench.resume(session=args.resume, max_evals=args.max_evals, evaluate_fn=evaluator)
    else:
        wb = Workbench(repo=args.repo, seed=args.seed, runs=args.runs,
                       mode='seed' if args.integration_test else args.mode,
                       max_evals=1 if args.integration_test else args.max_evals, evaluate_fn=evaluator)
    if args.integration_test:
        checked = wb.run_evaluator()
        tq = checked.get('tq_cost')
        success = checked.get('valid') is True and tq == BASELINE_TQ
        print(json.dumps({'integration_test': 'PASS' if success else 'FAIL',
                          'expected_baseline_tq': BASELINE_TQ,
                          'result': checked, 'session': str(wb.session)},
                         ensure_ascii=False, indent=2))
        return 0 if success else 2
    try:
        result = run_agent(wb, model=args.model, max_calls=args.max_calls,
                           max_tokens=args.max_tokens, max_output_tokens=args.max_output_tokens,
                           base_url=args.base_url, resumed=args.resume is not None,
                           max_context_chars=args.max_context_chars,
                           first_candidate_checkpoint=args.first_candidate_checkpoint)
    except BaseException as exc:
        wb._log('agent_error', {'type': type(exc).__name__, 'detail': str(exc)})
        write_json(wb.session / 'interrupted.json', {'error': repr(exc), **wb.status()})
        print(f'Interrupted, work preserved in {wb.session}: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
