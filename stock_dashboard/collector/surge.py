"""飆股專區:連續跳空漲停型態 + 市場恐慌/貪婪溫度計。

選股邏輯(依實戰經驗整理,規則全部透明):
  1. 型態:近 LOOKBACK 個交易日內出現「連續 ≥ 2 根漲停」,且其中至少一根是「跳空漲停」
     (當日最低價 > 前一日最高價,留下未回補的缺口)。這代表主力不計成本搶籌碼。
  2. 缺口是生命線:第一個跳空缺口(突破缺口)的下緣是型態的停損位。
     回測缺口不補 = 主力護盤、型態成立;缺口被完全回補 = 型態失敗。
  3. 最好的買點通常不是連板當下,而是「開板後量縮整理、回測缺口不破」或「整理後再創新高」。
  4. 低基期起漲(起漲前位於一年區間的低檔)續航力較好;高檔連板容易是末升段。
  5. 融資在連板期間暴增 = 散戶追價,後續籌碼凌亂;法人同步買超較健康。
  6. 大均線壓力:收盤必須站上季線、半年線、年線(日線 60/120/240 日均線與月 K 3/6/12 月均線),
     以及月 K 的長期大均線 20 月線、60 月線(五年線,用 Yahoo 十年月線計算)。
     日線再漂亮,月線圖上方壓著大均線就只是空頭格局裡的反彈,套牢賣壓重,剔除。
  7. 量能門檻:漲停日均量、平日均量,以及「起漲前 60 日的成交量中位數」都要達標;
     平常沒人交易、只有漲停那幾天爆量的冷門股,進出困難,剔除。
  8. 長線回檔・中線買點:短線缺口被回補(短線客出場),但月 K 仍是強勢多頭
     (3>6>12>20 月均線多頭排列、年線 3 個月上揚 ≥ 20%)、股價自高點回檔 ≥ 15% 且守住所有大均線,
     屬於長線多頭的回檔,反而是中線買點(以季線為支撐,持有週期以週~月計)。
  9. 大盤環境:市場越恐慌,強勢股越容易成為資金避風港(逆勢抗跌);
     市場極度貪婪、融資高檔時,要提高警覺、降低部位。

市場溫度計 0(極度恐慌)~100(極度貪婪),由七項公開資料等權平均:
  VIX 美股恐慌指數、大盤融資維持率(估算)、融資餘額 20 日變化、上漲家數比、
  漲停/跌停家數比、加權指數季線乖離、外資 10 日買賣超。

本模組只做規則式的機械判斷,所有分數與價位僅供參考,不構成投資建議。
"""
from __future__ import annotations

import json
import math
import os
from statistics import mean

from .analysis import r

LOOKBACK = 20         # 型態必須在最近 20 個交易日內發生
MAX_CANDIDATES = 60   # 網站最多顯示幾檔新候選(依續航分排序;追蹤池另計)
POOL_DAYS = 90        # 入選後保留在追蹤池的天數(日曆日):強勢股常要等好幾週才回測到買點
CHART_BARS = 160      # 每檔附的日 K 根數(週 K / 月 K 另外附全部兩年)
MODEL = "claude-opus-5-5"
# 量能門檻(張):成交量太小的股票進出困難、容易被少數人拉抬,直接剔除
MIN_LU_VOL = 1000     # 漲停日平均成交量
MIN_DAY_VOL = 800     # 近 20 日「非漲停日」平均成交量
# 長線回檔・中線買點(缺口已回補,但月 K 強勢多頭)
PB_MA12_RISE = 20     # 月 K 年線(12 月均線)近 3 個月至少上揚 %
PB_MIN_DROP = 15      # 自高點至少回檔 %(回檔不夠深就不算買點)
MIN_BASE_VOL = 300    # 起漲前 60 日的成交量中位數(平常就要有人交易,不是只有漲停那幾天爆量)
# 大均線:(日線天數, 月 K 月數, 名稱)。收盤必須站上全部,否則上方有大均線壓力。
# 20 月線、60 月線只看月 K(日線天數為 None)。上市未滿 N 個月的股票,該條均線略過不判斷。
BIG_MAS = ((60, 3, "季線"), (120, 6, "半年線"), (240, 12, "年線"), (None, 20, "20月線"), (None, 60, "60月線"))


# ---------------------------------------------------------------- 漲停價計算

def tick(p: float) -> float:
    """台股升降單位(股票)。"""
    if p < 10:
        return 0.01
    if p < 50:
        return 0.05
    if p < 100:
        return 0.1
    if p < 500:
        return 0.5
    if p < 1000:
        return 1.0
    return 5.0


def limit_up_price(prev: float) -> float:
    raw = prev * 1.1
    t = tick(raw)
    return round(math.floor(raw / t + 1e-6) * t, 2)


def is_limit_up(prev, close) -> bool:
    if not prev or not close or close <= prev * 1.085:
        return False
    return close >= limit_up_price(prev) - 1e-6


def snapshot_is_limit_up(p: dict) -> bool:
    """只有當日快照(收盤 + 漲跌幅)時:由漲跌幅反推昨收並對齊升降單位。"""
    chg = p.get("chg_pct") or 0
    c = p.get("close")
    if chg < 9.0 or not c:
        return False
    prev = c / (1 + chg / 100)
    t = tick(prev)
    return is_limit_up(round(round(prev / t) * t, 2), c)


def is_common(code: str) -> bool:
    return len(code) == 4 and code.isdigit() and not code.startswith("0")


# ---------------------------------------------------------------- 候選股(全市場快照掃描)

def find_candidates(daily: list[tuple[str, dict]]) -> list[str]:
    """從全市場每日快照找出近期有漲停的股票(之後再用完整日線做精確判斷)。
    條件寬鬆:近 LOOKBACK 日內漲停 ≥ 2 次,或最近 3 日內有漲停(首板觀察)。"""
    recent = daily[-LOOKBACK:]
    count: dict[str, int] = {}
    last3: set[str] = set()
    for i, (_, snap) in enumerate(recent):
        for c, p in (snap.get("px") or {}).items():
            if is_common(c) and snapshot_is_limit_up(p):
                count[c] = count.get(c, 0) + 1
                if i >= len(recent) - 3:
                    last3.add(c)
    out = [c for c, n in count.items() if n >= 2] + [c for c in last3 if count.get(c, 0) < 2]
    # 先處理漲停次數多的,避免候選太多時漏掉主角
    out.sort(key=lambda c: -count.get(c, 0))
    return out[:120]


def bars_from_snapshots(code: str, daily) -> list[dict]:
    """Yahoo 失敗時的備援:用每日快照組出日線(約 45 根,沒有一年基期資料)。"""
    out = []
    for d, snap in daily:
        p = (snap.get("px") or {}).get(code)
        if p and p.get("open") and p.get("high") and p.get("low"):
            out.append({"date": d, "open": p["open"], "high": p["high"], "low": p["low"],
                        "close": p["close"], "volume": int((p.get("vol") or 0) * 1000)})
    return out


# ---------------------------------------------------------------- 單一股票型態分析

def _runs(lu: list[bool]) -> list[tuple[int, int]]:
    runs, s = [], None
    for i, f in enumerate(lu + [False]):
        if f and s is None:
            s = i
        elif not f and s is not None:
            runs.append((s, i - 1))
            s = None
    return runs


def _pattern_events(o, h, l, c, lu, gap):
    """所有「連續 ≥2 根漲停且含跳空」的事件 -> [(start, end)]"""
    return [(s, e) for s, e in _runs(lu) if e - s + 1 >= 2 and any(gap[s:e + 1])]


def _avg_pos(vals):
    """平均量:成交量 0 多半是資料源尚未更新的 K 棒,不列入計算。"""
    vals = [x for x in vals if x > 0]
    return mean(vals) if vals else None


def _median_pos(vals):
    vals = sorted(x for x in vals if x > 0)
    if not vals:
        return None
    m = len(vals) // 2
    return vals[m] if len(vals) % 2 else (vals[m - 1] + vals[m]) / 2


def volume_check(v, lu, s, e, end):
    """量能門檻:漲停日平均量 ≥ MIN_LU_VOL、end 往前 20 日的非漲停日平均量 ≥ MIN_DAY_VOL,
    且起漲前 60 日的成交量中位數 ≥ MIN_BASE_VOL(資料不足 20 日就不判斷這一項)。"""
    lu_avg = _avg_pos([v[i] for i in range(s, e + 1) if lu[i]])
    day_avg = _avg_pos([v[i] for i in range(max(0, end - 19), end + 1) if not lu[i]])
    pre = v[max(0, s - 60):s]
    base_med = _median_pos(pre) if sum(1 for x in pre if x > 0) >= 20 else None
    reasons = []
    if base_med is not None and base_med < MIN_BASE_VOL:
        reasons.append(f"起漲前 60 日量中位數 {base_med:,.0f} 張 < {MIN_BASE_VOL:,}(平常交易冷清)")
    if lu_avg is None or lu_avg < MIN_LU_VOL:
        reasons.append(f"漲停日均量 {0 if lu_avg is None else lu_avg:,.0f} 張 < {MIN_LU_VOL:,}")
    if day_avg is None or day_avg < MIN_DAY_VOL:
        reasons.append(f"平日均量 {0 if day_avg is None else day_avg:,.0f} 張 < {MIN_DAY_VOL:,}")
    return lu_avg, day_avg, base_med, reasons


def resample(dates, o, h, l, c, v, unit):
    """日 K 轉週 K / 月 K。時間用該週期第一個交易日;最後一根是尚未走完的週期(跟看盤軟體一樣)。"""
    from datetime import date as _d
    out = []
    for i, d in enumerate(dates):
        if unit == "M":
            key = d[:7]
        else:
            y, w, _ = _d.fromisoformat(d).isocalendar()
            key = (y, w)
        if out and out[-1]["key"] == key:
            b = out[-1]
            b["high"] = max(b["high"], h[i]); b["low"] = min(b["low"], l[i]); b["close"] = c[i]; b["vol"] += v[i]
            b["end"] = i
        else:
            out.append({"key": key, "date": d, "open": o[i], "high": h[i], "low": l[i], "close": c[i], "vol": v[i], "end": i})
    return out


def month_bars(dates, o, h, l, c, v, end, mlong=None):
    """月 K:日線涵蓋的月份用日線重組(含當月最新價),更早的月份接 Yahoo 十年月線。"""
    months = resample(dates[:end + 1], o, h, l, c, v, "M")
    if mlong:
        first = dates[0][:7]
        old = [{"key": m["date"][:7], "date": m["date"], "open": m["open"], "high": m["high"], "low": m["low"],
                "close": m["close"], "vol": (m.get("volume") or 0) / 1000, "end": -1}
               for m in mlong if m["date"][:7] < first]
        months = old + months
    return months


def ma_check(dates, o, h, l, c, v, end, mlong=None):
    """大均線檢查:在 end 這天,收盤是否站上季線/半年線/年線(日線與月 K 都要)。
    回傳 (各均線明細, 壓在上方的均線名稱)。資料不足的均線略過不判斷。"""
    px = c[end]
    months = month_bars(dates, o, h, l, c, v, end, mlong)
    mc = [m["close"] for m in months]
    info, over = [], []
    for dn, mn, label in BIG_MAS:
        dv = mean(c[end - dn + 1:end + 1]) if dn and end + 1 >= dn else None
        mv = mean(mc[-mn:]) if len(mc) >= mn else None
        avail = [x for x in (dv, mv) if x is not None]
        bad = [x for x in avail if px < x]
        # 距離以較高(較嚴格)的那條為準:正值 = 站上幾 %,負值 = 還差幾 %
        info.append({"label": label, "d": r(dv), "m": r(mv), "days": dn, "months": mn,
                     "dist": r((px / max(avail) - 1) * 100, 1) if avail else None,
                     "ok": not bad if avail else None})
        if bad:
            over.append(label)
    return info, over


def _forward(c, h, e, n):
    """型態結束日收盤進場,之後 5/10/20 日報酬與 20 日內最大漲幅。"""
    res = {}
    for k in (5, 10, 20):
        res[f"r{k}"] = (c[e + k] / c[e] - 1) * 100 if e + k < n else None
    if e + 1 < n:
        hi = max(h[e + 1:min(n, e + 21)])
        res["max20"] = (hi / c[e] - 1) * 100
    else:
        res["max20"] = None
    return res


def analyze(code: str, bars: list[dict], name: str, chips_by_date: dict, extra: dict,
            lookback: int = LOOKBACK) -> dict | None:
    n = len(bars)
    if n < 25:
        return None
    o = [float(b["open"]) for b in bars]
    h = [float(b["high"]) for b in bars]
    l = [float(b["low"]) for b in bars]
    c = [float(b["close"]) for b in bars]
    v = [float(b.get("volume") or 0) / 1000 for b in bars]
    dates = [b["date"] for b in bars]
    lu = [False] + [is_limit_up(c[i - 1], c[i]) for i in range(1, n)]
    gap = [False] + [l[i] > h[i - 1] for i in range(1, n)]
    one_word = [lu[i] and o[i] == h[i] == l[i] == c[i] for i in range(n)]

    events = _pattern_events(o, h, l, c, lu, gap)
    recent = [ev for ev in events if ev[1] >= n - lookback]
    kind = None
    if recent:
        s, e = recent[-1]
        # 若前面還有相鄰的事件(中間只隔 1~2 天),視為同一波,起漲點往前推
        for ps, pe in reversed(recent[:-1]):
            if s - pe <= 3:
                s = ps
        kind = "pattern"
    else:
        # 首板觀察:最近 2 日內出現跳空漲停,但還沒有連板
        for i in range(n - 1, n - 3, -1):
            if lu[i] and gap[i]:
                s = e = i
                while e + 1 < n and lu[e + 1]:
                    e += 1
                kind = "early"
                break
    if kind is None:
        return None

    last = n - 1
    base = c[s - 1] if s >= 1 else o[s]
    run_len = sum(lu[s:e + 1])
    consec = max((ee - ss + 1 for ss, ee in _runs(lu[s:e + 1])), default=0)
    gaps = [(i, h[i - 1], l[i]) for i in range(s, e + 1) if gap[i]]
    peak = max(h[s:])
    peak_close = max(c[s:])
    days_since = last - e
    run_gain = (c[e] / base - 1) * 100
    total_gain = (c[last] / base - 1) * 100

    # 缺口:第一個缺口 = 突破缺口(生命線);最後一個 = 最上方缺口
    g_i, g_bot, g_top = gaps[0]
    after_low = min(l[g_i + 1:]) if g_i < last else l[g_i]
    gap_filled = after_low <= g_bot
    gap_tested = after_low <= g_top and not gap_filled
    u_i, u_bot, u_top = gaps[-1]
    upper_filled = len(gaps) > 1 and u_i < last and min(l[u_i + 1:]) <= u_bot
    # 進場/停損以「最接近現價、尚未回補」的缺口為準(突破缺口可能離現價太遠)
    open_gaps = [(i, b_, t_) for i, b_, t_ in gaps if (i == last or min(l[i + 1:]) > b_) and b_ < c[last]]
    tested = [g for g in open_gaps if g[0] < last]  # 今天才出現的缺口還沒經過考驗
    k_i, k_bot, k_top = (tested or open_gaps or gaps)[-1]

    # 量能:開板後是否量縮整理
    pre_vol = _avg_pos(v[max(0, s - 20):s]) if s >= 5 else None
    burst_vol = max(v[s:min(n, e + 2)])
    post = v[e + 1:] if e < last else []
    post_vol = _avg_pos(post[-5:]) if post else None
    lu_avg, day_avg, base_med, vol_reasons = volume_check(v, lu, s, e, last)
    mlong = extra.get("monthly")
    ma_info, ma_over = ma_check(dates, o, h, l, c, v, last, mlong)
    vol_dry = post_vol is not None and burst_vol > 0 and post_vol <= burst_vol * 0.5

    # 基期:起漲前位於近一年區間的位置
    w0 = max(0, s - 250)
    lo250, hi250 = min(l[w0:s] or [base]), max(h[w0:s] or [base])
    base_pos = (base - lo250) / (hi250 - lo250) * 100 if hi250 > lo250 and s - w0 >= 60 else None
    pre60 = (base / c[s - 61] - 1) * 100 if s >= 61 else None

    ma5 = mean(c[-5:])
    ma10 = mean(c[-10:])

    # 狀態判定
    if lu[last] and e == last:
        status = "running"
    elif gap_filled or c[last] < g_bot:
        status = "failed"
    elif days_since >= 2 and c[last] >= max(c[s:last]) and c[last] > c[e]:
        status = "breakout"
    elif c[last] <= k_top * 1.05:
        status = "buyzone"
    else:
        status = "consolidating"
    if kind == "early" and status != "failed":
        status = "early"
    if vol_reasons and status != "failed":
        status = "lowvol"
    if ma_over and status not in ("failed", "lowvol"):
        status = "overhead"

    # 長線回檔:短線缺口回補了,但月 K 仍是強勢多頭 → 中線買點
    pb = None
    _mc = [m["close"] for m in month_bars(dates, o, h, l, c, v, last, mlong)]
    if len(_mc) >= 23 and n >= 60:
        m3, m6, m12, m20 = (mean(_mc[-k:]) for k in (3, 6, 12, 20))
        m12_prev = mean(_mc[-15:-3])
        pb = {"m3": r(m3), "m6": r(m6), "m12": r(m12), "m20": r(m20), "d60": r(mean(c[-60:])),
              "aligned": m3 > m6 > m12 > m20, "m12_rise": r((m12 / m12_prev - 1) * 100, 1),
              "drop": r((c[last] / peak - 1) * 100, 1)}
        if (status == "failed" and not vol_reasons and not ma_over and pb["aligned"]
                and pb["m12_rise"] >= PB_MA12_RISE and pb["drop"] <= -PB_MIN_DROP):
            status = "pullback"

    # 籌碼:型態期間法人與融資
    period = dates[max(0, s - 1):]
    f_sum = t_sum = None
    m_start = m_end = short_end = None
    for d in period:
        ch = chips_by_date.get(d)
        if not ch:
            continue
        t86 = ch.get("t86")
        if t86 and d >= dates[s]:
            f_sum = (f_sum or 0) + t86[0]
            t_sum = (t_sum or 0) + t86[1]
        mg = ch.get("margin")
        if mg:
            if m_start is None:
                m_start = mg[0]
            m_end, short_end = mg[0], mg[2]
    margin_chg = (m_end / m_start - 1) * 100 if (m_start and m_end is not None and m_start >= 100) else None
    short_ratio = short_end / m_end * 100 if (m_end and short_end is not None) else None
    inst = (f_sum or 0) + (t_sum or 0) if (f_sum is not None or t_sum is not None) else None

    # ---------- 續航評分(0-100),每項列出理由
    bd = []

    def add(group, label, pts, mx):
        bd.append({"group": group, "label": label, "pts": r(pts, 1), "max": mx})
        return pts

    sc = 0
    sc += add("型態", f"連續漲停 {consec} 根(區間共 {run_len} 根)",
              {0: 0, 1: 6, 2: 10, 3: 14}.get(consec, 18), 18)
    sc += add("型態", f"跳空缺口 {len(gaps)} 個" + (f"、一字鎖 {sum(one_word[s:e + 1])} 根" if any(one_word[s:e + 1]) else ""),
              min(12, 6 * len(gaps)), 12)
    if base_pos is None:
        sc += add("基期", "一年基期資料不足", 8, 20)
    elif base_pos <= 35:
        sc += add("基期", f"低基期起漲(位於一年區間 {base_pos:.0f}% 位置)", 20, 20)
    elif base_pos <= 65:
        sc += add("基期", f"中基期起漲(一年區間 {base_pos:.0f}% 位置)", 12, 20)
    else:
        sc += add("基期", f"高基期起漲(一年區間 {base_pos:.0f}% 位置),留意末升段", 4, 20)
    if status == "failed":
        sc += add("缺口", "突破缺口已被回補,型態失敗", 0, 15)
    elif gap_tested:
        sc += add("缺口", "回測缺口但未回補,支撐有效", 15, 15)
    elif status in ("running", "early"):
        sc += add("缺口", "缺口尚未經過回測", 9, 15)
    else:
        sc += add("缺口", "缺口未回補,股價維持在缺口之上", 12, 15)
    if status in ("running", "early"):
        sc += add("量能", "仍在發動,尚無開板量縮資訊", 5, 10)
    elif vol_dry:
        sc += add("量能", "開板後量縮整理(籌碼安定)", 10, 10)
    else:
        sc += add("量能", "開板後量能仍大,籌碼換手中", 4, 10)
    if inst is None:
        sc += add("籌碼", "無法人資料", 5, 10)
    elif inst > 0:
        sc += add("籌碼", f"外資+投信期間買超 {inst:,.0f} 張", 10, 10)
    else:
        sc += add("籌碼", f"外資+投信期間賣超 {abs(inst):,.0f} 張(多為主力/隔日沖)", 3, 10)
    if margin_chg is None:
        sc += add("籌碼", "融資資料不足", 5, 10)
    elif margin_chg <= 10:
        sc += add("籌碼", f"融資期間 {margin_chg:+.0f}%,散戶未大量追價", 10, 10)
    elif margin_chg <= 30:
        sc += add("籌碼", f"融資期間 {margin_chg:+.0f}%,散戶開始上車", 5, 10)
    else:
        sc += add("籌碼", f"融資期間暴增 {margin_chg:+.0f}%,籌碼凌亂", 0, 10)
    if short_ratio is not None and short_ratio >= 15:
        sc += add("籌碼", f"券資比 {short_ratio:.0f}%,具軋空動能", 5, 5)
    else:
        sc += add("籌碼", "券資比不高", 1, 5)
    alerts = extra.get("alerts", {}).get(code)
    if alerts == "處置":
        sc -= 8
        bd.append({"group": "風險", "label": "已列處置股(分盤撮合,流動性下降)", "pts": -8, "max": 0})
    elif alerts == "注意":
        sc -= 3
        bd.append({"group": "風險", "label": "已列注意股,再漲可能被處置", "pts": -3, "max": 0})
    if vol_reasons:
        bd.append({"group": "量能門檻", "label": "未達標:" + "、".join(vol_reasons), "pts": 0, "max": 0})
    else:
        bd.append({"group": "量能門檻", "label": f"通過:漲停日均量 {lu_avg:,.0f} 張、平日均量 {day_avg:,.0f} 張"
                   + (f"、起漲前量中位數 {base_med:,.0f} 張" if base_med is not None else ""), "pts": 0, "max": 0})
    ma_txt = "、".join(f"{m['label']} {m['dist']:+.1f}%" for m in ma_info if m["ok"] is not None)
    if ma_over:
        bd.append({"group": "大均線", "label": f"未站上{'、'.join(ma_over)}(上方有壓力):{ma_txt}", "pts": 0, "max": 0})
    elif ma_txt:
        bd.append({"group": "大均線", "label": f"通過:已站上所有大均線({ma_txt})", "pts": 0, "max": 0})
    else:
        bd.append({"group": "大均線", "label": "上市時間太短,大均線資料不足", "pts": 0, "max": 0})
    if status == "pullback":  # 中線買點另外計分:短線缺口的分數已不適用
        d60 = pb["d60"]
        near = (c[last] / d60 - 1) * 100
        v5, v20 = _avg_pos(v[-5:]), _avg_pos(v[-20:])
        shrink = v5 / v20 if v5 and v20 else None
        bd = [b for b in bd if b["group"] in ("籌碼", "風險", "量能門檻", "大均線")]
        bd.insert(0, {"group": "中線", "label": "短線缺口已回補(短線客出場),改看中線", "pts": 0, "max": 0})
        sc = 0
        sc += add("中線", f"月K多頭排列:3月 {pb['m3']} > 6月 {pb['m6']} > 12月 {pb['m12']} > 20月 {pb['m20']}", 15, 15)
        sc += add("中線", f"年線 3 個月上揚 {pb['m12_rise']:.0f}%" + (",主升段" if pb["m12_rise"] >= 40 else ""),
                  15 if pb["m12_rise"] >= 40 else 10, 15)
        sc += add("中線", f"距季線 {d60}(日 60 日均線){near:+.1f}%" + (",已接近支撐" if near <= 8 else ",離支撐還遠,等拉回"),
                  20 if near <= 8 else 12 if near <= 15 else 6, 20)
        sc += add("中線", "近 5 日量縮至 20 日均量的 " + (f"{shrink * 100:.0f}%" if shrink else "—") + ",賣壓減輕",
                  15 if shrink and shrink <= 0.5 else 8 if shrink and shrink <= 0.8 else 3, 15)
        sc += add("中線", f"自高點回檔 {abs(pb['drop']):.0f}%,上檔空間大", 5 if pb["drop"] <= -25 else 3, 5)
        sc += sum(b["pts"] for b in bd if b["group"] in ("籌碼", "風險"))
        bd.sort(key=lambda b: b["group"] != "中線")
    if status == "failed":  # 型態失敗:不論其他條件多好,都不應排在可操作名單前面
        sc = min(sc, 30)
    if status in ("lowvol", "overhead"):  # 量能不足 / 大均線壓力:不列入可操作名單
        sc = min(sc, 40)
    score = r(max(0, min(100, sc)), 0)

    # ---------- 交易計畫
    stop = round(k_bot * 0.985, 2)
    if status == "breakout":  # 第二波沿均線操作,停損改用 10 日線
        stop = round(min(ma10, c[last]) * 0.97, 2)
    t1 = r(peak) if peak > c[last] * 1.03 else r(peak + 0.382 * (peak - base))
    t2 = r(max(peak + 0.5 * (peak - base), base * 1.3))
    if t2 <= t1:
        t2 = r(t1 * 1.08)
    entry_lo, entry_hi = r(k_top), r(k_top * 1.03)
    if status == "breakout":  # 第二波的買點是沿 5 日線,而不是回到缺口
        entry_lo, entry_hi = r(min(ma5, c[last])), r(c[last])
    if status == "pullback":  # 中線:季線附近分批,跌破季線 5% 停損,目標前高
        entry_lo, entry_hi = r(pb["d60"]), r(min(c[last], pb["d60"] * 1.05))
        stop = round(pb["d60"] * 0.95, 2)
        t1 = r(peak)
    plans = {
        "running": f"連板鎖單中不追價。等開板後量縮回測 {r(k_top)}(缺口上緣)不破再分批布局。",
        "early": f"首根跳空漲停,尚未確認連板。可小量試單,收盤跌破 {r(k_bot)}(缺口下緣)即出場。",
        "buyzone": f"股價回到缺口附近 {entry_lo}~{entry_hi},量縮守住缺口為主要買點,跌破 {stop} 停損。",
        "consolidating": f"強勢整理中,距缺口仍有距離。等拉回 {entry_lo}~{entry_hi} 或帶量突破 {r(peak)} 再進場。",
        "breakout": f"整理後再創新高(第二波)。可沿 5 日線 {r(ma5)} 操作,跌破 10 日線 {r(ma10)} 減碼、跌破 {stop} 出場。",
        "failed": f"突破缺口已回補,型態失敗,不進場;持有者應停損出場。",
        "pullback": (f"短線缺口已回補、短線客出場,但月K多頭排列、年線 3 個月上揚 {pb['m12_rise']:.0f}%,屬長線多頭的回檔。"
                     f"中線買點在季線 {entry_lo}~{entry_hi} 附近量縮止跌時分批布局,收盤跌破 {stop} 停損,第一目標前高 {r(peak)}。"
                     "持有週期以週~月計,不是隔日沖。") if pb else "",
        "lowvol": "成交量未達門檻(" + "、".join(vol_reasons) + "),流動性不足、容易被少數人拉抬,不列入操作。",
        "overhead": f"日線型態雖然成立,但股價還在{'、'.join(ma_over)}之下,屬於空頭格局裡的反彈,"
                    f"上方套牢賣壓重、隨時可能拉回。等收盤站上{'、'.join(ma_over)}再重新評估。",
    }
    rr = None
    if status not in ("failed", "running", "lowvol", "overhead"):
        ref = c[last]
        if ref > stop and t1 > ref:
            rr = r((t1 - ref) / (ref - stop), 2)

    # ---------- 本股歷史同型態回測(排除本次)
    # 只統計當時也通過量能門檻與大均線檢查的型態,口徑才一致
    hist = [_forward(c, h, ee, n) for ss, ee in events
            if ee < s - 1 and not volume_check(v, lu, ss, ee, ee)[3] and not ma_check(dates, o, h, l, c, v, ee, mlong)[1]]

    k0 = max(0, n - CHART_BARS)

    def mas(vals, k, start):
        return [None if i < k - 1 else r(mean(vals[i - k + 1:i + 1])) for i in range(start, len(vals))]

    def period_series(unit, ks):
        ps = month_bars(dates, o, h, l, c, v, n - 1, mlong) if unit == "M" else resample(dates, o, h, l, c, v, unit)
        cl = [p["close"] for p in ps]
        out = {"date": [p["date"] for p in ps], "open": [r(p["open"]) for p in ps], "high": [r(p["high"]) for p in ps],
               "low": [r(p["low"]) for p in ps], "close": [r(x) for x in cl], "vol_lots": [r(p["vol"], 0) for p in ps]}
        for k in ks:
            out[f"ma{k}"] = mas(cl, k, 0)
        return out
    return {
        "code": code, "name": name, "industry": extra.get("industry"),
        "kind": kind, "status": status,
        "close": r(c[last]), "chg_pct": r((c[last] / c[last - 1] - 1) * 100),
        "date": dates[last], "start": dates[s], "end": dates[e],
        "days_since": days_since, "consec": consec, "run_len": run_len,
        "gaps": len(gaps), "one_word": sum(one_word[s:e + 1]),
        "base": r(base), "peak": r(peak), "peak_close": r(peak_close),
        "run_gain": r(run_gain, 1), "total_gain": r(total_gain, 1),
        "pullback": r((c[last] / peak - 1) * 100, 1),
        "gap_bot": r(g_bot), "gap_top": r(g_top), "gap_date": dates[g_i],
        "gap_filled": gap_filled, "gap_tested": gap_tested, "upper_filled": upper_filled,
        "dist_gap": r((c[last] / k_top - 1) * 100, 1),
        "key_bot": r(k_bot), "key_top": r(k_top), "key_date": dates[k_i],
        "base_pos": r(base_pos, 0), "pre60": r(pre60, 1),
        "pre_vol": r(pre_vol, 0), "burst_vol": r(burst_vol, 0), "post_vol": r(post_vol, 0), "vol_dry": vol_dry,
        "lu_vol": r(lu_avg, 0), "day_vol": r(day_avg, 0), "base_vol": r(base_med, 0), "vol_reasons": vol_reasons,
        "mas": ma_info, "ma_over": ma_over, "pb": pb,
        "vol": r(v[last], 0), "turnover": r(v[last] * c[last] / 1e5, 2),
        "foreign": f_sum, "trust": t_sum, "margin_chg": r(margin_chg, 1), "short_ratio": r(short_ratio, 1),
        "alert": alerts,
        "score": score, "breakdown": bd,
        "plan": {"text": plans[status], "stop": stop, "stop_pct": r((stop / c[last] - 1) * 100, 1),
                 "entry_lo": entry_lo, "entry_hi": entry_hi,
                 "target1": t1, "target1_pct": r((t1 / c[last] - 1) * 100, 1),
                 "target2": t2, "target2_pct": r((t2 / c[last] - 1) * 100, 1), "rr": rr},
        "history": hist,
        "series": {"date": dates[k0:], "open": [r(x) for x in o[k0:]], "high": [r(x) for x in h[k0:]],
                   "low": [r(x) for x in l[k0:]], "close": [r(x) for x in c[k0:]],
                   "vol_lots": [r(x, 0) for x in v[k0:]], "ma5": mas(c, 5, k0), "ma20": mas(c, 20, k0),
                   "ma60": mas(c, 60, k0), "ma120": mas(c, 120, k0), "ma240": mas(c, 240, k0),
                   "lu": [bool(x) for x in lu[k0:]]},
        "series_w": period_series("W", (13, 26, 52)),
        "series_m": period_series("M", (3, 6, 12, 20, 60)),
    }


def _hist_stats(events: list[dict]) -> dict | None:
    if not events:
        return None
    out = {"n": len(events)}
    for k in ("r5", "r10", "r20", "max20"):
        vals = [x[k] for x in events if x[k] is not None]
        out[f"{k}_n"] = len(vals)
        out[f"{k}_avg"] = r(mean(vals), 1) if vals else None
        out[f"{k}_med"] = r(sorted(vals)[len(vals) // 2], 1) if vals else None
        out[f"{k}_win"] = r(sum(1 for x in vals if x > 0) / len(vals) * 100, 0) if vals else None
    vals = [x["max20"] for x in events if x["max20"] is not None]
    out["hit30"] = r(sum(1 for x in vals if x >= 30) / len(vals) * 100, 0) if vals else None
    out["hit10"] = r(sum(1 for x in vals if x >= 10) / len(vals) * 100, 0) if vals else None
    return out


# ---------------------------------------------------------------- 市場溫度計

def _clamp(x):
    return max(0.0, min(100.0, x))


SENTI_LEVELS = [
    (20, "極度恐慌", "fear2", "市場極度恐慌、人人急著下車。依策略這是「別人恐懼我貪婪」的時機:"
                         "優先挑逆勢抗跌、缺口未補的強勢股分批布局,但每筆都要先設停損。"),
    (40, "恐慌", "fear", "市場偏恐慌,賣壓較重。強勢股若能在這種環境守住缺口,續航力通常更好,可開始留意買點。"),
    (60, "中性", "neutral", "情緒中性,依個股型態與籌碼操作即可。"),
    (80, "貪婪", "greed", "市場偏樂觀、追價氣氛升溫。連板股容易出現隔日沖賣壓,進場要等回測、不追高。"),
    (101, "極度貪婪", "greed2", "市場極度貪婪、槓桿偏高。這時候最要小心:新倉降低部位、既有部位分批停利,"
                          "不要在連板末端追價。"),
]


def _level(score):
    for hi, label, cls, advice in SENTI_LEVELS:
        if score < hi:
            return label, cls, advice
    return SENTI_LEVELS[-1][1:]


def market_sentiment(daily, idx_bars, vix_bars):
    """回傳溫度計(含各分項)與歷史序列。idx_bars: [{'date','close'}](已含 MA60 所需長度)。"""
    if not daily:
        return None
    idx_close = {b["date"]: b["close"] for b in idx_bars}
    idx_dates = [b["date"] for b in idx_bars]
    vix = sorted((b["date"], b["close"]) for b in (vix_bars or []))

    def vix_before(d):
        val = None
        for vd, vc in vix:
            if vd < d:
                val = (vd, vc)
            else:
                break
        return val

    # 每日統計
    stats = []
    for d, snap in daily:
        px = snap.get("px") or {}
        common = [(c, p) for c, p in px.items() if is_common(c)]
        up = sum(1 for _, p in common if (p.get("chg_pct") or 0) > 0)
        dn = sum(1 for _, p in common if (p.get("chg_pct") or 0) < 0)
        lu = sum(1 for _, p in common if snapshot_is_limit_up(p))
        ld = sum(1 for _, p in common if (p.get("chg_pct") or 0) <= -9.5)
        mg = snap.get("margin") or {}
        mm = snap.get("market_margin")
        maint = None
        if mg and mm:
            val = sum(m[0] * 1000 * px[c]["close"] for c, m in mg.items() if c in px)
            maint = val / (mm * 1e8) * 100 if val else None
        stats.append({"date": d, "up": up, "dn": dn, "lu": lu, "ld": ld, "mm": mm, "maint": maint,
                      "foreign": (snap.get("bfi") or {}).get("foreign"),
                      "turnover": sum(p.get("turnover") or 0 for p in px.values())})

    def components(i):
        st = stats[i]
        d = st["date"]
        out = []
        vb = vix_before(d)
        if vb:
            out.append({"key": "vix", "name": "VIX 美股恐慌指數", "value": f"{vb[1]:.1f}",
                        "score": _clamp((32 - vb[1]) / (32 - 13) * 100),
                        "note": "VIX 越高代表全球資金越恐慌(>30 為恐慌、<15 為安逸)"})
        if st["maint"]:
            out.append({"key": "maint", "name": "大盤融資維持率(估)", "value": f"{st['maint']:.0f}%",
                        "score": _clamp((st["maint"] - 140) / (185 - 140) * 100),
                        "note": "低於 140% 容易引發斷頭賣壓(恐慌底);越高代表融資戶獲利豐厚、市場偏熱"})
        mms = [x["mm"] for x in stats[max(0, i - 20):i + 1] if x["mm"]]
        if len(mms) >= 6:
            chg = (mms[-1] / mms[0] - 1) * 100
            out.append({"key": "margin", "name": f"融資餘額 {len(mms) - 1} 日變化", "value": f"{chg:+.1f}%",
                        "score": _clamp((chg + 6) / 12 * 100),
                        "note": "融資快速增加 = 散戶槓桿追價,越高越要小心;融資大減 = 籌碼清洗"})
        w = stats[max(0, i - 9):i + 1]
        tot = sum(x["up"] + x["dn"] for x in w)
        if tot:
            ratio = sum(x["up"] for x in w) / tot
            out.append({"key": "breadth", "name": f"近 {len(w)} 日上漲家數比", "value": f"{ratio * 100:.0f}%",
                        "score": _clamp((ratio - 0.35) / 0.3 * 100), "note": "市場廣度:多數股票上漲代表情緒樂觀"})
        lsum = sum(x["lu"] + x["ld"] for x in w)
        if lsum:
            share = sum(x["lu"] for x in w) / lsum
            out.append({"key": "limits", "name": f"近 {len(w)} 日漲停/跌停", "value": f"{sum(x['lu'] for x in w)}/{sum(x['ld'] for x in w)}",
                        "score": _clamp((share - 0.45) / 0.5 * 100), "note": "跌停家數多 = 恐慌殺盤;漲停家數多 = 投機熱絡"})
        if d in idx_close:
            k = idx_dates.index(d)
            if k >= 59:
                ma60 = mean(idx_close[x] for x in idx_dates[k - 59:k + 1])
                bias = (idx_close[d] / ma60 - 1) * 100
                out.append({"key": "bias", "name": "加權指數季線乖離", "value": f"{bias:+.1f}%",
                            "score": _clamp((bias + 6) / 12 * 100), "note": "指數遠低於季線為恐慌,遠高於季線為過熱"})
        fw = [x for x in w if x["foreign"] is not None and x["turnover"]]
        if len(fw) >= 3:
            f = sum(x["foreign"] for x in fw)
            share = f / sum(x["turnover"] for x in fw) * 100  # 佔成交值 %
            out.append({"key": "foreign", "name": f"外資近 {len(fw)} 日買賣超", "value": f"{f:+,.0f} 億",
                        "score": _clamp((share + 8) / 16 * 100),
                        "note": f"佔同期成交值 {share:+.1f}%;外資連續大賣通常伴隨市場恐慌"})
        return out

    hist = []
    for i in range(len(stats)):
        comps = components(i)
        if len(comps) >= 4:
            hist.append({"date": stats[i]["date"], "score": r(mean(x["score"] for x in comps), 0)})
    comps = components(len(stats) - 1)
    if not comps:
        return None
    score = mean(x["score"] for x in comps)
    label, cls, advice = _level(score)
    for x in comps:
        x["score"] = r(x["score"], 0)
    # 融資警示:融資分項偏熱時加註(融資餘額越高越要小心)
    warn = []
    mcomp = next((x for x in comps if x["key"] == "margin"), None)
    if mcomp and mcomp["score"] >= 75:
        warn.append(f"融資餘額快速增加({mcomp['value']}),散戶槓桿升溫,強勢股追價風險提高。")
    mt = next((x for x in comps if x["key"] == "maint"), None)
    if mt and mt["score"] <= 15:
        warn.append(f"融資維持率偏低({mt['value']}),留意斷頭賣壓;斷頭潮出現後常是中期低點。")
    vb = next((x for x in comps if x["key"] == "vix"), None)
    if vb and vb["score"] <= 10:
        warn.append(f"VIX 飆高({vb['value']}),全球避險情緒濃厚,台股易受外資提款影響。")
    vix_series = [{"date": d_, "v": v_} for d_, v_ in vix[-120:]]
    last = stats[-1]
    return {
        "score": r(score, 0), "label": label, "cls": cls, "advice": advice, "warnings": warn,
        "components": comps, "history": hist[-60:],
        "vix": vix_series,
        "margin": [{"date": x["date"], "v": x["mm"]} for x in stats if x["mm"]][-45:],
        "maint": [{"date": x["date"], "v": r(x["maint"], 1)} for x in stats if x["maint"]][-45:],
        "today": {"lu": last["lu"], "ld": last["ld"], "mm": last["mm"], "maint": r(last["maint"], 1)},
    }


# ---------------------------------------------------------------- 題材(新聞)

THEMES = ["AI", "CPO", "矽光子", "機器人", "伺服器", "散熱", "PCB", "ABF", "載板", "記憶體", "HBM", "先進封裝",
          "半導體", "低軌衛星", "衛星", "無人機", "軍工", "國防", "重電", "電網", "儲能", "綠能", "太陽能", "風電",
          "電動車", "車用", "光通訊", "5G", "網通", "生技", "新藥", "減重", "醫材", "漲價", "缺貨", "併購",
          "公開收購", "轉型", "營收", "法說", "接單", "擴產", "庫藏股", "航運", "觀光", "營建", "資產", "鋼鐵", "銅", "金價"]


def themes_of(news_items) -> list[str]:
    text = " ".join(x.get("title", "") for x in news_items or [])
    found = [t for t in THEMES if t in text]
    return found[:5]


AI_SYSTEM = """你是台股短線/波段操盤顧問,專長是解讀「連續跳空漲停」強勢股的題材與續航力。
使用者會提供多檔股票的型態數據與近兩週新聞標題(JSON)。請針對每一檔輸出:
- theme:漲停背後的題材,10 字以內(新聞看不出來就寫「題材不明」)
- quality:只能是「實質題材」「概念炒作」「消息不明」三者之一
  (實質題材=有營收/訂單/獲利/併購等具體事件;概念炒作=只有題材想像或族群輪動)
- note:一句 40 字以內的操作提醒,要結合型態狀態(連板中/整理/回測缺口/失敗)
只能根據提供的資料判斷,不要編造新聞裡沒有的事件或數字。
只輸出一個 JSON 物件,格式:{"代號": {"theme": "...", "quality": "...", "note": "..."}, ...},不要有其他文字。"""


def ai_notes(rows: list[dict]) -> dict | None:
    """有 ANTHROPIC_API_KEY 時,請 Claude 判讀題材(非結構化新聞)。失敗時回傳 None。"""
    if not rows or not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
    except ImportError:
        return None
    payload = [{
        "code": x["code"], "name": x["name"], "industry": x.get("industry"), "status": STATUS_LABEL[x["status"]],
        "連續漲停": x["consec"], "跳空缺口": x["gaps"], "起漲以來漲幅%": x["total_gain"],
        "基期位置%": x["base_pos"], "融資期間變化%": x["margin_chg"],
        "news": [n["title"] for n in x.get("news", [])[:8]],
    } for x in rows[:20]]
    try:
        client = anthropic.Anthropic()
        resp = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low"},
            system=AI_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        )
    except Exception as e:  # API 異常時退回規則式題材標籤,不影響網站產生
        print(f"[warn] 飆股題材 AI 判讀失敗,改用關鍵字標籤: {e!r}")
        return None
    if resp.stop_reason == "refusal":
        return None
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    start, end = text.find("{"), text.rfind("}")
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        print("[warn] 飆股題材 AI 回應不是合法 JSON,改用關鍵字標籤")
        return None
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


# ---------------------------------------------------------------- 組合

STATUS_LABEL = {
    "running": "連板鎖單中", "early": "首根跳空漲停", "buyzone": "回測缺口・買點區",
    "consolidating": "強勢整理", "breakout": "整理後再創新高", "failed": "缺口回補・型態失敗",
    "lowvol": "量能不足・剔除", "overhead": "大均線壓力・剔除", "pullback": "長線回檔・中線買點",
}
STATUS_ORDER = ["buyzone", "breakout", "pullback", "consolidating", "running", "early", "overhead", "lowvol", "failed"]
EXCLUDED = ("failed", "lowvol", "overhead")  # 不列入可操作名單


# ---------------------------------------------------------------- 追蹤池

SIGNAL_STATUS = ("buyzone", "breakout", "pullback")


def pool_active(pool: dict, today: str) -> dict:
    """去掉已過期(入池超過 POOL_DAYS 天)的股票。"""
    return {c: p for c, p in (pool or {}).items() if p.get("expires", "") >= today}


def entry_signal(x) -> bool:
    """是否到達買點:回測缺口買點區、整理後再創新高,或中線回檔且股價已回到季線買點區附近。"""
    if x["status"] in ("buyzone", "breakout"):
        return True
    return x["status"] == "pullback" and x["close"] <= x["plan"]["entry_hi"] * 1.02


def update_pool(pool: dict, rows: list[dict], today: str) -> dict:
    """新入選(可操作)的股票放進追蹤池;已在池中的更新最新狀態。入池時的條件快照保留不動。"""
    from datetime import date, timedelta
    pool = dict(pool)
    for x in rows:
        p = pool.get(x["code"])
        if p is None and x["status"] not in EXCLUDED:
            # 入池日 = 型態完成日(第一次能被發現的日子),保留 POOL_DAYS 天
            added = min(x["end"], today)
            sd = x["series"]["date"]
            add_close = x["series"]["close"][sd.index(added)] if added in sd else x["close"]
            p = pool[x["code"]] = {
                "name": x["name"], "added": added,
                "expires": (date.fromisoformat(added) + timedelta(days=POOL_DAYS)).isoformat(),
                "add_close": add_close, "add_status": x["status"], "add_score": x["score"],
                "start": x["start"], "end": x["end"], "base": x["base"], "peak": x["peak"],
                "gap_bot": x["gap_bot"], "gap_top": x["gap_top"],
            }
        if p is not None:
            p.update({"last": today, "last_status": x["status"], "last_close": x["close"], "name": x["name"]})
            if x["status"] not in EXCLUDED:
                p["peak"] = max(p.get("peak") or 0, x["peak"])
    return pool


def build_zone(ctx, names, idx_bars):
    daily = ctx["daily"]
    sz = ctx.get("surge") or {}
    chips_by_date = {}
    for d, snap in daily:
        chips_by_date[d] = {"t86_all": snap.get("t86") or {}, "margin_all": snap.get("margin") or {}}

    rows = []
    pool = sz.get("pool") or {}
    for code in sz.get("candidates") or []:
        bars = (sz.get("bars") or {}).get(code) or bars_from_snapshots(code, daily)
        if not bars:
            continue
        # TWSE 收盤比 Yahoo 早:若 Yahoo 少了最新一天,用快照補上
        latest_d, latest = daily[-1]
        p = (latest.get("px") or {}).get(code)
        if p and p.get("open") and bars[-1]["date"] < latest_d:
            bars = bars + [{"date": latest_d, "open": p["open"], "high": p["high"], "low": p["low"],
                            "close": p["close"], "volume": int((p.get("vol") or 0) * 1000)}]
        cbd = {d: {"t86": x["t86_all"].get(code), "margin": x["margin_all"].get(code)} for d, x in chips_by_date.items()}
        lb = LOOKBACK
        if code in pool:  # 追蹤池的股票:型態可能早已超過 20 日,往回看到起漲日
            ds = [b["date"] for b in bars]
            st = pool[code].get("start")
            lb = max(LOOKBACK, len(ds) - next((i for i, d in enumerate(ds) if d >= st), len(ds)) + 1) if st else LOOKBACK
        try:
            a = analyze(code, bars, names.get(code, code), cbd,
                        {"industry": ctx["industries"].get(code), "alerts": sz.get("alerts") or {},
                         "monthly": (sz.get("monthly") or {}).get(code)}, lookback=lb)
        except Exception as e:  # 單檔異常不影響整體
            print(f"[warn] 飆股分析 {code} 失敗: {e!r}")
            a = None
        if a and a["date"] == latest_d:
            news = (sz.get("news") or {}).get(code) or []
            a["news"] = news[:6]
            a["themes"] = themes_of(news)
            rows.append(a)

    pooled = [ev for x in rows for ev in x["history"]]
    for x in rows:
        x["hist"] = _hist_stats(x.pop("history"))
    rows.sort(key=lambda x: (x["status"] in EXCLUDED, x["status"] == "failed", -x["score"]))
    # 新候選依分數取前 MAX_CANDIDATES 檔;追蹤池的股票一律保留
    fresh = [x for x in rows if x["code"] not in pool][:MAX_CANDIDATES]
    keep = {x["code"] for x in fresh} | set(pool)
    rows = [x for x in rows if x["code"] in keep]
    today = daily[-1][0]
    new_pool = update_pool(pool_active(pool, today), rows, today)
    for x in rows:
        p = new_pool.get(x["code"])
        x["signal"] = entry_signal(x)
        if p:
            from datetime import date
            x["pool"] = {"added": p["added"], "expires": p["expires"], "add_close": p["add_close"],
                         "add_status": p["add_status"],
                         "days_left": (date.fromisoformat(p["expires"]) - date.fromisoformat(today)).days,
                         "chg": r((x["close"] / p["add_close"] - 1) * 100, 1) if p.get("add_close") else None}

    senti = market_sentiment(daily, idx_bars, sz.get("vix"))
    # 大盤環境加減分:越恐慌越有利(強勢股抗跌、資金集中),極度貪婪扣分
    if senti:
        adj = 6 if senti["score"] < 25 else 3 if senti["score"] < 45 else -4 if senti["score"] >= 80 else 0
        if adj:
            for x in rows:
                x["score"] = r(max(0, min(100, x["score"] + adj)), 0)
                x["breakdown"].append({"group": "大盤", "label": f"市場溫度 {senti['score']:.0f}({senti['label']})",
                                       "pts": adj, "max": 6})
    counts = {k: sum(1 for x in rows if x["status"] == k) for k in STATUS_ORDER}
    return {
        "sentiment": senti,
        "rows": rows,
        "counts": counts,
        "status_label": STATUS_LABEL,
        "status_order": STATUS_ORDER,
        "excluded": list(EXCLUDED),
        "vol_rule": {"lu": MIN_LU_VOL, "day": MIN_DAY_VOL, "base": MIN_BASE_VOL},
        "ma_rule": [{"label": lb, "days": dn, "months": mn} for dn, mn, lb in BIG_MAS],
        "pooled": _hist_stats(pooled),
        "pool": new_pool, "pool_days": POOL_DAYS,
        "scanned": len(sz.get("candidates") or []),
        "ai": False,
    }


def attach_ai(zone):
    """在網站資料產生後,用 Claude 為候選股補上題材判讀(沒有 key 就略過)。"""
    rows = [x for x in zone["rows"] if x["status"] not in EXCLUDED]
    notes = ai_notes(rows)
    if not notes:
        return zone
    for x in zone["rows"]:
        n = notes.get(x["code"])
        if n:
            x["ai"] = {"theme": str(n.get("theme", ""))[:20], "quality": str(n.get("quality", ""))[:8],
                       "note": str(n.get("note", ""))[:80]}
    zone["ai"] = True
    return zone
