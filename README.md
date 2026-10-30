# TW_STOCK_INFO

台股資訊工具集。所有資料皆來自公開來源(臺灣證券交易所、Yahoo Finance、Google News / 鉅亨網),不需付費 API。

| 目錄 | 說明 | 排程 (workflow) |
|---|---|---|
| [`stock_dashboard/`](stock_dashboard/README.md) | 台股戰情室:自動收集新聞、技術面與籌碼資料,產生靜態分析網站與盤後報告 email | `.github/workflows/stock-dashboard.yml` |
| [`daytrade_notifier/`](daytrade_notifier/README.md) | 當沖觀察名單推播(依前一交易日結算資料篩選) | `.github/workflows/daytrade-notify.yml` |
| [`overnight_notifier/`](overnight_notifier/README.md) | 隔日沖進場訊號推播(盤中即時報價篩選) | `.github/workflows/overnight-notify.yml` |

## 設定

本 repo 不包含任何帳號、密碼或金鑰。需要寄信或使用 AI 摘要時,請在
**Settings → Secrets and variables → Actions** 自行設定:

| 名稱 | 類型 | 用途 |
|---|---|---|
| `GMAIL_USER` | Secret | 寄件 Gmail 帳號 |
| `GMAIL_APP_PASSWORD` | Secret | Gmail 應用程式密碼 |
| `MAIL_TO` | Secret | (選用)收件信箱,逗號分隔 |
| `ANTHROPIC_API_KEY` | Secret | (選用)戰情室「盤後重點」改由 Claude 撰寫 |
| `ENABLE_PAGES` | Variable | 設為 `true` 以 GitHub Pages 發布戰情室網站 |
| `DASHBOARD_URL` | Variable | (選用)網站網址,會放在 email 中 |

本機測試請使用環境變數,切勿把真實帳密寫進程式或 commit(`.env` 已列入 `.gitignore`)。

## 免責聲明

本專案僅為公開資料的整理與機械式分析,所有評分、訊號與價位都不構成投資建議,也不保證獲利。投資決策與風險請自行負責。
