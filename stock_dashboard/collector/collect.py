"""即時資料收集 + 歷史快照維護。

state/ 目錄(由 GitHub Actions 每次執行後 commit 回 repo)保存:
  daily/YYYYMMDD.json  每個交易日的全市場快照(收盤行情、三大法人、融資券、大盤法人)
  prices/<code>.json   自選股/大盤日線快取(Yahoo 失敗時的備援)
  extra.json           本益比/殖利率、月營收、產業別(每日更新一次)
  names.json           股票代號 -> 名稱
TWSE 每個請求都要排隊間隔 2.6 秒,所以已經抓過的日期不會重抓;第一次執行會回補
BACKFILL_DAYS 個交易日(約 4~5 分鐘),之後每天只抓新的一天。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from . import sources as S

BACKFILL_DAYS = 25
KEEP_DAYS = 45


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def _save(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _compact_px(px: dict) -> dict:
    return {c: [p["close"], p["chg_pct"], p["vol"], p["turnover"], p["open"], p["high"], p["low"]]
            for c, p in px.items()}


def expand_px(cpx: dict) -> dict:
    keys = ("close", "chg_pct", "vol", "turnover", "open", "high", "low")
    return {c: dict(zip(keys, v)) for c, v in (cpx or {}).items()}


def _merge_bars(old: list, new: list) -> list:
    m = {b["date"]: b for b in old or []}
    m.update({b["date"]: b for b in new or []})
    return [m[d] for d in sorted(m)]


def price_history(http, code, market, cache_dir: Path, today: datetime):
    """自選股日線:Yahoo 優先,失敗時用快取 + TWSE 月資料補齊。"""
    cache_path = cache_dir / f"{code}.json"
    cached = _load(cache_path, [])
    suffix = ".TWO" if market == "TPEX" else ".TW"
    bars = S.yahoo_chart(http, code + suffix)
    if not bars and market != "TPEX":
        months = S.month_starts(2 if len(cached) > 200 else 14, today)
        fetched = []
        for m in months:
            fetched += S.twse_stock_month(http, code, m)
        bars = _merge_bars(cached, fetched) if fetched else None
    if bars:
        bars = _merge_bars(cached, bars)[-520:]
        _save(cache_path, bars)
        return bars
    return cached or None


def index_history(http, cache_dir: Path, today: datetime):
    cache_path = cache_dir / "TAIEX.json"
    cached = _load(cache_path, [])
    bars = S.yahoo_chart(http, "^TWII")
    if not bars:
        fetched = []
        for m in S.month_starts(2 if len(cached) > 200 else 14, today):
            fetched += S.twse_index_month(http, m)
        bars = _merge_bars(cached, fetched) if fetched else None
    if bars:
        bars = _merge_bars(cached, bars)[-520:]
        _save(cache_path, bars)
        return bars
    return cached or None


def collect_live(root: Path, watchlist: list[dict]):
    http = S.Http()
    state = root / "state"
    daily_dir = state / "daily"
    now = datetime.now(S.TAIPEI)
    S.log(f"[info] 開始收集 {now:%Y-%m-%d %H:%M} (台北)")

    index_bars = index_history(http, state / "prices", now)
    if not index_bars:
        raise RuntimeError("無法取得加權指數歷史資料(Yahoo 與 TWSE 皆失敗)")

    # 交易日曆:以指數日線日期為準,再補上指數最後一天之後到今天的平日
    # (Yahoo 常晚一天才有當日 K 棒;若其實是休市日,TWSE 會回傳無資料而略過)
    dates = [b["date"].replace("-", "") for b in index_bars[-BACKFILL_DAYS:]]
    known = set(dates)
    today = now.strftime("%Y%m%d")
    day = datetime.strptime(index_bars[-1]["date"], "%Y-%m-%d").replace(tzinfo=S.TAIPEI) + timedelta(days=1)
    while day.strftime("%Y%m%d") <= today:
        d = day.strftime("%Y%m%d")
        if day.weekday() < 5 and (d != today or now.hour >= 14):
            dates.append(d)
        day += timedelta(days=1)

    names = _load(state / "names.json", {})
    latest_full_px = None
    for i, d in enumerate(dates):
        is_latest = i == len(dates) - 1
        path = daily_dir / f"{d}.json"
        snap = _load(path, {"date": f"{d[:4]}-{d[4:6]}-{d[6:]}"})
        changed = False
        if "px" not in snap:
            px = S.mi_index(http, d)
            if px:
                snap["px"] = _compact_px(px)
                names.update({c: p["name"] for c, p in px.items()})
                if is_latest:
                    latest_full_px = px
                changed = True
            elif d not in known:
                S.log(f"[info] {d} 無收盤資料(休市或尚未公布)")
                continue
        if "t86" not in snap:
            t = S.t86(http, d)
            if t:
                snap["t86"] = t
                changed = True
        if "margin" not in snap:
            m, mm = S.margin(http, d)
            if m:
                snap["margin"] = m
                snap["market_margin"] = mm
                changed = True
        if "bfi" not in snap:
            b = S.bfi82u(http, d)
            if b:
                snap["bfi"] = b
                changed = True
        if is_latest and "qfii" not in snap:
            q = S.qfii(http, d)
            if q:
                snap["qfii"] = {c: v for c, v in q.items() if v is not None}
                changed = True
        if changed and "px" in snap:
            _save(path, snap)
            S.log(f"[info] 已更新 {d}: {sorted(k for k in snap if k != 'date')}")

    # 清除過舊快照
    all_files = sorted(daily_dir.glob("*.json"))
    for f in all_files[:-KEEP_DAYS]:
        f.unlink()
    daily = []
    for f in sorted(daily_dir.glob("*.json")):
        snap = _load(f, None)
        if snap and "px" in snap:
            snap["px"] = expand_px(snap["px"])
            daily.append((snap["date"], snap))
    if not daily:
        raise RuntimeError("沒有任何交易日快照,TWSE 資料可能無法連線")
    latest_date = daily[-1][0].replace("-", "")
    _save(state / "names.json", names)

    # 每日一次的附加資料
    extra = _load(state / "extra.json", {})
    if extra.get("date") != latest_date:
        val = S.bwibbu(http, latest_date)
        rev = S.monthly_revenue(http)
        ind = S.industries(http)
        extra = {
            "date": latest_date,
            "valuation": val or extra.get("valuation") or {},
            "revenue": rev or extra.get("revenue") or {},
            "industries": ind or extra.get("industries") or {},
            "holidays": extra.get("holidays") or [],
        }
        if val:
            _save(state / "extra.json", extra)
    # 休市日曆一年才變一次:有了就不重抓,沒有(或上次失敗)才單獨補抓
    if not extra.get("holidays"):
        hol = S.holidays(http)
        if hol:
            extra["holidays"] = hol
            _save(state / "extra.json", extra)

    prices = {}
    for s in watchlist:
        bars = price_history(http, s["code"], s.get("market", "TWSE"), state / "prices", now)
        if not bars:
            S.log(f"[warn] {s['code']} 無日線資料,略過")
            continue
        # Yahoo 偶爾晚一天才有當日 K 棒,用 TWSE 官方收盤補上
        latest_px = (latest_full_px or daily[-1][1]["px"]).get(s["code"])
        latest_iso = daily[-1][0]
        if latest_px and bars[-1]["date"] < latest_iso and latest_px.get("open"):
            bars.append({"date": latest_iso, "open": latest_px["open"], "high": latest_px["high"],
                         "low": latest_px["low"], "close": latest_px["close"],
                         "volume": int(latest_px["vol"] * 1000)})
        prices[s["code"]] = bars
    latest_idx = daily[-1][1]
    if index_bars[-1]["date"] < daily[-1][0]:
        # 指數日線少了最新交易日:用 TWSE 當月指數 OHLC 補上
        month = S.twse_index_month(http, latest_date[:6] + "01")
        index_bars = _merge_bars(index_bars, [b for b in month if b["date"] > index_bars[-1]["date"]])

    news = {}
    for s in watchlist:
        news[s["code"]] = S.google_news(http, f"{s['name']} {s['code']} when:7d", limit=12)
    market_news = S.cnyes_news(http, "tw_stock", 25)
    if len(market_news) < 5:
        market_news += S.google_news(http, "台股 盤勢 when:2d", limit=20)

    S.log(f"[info] 收集完成,TWSE 請求 {http.twse_calls} 次")
    return {
        "demo": False,
        "generated_at": now.strftime("%Y-%m-%d %H:%M"),
        "index_bars": index_bars,
        "daily": daily,
        "prices": prices,
        "valuation": extra.get("valuation") or {},
        "revenue": extra.get("revenue") or {},
        "industries": extra.get("industries") or {},
        "holidays": extra.get("holidays") or [],
        "names": names,
        "news": news,
        "market_news": market_news,
        "data_complete": "margin" in latest_idx and "t86" in latest_idx,
    }
