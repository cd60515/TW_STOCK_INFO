/* 台股戰情室 前端:讀取 dashboard.json(或單檔報告內嵌資料),以 hash 路由切換各頁。 */
(function () {
  "use strict";
  const $app = document.getElementById("app");
  let D = null;
  let charts = [];

  // ---------------------------------------------------------------- helpers
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (v, d = 2) => (v == null || isNaN(v) ? "—" : Number(v).toLocaleString("zh-TW", { minimumFractionDigits: d, maximumFractionDigits: d }));
  const int = (v) => fmt(v, 0);
  const cls = (v) => (v == null ? "flat" : v > 0 ? "up" : v < 0 ? "down" : "flat");
  const pct = (v, d = 2) => (v == null ? "—" : `<span class="${cls(v)}">${v > 0 ? "+" : ""}${fmt(v, d)}%</span>`);
  const signed = (v, d = 0) => (v == null ? "—" : `<span class="${cls(v)}">${v > 0 ? "+" : ""}${fmt(v, d)}</span>`);
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const store = {
    get(k, def) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : def; } catch (e) { return def; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* 無法儲存時僅本次有效 */ } },
  };
  const stockByCode = (c) => D.stocks.find((s) => s.code === c);
  const quoteUrl = (c) => `https://tw.stock.yahoo.com/quote/${encodeURIComponent(c)}`;
  const chip = (label, c) => `<span class="chip ${c}">${esc(label)}</span>`;

  function md(text) {
    // 摘要只用到 ### 標題、- 條列、**粗體**
    let html = "", inList = false;
    for (const raw of (text || "").split("\n")) {
      const line = raw.trim();
      if (!line) continue;
      const inline = esc(line).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
      if (line.startsWith("- ") || line.startsWith("• ")) {
        if (!inList) { html += "<ul>"; inList = true; }
        html += `<li>${inline.slice(2)}</li>`;
        continue;
      }
      if (inList) { html += "</ul>"; inList = false; }
      if (line.startsWith("#")) html += `<h3>${inline.replace(/^#+\s*/, "")}</h3>`;
      else html += `<p>${inline}</p>`;
    }
    return html + (inList ? "</ul>" : "");
  }

  function destroyCharts() { charts.forEach((c) => { try { c.remove(); } catch (e) {} }); charts = []; }

  // ---------------------------------------------------------------- charts (lightweight-charts)
  function baseChart(el, opts = {}) {
    const LC = window.LightweightCharts;
    if (!LC) { el.innerHTML = '<p class="empty">圖表元件載入失敗(需要網路連線)</p>'; return null; }
    const ch = LC.createChart(el, {
      autoSize: true,
      layout: { background: { type: "solid", color: "transparent" }, textColor: css("--muted"), fontFamily: "IBM Plex Mono, monospace", fontSize: 11 },
      grid: { vertLines: { color: css("--soft") }, horzLines: { color: css("--soft") } },
      rightPriceScale: { borderColor: css("--line") },
      timeScale: { borderColor: css("--line"), rightOffset: 3, minBarSpacing: 2 },
      crosshair: { mode: 0 },
      localization: { locale: "zh-TW" },
      ...opts,
    });
    charts.push(ch);
    return ch;
  }
  const toLine = (S, key) => S.date.map((t, i) => (S[key][i] == null ? { time: t } : { time: t, value: S[key][i] }));

  function priceChart(el, S, { candles = true, mas = [5, 20, 60], bb = false, volume = true } = {}) {
    const ch = baseChart(el);
    if (!ch) return null;
    const up = css("--up"), dn = css("--down");
    if (candles) {
      const s = ch.addCandlestickSeries({ upColor: up, downColor: dn, borderUpColor: up, borderDownColor: dn, wickUpColor: up, wickDownColor: dn, priceLineVisible: false });
      s.setData(S.date.map((t, i) => ({ time: t, open: S.open[i], high: S.high[i], low: S.low[i], close: S.close[i] })));
    } else {
      const s = ch.addAreaSeries({ lineColor: css("--accent"), topColor: css("--accent-soft"), bottomColor: "transparent", lineWidth: 2, priceLineVisible: false });
      s.setData(toLine(S, "close"));
    }
    const maColors = { 5: "#e8a33d", 10: "#9b6dd6", 20: "#3f86e0", 60: "#d6609b", 120: "#5aa69a", 240: "#8a8f99" };
    mas.forEach((n) => {
      const l = ch.addLineSeries({ color: maColors[n], lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
      l.setData(toLine(S, "ma" + n));
    });
    if (bb) {
      ["bb_up", "bb_dn"].forEach((k) => {
        const l = ch.addLineSeries({ color: css("--muted"), lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
        l.setData(toLine(S, k));
      });
    }
    if (volume && S.vol_lots.some((v) => v)) {
      const v = ch.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
      ch.priceScale("vol").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
      v.setData(S.date.map((t, i) => ({ time: t, value: S.vol_lots[i] || 0, color: (S.close[i] >= (S.open[i] ?? S.close[i]) ? up : dn) + "66" })));
      ch.priceScale("right").applyOptions({ scaleMargins: { top: 0.06, bottom: 0.24 } });
    }
    const n = S.date.length;
    ch.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 120), to: n + 2 });
    return ch;
  }

  function indicatorChart(el, S, kind) {
    const ch = baseChart(el, { timeScale: { visible: false } });
    if (!ch) return null;
    const line = (key, color) => { const l = ch.addLineSeries({ color, lineWidth: 1.5, priceLineVisible: false, lastValueVisible: true }); l.setData(toLine(S, key)); return l; };
    if (kind === "kd") {
      const k = line("k", "#e8a33d"); line("d", "#3f86e0");
      [80, 20].forEach((p) => k.createPriceLine({ price: p, color: css("--line"), lineWidth: 1, lineStyle: 2, axisLabelVisible: false }));
    } else if (kind === "rsi") {
      const r = line("rsi", "#9b6dd6");
      [70, 30].forEach((p) => r.createPriceLine({ price: p, color: css("--line"), lineWidth: 1, lineStyle: 2, axisLabelVisible: false }));
    } else {
      const h = ch.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false });
      h.setData(S.date.map((t, i) => (S.osc[i] == null ? { time: t } : { time: t, value: S.osc[i], color: S.osc[i] >= 0 ? css("--up") : css("--down") })));
      line("dif", "#e8a33d"); line("dea", "#3f86e0");
    }
    return ch;
  }

  function syncCharts(a, b) {
    if (!a || !b) return;
    let lock = false;
    const link = (src, dst) => src.timeScale().subscribeVisibleLogicalRangeChange((r) => {
      if (lock || !r) return; lock = true; dst.timeScale().setVisibleLogicalRange(r); lock = false;
    });
    link(a, b); link(b, a);
    const r = a.timeScale().getVisibleLogicalRange();
    if (r) b.timeScale().setVisibleLogicalRange(r);
  }

  // 簡易正負長條圖(法人買賣超)
  function barsSVG(rows, keys, { height = 150, unit = "" } = {}) {
    if (!rows.length) return '<p class="empty">尚無資料</p>';
    const W = 640, H = height, padL = 46, padB = 18, padT = 8;
    const totals = rows.map((r) => keys.reduce((s, k) => s + (r[k.key] || 0), 0));
    const vals = rows.flatMap((r) => keys.map((k) => r[k.key] || 0)).concat(totals);
    const max = Math.max(1, ...vals.map(Math.abs));
    const y = (v) => padT + (H - padT - padB) / 2 - (v / max) * (H - padT - padB) / 2;
    const bw = (W - padL - 4) / rows.length;
    const sub = Math.max(1, (bw - 3) / keys.length);
    let g = `<line x1="${padL}" x2="${W}" y1="${y(0)}" y2="${y(0)}" stroke="currentColor" stroke-opacity=".25"/>`;
    [max, -max].forEach((v) => { g += `<text x="${padL - 4}" y="${y(v) + 3}" text-anchor="end">${fmt(v, Math.abs(max) < 10 ? 1 : 0)}${unit}</text>`; });
    g += `<text x="${padL - 4}" y="${y(0) + 3}" text-anchor="end">0</text>`;
    rows.forEach((r, i) => {
      keys.forEach((k, j) => {
        const v = r[k.key] || 0;
        const x = padL + i * bw + 1.5 + j * sub;
        g += `<rect x="${x.toFixed(1)}" y="${Math.min(y(v), y(0)).toFixed(1)}" width="${(sub - 0.6).toFixed(1)}" height="${Math.max(0.6, Math.abs(y(v) - y(0))).toFixed(1)}" fill="${k.color}"><title>${esc(r.date)} ${k.label} ${fmt(v, 1)}${unit}</title></rect>`;
      });
    });
    const step = Math.ceil(rows.length / 6);
    rows.forEach((r, i) => { if (i % step === 0 || i === rows.length - 1) g += `<text x="${(padL + i * bw + bw / 2).toFixed(1)}" y="${H - 4}" text-anchor="middle">${esc(r.date.slice(5))}</text>`; });
    const legend = keys.map((k) => `<span><i style="background:${k.color}"></i>${k.label}</span>`).join("");
    return `<div class="legend">${legend}</div><svg class="chart" viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="買賣超長條圖" style="color:var(--ink)">${g}</svg>`;
  }

  function lineSVG(points, { height = 90, color = "var(--accent)", unit = "" } = {}) {
    const pts = points.filter((p) => p.v != null);
    if (pts.length < 2) return '<p class="empty">資料累積中</p>';
    const W = 640, H = height, padL = 46, padB = 16, padT = 8;
    const vs = pts.map((p) => p.v), mn = Math.min(...vs), mx = Math.max(...vs), span = mx - mn || 1;
    const x = (i) => padL + (i / (pts.length - 1)) * (W - padL - 6);
    const y = (v) => padT + (1 - (v - mn) / span) * (H - padT - padB);
    const d = pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.v).toFixed(1)}`).join("");
    const area = `${d}L${x(pts.length - 1).toFixed(1)},${H - padB}L${padL},${H - padB}Z`;
    const last = pts[pts.length - 1];
    return `<svg class="chart" viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="趨勢圖">
      <path d="${area}" fill="${color}" fill-opacity=".12"/><path d="${d}" fill="none" stroke="${color}" stroke-width="1.8"/>
      <circle cx="${x(pts.length - 1)}" cy="${y(last.v)}" r="3.5" fill="${color}"/>
      <text x="${padL - 4}" y="${y(mx) + 3}" text-anchor="end">${fmt(mx, 0)}${unit}</text><text x="${padL - 4}" y="${y(mn) + 3}" text-anchor="end">${fmt(mn, 0)}${unit}</text>
      <text x="${padL}" y="${H - 3}">${esc(pts[0].date.slice(5))}</text><text x="${W - 6}" y="${H - 3}" text-anchor="end">${esc(last.date.slice(5))}</text></svg>`;
  }

  // ---------------------------------------------------------------- views
  function viewOverview() {
    const m = D.market, rg = m.regime, idx = m.index, br = m.breadth || {};
    const b = m.bfi[m.bfi.length - 1];
    const sectors = m.sectors || [];
    const smax = Math.max(0.5, ...sectors.map((s) => Math.abs(s.chg_pct)));
    const secRows = sectors.length > 14 ? sectors.slice(0, 7).concat(sectors.slice(-7)) : sectors;
    $app.innerHTML = `
    <section class="panel regime">
      <div><div class="label">大盤多空格局 ・ ${esc(m.date)}</div><div class="regime-label ${rg.cls}">${esc(rg.label)}</div></div>
      <div>
        <div class="row" style="justify-content:space-between"><span>多空分數 <b class="num">${fmt(rg.score, 0)}</b> / 100</span><span>建議持股水位 <b class="num">${esc(rg.exposure)}</b></span></div>
        <div class="gauge"><i style="left:${Math.max(0, Math.min(100, rg.score))}%"></i></div>
        <div class="gauge-scale"><span>空頭</span><span>盤整</span><span>多頭</span></div>
      </div>
      <ul class="sig" style="grid-column:1/-1;grid-template-columns:repeat(auto-fill,minmax(260px,1fr))">${rg.notes.map((n) => `<li class="${n.type}">${esc(n.text)}</li>`).join("")}</ul>
    </section>
    <div class="grid g-main">
      <section class="panel">
        <div class="ph"><span>加權指數 <span class="idx-num ${cls(idx.chg)}" style="margin-left:8px">${fmt(idx.close)}</span> <span class="num ${cls(idx.chg)}">${idx.chg > 0 ? "▲" : idx.chg < 0 ? "▼" : ""}${fmt(Math.abs(idx.chg))} (${idx.chg_pct > 0 ? "+" : ""}${fmt(idx.chg_pct)}%)</span></span>
          <small>月線 ${fmt(idx.ma20, 0)} ・ 季線 ${fmt(idx.ma60, 0)} ・ KD ${fmt(idx.k, 0)}/${fmt(idx.d, 0)} ・ RSI ${fmt(idx.rsi, 0)}</small></div>
        <div class="legend"><span><i style="background:#e8a33d"></i>MA5</span><span><i style="background:#3f86e0"></i>MA20</span><span><i style="background:#d6609b"></i>MA60</span></div>
        <div id="idxChart" class="chart-box" style="height:320px"></div>
        <div class="kv" style="margin-top:12px">
          <div><span class="label">上漲 / 下跌家數</span><b><span class="up">${int(br.up)}</span> / <span class="down">${int(br.down)}</span></b></div>
          <div><span class="label">漲停 / 跌停</span><b><span class="up">${int(br.limit_up)}</span> / <span class="down">${int(br.limit_down)}</span></b></div>
          <div><span class="label">上市成交值</span><b>${int(br.turnover)} 億</b></div>
          <div><span class="label">外資買賣超</span><b>${b ? signed(b.foreign, 1) + " 億" : "—"}</b></div>
          <div><span class="label">投信買賣超</span><b>${b ? signed(b.trust, 1) + " 億" : "—"}</b></div>
          <div><span class="label">自營商買賣超</span><b>${b ? signed(b.dealer, 1) + " 億" : "—"}</b></div>
        </div>
      </section>
      <section class="panel brief">
        <div class="ph"><span>盤後重點</span>${D.brief.source === "ai" ? chip("Claude AI 撰寫", "ai") : chip("規則式摘要", "neutral")}</div>
        ${md(D.brief.text)}
      </section>
    </div>
    <div class="grid g2">
      <section class="panel"><h2>三大法人買賣超 <small>單位:億元,近 20 日</small></h2>
        ${barsSVG(m.bfi, [{ key: "foreign", label: "外資", color: "#3f86e0" }, { key: "trust", label: "投信", color: "#e8a33d" }, { key: "dealer", label: "自營商", color: "#9b6dd6" }], { unit: "" })}
        <div class="ph" style="margin-top:12px"><span style="font-size:13px">大盤融資餘額</span><small>單位:億元,融資增加代表散戶槓桿升溫</small></div>
        ${lineSVG(m.margin.map((x) => ({ date: x.date, v: x.value })), { color: "var(--warn)" })}
      </section>
      <section class="panel"><h2>類股強弱 <small>依成交值加權漲跌幅;右欄為外資買賣超(億)</small></h2>
        <div class="hbars">${secRows.map((s) => {
          const w = (Math.abs(s.chg_pct) / smax) * 50;
          const left = s.chg_pct >= 0 ? 50 : 50 - w;
          return `<div class="hbar"><span>${esc(s.name)}</span><span class="track"><i style="left:${left}%;width:${w}%;background:var(${s.chg_pct >= 0 ? "--up" : "--down"})"></i></span><span class="v">${pct(s.chg_pct)}<br><small class="${cls(s.foreign)}">${s.foreign > 0 ? "+" : ""}${fmt(s.foreign, 1)}</small></span></div>`;
        }).join("") || '<p class="empty">尚無類股資料</p>'}</div>
      </section>
    </div>
    <div class="grid g2">
      <section class="panel"><h2>成交值排行 <small>資金最集中的個股</small></h2>
        <div class="tbl-wrap"><table class="t"><thead><tr><th>股票</th><th>收盤</th><th>漲跌</th><th>成交值(億)</th><th>外資(張)</th></tr></thead><tbody>
        ${m.top_turnover.map((r) => `<tr${rowAttr(r.code)}><td>${nameCell(r.code, r.name)}</td><td>${fmt(r.close)}</td><td>${pct(r.chg_pct)}</td><td>${fmt(r.turnover, 1)}</td><td>${signed(r.foreign)}</td></tr>`).join("")}
        </tbody></table></div></section>
      <section class="panel"><h2>市場新聞 <small>標題情緒為關鍵字判讀,僅供快速篩選</small></h2>${newsList(m.news.slice(0, 14))}</section>
    </div>`;
    const ch = priceChart(document.getElementById("idxChart"), m.index_series, { mas: [5, 20, 60], volume: false });
    if (ch) ch.timeScale().setVisibleLogicalRange({ from: m.index_series.date.length - 160, to: m.index_series.date.length + 2 });
    bindGo();
  }

  function newsList(items) {
    if (!items || !items.length) return '<p class="empty">暫無新聞</p>';
    const t = { pos: "利多", neg: "利空", neu: "中性" };
    return `<ul class="news">${items.map((n) => `<li><span class="tone ${n.tone}">${t[n.tone]}</span><div><a href="${esc(n.link)}" target="_blank" rel="noopener">${esc(n.title)}</a><div class="meta">${esc(n.source)} ・ ${esc(n.time)}</div></div></li>`).join("")}</ul>`;
  }

  // 自選股:整列可點進個股頁;其他股票:名稱連到 Yahoo 股市(新分頁)
  const nameCell = (code, name) => stockByCode(code)
    ? `<span class="cd">${esc(code)}</span><span class="nm">${esc(name)}</span>`
    : `<a href="${quoteUrl(code)}" target="_blank" rel="noopener"><span class="cd">${esc(code)}</span><span class="nm">${esc(name)}</span></a>`;
  const rowAttr = (code) => (stockByCode(code) ? ` class="click" data-go="${esc(code)}"` : "");
  function bindGo() {
    $app.querySelectorAll("[data-go]").forEach((el) => el.addEventListener("click", () => {
      location.hash = "stock-" + el.getAttribute("data-go");
    }));
  }

  let wlSort = store.get("wlSort", { key: "score", dir: -1 });
  function viewWatchlist() {
    const cols = [
      { key: "name", label: "股票", l: true }, { key: "close", label: "收盤" }, { key: "chg_pct", label: "漲跌%" },
      { key: "chg5", label: "5日%" }, { key: "chg20", label: "20日%" }, { key: "score", label: "評分" }, { key: "rating", label: "評等" },
      { key: "f5", label: "外資5日" }, { key: "t5", label: "投信5日" }, { key: "k", label: "K值" }, { key: "rsi", label: "RSI" }, { key: "sig", label: "重點訊號", l: true },
    ];
    const rows = D.stocks.map((s) => ({ ...s, f5: s.chips?.foreign5 ?? null, t5: s.chips?.trust5 ?? null }));
    rows.sort((a, b) => {
      const va = a[wlSort.key], vb = b[wlSort.key];
      if (va == null) return 1; if (vb == null) return -1;
      return (va > vb ? 1 : va < vb ? -1 : 0) * wlSort.dir;
    });
    const counts = { bull: D.stocks.filter((s) => s.score >= 58).length, neu: D.stocks.filter((s) => s.score >= 42 && s.score < 58).length, bear: D.stocks.filter((s) => s.score < 42).length };
    $app.innerHTML = `
    <section class="panel">
      <div class="ph"><span>自選股評分總表 <small>點欄位排序、點股票看完整分析</small></span>
        <small>偏多 <b class="up num">${counts.bull}</b> ・ 中性 <b class="num">${counts.neu}</b> ・ 偏空 <b class="down num">${counts.bear}</b></small></div>
      <div class="tbl-wrap"><table class="t"><thead><tr>${cols.map((c) => `<th class="${c.l ? "l " : ""}${c.key !== "sig" ? "sortable" : ""}" data-k="${c.key}">${c.label}${wlSort.key === c.key ? (wlSort.dir > 0 ? " ▲" : " ▼") : ""}</th>`).join("")}</tr></thead>
      <tbody>${rows.map((s) => {
        const top = s.signals.filter((x) => x.type !== "info").slice(0, 2).map((x) => `<span class="${x.type === "bull" ? "up" : "down"}">●</span> ${esc(x.text)}`).join("<br>") || "—";
        return `<tr class="click" data-go="${esc(s.code)}"><td><span class="cd">${esc(s.code)}</span><span class="nm">${esc(s.name)}</span></td>
          <td>${fmt(s.close)}</td><td>${pct(s.chg_pct)}</td><td>${pct(s.chg5, 1)}</td><td>${pct(s.chg20, 1)}</td>
          <td><span class="scorebar"><b>${fmt(s.score, 0)}</b><span><i style="width:${s.score}%"></i></span></span></td>
          <td>${chip(s.rating, s.rating_cls)}</td><td>${signed(s.f5)}</td><td>${signed(s.t5)}</td><td>${fmt(s.k, 0)}</td><td>${fmt(s.rsi, 0)}</td><td class="txt">${top}</td></tr>`;
      }).join("")}</tbody></table></div>
    </section>
    <section class="panel"><h2>如何修改自選股</h2><p class="muted" style="margin:0">在 GitHub repo 編輯 <code class="num">stock_dashboard/watchlist.json</code>,加入或刪除 <code class="num">{"code": "2330", "name": "台積電", "market": "TWSE"}</code> 並 commit,系統會自動重新抓資料並更新網站(約 3~6 分鐘)。上櫃股票 market 請填 <code class="num">TPEX</code>。</p></section>`;
    $app.querySelectorAll("th.sortable").forEach((th) => th.addEventListener("click", () => {
      const k = th.getAttribute("data-k");
      wlSort = { key: k, dir: wlSort.key === k ? -wlSort.dir : (k === "name" ? 1 : -1) };
      store.set("wlSort", wlSort);
      viewWatchlist();
    }));
    bindGo();
  }

  let indKind = store.get("indKind", "kd");
  let showBB = store.get("showBB", false);
  function viewStock(code) {
    const s = stockByCode(code) || D.stocks[0];
    if (!s) { $app.innerHTML = '<p class="empty">尚無自選股資料</p>'; return; }
    store.set("lastStock", s.code);
    const c = s.chips, p = s.plan, v = s.valuation || {}, rv = s.revenue || {};
    const bearish = s.score < 42;
    const groups = {};
    s.breakdown.forEach((b) => { (groups[b.group] = groups[b.group] || []).push(b); });
    const meters = [["技術面", s.parts.tech], ["籌碼面", s.parts.chips], ["消息面", s.parts.news], ["基本面", s.parts.fundamental]];
    const chipRows = c ? c.series.slice(-30) : [];
    $app.innerHTML = `
    <div class="picker">${D.stocks.map((x) => `<button type="button" data-code="${esc(x.code)}" class="${x.code === s.code ? "on" : ""}">${esc(x.name)}</button>`).join("")}</div>
    <section class="panel">
      <div class="stock-head">
        <div><div class="stock-title">${esc(s.name)}<span class="cd">${esc(s.code)}${s.industry ? " ・ " + esc(s.industry) : ""}</span></div>
          <div class="row" style="margin-top:6px">${chip(s.rating, s.rating_cls)}<span class="muted">綜合評分 <b class="num" style="color:var(--ink)">${fmt(s.score, 1)}</b> / 100</span><span class="muted">資料日 ${esc(s.date)}</span></div></div>
        <div style="text-align:right"><div class="stock-price ${cls(s.chg)}">${fmt(s.close)}</div><div class="num">${signed(s.chg, 2)} (${pct(s.chg_pct)})</div></div>
      </div>
      <div class="meters" style="margin-top:14px">${meters.map(([n, val]) => `<div class="meter"><span class="label">${n}</span><b>${val == null ? "—" : fmt(val, 0)}</b><span class="bar"><i style="width:${val || 0}%"></i></span></div>`).join("")}</div>
      <p class="view" style="margin-top:14px">${esc(s.view)}</p>
    </section>
    <section class="panel">
      <div class="ph"><span>K 線與技術指標</span>
        <span class="seg"><button type="button" data-bb class="${showBB ? "on" : ""}">布林通道</button>
        ${[["kd", "KD"], ["macd", "MACD"], ["rsi", "RSI"]].map(([k, l]) => `<button type="button" data-ind="${k}" class="${indKind === k ? "on" : ""}">${l}</button>`).join("")}</span></div>
      <div class="legend"><span><i style="background:#e8a33d"></i>MA5 ${fmt(s.series.ma5.at(-1))}</span><span><i style="background:#9b6dd6"></i>MA10 ${fmt(s.series.ma10.at(-1))}</span><span><i style="background:#3f86e0"></i>MA20 ${fmt(s.series.ma20.at(-1))}</span><span><i style="background:#d6609b"></i>MA60 ${fmt(s.series.ma60.at(-1))}</span><span><i style="background:#5aa69a"></i>MA120 ${fmt(s.series.ma120.at(-1))}</span></div>
      <div id="pxChart" class="chart-box"></div>
      <div class="legend">${indKind === "kd" ? `<span><i style="background:#e8a33d"></i>K ${fmt(s.k, 1)}</span><span><i style="background:#3f86e0"></i>D ${fmt(s.d, 1)}</span>` : indKind === "rsi" ? `<span><i style="background:#9b6dd6"></i>RSI(14) ${fmt(s.rsi, 1)}</span>` : `<span><i style="background:#e8a33d"></i>DIF</span><span><i style="background:#3f86e0"></i>MACD</span><span>柱狀 OSC ${fmt(s.osc)}</span>`}</div>
      <div id="indChart" class="chart-sub"></div>
    </section>
    <div class="grid g2">
      <section class="panel plan">
        <div class="ph"><span>交易計畫</span>${chip(p.stance, s.rating_cls)}</div>
        <div>${esc(p.entry)}</div>
        <div class="levels">
          <div class="lv stop"><span class="label">${bearish ? "持有者停損" : "停損參考"}</span><b>${fmt(p.stop)}</b><small>${p.stop_pct}%</small></div>
          <div class="lv tgt"><span class="label">${bearish ? "反彈壓力" : "第一目標"}</span><b>${fmt(p.target1)}</b><small>+${p.target1_pct}%</small></div>
          <div class="lv tgt"><span class="label">${bearish ? "次壓力" : "第二目標"}</span><b>${fmt(p.target2)}</b><small>+${p.target2_pct}%</small></div>
        </div>
        <div class="kv">
          <div><span class="label">風險報酬比</span><b>${p.rr == null ? "—" : "1 : " + fmt(p.rr, 2)}</b></div>
          <div><span class="label">ATR(日波動)</span><b>${fmt(p.atr)} <small class="muted">${p.atr_pct}%</small></b></div>
          <div><span class="label">支撐</span><b style="font-size:13px">${p.supports.map((x) => fmt(x)).join(" / ") || "—"}</b></div>
          <div><span class="label">壓力</span><b style="font-size:13px">${p.resistances.map((x) => fmt(x)).join(" / ") || "—"}</b></div>
          <div><span class="label">52 週高 / 低</span><b style="font-size:13px">${fmt(s.high52)} / ${fmt(s.low52)}</b></div>
        </div>
        ${sizer(s)}
      </section>
      <section class="panel"><h2>今日訊號 <small>紅=偏多、綠=偏空、黃=提醒</small></h2>
        <ul class="sig">${s.signals.map((x) => `<li class="${x.type}">${esc(x.text)}</li>`).join("") || '<li class="info">今日無特別訊號</li>'}</ul></section>
    </div>
    <div class="grid g2">
      <section class="panel"><h2>法人籌碼 <small>單位:張,近 30 個交易日</small></h2>
        ${c ? `<div class="kv" style="margin-bottom:8px">
          <div><span class="label">外資 5 日 / 20 日</span><b>${signed(c.foreign5)} / ${signed(c.foreign20)}</b></div>
          <div><span class="label">投信 5 日 / 20 日</span><b>${signed(c.trust5)} / ${signed(c.trust20)}</b></div>
          <div><span class="label">外資連續</span><b>${c.foreign_streak > 0 ? `<span class="up">買 ${c.foreign_streak} 日</span>` : c.foreign_streak < 0 ? `<span class="down">賣 ${-c.foreign_streak} 日</span>` : "—"}</b></div>
          <div><span class="label">投信連續</span><b>${c.trust_streak > 0 ? `<span class="up">買 ${c.trust_streak} 日</span>` : c.trust_streak < 0 ? `<span class="down">賣 ${-c.trust_streak} 日</span>` : "—"}</b></div>
          <div><span class="label">外資買超佔量</span><b>${c.foreign5_pct_vol == null ? "—" : pct(c.foreign5_pct_vol, 1)}</b></div>
          <div><span class="label">外資持股比</span><b>${c.foreign_hold == null ? "—" : fmt(c.foreign_hold) + "%"} ${c.foreign_hold_chg != null ? `<small>${signed(c.foreign_hold_chg, 2)}</small>` : ""}</b></div>
        </div>
        ${barsSVG(chipRows, [{ key: "foreign", label: "外資", color: "#3f86e0" }, { key: "trust", label: "投信", color: "#e8a33d" }, { key: "dealer", label: "自營商", color: "#9b6dd6" }])}` : '<p class="empty">上櫃股票/ETF 暫無三大法人資料</p>'}
      </section>
      <section class="panel"><h2>融資融券 <small>融資=散戶槓桿;券資比高具軋空潛力</small></h2>
        ${c && c.margin != null ? `<div class="kv" style="margin-bottom:8px">
          <div><span class="label">融資餘額</span><b>${int(c.margin)} 張</b></div>
          <div><span class="label">融資 5 日增減</span><b>${pct(c.margin_chg5)}</b></div>
          <div><span class="label">融券餘額</span><b>${int(c.short)} 張</b></div>
          <div><span class="label">券資比</span><b>${c.short_ratio == null ? "—" : fmt(c.short_ratio, 1) + "%"}</b></div></div>
          ${lineSVG(c.series.map((x) => ({ date: x.date, v: x.margin })), { color: "var(--warn)" })}` : '<p class="empty">暫無融資融券資料</p>'}
      </section>
    </div>
    <div class="grid g2">
      <section class="panel"><h2>訊號歷史勝率 <small>本股近兩年同一訊號出現後的表現</small></h2>
        <div class="tbl-wrap"><table class="t"><thead><tr><th>訊號</th><th>次數</th><th>5日勝率</th><th>10日勝率</th><th>10日均報酬</th><th>20日勝率</th></tr></thead><tbody>
        ${s.backtest.map((b) => `<tr${b.active ? ' style="background:var(--accent-soft)"' : ""}><td class="l" style="font-family:var(--font-body)"><span class="${b.side === "bull" ? "up" : "down"}">●</span> ${esc(b.name)}${b.active ? " ・今日" : ""}</td><td>${b.count}</td><td>${b.win5 == null ? "—" : b.win5 + "%"}</td><td>${b.win10 == null ? "—" : b.win10 + "%"}</td><td>${pct(b.avg10)}</td><td>${b.win20 == null ? "—" : b.win20 + "%"}</td></tr>`).join("")}
        </tbody></table></div>
        <p class="muted" style="font-size:12px;margin:8px 0 0">多方訊號以「之後上漲」為勝、空方訊號以「之後下跌」為勝。樣本少於 5 次時參考價值有限。</p></section>
      <section class="panel"><h2>基本面</h2>
        <div class="kv">
          <div><span class="label">本益比</span><b>${fmt(v.pe, 1)}</b></div><div><span class="label">殖利率</span><b>${v.yield == null ? "—" : fmt(v.yield) + "%"}</b></div>
          <div><span class="label">股價淨值比</span><b>${fmt(v.pb)}</b></div><div><span class="label">月營收 ${esc(rv.ym || "")}</span><b>${rv.rev == null ? "—" : fmt(rv.rev, 1) + " 億"}</b></div>
          <div><span class="label">營收月增</span><b>${pct(rv.mom, 1)}</b></div><div><span class="label">營收年增</span><b>${pct(rv.yoy, 1)}</b></div>
          <div><span class="label">累計營收年增</span><b>${pct(rv.cum_yoy, 1)}</b></div>
          <div><span class="label">漲跌 5/20/60/240 日</span><b style="font-size:12.5px">${pct(s.chg5, 1)} ${pct(s.chg20, 1)} ${pct(s.chg60, 1)} ${pct(s.chg240, 1)}</b></div>
        </div>
        <h2 style="margin-top:16px">評分明細</h2>
        <div class="tbl-wrap"><table class="t"><tbody>${Object.entries(groups).map(([g, items]) => items.map((b, i) => `<tr><td class="l muted" style="font-family:var(--font-body)">${i === 0 ? g : ""}</td><td class="l" style="font-family:var(--font-body)">${esc(b.label)}</td><td>${fmt(b.pts, 1)} / ${b.max}</td></tr>`).join("")).join("")}</tbody></table></div>
      </section>
    </div>
    <section class="panel"><h2>${esc(s.name)} 相關新聞 <small>近 7 日 ・ 情緒 ${esc(s.news_summary.label)}(正面 ${s.news_summary.pos || 0}、負面 ${s.news_summary.neg || 0})</small></h2>${newsList(s.news)}
      <p style="margin:8px 0 0;font-size:13px"><a href="${quoteUrl(s.code)}" target="_blank" rel="noopener">在 Yahoo 股市查看即時報價 →</a></p></section>`;

    const pc = priceChart(document.getElementById("pxChart"), s.series, { mas: [5, 10, 20, 60, 120], bb: showBB });
    const ic = indicatorChart(document.getElementById("indChart"), s.series, indKind);
    syncCharts(pc, ic);
    $app.querySelectorAll("[data-code]").forEach((b) => b.addEventListener("click", () => { location.hash = "stock-" + b.getAttribute("data-code"); }));
    $app.querySelectorAll("[data-ind]").forEach((b) => b.addEventListener("click", () => { indKind = b.getAttribute("data-ind"); store.set("indKind", indKind); viewStock(s.code); }));
    $app.querySelector("[data-bb]").addEventListener("click", () => { showBB = !showBB; store.set("showBB", showBB); viewStock(s.code); });
    bindSizer(s);
  }

  function sizer(s) {
    const cap = store.get("capital", 1000000), risk = store.get("riskPct", 1);
    return `<div style="border-top:1px solid var(--line);padding-top:10px">
      <div class="label" style="margin-bottom:6px">部位計算機:依「最多可承受虧損」反推可買張數</div>
      <form class="row" id="sizer">
        <label class="field" for="szCap">總資金(元)<input id="szCap" type="number" min="0" step="10000" value="${cap}"></label>
        <label class="field" for="szRisk">單筆風險 %<input id="szRisk" type="number" min="0.1" max="10" step="0.1" value="${risk}" style="width:80px"></label>
        <label class="field" for="szEntry">進場價<input id="szEntry" type="number" step="0.01" value="${s.close}" style="width:96px"></label>
        <label class="field" for="szStop">停損價<input id="szStop" type="number" step="0.01" value="${s.plan.stop}" style="width:96px"></label>
      </form>
      <div class="calc-out" id="szOut" style="margin-top:8px"></div></div>`;
  }
  function bindSizer() {
    const f = document.getElementById("sizer");
    if (!f) return;
    const calc = () => {
      const cap = +document.getElementById("szCap").value, risk = +document.getElementById("szRisk").value;
      const entry = +document.getElementById("szEntry").value, stop = +document.getElementById("szStop").value;
      store.set("capital", cap); store.set("riskPct", risk);
      const out = document.getElementById("szOut");
      if (!(cap > 0 && entry > 0 && stop > 0) || stop >= entry) { out.innerHTML = '<span class="muted">停損價需低於進場價</span>'; return; }
      const maxLoss = cap * risk / 100, perShare = entry - stop;
      let shares = Math.floor(maxLoss / perShare);
      shares = Math.min(shares, Math.floor(cap / entry));
      const lots = Math.floor(shares / 1000), odd = shares % 1000;
      out.innerHTML = `最多可虧 <b>${int(maxLoss)}</b> 元 → 可買 <b>${int(shares)}</b> 股(<b>${lots}</b> 張${odd ? ` + ${odd} 股零股` : ""}),投入約 <b>${int(shares * entry)}</b> 元,佔資金 <b>${fmt(shares * entry / cap * 100, 1)}%</b>`;
    };
    f.addEventListener("input", calc);
    f.addEventListener("submit", (e) => e.preventDefault());
    calc();
  }

  let radarKey = store.get("radarKey", null);
  function viewRadar() {
    const screens = D.screens || [];
    if (!screens.length) { $app.innerHTML = '<section class="panel"><p class="empty">選股資料累積中(需要至少一個交易日的全市場資料)</p></section>'; return; }
    const cur = screens.find((x) => x.key === radarKey) || screens[0];
    $app.innerHTML = `
    <section class="panel">
      <div class="ph"><span>選股雷達 <small>每日掃描全體上市股票</small></span></div>
      <div class="radar-tabs seg">${screens.map((x) => `<button type="button" data-rk="${x.key}" class="${x.key === cur.key ? "on" : ""}">${esc(x.title)} <span class="num" style="opacity:.7">${x.rows.length}</span></button>`).join("")}</div>
      <p class="muted" style="margin:12px 0 8px">${esc(cur.desc)}</p>
      ${cur.rows.length ? `<div class="tbl-wrap"><table class="t"><thead><tr><th>股票</th><th>收盤</th><th>漲跌</th><th>成交值(億)</th><th>${esc(cur.rows[0].mlabel)}</th><th></th></tr></thead><tbody>
      ${cur.rows.map((r) => `<tr${rowAttr(r.code)}><td>${nameCell(r.code, r.name)}</td><td>${fmt(r.close)}</td><td>${pct(r.chg_pct)}</td><td>${fmt(r.turnover, 1)}</td><td><b>${fmt(r.metric, Number.isInteger(r.metric) ? 0 : 2)}</b></td><td class="muted" style="font-family:var(--font-body);font-size:12px">${stockByCode(r.code) ? "看分析 →" : `<a href="${quoteUrl(r.code)}" target="_blank" rel="noopener">Yahoo →</a>`}</td></tr>`).join("")}
      </tbody></table></div>` : '<p class="empty">今日沒有符合條件的股票</p>'}
    </section>
    <section class="panel"><h2>使用建議</h2><ul class="sig">
      <li class="info">雷達是「找候選名單」的工具:挑出有興趣的股票後,加入自選股讓系統做完整技術、籌碼、消息評分。</li>
      <li class="info">「土洋同買」「投信連買」偏向波段;「爆量長紅」偏向短線,隔日容易震盪,適合等拉回。</li>
      <li class="info">「高息低估」適合存股或防禦配置,但仍需確認獲利是否穩定、殖利率是否來自一次性配息。</li>
    </ul></section>`;
    $app.querySelectorAll("[data-rk]").forEach((b) => b.addEventListener("click", () => { radarKey = b.getAttribute("data-rk"); store.set("radarKey", radarKey); viewRadar(); }));
    bindGo();
  }

  function knownPrice(code) {
    const s = stockByCode(code);
    if (s) return { close: s.close, name: s.name, chg_pct: s.chg_pct, plan: s.plan };
    const t = D.market.top_turnover.find((x) => x.code === code);
    if (t) return { close: t.close, name: t.name, chg_pct: t.chg_pct };
    for (const sc of D.screens) { const r = sc.rows.find((x) => x.code === code); if (r) return { close: r.close, name: r.name, chg_pct: r.chg_pct }; }
    return null;
  }

  function viewPortfolio() {
    let hold = store.get("holdings", null);
    const sample = hold == null;
    if (sample) hold = D.stocks.slice(0, 2).map((s) => ({ code: s.code, shares: 1000, cost: +(s.close * 0.95).toFixed(2), sample: true }));
    let tv = 0, tc = 0;
    const rows = hold.map((h, i) => {
      const q = knownPrice(h.code);
      const val = q ? q.close * h.shares : null, cost = h.cost * h.shares;
      if (val != null) { tv += val; tc += cost; }
      const pl = val != null ? val - cost : null;
      let alert = "—";
      if (q && q.plan) {
        if (q.close <= q.plan.stop) alert = '<span class="chip bear2">跌破停損</span>';
        else if (q.close >= q.plan.target1) alert = '<span class="chip bull2">達第一目標</span>';
        else alert = `<span class="muted" style="font-size:12px">停損 ${fmt(q.plan.stop)} ・ 目標 ${fmt(q.plan.target1)}</span>`;
      }
      return `<tr><td><span class="cd">${esc(h.code)}</span><span class="nm">${esc(q ? q.name : "")}</span>${h.sample ? ' <span class="chip neutral">範例</span>' : ""}</td>
        <td>${int(h.shares)}</td><td>${fmt(h.cost)}</td><td>${q ? fmt(q.close) : "—"}</td><td>${q ? pct(q.chg_pct) : "—"}</td>
        <td>${pl == null ? "—" : signed(pl)}</td><td>${pl == null ? "—" : pct(pl / cost * 100)}</td><td class="l">${alert}</td>
        <td><button type="button" class="btn" data-del="${i}" aria-label="刪除 ${esc(h.code)}">刪除</button></td></tr>`;
    }).join("");
    $app.innerHTML = `
    <section class="panel">
      <div class="ph"><span>我的持股 <small>只存在你這台裝置的瀏覽器中,不會上傳</small></span>
        <span>總損益 <b class="num">${signed(tv - tc)}</b> 元 <span class="num">(${tc ? pct((tv - tc) / tc * 100) : "—"})</span></span></div>
      ${sample ? '<p class="muted" style="margin:0 0 8px">下表為範例持股,新增第一筆後會自動清除範例。</p>' : ""}
      <div class="tbl-wrap"><table class="t"><thead><tr><th>股票</th><th>股數</th><th>成本</th><th>現價</th><th>今日</th><th>損益(元)</th><th>報酬率</th><th class="l">停損/目標提醒</th><th></th></tr></thead><tbody>${rows || '<tr><td colspan="9" class="empty">尚無持股</td></tr>'}</tbody></table></div>
      <form class="row" id="addHold" style="margin-top:12px">
        <label class="field" for="hCode">股票代號<input id="hCode" required placeholder="2330" style="width:90px"></label>
        <label class="field" for="hShares">股數<input id="hShares" type="number" min="1" required value="1000" style="width:100px"></label>
        <label class="field" for="hCost">成本價<input id="hCost" type="number" step="0.01" min="0" required style="width:100px"></label>
        <button class="btn primary" type="submit">新增持股</button>
      </form>
      <p class="muted" style="font-size:12px;margin:8px 0 0">現價取自本系統最新收盤資料(自選股、成交值排行、選股雷達中的股票)。停損/目標提醒只對自選股有效。</p>
    </section>`;
    document.getElementById("addHold").addEventListener("submit", (e) => {
      e.preventDefault();
      const code = document.getElementById("hCode").value.trim().toUpperCase();
      const shares = +document.getElementById("hShares").value, cost = +document.getElementById("hCost").value;
      if (!code || !(shares > 0) || !(cost > 0)) return;
      const cur = (store.get("holdings", null) || []).filter((h) => !h.sample);
      cur.push({ code, shares, cost });
      store.set("holdings", cur);
      viewPortfolio();
    });
    $app.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", () => {
      const cur = hold.filter((h) => !h.sample);
      if (!sample) cur.splice(+b.getAttribute("data-del"), 1);
      store.set("holdings", cur);
      viewPortfolio();
    }));
  }

  function viewGuide() {
    $app.innerHTML = `<section class="panel guide">
      <h2 style="font-size:18px">系統說明</h2>
      <h3>每天自動做什麼</h3>
      <ul>
        <li>台股收盤後由 GitHub Actions 自動執行(約 17:30 與 22:30 兩次;第二次補上較晚公布的融資融券資料)。</li>
        <li>收集:加權指數與個股日線、三大法人買賣超、融資融券、外資持股比、本益比/殖利率、月營收、產業別、個股與市場新聞。</li>
        <li>分析後更新本網站,並寄出盤後報告 email(附單檔網頁版,手機可直接開)。</li>
      </ul>
      <h3>綜合評分(0–100)怎麼算</h3>
      <ul>
        <li><b>技術面 40 分</b>:趨勢(月線、季線、均線排列)、動能(KD、MACD、RSI)、量價型態(帶量突破、價量背離)。</li>
        <li><b>籌碼面 35 分</b>:外資/投信 5 日買賣超與連續買賣天數、三大法人 20 日累計、融資增減與股價的關係。</li>
        <li><b>消息面 15 分</b>:近 7 日新聞標題的利多/利空關鍵字情緒。</li>
        <li><b>基本面 10 分</b>:本益比、殖利率、月營收年增率。</li>
        <li>評等:72 以上「強勢偏多」、58–72「偏多」、42–58「中性觀望」、30–42「偏空」、30 以下「弱勢偏空」。每一項加減分都列在個股頁的「評分明細」。</li>
      </ul>
      <h3>交易計畫怎麼產生</h3>
      <ul>
        <li>支撐取 MA10/MA20/MA60、20 日低點、布林下軌中最接近現價者;壓力取 20/60 日高點、布林上軌、一年高點。</li>
        <li>停損 = 最近支撐再扣 0.5 個 ATR(若距離不合理則用 2 個 ATR);目標取至少 1 個 ATR 以外的壓力位。</li>
        <li>建議搭配「部位計算機」:每筆交易最多虧總資金 1%–2%,讓停損真的守得住。</li>
      </ul>
      <h3>大盤多空與持股水位</h3>
      <p>以指數與月線/季線位置及斜率、MACD、外資 5 日買賣超、漲跌家數計分。大盤偏空時,即使個股評分高也建議降低部位。</p>
      <h3>注意事項</h3>
      <ul>
        <li>資料為收盤後公開資料,不是即時報價;盤中請以券商系統為準。</li>
        <li>網頁會自動檢查資料是否過期(已扣除週末與證交所休市日):落後 1 個交易日顯示黃色提醒,落後 2 個以上顯示紅色警告,代表自動排程可能停止,請到 GitHub Actions 查看執行紀錄。</li>
        <li>新聞情緒是關鍵字判讀,可能誤判反諷或複雜語意,重要消息請點進原文確認。</li>
        <li>所有分數、訊號與價位都是機械式規則的結果,不保證獲利,也不構成投資建議。</li>
      </ul>
    </section>`;
  }

  // ---------------------------------------------------------------- router
  function route() {
    destroyCharts();
    const h = (location.hash || "#overview").slice(1);
    let view = h, code = null;
    if (h.startsWith("stock")) { view = "stock"; code = h.split("-")[1] || store.get("lastStock", null); }
    const views = { overview: viewOverview, watchlist: viewWatchlist, stock: () => viewStock(code), radar: viewRadar, portfolio: viewPortfolio, guide: viewGuide };
    (views[view] || viewOverview)();
    document.querySelectorAll("#tabs a").forEach((a) => a.classList.toggle("on", a.dataset.view === (views[view] ? view : "overview")));
    window.scrollTo(0, 0);
  }

  // ---------------------------------------------------------------- 資料是否過期
  // 計算「資料日之後、到今天為止應該已有資料」的交易日數。收盤資料要到晚間排程(22:20)
  // 才會完整,所以台北時間 23:00 前不把今天算進去;週末與證交所休市日不算。
  function missedTradingDays(dataDate, holidays, now = new Date()) {
    const tpe = new Date(now.getTime() + 8 * 3600 * 1000); // 以 UTC getter 讀台北時間
    const end = new Date(Date.UTC(tpe.getUTCFullYear(), tpe.getUTCMonth(), tpe.getUTCDate()));
    if (tpe.getUTCHours() < 23) end.setUTCDate(end.getUTCDate() - 1);
    const off = new Set(holidays || []);
    const d = new Date(dataDate + "T00:00:00Z");
    let n = 0;
    for (d.setUTCDate(d.getUTCDate() + 1); d <= end; d.setUTCDate(d.getUTCDate() + 1)) {
      const wd = d.getUTCDay();
      if (wd !== 0 && wd !== 6 && !off.has(d.toISOString().slice(0, 10))) n++;
    }
    return n;
  }

  function checkStale() {
    const el = document.getElementById("staleBanner");
    const n = missedTradingDays(D.market.date, D.holidays);
    const where = D.actions_url ? `<a href="${esc(D.actions_url)}" target="_blank" rel="noopener">GitHub Actions 執行紀錄</a>` : "GitHub Actions 執行紀錄";
    el.classList.toggle("stale", n >= 2);
    el.classList.toggle("late", n === 1);
    document.getElementById("stamp").classList.toggle("stale", n >= 2);
    if (n >= 2) el.innerHTML = `資料已過期:最新資料日為 ${esc(D.market.date)},已落後 ${n} 個交易日。自動更新可能已停止,請檢查${where}。`;
    else if (n === 1) el.innerHTML = `資料日 ${esc(D.market.date)} 落後 1 個交易日,可能是排程延遲,通常稍後會自動更新;若明天仍未更新請檢查${where}。`;
    el.hidden = D.demo || n < 1;
  }

  function start(data) {
    D = data;
    document.getElementById("demoBanner").hidden = !D.demo;
    document.getElementById("stamp").innerHTML = `資料日 <b class="num">${esc(D.market.date)}</b><br>更新於 ${esc(D.generated_at)}${D.data_complete ? "" : " ・ 融資券待更新"}`;
    checkStale();
    setInterval(checkStale, 10 * 60 * 1000);
    route();
    window.addEventListener("hashchange", route);
    const mq = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)");
    if (mq && mq.addEventListener) mq.addEventListener("change", route);
    new MutationObserver(route).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  }

  if (window.__DASHBOARD__) start(window.__DASHBOARD__);
  else fetch("data/dashboard.json", { cache: "no-cache" })
    .then((r) => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
    .then(start)
    .catch((e) => { $app.innerHTML = `<section class="panel"><p>無法載入資料(${esc(e.message)})。請確認 GitHub Actions 已執行過一次,或直接開啟每日 email 附件中的單檔報告。</p></section>`; });
})();
