"""資料來源:TWSE 證交所公開資料、Yahoo Finance 日線、新聞 RSS。

所有函式失敗時都回傳 None / 空集合而不是拋例外,讓單一來源異常不會讓整個網站停擺;
呼叫端負責在資料缺漏時降級顯示。

TWSE 對短時間大量請求會暫時封鎖 IP(約每 5 秒 3 次以上就有風險),因此所有
TWSE 請求都經過 `Http.twse()`,強制每次請求間隔 TWSE_PACE_SECONDS 秒。
"""
from __future__ import annotations

import csv
import io
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

TAIPEI = timezone(timedelta(hours=8))
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}
TWSE_PACE_SECONDS = 2.6
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 4

TWSE = "https://www.twse.com.tw/rwd/zh"


def log(msg: str):
    print(msg, file=sys.stderr, flush=True)


def to_float(v):
    """TWSE 數字欄位帶千分位逗號,停牌/無資料時是 '--' 或空字串。"""
    if v is None:
        return None
    s = str(v).replace(",", "").replace("%", "").strip()
    if s in ("", "--", "---", "-", "X", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _round(v, n=2):
    return None if v is None else round(v, n)


def parse_sign(raw: str) -> int:
    """TWSE 漲跌符號欄位常包在 HTML <span> 內(red=漲/green=跌),也可能是純文字 +/-。"""
    raw = raw or ""
    if "red" in raw or raw.strip() == "+":
        return 1
    if "green" in raw or raw.strip() == "-":
        return -1
    return 0


def roc_to_iso(s: str) -> str | None:
    """民國日期 '115/10/30' -> '2026-10-30'。"""
    m = re.match(r"\s*(\d{2,3})/(\d{1,2})/(\d{1,2})", s or "")
    if not m:
        return None
    return f"{int(m.group(1)) + 1911:04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


class Http:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update(UA)
        self._last_twse = 0.0
        self.twse_calls = 0

    def get(self, url, timeout=20, **kw):
        last = None
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                r = self.s.get(url, timeout=timeout, **kw)
                if r.status_code == 429 or r.status_code >= 500:
                    raise requests.HTTPError(f"HTTP {r.status_code}")
                r.raise_for_status()
                return r
            except requests.RequestException as e:
                last = e
                if attempt < RETRY_ATTEMPTS:
                    time.sleep(RETRY_BACKOFF_SECONDS * attempt)
        log(f"[warn] 請求失敗 {url[:120]}: {last}")
        return None

    def json(self, url, timeout=20):
        r = self.get(url, timeout=timeout)
        if r is None:
            return None
        try:
            return r.json()
        except ValueError:
            log(f"[warn] 非 JSON 回應 {url[:120]}")
            return None

    def twse(self, url):
        wait = TWSE_PACE_SECONDS - (time.time() - self._last_twse)
        if wait > 0:
            time.sleep(wait)
        self._last_twse = time.time()
        self.twse_calls += 1
        data = self.json(url)
        if not data or str(data.get("stat") or "OK").upper() != "OK":
            return None
        return data


def _field_index(fields, *keywords, exact=None):
    """依欄位名稱關鍵字找欄位位置(TWSE 偶爾調整欄位順序,不能寫死索引)。"""
    for i, f in enumerate(fields):
        if exact is not None and f == exact:
            return i
        if exact is None and all(k in f for k in keywords):
            return i
    return None


# ---------------------------------------------------------------- 個股/大盤日線

def yahoo_chart(http: Http, symbol: str, range_="2y"):
    """Yahoo Finance 日線。symbol 例:2330.TW、6488.TWO、^TWII。回傳 list[dict] 依日期排序。"""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}"
           f"?range={range_}&interval=1d&includePrePost=false")
    data = http.json(url)
    try:
        res = data["chart"]["result"][0]
        ts = res["timestamp"]
        q = res["indicators"]["quote"][0]
    except (TypeError, KeyError, IndexError):
        return None
    bars = {}
    for i, t in enumerate(ts):
        o, h, l, c, v = (q.get(k, [None] * len(ts))[i] for k in ("open", "high", "low", "close", "volume"))
        if None in (o, h, l, c):
            continue
        d = datetime.fromtimestamp(t, TAIPEI).strftime("%Y-%m-%d")
        bars[d] = {"date": d, "open": round(o, 2), "high": round(h, 2), "low": round(l, 2),
                   "close": round(c, 2), "volume": int(v or 0)}
    return [bars[d] for d in sorted(bars)] or None


def twse_stock_month(http: Http, code: str, yyyymm01: str):
    """TWSE 個股單月日成交資訊(Yahoo 失敗時的備援)。"""
    data = http.twse(f"{TWSE}/afterTrading/STOCK_DAY?date={yyyymm01}&stockNo={code}&response=json")
    if not data or not data.get("data"):
        return []
    f = data["fields"]
    ix = {k: _field_index(f, k) for k in ("日期", "成交股數", "開盤價", "最高價", "最低價", "收盤價")}
    out = []
    for row in data["data"]:
        d = roc_to_iso(row[ix["日期"]])
        o, h, l, c = (to_float(row[ix[k]]) for k in ("開盤價", "最高價", "最低價", "收盤價"))
        if not d or None in (o, h, l, c):
            continue
        out.append({"date": d, "open": o, "high": h, "low": l, "close": c,
                    "volume": int(to_float(row[ix["成交股數"]]) or 0)})
    return out


def twse_index_month(http: Http, yyyymm01: str):
    """加權指數單月 OHLC(^TWII 備援)。"""
    data = http.twse(f"{TWSE}/TAIEX/MI_5MINS_HIST?date={yyyymm01}&response=json")
    if not data or not data.get("data"):
        return []
    out = []
    for row in data["data"]:
        d = roc_to_iso(row[0])
        vals = [to_float(x) for x in row[1:5]]
        if d and None not in vals:
            out.append({"date": d, "open": vals[0], "high": vals[1], "low": vals[2], "close": vals[3], "volume": 0})
    return out


def month_starts(months_back: int, today: datetime):
    y, m = today.year, today.month
    out = []
    for _ in range(months_back):
        out.append(f"{y:04d}{m:02d}01")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


# ---------------------------------------------------------------- 全市場每日資料

def mi_index(http: Http, date: str):
    """全市場個股收盤行情 -> {code: {name, close, chg, chg_pct, vol(張), turnover(億), open, high, low}}"""
    data = http.twse(f"{TWSE}/afterTrading/MI_INDEX?date={date}&type=ALLBUT0999&response=json")
    if not data:
        return None
    table = None
    for t in data.get("tables", []):
        f = t.get("fields") or []
        if "證券代號" in f and "收盤價" in f:
            table = t
            break
    if not table or not table.get("data"):
        return None
    f = table["fields"]
    ix = {k: _field_index(f, exact=k) for k in
          ("證券代號", "證券名稱", "成交股數", "成交金額", "開盤價", "最高價", "最低價", "收盤價", "漲跌(+/-)", "漲跌價差")}
    out = {}
    for row in table["data"]:
        try:
            code = str(row[ix["證券代號"]]).strip()
            close = to_float(row[ix["收盤價"]])
            if close is None:
                continue
            diff = (to_float(row[ix["漲跌價差"]]) or 0.0) * parse_sign(str(row[ix["漲跌(+/-)"]]))
            prev = close - diff
            out[code] = {
                "name": str(row[ix["證券名稱"]]).strip(),
                "close": close,
                "open": to_float(row[ix["開盤價"]]),
                "high": to_float(row[ix["最高價"]]),
                "low": to_float(row[ix["最低價"]]),
                "chg": round(diff, 2),
                "chg_pct": round(diff / prev * 100, 2) if prev else 0.0,
                "vol": round((to_float(row[ix["成交股數"]]) or 0) / 1000),
                "turnover": round((to_float(row[ix["成交金額"]]) or 0) / 1e8, 2),
            }
        except (TypeError, IndexError, KeyError):
            continue
    return out or None


def t86(http: Http, date: str):
    """三大法人個股買賣超(單位:張) -> {code: [外資, 投信, 自營商, 合計]}"""
    data = http.twse(f"{TWSE}/fund/T86?date={date}&selectType=ALLBUT0999&response=json")
    if not data or not data.get("data"):
        return None
    f = data["fields"]
    i_code = _field_index(f, "證券代號")
    i_name = _field_index(f, "證券名稱")
    i_f1 = _field_index(f, "外陸資買賣超")
    i_f2 = _field_index(f, "外資自營商買賣超")
    i_t = _field_index(f, "投信買賣超")
    i_d = _field_index(f, exact="自營商買賣超股數")
    i_all = _field_index(f, "三大法人買賣超")
    out, names = {}, {}
    for row in data["data"]:
        try:
            code = str(row[i_code]).strip()
            foreign = (to_float(row[i_f1]) or 0) + ((to_float(row[i_f2]) or 0) if i_f2 is not None else 0)
            trust = to_float(row[i_t]) or 0
            dealer = to_float(row[i_d]) or 0 if i_d is not None else 0
            total = to_float(row[i_all]) if i_all is not None else None
            if total is None:
                total = foreign + trust + dealer
            out[code] = [round(foreign / 1000), round(trust / 1000), round(dealer / 1000), round(total / 1000)]
            if i_name is not None:
                names[code] = str(row[i_name]).strip()
        except (TypeError, IndexError):
            continue
    return out or None


def margin(http: Http, date: str):
    """融資融券 -> ({code: [融資今日餘額, 融資前日餘額, 融券今日餘額, 融券前日餘額]}(張), 大盤融資金額(億))"""
    data = http.twse(f"{TWSE}/marginTrading/MI_MARGN?date={date}&selectType=ALL&response=json")
    if not data:
        return None, None
    tables = data.get("tables") or []
    stock_tbl, summary_tbl = None, None
    for t in tables:
        f = t.get("fields") or []
        if f and f[0] == "代號":
            stock_tbl = t
        elif f and "項目" in f[0]:
            summary_tbl = t
    out = {}
    if stock_tbl and stock_tbl.get("data"):
        for row in stock_tbl["data"]:
            try:
                code = str(row[0]).strip()
                vals = [to_float(row[i]) for i in (6, 5, 12, 11)]
                if None in vals:
                    continue
                out[code] = [int(v) for v in vals]
            except (IndexError, TypeError):
                continue
    market_margin = None
    if summary_tbl:
        for row in summary_tbl.get("data", []):
            if "融資金額" in str(row[0]):
                v = to_float(row[5]) if len(row) > 5 else None
                market_margin = round(v / 1e5, 1) if v else None  # 仟元 -> 億
    return (out or None), market_margin


def bfi82u(http: Http, date: str):
    """大盤三大法人買賣金額(億) -> {foreign, trust, dealer, total}"""
    data = http.twse(f"{TWSE}/fund/BFI82U?type=day&dayDate={date}&response=json")
    if not data or not data.get("data"):
        return None
    res = {"foreign": 0.0, "trust": 0.0, "dealer": 0.0, "total": 0.0}
    for row in data["data"]:
        name, net = str(row[0]), to_float(row[3])
        if net is None:
            continue
        net /= 1e8
        if "合計" in name:
            res["total"] = net
        elif "投信" in name:
            res["trust"] += net
        elif "外資" in name:
            res["foreign"] += net
        elif "自營商" in name:
            res["dealer"] += net
    return {k: round(v, 2) for k, v in res.items()}


def bwibbu(http: Http, date: str):
    """本益比、殖利率、股價淨值比 -> {code: {pe, yield, pb}}"""
    data = http.twse(f"{TWSE}/afterTrading/BWIBBU_d?date={date}&selectType=ALL&response=json")
    if not data or not data.get("data"):
        return None
    f = data["fields"]
    i_code, i_pe, i_y, i_pb = (_field_index(f, "證券代號"), _field_index(f, "本益比"),
                               _field_index(f, "殖利率"), _field_index(f, "股價淨值比"))
    out = {}
    for row in data["data"]:
        try:
            out[str(row[i_code]).strip()] = {"pe": to_float(row[i_pe]), "yield": to_float(row[i_y]),
                                             "pb": to_float(row[i_pb])}
        except (IndexError, TypeError):
            continue
    return out or None


def qfii(http: Http, date: str):
    """外資持股比率(%) -> {code: ratio}"""
    data = http.twse(f"{TWSE}/fund/MI_QFIIS?date={date}&selectType=ALLBUT0999&response=json")
    if not data or not data.get("data"):
        return None
    f = data["fields"]
    i_code = _field_index(f, "證券代號")
    i_r = _field_index(f, "全體外資", "持股比率")
    if i_r is None:
        return None
    return {str(r[i_code]).strip(): to_float(r[i_r]) for r in data["data"] if len(r) > i_r}


def monthly_revenue(http: Http):
    """上市公司最新月營收(openapi)-> {code: {ym, rev(億), mom, yoy, cum_yoy}}"""
    data = http.json("https://openapi.twse.com.tw/v1/opendata/t187ap05_L", timeout=40)
    if not isinstance(data, list):
        return None

    def pick(row, *kw):
        for k, v in row.items():
            if all(x in k for x in kw):
                return v
        return None

    out = {}
    for row in data:
        code = str(pick(row, "公司代號") or "").strip()
        if not code:
            continue
        rev = to_float(pick(row, "當月營收"))
        out[code] = {
            "ym": str(pick(row, "資料年月") or ""),
            "rev": round(rev / 1e5, 2) if rev is not None else None,  # 仟元 -> 億
            "mom": _round(to_float(pick(row, "上月比較增減"))),
            "yoy": _round(to_float(pick(row, "去年同月增減"))),
            "cum_yoy": _round(to_float(pick(row, "前期比較增減"))),
        }
    return out or None


INDUSTRY_CODE_NAMES = {
    "01": "水泥", "02": "食品", "03": "塑膠", "04": "紡織纖維", "05": "電機機械", "06": "電器電纜",
    "08": "玻璃陶瓷", "09": "造紙", "10": "鋼鐵", "11": "橡膠", "12": "汽車", "13": "電子",
    "14": "建材營造", "15": "航運", "16": "觀光餐旅", "17": "金融保險", "18": "貿易百貨", "19": "綜合",
    "20": "其他", "21": "化學", "22": "生技醫療", "23": "油電燃氣", "24": "半導體", "25": "電腦週邊",
    "26": "光電", "27": "通信網路", "28": "電子零組件", "29": "電子通路", "30": "資訊服務",
    "31": "其他電子", "32": "文化創意", "33": "農業科技", "34": "電子商務", "35": "綠能環保",
    "36": "數位雲端", "37": "運動休閒", "38": "居家生活", "91": "存託憑證",
}


def holidays(http: Http):
    """TWSE 今年休市日 -> ['YYYY-MM-DD', ...]。表內「開始交易日/最後交易日」是交易日,要排除。
    不假設欄位順序:每列找出日期格式的那一格,其餘格當作名稱/說明。"""
    data = http.twse(f"{TWSE}/holidaySchedule/holidaySchedule?response=json")
    if not data or not data.get("data"):
        log(f"[warn] 休市日曆無資料: stat={data.get('stat') if data else None}")
        return None
    out = set()
    for row in data["data"]:
        cells = [str(c).strip() for c in row]
        iso = None
        for c in cells:
            m = re.match(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})$", c)
            iso = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else roc_to_iso(c) if re.match(r"\d{2,3}/\d{1,2}/\d{1,2}$", c) else None
            if iso:
                break
        if iso and not any("交易" in c for c in cells):
            out.add(iso)
    if not out:
        log(f"[warn] 休市日曆解析不到日期: fields={data.get('fields')} first={data['data'][:2]}")
    return sorted(out) or None


def industries(http: Http):
    """上市公司產業別 -> {code: 產業名稱}"""
    data = http.json("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", timeout=40)
    rows = []
    if isinstance(data, list):
        rows = [(str(r.get("公司代號", "")).strip(), str(r.get("產業別", "")).strip()) for r in data]
    else:
        r = http.get("https://mopsfin.twse.com.tw/opendata/t187ap03_L.csv", timeout=40)
        if r is not None:
            text = r.content.decode("utf-8-sig", errors="ignore")
            for row in csv.DictReader(io.StringIO(text)):
                rows.append((str(row.get("公司代號", "")).strip(), str(row.get("產業別", "")).strip()))
    out = {c: INDUSTRY_CODE_NAMES.get(i, i) for c, i in rows if c and i}
    return out or None


# ---------------------------------------------------------------- 新聞

def _clean_title(t: str) -> tuple[str, str]:
    """Google News 標題格式為「標題 - 來源」。"""
    t = re.sub(r"\s+", " ", t or "").strip()
    if " - " in t:
        head, src = t.rsplit(" - ", 1)
        return head.strip(), src.strip()
    return t, ""


def google_news(http: Http, query: str, limit=12):
    url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(query)
           + "&hl=zh-TW&gl=TW&ceid=TW:zh-Hant")
    r = http.get(url, timeout=20)
    if r is None:
        return []
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError:
        return []
    out = []
    for item in root.iter("item"):
        title, src = _clean_title(item.findtext("title"))
        src = item.findtext("source") or src
        try:
            ts = parsedate_to_datetime(item.findtext("pubDate")).astimezone(TAIPEI)
        except (TypeError, ValueError):
            ts = None
        out.append({"title": title, "source": src, "link": item.findtext("link") or "",
                    "time": ts.strftime("%Y-%m-%d %H:%M") if ts else ""})
    out.sort(key=lambda x: x["time"], reverse=True)
    return out[:limit]


def cnyes_news(http: Http, category="tw_stock", limit=30):
    data = http.json(f"https://api.cnyes.com/media/api/v1/newslist/category/{category}?limit={limit}")
    try:
        items = data["items"]["data"]
    except (TypeError, KeyError):
        return []
    out = []
    for it in items:
        ts = datetime.fromtimestamp(int(it.get("publishAt", 0)), TAIPEI)
        out.append({"title": (it.get("title") or "").strip(), "source": "鉅亨網",
                    "link": f"https://news.cnyes.com/news/id/{it.get('newsId')}",
                    "time": ts.strftime("%Y-%m-%d %H:%M"),
                    "summary": (it.get("summary") or "").strip()[:160]})
    return out
