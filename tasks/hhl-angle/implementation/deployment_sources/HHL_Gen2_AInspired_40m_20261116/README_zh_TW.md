# HHL Gen2 — A 風格 Prompt，單獨 40 分鐘（2026-11-16）

## 這個實驗是什麼
- 新的獨立研究輪次，仍從有來源綁定證據的 Gen1 Parent (T=14444, Q=248, TQ=3582112) 起跑；**不把上一輪 430 ULP 超標的電路當合格父代**。
- 研究總牆鐘上限 2400 秒；正常 `stop` 後按同一 OpenCode Session ID 續研，最多 240 次呼叫。若故障、Session ID 不明或達到上限，會提前中止並標記，不冒稱完成。
- Prompt 保留原始 HHL-P7 動態產生的任務契約、`research_python` 的 `entry/files`、`scratch/runs` 相對路徑、Builder -> .ops -> 原生 grader、驗證來源綁定、可見研究日誌；另追加 A 的「先實作候選→評分→失敗修正→重驗」原則。完整追加文字見 `PROMPT_A_INSPIRED_APPENDIX.txt`，每輪完整 Prompt 由控制器保存成 `prompt.txt`。
- 特別指出 429 ULP 包含上限、430 是失敗，抽樣原生 OK 不等於全域正確，精確檢查需對齊 Builder 的逐列截斷語義。這是正確性條件，不指定優化演算法。
- 研究時間 2400 秒**不含**免費環境檢查、候選收據收集及獨立 replay；外層 systemd 安全期限 7200 秒，避免 40m 舊版在回收時限附近被截斷。
- 不覆蓋舊實驗、不寫正式 OpenEvolve DB、不修改 Coulomb、原生 evaluator 或信任的 grader；不能保證實際付費價格硬上限。

## VM 一鍵操作
將本次 ZIP 及對應 `HHL_Gen2_AInspired_40m_Detached.sh` 上傳至 `/home/researcher/`。

免費預檢（不呼叫模型）：

```
sudo bash /home/researcher/HHL_Gen2_AInspired_40m_Detached.sh --preflight
```

付費單輪（需要你本人明確執行，不含第二輪）：

```
sudo bash /home/researcher/HHL_Gen2_AInspired_40m_Detached.sh --authorize-paid-gen2
```

啟動會顯示 `GEN2_20M_DETACHED_UNIT=...`，再以確切 unit 查看：

```
sudo journalctl -u '<啟動時顯示的 unit 名稱>' -f --no-pager
```

`GEN2_RESEARCH_BUDGET_SECONDS=1200` 才是這次研究預算；C 原始免費 preflight 仍會印出 `HHL_MODEL_TIMEOUT_SECONDS=900`，不代表本次付費 run 是 900 秒。

## 結果與效度
根據日誌中的 `P7_GEN2_RECORD=...` 定位新建記錄。主要檔案：`prompt.txt`、`run-start.json`、`opencode-gen2.log`、`opencode-continue-XX.log`、`continuation-events.json`、`frozen-after-session/`、`agent-*-broker.json`、`agent-*-evaluator.json`、`replay-*`、`result.json`。若缺 `result.json`，視為**未完成收據回收／仍須調查**，不視為成功。分辨原生抽樣 OK、來源綁定重播和全域精度。

## 變更與範圍
源自 **已通過 VM 免費預檢**的 `HHL_Gen2_AInspired_20m_20261109.zip`；只延長同一套控制流程的研究時間。相對 20 分鐘版，僅調整研究期限 1200→2400 秒、延續呼叫上限 120→240、實驗識別文字。原 A-inspired 候選優先 Prompt、候選事件上限、Builder/Broker/原生 Evaluator/正式 DB 都不變。`launch_gen2.sh` 使用已通過免費預檢的 stdout+stderr Help 檢查，**不再使用會造成 SIGPIPE/管線誤判的 `grep -q`**。

## 尚未實測
本交付完成離線封包、雜湊、Shell/Python 語法與靜態約束檢查；VM 端須先免費預檢，無法由此環境直接觸發雲端付費實驗。

## 40 分鐘版驗收界線
- 預檢應顯示 `GEN2_RESEARCH_BUDGET_SECONDS=2400`、`GEN2_PROMPT_VARIANT=A_INSPIRED_HHL_CANDIDATE_FIRST_40M`，其餘 P5/P6/P7 免費 gate 仍應 PASS。
- 研究時間是上限；若 OpenCode 非正常結束、找不到 Session ID，仍可能提前停止；不保證整整 40 分鐘或費用硬上限。
- 是否取得可用 Gen2，只能從 `result.json`、Broker/Evaluator 事件與全域精度驗證判定，不能以耗時判定成功。
