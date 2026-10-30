"""單檔 HTML 報告(資料內嵌,可直接用手機/瀏覽器打開)與盤後 email。"""
from __future__ import annotations

import html
import json
import os
import smtplib
import ssl
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path


def write_single_file(site_dir: Path, dash: dict) -> Path:
    page = (site_dir / "index.html").read_text(encoding="utf-8")
    css = (site_dir / "style.css").read_text(encoding="utf-8")
    js = (site_dir / "app.js").read_text(encoding="utf-8")
    data = json.dumps(dash, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    page = page.replace('<link rel="stylesheet" href="style.css">', f"<style>\n{css}\n</style>")
    page = page.replace('<script src="app.js"></script>',
                        f"<script>window.__DASHBOARD__={data};</script>\n<script>\n{js}\n</script>")
    out = site_dir / "report.html"
    out.write_text(page, encoding="utf-8")
    return out


def _color(v):
    if v is None:
        return "#666"
    return "#d93025" if v > 0 else "#1e8e3e" if v < 0 else "#666"


def email_html(dash: dict) -> str:
    m = dash["market"]
    idx, rg = m["index"], m["regime"]
    url = os.environ.get("DASHBOARD_URL", "").strip()
    rows = []
    for s in dash["stocks"]:
        sig = "; ".join(x["text"] for x in s["signals"][:2]) or "-"
        rows.append(
            f"<tr><td style='padding:6px 8px'><b>{s['code']}</b> {html.escape(s['name'])}</td>"
            f"<td style='padding:6px 8px;text-align:right'>{s['close']}</td>"
            f"<td style='padding:6px 8px;text-align:right;color:{_color(s['chg_pct'])}'>{s['chg_pct']:+.2f}%</td>"
            f"<td style='padding:6px 8px;text-align:center'>{s['score']:.0f}<br><small>{s['rating']}</small></td>"
            f"<td style='padding:6px 8px;font-size:12px'>{html.escape(sig)}</td>"
            f"<td style='padding:6px 8px;font-size:12px'>停損 {s['plan']['stop']}<br>目標 {s['plan']['target1']}</td></tr>")
    brief_html = ""
    for line in dash["brief"]["text"].splitlines():
        line = html.escape(line.strip())
        if not line:
            continue
        if line.startswith("### "):
            brief_html += f"<h3 style='margin:14px 0 4px;font-size:15px'>{line[4:]}</h3>"
        elif line.startswith("- "):
            brief_html += f"<div style='margin:2px 0 2px 10px'>• {line[2:].replace('**', '')}</div>"
        else:
            brief_html += f"<p style='margin:4px 0'>{line.replace('**', '')}</p>"
    screens = "".join(
        f"<p style='margin:4px 0'><b>{html.escape(sc['title'])}</b>:"
        + html.escape("、".join(f"{r['code']} {r['name']}" for r in sc["rows"][:6]) or "今日無符合") + "</p>"
        for sc in dash["screens"])
    link = (f"<p><a href='{html.escape(url)}' style='color:#1a73e8'>開啟完整分析網站 →</a></p>" if url else
            "<p>完整互動版請開啟附件 <b>report.html</b>(手機可直接用瀏覽器開啟)。</p>")
    return f"""<div style="font-family:-apple-system,'PingFang TC','Microsoft JhengHei',sans-serif;max-width:760px;color:#202124">
<h2 style="margin:0 0 4px">台股盤後分析 {m['date']}</h2>
<p style="margin:0 0 12px;color:#5f6368">加權指數 <b>{idx['close']:,.2f}</b>
<span style="color:{_color(idx['chg'])}">{idx['chg']:+,.2f} ({idx['chg_pct']:+.2f}%)</span>
 ・ 大盤格局 <b>{rg['label']}</b> ・ 建議持股 {rg['exposure']}</p>
{link}
<div style="background:#f8f9fa;border-radius:8px;padding:10px 14px">{brief_html}</div>
<h3 style="margin:18px 0 6px">自選股評分</h3>
<table style="border-collapse:collapse;width:100%;font-size:13px" border="0">
<tr style="background:#f1f3f4"><th align="left" style="padding:6px 8px">股票</th><th>收盤</th><th>漲跌</th><th>評分</th><th align="left">重點訊號</th><th align="left">計畫</th></tr>
{''.join(rows)}</table>
<h3 style="margin:18px 0 6px">選股雷達</h3>{screens}
<p style="color:#80868b;font-size:12px;margin-top:20px">本報告為公開資料之機械式整理分析,不構成投資建議,投資盈虧請自負。</p>
</div>"""


def maybe_email(state_dir: Path, dash: dict, report_path: Path, force=False):
    user = os.environ.get("GMAIL_USER")
    pw = os.environ.get("GMAIL_APP_PASSWORD")
    to = os.environ.get("MAIL_TO") or user
    if not user or not pw:
        print("[info] 未設定 GMAIL_USER / GMAIL_APP_PASSWORD,略過寄信")
        return
    if dash.get("demo"):
        print("[info] 示範資料不寄信")
        return
    meta_path = state_dir / "meta.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        meta = {}
    date = dash["market"]["date"]
    if not force:
        if meta.get("emailed_for") == date:
            print(f"[info] {date} 的報告已寄過")
            return
        if not dash.get("data_complete"):
            print("[info] 融資融券資料尚未公布,等晚間排程再寄完整報告")
            return

    msg = MIMEMultipart()
    msg["Subject"] = f"[台股盤後分析] {date} 大盤{dash['market']['regime']['label']}"
    msg["From"] = user
    msg["To"] = to
    msg.attach(MIMEText(email_html(dash), "html", "utf-8"))
    att = MIMEApplication(report_path.read_bytes(), Name=f"stock-report-{date}.html")
    att["Content-Disposition"] = f'attachment; filename="stock-report-{date}.html"'
    msg.attach(att)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as server:
        server.login(user, pw)
        server.sendmail(user, [a.strip() for a in to.split(",")], msg.as_string())
    meta["emailed_for"] = date
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    print(f"[info] 已寄出 {date} 盤後報告至 {to}")
