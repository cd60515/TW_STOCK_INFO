#!/usr/bin/env python3
"""隔日沖進場訊號 - 每個開盤日中午 12:00(盤中)掃描即時報價,寄信推播。

隔日沖的核心是「今天買、明天賣」,所以判斷買不買必須用「現在(盤中)的即時價格」,
不能用收盤後才結算的資料——收盤才知道結果時已經無法下單了。因此本程式邏輯是:

  1. 先用前一個已結算交易日的全市場收盤行情,取成交值排行前 N 大個股,
     作為「今天要盯盤的候選池」(只是拿來決定要查誰的即時報價,不是判斷依據本身)
  2. 對候選池逐一查詢 TWSE 即時報價(當下成交價、累積成交量),
     用「現在(通常是中午執行時)的即時漲幅/量能」重新計算今天的表現
  3. 篩出「即時漲幅達門檻」且「即時成交量達門檻」的個股,依漲幅排序取前 N 檔
  4. 標記「已經非常接近漲停」的個股(可能追價買不到,需留意)
  5. 附上每檔新聞搜尋連結,方便自行覆核當下利多/利空(程式本身不做新聞摘要)

判斷買不買、要不要繼續盯,完全依據執行當下的即時數據,不依賴任何預先整理的產業/
題材清單,因此每個交易日都能重新反映當下最熱的標的,可以一直自動運作下去。

免責聲明:本程式僅做公開行情的機械式篩選與整理,不構成投資建議、不保證獲利、
不保證個股當下籌碼結構或是否為隔日沖大戶鎖定標的。實際交易前請務必自行至
券商系統查證分點進出、股本、即時委買委賣與最新消息。隔日沖為高風險策略,
今天買進、明天賣出的持有期間仍可能因為隔夜消息大幅逆轉,務必嚴守停損紀律。
"""
import argparse
import csv
import io
import os
import sys
import ssl
import smtplib
import time
import urllib.parse
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from pathlib import Path

import requests

TAIPEI = timezone(timedelta(hours=8))
UA = {"User-Agent": "Mozilla/5.0 (compatible; OvernightNotifier/1.0)"}

POOL_SIZE = int(os.environ.get("POOL_SIZE", 150))                  # 候選池大小(取成交值前幾大)
MIN_CHANGE_PCT = float(os.environ.get("MIN_CHANGE_PCT", 5))        # 最低即時漲幅(%)
MIN_VOLUME_LOTS = float(os.environ.get("MIN_VOLUME_LOTS", 300))    # 最低即時累積成交量(張)
NEAR_LIMIT_PCT = float(os.environ.get("NEAR_LIMIT_PCT", 9))        # 視為「接近漲停,可能追不到」的漲幅門檻
TOP_N = int(os.environ.get("TOP_N", 10))                           # 最終輸出檔數
QUOTE_BATCH_SIZE = 100                                              # 即時報價每批查詢檔數
RETRY_ATTEMPTS = 3                                                  # TWSE 請求失敗時的重試次數
RETRY_BACKOFF_SECONDS = 3                                           # 重試間隔基準秒數(每次遞增)

MI_INDEX_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?response=json&type=ALLBUT0999"
MIS_WARMUP_URL = "https://mis.twse.com.tw/stock/index.jsp"
MIS_QUOTE_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
INDUSTRY_CSV_URL = "https://mopsfin.twse.com.tw/opendata/t187ap03_L.csv"

# 證交所上市公司標準產業分類代碼表(t187ap03_L 的「產業別」欄位存的是代碼,不是名稱)。
INDUSTRY_CODE_NAMES = {
    "01": "水泥工業", "02": "食品工業", "03": "塑膠工業", "04": "紡織纖維",
    "05": "電機機械", "06": "電器電纜", "08": "玻璃陶瓷", "09": "造紙工業",
    "10": "鋼鐵工業", "11": "橡膠工業", "12": "汽車工業", "13": "電子工業",
    "14": "建材營造", "15": "航運業", "16": "觀光事業", "17": "金融保險",
    "18": "貿易百貨", "19": "綜合", "20": "其他", "21": "化學工業",
    "22": "生技醫療業", "23": "油電燃氣業", "24": "半導體業",
    "25": "電腦及週邊設備業", "26": "光電業", "27": "通信網路業",
    "28": "電子零組件業", "29": "電子通路業", "30": "資訊服務業",
    "31": "其他電子業", "32": "文化創意業", "33": "農業科技業",
    "34": "電子商務業", "35": "綠能環保業", "36": "數位雲端業",
    "37": "運動休閒業", "38": "居家生活業",
}


def decode_industry(code: str) -> str:
    """把產業別代碼轉成中文名稱;遇到不在對照表裡的代碼就照原樣顯示,避免顯示錯誤名稱。"""
    code = (code or "").strip()
    return INDUSTRY_CODE_NAMES.get(code, code)

HOLIDAY_FILE = Path(__file__).parent / "holidays.txt"


def is_manual_holiday(today: datetime) -> bool:
    """檢查 holidays.txt 是否手動標記今天為休市日(選用,見該檔案說明)。"""
    if not HOLIDAY_FILE.exists():
        return False
    date_str = today.strftime("%Y-%m-%d")
    for line in HOLIDAY_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and line == date_str:
            return True
    return False


def _parse_sign(raw: str) -> str:
    """TWSE 的漲跌符號欄位常包在 HTML <span> 內(color:red=漲/color:green=跌),
    也可能是純文字 +/-/X,兩種情況都要能辨識。"""
    raw = raw or ""
    if "red" in raw or raw.strip() == "+":
        return "+"
    if "green" in raw or raw.strip() == "-":
        return "-"
    return "X"


def _find_stock_table(tables):
    """MI_INDEX(全部)回應含多個表格(大盤統計、股票、ETF、權證...),
    找出「個股」那張表:特徵是欄位同時包含證券代號與本益比。"""
    for table in tables:
        fields = table.get("fields") or []
        if "證券代號" in fields and "本益比" in fields:
            return table
    return None


def fetch_candidate_pool(session: requests.Session, date_str=None):
    """取得前一個已結算交易日成交值排行前 POOL_SIZE 大個股代號,
    作為今天要查即時報價的候選池(僅決定查誰,不是判斷依據本身)。
    date_str 省略時,TWSE 會自動回傳最近一個已結算交易日的資料。
    """
    url = MI_INDEX_URL + (f"&date={date_str}" if date_str else "")
    resp = _get_with_retry(session, url, headers=UA, timeout=20)
    data = resp.json()
    if data.get("stat") != "OK":
        raise RuntimeError(f"MI_INDEX 無資料或格式異常: {data.get('stat')}")

    table = _find_stock_table(data.get("tables", []))
    if table is None or not table.get("data"):
        raise RuntimeError("MI_INDEX 回應中找不到個股資料表")

    fields = table["fields"]
    idx = {name: i for i, name in enumerate(fields)}
    rows = []
    for row in table["data"]:
        try:
            code = str(row[idx["證券代號"]]).strip()
            turnover_raw = str(row[idx["成交金額"]]).replace(",", "") if "成交金額" in idx else None
            turnover = int(turnover_raw) if turnover_raw else 0
        except (KeyError, ValueError, IndexError, TypeError):
            continue
        if code:
            rows.append((code, turnover))

    rows.sort(key=lambda r: r[1], reverse=True)
    return data.get("date", ""), [code for code, _ in rows[:POOL_SIZE]]


def _chunk(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _get_with_retry(session: requests.Session, url, **kwargs):
    """對 TWSE 的請求偶爾會遇到暫時性錯誤(如 502),重試幾次再放棄。
    全部重試失敗時,把最後一次的例外往上拋。
    """
    last_error = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            resp = session.get(url, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.exceptions.RequestException as e:
            last_error = e
            if attempt < RETRY_ATTEMPTS:
                print(f"[warn] 請求失敗(第 {attempt} 次): {e},{RETRY_BACKOFF_SECONDS * attempt} 秒後重試",
                      file=sys.stderr)
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    raise last_error


def fetch_realtime_quotes(session: requests.Session, codes):
    """批次查詢 TWSE 即時報價(目前成交價、累積成交量)。
    單一批次在重試用盡後仍失敗時,跳過該批次、繼續處理其他批次,
    避免一批暫時性錯誤(如 TWSE 502)害整次執行完全沒有結果。
    """
    try:
        _get_with_retry(session, MIS_WARMUP_URL, headers=UA, timeout=15)  # 建立 session cookie,否則常回空資料
    except requests.exceptions.RequestException as e:
        print(f"[warn] 建立 session cookie 失敗,繼續嘗試查詢: {e}", file=sys.stderr)

    quotes = {}
    for batch in _chunk(codes, QUOTE_BATCH_SIZE):
        ex_ch = "|".join(f"tse_{code}.tw" for code in batch)
        try:
            resp = _get_with_retry(
                session,
                MIS_QUOTE_URL,
                params={"ex_ch": ex_ch, "json": "1", "delay": "0"},
                headers=UA,
                timeout=20,
            )
        except requests.exceptions.RequestException as e:
            print(f"[warn] 這批 {len(batch)} 檔即時報價查詢失敗,略過此批: {e}", file=sys.stderr)
            continue
        data = resp.json()
        for item in data.get("msgArray", []):
            code = str(item.get("c", "")).strip()
            name = str(item.get("n", "")).strip()
            price_raw = item.get("z")
            prev_close_raw = item.get("y")
            volume_raw = item.get("v")
            high_raw = item.get("h")
            if not code or price_raw in (None, "-", ""):
                continue
            try:
                price = float(price_raw)
                prev_close = float(prev_close_raw)
                volume_lots = float(volume_raw) if volume_raw not in (None, "-", "") else 0.0
                high = float(high_raw) if high_raw not in (None, "-", "") else price
            except (TypeError, ValueError):
                continue
            if prev_close <= 0:
                continue
            quotes[code] = {
                "code": code,
                "name": name,
                "price": price,
                "prev_close": prev_close,
                "high": high,
                "volume_lots": volume_lots,
                "change_pct": (price - prev_close) / prev_close * 100,
            }
    return quotes


def fetch_industry_map(session: requests.Session) -> dict:
    """取得上市公司「公司代號→產業別」對照表(證交所 MOPS 官方結構化資料)。
    抓取失敗時回傳空字典,呼叫端要能容忍缺少產業別資訊而不中斷主流程。
    """
    try:
        resp = _get_with_retry(session, INDUSTRY_CSV_URL, headers=UA, timeout=20)
        text = resp.content.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        fieldnames = reader.fieldnames or []
        code_col = next((f for f in fieldnames if "代號" in f), None)
        industry_col = next((f for f in fieldnames if "產業別" in f), None)
        if not code_col or not industry_col:
            print(f"[warn] 產業別 CSV 欄位格式異常(欄位: {fieldnames}),略過產業別標記",
                  file=sys.stderr)
            return {}
        result = {}
        for row in reader:
            code = (row.get(code_col) or "").strip()
            industry = decode_industry(row.get(industry_col) or "")
            if code:
                result[code] = industry
        return result
    except Exception as e:
        print(f"[warn] 產業別對照表抓取失敗,略過產業別標記: {e}", file=sys.stderr)
        return {}


def build_candidates(date_str=None):
    session = requests.Session()
    pool_date, codes = fetch_candidate_pool(session, date_str)
    if not codes:
        return pool_date, [], 0

    quotes = fetch_realtime_quotes(session, codes)
    industry_map = fetch_industry_map(session)
    candidates = []
    for q in quotes.values():
        if q["change_pct"] < MIN_CHANGE_PCT:
            continue
        if q["volume_lots"] < MIN_VOLUME_LOTS:
            continue
        candidates.append({
            **q,
            "near_limit": q["change_pct"] >= NEAR_LIMIT_PCT,
            "industry": industry_map.get(q["code"], ""),
        })

    candidates.sort(key=lambda c: c["change_pct"], reverse=True)
    return pool_date, candidates[:TOP_N], len(quotes)


def format_date(date_str: str) -> str:
    if len(date_str) == 8 and date_str.isdigit():
        return f"{date_str[:4]}/{date_str[4:6]}/{date_str[6:]}"
    return date_str or "N/A"


def news_search_url(code: str, name: str) -> str:
    query = f"{code} {name} 新聞"
    return "https://www.google.com/search?q=" + urllib.parse.quote(query) + "&tbm=nws"


def render_email(pool_date, candidates, quoted_count, now: datetime) -> str:
    lines = []
    lines.append(f"隔日沖進場訊號 - {now.strftime('%Y/%m/%d %H:%M')} (台北時間,盤中即時)")
    lines.append(f"候選池依據: {format_date(pool_date)} 成交值排行前 {POOL_SIZE} 大個股,"
                 f"實際查得即時報價 {quoted_count} 檔")
    lines.append("")
    lines.append("篩選邏輯(依「現在」的即時報價機械式篩選,非收盤後資料,也非新聞/產業預測):")
    lines.append(f"  1. 候選池:前一交易日成交值前 {POOL_SIZE} 大個股")
    lines.append(f"  2. 即時漲幅 ≥ {MIN_CHANGE_PCT:g}%")
    lines.append(f"  3. 即時累積成交量 ≥ {MIN_VOLUME_LOTS:,.0f} 張")
    lines.append(f"  4. 標記「接近漲停」:即時漲幅 ≥ {NEAR_LIMIT_PCT:g}%(可能追價買不到,留意委買賣)")
    lines.append("")

    if not candidates:
        lines.append("此刻無個股符合以上門檻,可能是大盤整體偏弱、門檻設定偏嚴,或非交易時段。")
    else:
        lines.append(f"{'代號':<6}{'名稱':<10}{'現價':>8}{'即時漲幅%':>10}{'累積量(張)':>10}{'接近漲停':>8}  產業別")
        for c in candidates:
            lines.append(
                f"{c['code']:<6}{c['name']:<10}{c['price']:>8.2f}{c['change_pct']:>+10.2f}"
                f"{c['volume_lots']:>10,.0f}{'⚠是' if c['near_limit'] else '':>8}  {c['industry']}"
            )

        industry_counts = {}
        for c in candidates:
            if c["industry"]:
                industry_counts[c["industry"]] = industry_counts.get(c["industry"], 0) + 1
        clustered = sorted(
            ((ind, n) for ind, n in industry_counts.items() if n >= 2),
            key=lambda x: x[1], reverse=True,
        )
        if clustered:
            lines.append("")
            lines.append("產業聚焦(候選名單中同產業出現 2 檔以上,可能是今天的族群輪動焦點):")
            for ind, n in clustered:
                lines.append(f"  {ind}: {n} 檔")

        lines.append("")
        lines.append("個股新聞快速查證連結(下單前請務必自行確認利多/利空):")
        for c in candidates:
            lines.append(f"  {c['code']} {c['name']}: {news_search_url(c['code'], c['name'])}")

    lines.append("")
    lines.append("隔日沖跟單心法提醒:")
    lines.append("  • 這份清單是「現在可以考慮買進」的訊號,持有到明天開盤附近伺機賣出,不是長抱")
    lines.append("  • 標記「接近漲停」的個股,實際下單前務必看一下委買委賣,若已經鎖死可能根本買不到")
    lines.append("  • 進場前先設好停損(如跌破當日均價或虧損 3~5% 無條件出場)")
    lines.append("  • 隔天開盤以漲幅 4% 為分水嶺:開盤仍強可續觀察,不到 4% 且量放大則盡速獲利了結")
    lines.append("  • 隔天 09:00–09:30 是黃金出場期,錯過常伴隨主力無情下殺,絕不留戀")
    lines.append("  • 每天鎖定觀察標的不超過 5 檔,提高專注度與勝率")
    lines.append("")
    lines.append("免責聲明:本清單為公開即時行情之機械式篩選整理,不構成投資建議,盈虧請自負。")
    lines.append("隔日沖為高風險策略,今天買、明天賣的過程中仍可能因隔夜消息大幅逆轉,請自行查證後再決策。")
    return "\n".join(lines)


def send_email(subject: str, body: str):
    user = os.environ.get("GMAIL_USER")
    app_password = os.environ.get("GMAIL_APP_PASSWORD")
    to_addr = os.environ.get("MAIL_TO") or user
    if not user or not app_password:
        sys.exit("缺少環境變數 GMAIL_USER / GMAIL_APP_PASSWORD,請於 GitHub Secrets 設定後再執行。")

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_addr

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(user, app_password)
        server.sendmail(user, [a.strip() for a in to_addr.split(",")], msg.as_string())


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        help="測試用:指定候選池依據的結算日期 YYYYMMDD(例如 20260827)。"
             "注意即時報價一定是『執行當下』的市場狀態,無法回測到過去某一刻的盤中報價,"
             "此參數只影響候選池挑選,並會略過週末/假日檢查。",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    test_date = args.date or os.environ.get("TEST_DATE") or None

    now = datetime.now(TAIPEI)
    if not test_date:
        if now.weekday() >= 5:
            print("今天是週末,非開盤日,不執行。")
            return
        if is_manual_holiday(now):
            print("今天在 holidays.txt 中被標記為休市日,不執行。")
            return

    try:
        pool_date, candidates, quoted_count = build_candidates(test_date)
    except Exception as e:
        # 候選池抓取在重試用盡後仍失敗(例如 TWSE 伺服器持續異常):
        # 寄一封簡短的失敗通知,而不是讓使用者完全不知道今天為什麼沒收到信。
        print(f"[error] 候選名單產生失敗: {e}", file=sys.stderr)
        subject = f"[隔日沖進場訊號] 今日查詢失敗 {now.strftime('%Y/%m/%d %H:%M')}"
        body = (
            f"今天(台北時間 {now.strftime('%Y/%m/%d %H:%M')})查詢 TWSE 資料時發生錯誤,"
            f"已重試 {RETRY_ATTEMPTS} 次仍失敗,本次未能產生候選名單。\n\n"
            f"錯誤訊息: {e}\n\n"
            "通常是 TWSE 伺服器暫時性異常,稍後應會恢復;若連續多日都收到這封信,"
            "請回報以便進一步排查。"
        )
        send_email(subject, body)
        sys.exit(1)

    body = render_email(pool_date, candidates, quoted_count, now)
    label = " [測試模式]" if test_date else ""
    subject = f"[隔日沖進場訊號]{label} {now.strftime('%Y/%m/%d %H:%M')}"
    send_email(subject, body)
    print("已寄出通知信。")
    print(body)


if __name__ == "__main__":
    main()
