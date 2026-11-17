#!/usr/bin/env python3
"""Fixed-path bridge: OpenCode candidate -> existing isolated evaluator.

Installed in ~/quantum-agent, not in the model-editable project.
No candidate code is executed on the host: Python AST instrumentation only;
execution is by existing evaluator.py inside the user's restricted Docker.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path

from deepseek_workbench import prepare_evaluator_input, _norm_eval_result
from evaluator import evaluate

HOME = Path.home()
CANDIDATE = HOME / "opencode-lab/quantum-trial/circuit.py"
ARCHIVE = HOME / "quantum-agent/runs/opencode_evaluations"
MAX_BYTES = 400_000
BASELINE_TQ = 1_601_460


def main() -> None:
    begin = time.monotonic()
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    folder = ARCHIVE / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:8]}"
    folder.mkdir(mode=0o700)
    result: dict = {"valid": False}
    try:
        if CANDIDATE.is_symlink() or not CANDIDATE.is_file():
            raise ValueError("Candidate must be an ordinary file at the fixed path")
        source_bytes = CANDIDATE.read_bytes()
        if len(source_bytes) > MAX_BYTES:
            raise ValueError(f"Candidate exceeds {MAX_BYTES} bytes")
        source = source_bytes.decode("utf-8")
        (folder / "circuit.py").write_bytes(source_bytes)
        # The legacy evaluator requires one assert_metrics marker. The helper
        # inserts it into a temporary copy while suppressing the old cost pin.
        # Add a shim import so entirely rewritten candidates also work.
        instrumented = prepare_evaluator_input(source)
        needle = "from uncompute.builder import Builder as _WBBuilder"
        if instrumented.count(needle) != 1:
            raise ValueError("Unexpected evaluator instrumentation format")
        instrumented = instrumented.replace(
            needle, "from uncompute.testing import assert_metrics\n" + needle, 1
        )
        (folder / "evaluator_input.py").write_text(instrumented)
        normalized = _norm_eval_result(evaluate(str(folder / "evaluator_input.py")))
        result = {
            "valid": normalized["valid"],
            "metrics": normalized.get("metrics", {}),
            "artifacts": normalized.get("artifacts", {}),
        }
        if normalized.get("error"):
            result["error"] = normalized["error"]
        if normalized.get("valid") and normalized.get("tq_cost"):
            result["improvement_pct"] = round(
                100 * (1 - normalized["tq_cost"] / BASELINE_TQ), 5
            )
        result["sha256"] = hashlib.sha256(source_bytes).hexdigest()
    except Exception as exc:
        result = {"valid": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        result["elapsed_s"] = round(time.monotonic() - begin, 3)
        result["snapshot"] = str(folder)
        (folder / "result.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False, default=str) + "\n"
        )
        # Tool output is deliberately bounded to keep the LLM context concise.
        if result.get("artifacts"):
            result["artifacts"] = {k: str(v)[-1800:] for k, v in result["artifacts"].items()}
        print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
