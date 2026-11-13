"""阿家飆股:成長前段班 × AI 供應鏈 × 近三個月有漲停 × 量能足夠。

四個條件全部符合才入選(規則全部透明,參數在本檔開頭調整):
  1. 成長動能前 GROWTH_TOP 名(全體上市、獲利中的普通股):
     「明年預估 EPS」沒有免費公開的法人共識資料,改用最能領先反映明年獲利的三個公開數字推估:
       - 近四季 EPS 年增率(權重 40%):今天的近四季 EPS 對比一年前的近四季 EPS
         (EPS = 收盤價 ÷ 證交所本益比;一年前的數字由當天的本益比與收盤價還原)
       - 最新單月營收年增率(權重 35%):營收是獲利的領先指標
       - 今年累計營收年增率(權重 25%):確認不是單月爆衝
     三者各自換算成全市場百分位(0~100)後加權,得到「成長分」,取前 GROWTH_TOP 名。
     虧損股(沒有本益比)不列入。
  2. AI 供應鏈:名單在 stock_dashboard/ai_chain.json(代號: 子題材),可以自行增刪。
  3. 近 LU_DAYS 個交易日(約三個月)內至少有一根收盤漲停。
  4. 量能:近 20 日平均成交量 ≥ MIN_VOL 張(冷門股進出困難,剔除)。

只差一個條件的股票列在「差一步」,方便追蹤;所有 AI 供應鏈股票都可以在「AI 供應鏈全覽」查看每一項的狀態。
本模組只做規則式的機械判斷,不構成投資建議。
"""
from __future__ import annotations

import json
from pathlib import Path

from .analysis import r
from .surge import bars_from_snapshots, is_common, is_limit_up

GROWTH_TOP = 100     # 成長分取前幾名
LU_DAYS = 60         # 漲停回看交易日數(約三個月)
MIN_VOL = 1000       # 近 20 日平均成交量門檻(張)
CHART_BARS = 120     # 網站附的日 K 根數
W_EPS, W_REV, W_CUM = 0.40, 0.35, 0.25
EPS_YOY_CAP = 300    # EPS 年增率上限(低基期公司動輒好幾倍,避免極端值)

CHAIN_FILE = Path(__file__).resolve().parent.parent / "ai_chain.json"
CHECKS = [("growth", "成長前 %d" % GROWTH_TOP), ("ai", "AI 供應鏈"), ("lu", "3 個月內漲停"), ("vol", "量能 ≥ %d 張" % MIN_VOL)]


def load_chain(path: Path = CHAIN_FILE) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError) as e:
        print(f"[warn] 讀不到 AI 供應鏈名單 {path}: {e!r}")
        return {}
    return {str(c): str(t) for c, t in (data.get("stocks") or {}).items()}


def _pct_rank(vals: dict) -> dict:
    """{code: 值} -> {code: 百分位 0~100}(值越大越高;同值取平均名次)。"""
    items = sorted(vals.items(), key=lambda kv: kv[1])
    n = len(items)
    if n < 2:
        return {c: 50.0 for c, _ in items}
    out, i = {}, 0
    while i < n:
        j = i
        while j + 1 < n and items[j + 1][1] == items[i][1]:
            j += 1
        p = (i + j) / 2 / (n - 1) * 100
        for k in range(i, j + 1):
            out[items[k][0]] = p
        i = j + 1
    return out


def growth_rank(valuation: dict, px: dict, revenue: dict, eps_base: dict, names: dict) -> list[dict]:
    """全體上市獲利股的成長分排名(由高到低)。"""
    base = (eps_base or {}).get("eps") or {}
    base_loss = set((eps_base or {}).get("loss") or [])
    rows = {}
    for c, v in (valuation or {}).items():
        pe, close = v.get("pe"), (px.get(c) or {}).get("close")
        if not (is_common(c) and pe and pe > 0 and close):
            continue
        eps = close / pe
        b = base.get(c)
        if b and b > 0:
            yoy = min((eps / b - 1) * 100, EPS_YOY_CAP)
            turn = False
        elif c in base_loss:  # 一年前虧損、現在獲利 = 轉虧為盈
            yoy, turn = EPS_YOY_CAP, True
        else:
            yoy, turn = None, False
        rv = revenue.get(c) or {}
        rows[c] = {"code": c, "name": names.get(c, c), "eps": round(eps, 2), "eps_base": round(b, 2) if b else None,
                   "eps_yoy": r(yoy, 1) if yoy is not None else None, "turnaround": turn,
                   "rev_yoy": rv.get("yoy"), "rev_cum": rv.get("cum_yoy"), "rev_mom": rv.get("mom"), "rev_ym": rv.get("ym")}
    p_eps = _pct_rank({c: x["eps_yoy"] for c, x in rows.items() if x["eps_yoy"] is not None})
    p_rev = _pct_rank({c: x["rev_yoy"] for c, x in rows.items() if x["rev_yoy"] is not None})
    p_cum = _pct_rank({c: x["rev_cum"] for c, x in rows.items() if x["rev_cum"] is not None})
    out = []
    for c, x in rows.items():
        parts = [(w, p[c]) for w, p in ((W_EPS, p_eps), (W_REV, p_rev), (W_CUM, p_cum)) if c in p]
        if not parts or sum(w for w, _ in parts) < 0.5:  # 資料太少(例如只有一項)不排名
            continue
        x["growth"] = r(sum(w * s for w, s in parts) / sum(w for w, _ in parts), 1)
        out.append(x)
    out.sort(key=lambda x: -x["growth"])
    for i, x in enumerate(out, 1):
        x["rank"] = i
    return out


def _ma(c, n, i):
    return sum(c[i - n + 1:i + 1]) / n if i >= n - 1 else None


def price_stats(bars: list[dict]) -> dict | None:
    """近三個月漲停、量能與股價位置。bars 依日期排序。"""
    if not bars or len(bars) < 25:
        return None
    c = [b["close"] for b in bars]
    i = len(c) - 1
    lus = [k for k in range(max(1, i - LU_DAYS + 1), i + 1) if is_limit_up(c[k - 1], c[k])]
    vols = [(b.get("volume") or 0) / 1000 for b in bars]
    vol20 = sum(vols[-20:]) / min(20, len(vols))
    high52 = max(b["high"] for b in bars[-250:])
    ma20, ma60 = _ma(c, 20, i), _ma(c, 60, i)
    last_lu = lus[-1] if lus else None
    return {
        "date": bars[-1]["date"], "close": c[i], "chg_pct": r((c[i] / c[i - 1] - 1) * 100),
        "vol": r(vols[-1], 0), "vol20": r(vol20, 0),
        "lu_count": len(lus), "lu_last": bars[last_lu]["date"] if last_lu is not None else None,
        "lu_days_ago": i - last_lu if last_lu is not None else None,
        "since_lu": r((c[i] / c[last_lu] - 1) * 100, 1) if last_lu is not None else None,
        "high52": r(high52), "dd": r((c[i] / high52 - 1) * 100, 1),
        "ma20": r(ma20) if ma20 else None, "ma60": r(ma60) if ma60 else None,
        "d_ma20": r((c[i] / ma20 - 1) * 100, 1) if ma20 else None,
        "d_ma60": r((c[i] / ma60 - 1) * 100, 1) if ma60 else None,
        "chg20": r((c[i] / c[i - 20] - 1) * 100, 1) if i >= 20 else None,
        "chg60": r((c[i] / c[i - 60] - 1) * 100, 1) if i >= 60 else None,
    }


def _series(bars: list[dict]) -> dict:
    c = [b["close"] for b in bars]
    k0 = max(0, len(bars) - CHART_BARS)
    out = {k: [] for k in ("date", "open", "high", "low", "close", "vol_lots", "ma20", "ma60", "lu")}
    for k in range(k0, len(bars)):
        b = bars[k]
        out["date"].append(b["date"])
        for f in ("open", "high", "low", "close"):
            out[f].append(b.get(f) if b.get(f) is not None else b["close"])
        out["vol_lots"].append(round((b.get("volume") or 0) / 1000))
        for n in (20, 60):
            m = _ma(c, n, k)
            out[f"ma{n}"].append(r(m) if m else None)
        out["lu"].append(1 if k and is_limit_up(c[k - 1], c[k]) else 0)
    return out


def build_zone(ctx) -> dict | None:
    az = ctx.get("ajia")
    if not az:
        return None
    daily = ctx["daily"]
    today, latest = daily[-1]
    px = latest.get("px") or {}
    names = ctx.get("names") or {}
    chain = az.get("chain") or {}
    ranked = growth_rank(ctx.get("valuation"), px, ctx.get("revenue") or {}, az.get("eps_base"), names)
    by_code = {x["code"]: x for x in ranked}
    top = {x["code"] for x in ranked[:GROWTH_TOP]}
    rows = []
    for c, theme in chain.items():
        if c not in px and c not in names:  # 不是上市股票(或已下市)
            continue
        # Yahoo 抓不到時退回每日快照(約 45 個交易日,漲停只能回看到這麼遠)
        bars = list((az.get("bars") or {}).get(c) or bars_from_snapshots(c, daily))
        sp = px.get(c)
        if sp and sp.get("open") and (not bars or bars[-1]["date"] < today):  # Yahoo 少了最新一天,用 TWSE 收盤補上
            bars.append({"date": today, "open": sp["open"], "high": sp["high"], "low": sp["low"], "close": sp["close"],
                         "volume": int((sp.get("vol") or 0) * 1000)})
        st = price_stats(bars)
        g = by_code.get(c) or {}
        row = {"code": c, "name": names.get(c, c), "theme": theme, "industry": (ctx.get("industries") or {}).get(c),
               "rank": g.get("rank"), "growth": g.get("growth"), "eps": g.get("eps"), "eps_base": g.get("eps_base"),
               "eps_yoy": g.get("eps_yoy"), "turnaround": g.get("turnaround", False),
               "rev_yoy": g.get("rev_yoy"), "rev_cum": g.get("rev_cum"), "rev_mom": g.get("rev_mom"), "rev_ym": g.get("rev_ym"),
               "pe": ((ctx.get("valuation") or {}).get(c) or {}).get("pe")}
        if st:
            row.update(st)
            row["series"] = _series(bars)
        else:
            row.update({"close": (sp or {}).get("close"), "chg_pct": (sp or {}).get("chg_pct"), "lu_count": None, "vol20": None})
        row["checks"] = {
            "growth": c in top,
            "ai": True,
            "lu": bool(row.get("lu_count")),
            "vol": (row.get("vol20") or 0) >= MIN_VOL,
        }
        row["miss"] = [k for k, ok in row["checks"].items() if not ok]
        row["pass"] = not row["miss"]
        rows.append(row)

    def key(x):
        return (len(x["miss"]), x["rank"] if x["rank"] else 10 ** 6, -(x.get("lu_count") or 0))
    rows.sort(key=key)
    return {
        "rows": rows,
        "counts": {"pass": sum(1 for x in rows if x["pass"]), "near": sum(1 for x in rows if len(x["miss"]) == 1),
                   "chain": len(rows)},
        "ranked": len(ranked),
        "eps_base_date": (az.get("eps_base") or {}).get("date"),
        "growth_top": [{k: x.get(k) for k in ("rank", "code", "name", "growth", "eps", "eps_yoy", "turnaround",
                                               "rev_yoy", "rev_cum")} | {"ai": chain.get(x["code"])}
                       for x in ranked[:GROWTH_TOP]],
        "checks": [{"key": k, "label": l} for k, l in CHECKS],
        "rule": {"growth_top": GROWTH_TOP, "lu_days": LU_DAYS, "min_vol": MIN_VOL,
                 "w_eps": W_EPS, "w_rev": W_REV, "w_cum": W_CUM, "eps_cap": EPS_YOY_CAP},
    }
