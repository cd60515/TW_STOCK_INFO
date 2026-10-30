"""新聞情緒:以台股常見利多/利空用語做關鍵字計分(可解釋、免費、無需外部 API)。

這是粗略的標題情緒判讀,用途是協助快速掃過大量標題、標出值得點進去看的消息,
不能取代閱讀原文。
"""
from __future__ import annotations

# 先比對較長的片語,避免「不如預期」被「預期」之類的短詞誤判
POSITIVE = [
    "優於預期", "超乎預期", "目標價上調", "調升目標價", "上修", "調升", "創新高", "創高", "歷史新高",
    "漲停", "大漲", "飆漲", "噴出", "強漲", "勁揚", "上攻", "走揚", "反彈", "攻高",
    "利多", "看好", "加碼", "買超", "大單", "擴產", "接單", "訂單滿", "滿載", "熱銷", "漲價",
    "受惠", "亮眼", "年增", "月增", "成長", "轉盈", "獲利增", "賺", "創佳績", "雙增", "回溫",
    "突破", "站上", "上調評等", "買進評等", "股利", "配息", "庫藏股", "併購", "認證", "打入",
]
NEGATIVE = [
    "不如預期", "低於預期", "遜於預期", "目標價下調", "調降目標價", "下修", "調降", "降評",
    "跌停", "大跌", "重挫", "暴跌", "急殺", "崩", "走弱", "下挫", "失守", "跌破", "破底", "翻黑",
    "利空", "看淡", "減碼", "賣超", "砍單", "衰退", "年減", "月減", "轉虧", "虧損", "虧",
    "示警", "警訊", "處置", "注意股", "違約", "裁員", "停工", "停產", "罰", "調查", "訴訟",
    "下滑", "疲弱", "去化", "庫存調整", "關稅", "制裁", "斷鏈", "跳票", "掏空",
]


def score_title(title: str) -> int:
    """回傳 -2..+2 的情緒分數。"""
    t = title or ""
    used = []
    pos = neg = 0
    for w in sorted(NEGATIVE, key=len, reverse=True):
        if w in t and not any(w in u for u in used):
            neg += 1
            used.append(w)
    for w in sorted(POSITIVE, key=len, reverse=True):
        if w in t and not any(w in u or u in w for u in used):
            pos += 1
            used.append(w)
    return max(-2, min(2, pos - neg))


def tag(items: list[dict]) -> list[dict]:
    for it in items:
        s = score_title(it.get("title", ""))
        it["sentiment"] = s
        it["tone"] = "pos" if s > 0 else "neg" if s < 0 else "neu"
    return items


def summarize(items: list[dict]) -> dict:
    if not items:
        return {"count": 0, "pos": 0, "neg": 0, "score": 0.0, "label": "無新聞"}
    pos = sum(1 for i in items if i["sentiment"] > 0)
    neg = sum(1 for i in items if i["sentiment"] < 0)
    score = sum(i["sentiment"] for i in items) / len(items)
    label = "偏多" if score > 0.25 else "偏空" if score < -0.25 else "中性"
    return {"count": len(items), "pos": pos, "neg": neg, "score": round(score, 2), "label": label}
