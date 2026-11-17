# Coulomb 批次控制器 v3：有證據的分支進展判定＋研究紀錄

**僅支援現有 Coulomb 專案。原有經 GCP 驗證的 v6.2、OpenEvolve 0.4.0／`complexity,diversity`、DeepSeek V4 Pro `low`、OpenCode 原權限、原始量子評估器、資料庫與保護的最佳工作電路均未修改。**

這版只改**外層批次控制器**與**唯讀報表**，不在 GCP 自行啟動模型、不植入提示詞。經先前 GCP v2 實測：最後 17 個候選、全域最佳 T×Q 577,700；新的 v3 **仍須進行免費實機驗收**。

## 改進重點

1. 新增**可選** `--max-no-progress N`：連續 N 輪「既沒有全域最佳改善，又沒有**真正留存在重載 MAP-Elites DB** 的新子代比當輪父代改善至少 X%」才停止。`--min-parent-improvement-pct X` 預設 **1.0%**，必須介於 0 和 100 之間；1% 是試驗用管理閾值、不是物理正確性容差。已實測的 v2 演化分支 **821,755 → 642,675 → 593,553** 的兩輪均有足夠改善，因此 v3 不會因全域最佳未刷新而在第二輪停止。
2. **不把新 SHA 當成進展**：檢查子代必須由 v6.2 獨立量子複驗後寫入、且在原本就要做的 DB 重載中 `db.get(sha)` 仍找得到，才可計算其父代改善。這是在既有 postflight 上加最多數次 DB hash 查詢，**不額外重新載入 DB、不另行呼叫 evaluator／DeepSeek**。保留數量與 `returned_to_database` 計數分開記錄。
3. **保留舊功能與安全檢查**：`--max-empty N`（無新提交子代）、v2 `--max-no-best N`（全域最佳無改善）都還在，後兩者互斥以避免意外提前停止。新的進展判斷**預設不啟用**。付費仍要求 `--run --ack-paid --rounds N`，最多 10 輪，且沒有強制美元上限。
4. **跨輪研究證據**：保存父代／子代 SHA、父代改善率、保留子代數、可確認的上一輪子代重用（比對 SHA，不再只憑 T×Q 恰好相同）。`coulomb_report.py` 唯讀讀取 v1/v2/v3 報表。僅在事件發生時寫入 `events.jsonl`。
5. **研究心得整理**：請讀 `RESEARCH_NOTES_zh_TW.md`。分開標示 GCP 事實、研究假說、已落實及未落實功能；**這份檔案沒有自動被 DeepSeek 讀取，不可稱為已部署研究記憶**。

## 1. 上傳後免費驗收（零模型呼叫）

先確認沒有 v1／v2／v3 其他活躍批次，再將 `coulomb_batch_runner_v3.zip` 上傳至 VM 家目錄。

```bash
mkdir -p ~/coulomb-batch-v3
python3 -m zipfile -e ~/coulomb_batch_runner_v3.zip ~/coulomb-batch-v3
cd ~/coulomb-batch-v3/coulomb_batch_runner_v3

env -u DEEPSEEK_API_KEY -u OPENAI_API_KEY \
  ~/openevolve-native-check/venv/bin/python -B coulomb_batch.py --check

python3 -B coulomb_report.py
python3 -B -m unittest discover -p 'test_*.py' -q
```

預期 `COULOMB_BATCH_PREFLIGHT_PASS`、**至少 17 候選**（這是上一次使用者 GCP 實測數，不是硬性 v3 門檻）、全域最佳 **577,700 或更優**、工作區 SHA256 = `d242f66d35d7fc8b78dfe5a1e76178ca155f6c575df78e1e53ed5062d7157625`、沒有 `active.json`／殘留 OpenCode。任何一項失敗就**不要付費執行**。

`coulomb_report.py` 會顯示上一批 v2 的兩輪 T×Q 與改善比例，並以原始 SHA 做父子代血緣檢查；若舊報表缺少 parent SHA 則顯示 `null`，**不會亂猜**。

## 2. 日後明確決定要做小規模付費演化時

**以下會呼叫 DeepSeek 產生費用；不必在免費檢查時執行。**

```bash
export DEEPSEEK_API_KEY="$(cat ~/.config/quantum-agent/deepseek.key)"
export OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX=65536
export OPENCODE_CONFIG_CONTENT='{"provider":{"deepseek":{"models":{"deepseek-v4-pro":{"options":{"reasoningEffort":"low"}}}}}}'

cd ~/coulomb-batch-v3/coulomb_batch_runner_v3
~/openevolve-native-check/venv/bin/python -B coulomb_batch.py \
  --run --ack-paid --rounds 3 \
  --max-empty 2 --max-no-progress 2 \
  --min-parent-improvement-pct 1.0 \
  --timeout 1200 --max-grades 8 --max-continuations 1
```

- 這仍然**每輪只讓 v6.2 執行一次付費模型 Session**。v6.2 原有 timeout 快照回收、獨立複驗、DB 回存與工作區恢復機制保持不變。
- 當第 1、2 輪都改善父代且成功保留至少 1% 時，`--max-no-progress 2` **不會誤判成停滯**；但仍受 `--rounds 3` 限制。
- 如果只是新 SHA、改善父代不到 1%、或子代重載後不在 DB 裡，則**不**重設進展計數。
- 若父代 T×Q 無法確認，子代不能以「父代改善」計入，只有全域最佳改善才能重置計數。
- `--max-empty` 是另個獨立停止條件；這版維持 v2 既有定義（沒有向 DB 提交新子代）。
- 兩個互斥停滯參數 `--max-no-best`／`--max-no-progress` 請**不要一起使用**。
- **沒有硬性美元限額**。原始控制器執行 20 分鐘模型階段後仍可能進行獨立複驗；VM 與 DeepSeek 費用須另行記帳。

## 3. 極輕量監控與無費用研究報表

```bash
BATCH_ROOT="$HOME/quantum-agent/runs/coulomb-batches"
LATEST=$(find "$BATCH_ROOT" -mindepth 1 -maxdepth 1 -type d -name '20*' | sort | tail -1)
tail -n 15 -F "$LATEST/events.jsonl"

python3 -B ~/coulomb-batch-v3/coulomb_batch_runner_v3/coulomb_report.py
```

`events.jsonl` **只在每輪開始／結束追加一行**，不輪詢進行中的 DeepSeek Session。想觀察模型 `read/edit/quantum_evaluate`，另外唯讀 `tail -F` 原 v6.2 的 `opencode.log`。

## 4. 已知限制與研究政策

- **新 v3 尚未在 GCP 付費跑過**；離線測試及 v6.2 原版測試不可取代雲端驗收。
- 「改善父代」只是度量進展的**搜尋成本規則**，不是全域最佳進步；該分支的改善仍可能劣於 577,700。
- **尚未實作**將歷史發現作為 DeepSeek 的跨 Session 研究記憶。僅寫研究筆記、紀錄 SHA 與 T×Q，不會自動提高模型能力。
- 未加入 Subagent，未改 `random_seed=42`、`num_inspirations=0`、推理強度 `low`、官方特徵維度或已成功的原始 Prompt。這些若要改，需隔離 A/B 實驗與預算記錄。
- 舊成本快照 **不代表目前帳單**。不可宣稱已測得原作者的 API／VM 費用。
