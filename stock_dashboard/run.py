#!/usr/bin/env python3
"""台股智慧分析儀表板 - 資料收集與網站產生器。

用法:
  python stock_dashboard/run.py            # 抓取最新資料,產生 site/data/dashboard.json 與單檔報告
  python stock_dashboard/run.py --demo     # 使用模擬資料(無網路環境測試版面用)
  python stock_dashboard/run.py --email    # 產生後寄送盤後報告 email(需 GMAIL_USER / GMAIL_APP_PASSWORD)

免責聲明:本系統僅為公開資料的整理與機械式分析,不構成投資建議,投資盈虧請自負。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from collector import brief, build, report  # noqa: E402
from collector.collect import collect_live  # noqa: E402
from collector.demo import collect_demo  # noqa: E402


def _clean(o):
    """NaN/inf 不是合法 JSON,一律轉成 null。"""
    if isinstance(o, float):
        return None if (math.isnan(o) or math.isinf(o)) else o
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if hasattr(o, "item"):  # numpy 純量
        return _clean(o.item())
    return o


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", action="store_true", help="使用模擬資料")
    ap.add_argument("--email", action="store_true", help="寄送盤後報告 email")
    ap.add_argument("--out", default=str(ROOT / "site"), help="網站輸出目錄")
    args = ap.parse_args()

    watchlist = json.loads((ROOT / "watchlist.json").read_text(encoding="utf-8"))["stocks"]
    ctx = collect_demo(watchlist) if args.demo else collect_live(ROOT, watchlist)
    dash = build.build(ctx, watchlist)
    dash["brief"] = brief.make_brief(dash)
    dash = _clean(dash)

    out = Path(args.out)
    (out / "data").mkdir(parents=True, exist_ok=True)
    (out / "data" / "dashboard.json").write_text(
        json.dumps(dash, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    report_path = report.write_single_file(out, dash)
    print(f"已產生 {out / 'data' / 'dashboard.json'} 與 {report_path}")
    print(f"自選股 {len(dash['stocks'])} 檔、選股清單 {len(dash['screens'])} 組、"
          f"摘要來源 {dash['brief']['source']}、資料完整度 {'完整' if dash['data_complete'] else '融資券待更新'}")
    for s in dash["stocks"]:
        print(f"  {s['code']} {s['name']:<6} {s['close']:>9} {s['chg_pct']:+6.2f}%  {s['score']:5.1f} 分  {s['rating']}")

    if args.email:
        report.maybe_email(ROOT / "state", dash, report_path, force=bool(os.environ.get("FORCE_EMAIL")))


if __name__ == "__main__":
    main()
