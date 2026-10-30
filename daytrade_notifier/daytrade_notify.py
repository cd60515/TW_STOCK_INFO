#!/usr/bin/env python3
"""台股當沖觀察名單 - 每個開盤日中午 12:00 寄信推播。

篩選邏輯依常見當沖心法設計(流動性優先、成交值排行、要有振幅空間、避開鎖死漲跌停):
  1. 先取「成交量排行前 N 大」個股(TWSE 每日成交量前 20 名報表,人氣 + 流動性)
  2. 成交量(張)需達門檻,避免收盤沖不掉
  3. 成交值(以收盤價 x 成交股數估算,億)需達門檻
  4. 當日振幅 (最高-最低)/前一日收盤 需達門檻,太悶的盤沒有沖的空間
  5. 排除漲跌幅接近漲跌停的個股(鎖死流動性風險高)

資料來源為 TWSE 公開資訊(每日收盤後結算),因此中午執行時反映的是「最近一個已結算交易日」
的數據,用途是提供隔日/盤中觀察名單的機械式篩選參考,不是即時報價。

免責聲明:本程式僅做公開數據的機械式篩選與整理,不構成投資建議、不保證獲利、不保證個股
當下可現股當沖或未被列為處置股/注意股。實際交易前請務必自行於券商系統覆核。
"""
import argparse
import os
import sys
import ssl
import smtplib
import time
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from pathlib import Path

import requests

TAIPEI = timezone(timedelta(hours=8))
UA = {"User-Agent": "Mozilla/5.0 (compatible; DayTradeNotifier/1.0)"}
RETRY_ATTEMPTS = 3           # TWSE 請求失敗時的重試次數
RETRY_BACKOFF_SECONDS = 3    # 重試間隔基準秒數(每次遞增)

MIN_VOLUME_LOTS = float(os.environ.get("MIN_VOLUME_LOTS", 2000))   # 最低成交量(張)
MIN_TURNOVER_YI = float(os.environ.get("MIN_TURNOVER_YI", 5))      # 最低成交值(億元)
MIN_RANGE_PCT = float(os.environ.get("MIN_RANGE_PCT", 3))          # 最低當日振幅(%)
MAX_CHANGE_PCT = float(os.environ.get("MAX_CHANGE_PCT", 9))        # 排除漲跌幅過大(接近鎖死)個股
TOP_N = int(os.environ.get("TOP_N", 8))                            # 最終輸出檔數
CANDIDATE_POOL = int(os.environ.get("CANDIDATE_POOL", 20))         # 先取成交值前幾大做篩選

MI_INDEX20_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX20?response=json"

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


def fetch_json(url: str, timeout=15):
    """對 TWSE 的請求偶爾會遇到暫時性錯誤(如 502),重試幾次再放棄。"""
    last_error = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            resp = requests.get(url, headers=UA, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            last_error = e
            if attempt < RETRY_ATTEMPTS:
                print(f"[warn] 請求失敗(第 {attempt} 次): {e},{RETRY_BACKOFF_SECONDS * attempt} 秒後重試",
                      file=sys.stderr)
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    raise last_error


def _parse_sign(raw: str) -> str:
    """TWSE 的漲跌符號欄位常包在 HTML <span> 內(color:red=漲/color:green=跌),
    也可能是純文字 +/-/X,兩種情況都要能辨識。"""
    raw = raw or ""
    if "red" in raw or raw.strip() == "+":
        return "+"
    if "green" in raw or raw.strip() == "-":
        return "-"
    return "X"


def fetch_top20(date_str=None):
    """取得成交量前 20 大個股(TWSE 每日成交量前 20 名報表),含當日最高/最低/收盤價。

    date_str 為 YYYYMMDD 字串時,查詢該指定日期的排行(用於測試/回測特定交易日);
    省略時查詢「最近一個已結算交易日」。

    注意:此報表不含精確成交金額欄位,程式以「收盤價 x 成交股數」估算成交值,
    僅供排序與門檻篩選參考,並非交易所公告的精確成交金額。
    """
    url = MI_INDEX20_URL + (f"&date={date_str}" if date_str else "")
    data = fetch_json(url)
    if data.get("stat") != "OK" or not data.get("data"):
        raise RuntimeError(f"MI_INDEX20 無資料或格式異常: {data.get('stat')}")

    fields = data["fields"]
    idx = {name: i for i, name in enumerate(fields)}
    rows = []
    for row in data["data"]:
        try:
            code = str(row[idx["證券代號"]]).strip()
            name = str(row[idx["證券名稱"]]).strip()
            volume_shares = int(str(row[idx["成交股數"]]).replace(",", ""))
            high = float(str(row[idx["最高價"]]).replace(",", ""))
            low = float(str(row[idx["最低價"]]).replace(",", ""))
            close = float(str(row[idx["收盤價"]]).replace(",", ""))
            diff_raw = str(row[idx["漲跌價差"]]).replace(",", "").strip()
            diff_val = float(diff_raw) if diff_raw not in ("", "--") else 0.0
            sign = _parse_sign(str(row[idx["漲跌(+/-)"]]))
            signed_diff = diff_val if sign == "+" else -diff_val if sign == "-" else 0.0
            prev_close = close - signed_diff
        except (KeyError, ValueError, IndexError, TypeError):
            continue

        turnover = None
        if "成交金額" in idx:
            try:
                turnover = int(str(row[idx["成交金額"]]).replace(",", ""))
            except (ValueError, TypeError):
                turnover = None
        if turnover is None:
            turnover = close * volume_shares  # 估算值,見函式說明

        rows.append({
            "code": code,
            "name": name,
            "volume_shares": volume_shares,
            "turnover": turnover,
            "high": high,
            "low": low,
            "close": close,
            "prev_close": prev_close,
            "change_pct": (signed_diff / prev_close * 100) if prev_close else 0.0,
        })
    return data.get("date", ""), rows


def build_candidates(date_str=None):
    date_used, rows = fetch_top20(date_str)

    rows.sort(key=lambda r: r["turnover"], reverse=True)
    pool = rows[:CANDIDATE_POOL]

    enriched = []
    for r in pool:
        range_pct = None
        if r["prev_close"]:
            range_pct = (r["high"] - r["low"]) / r["prev_close"] * 100
        enriched.append({
            **r,
            "volume_lots": r["volume_shares"] / 1000,
            "turnover_yi": r["turnover"] / 1e8,
            "range_pct": range_pct,
        })

    candidates = [
        c for c in enriched
        if c["volume_lots"] >= MIN_VOLUME_LOTS
        and c["turnover_yi"] >= MIN_TURNOVER_YI
        and abs(c["change_pct"]) < MAX_CHANGE_PCT
        and (c["range_pct"] is None or c["range_pct"] >= MIN_RANGE_PCT)
    ]
    fallback = False
    if not candidates:
        fallback = True
        candidates = enriched[:5]

    candidates.sort(key=lambda c: (c["range_pct"] or 0) + c["turnover_yi"], reverse=True)
    return date_used, candidates[:TOP_N], fallback


def format_date(date_str: str) -> str:
    if len(date_str) == 8 and date_str.isdigit():
        return f"{date_str[:4]}/{date_str[4:6]}/{date_str[6:]}"
    return date_str or "N/A"


def render_email(date_str, candidates, fallback, now: datetime, test_date=None) -> str:
    lines = []
    lines.append(f"台股當沖觀察名單 - {now.strftime('%Y/%m/%d %H:%M')} (台北時間)")
    lines.append(f"資料基準日(最近一個已結算交易日): {format_date(date_str)}")
    if test_date:
        lines.append(f"⚠ 測試模式:資料為手動指定日期 {format_date(test_date)},非自動排程結果。")
    lines.append("")
    lines.append("篩選邏輯(機械式數據篩選,非投資建議):")
    lines.append(f"  1. 成交值排行前 {CANDIDATE_POOL} 大個股(人氣 + 流動性)")
    lines.append(f"  2. 成交量 ≥ {MIN_VOLUME_LOTS:,.0f} 張(避免收盤沖不掉)")
    lines.append(f"  3. 成交值(估) ≥ {MIN_TURNOVER_YI:g} 億元")
    lines.append(f"  4. 當日振幅((最高-最低)/前一日收盤) ≥ {MIN_RANGE_PCT:g}%(要有波動空間)")
    lines.append(f"  5. 排除漲跌幅 ≥ {MAX_CHANGE_PCT:g}% 個股(接近漲跌停,鎖死流動性風險高)")
    lines.append("")
    if fallback:
        lines.append("⚠ 今日無個股完全符合以上門檻,以下改列出成交值排行最前面的標的供參考:")
        lines.append("")

    lines.append(f"{'代號':<6}{'名稱':<10}{'收盤':>8}{'漲跌%':>8}{'成交值估(億)':>12}{'成交量(張)':>10}{'振幅%':>8}")
    for c in candidates:
        range_str = f"{c['range_pct']:.2f}" if c["range_pct"] is not None else "N/A"
        lines.append(
            f"{c['code']:<6}{c['name']:<10}{c['close']:>8.2f}{c['change_pct']:>+8.2f}"
            f"{c['turnover_yi']:>12.2f}{c['volume_lots']:>10,.0f}{range_str:>8}"
        )
    lines.append("")
    lines.append("當沖心法提醒:")
    lines.append("  • 進場時段建議 9:00–10:30(尤其開盤前 30 分鐘量能最大),收盤前 30 分鐘避免新進場")
    lines.append("  • 每日鎖定觀察標的不超過 5 檔,提高勝率與專注度")
    lines.append("  • 進場前先設好停損點(前波低點或固定金額),走勢不如預期就嚴守紀律出場")
    lines.append("  • 交易前務必於券商系統覆核個股是否可現股當沖、是否為處置股/注意股")
    lines.append("")
    lines.append("免責聲明:本清單為公開行情之機械式篩選整理,不構成投資建議,盈虧請自負。")
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
        help="測試/回測用:指定資料日期 YYYYMMDD(例如 20260827)。"
             "指定時會略過週末/假日檢查,並改用該日的排行與最高低價計算振幅。",
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
        date_str, candidates, fallback = build_candidates(test_date)
    except Exception as e:
        # 候選池抓取在重試用盡後仍失敗(例如 TWSE 伺服器持續異常):
        # 寄一封簡短的失敗通知,而不是讓使用者完全不知道今天為什麼沒收到信。
        print(f"[error] 候選名單產生失敗: {e}", file=sys.stderr)
        subject = f"[台股當沖觀察名單] 今日查詢失敗 {now.strftime('%Y/%m/%d %H:%M')}"
        body = (
            f"今天(台北時間 {now.strftime('%Y/%m/%d %H:%M')})查詢 TWSE 資料時發生錯誤,"
            f"已重試 {RETRY_ATTEMPTS} 次仍失敗,本次未能產生候選名單。\n\n"
            f"錯誤訊息: {e}\n\n"
            "通常是 TWSE 伺服器暫時性異常,稍後應會恢復;若連續多日都收到這封信,"
            "請回報以便進一步排查。"
        )
        send_email(subject, body)
        sys.exit(1)

    body = render_email(date_str, candidates, fallback, now, test_date)
    label = " [測試模式]" if test_date else ""
    subject = f"[台股當沖觀察名單]{label} {now.strftime('%Y/%m/%d')} (資料基準日 {format_date(date_str)})"
    send_email(subject, body)
    print("已寄出通知信。")
    print(body)


if __name__ == "__main__":
    main()
