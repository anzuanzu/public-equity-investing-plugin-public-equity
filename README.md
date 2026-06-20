# AI Watchlist Monitor

這個工作區現在除了靜態研究報告，也包含一個可每日更新的 AI 美股監控流程。

## 功能

- 追蹤核心名單：`PENG`、`APLD`、`CLS`、`NVT`、`CRDO`、`VRT`、`ALAB`、`CRWV`、`RXT`、`TSSI`
- 同時掃描延伸候選名單：`ANET`、`SMCI`、`DELL`、`AMD`、`MRVL`、`ETN`、`ORCL`、`NBIS`
- 自動抓取：
  - Yahoo Finance chart endpoint 的近期價格與成交量
  - Google News RSS 的最新新聞
- 動態判斷：
  - `Focus today`
  - `Core watch`
  - `Monitor`
  - `Re-underwrite`
  - `Promotion candidate`

## 執行方式

```powershell
& "C:\Users\james\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" `
  scripts/generate_ai_watchlist_report.py
```

## 產出

- HTML 儀表板：`outputs/ai-watchlist-daily-monitor.html`
- 最新 JSON 快照：`work/daily-monitor/latest-snapshot.json`
- 歷史快照：`work/daily-monitor/history/`

## 可調整的地方

- 追蹤名單與延伸候選：`config/ai_watchlist.json`
- 分數門檻、正負面關鍵字、新聞查詢字串：`config/ai_watchlist.json`

## 建議的日常節奏

- 台北時間每個平日早上 8:30 跑一次
- 看 `Focus today` 與 `Promotion candidate`
- 如果有 `Re-underwrite`，代表不是先加碼，而是先回頭檢查 thesis
