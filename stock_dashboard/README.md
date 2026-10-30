# 台股戰情室

這套系統會自動收集台股新聞、技術面與籌碼資料,每天收盤後更新一個分析網站,並寄出盤後報告 email。

## 你會得到什麼

| 頁面 | 內容 |
|---|---|
| 大盤總覽 | 大盤多空格局判定與建議持股水位、加權指數 K 線、漲跌家數、三大法人近 20 日買賣超、大盤融資餘額、類股強弱(含外資流向)、成交值排行、市場新聞(利多/利空標記)、盤後重點摘要 |
| 自選股 | 自選股綜合評分總表(0–100),可依評分、漲跌、外資/投信買賣超、KD、RSI 排序 |
| 個股分析 | K 線 + MA5/10/20/60/120 + 布林通道,KD/MACD/RSI 副圖;分析師短評;交易計畫(進場區、停損、兩段目標、風險報酬比、支撐壓力);部位計算機;今日訊號;三大法人與融資融券;**訊號歷史勝率**(本股過去兩年同訊號出現後 5/10/20 日表現);本益比、殖利率、月營收;相關新聞與情緒 |
| 選股雷達 | 每天掃描全體上市股:土洋同買、投信連買、外資連買、爆量長紅、相對強勢、籌碼沉澱(融資減價漲)、高息低估、營收雙高成長 |
| 持股管理 | 輸入持股成本即時算損益,並提醒「跌破停損」或「達第一目標」(資料只存在你的瀏覽器) |

**盤後 email**:每個交易日資料完整後寄出一次,內含盤勢摘要、自選股評分與訊號、選股雷達,並附上**單檔網頁報告**(手機直接點開就是完整互動網站)。

## 評分邏輯

綜合評分 = 技術面 40 + 籌碼面 35 + 消息面 15 + 基本面 10。每一項加減分都列在個股頁的「評分明細」,邏輯完全透明。詳細規則見網站的「使用說明」頁或 `collector/analysis.py`。

## 自動化與資料來源

- 排程:`.github/workflows/stock-dashboard.yml`,週一至週五 17:20、22:20(台北)各跑一次;修改 `watchlist.json` 後也會自動重跑。
- 資料:臺灣證券交易所公開資料(收盤行情、T86 三大法人、MI_MARGN 融資融券、BFI82U 大盤法人、BWIBBU 本益比、MI_QFIIS 外資持股、月營收、產業別)、Yahoo Finance 日線、Google News / 鉅亨網新聞。全部免費,不需要 API key。
- 歷史快照存在 `state/`,每次執行後由 Actions commit 回 repo,所以「連續買超天數」等需要歷史的指標會越用越準。第一次執行會自動回補約 25 個交易日(約 5 分鐘)。

## 設定(都是選用)

在 GitHub repo 的 **Settings → Secrets and variables → Actions**:

| 名稱 | 類型 | 用途 |
|---|---|---|
| `GMAIL_USER`、`GMAIL_APP_PASSWORD`、`MAIL_TO` | Secret | 寄盤後報告(沿用當沖推播程式已設定的同一組) |
| `ANTHROPIC_API_KEY` | Secret | 設定後「盤後重點」改由 Claude 撰寫分析師評論;未設定則使用規則式摘要 |
| `ENABLE_PAGES` | Variable | 預設會用 GitHub Pages 架站(需 Settings → Pages → Source 選 GitHub Actions;私人 repo 需要 GitHub Pro),設為 `false` 可關閉 |
| `DASHBOARD_URL` | Variable | 網站網址,會放在 email 裡 |

**不想用 GitHub Pages?** `site/` 是純靜態網站,可以直接用 Zeabur、Netlify 或 Cloudflare Pages 部署 `stock_dashboard/site` 目錄;Actions 每次更新資料都會 commit,託管平台會自動重新部署。

## 修改自選股

編輯 `watchlist.json`,加入 `{"code": "2330", "name": "台積電", "market": "TWSE"}`。上櫃股票 `market` 填 `TPEX`(提供技術面與新聞,三大法人資料目前以上市股為主)。

## 本機執行

```bash
pip install -r stock_dashboard/requirements.txt
python stock_dashboard/run.py          # 抓真實資料
python stock_dashboard/run.py --demo   # 模擬資料(測試版面)
```

產生的 `site/report.html` 可直接用瀏覽器開啟。

## 免責聲明

本系統僅為公開資料的整理與機械式分析,所有評分、訊號與價位都不構成投資建議,也不保證獲利。資料為收盤後資料,非即時報價。投資前請自行判斷並嚴守停損紀律。
