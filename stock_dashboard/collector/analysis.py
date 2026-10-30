"""分析引擎:訊號偵測、綜合評分、交易計畫、訊號歷史回測、大盤多空、選股雷達、類股強弱。

評分是規則式、可解釋的加權模型,每一項加減分都會列在 breakdown 中,方便判讀為何得到
這個分數;它是「整理資訊的工具」,不是預測模型,也不構成投資建議。
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def r(x, n=2):
    if x is None:
        return None
    try:
        if isinstance(x, (float, np.floating)) and (math.isnan(x) or math.isinf(x)):
            return None
        return round(float(x), n)
    except (TypeError, ValueError):
        return None


def _cross_up(a: pd.Series, b) -> pd.Series:
    b = b if isinstance(b, pd.Series) else pd.Series(b, index=a.index)
    return (a > b) & (a.shift(1) <= b.shift(1))


def _cross_dn(a: pd.Series, b) -> pd.Series:
    b = b if isinstance(b, pd.Series) else pd.Series(b, index=a.index)
    return (a < b) & (a.shift(1) >= b.shift(1))


# ---------------------------------------------------------------- 訊號事件(可回測)

def signal_events(df: pd.DataFrame) -> dict[str, tuple[str, pd.Series]]:
    """回傳 {訊號名稱: (多/空, 布林序列)},用於「今日訊號」與「歷史勝率」。"""
    c = df["close"]
    return {
        "KD 低檔黃金交叉": ("bull", _cross_up(df["k"], df["d"]) & (df["k"].shift(1) < 30)),
        "均線黃金交叉 (5/20)": ("bull", _cross_up(df["ma5"], df["ma20"])),
        "MACD 柱狀翻紅": ("bull", _cross_up(df["osc"], 0)),
        "帶量突破 20 日高": ("bull", (c > df["hh20"].shift(1)) & (df["vol_lots"] > 1.5 * df["vma20"].shift(1))),
        "站上季線": ("bull", _cross_up(c, df["ma60"])),
        "RSI 超賣回升": ("bull", _cross_up(df["rsi"], 30)),
        "KD 高檔死亡交叉": ("bear", _cross_dn(df["k"], df["d"]) & (df["k"].shift(1) > 70)),
        "均線死亡交叉 (5/20)": ("bear", _cross_dn(df["ma5"], df["ma20"])),
        "MACD 柱狀翻綠": ("bear", _cross_dn(df["osc"], 0)),
        "跌破月線": ("bear", _cross_dn(c, df["ma20"])),
        "帶量跌破 20 日低": ("bear", (c < df["ll20"].shift(1)) & (df["vol_lots"] > 1.5 * df["vma20"].shift(1))),
    }


def backtest_signals(df: pd.DataFrame, events) -> list[dict]:
    """每個訊號在本股歷史上出現後 5/10/20 日的報酬統計。
    多方訊號以「之後上漲」為勝,空方訊號以「之後下跌」為勝(=訊號有效)。"""
    c = df["close"].to_numpy()
    n = len(c)
    out = []
    for name, (side, mask) in events.items():
        idx = np.flatnonzero(mask.fillna(False).to_numpy())
        row = {"name": name, "side": side, "count": int(len(idx)),
               "active": bool(len(idx) and idx[-1] >= n - 3)}
        for h in (5, 10, 20):
            rets = [(c[i + h] / c[i] - 1) * 100 for i in idx if i + h < n]
            if rets:
                wins = sum(1 for x in rets if (x > 0 if side == "bull" else x < 0))
                row[f"win{h}"] = r(wins / len(rets) * 100, 0)
                row[f"avg{h}"] = r(float(np.mean(rets)), 2)
                row[f"n{h}"] = len(rets)
            else:
                row[f"win{h}"] = row[f"avg{h}"] = None
                row[f"n{h}"] = 0
        out.append(row)
    return out


# ---------------------------------------------------------------- 籌碼

def chip_summary(code: str, daily: list[tuple[str, dict]], df: pd.DataFrame | None):
    """由每日快照(由舊到新)整理單一股票的籌碼序列與統計。"""
    rows = []
    for date, snap in daily:
        t = (snap.get("t86") or {}).get(code)
        m = (snap.get("margin") or {}).get(code)
        if t is None and m is None:
            continue
        rows.append({
            "date": date,
            "foreign": t[0] if t else None, "trust": t[1] if t else None,
            "dealer": t[2] if t else None, "total": t[3] if t else None,
            "margin": m[0] if m else None, "short": m[2] if m else None,
            "foreign_hold": (snap.get("qfii") or {}).get(code),
        })
    if not rows:
        return None

    def ssum(key, n):
        vals = [x[key] for x in rows[-n:] if x[key] is not None]
        return sum(vals) if vals else None

    def streak(key):
        s = 0
        for x in reversed(rows):
            v = x[key]
            if v is None:
                break
            if v > 0 and s >= 0:
                s += 1
            elif v < 0 and s <= 0:
                s -= 1
            else:
                break
        return s

    margins = [x["margin"] for x in rows if x["margin"] is not None]
    shorts = [x["short"] for x in rows if x["short"] is not None]
    margin_chg5 = None
    if len(margins) >= 6 and margins[-6]:
        margin_chg5 = (margins[-1] / margins[-6] - 1) * 100
    vol5 = None
    if df is not None and len(df) >= 5:
        vol5 = float(df["vol_lots"].tail(5).sum())
    f5, t5 = ssum("foreign", 5), ssum("trust", 5)
    holds = [x["foreign_hold"] for x in rows if x["foreign_hold"] is not None]
    return {
        "series": rows[-60:],
        "foreign5": f5, "trust5": t5, "dealer5": ssum("dealer", 5), "total5": ssum("total", 5),
        "foreign20": ssum("foreign", 20), "trust20": ssum("trust", 20), "total20": ssum("total", 20),
        "foreign_streak": streak("foreign"), "trust_streak": streak("trust"),
        "foreign5_pct_vol": r(f5 / vol5 * 100, 1) if (f5 is not None and vol5) else None,
        "trust5_pct_vol": r(t5 / vol5 * 100, 1) if (t5 is not None and vol5) else None,
        "margin": margins[-1] if margins else None,
        "margin_chg5": r(margin_chg5, 2),
        "short": shorts[-1] if shorts else None,
        "short_ratio": r(shorts[-1] / margins[-1] * 100, 1) if (margins and shorts and margins[-1]) else None,
        "foreign_hold": holds[-1] if holds else None,
        "foreign_hold_chg": r(holds[-1] - holds[-6], 2) if len(holds) >= 6 else None,
        "days": len(rows),
    }


# ---------------------------------------------------------------- 綜合評分

def score_stock(df, chips, news_sum, valuation, revenue):
    """回傳 (總分 0-100, breakdown 清單, 今日訊號清單)。"""
    last, prev = df.iloc[-1], df.iloc[-2]
    c = last["close"]
    bd, sig = [], []

    def add(group, label, pts, maxpts):
        bd.append({"group": group, "label": label, "pts": r(pts, 1), "max": maxpts})
        return pts

    def s(kind, text):
        sig.append({"type": kind, "text": text})

    # --- 技術面:趨勢 (15)
    trend = 0
    above20, above60 = c > (last["ma20"] or 0), c > (last["ma60"] or 0)
    ma20_up = len(df) > 6 and last["ma20"] > df["ma20"].iloc[-6]
    bull_align = all(pd.notna(last[k]) for k in ("ma5", "ma10", "ma20", "ma60")) and \
        last["ma5"] > last["ma10"] > last["ma20"] > last["ma60"]
    bear_align = all(pd.notna(last[k]) for k in ("ma5", "ma10", "ma20", "ma60")) and \
        last["ma5"] < last["ma10"] < last["ma20"] < last["ma60"]
    trend += add("技術面", "收盤在月線之上" if above20 else "收盤在月線之下", 4 if above20 else 0, 4)
    trend += add("技術面", "收盤在季線之上" if above60 else "收盤在季線之下", 4 if above60 else 0, 4)
    trend += add("技術面", "月線上揚" if ma20_up else "月線下彎", 3 if ma20_up else 0, 3)
    trend += add("技術面", "均線多頭排列" if bull_align else ("均線空頭排列" if bear_align else "均線糾結"),
                 4 if bull_align else (0 if bear_align else 2), 4)
    if bull_align:
        s("bull", "均線多頭排列(5>10>20>60),趨勢偏多")
    if bear_align:
        s("bear", "均線空頭排列(5<10<20<60),趨勢偏空")

    # --- 技術面:動能 (15)
    k, d, rsi, osc = last["k"], last["d"], last["rsi"], last["osc"]
    mom = 0
    mom += add("技術面", f"KD {k:.0f}/{d:.0f}" + (" K>D" if k > d else " K<D"), 4 if k > d else 1, 4)
    mom += add("技術面", "MACD 柱狀體為正" if osc > 0 else "MACD 柱狀體為負", 4 if osc > 0 else 0, 4)
    mom += add("技術面", "MACD 柱狀體擴大" if osc > prev["osc"] else "MACD 柱狀體收斂",
               3 if osc > prev["osc"] else 0, 3)
    if rsi >= 80:
        rp, rl = 1, f"RSI {rsi:.0f} 過熱"
    elif rsi >= 50:
        rp, rl = 4, f"RSI {rsi:.0f} 多方區"
    elif rsi <= 25:
        rp, rl = 2, f"RSI {rsi:.0f} 超賣"
    else:
        rp, rl = 1, f"RSI {rsi:.0f} 弱勢區"
    mom += add("技術面", rl, rp, 4)
    if k > 80 and d > 80:
        s("info", f"KD 高檔({k:.0f}/{d:.0f}),強勢鈍化或過熱,追價需控管部位")
    elif k < 20 and d < 20:
        s("info", f"KD 低檔({k:.0f}/{d:.0f}),超賣區,留意止跌訊號")
    if rsi >= 80:
        s("bear", f"RSI {rsi:.0f} 嚴重過熱,短線拉回風險升高")
    elif rsi <= 25:
        s("bull", f"RSI {rsi:.0f} 超賣,具跌深反彈條件")

    # --- 技術面:量價型態 (10)
    vol_ratio = last["vol_lots"] / last["vma20"] if last["vma20"] else 1
    breakout = pd.notna(df["hh20"].iloc[-2]) and c > df["hh20"].iloc[-2]
    if breakout and vol_ratio >= 1.5:
        pv = add("技術面", f"帶量突破 20 日高(量比 {vol_ratio:.1f})", 10, 10)
        s("bull", f"帶量突破近 20 日高點,量比 {vol_ratio:.1f} 倍")
    elif last["chg_pct"] > 0 and vol_ratio >= 1.2:
        pv = add("技術面", f"價漲量增(量比 {vol_ratio:.1f})", 7, 10)
    elif last["chg_pct"] < 0 and vol_ratio >= 1.5:
        pv = add("技術面", f"價跌量增(量比 {vol_ratio:.1f}),賣壓沉重", 1, 10)
        s("bear", f"價跌量增(量比 {vol_ratio:.1f} 倍),留意主力出貨")
    elif last["chg_pct"] < 0 and vol_ratio < 0.8:
        pv = add("技術面", "價跌量縮,屬量縮整理", 5, 10)
    else:
        pv = add("技術面", f"量能平穩(量比 {vol_ratio:.1f})", 4, 10)
    if vol_ratio >= 2.5:
        s("info", f"爆量 {vol_ratio:.1f} 倍於 20 日均量,留意是否為轉折")

    if pd.notna(last["hh250"]) and c >= df["hh250"].iloc[-2]:
        s("bull", "股價創近一年新高,上方無套牢賣壓")
    if pd.notna(last["ll250"]) and c <= df["ll250"].iloc[-2]:
        s("bear", "股價創近一年新低,趨勢疲弱勿輕易接刀")
    if pd.notna(last["bb_up"]) and c > last["bb_up"]:
        s("info", "收盤突破布林上軌,強勢但短線乖離大")
    if pd.notna(last["bb_dn"]) and c < last["bb_dn"]:
        s("info", "收盤跌破布林下軌,弱勢但短線乖離大")
    bw = df["bb_width"].dropna().tail(120)
    if len(bw) >= 60 and last["bb_width"] <= bw.quantile(0.1):
        s("info", "布林通道極度壓縮,波動收斂,變盤在即")
    bias20 = (c / last["ma20"] - 1) * 100 if last["ma20"] else 0
    if bias20 > 15:
        s("bear", f"月線乖離 {bias20:.1f}%,短線漲多")
    elif bias20 < -15:
        s("bull", f"月線乖離 {bias20:.1f}%,短線跌深")

    tech = trend + mom + pv  # 0-40

    # --- 籌碼面 (35)
    chip_pts = None
    if chips and chips.get("days", 0) >= 3:
        cp = 0
        f5, t5 = chips["foreign5"] or 0, chips["trust5"] or 0
        cp += add("籌碼面", f"外資 5 日{'買' if f5 >= 0 else '賣'}超 {abs(f5):,.0f} 張", 8 if f5 > 0 else (4 if f5 == 0 else 0), 8)
        cp += add("籌碼面", f"投信 5 日{'買' if t5 >= 0 else '賣'}超 {abs(t5):,.0f} 張", 8 if t5 > 0 else (4 if t5 == 0 else 1), 8)
        fs, ts = chips["foreign_streak"], chips["trust_streak"]
        cp += add("籌碼面", f"外資連續{'買' if fs > 0 else '賣'}超 {abs(fs)} 日" if fs else "外資動向不明",
                  4 if fs >= 3 else (2 if fs > 0 else 0 if fs <= -3 else 1), 4)
        cp += add("籌碼面", f"投信連續{'買' if ts > 0 else '賣'}超 {abs(ts)} 日" if ts else "投信動向不明",
                  5 if ts >= 3 else (3 if ts > 0 else 0 if ts <= -3 else 1.5), 5)
        t20 = chips["total20"] or 0
        cp += add("籌碼面", f"三大法人 20 日合計{'買' if t20 >= 0 else '賣'}超 {abs(t20):,.0f} 張", 5 if t20 > 0 else 0, 5)
        mchg = chips.get("margin_chg5")
        price5 = (c / df["close"].iloc[-6] - 1) * 100 if len(df) > 6 else 0
        if mchg is None:
            mp, ml = 2.5, "融資資料不足"
        elif mchg < -3 and price5 > 0:
            mp, ml = 5, f"融資 5 日減 {abs(mchg):.1f}% 而股價上漲,籌碼沉澱"
        elif mchg > 5 and price5 < 0:
            mp, ml = 0, f"融資 5 日增 {mchg:.1f}% 而股價下跌,散戶接刀"
        elif mchg > 8:
            mp, ml = 1.5, f"融資 5 日暴增 {mchg:.1f}%,籌碼轉亂"
        else:
            mp, ml = 3, f"融資 5 日變化 {mchg:+.1f}%"
        cp += add("籌碼面", ml, mp, 5)
        chip_pts = cp
        if fs >= 3:
            s("bull", f"外資連續買超 {fs} 日")
        if fs <= -3:
            s("bear", f"外資連續賣超 {abs(fs)} 日")
        if ts >= 3:
            s("bull", f"投信連續買超 {ts} 日(作帳/認養行情)")
        if ts <= -3:
            s("bear", f"投信連續賣超 {abs(ts)} 日")
        if f5 > 0 and t5 > 0:
            s("bull", "外資、投信 5 日同步買超,土洋合力")
        if f5 < 0 and t5 < 0:
            s("bear", "外資、投信 5 日同步賣超")
        if mp == 0:
            s("bear", ml)
        if mp == 5:
            s("bull", ml)
        sr = chips.get("short_ratio")
        if sr and sr >= 20:
            s("info", f"券資比 {sr:.0f}%,若股價走強具軋空動能")

    # --- 消息面 (15)
    ns = news_sum or {"count": 0, "score": 0}
    news_pts = 7.5 + max(-1, min(1, ns["score"])) * 7.5 if ns["count"] else 7.5
    add("消息面", f"近期新聞 {ns['count']} 則,情緒{ns.get('label', '中性')}", news_pts, 15)
    if ns["count"] >= 3 and ns["score"] >= 0.5:
        s("bull", f"近期新聞情緒偏多({ns['pos']} 則正面)")
    if ns["count"] >= 3 and ns["score"] <= -0.5:
        s("bear", f"近期新聞情緒偏空({ns['neg']} 則負面)")

    # --- 基本面:評價 + 營收 (10)
    val_pts = 0
    v = valuation or {}
    pe, dy, pb = v.get("pe"), v.get("yield"), v.get("pb")
    if pe:
        val_pts += add("基本面", f"本益比 {pe:.1f}", 3 if pe < 12 else 2 if pe < 20 else 1 if pe < 30 else 0, 3)
    else:
        val_pts += add("基本面", "本益比無資料(虧損或 ETF)", 1, 3)
    if dy is not None:
        val_pts += add("基本面", f"殖利率 {dy:.2f}%", 2 if dy >= 5 else 1.5 if dy >= 3 else 0.5, 2)
    else:
        val_pts += add("基本面", "殖利率無資料", 1, 2)
    rev = revenue or {}
    yoy = rev.get("yoy")
    if yoy is not None:
        val_pts += add("基本面", f"月營收年增 {yoy:+.1f}%", 5 if yoy >= 20 else 3.5 if yoy >= 5 else 2 if yoy >= -5 else 0, 5)
        if yoy >= 30:
            s("bull", f"最新月營收年增 {yoy:.1f}%,高成長")
        elif yoy <= -15:
            s("bear", f"最新月營收年減 {abs(yoy):.1f}%")
    else:
        val_pts += add("基本面", "月營收無資料", 2.5, 5)

    if chip_pts is None:
        # 無籌碼資料(上櫃/ETF 等):以技術面等比放大補足,避免分數被系統性壓低
        total = tech * 75 / 40 + news_pts + val_pts
    else:
        total = tech + chip_pts + news_pts + val_pts
    return r(min(100, max(0, total)), 1), bd, sig, {
        "tech": r(tech / 40 * 100, 0),
        "chips": r(chip_pts / 35 * 100, 0) if chip_pts is not None else None,
        "news": r(news_pts / 15 * 100, 0),
        "fundamental": r(val_pts / 10 * 100, 0),
    }


def rating(score):
    if score >= 72:
        return "強勢偏多", "bull2"
    if score >= 58:
        return "偏多", "bull"
    if score >= 42:
        return "中性觀望", "neutral"
    if score >= 30:
        return "偏空", "bear"
    return "弱勢偏空", "bear2"


# ---------------------------------------------------------------- 交易計畫

def trade_plan(df: pd.DataFrame, score: float):
    last = df.iloc[-1]
    c, atr = float(last["close"]), float(last["atr"] or 0) or float(last["close"]) * 0.02
    supports = sorted({r(x) for x in (last["ma20"], last["ma60"], last["ll20"], last["bb_dn"], last["ma10"])
                       if pd.notna(x) and x < c}, reverse=True)
    resist = sorted({r(x) for x in (last["hh20"], last["hh60"], last["bb_up"], last["hh250"])
                     if pd.notna(x) and x > c * 1.003})
    sup1 = supports[0] if supports else r(c - 2 * atr)
    # 距離不到 1 ATR 的壓力位太近,當作「突破即將挑戰」而非獲利目標
    far = [x for x in resist if x >= c + atr]
    res1 = far[0] if far else r(c + 2 * atr)
    res2 = next((x for x in far if x >= res1 + atr), r(res1 + 1.5 * atr))

    stop = sup1 - 0.5 * atr
    if c - stop > 3 * atr or c - stop < 0.8 * atr:
        stop = c - 2 * atr
    risk = c - stop
    rr = (res1 - c) / risk if risk > 0 else None

    if score >= 58:
        stance = "偏多操作"
        entry = f"拉回 {r(max(sup1, c - atr))}~{r(c)} 分批布局,不追高"
    elif score >= 42:
        stance = "區間操作 / 觀望"
        entry = f"等待站穩 {res1} 或回測 {sup1} 有守再考慮"
    else:
        stance = "偏空,不宜進場"
        entry = f"空手觀望;持有者跌破 {r(stop)} 應減碼"
    return {
        "stance": stance, "entry": entry,
        "stop": r(stop), "stop_pct": r((stop / c - 1) * 100, 1),
        "target1": res1, "target1_pct": r((res1 / c - 1) * 100, 1),
        "target2": res2, "target2_pct": r((res2 / c - 1) * 100, 1),
        "rr": r(rr, 2), "atr": r(atr), "atr_pct": r(atr / c * 100, 2),
        "supports": supports[:3], "resistances": resist[:3],
    }


def analyst_view(name, df, rating_label, plan, chips, sig):
    """規則式分析師短評(3~4 句)。"""
    last = df.iloc[-1]
    c = last["close"]
    parts = []
    trend = ("多頭格局" if c > last["ma20"] > last["ma60"] else
             "空頭格局" if c < last["ma20"] < last["ma60"] else "區間整理")
    chg20 = (c / df["close"].iloc[-21] - 1) * 100 if len(df) > 21 else 0
    parts.append(f"{name} 目前呈{trend},近 20 日{'上漲' if chg20 >= 0 else '下跌'} {abs(chg20):.1f}%,"
                 f"KD {last['k']:.0f}/{last['d']:.0f}、RSI {last['rsi']:.0f}。")
    if chips and chips.get("days", 0) >= 3:
        f5, t5 = chips["foreign5"] or 0, chips["trust5"] or 0
        who = []
        who.append(f"外資近 5 日{'買' if f5 >= 0 else '賣'}超 {abs(f5):,.0f} 張")
        who.append(f"投信{'買' if t5 >= 0 else '賣'}超 {abs(t5):,.0f} 張")
        if chips.get("margin_chg5") is not None:
            who.append(f"融資 5 日{chips['margin_chg5']:+.1f}%")
        parts.append("籌碼面:" + "、".join(who) + "。")
    bulls = [x["text"] for x in sig if x["type"] == "bull"][:2]
    bears = [x["text"] for x in sig if x["type"] == "bear"][:2]
    if bulls:
        parts.append("加分:" + ";".join(bulls) + "。")
    if bears:
        parts.append("風險:" + ";".join(bears) + "。")
    if plan["stance"].startswith("偏空"):
        parts.append(f"綜合評等「{rating_label}」,{plan['stance']};持有者跌破 {plan['stop']}"
                     f"({plan['stop_pct']}%)應減碼,上方反彈壓力 {plan['target1']}。")
    else:
        parts.append(f"綜合評等「{rating_label}」,建議{plan['stance']};停損參考 {plan['stop']}"
                     f"({plan['stop_pct']}%),第一目標 {plan['target1']}(+{plan['target1_pct']}%)。")
    return "".join(parts)


# ---------------------------------------------------------------- 大盤

def market_regime(idx_df, bfi_hist, margin_hist, breadth):
    last = idx_df.iloc[-1]
    c = last["close"]
    pts, notes = 0, []

    def add(cond, p, yes, no):
        nonlocal pts
        if cond:
            pts += p
            notes.append({"type": "bull", "text": yes})
        else:
            notes.append({"type": "bear", "text": no})

    # 權重:中期趨勢(季線位置與方向)重於短期(月線)
    add(c > last["ma20"], 15, "加權指數站上月線", "加權指數跌破月線")
    add(c > last["ma60"], 20, "加權指數站上季線(中期多頭)", "加權指數在季線之下(中期偏弱)")
    add(last["ma20"] > idx_df["ma20"].iloc[-6], 10, "月線上揚", "月線下彎")
    add(last["ma60"] > idx_df["ma60"].iloc[-6], 20, "季線上揚(中期趨勢仍向上)", "季線下彎")
    add(last["osc"] > 0, 10, "MACD 柱狀體為正", "MACD 柱狀體為負")
    f5 = None
    if bfi_hist:
        f5 = sum(x["foreign"] for x in bfi_hist[-5:])
        add(f5 > 0, 15, f"外資近 5 日買超 {f5:,.0f} 億", f"外資近 5 日賣超 {abs(f5):,.0f} 億")
    else:
        pts += 7.5
    if breadth and breadth.get("up") is not None:
        add(breadth["up"] >= breadth["down"], 10, f"上漲家數 {breadth['up']} > 下跌 {breadth['down']}",
            f"下跌家數 {breadth['down']} > 上漲 {breadth['up']}")
    else:
        pts += 5
    if last["rsi"] > 80:
        notes.append({"type": "info", "text": f"指數 RSI {last['rsi']:.0f} 過熱,留意短線震盪"})
    bias = (c / last["ma20"] - 1) * 100
    if abs(bias) > 6:
        notes.append({"type": "info", "text": f"指數月線乖離 {bias:+.1f}%,{'漲多' if bias > 0 else '跌深'}易有反向修正"})
    if margin_hist and len(margin_hist) >= 6 and margin_hist[-6]["value"]:
        mc = (margin_hist[-1]["value"] / margin_hist[-6]["value"] - 1) * 100
        notes.append({"type": "info", "text": f"大盤融資餘額 {margin_hist[-1]['value']:,.0f} 億,5 日 {mc:+.1f}%"
                      + (",散戶追價偏熱" if mc > 3 else "")})

    if pts >= 75:
        label, cls, exposure = "多頭", "bull2", "70~90%"
    elif pts >= 55:
        label, cls, exposure = "偏多", "bull", "50~70%"
    elif pts >= 40:
        label, cls, exposure = "盤整", "neutral", "30~50%"
    elif pts >= 20:
        label, cls, exposure = "偏空", "bear", "10~30%"
    else:
        label, cls, exposure = "空頭", "bear2", "0~10%"
    return {"score": r(pts, 0), "label": label, "cls": cls, "exposure": exposure, "notes": notes,
            "foreign5": r(f5, 1)}


# ---------------------------------------------------------------- 全市場選股雷達

def screener(daily: list[tuple[str, dict]], valuation, revenue, names):
    if not daily:
        return []
    latest = daily[-1][1]
    px = latest.get("px") or {}
    t86 = latest.get("t86") or {}
    lists = []

    def row(code, metric, mlabel):
        p = px.get(code, {})
        return {"code": code, "name": names.get(code, p.get("name", code)), "close": p.get("close"),
                "chg_pct": p.get("chg_pct"), "turnover": p.get("turnover"), "metric": metric, "mlabel": mlabel}

    def liquid(code, min_turnover=0.5):
        p = px.get(code)
        return p is not None and (p.get("turnover") or 0) >= min_turnover and len(code) == 4

    # 法人連續買超天數
    def streak(code, idx):
        s = 0
        for _, snap in reversed(daily):
            v = (snap.get("t86") or {}).get(code)
            if not v or v[idx] <= 0:
                break
            s += 1
        return s

    def net_value(code, idx):  # 張 * 股價 -> 億
        v = t86.get(code)
        p = px.get(code, {}).get("close")
        return (v[idx] * p / 1e5) if (v and p) else 0

    both = [(c, net_value(c, 0) + net_value(c, 1)) for c, v in t86.items()
            if v[0] > 0 and v[1] > 0 and liquid(c)]
    both.sort(key=lambda x: x[1], reverse=True)
    lists.append({"key": "both_buy", "title": "土洋同買", "desc": "今日外資與投信同步買超,依合計買超金額排序",
                  "rows": [row(c, r(v, 2), "合買超(億)") for c, v in both[:15]]})

    trust = [(c, streak(c, 1)) for c, v in t86.items() if v[1] > 0 and liquid(c, 0.3)]
    trust = [(c, s_) for c, s_ in trust if s_ >= 3]
    trust.sort(key=lambda x: (x[1], net_value(x[0], 1)), reverse=True)
    lists.append({"key": "trust_streak", "title": "投信連買", "desc": "投信連續買超 3 日以上(投信認養股常有波段行情)",
                  "rows": [row(c, s_, "連買日數") for c, s_ in trust[:15]]})

    foreign = [(c, streak(c, 0)) for c, v in t86.items() if v[0] > 0 and liquid(c, 1)]
    foreign = [(c, s_) for c, s_ in foreign if s_ >= 4]
    foreign.sort(key=lambda x: (x[1], net_value(x[0], 0)), reverse=True)
    lists.append({"key": "foreign_streak", "title": "外資連買", "desc": "外資連續買超 4 日以上,成交值 1 億以上",
                  "rows": [row(c, s_, "連買日數") for c, s_ in foreign[:15]]})

    # 爆量長紅:漲幅 >= 4%、量 >= 前 5 日均量 2 倍
    vol_hist: dict[str, list] = {}
    for _, snap in daily[-6:-1]:
        for c, p in (snap.get("px") or {}).items():
            vol_hist.setdefault(c, []).append(p.get("vol") or 0)
    burst = []
    for c, p in px.items():
        hv = vol_hist.get(c)
        if not hv or not liquid(c, 1):
            continue
        avg = sum(hv) / len(hv)
        if avg > 0 and (p.get("chg_pct") or 0) >= 4 and p["vol"] >= 2 * avg:
            burst.append((c, p["vol"] / avg))
    burst.sort(key=lambda x: x[1], reverse=True)
    lists.append({"key": "volume_burst", "title": "爆量長紅", "desc": "漲幅 ≥ 4% 且成交量 ≥ 前 5 日均量 2 倍",
                  "rows": [row(c, r(v, 1), "量比") for c, v in burst[:15]]})

    # 20 日強勢股
    if len(daily) >= 15:
        base = daily[max(0, len(daily) - 20)][1].get("px") or {}
        rs = []
        for c, p in px.items():
            b = base.get(c, {}).get("close")
            if b and liquid(c, 2):
                rs.append((c, (p["close"] / b - 1) * 100))
        rs.sort(key=lambda x: x[1], reverse=True)
        lists.append({"key": "momentum", "title": "相對強勢", "desc": f"近 {min(20, len(daily))} 個交易日漲幅最大(成交值 2 億以上)",
                      "rows": [row(c, r(v, 1), "區間漲幅%") for c, v in rs[:15]]})

    # 融資減、股價漲:籌碼沉澱
    if len(daily) >= 6:
        m_now = latest.get("margin") or {}
        m_old = daily[-6][1].get("margin") or {}
        p_old = daily[-6][1].get("px") or {}
        clean = []
        for c, m in m_now.items():
            mo, po = m_old.get(c), p_old.get(c, {}).get("close")
            if mo and mo[0] >= 500 and po and liquid(c, 0.5):
                mchg = (m[0] / mo[0] - 1) * 100
                pchg = (px[c]["close"] / po - 1) * 100
                if mchg <= -8 and pchg > 0:
                    clean.append((c, mchg))
        clean.sort(key=lambda x: x[1])
        lists.append({"key": "margin_clean", "title": "籌碼沉澱", "desc": "融資 5 日減少 ≥ 8% 但股價上漲(散戶下車、主力掌控)",
                      "rows": [row(c, r(v, 1), "融資5日%") for c, v in clean[:15]]})

    # 存股:高殖利率、合理本益比
    if valuation:
        div = []
        for c, v in valuation.items():
            y, pe = v.get("yield"), v.get("pe")
            if y and pe and y >= 5 and 0 < pe <= 15 and liquid(c, 0.3):
                div.append((c, y))
        div.sort(key=lambda x: x[1], reverse=True)
        lists.append({"key": "dividend", "title": "高息低估", "desc": "殖利率 ≥ 5% 且本益比 ≤ 15(存股/防禦型參考)",
                      "rows": [row(c, v, "殖利率%") for c, v in div[:15]]})

    # 營收高成長
    if revenue:
        grow = []
        for c, v in revenue.items():
            if v.get("yoy") is not None and v.get("cum_yoy") is not None and v["yoy"] >= 30 \
                    and v["cum_yoy"] >= 20 and liquid(c, 0.5):
                grow.append((c, v["yoy"]))
        grow.sort(key=lambda x: x[1], reverse=True)
        lists.append({"key": "revenue_growth", "title": "營收雙高成長", "desc": "月營收年增 ≥ 30% 且累計年增 ≥ 20%",
                      "rows": [row(c, r(v, 1), "月營收年增%") for c, v in grow[:15]]})
    return lists


def sectors(px, t86, industry_map):
    if not px or not industry_map:
        return []
    agg = {}
    for c, p in px.items():
        ind = industry_map.get(c)
        if not ind or not p.get("turnover"):
            continue
        a = agg.setdefault(ind, {"name": ind, "turnover": 0.0, "wchg": 0.0, "up": 0, "down": 0, "foreign": 0.0, "n": 0})
        a["turnover"] += p["turnover"]
        a["wchg"] += p["turnover"] * (p.get("chg_pct") or 0)
        a["n"] += 1
        a["up" if (p.get("chg_pct") or 0) > 0 else "down"] += 1
        v = (t86 or {}).get(c)
        if v:
            a["foreign"] += v[0] * p["close"] / 1e5
    out = []
    for a in agg.values():
        if a["turnover"] < 1 or a["n"] < 3:
            continue
        out.append({"name": a["name"], "chg_pct": r(a["wchg"] / a["turnover"], 2), "turnover": r(a["turnover"], 1),
                    "up": a["up"], "down": a["down"], "foreign": r(a["foreign"], 2), "n": a["n"]})
    out.sort(key=lambda x: x["chg_pct"], reverse=True)
    return out
