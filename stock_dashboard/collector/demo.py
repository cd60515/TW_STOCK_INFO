"""示範資料產生器:無網路環境下測試整條流程、預覽網站版面用。

產生的價格/籌碼/新聞全部是隨機模擬值,網站上會明確標示「示範資料」。
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

from .sources import TAIPEI
from .surge import find_candidates, limit_up_price, tick

HEADLINES_POS = ["{n}法說會釋利多 第四季營收展望優於預期", "外資調升{n}目標價 看好AI訂單滿載",
                 "{n}月營收年增逾三成 創同期新高", "{n}獲大單 擴產計畫加速", "{n}股價強漲 站上季線"]
HEADLINES_NEG = ["{n}毛利率下修 法人看淡短線", "{n}遭外資降評 股價走弱", "{n}月營收年減 庫存調整未完"]
HEADLINES_NEU = ["{n}股東會今登場 董事長談明年布局", "{n}參加國際展會 展示新產品", "{n}公告董事會決議事項"]


def _walk(rng, n, start, drift, vol):
    bars, c = [], start
    day = datetime.now(TAIPEI).date() - timedelta(days=int(n * 1.45))
    while len(bars) < n:
        day += timedelta(days=1)
        if day.weekday() >= 5:
            continue
        o = c * (1 + rng.gauss(0, vol * 0.3))
        c = max(1.0, c * (1 + rng.gauss(drift, vol)))
        h = max(o, c) * (1 + abs(rng.gauss(0, vol * 0.4)))
        l = min(o, c) * (1 - abs(rng.gauss(0, vol * 0.4)))
        bars.append({"date": day.isoformat(), "open": round(o, 2), "high": round(h, 2), "low": round(l, 2),
                     "close": round(c, 2), "volume": int(abs(rng.gauss(1, 0.35)) * 2e7 / max(c, 10) * 100)})
    return bars


def collect_demo(watchlist):
    rng = random.Random(20261030)
    n = 500
    index_bars = _walk(rng, n, 17000, 0.0007, 0.011)
    dates = [b["date"] for b in index_bars]

    prices, news = {}, {}
    starts = {"2330": 650, "2317": 120, "2454": 900, "2308": 280, "2382": 180, "3231": 90, "2345": 450,
              "3017": 600, "2881": 70, "2882": 55, "2603": 180, "1519": 400, "0050": 140}
    for i, s in enumerate(watchlist):
        bars = _walk(rng, n, starts.get(s["code"], 100), rng.uniform(-0.0004, 0.0016), rng.uniform(0.013, 0.026))
        for b, d in zip(bars, dates):
            b["date"] = d
        prices[s["code"]] = bars
        items = []
        for j in range(rng.randint(5, 10)):
            pool = rng.choice([HEADLINES_POS, HEADLINES_POS, HEADLINES_NEU, HEADLINES_NEG])
            t = datetime.now(TAIPEI) - timedelta(hours=rng.randint(1, 160))
            items.append({"title": "【示範】" + rng.choice(pool).format(n=s["name"]), "source": "示範新聞",
                          "link": "#", "time": t.strftime("%Y-%m-%d %H:%M")})
        news[s["code"]] = sorted(items, key=lambda x: x["time"], reverse=True)

    # 全市場:自選股 + 300 檔虛擬股票
    universe = {s["code"]: s["name"] for s in watchlist}
    for k in range(300):
        universe[str(4100 + k * 7)] = f"示範{k:03d}"
    walks = {c: (prices[c] if c in prices else _walk(rng, 30, rng.uniform(15, 400), rng.uniform(-0.004, 0.006), 0.025))
             for c in universe}
    # 飆股專區示範:植入幾種「連續跳空漲停」型態(日線較長,才有一年基期與歷史回測)
    plants = {  # code: (名稱, 型態結束距今天數, 連板數, 之後走勢)
        "6101": ("示範飆股甲", 6, 3, "hold"), "6102": ("示範飆股乙", 0, 2, "run"),
        "6103": ("示範飆股丙", 0, 1, "run"), "6104": ("示範飆股丁", 9, 3, "fail"),
        "6105": ("示範飆股戊", 11, 4, "breakout"), "6106": ("示範飆股己", 4, 4, "hold"),
        "6107": ("示範飆股庚", 5, 2, "hold"),  # 長期空頭裡的反彈:上方壓著年線 → 大均線壓力
    }
    for code, (nm, ago, nlu, after) in plants.items():
        w = _walk(rng, 300, rng.uniform(20, 120), -0.003 if code == "6107" else 0.0002, 0.012)
        for b, d in zip(w, dates[-300:]):
            b["date"] = d
        if code in ("6101", "6106"):
            _plant(rng, w, 150, 3, "hold", tail=30)  # 歷史上的同型態,供回測
        _plant(rng, w, 300 - 1 - ago - (nlu - 1), nlu, after)
        universe[code] = nm
        walks[code] = w
    industries_list = ["半導體", "電腦週邊", "電子零組件", "通信網路", "金融保險", "航運", "電機機械", "生技醫療", "光電", "鋼鐵"]
    industries = {c: rng.choice(industries_list) for c in universe}
    for c, ind in (("2330", "半導體"), ("2454", "半導體"), ("2317", "其他電子"), ("2382", "電腦週邊"),
                   ("3231", "電腦週邊"), ("2881", "金融保險"), ("2882", "金融保險"), ("2603", "航運")):
        industries[c] = ind

    daily = []
    margins = {c: rng.randint(2000, 40000) for c in universe}
    bias = {c: (rng.gauss(0.2, 1), rng.gauss(0.1, 1)) for c in universe}
    for di, d in enumerate(dates[-25:]):
        px, t86, mg = {}, {}, {}
        for c, w in walks.items():
            b, pb = w[-25 + di], w[-26 + di]
            vol = b["volume"] / 1000 * (3 if rng.random() < 0.03 else 1)
            px[c] = {"close": b["close"], "chg_pct": round((b["close"] / pb["close"] - 1) * 100, 2), "vol": round(vol),
                     "turnover": round(vol * 1000 * b["close"] / 1e8, 2), "open": b["open"], "high": b["high"], "low": b["low"],
                     "name": universe[c]}
            fb, tb = bias[c]
            t86[c] = [round(rng.gauss(fb, 1.2) * vol * 0.05), round(rng.gauss(tb, 1) * vol * 0.02),
                      round(rng.gauss(0, 1) * vol * 0.01), 0]
            t86[c][3] = sum(t86[c][:3])
            margins[c] = max(100, int(margins[c] * (1 + rng.gauss(0, 0.02))))
            mg[c] = [margins[c], margins[c], int(margins[c] * rng.uniform(0.02, 0.3)), 0]
        bfi = {"foreign": round(rng.gauss(20, 120), 2), "trust": round(rng.gauss(5, 20), 2),
               "dealer": round(rng.gauss(0, 30), 2)}
        bfi["total"] = round(sum(bfi.values()), 2)
        daily.append((d, {"date": d, "px": px, "t86": t86, "margin": mg, "bfi": bfi,
                          "market_margin": round(3000 + di * rng.uniform(-5, 12), 1),
                          "qfii": {c: round(rng.uniform(5, 75), 2) for c in universe}}))

    valuation = {c: {"pe": round(rng.uniform(6, 40), 2), "yield": round(rng.uniform(0, 8), 2), "pb": round(rng.uniform(0.6, 8), 2)}
                 for c in universe}
    valuation["0050"] = {"pe": None, "yield": 2.1, "pb": None}
    revenue = {c: {"ym": "11509", "rev": round(rng.uniform(5, 3000), 2), "mom": round(rng.gauss(2, 10), 2),
                   "yoy": round(rng.gauss(12, 25), 2), "cum_yoy": round(rng.gauss(10, 18), 2)} for c in universe}
    market_news = []
    for k in range(12):
        t = datetime.now(TAIPEI) - timedelta(hours=k * 3 + 1)
        name = rng.choice(list(universe.values())[:13])
        pool = rng.choice([HEADLINES_POS, HEADLINES_NEU, HEADLINES_NEG])
        market_news.append({"title": "【示範】" + rng.choice(pool).format(n=name), "source": "示範新聞",
                            "link": "#", "time": t.strftime("%Y-%m-%d %H:%M")})
    vix = _walk(rng, 250, 17, 0.0005, 0.05)
    for b, d in zip(vix, dates[-250:]):
        b["date"] = d
    oil = {}
    for key, start in (("wti", 72), ("brent", 76)):
        oil[key] = _walk(random.Random(start), 250, start, 0.0004, 0.018)
        for b, d in zip(oil[key], dates[-250:]):
            b["date"] = d
    # 績優股池示範:自選股給較高 EPS(有完整日線),其餘虛擬股票隨機
    eps = {c: {"name": nm, "eps": round(rng.uniform(8, 45) if c in prices else rng.uniform(-2, 12), 2), "year": "115", "q": "3"}
           for c, nm in universe.items()}
    cands = find_candidates(daily)
    surge_news = {c: [{"title": "【示範】" + rng.choice(HEADLINES_POS).format(n=universe[c]), "source": "示範新聞", "link": "#",
                       "time": datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M")}] for c in cands}
    surge = {"candidates": cands, "bars": {c: walks[c] for c in cands if len(walks[c]) >= 60},
             "news": surge_news, "vix": vix, "oil": oil, "alerts": {"6106": "注意"}}
    return {
        "surge": surge,
        "quality": {"eps": eps, "pool": {}, "bars": {c: prices[c] for c in prices}},
        "demo": True,
        "generated_at": datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M"),
        "index_bars": index_bars, "daily": daily, "prices": prices,
        "valuation": valuation, "revenue": revenue, "industries": industries,
        "names": universe, "news": news, "market_news": market_news, "data_complete": True,
    }


def _plant(rng, w, start, nlu, after, tail=None):
    """在日線 w 的 start 位置植入 nlu 根跳空漲停,之後依 after 決定走勢(hold/run/fail/breakout)。"""
    n = len(w)
    end = start + nlu - 1
    b0 = w[start - 1]  # 真實股價一定落在升降單位上,示範資料也對齊
    t = tick(b0["close"])
    b0["close"] = round(round(b0["close"] / t) * t, 2)
    b0["high"] = max(b0["high"], b0["close"])
    b0["low"] = min(b0["low"], b0["close"])
    for i in range(start, end + 1):
        prev = w[i - 1]
        c = limit_up_price(prev["close"])
        o = round(max(prev["high"] * 1.01, c * 0.97), 2) if i > start else round(max(prev["high"] * 1.005, prev["close"] * 1.03), 2)
        o = min(o, c)
        low = o if i == start else c if i == end - 1 else o
        w[i].update({"open": o, "high": c, "low": round(min(low, o), 2), "close": c,
                     "volume": int(w[i - 1]["volume"] * (2.5 if i == start else 0.8))})
    peak = w[end]["close"]
    gap_bot = w[start - 1]["high"]
    stop_at = n if tail is None else min(n, end + 1 + tail)
    for j, i in enumerate(range(end + 1, stop_at)):
        prev = w[i - 1]["close"]
        if after == "fail":
            c = prev * (0.955 if j < 6 else 1 + rng.gauss(0, 0.01))
        elif after == "breakout" and i == n - 1:
            c = max(x["close"] for x in w[start:i]) * 1.04
        elif after in ("hold", "breakout"):
            c = max(gap_bot * 1.04, prev * (1 + rng.gauss(-0.004 if j < 5 else 0.002, 0.015)))
            c = min(c, peak * 1.03)
        else:
            c = prev * (1 + rng.gauss(0, 0.012))
        o = prev * (1 + rng.gauss(0, 0.006))
        vol = w[start]["volume"] * (1.6 if j == 0 else 0.35 if after != "fail" else 0.9)
        w[i].update({"open": round(o, 2), "close": round(c, 2), "high": round(max(o, c) * 1.01, 2),
                     "low": round(max(min(o, c) * 0.99, gap_bot * 1.005) if after != "fail" else min(o, c) * 0.99, 2),
                     "volume": int(vol)})
    if tail is not None:  # 歷史型態之後的走勢接回原本的隨機漫步
        shift = w[stop_at - 1]["close"] / w[stop_at]["close"] if stop_at < n else 1
        for i in range(stop_at, n):
            for k in ("open", "high", "low", "close"):
                w[i][k] = round(w[i][k] * shift, 2)
