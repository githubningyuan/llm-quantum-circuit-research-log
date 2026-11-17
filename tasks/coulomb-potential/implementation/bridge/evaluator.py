import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

from openevolve.evaluation_result import EvaluationResult

REPO = Path.home() / "uncompute"
TASK = REPO / "tasks/coulomb-potential"
SEED = Path.home() / "quantum-agent/seed"
REFERENCE_TQ = 1601460


def evaluate(program_path):
    container_name = "quantum-eval-" + uuid4().hex

    try:
        # 建立與原作者任務結構相容的暫存目錄
        with tempfile.TemporaryDirectory(
            prefix="_agent_", dir=TASK / "circuits"
        ) as tempdir:
            workdir = Path(tempdir)
            shutil.copytree(SEED, workdir, dirs_exist_ok=True)
            shutil.copy2(
                Path(program_path).resolve(),
                workdir / "circuit.py"
            )

            # 只調整候選副本：取消固定成本 pin，保留 ancilla leak guard
            circuit = workdir / "circuit.py"
            code = circuit.read_text()
            pin = "    assert_metrics(b, __file__)"
            if code.count(pin) != 1:
                raise ValueError("Expected exactly one assert_metrics call")

            leak_guard = """    from pathlib import Path as _Path
    import json as _json
    from uncompute.compact import Regs as _Regs, WKIND_QUBIT as _WKIND_QUBIT
    _meta = _json.loads((_Path(__file__).resolve().parent / "meta.json").read_text())
    _resident = sum(band.get("size", 0) for band in _meta.get("registers", []))
    _io = sum(len(wires) for kind, wires in _Regs(b._registers).registers
              if kind == _WKIND_QUBIT) + _resident
    assert b.active_qubits == _io, (
        f"ancilla leak: {b.active_qubits} wires live, registers hold {_io}"
    )"""
            circuit.write_text(code.replace(pin, leak_guard))

            # 允許容器內的非 root 使用者讀取電路
            workdir.chmod(0o755)
            (workdir / "circuit.py").chmod(0o644)

            circuit_in_docker = (
                "/app/uncompute/"
                + (workdir / "circuit.py").relative_to(REPO).as_posix()
            )

            command = [
                "sudo", "-n", "docker", "run",
                "--rm",
                "--name", container_name,
                "--network", "none",
                "--read-only",
                "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges",
                "--user", "65534:65534",
                "--cpus", "1",
                "--memory", "2g",
                "--pids-limit", "128",
                "--tmpfs", "/tmp:rw,nosuid,nodev,size=512m,mode=1777",
                "-e", "HOME=/tmp",
                "-e", "PYTHONDONTWRITEBYTECODE=1",
                "-v", f"{REPO}:/app/uncompute:ro",
                "quantum-eval-base",
                "python", "tools/eval.py",
                "coulomb-potential",
                "--circuit", circuit_in_docker,
                "--json",
                "--jobs", "2",
                "--timeout", "60",
            ]

            process = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=90,
            )

            if process.returncode != 0:
                raise RuntimeError(
                    process.stderr[-2000:] or process.stdout[-2000:]
                )

            report = json.loads(process.stdout)

        if report["verdict"] != "OK":
            raise ValueError(str(report.get("fail")))

        metrics = report["metrics"]
        tq = float(metrics["tq_cost"])

        if tq <= 0:
            raise ValueError("Invalid TQ cost")

        return EvaluationResult(metrics={
            "combined_score": REFERENCE_TQ / tq,
            "valid": 1.0,
            "toffoli": float(metrics["toffoli"]),
            "qubits": float(metrics["qubits"]),
            "tq_cost": tq,
        })

    except Exception as e:
        return EvaluationResult(
            metrics={"combined_score": 0.0, "valid": 0.0},
            artifacts={"error": str(e)},
        )

    finally:
        # 超時或異常時，也盡量清除可能殘留的容器
        try:
            subprocess.run(
                ["sudo", "-n", "docker", "rm", "-f", container_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        except Exception:
            pass
