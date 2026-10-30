"""技術指標(台股慣用參數)。輸入為含 date/open/high/low/close/volume 的 DataFrame。"""
from __future__ import annotations

import numpy as np
import pandas as pd


def compute(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy().reset_index(drop=True)
    c, h, l = df["close"], df["high"], df["low"]
    df["vol_lots"] = df["volume"] / 1000

    for n in (5, 10, 20, 60, 120, 240):
        df[f"ma{n}"] = c.rolling(n).mean()
    df["vma5"] = df["vol_lots"].rolling(5).mean()
    df["vma20"] = df["vol_lots"].rolling(20).mean()

    # KD(9,3,3):台灣券商軟體的遞迴算法,K/D 初始值 50
    low9, high9 = l.rolling(9, min_periods=1).min(), h.rolling(9, min_periods=1).max()
    rsv = ((c - low9) / (high9 - low9).replace(0, np.nan) * 100).fillna(50).to_numpy()
    k = np.empty(len(df))
    d = np.empty(len(df))
    pk = pd_ = 50.0
    for i, r in enumerate(rsv):
        pk = pk * 2 / 3 + r / 3
        pd_ = pd_ * 2 / 3 + pk / 3
        k[i], d[i] = pk, pd_
    df["k"], df["d"] = k, d

    # MACD(12,26,9)
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    df["dif"] = ema12 - ema26
    df["dea"] = df["dif"].ewm(span=9, adjust=False).mean()
    df["osc"] = df["dif"] - df["dea"]

    # RSI(14) Wilder
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    df["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    df.loc[loss == 0, "rsi"] = 100.0

    # 布林通道(20, 2)
    std20 = c.rolling(20).std(ddof=0)
    df["bb_up"] = df["ma20"] + 2 * std20
    df["bb_dn"] = df["ma20"] - 2 * std20
    df["bb_width"] = (df["bb_up"] - df["bb_dn"]) / df["ma20"] * 100

    # ATR(14)
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()

    df["hh20"] = h.rolling(20).max()
    df["ll20"] = l.rolling(20).min()
    df["hh60"] = h.rolling(60).max()
    df["ll60"] = l.rolling(60).min()
    df["hh250"] = h.rolling(250, min_periods=60).max()
    df["ll250"] = l.rolling(250, min_periods=60).min()
    df["chg_pct"] = c.pct_change() * 100
    return df
