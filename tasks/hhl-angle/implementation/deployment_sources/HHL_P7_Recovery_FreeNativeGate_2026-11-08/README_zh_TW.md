# HHL P5：正式 gen0 Parent → C 原生 P1 來源綁定 → 真實 gen1 Session

## 定位與完成度

這份單包以 **C 原版 one_session 的 Session/逾時回收**、**C 的 rootless Podman/Builder**、**P1 在 VM 已通過的兩個隔離 systemd 原生服務**、**P4 正式 gen0 原生 OpenEvolve DB** 為核心。沒有重新設計 Broker 或演化控制器，**不修改 C 正式部署檔與 Coulomb DB79/88/117**。

**目前只通過離線靜態／契約測試，尚未在 VM 驗收本 P5 封包。** 不要把 P1／P4 的既有 PASS 誤認為 P5 已正式通過。

### 免費模式 `--preflight`

1. 呼叫 C 的原版 `hhl_first_one_session.sh --preflight`。
2. 以 P1 R2 已驗證原始碼啟動獨立、暫時性 Broker 和 Native Evaluator，保留既有 Event Spool。
3. 透過 A 原生 OpenEvolve 0.4.0，從 **P4 真正式 DB** 抽取完整 14 檔 gen0 Parent，重設到新的 C Scratch Attempt。
4. 以 C 已驗證的 Builder 和受限 Podman 建構 Parent，**實際**呼叫 P1 來源綁定原生評分、核對兩份 root-only spool。
5. 在 hhl-agent 身分執行 `opencode debug config`，核對有效權限與受限工具設定；這**不等於**已證明同名工具覆蓋優先順序，因此第一次付費 Session 必須以 P1 原生來源事件作為最後驗收。
6. 自動停止暫時 Socket、刪除臨時 root-controlled 服務／配置，保留 private receipt 與已 staging 的 Candidate（**只是一份 Parent 複製，不算新子代**）。

預期 `P5_FORMAL_PARENT_TO_NATIVE_SOURCE_BOUND_CANARY=PASS`、`P5_TEMP_SERVICES_CLEANUP=PASS`、`P5_PAID_GEN1_NOT_STARTED=YES`。只做免費預檢，沒有供應商 API 花費，不寫入正式 DB。

### 真模型模式 `--paid --authorize-paid-gen1` （**須使用者明確授權後**）

在上述免費模式驗收通過且你批准 API 費用之後，同一包支援單次 900 秒 DeepSeek gen1 Session。於 VM TTY **隱藏輸入** API Key，不記錄在檔案／命令列／聊天中。使用 C 已成功的 Prompt/Agent/原生 Builder/Python 以及 P1 綁定工具的隔離 overlay；即使模型超時，仍從兩個受保護 Spool 收集已評分來源，嘗試針對最多五組有變更的候選完成兩次獨立重播。只保存可信 P1 回條；**不自動更動正式 HHL DB**。

**成本限制：** 900 秒是 wall-time 上限，不是 API 供應商的硬性 USD 上限。首次付費可能因 OpenCode 1.19.4 的工具覆蓋順序而無法產生 P1 事件，屆時嚴格標記為未達標，不能冒充已驗證子代。

### 仍未完成

- gen1 成果通過雙重重播之後，仍須建立 **gen1 入庫＋gen2 原生 DB 抽樣**，才算真正多代演化。
- 第三輪 2026-11-06 評分的歷史來源歸屬始終為 `NOT_PROVEN`；P1/P5 新回條不能追溯變更該歷史事實。
- P5 的第一輪 OpenCode 1.19.4 工具 overlay 實際載入尚未經付費模型實測。

## 安全要點

- 不碰 C 的線上 Broker/Evaluator/Tool；所有改動只在 `/run/hhl-task2/p1-review-code/`、新的 P5 root-only receipt 及 HHL Scratch 新 run。
- Reuse P1 原版 Unit、Runner UID/GID、Podman、native Evaluator，只修改暫時 client 的固定 Socket 指向。
- Agent 的 `quantum_evaluate` 必須同時提供 `broker_run_id` 和 `broker_artifact`；root 仍會獨立核對原始 Spool，絕不信任模型自述。
- 原始 P1 Spool 永不刪除／覆寫，Coulomb 資產永不觸碰。失敗就停止，不放寬權限或改寫已驗證依賴。
- 接受新 Parent 的複製絕不等於新發現；不能用路徑改變製造假 offspring。

## VM 執行

只需將本 ZIP 上傳到 `/home/researcher/`，使用交付訊息提供的 **固定 SHA256** 在 root 私有目錄驗證後解壓，執行 `run_vm_once.sh --preflight`。**不要在未授權的情況下啟用 `--paid`。**

如果 P5 專屬的檔案、服務、正式 DB 身分或環境發生漂移，程式會停止，保留用來診斷的原始證據，而非自行修復。


## R3：有效權限解析的最小修正（2026-11-08）

R2 的 Node.js ESM 問題已修復，但 VM 報 `P5_STOP=Stop:OpenCode live permission contract is not fail-closed`。
R3 只調整 `gen1_canary.py` 的權限比對：同時接受 `deny` 與精確的 `{"*":"deny"}`，但逐項比對所有 C 原定義的關鍵權限及 build-agent 覆寫，不能因值不同就放行。若真有權限漂移，輸出 `P5_EFFECTIVE_PERMISSION_DIAGNOSTIC`，不打印整份 `debug config`（其中可能有機密）。

仍然只執行 `--preflight`，不得自動啟動付費模式；不修改 C 的原部署。


## R4：修正 OpenCode 設定載入優先順序（2026-11-08）

R3 VM 診斷 `read` 2 規則、`edit` 5 規則、`quantum_evaluate` 缺失，精確吻合 C 原本專案的基礎權限，不是 P5 產生的 `ALLOWED_READ`／`ALLOWED_EDIT`。
根據 OpenCode 設定來源優先序，專案 `opencode.json` 可覆蓋 `OPENCODE_CONFIG` 的值。

R4 **只修改 `run_vm_once.sh`**：將 C 原有釘選政策產生的**同一份** `/run/hhl-task2/hhl-v06-opencode.json`，以 root:root 0644 逐 bytes 原封不動放進 P5 已存在的 `OPENCODE_CONFIG_DIR/tools-overlay/opencode.json`，並 `cmp` 比對；禁止原專案檔案或 P1 核心服務的修改。`opencode debug config` 仍會依 R3 的逐項 fail-closed 比對驗證實際結果。任何落差停止，不放寬權限。保留 preflight 零模型呼叫、清理與舊證據。

**未知：** OpenCode 1.19.4 實際載入行為必須 VM 驗收，離線測試不能替代。本 R4 封包未批准任何付費 Session。成功後才可討論單次有預算上限的 gen1 模型實驗。


## R5：修正 OpenCode 深度合併後殘留的廣泛允許規則（2026-11-08）

R4 VM 的回報：`read` 25 條（P5 僅允許 24 條）、`edit` 8 條（P5 僅允許 4 條），但其他工具權限沒有報錯。這**恰好等於** C 原專案設定額外的 1 條廣泛 Read allow、4 條廣泛 Edit allow 被 OpenCode 配置合併保留。R4 的 fail-closed 檢查阻止了可能擴大存取權限的設定，這是正確結果。

R5 **沒有放寬比較**。原 P5 runtime config 保留原樣以符合 C 的已有 Session contract；僅在隔離 `OPENCODE_CONFIG_DIR` 中，從 C **實際釘選的** `ORIGINAL_READ/EDIT` 與 `ALLOWED_READ/EDIT` 計算強化配置：把既有廣泛 allow 逐條改為 deny，之後保留受限允許規則。沒有改動原專案 `opencode.json`、原 Broker、Evaluator、正式 DB 或服務範本。

OpenCode 原則為最後符合的規則生效；R5 對有效 `debug config` 的規則內容與典型「允許工作檔／禁止根設定及敏感檔」路徑進行逐項比對。**只有最終顯示 `P5_OPENCODE_1_19_4_EFFECTIVE_CONFIG=PASS`、`P5_FORMAL_PARENT_TO_NATIVE_SOURCE_BOUND_CANARY=PASS` 才算 VM 驗收通過**。`P5_LEGACY_ALLOW_REVOCATION_CONFIG_PREPARED=YES` 僅代表已在暫時目錄建立限制配置，並不表示已驗證 OpenCode 真正套用。

此版仍只先做 `--preflight`。付費測試不在此次授權內；不能因離線測試通過而跳過 live config 及來源證據檢查。


## R6：修復驗收設定檔建立模式（2026-11-08）

R5 VM 實際回報 `P5_STOP=P5 overlay config permission drift`，發生在啟動 P1 Socket 及 Parent canary 前。原因是啟動腳本先設 `umask 077`，兩段 Python `os.open(..., 0o644)` 未呼叫 `fchmod`，實際建立成 `0600`；其中 Overlay 還會要求 `root:root 0644`。這不是 C 的權限架構問題。

R6 對 **兩個**新建的設定檔（`/run/hhl-task2/hhl-v06-opencode.json` 與獨立 overlay 的 `opencode.json`）採用 C 原版成功使用的 `os.fchmod(fd, 0o644)`，保留 R5 逐條撤銷過寬 allow 規則的 fail-closed 檢查。其餘 Broker、Evaluator、Podman、DB、Prompt、Model 控制及 C 原檔一律不變。

R6 離線檢查包含在 `umask 077` 下實際建立兩份檔案、權限與策略內容查核，以及原有 P5 契約測試；仍需 VM 的 `--preflight` 驗收。**未授權付費 Session。**


## R7：有序規則與 Build Agent 權限的最小修正（2026-11-08）

R6 VM 結果：`edit.allowed-path-probe=not_allow`、`read.allowed-path-probe=not_allow`，但規則值與預期完全一致。離線重現證明：Python 對兩份具有相同 key/value、但 key 順序不同的映射判定相等；OpenCode 的最後匹配規則卻可能令舊廣域 deny 蓋過合法路徑。**不能省略這項檢查或改成全域 allow。**

R7 保留 R6 的全域 fail-closed 撤銷表，不修改現有 C 專案或執行核心。僅在隔離 overlay 的 `agent.build.permission` 中補入**精確相同**的 C 已核准 `ALLOWED_READ` / `ALLOWED_EDIT` 規則。OpenCode 以 agent 規則後套用覆蓋全域規則；只有 build Agent 能在允許的 HHL 工作區與文件上執行。預檢會同時驗證：全域不得有過寬允許、build-agent 的映射精確等於 C 窄政策、有效允許及拒絕範例、其他危險工具維持 deny。若 OpenCode 1.19.4 合併或相容性不同，停止並印出不含機密的診斷。

本包不包含任何 API 金鑰，也**不會自動付費**。本次僅授權 `--preflight`，不修改正式 DB。R7 VM 驗收通過以前，不能宣稱 HHL gen1 已可運行。

### R7 額外發現：讀取敏感檔案的規則順序

在建立設定檔時，不可對含 `permission.read`/`permission.edit` 規則的 JSON 使用 `sort_keys=True`，否則 `.env` 的 deny 可能排在較廣的允許規則之前。R7 的兩處 JSON writer 均使用 `sort_keys=False` 保留 C 已核准的規則順序，並新增 `.env` 在 `scratch/runs/...` 底下的拒絕測試。已知 R6 直接以 Python 字典相等判斷，無法發現順序改變。本次離線測試只能驗證生成檔案與路徑規則，不等於 OpenCode 1.19.4 的實機驗收。

## R7 Keyfile Launch：沿用 A／C 的 VM 既有金鑰（2026-11-08）

這是已實機通過的 **R7** 的單點付費入口修訂；不變更 OpenCode／Broker／Evaluator／Podman／Prompt／gen1_paid.py，**不稱為新一輪架構改版**。

原 R7 的 `--paid` 無條件詢問 key；這一處改為在免費 Canary 全部通過後，由 root 從 `/home/researcher/.config/quantum-agent/deepseek.key` 讀取。此檔是 A 原始 Batch v3 README 及 C 交接均記錄的既有 VM key，**封包裡沒有金鑰**。拒絕 symbolic link、非 0600、非 root/指定 VM 使用者擁有者、hard link >1 或不合理長度／格式。金鑰僅傳入付費子程序的環境變數；不在命令列、Shell 歷史、標準輸出、ZIP、source manifest 中記錄。

保持 `--preflight` 完全零憑證讀取、零模型呼叫；`--paid --authorize-paid-gen1` 仍須兩個明確旗標，不會自動續跑。原生來源綁定及正式 DB 零寫入的結果判定不變。

**證據範圍：** 這次離線測試驗證差異／語法／契約，不代表改後的付費執行已在 VM 驗證。R7 的 VM 免費 PASS 仍是既有證據；下次付費執行時仍會再次執行完整免費 canary。


## P5 R7 module-resolution-only compatibility repair

Only the `run_vm_once.sh` bootstrap changes. The already verified P5 R7 tools,
Broker, Evaluator, parent, DB and permissions are byte-for-byte retained.
OpenCode/Bun resolves imports relative to actual tool file location. This patch
adds a root-owned `node_modules` symlink inside the temporary overlay to C's
existing protected `agent/.opencode/node_modules`, and checks both tool
modules using `hhl-agent` and Node ESM before any model call. No npm install,
no package downloads, no production sources or formal DB modified (the standard canary writes its
normal root-only audit record). It is a compatibility
hypothesis until proven on VM.

Run `--preflight` first and confirm `P5_BOTH_TOOLS_ESM_MODULE_IMPORT=PASS`,
`P5_OPENCODE_1_19_4_EFFECTIVE_CONFIG=PASS`, and
`P5_FORMAL_PARENT_TO_NATIVE_SOURCE_BOUND_CANARY=PASS`. Paid run not automatic.

The Node probe is not equivalent to OpenCode/Bun tool registry verification.


## P7（2026-11-08）：C 研究循環最小修正；未在 VM 驗證

本版**只調整** `clients/p5_broker_client.py`、`tools/research_python.js`
與兩段 Prompt Adapter，其他 Broker、Evaluator、socket、P1 封包、正式 DB、
OpenCode 權限及啟動器均繼承已通過免費驗收的 R7 + C dependency bridge。
研究用 `research_python.js` 在每次 Broker 真正成功產生 `.ops` 後，
以已返回的受保護 Broker receipt ID、實際輸出 artifact name，自動呼叫
**原本 P1 原生 Evaluator client**。Agent 只讀取回傳的
`auto_native_evaluations` native TQ/有效性並據此修改；不再需要自行整理
Broker ID 才能評分。為避免單次呼叫過長，每次成功建構最多自動評分一個
`.ops`。只有 `--auto-native-evaluate` 明確出現才自動提交；
P5 免費 gen0 canary 不帶該參數，避免雙重評分。

這不是繞過 P1：native event 仍記錄 `UNVERIFIED_BROKER_CLAIM`，由獨立
root intake 讀取 Broker + Evaluator 各自的 root-private spool，核對**實際 bytes**、
再做兩次原生獨立重播。無法對應時，結果不能入庫。任何 Broker failure、
無 `.ops`、無 receipt、評分服務拒絕均不會標成已驗證成果。

**執行前仍有阻塞**：本輪上一個 P5 回報 `P5_CLEANUP_STOP=orphan_container`，
不得直接重跑 preflight/paid；先保全來源與回條，查明 Podman 容器狀態
並人工授權精準清理。P7 只經本地離線測試，不代表 VM live auto-grade PASS。
不得在未另獲明確付費授權時執行 `--paid`。


## P7 單次 VM 收尾及免費實測 Gate（2026-11-08）

與 P7 研究程式相比，**唯一額外改動 `gen1_canary.py`**：成功重播
已核准的 gen0 Parent 後，再透過 `p5_broker_client.py --auto-native-evaluate`
實際產生 `candidate.ops`、由 P7 自動提交 P1 原生評分、讀取受保護的
Broker / Evaluator 兩份原始回條，核對相同 Parent SHA 與 TQ=4,169,620。
`P7_FREE_AUTO_NATIVE_SOURCE_BOUND=PASS` 才證明自動評分路徑在 VM 有效。
這是 gen0 連線驗收，**不是找到 gen1**，也不代表 OpenCode 真 Agent 已使用該工具。

`run_vm_safe_free_gate.sh` 會先把之前 P5 `jjl9nc5n` 根目錄保存成 root-only
tar+SHA，再檢查 HHL rootless 容器；**只有完全確認已退出**、名稱/標籤
與內核狀態一致的舊容器才容許在保存 metadata 後精準 `podman rm`（無 `-f`）。
任何仍在執行、未知或不符的容器都 STOP；從不刪 Scratch 或 spool。
最後執行 `run_vm_once.sh --preflight`，零 API、零正式 DB 寫入。
此腳本不是 Paid Launcher，沒有付費旗標。
