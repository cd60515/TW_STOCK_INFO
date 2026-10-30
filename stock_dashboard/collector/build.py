"""把收集到的原始資料整理成網站使用的 dashboard.json。"""
from __future__ import annotations

import os

import pandas as pd

from . import analysis as A
from . import indicators as I
from . import news as N

CHART_BARS = 260  # 網站上顯示約一年的 K 線
SERIES_COLS = ["open", "high", "low", "close", "vol_lots", "ma5", "ma10", "ma20", "ma60", "ma120", "ma240",
               "k", "d", "dif", "dea", "osc", "rsi", "bb_up", "bb_dn"]


def _bars_df(bars):
    df = pd.DataFrame(bars)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return I.compute(df.dropna(subset=["close"]))


def _series(df):
    tail = df.tail(CHART_BARS)
    out = {"date": tail["date"].tolist()}
    for col in SERIES_COLS:
        nd = 0 if col == "vol_lots" else 2
        out[col] = [A.r(v, nd) for v in tail[col].tolist()]
    return out


def build_stock(s, ctx):
    code = s["code"]
    bars = ctx["prices"].get(code)
    if not bars or len(bars) < 30:
        return None
    df = _bars_df(bars)
    last, prev = df.iloc[-1], df.iloc[-2]
    name = s.get("name") or ctx["names"].get(code, code)

    chips = A.chip_summary(code, ctx["daily"], df) if s.get("market", "TWSE") == "TWSE" else None
    news_items = N.tag(ctx["news"].get(code, []))
    news_sum = N.summarize(news_items)
    valuation = ctx["valuation"].get(code)
    revenue = ctx["revenue"].get(code)

    score, breakdown, sig, parts = A.score_stock(df, chips, news_sum, valuation, revenue)
    label, cls = A.rating(score)
    plan = A.trade_plan(df, score)
    events = A.signal_events(df)
    bt = A.backtest_signals(df, events)
    for b in bt:
        if b["active"]:
            sig.insert(0, {"type": b["side"], "text": f"今日觸發「{b['name']}」"
                           + (f"(本股歷史 {b['n10']} 次,10 日後勝率 {b['win10']:.0f}%)" if b["n10"] else "")})
    view = A.analyst_view(name, df, label, plan, chips, sig)

    def chg_n(n):
        return A.r((last["close"] / df["close"].iloc[-n - 1] - 1) * 100, 2) if len(df) > n else None

    return {
        "code": code, "name": name, "market": s.get("market", "TWSE"),
        "industry": ctx["industries"].get(code),
        "date": last["date"], "close": A.r(last["close"]),
        "chg": A.r(last["close"] - prev["close"]), "chg_pct": A.r(last["chg_pct"]),
        "chg5": chg_n(5), "chg20": chg_n(20), "chg60": chg_n(60), "chg240": chg_n(240),
        "vol": A.r(last["vol_lots"], 0), "vol_ratio": A.r(last["vol_lots"] / last["vma20"], 2) if last["vma20"] else None,
        "high52": A.r(last["hh250"]), "low52": A.r(last["ll250"]),
        "k": A.r(last["k"], 1), "d": A.r(last["d"], 1), "rsi": A.r(last["rsi"], 1),
        "osc": A.r(last["osc"]), "ma20": A.r(last["ma20"]), "ma60": A.r(last["ma60"]),
        "score": score, "rating": label, "rating_cls": cls, "parts": parts,
        "breakdown": breakdown, "signals": sig, "plan": plan, "view": view,
        "backtest": bt, "chips": chips, "valuation": valuation, "revenue": revenue,
        "news": news_items, "news_summary": news_sum,
        "series": _series(df),
    }


def build(ctx, watchlist):
    stocks = []
    for s in watchlist:
        try:
            st = build_stock(s, ctx)
        except Exception as e:  # 單一股票異常不應讓整個網站失敗
            print(f"[warn] {s['code']} 分析失敗: {e!r}")
            st = None
        if st:
            stocks.append(st)

    idx_df = _bars_df(ctx["index_bars"])
    daily = ctx["daily"]
    latest = daily[-1][1] if daily else {}
    px = latest.get("px") or {}
    common = [p for c, p in px.items() if len(c) == 4 and not c.startswith("00")]
    breadth = {
        "up": sum(1 for p in common if (p.get("chg_pct") or 0) > 0),
        "down": sum(1 for p in common if (p.get("chg_pct") or 0) < 0),
        "flat": sum(1 for p in common if (p.get("chg_pct") or 0) == 0),
        "limit_up": sum(1 for p in common if (p.get("chg_pct") or 0) >= 9.5),
        "limit_down": sum(1 for p in common if (p.get("chg_pct") or 0) <= -9.5),
        "turnover": A.r(sum(p.get("turnover") or 0 for p in px.values()), 0),
    } if px else None
    bfi_hist = [{"date": d, **s["bfi"]} for d, s in daily if s.get("bfi")]
    margin_hist = [{"date": d, "value": s["market_margin"]} for d, s in daily if s.get("market_margin")]
    regime = A.market_regime(idx_df, bfi_hist, margin_hist, breadth)
    il, ip = idx_df.iloc[-1], idx_df.iloc[-2]

    top_turnover = sorted(((c, p) for c, p in px.items() if len(c) == 4), key=lambda x: -(x[1].get("turnover") or 0))[:15]
    names = ctx["names"]
    market = {
        "date": daily[-1][0] if daily else il["date"],
        "index": {"close": A.r(il["close"]), "chg": A.r(il["close"] - ip["close"]), "chg_pct": A.r(il["chg_pct"]),
                  "ma20": A.r(il["ma20"]), "ma60": A.r(il["ma60"]), "k": A.r(il["k"], 1), "d": A.r(il["d"], 1),
                  "rsi": A.r(il["rsi"], 1), "date": il["date"]},
        "index_series": _series(idx_df),
        "regime": regime, "breadth": breadth,
        "bfi": bfi_hist[-20:], "margin": margin_hist[-20:],
        "sectors": A.sectors(px, latest.get("t86"), ctx["industries"]),
        "top_turnover": [{"code": c, "name": names.get(c, c), "close": p["close"], "chg_pct": p["chg_pct"],
                          "turnover": p["turnover"], "foreign": (latest.get("t86") or {}).get(c, [None])[0]}
                         for c, p in top_turnover],
        "news": N.tag(ctx["market_news"]),
    }
    screens = A.screener(daily, ctx["valuation"], ctx["revenue"], names)
    stocks.sort(key=lambda x: -x["score"])
    return {
        "demo": ctx["demo"],
        "generated_at": ctx["generated_at"],
        "data_complete": ctx["data_complete"],
        "holidays": ctx.get("holidays") or [],
        "actions_url": (f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/workflows/stock-dashboard.yml"
                        if os.environ.get("GITHUB_REPOSITORY") else None),
        "market": market,
        "stocks": stocks,
        "screens": screens,
    }
