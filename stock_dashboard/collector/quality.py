"""績優股池:EPS 排名前段班的上市公司,入選後保留一年,等股價拉回長期均線支撐時找買點。

邏輯(規則全部透明):
  1. 入池:近四季 EPS 排名前 TOP_N 名。EPS = 收盤價 ÷ 本益比(證交所本益比 = 收盤價 ÷ 近四季 EPS),
     每天涵蓋全體上市股,不受財報申報期(各公司陸續申報)影響,也沒有季節累計的落差。
     新財報公布後排名自然更新,新進榜的公司加入;已在池中的公司不因掉出排名而移除。
  2. 保留:入池後保留 HOLD_DAYS 天。到期時若仍在排名內,會以新的入池日重新加入。
  3. 買點:賺錢的公司不追高,等拉回。股價自 52 週高點回檔 ≥ PULLBACK_MIN %,
     且回到季線、半年線或年線(日線 60/120/240 日均線)±NEAR % 以內 = 回檔到支撐(買點)。
     回檔 ≥ DEEP_PCT % 但仍守住年線 = 深度回檔(分批布局)。
  4. 跌破年線 BREAK_PCT % 以上 = 長線轉弱,先觀望,不急著接。
  5. 停損:年線下方 STOP_PCT %;目標:52 週高點。

本模組只做規則式的機械判斷,所有價位僅供參考,不構成投資建議。
"""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

from .analysis import r
from .surge import is_common

TOP_N = 100          # EPS 排名取前幾名入池
HOLD_DAYS = 365      # 入池後保留天數(日曆日)
CHART_DAYS = 120     # 網站附的日線天數
PULLBACK_MIN = 10    # 自 52 週高點至少回檔 % 才算拉回
DEEP_PCT = 20        # 深度回檔 %
NEAR = 3             # 距離均線 ±% 以內算「回到支撐」
BREAK_PCT = 3        # 收盤低於年線超過 % = 跌破年線
STOP_PCT = 5         # 停損:年線下方 %
SUPPORTS = ((60, "季線"), (120, "半年線"), (240, "年線"))

STATUS_LABEL = {"buy": "回檔到支撐", "deep": "深度回檔", "pullback": "拉回中", "high": "接近高點",
                "broken": "跌破年線", "nodata": "無日線資料"}
STATUS_ORDER = ["buy", "deep", "pullback", "high", "broken"]


def rank_eps(valuation: dict, px: dict, names: dict) -> tuple[list[dict], str | None]:
    """近四季 EPS(收盤價 ÷ 本益比)由高到低排名前 TOP_N。虧損股沒有本益比,自然排除。"""
    rows = []
    for c, v in (valuation or {}).items():
        pe, close = v.get("pe"), (px.get(c) or {}).get("close")
        if is_common(c) and pe and pe > 0 and close:
            rows.append({"code": c, "name": names.get(c, c), "eps": round(close / pe, 2)})
    if not rows:
        return [], None
    rows = sorted(rows, key=lambda x: -x["eps"])[:TOP_N]
    for i, x in enumerate(rows, 1):
        x["rank"] = i
    return rows, "近四季"


def pool_active(pool: dict, today: str) -> dict:
    """去掉已過期(入池超過 HOLD_DAYS 天)的公司。"""
    return {c: p for c, p in (pool or {}).items() if p.get("expires", "") >= today}


def update_pool(pool: dict, ranked: list[dict], period: str | None, today: str, px: dict) -> dict:
    """新進榜的公司加入;已在池中的更新最新排名。ranked 為空(財報抓取失敗)時只保留原池。"""
    pool = dict(pool)
    in_rank = {x["code"]: x for x in ranked}
    for c, x in in_rank.items():
        p = pool.get(c)
        if p is None:
            p = pool[c] = {"name": x["name"], "added": today,
                           "expires": (date.fromisoformat(today) + timedelta(days=HOLD_DAYS)).isoformat(),
                           "add_rank": x["rank"], "add_eps": x["eps"], "add_period": period,
                           "add_close": (px.get(c) or {}).get("close")}
        p.update({"name": x["name"], "rank": x["rank"], "eps": x["eps"], "period": period})
    if ranked:
        for c, p in pool.items():
            p["in_rank"] = c in in_rank
    return pool


def _ma(c: list[float], n: int, i: int):
    return sum(c[i - n + 1:i + 1]) / n if i >= n - 1 else None


def analyze(bars: list[dict]) -> dict | None:
    """回檔判斷與參考價位。bars 依日期排序,至少要 60 根才判斷。"""
    if not bars or len(bars) < 60:
        return None
    c = [b["close"] for b in bars]
    i = len(c) - 1
    close = c[i]
    high52 = max(b["high"] for b in bars[-250:])
    dd = (close / high52 - 1) * 100
    mas = {n: _ma(c, n, i) for n, _ in SUPPORTS}
    ma240 = mas[240]
    ma240_prev = _ma(c, 240, i - 20)
    sup = []
    for n, label in SUPPORTS:
        m = mas[n]
        if m:
            sup.append({"label": label, "days": n, "ma": r(m), "dist": r((close / m - 1) * 100, 1)})
    near = [s for s in sup if abs(s["dist"]) <= NEAR]
    if ma240 and close < ma240 * (1 - BREAK_PCT / 100):
        status = "broken"
    elif dd <= -PULLBACK_MIN and near:
        status = "buy"
    elif dd <= -DEEP_PCT:
        status = "deep"
    elif dd <= -PULLBACK_MIN / 2:
        status = "pullback"
    else:
        status = "high"
    # 參考買點:目前價位附近的支撐;還沒拉到支撐時,用下方最近的一條均線
    below = [s for s in sup if s["ma"] <= close * (1 + NEAR / 100)]
    key = near[0] if near else max(below, key=lambda s: s["ma"]) if below else None
    stop = ma240 * (1 - STOP_PCT / 100) if ma240 else (key["ma"] * (1 - STOP_PCT / 100) if key else None)
    return {
        "close": close, "chg_pct": r((close / c[i - 1] - 1) * 100) if i else None,
        "date": bars[-1]["date"], "high52": r(high52), "dd": r(dd, 1),
        "supports": sup, "ma240_up": (ma240 > ma240_prev) if ma240 and ma240_prev else None,
        "status": status, "signal": status in ("buy", "deep"),
        "plan": {
            "support": key["label"] if key else None,
            "entry_lo": r(key["ma"] * (1 - NEAR / 100)) if key else None,
            "entry_hi": r(key["ma"] * (1 + NEAR / 100)) if key else None,
            "stop": r(stop) if stop and status != "broken" else None,
            "target": r(high52),
        },
    }


def _series(bars: list[dict], dates: list[str]) -> dict:
    """對齊到共用日期軸,附季線/半年線/年線(停牌日為 null)。"""
    c = [b["close"] for b in bars]
    idx = {b["date"]: k for k, b in enumerate(bars)}
    out = {"close": []}
    for n, _ in SUPPORTS:
        out[f"ma{n}"] = []
    for d in dates:
        k = idx.get(d)
        out["close"].append(c[k] if k is not None else None)
        for n, _ in SUPPORTS:
            m = _ma(c, n, k) if k is not None else None
            out[f"ma{n}"].append(r(m) if m else None)
    return out


def build_zone(ctx) -> dict | None:
    q = ctx.get("quality")
    if not q:
        return None
    daily = ctx["daily"]
    today, latest = daily[-1]
    px = latest.get("px") or {}
    ranked, period = rank_eps(ctx.get("valuation"), px, ctx.get("names") or {})
    pool = update_pool(pool_active(q.get("pool") or {}, today), ranked, period, today, px)
    dates = [d for d, _ in daily][-CHART_DAYS:]
    if q.get("bars"):  # 日期軸以日線最長的一檔為準(快照只保留 45 天)
        longest = max(q["bars"].values(), key=len)
        dates = [b["date"] for b in longest][-CHART_DAYS:]
        if daily and dates and dates[-1] < today:
            dates = dates[1:] + [today]
    val = ctx.get("valuation") or {}
    rows = []
    for c, p in pool.items():
        bars = (q.get("bars") or {}).get(c) or []
        sp = px.get(c)
        if sp and sp.get("open") and (not bars or bars[-1]["date"] < today):  # Yahoo 少了最新一天,用 TWSE 收盤補上
            bars = bars + [{"date": today, "open": sp["open"], "high": sp["high"], "low": sp["low"], "close": sp["close"]}]
        a = analyze(bars)
        v = val.get(c) or {}
        row = {"code": c, "name": p["name"], "industry": (ctx.get("industries") or {}).get(c),
               "rank": p.get("rank"), "eps": p.get("eps"), "period": p.get("period"), "in_rank": p.get("in_rank", True),
               "added": p["added"], "expires": p["expires"],
               "days_left": (date.fromisoformat(p["expires"]) - date.fromisoformat(today)).days,
               "add_close": p.get("add_close"), "pe": v.get("pe"), "yield": v.get("yield")}
        if a:
            row.update(a)
            row["since_add"] = r((a["close"] / p["add_close"] - 1) * 100, 1) if p.get("add_close") else None
            row["series"] = _series(bars, dates)
        else:
            row.update({"status": "nodata", "signal": False, "close": (sp or {}).get("close")})
        rows.append(row)
    order = {k: i for i, k in enumerate(STATUS_ORDER + ["nodata"])}
    rows.sort(key=lambda x: (order[x["status"]], x.get("dd") if x.get("dd") is not None else 0))
    return {
        "rows": rows, "dates": dates, "period": period, "ranked": len(ranked),
        "counts": {k: sum(1 for x in rows if x["status"] == k) for k in STATUS_ORDER},
        "status_label": STATUS_LABEL, "status_order": STATUS_ORDER,
        "pool": pool,
        "rule": {"top_n": TOP_N, "hold_days": HOLD_DAYS, "pullback": PULLBACK_MIN, "deep": DEEP_PCT,
                 "near": NEAR, "break": BREAK_PCT, "stop": STOP_PCT},
    }
