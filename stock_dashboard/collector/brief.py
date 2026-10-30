"""盤後重點摘要:有設定 ANTHROPIC_API_KEY 時由 Claude 撰寫分析師短評,否則用規則式模板。"""
from __future__ import annotations

import json
import os

MODEL = "claude-opus-5-5"

SYSTEM = """你是一位資深台股分析師,為一位自行操作的個人投資人撰寫每日盤後重點。
讀者每天只花幾分鐘看,所以要精簡、具體、可執行,用繁體中文。

請根據提供的 JSON 資料(大盤、三大法人、類股、自選股評分與訊號、選股雷達、新聞標題)撰寫:
### 今日盤勢
2~3 句:指數表現、多空格局、法人動向與資金流向的類股。
### 自選股重點
最多 5 檔最值得注意的(偏多或轉弱都算),每檔一行:代號 名稱 — 關鍵理由 — 操作想法(含停損/目標價位)。
### 明日觀察
2~4 個條列:要盯的價位、事件或風險。
### 風險提醒
1 句。

規則:只使用資料中的數字,不要編造資料裡沒有的事件或數字;新聞標題只能當作線索,不要過度解讀。
不要使用「保證」「穩賺」等字眼。總長度約 350~500 字。"""


def _payload(dash):
    m = dash["market"]
    return {
        "date": m["date"],
        "index": m["index"],
        "regime": {k: m["regime"][k] for k in ("label", "score", "exposure")},
        "regime_notes": [n["text"] for n in m["regime"]["notes"]],
        "breadth": m["breadth"],
        "institutional_億": m["bfi"][-5:],
        "sectors_top": m["sectors"][:5], "sectors_bottom": m["sectors"][-5:],
        "watchlist": [{
            "code": s["code"], "name": s["name"], "close": s["close"], "chg_pct": s["chg_pct"],
            "chg20": s["chg20"], "score": s["score"], "rating": s["rating"],
            "signals": [x["text"] for x in s["signals"][:6]],
            "plan": {k: s["plan"][k] for k in ("stance", "stop", "target1", "rr")},
            "chips": {k: (s["chips"] or {}).get(k) for k in ("foreign5", "trust5", "foreign_streak", "trust_streak", "margin_chg5")},
            "news": [n["title"] for n in s["news"][:3]],
        } for s in dash["stocks"]],
        "screens": {sc["title"]: [f"{r['code']} {r['name']}" for r in sc["rows"][:5]] for sc in dash["screens"]},
        "market_news": [n["title"] for n in m["news"][:12]],
    }


def claude_brief(dash):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
    except ImportError:
        return None
    client = anthropic.Anthropic()
    content = "以下是今日資料(JSON):\n" + json.dumps(_payload(dash), ensure_ascii=False)
    try:
        resp = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "medium"},
            system=SYSTEM,
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.RateLimitError as e:
        print(f"[warn] Claude 速率限制,改用規則式摘要: {e}")
        return None
    except anthropic.APIStatusError as e:
        print(f"[warn] Claude API 錯誤 {e.status_code},改用規則式摘要: {e.message}")
        return None
    except anthropic.APIConnectionError as e:
        print(f"[warn] 無法連線 Claude API,改用規則式摘要: {e}")
        return None
    if resp.stop_reason == "refusal":
        print("[warn] Claude 拒絕回應,改用規則式摘要")
        return None
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    return {"source": "ai", "text": text} if text else None


def rule_brief(dash):
    m = dash["market"]
    idx, rg, br = m["index"], m["regime"], m["breadth"] or {}
    lines = ["### 今日盤勢"]
    s = (f"加權指數收 {idx['close']:,.2f},{'上漲' if idx['chg'] >= 0 else '下跌'} {abs(idx['chg']):,.2f} 點"
         f"({idx['chg_pct']:+.2f}%)。大盤格局判定為「{rg['label']}」(多空分數 {rg['score']:.0f}/100),"
         f"建議持股水位 {rg['exposure']}。")
    if br:
        s += f"上漲 {br['up']} 家、下跌 {br['down']} 家,成交值約 {br['turnover']:,.0f} 億。"
    if m["bfi"]:
        b = m["bfi"][-1]
        s += f"三大法人合計{'買' if b['total'] >= 0 else '賣'}超 {abs(b['total']):,.1f} 億(外資 {b['foreign']:+,.1f}、投信 {b['trust']:+,.1f})。"
    lines.append(s)
    if m["sectors"]:
        lines.append("資金流向:強勢類股 " + "、".join(f"{x['name']}({x['chg_pct']:+.2f}%)" for x in m["sectors"][:3])
                     + ";弱勢類股 " + "、".join(f"{x['name']}({x['chg_pct']:+.2f}%)" for x in m["sectors"][-3:]) + "。")
    lines.append("### 自選股重點")
    picks = dash["stocks"][:3] + [x for x in dash["stocks"][-2:] if x["score"] < 42]
    seen = set()
    for st in picks:
        if st["code"] in seen:
            continue
        seen.add(st["code"])
        top = next((x["text"] for x in st["signals"] if x["type"] != "info"), "無明顯訊號")
        p = st["plan"]
        lines.append(f"- **{st['code']} {st['name']}**({st['rating']},{st['score']:.0f} 分)— {top} — "
                     + (f"{p['stance']},停損 {p['stop']}、目標 {p['target1']}" if st["score"] >= 42
                        else f"{p['stance']},持有者跌破 {p['stop']} 減碼,反彈壓力 {p['target1']}"))
    lines.append("### 明日觀察")
    lines.append(f"- 加權指數月線 {idx['ma20']:,.0f}、季線 {idx['ma60']:,.0f} 為多空分水嶺")
    for sc in dash["screens"][:2]:
        if sc["rows"]:
            lines.append(f"- {sc['title']}:" + "、".join(f"{r['code']} {r['name']}" for r in sc["rows"][:4]))
    lines.append("### 風險提醒")
    lines.append("以上為公開資料的機械式整理,不構成投資建議;進場前請設好停損並控制單筆部位風險。")
    return {"source": "rule", "text": "\n".join(lines)}


def make_brief(dash):
    return claude_brief(dash) or rule_brief(dash)
