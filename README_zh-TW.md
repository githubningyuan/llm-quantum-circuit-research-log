# 跨任務量子電路演化研究

以 DeepSeek、OpenCode 與 OpenEvolve 0.4.0，獨立重現並改良論文中的 Coulomb potential 與 HHL angle 數值電路。研究於 2026-11-17 收尾，指標為實際 Toffoli 數 T × 峰值 qubit 數 Q。

| 任務 | 論文 baseline | 本研究 T / Q | 本研究最佳 TQ | 相對 baseline | 論文最佳 |
|---|---:|---:|---:|---:|---:|
| Coulomb | 1,601,460 | 1,955 / 177 | 346,035 | 4.63× | 161,880 |
| HHL | 4,940,400 | 2,839 / 168 | 476,952 | 10.36× | 155,020 |

兩個最佳電路均已從 VM 匯出來源重新 build，並經共用原生 evaluator 再評分通過；HHL OPS 雜湊更與歷史記錄完全一致。HHL 新驗收 37 個候選，A、B 均至 Gen4。Coulomb 保留 DB79／DB88／DB117 的獨立身分，去重後 126 個候選。沒有宣稱超越原論文最佳，也不將取樣通過當成全域證明。HHL 未達原設定 12× 目標，已結案。

## 不需要模型的重現方式

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python tools/verify_archive.py
python tools/reproduce.py both
```

共用 Builder、Evaluator 及結果格式；個別任務保留各自原生 grader、候選和歷史演化控制器。此 repo 可重現最佳電路的評分，而非保證可在任意環境重新執行歷史付費研究。歷史 VM 控制器的範例帳號路徑已匿名化，原始部署包保留在私人備份。研究分析、逐代演算法解讀、最終報告交由後續研究工作。詳見 [文件](docs/REPRODUCIBILITY.md)、[權利聲明](RIGHTS.md)。
