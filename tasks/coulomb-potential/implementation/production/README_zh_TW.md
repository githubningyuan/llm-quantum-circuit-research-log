# v6.2：OpenCode 模型超時仍能保留已通過驗證的候選

## 問題證據

實際 GCP 第三輪收到：

`STOP: TimeoutError: Child process exceeded 1199s; process group terminated`

v6.1 的模型 `run_command` 在超時時會清理整個 OpenCode 子程序群組；`paid_step` 也會透過 finally 嘗試恢復原本最佳工作電路並寫入 summary.json，但拋出的 TimeoutError 會略過 snapshot_codes、independent_eval 和資料庫回存。超時前若有有效新電路，會錯失儲存機會。

## v6.2 變更

* **只修改新增的 openevolve_opencode_loop.py**：捕捉模型階段 TimeoutError，記錄 `model_timeout` 與 `model_time_budget_exhausted`，但不宣稱模型正常完成。
* 清理模型程序後仍解析原始橋接評估的已保存快照；**只對新的 SHA256** 使用**原始量子評估橋接做獨立複驗**；通過才回存 MAP-Elites。
* 超時時不嘗試依賴中斷 Session 的 finish 狀態，也不額外呼叫 DeepSeek；執行最後復原流程，仍驗證最佳工作電路 SHA256。
* 超時且沒有新候選：`model_timeout_no_new_offspring`；超時但有複驗通過子代：`model_timeout_with_verified_offspring`。兩者都不是研究完成的宣稱。
* `last-ungraded-candidate.py` 僅備份可能未評估的最終工作檔，**永遠不直接回存**。
* 不更動 original evaluator、OpenCode 的 config、安全權限、父代抽樣、官方 MAP-Elites 的 complexity/diversity、T×Q 評分。

## 本地測試

`python3 -B -m unittest discover -p 'test_*.py' -q`

**50 項離線測試通過**。新測試包括模型超時沒有候選、超時時保存有效新快照並獨立複驗及回存、超時時無效快照拒收與最佳電路恢復；原有 subprocess timeout group cleanup、OpenCode 1.19.4 匯出格式測試保留。

**未在實際 GCP OpenEvolve 0.4.0 上完成 v6.2 原生測試，不能聲稱實機已通過。**

## 先調查剛才超時的一輪（完全唯讀、0 API）

在 GCP 第二 SSH 執行：

```bash
python3 - <<'PY'
import json, subprocess
from pathlib import Path
root=Path.home()/"quantum-agent/runs/openevolve-opencode"
runs=sorted(x for x in root.iterdir() if x.is_dir() and (x/"summary.json").is_file())
if not runs: raise SystemExit("No run summaries")
p=runs[-1]; report=json.loads((p/"summary.json").read_text())
expected="d242f66d35d7fc8b78dfe5a1e76178ca155f6c575df78e1e53ed5062d7157625"
import hashlib
candidate=Path.home()/"opencode-lab/quantum-trial/circuit.py"
actual=hashlib.sha256(candidate.read_bytes()).hexdigest() if candidate.is_file() else "MISSING"
print(json.dumps({"latest_run":p.name,"summary":report,"best_worktree_sha256":actual,
    "best_sha_matches":actual==expected,
    "active_json_exists":(root/"active.json").exists()},indent=2,ensure_ascii=False))
print("OpenCode process list (empty is expected):")
subprocess.run(["pgrep","-af","opencode run"],check=False)
print("Recent bridge snapshots:")
archive=Path.home()/"quantum-agent/runs/opencode_evaluations"
for folder in sorted((x for x in archive.iterdir() if x.is_dir() and x.name>=p.name[:16]),reverse=True)[:15]:
    q=folder/"result.json"
    if q.is_file():
        data=json.loads(q.read_text()); print(folder.name,"valid=",data.get("valid"),"sha256=",data.get("sha256"),"metrics=",data.get("metrics"))
PY
```

**若 best SHA 不一致，或 active.json 仍存在，不要啟動任何新 paid-step，也不要自行覆蓋工作電路。** 先確認沒有 OpenCode 子程序，然後回報 summary，判斷要不要使用既有 `--recover` 恢復。

## GCP 免費新版本驗收（另存到新資料夾，避免覆蓋當前程式）

上傳 `openevolve_timeout_recovery_v6_2.zip` 至 GCP 家目錄：

```bash
mkdir -p ~/openevolve-timeout-guard-v6-2
python3 -m zipfile -e ~/openevolve_timeout_recovery_v6_2.zip ~/openevolve-timeout-guard-v6-2
cd ~/openevolve-timeout-guard-v6-2/openevolve_timeout_recovery_v6_2
env -u DEEPSEEK_API_KEY -u OPENAI_API_KEY \
  ~/openevolve-native-check/venv/bin/python -B -m unittest discover -p 'test_*.py' -q
env -u DEEPSEEK_API_KEY -u OPENAI_API_KEY \
  ~/openevolve-native-check/venv/bin/python -B native_length_guard_gate.py
```

測試不會呼叫真實 DeepSeek。**不要把 v6.2 驗收視為新一輪付費演化的授權。**

## 歷史超時輪次如何處理

這個修正只能改變往後的超時處理。**前一輪是否有可複驗的新快照，必須先調查其實際 archive；本版不自動回溯匯入舊輪次，不應推定已有新成果。**
