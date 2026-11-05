/* 期货品种分析工作台 —— 前端逻辑 */
(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const fmt = (n, d = 0) => (n == null ? "—" : Number(n).toLocaleString("zh-CN", { maximumFractionDigits: d }));
  const pct = (n) => (n == null ? "—" : (n > 0 ? "+" : "") + n.toFixed(2) + "%");
  const cls = (n) => (n > 0 ? "up" : n < 0 ? "down" : "muted");
  // 将 8 位日期(20261103)或带横线日期格式化为 YYYY-MM-DD，便于展示
  const fmtDate = (d) => {
    if (d == null || d === "") return "—";
    const s = String(d).replace(/-/g, "");
    if (/^\d{8}$/.test(s)) return `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}`;
    return String(d);
  };

  let CATS = null, curCat = null, curProd = null;
  let SENT = null;            // 全市场机构情绪总览（含板块/榜单/净情绪排行）
  let resizeBound = false;
  let OPINIONS = [];          // 当前品种的机构研报观点流
  let OP_FILTER = 15;         // 时效窗口（天），0 = 全部
  let OP_EXPANDED = false;    // 观点列表是否展开
  let scrollToOpinion = false; // 从情绪榜点选品种后，自动定位到该品种的机构观点

  // ---- 刷新状态/时间：注入到「数据日期」展示（在线网页任意设备可见）----
  let dataDateBase = "";
  let REFRESH_INFO = null;
  async function loadRefreshInfo() {
    try {
      const r = await fetch('/api/refresh_status');
      REFRESH_INFO = await r.json().catch(() => null);
    } catch (e) { REFRESH_INFO = null; }
    paintDataDate();
  }
  function refreshSuffix() {
    if (!REFRESH_INFO || REFRESH_INFO.available === false) return "";
    const parts = [];
    parts.push("· 刷新 " + (REFRESH_INFO.finished_at || REFRESH_INFO.generated_at || ""));
    if (REFRESH_INFO.build_all_ok != null)
      parts.push("构建" + (REFRESH_INFO.build_all_ok ? "✓" : "✗"));
    if (REFRESH_INFO.kline_ok != null) parts.push("K线" + REFRESH_INFO.kline_ok);
    if (REFRESH_INFO.warehouse_ok != null)
      parts.push("仓单" + (REFRESH_INFO.warehouse_ok ? "✓" : "✗"));
    if (REFRESH_INFO.sentiment_ok != null)
      parts.push("情绪" + (REFRESH_INFO.sentiment_ok ? "✓" : "✗"));
    return " " + parts.join(" · ");
  }
  function paintDataDate() {
    const el = document.getElementById('dataDate');
    if (el) el.textContent = dataDateBase + refreshSuffix();
  }
  function startRefreshPoller() {
    loadRefreshInfo();
    setInterval(loadRefreshInfo, 60000);
  }

  async function api(path) {
    const r = await fetch(path);
    return r.json().catch(() => ({}));
  }

  async function init() {
    CATS = await api("/api/categories");
    curCat = CATS.default_category;
    curProd = CATS.default_product;
    renderCatNav();
    selectCategory(curCat);
    $("#btnRefresh").onclick = onRefresh;
    $("#btnWind").onclick = () => alert("Wind Alice 三技能（商品日报 / 研报观点 / 量化评分）需在 WorkBuddy 会话内运行并落盘。\n当前 wind-finance 连接器未连接。\n连接后于对话中触发对应技能，结果将填充至「观点与情绪中心」。");
    startRefreshPoller();
  }

  function renderCatNav() {
    const nav = $("#catnav");
    nav.innerHTML = "";
    CATS.categories.forEach(c => {
      const b = document.createElement("button");
      b.textContent = c.name;
      b.className = c.name === curCat ? "active" : "";
      b.onclick = () => selectCategory(c.name);
      nav.appendChild(b);
    });
  }

  function selectCategory(name) {
    curCat = name;
    renderCatNav();
    const cat = CATS.categories.find(c => c.name === name);
    const sel = $("#productSelect");
    sel.innerHTML = "";
    cat.products.forEach(p => {
      const o = document.createElement("option");
      o.value = p.code; o.textContent = `${p.name} (${p.code})`;
      if (p.code === curProd) o.selected = true;
      sel.appendChild(o);
    });
    sel.onchange = () => { curProd = sel.value; loadProduct(); };
    // 若当前产品不在该板块，选第一个
    if (!cat.products.find(p => p.code === curProd)) curProd = cat.products[0].code;
    loadProduct();
  }

  async function loadProduct() {
    const dash = $("#dashboard");
    dash.innerHTML = '<div class="loading">加载中…</div>';
    const snap = await api(`/api/snapshot/${encodeURIComponent(curCat)}/${encodeURIComponent(curProd)}`);
    if (snap.error === "no_snapshot") {
      showEmpty();
      return;
    }
    dataDateBase = "数据日期 " + (snap.as_of || "—");
    paintDataDate();
    const cat = CATS.categories.find(c => c.name === curCat);
    const prod = cat.products.find(p => p.code === curProd);
    $("#productNote").textContent = prod ? prod.note || "" : "";
    renderDashboard(snap);
  }

  function showEmpty() {
    $("#dashboard").innerHTML =
      '<div class="empty"><div style="font-size:16px;margin-bottom:8px">该品种暂无快照</div>' +
      '<div class="note">采集需在 WorkBuddy 会话内运行对应采集器（NeoData / 缠论 / 知识库）。\n当前所有品种均含真实 K 线（新浪主连）与缠论结构；前20/仓单/基差/利润等模块待接入对应数据源后自动补全。</div></div>';
  }

  async function onRefresh() {
    const btn = $("#btnRefresh"); btn.disabled = true; btn.textContent = "计算中…";
    await api(`/api/recompute/${encodeURIComponent(curCat)}/${encodeURIComponent(curProd)}`);
    btn.disabled = false; btn.textContent = "重新计算 / 刷新";
    loadProduct();
  }

  // -------------------- 渲染仪表盘 --------------------
  async function renderDashboard(s) {
    const d = $("#dashboard");
    d.innerHTML = '<div class="loading">加载中…</div>';
    let sent = {};
    try { sent = await api("/api/sentiment_summary"); } catch (e) { sent = {}; }
    SENT = sent;
    d.innerHTML = [
      cardOverview(s), cardSupplyDemand(s), cardInventory(s), cardBasis(s), cardProfit(s),
      cardCapital(s), cardTop20(s), cardChanlun(s),
      cardSentimentSummary(sent), cardOpinion(s),
    ].join("");
    initCharts(s, sent);
    if (scrollToOpinion) {
      scrollToOpinion = false;
      const el = document.getElementById("cardOpinion");
      if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  // ⑫ 全市场机构情绪总览（含板块下拉筛选 + 可点选跳转品种）
  function cardSentimentSummary(sent) {
    SENT = sent || {};
    const t = SENT.totals || { bullish: 0, bearish: 0, neutral: 0, opinions: 0 };
    const sum = t.bullish + t.bearish + t.neutral;
    const pctBull = sum ? Math.round(t.bullish / sum * 100) : 0;
    const pctBear = sum ? Math.round(t.bearish / sum * 100) : 0;
    const tone = t.bullish > t.bearish ? "偏多" : t.bearish > t.bullish ? "偏空" : "中性";
    // 板块下拉选项（按 CATS 顺序，仅保留有数据的板块）
    const sectorOpts = (CATS && CATS.categories ? CATS.categories.map(c => c.name) : [])
      .filter(n => SENT.by_category && SENT.by_category[n])
      .map(n => `<option value="${n}">${n}</option>`).join("");
    const body = `<div class="flex" style="align-items:flex-start">
      <div style="flex:0 0 280px"><div id="chSentPie" class="chart" style="height:240px"></div>
        <div class="note" style="text-align:center" id="sentPieCap">全市场机构情绪：<b class="${tone === "偏多" ? "up" : tone === "偏空" ? "down" : ""}">${tone}</b>（看多 ${pctBull}% / 看空 ${pctBear}%）</div></div>
      <div style="flex:1.4">
        <div class="sent-toolbar">
          <label class="sent-filter-label">板块</label>
          <select id="sentSector" class="sent-select">
            <option value="__all__">全部板块</option>${sectorOpts}
          </select>
          <span class="hint">点击柱条或榜单条目可跳转品种详情</span>
        </div>
        <div id="chSentNet" class="chart" style="height:420px"></div>
      </div>
      <div id="sentLists" style="flex:0 0 220px"></div>
      </div>
      <div class="sent-curve-block" style="width:100%;margin-top:14px;border-top:1px solid var(--line);padding-top:12px">
        <div class="sent-toolbar">
          <label class="sent-filter-label">板块</label>
          <select id="sentCurveSector" class="sent-select"></select>
          <label class="sent-filter-label">品种</label>
          <select id="sentCurveProduct" class="sent-select"></select>
          <span class="hint">近30天 · 7日滚动净情绪比例（(看多−看空)/(看多+看空+中性)），圆点=当日有观点</span>
        </div>
        <div id="sentCurveCap" class="note" style="margin:4px 0 2px"></div>
        <div id="chSentCurve" class="chart" style="height:240px"></div>
      </div>
      <div class="note" style="width:100%">样本：全市场 ${SENT.total_products || 0} 个品种、共 ${t.opinions} 条机构研报观点（多源聚合：奇货可查 qhkch 每日直抓 + WebSearch 全网检索 + 金十期货 + 东方财富期货资讯 + 文华财经 + Mysteel 等产业源；按<b>期货公司机构名跨源去重</b>，剔除无机构名/模糊日期的存量以避免重复与污染，近30天）；数据截至 ${SENT.as_of || "—"}。净情绪比例 = (看多 − 看空) / (看多 + 看空 + 中性)。</div>`;
    return card("⑪ 全市场机构情绪总览", "Sentiment", "span12", body, tone);
  }

  // 跳转到指定品种详情（切换板块 + 选中品种 + 重载）
  function gotoProduct(code) {
    if (!CATS) return;
    const cat = CATS.categories.find(c => c.products.some(p => p.code === code));
    if (!cat) return;
    curCat = cat.name;
    renderCatNav();
    const sel = $("#productSelect");
    sel.innerHTML = "";
    cat.products.forEach(p => {
      const o = document.createElement("option");
      o.value = p.code; o.textContent = `${p.name} (${p.code})`;
      if (p.code === code) o.selected = true;
      sel.appendChild(o);
    });
    sel.onchange = () => { curProd = sel.value; loadProduct(); };
    curProd = code;
    scrollToOpinion = true;   // 跳转后直接定位到该品种的机构观点（含日期与时效）
    loadProduct();
  }

  // 按板块过滤排行
  function filterRanking(catFilter) {
    if (!SENT || !SENT.ranking) return [];
    return (catFilter && catFilter !== "__all__")
      ? SENT.ranking.filter(r => r.category === catFilter)
      : SENT.ranking;
  }

  // 净情绪条形榜（随下拉过滤 + 可点选）
  function renderSentNet(catFilter) {
    const el = document.getElementById("chSentNet");
    if (!el) return;
    const old = echarts.getInstanceByDom(el);
    if (old) old.dispose();
    Charts.sentimentNet(el, filterRanking(catFilter), (r) => gotoProduct(r.code));
  }

  // 偏多 / 偏空榜单（随下拉过滤 + 可点选）
  function renderSentLists(catFilter) {
    const box = document.getElementById("sentLists");
    if (!box) return;
    const rl = filterRanking(catFilter);
    const bull = rl.filter(r => r.net > 0).sort((a, b) => b.net - a.net).slice(0, 12);
    const bear = rl.filter(r => r.net < 0).sort((a, b) => a.net - b.net).slice(0, 12);
    const li = (r) => `<li class="pick" data-code="${r.code}"><span class="pill ${r.net >= 0 ? "up" : "down"}">${(r.net > 0 ? "+" : "") + r.net}</span><div class="t">${r.name}(${r.code})</div><div class="m">看多${r.bullish}/看空${r.bearish}/中性${r.neutral} · ${r.category}</div></li>`;
    box.innerHTML =
      `<b class="muted" style="font-size:12px">偏多品种榜</b><ul class="list" style="margin-bottom:10px">${bull.map(li).join("") || '<li class="muted">无</li>'}</ul>` +
      `<b class="muted" style="font-size:12px">偏空品种榜</b><ul class="list">${bear.map(li).join("") || '<li class="muted">无</li>'}</ul>`;
    box.querySelectorAll(".pick").forEach(li => { li.onclick = () => gotoProduct(li.dataset.code); });
  }

  // 饼图随板块过滤更新（全市场 / 单板块）
  function renderSentPie(catFilter) {
    const el = document.getElementById("chSentPie");
    if (!el || !SENT) return;
    const rl = filterRanking(catFilter);
    const tot = rl.reduce((a, r) => ({ bullish: a.bullish + r.bullish, bearish: a.bearish + r.bearish, neutral: a.neutral + r.neutral }),
      { bullish: 0, bearish: 0, neutral: 0 });
    const sum = tot.bullish + tot.bearish + tot.neutral;
    const tone = tot.bullish > tot.bearish ? "偏多" : tot.bearish > tot.bullish ? "偏空" : "中性";
    const pctBull = sum ? Math.round(tot.bullish / sum * 100) : 0;
    const pctBear = sum ? Math.round(tot.bearish / sum * 100) : 0;
    const old = echarts.getInstanceByDom(el);
    if (old) old.dispose();
    Charts.sentimentPie(el, tot);
    const cap = document.getElementById("sentPieCap");
    if (cap) {
      const label = (catFilter && catFilter !== "__all__") ? (catFilter + " 机构情绪") : "全市场机构情绪";
      cap.innerHTML = `${label}：<b class="${tone === "偏多" ? "up" : tone === "偏空" ? "down" : ""}">${tone}</b>（看多 ${pctBull}% / 看空 ${pctBear}%）`;
    }
  }

  // 计算单品种近 N 天净情绪比例曲线（win 日滚动：(看多−看空)/(看多+看空+中性)）
  // anchor：窗口锚点（默认取快照 as_of 日期，使「近一个月」始终相对数据日期成立）
  function computeSentCurve(opinions, days, win, anchor) {
    const byDate = {};
    opinions.forEach(o => {
      const d = parseDate(o.date);
      if (!d) return;
      const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
      const b = byDate[key] || (byDate[key] = { bull: 0, bear: 0, neutral: 0 });
      if (o.stance === "bullish") b.bull++;
      else if (o.stance === "bearish") b.bear++;
      else b.neutral++;
    });
    const out = [];
    const today = parseDate(anchor) || new Date(); today.setHours(0, 0, 0, 0);
    for (let i = days - 1; i >= 0; i--) {
      const dt = new Date(today); dt.setDate(dt.getDate() - i);
      let nbull = 0, nbear = 0, nneutral = 0, hasOp = false;
      for (let j = 0; j < win; j++) {
        const dd = new Date(dt); dd.setDate(dd.getDate() - j);
        const dk = `${dd.getFullYear()}-${String(dd.getMonth() + 1).padStart(2, "0")}-${String(dd.getDate()).padStart(2, "0")}`;
        const b = byDate[dk];
        if (b) { nbull += b.bull; nbear += b.bear; nneutral += b.neutral; hasOp = true; }
      }
      // 中性纳入比例：net = (看多−看空)/(看多+看空+中性)，与情绪榜/背离序列口径一致
      const tot = nbull + nbear + nneutral;
      const net = tot ? (nbull - nbear) / tot : 0;
      out.push({ label: `${dt.getMonth() + 1}/${dt.getDate()}`, net, hasOp, bull: nbull, bear: nbear, neutral: nneutral });
    }
    return out;
  }

  // 各品种近一月净多空情绪曲线：板块 + 品种下拉 → 拉取该品种快照的按日期观点 → 绘制
  let curveChart = null;
  function buildCurveSelects() {
    const sectorSel = document.getElementById("sentCurveSector");
    const prodSel = document.getElementById("sentCurveProduct");
    if (!sectorSel || !prodSel) return;
    const seen = {}; const cats = [];
    (SENT.ranking || []).forEach(r => { if (!seen[r.category]) { seen[r.category] = 1; cats.push(r.category); } });
    sectorSel.innerHTML = cats.map(c => `<option value="${c}">${c}</option>`).join("");
    const defCat = (curCat && cats.includes(curCat)) ? curCat : cats[0];
    sectorSel.value = defCat;
    const fillProds = (cat) => {
      const ps = (SENT.ranking || []).filter(r => r.category === cat).sort((a, b) => a.code.localeCompare(b.code));
      prodSel.innerHTML = ps.map(r => `<option value="${r.code}">${r.name}(${r.code})</option>`).join("");
      prodSel.value = (curProd && ps.some(p => p.code === curProd)) ? curProd : (ps[0] ? ps[0].code : "");
    };
    fillProds(defCat);
    sectorSel.onchange = () => { fillProds(sectorSel.value); renderSentCurve(); };
    prodSel.onchange = () => renderSentCurve();
  }
  async function renderSentCurve() {
    const prodSel = document.getElementById("sentCurveProduct");
    const box = document.getElementById("chSentCurve");
    const cap = document.getElementById("sentCurveCap");
    if (!prodSel || !box) return;
    const code = prodSel.value;
    const r = (SENT.ranking || []).find(x => x.code === code);
    const cat = r ? r.category : curCat;
    let snap = {};
    try { snap = await api(`/api/snapshot/${encodeURIComponent(cat)}/${encodeURIComponent(code)}`); } catch (e) { snap = {}; }
    const ops = (snap.opinion && snap.opinion.public_opinions) || [];
    // 锚点取「不晚于今天的最新观点日期」与 as_of 的较大者：观点刷新后曲线能立即延伸到最新，
    // 同时剔除 11-15 这类"未来月报占位"日期，避免曲线尾部出现空白跳跃。
    let anchor = snap.as_of;
    const today = new Date(); today.setHours(0, 0, 0, 0);
    ops.forEach(o => {
      const d = parseDate(o.date); const a = parseDate(anchor);
      if (d && a && d > a && d <= today) anchor = o.date;
    });
    const series = computeSentCurve(ops, 30, 7, anchor);
    if (curveChart) curveChart.dispose();
    curveChart = Charts.sentimentCurve(box, series);
    const last = series[series.length - 1] || { net: 0, hasOp: false };
    const tone = last.net > 0 ? "偏多" : last.net < 0 ? "偏空" : "中性";
    if (cap) cap.innerHTML = `${r ? r.name + "(" + code + ")" : code} · 近30天观点 <b>${ops.length}</b> 条 · 近7日净情绪比例 <b class="${last.net > 0 ? "up" : last.net < 0 ? "down" : ""}">${(last.net > 0 ? "+" : "") + (last.net * 100).toFixed(0) + "%"}</b>（${tone}）`;
  }

  // 绑定情绪总览卡片的交互（下拉筛选 + 点选跳转）
  function bindSentimentControls() {
    const sel = document.getElementById("sentSector");
    if (!sel) return;
    sel.value = "__all__";
    sel.onchange = () => {
      const v = sel.value;
      renderSentNet(v);
      renderSentLists(v);
      renderSentPie(v);
    };
    renderSentNet("__all__");
    renderSentLists("__all__");
    renderSentPie("__all__");
    buildCurveSelects();
    renderSentCurve();
  }

  function tag(label) {
    if (!label) return "";
    const m = { "偏多": "bull", "偏空": "bear", "中性": "neutral" };
    const k = m[label] || "neutral";
    return `<span class="tag ${k}">${label}</span>`;
  }
  function card(title, badge, cls2, body, tagLabel, id) {
    return `<section class="card ${cls2 || ""}"${id ? ` id="${id}"` : ""}><h3>${title}${badge ? `<span class="badge">${badge}</span>` : ""}${tagLabel ? tag(tagLabel) : ""}</h3>${body}</section>`;
  }

  // ① 概览
  function cardOverview(s) {
    const o = s.overview || {};
    const k = (label, val, c) => `<div class="kpi"><div class="k">${label}</div><div class="v ${c || ""}">${val}</div></div>`;
    const chg = o.chg_pct;
    const body = `<div class="kpis">
      ${k("主力合约", o.main_contract || "—")}
      ${k("最新价", fmt(o.last, 1), "")}
      ${k("涨跌幅", pct(chg), cls(chg))}
      ${k("结算价", fmt(o.settle, 1))}
      ${k("持仓量", fmt(o.oi) + " 手", cls(o.oi_chg))}
      ${k("持仓增减", fmt(o.oi_chg) + " 手", cls(o.oi_chg))}
      ${k("成交量", fmt(o.volume) + " 手", cls(o.volume_chg))}
      ${k("成交变化", (o.volume_chg != null ? (o.volume_chg > 0 ? "+" : "") + fmt(o.volume_chg) : "—") + " 手", cls(o.volume_chg))}
      ${k("近远月价差", fmt(o.near_far_spread, 1) + " " + (o.near_far_label || ""), cls(o.near_far_spread))}
    </div>`;
    return card("① 概览", "Overview", "span12", body);
  }

  // ② 供需关系
  function cardSupplyDemand(s) {
    const sd = s.supply_demand || {};
    const inv = sd.inventory || {};
    const prod = sd.production || {};
    const cons = sd.apparent_consumption || {};
    const term = sd.demand_terminal || {};
    const sentiment = sd.balance && sd.balance.indexOf("累库") >= 0 ? "偏空"
      : sd.balance && sd.balance.indexOf("去库") >= 0 ? "偏多" : "中性";
    const st = inv.standard || {}, soc = inv.social || {}, mill = inv.mill || {};
    const chgStr = st.change != null
      ? (st.change > 0 ? "+" : "") + fmt(st.change) + " " + (st.unit || "")
      : "—";
    const socStr = soc.total != null
      ? fmt(soc.total) + " " + (soc.unit || "") + "（" + (soc.change > 0 ? "+" : "") + fmt(soc.change) + "）"
      : "待接入";
    const millStr = mill.total != null
      ? fmt(mill.total) + " " + (mill.unit || "") + "（" + (mill.change > 0 ? "+" : "") + fmt(mill.change) + "）"
      : "无厂库";
    const body = `<div class="flex">
      <div style="flex:1">
        <div class="kv big"><span class="k">标准仓单(总数)</span><span class="v">${fmt(st.total)} ${st.unit || ""}</span></div>
        <div class="kv big"><span class="k">仓单较上期</span><span class="v ${cls(st.change)}">${chgStr}</span></div>
        <div class="kv"><span class="k">社会库存(总)</span><span class="v ${soc.change != null ? cls(soc.change) : ""}">${socStr}</span></div>
        <div class="kv"><span class="k">厂库(总)</span><span class="v ${mill.change != null ? cls(mill.change) : ""}">${millStr}</span></div>
      </div>
      <div style="flex:1">
        <div class="kv"><span class="k">周度产量</span><span class="v muted">${prod.value != null ? fmt(prod.value) + " " + (prod.unit || "") : "待接入"}</span></div>
        <div class="kv"><span class="k">表观消费量</span><span class="v muted">${cons.value != null ? fmt(cons.value) + " " + (cons.unit || "") : "待接入"}</span></div>
        <div class="note">${prod.note || ""}</div>
      </div>
      <div style="flex:1.4">
        <div class="sub">终端需求</div>
        <div class="kv"><span class="k">地产</span><span class="v">${term.real_estate || "—"}</span></div>
        <div class="kv"><span class="k">基建</span><span class="v">${term.infra || "—"}</span></div>
        <div class="kv"><span class="k">制造业</span><span class="v">${term.manufacturing || "—"}</span></div>
      </div></div>
      <div class="note" style="width:100%">${sd.note || ""}</div>`;
    return card("② 供需关系", "Supply-Demand", "span12", body, sentiment);
  }

  // ③ 库存供需
  function cardInventory(s) {
    const inv = s.inventory || {};
    const st = inv.standard || {}, soc = inv.social || {}, mill = inv.mill || {};
    const chg = (v) => v == null ? "—" : (v > 0 ? "+" : "") + fmt(v) + " " + (st.unit || soc.unit || mill.unit || "");
    const body = `<div class="flex">
      <div><div id="chInv" class="chart"></div></div>
      <div style="min-width:240px">
        <div class="kv big"><span class="k">标准仓单</span><span class="v">${fmt(st.total)} ${st.unit || ""}</span></div>
        <div class="kv big"><span class="k">较上期</span><span class="v ${st.change != null ? cls(st.change) : ""}">${chg(st.change)}</span></div>
        <div class="kv big"><span class="k">社会库存(总)</span><span class="v ${soc.change != null ? cls(soc.change) : ""}">${soc.total != null ? fmt(soc.total) + " " + (soc.unit || "") : "待接入"}</span></div>
        ${soc.change != null ? `<div class="kv"><span class="k">社库较上期</span><span class="v ${cls(soc.change)}">${chg(soc.change)}</span></div>` : ""}
        <div class="kv big"><span class="k">厂库(总)</span><span class="v ${mill.change != null ? cls(mill.change) : ""}">${mill.total != null ? fmt(mill.total) + " " + (mill.unit || "") : "无厂库/待接入"}</span></div>
        ${mill.change != null ? `<div class="kv"><span class="k">厂库较上期</span><span class="v ${cls(mill.change)}">${chg(mill.change)}</span></div>` : ""}
        <div class="kv"><span class="k">数据日期</span><span class="v">${st.date || "—"}</span></div>
      </div></div>
      <div class="note" style="width:100%">${inv.note || ""}</div>`;
    return card("③ 库存供需", "Inventory", "span7", body);
  }

  // ③ 期限基差 / 结构
  function cardBasis(s) {
    const b = s.basis || {};
    const body = `<div class="flex">
      <div><div id="chBasis" class="chart"></div></div>
      <div style="min-width:230px">
        <div class="kv"><span class="k">结构</span><span class="v">${b.structure_label || "—"}</span></div>
        <div class="kv"><span class="k">主力 ${b.main_contract}</span><span class="v">${fmt(b.main_close)}</span></div>
        <div class="kv"><span class="k">次主力 ${b.sub_contract}</span><span class="v">${fmt(b.sub_close)}</span></div>
        <div class="kv"><span class="k">价差(主-次)</span><span class="v ${cls(b.spread)}">${fmt(b.spread, 1)}</span></div>
        <div class="kv"><span class="k">现货基差</span><span class="v">${b.spot != null ? fmt(b.spot) : "待接入"}</span></div>
      </div></div>
      <div class="note" style="width:100%">${b.note || ""}</div>`;
    return card("④ 期限基差 / 结构", "Basis", "span5", body, b.structure === "back" ? "偏多" : b.structure === "contango" ? "偏空" : null);
  }

  // ④ 产业利润
  function cardProfit(s) {
    const p = s.profit || {};
    const body = `<div class="kv"><span class="k">状态</span><span class="v amber">${p.status || "待接入"}</span></div>
      <div class="kv"><span class="k">盘面利润</span><span class="v">${p.value != null ? fmt(p.value) : "—"}</span></div>
      <div class="kv"><span class="k">分位</span><span class="v">${p.pct_rank != null ? p.pct_rank + "%" : "—"}</span></div>
      <div class="note" style="margin-top:8px"><b>模型公式</b>：${p.formula || ""}</div>
      <div class="note">${p.note || ""}</div>`;
    return card("⑤ 产业利润", "Profit", "span4", body);
  }

  // ⑤ 资金面总览
  function cardCapital(s) {
    const c = s.capital || {}, k = s.kline || {};
    const body = `<div id="chCap" class="chart"></div>
      <div class="kv"><span class="k">成交量(今日)</span><span class="v">${fmt(c.volume)} 手</span></div>
      <div class="kv"><span class="k">成交量变化</span><span class="v ${cls(c.volume_chg)}">${c.volume_chg != null ? (c.volume_chg > 0 ? "+" : "") + fmt(c.volume_chg) : "—"} 手</span></div>
      <div class="kv"><span class="k">净持仓(前20)</span><span class="v ${cls(c.net_position)}">${fmt(c.net_position)} 手</span></div>
      <div class="kv"><span class="k">净持仓增减</span><span class="v ${cls(c.net_position_chg)}">${fmt(c.net_position_chg)} 手</span></div>
      <div class="kv"><span class="k">净多占比</span><span class="v">${fmt(c.net_long_pct, 2)}%</span></div>
      <div class="kv"><span class="k">多空比</span><span class="v">${fmt(c.long_short_ratio, 3)}</span></div>
      <div class="note" style="margin-top:6px">${c.note || ""}</div>`;
    return card("⑥ 资金面总览", "Capital", "span8", body, c.bias);
  }

  // ⑦ 资金动向（AKShare·持仓结构·主力追踪）—— 由 capital_flow 数据驱动；缺失时回退原前20会员
  function renderCapFlow(s, cf) {
    const tp = cf.total_position || {};
    const tiers = cf.tiers || [];
    const iv = cf.institutional_vs_retail || {};
    const lo = cf.large_orders || {};
    const inst = iv.institutional || {}, ret = iv.retail || {};
    const sign = (n) => (n > 0 ? "+" : "") + fmt(n);
    const kpi = (label, val, c, sub, hero) =>
      `<div class="netchg-card ${hero ? "hero" : ""}"><div class="k">${label}</div>
        <div class="v ${c || ""}">${val}</div><div class="s">${sub || ""}</div></div>`;

    const kpis = `<div class="netchg">
      ${kpi("总持仓(主连)", (tp.latest != null ? fmt(tp.latest) : "—") + " 手", "", "近20日 " + (tp.chg_20d != null ? sign(tp.chg_20d) : "—"), true)}
      ${kpi("总持仓变化(1日)", (tp.chg_1d != null ? sign(tp.chg_1d) : "—") + " 手", cls(tp.chg_1d), tp.chg_1d > 0 ? "资金流入 ↑" : "资金流出 ↓")}
      ${kpi("总持仓变化(5日)", (tp.chg_5d != null ? sign(tp.chg_5d) : "—") + " 手", cls(tp.chg_5d), tp.chg_5d > 0 ? "5日净流入" : "5日净流出")}
      ${kpi("机构净持仓(前" + (inst.count || 5) + ")", sign(inst.net) + " 手", cls(inst.net), "机构/主力席位", true)}
      ${kpi("机构净持仓变化", sign(inst.net_chg) + " 手", cls(inst.net_chg), inst.net_chg > 0 ? "主力净多增仓" : "主力净空增仓")}
      ${kpi("散户净持仓变化", sign(ret.net_chg) + " 手", cls(ret.net_chg), ret.net_chg > 0 ? "散户净多增仓" : "散户净空增仓")}
    </div>`;

    const tierRows = tiers.map(t => `<tr>
      <td>${t.tier}</td>
      <td class="${cls(t.net)}">${sign(t.net)}</td>
      <td class="${cls(t.net_chg)}">${sign(t.net_chg)}</td>
      <td>${fmt(t.long)}</td><td>${fmt(t.short)}</td><td>${t.count}</td>
    </tr>`).join("");

    const listItems = (arr, key, valCls) => (arr && arr.length
      ? arr.map(m => `<div class="kv"><span class="k">${m.name}</span><span class="v ${valCls(m)}">${(key === "net_chg" ? sign(m.net_chg) : fmt(m[key]))}</span></div>`).join("")
      : '<div class="note">—</div>');

    const body = `${kpis}
      <div class="verdict">${cf.summary || ""}</div>
      <div class="flex">
        <div style="flex:1.2">
          <div class="t20-hist-head"><span class="t">净持仓分布 · 按账户资金规模分层（净持仓 / 当日净变化）</span></div>
          <div id="chCapFlowTier" class="chart" style="height:230px"></div>
          <div class="table-wrap" style="margin-top:6px">
            <table><thead><tr><th>资金规模分层</th><th>净持仓</th><th>当日净变化</th><th>持买</th><th>持卖</th><th>席位</th></tr></thead>
            <tbody>${tierRows}</tbody></table>
          </div>
        </div>
        <div style="flex:1">
          <div class="t20-hist-head"><span class="t">总持仓变动 · 历史回溯（资金整体进出场节奏）</span></div>
          <div id="chCapFlowTotal" class="chart" style="height:230px"></div>
        </div>
      </div>
      <div class="flex" style="margin-top:10px">
        <div style="flex:1.3">
          <div class="t20-hist-head"><span class="t">大单动向 · 主力机构操作痕迹</span></div>
          <div class="note">净持仓变化 = 多头增仓 − 空头增仓；正值=净多增加（偏多），负值=净空增加（偏空）。</div>
          <div class="flex">
            <div style="flex:1"><div class="k">净多增仓主力</div>${listItems(lo.top_net_increase, "net_chg", m => cls(m.net_chg))}</div>
            <div style="flex:1"><div class="k">净空增仓主力</div>${listItems(lo.top_net_decrease, "net_chg", m => cls(m.net_chg))}</div>
            <div style="flex:1"><div class="k">成交量领先席位</div>${listItems(lo.volume_leaders, "volume", () => "")}</div>
          </div>
        </div>
        <div style="flex:1.2">
          <div class="t20-hist-head"><span class="t">历史回溯 · 各资金层级净持仓变化</span></div>
          <div id="chCapFlowHist" class="chart" style="height:220px"></div>
        </div>
      </div>
      <div class="flex" style="margin-top:10px">
        <div style="flex:1">
          <div class="t20-hist-head"><span class="t">前20会员近1月净持仓变化（合计净持仓 · 主力追踪）</span></div>
          <div id="chCapFlowT20Hist" class="chart" style="height:240px"></div>
          <div id="capFlowT20Verdict" class="verdict" style="margin-top:4px"></div>
        </div>
      </div>
      <div class="note" style="margin-top:8px">数据来源：${cf.source}。${cf.note_proxy || ""} 主力合约 ${cf.main_contract || cf.contract}；数据交易日 ${cf.as_of}。总持仓为主连口径，合约换月时或有跳变。</div>`;

    const tone = inst.net_chg > 0 ? "机构净多增仓" : (inst.net_chg < 0 ? "机构净空增仓" : "中性");
    return card("⑦ 资金动向", "AKShare·持仓结构·主力追踪", "span12", body, tone);
  }

  // ⑦ 前20会员多空对比（重点突出：净持仓 + 当日净持仓变化量）
  function cardTop20(s) {
    const cf = s.capital_flow;
    if (cf && cf.available) return renderCapFlow(s, cf);
    const t = s.top20 || {};
    // 数据交易日：优先取净持仓曲线最新采样日，回退快照 as_of / data_date
    const t20DateRaw = (t.history && t.history.length)
      ? t.history[t.history.length - 1].date
      : (s.as_of || s.data_date || null);
    const t20Date = fmtDate(t20DateRaw);
    if (!t.members || !t.members.length) return card("⑦ 前20会员多空对比", "Top20 · 交易日 " + t20Date, "span12", '<div class="note">暂无前20会员数据。</div>');
    const net = t.net != null ? t.net : 0;
    const longChg = t.long_chg != null ? t.long_chg : 0;
    const shortChg = t.short_chg != null ? t.short_chg : 0;
    const netChg = t.net_chg != null ? t.net_chg : (longChg - shortChg);
    const netLabel = net > 0 ? "净多持仓" : net < 0 ? "净空持仓" : "多空均衡";
    const side = netChg > 0 ? "多头增仓占优（净多增加）" : netChg < 0 ? "空头增仓占优（净空增加）" : "多空增减相当";
    const tone = netChg > 0 ? "偏多" : netChg < 0 ? "偏空" : "中性";
    const sign = (n) => (n > 0 ? "+" : "") + fmt(n);
    const kpi = (label, val, c, sub, hero) =>
      `<div class="netchg-card ${hero ? "hero" : ""}"><div class="k">${label}</div>
        <div class="v ${c || ""}">${val}</div><div class="s">${sub || ""}</div></div>`;
    const verdict =
      `前20会员合计<b>净持仓 ${sign(net)} 手</b>（${netLabel}）；当日<b class="${cls(netChg)}">净持仓变化 ${sign(netChg)} 手</b>` +
      ` —— 多头当日增仓 ${sign(longChg)} 手、空头当日增仓 ${sign(shortChg)} 手，` +
      `<b>${side}</b>，席位资金情绪<b class="${cls(netChg)}">${tone}</b>。`;
    const rows = t.members.slice(0, 12).map(m => {
      const mc = (m.long_chg || 0) - (m.short_chg || 0);
      return `<tr><td>${m.name}</td><td class="up">${fmt(m.long)}</td><td class="down">${fmt(m.short)}</td>
        <td class="${cls(m.net)}">${fmt(m.net)}</td><td class="${cls(mc)}">${sign(mc)}</td></tr>`;
    }).join("");
    const body = `<div class="netchg">
        ${kpi("前20净持仓（持买 − 持卖）", sign(net) + " 手", cls(net), netLabel + " · 净多占比 " + fmt(t.net_long_pct, 2) + "%", true)}
        ${kpi("当日净持仓变化", sign(netChg) + " 手", cls(netChg), netChg > 0 ? "净多增加 ↑" : netChg < 0 ? "净空增加 ↓" : "当日持平")}
        ${kpi("多头当日增仓", sign(longChg) + " 手", cls(longChg), "持买合计 " + fmt(t.long_total) + " 手")}
        ${kpi("空头当日增仓", sign(shortChg) + " 手", cls(shortChg), "持卖合计 " + fmt(t.short_total) + " 手")}
      </div>
      <div class="verdict">${verdict}</div>
      <div class="flex">
        <div style="flex:1.1"><div id="chFly" class="chart tall"></div></div>
        <div style="flex:1.1"><div id="chNetChg" class="chart tall"></div></div>
        <div class="table-wrap" style="flex:1.2">
          <table><thead><tr><th>会员</th><th>持买</th><th>持卖</th><th>净持仓</th><th>当日净变化</th></tr></thead>
          <tbody>${rows}</tbody></table>
        </div></div>
      <div class="kv"><span class="k">持买合计 / 持卖合计</span><span class="v">${fmt(t.long_total)} / ${fmt(t.short_total)} 手</span></div>
      <div class="kv"><span class="k">多空比 / 净多占比</span><span class="v">${fmt(t.long_short_ratio, 3)} / ${fmt(t.net_long_pct, 2)}%</span></div>
      <div class="kv"><span class="k">数据交易日</span><span class="v">${t20Date}</span></div>
      <div class="t20-hist">
        <div class="t20-hist-head"><span class="t">近1月 前20会员主力合约净持仓</span><span id="t20HistVerdict" class="verdict-inline"></span></div>
        <div id="chT20Hist" class="chart" style="height:200px"></div>
        <div class="note" style="margin-top:4px">口径：前20会员主力合约（持买合计最大合约）持买 − 持卖，按采样交易日绘制；与快照前20会员口径一致，可观察近一月净持仓走向与多空力量消长。</div>
      </div>
      <div class="t20-hist" style="margin-top:10px">
        <div class="t20-hist-head"><span class="t">主力合约净持仓 · z-score 归一化 · 机构观点净情绪叠加（背离分析）</span><span id="t20DivVerdict" class="verdict-inline"></span></div>
        <div id="chT20Div" class="chart" style="height:240px"></div>
        <div class="note" style="margin-top:4px">左轴：前20会员主力合约净持仓（手）；右轴：z-score 标准化。红线=COT 净持仓 z，橙虚线=机构观点净情绪比例 [（累计看多−看空)/(看多+看空+中性)] 的 z。两者反向即<b>背离</b>（阴影区），提示持仓与观点情绪不一致，需结合基本面研判。</div>
      </div>
      <div class="note" style="margin-top:6px">${t.note || ""}净持仓变化 = 多头当日增仓 − 空头当日增仓；为正代表净多增加（偏多），为负代表净空增加（偏空）。</div>`;
    return card("⑦ 前20会员多空对比", "Top20 · 核心 · 交易日 " + t20Date, "span12", body, tone);
  }

  // ⑦ 缠论结构
  function cardChanlun(s) {
    const ch = s.chanlun || {};
    const c = s.kline || {};
    let summary = "";
    if (ch.available) {
      summary = `<div class="kv"><span class="k">趋势</span><span class="v">${ch.trend || "—"}</span></div>
        <div class="kv"><span class="k">分型 / 笔 / 线段</span><span class="v">${ch.fractals} / ${ch.bi_count} / ${ch.segment_count}</span></div>
        <div class="kv"><span class="k">中枢数</span><span class="v">${ch.center_count}</span></div>
        <div class="kv"><span class="k">背驰</span><span class="v">${ch.divergences ? ch.divergences.length : 0}</span></div>
        <div class="kv"><span class="k">买卖点</span><span class="v">${ch.trade_points ? ch.trade_points.length : 0}</span></div>`;
    } else {
      summary = `<div class="note">${ch.note || "缠论引擎未挂载。"}</div>`;
    }
    const body = `<div class="zoombar" id="chKzoom">
        <span class="zl">时间缩放</span>
        <button data-days="0">全部</button>
        <button data-days="252">1年</button>
        <button data-days="126">半年</button>
        <button data-days="63">3月</button>
        <button data-days="21">1月</button>
      </div>
      <div id="chK" class="chart tall"></div>
      <div class="flex" style="margin-top:8px">${summary}
        <div class="note" style="flex:2">${ch.note || ""}<br/>数据样本 ${c.rows ? c.rows.length : 0} 个交易日（${s.kline && s.kline.contract || "主连"}，跨月连续）。已识别笔/线段/中枢，可结合缩放查看多年结构，请综合其它模块研判。</div>
      </div>`;
    return card("⑧ 缠论结构", "Chanlun", "span12", body);
  }

  function cardOpinion(s) {
    const ms = s.market_sentiment || {};
    const op = s.opinion || {};
    const qv = op.qualitative_view || {};
    OPINIONS = op.public_opinions || [];
    const cot = ms.cot || {}, st = ms.structure || {}, rc = ms.receipt || {};
    const ss = op.stance_summary || { bullish: 0, bearish: 0, neutral: 0 };
    const fresh7 = opWithin(7).length, fresh15 = opWithin(15).length;
    const body = `<div class="flex" style="align-items:flex-start">
      <div style="flex:0 0 200px"><div id="chGauge" class="chart" style="height:220px"></div>
        <div class="note" style="text-align:center">综合情绪：${ms.label || "—"}（分 ${ms.score != null ? ms.score : "—"}）</div></div>
      <div style="flex:2">
        <div class="kv"><span class="k">COT 持仓情绪</span><span class="v">${cot.signal || "—"}</span></div>
        <div class="kv"><span class="k">　净多占比 / 多空比</span><span class="v">${fmt(cot.net_long_pct, 2)}% / ${fmt(cot.long_short_ratio, 3)}</span></div>
        <div class="kv"><span class="k">　席位增减</span><span class="v">${cot.seat_bias || "—"}</span></div>
        <div class="kv"><span class="k">期限结构情绪</span><span class="v">${st.signal || "—"}（价差 ${fmt(st.spread, 1)}）</span></div>
        <div class="kv"><span class="k">仓单情绪</span><span class="v">${rc.signal || "—"}</span></div>
        <div class="kv"><span class="k">期权 / 舆情 / 宏观</span><span class="v muted">增强项 · 待接入</span></div>
      </div></div>
      <div style="width:100%;margin-top:10px">
        <div class="note" style="margin-bottom:6px"><b>定性机构观点</b>（${qv.source || ""} · ${qv.date || ""}）：${qv.summary || "—"}</div>
        <div class="note" style="margin-bottom:6px"><b>Wind Alice 三技能</b>：${op.wind_alice && op.wind_alice.reason ? op.wind_alice.reason : "待接入"}</div>
        <div style="margin:6px 0">
          <b class="muted" style="font-size:12px">机构研报观点流</b>
          <span class="pill up">看多 ${ss.bullish}</span><span class="pill down">看空 ${ss.bearish}</span><span class="pill flat">中性 ${ss.neutral}</span>
          <span class="fresh f0">近7天 ${fresh7} 条</span><span class="fresh f1">近15天 ${fresh15} 条</span>
        </div>
        <div class="op-toolbar">
          <label class="sent-filter-label">时效筛选</label>
          <select id="opFresh" class="sent-select">
            <option value="7">近 7 天（最新）</option>
            <option value="15">近 15 天</option>
            <option value="30">近 30 天</option>
            <option value="0">全部观点</option>
          </select>
          <span class="hint" id="opCount"></span>
        </div>
        <div id="opList"></div>
      </div>`;
    return card("⑫ 机构观点详情", "Opinion", "span8", body, ms.label, "cardOpinion");
  }

  // ---- 机构观点：日期解析 / 时效 ----
  function parseDate(s) {
    if (!s) return null;
    s = String(s).trim();
    let m = s.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (m) return new Date(+m[1], +m[2] - 1, +m[3]);
    m = s.match(/^(\d{4})(\d{2})(\d{2})$/);
    if (m) return new Date(+m[1], +m[2] - 1, +m[3]);
    m = s.match(/^(\d{4})-(\d{2})$/);
    if (m) return new Date(+m[1], +m[2] - 1, 1);
    return null; // 如 "近期"
  }
  function daysAgo(s) {
    const d = parseDate(s);
    if (!d) return null;
    const now = new Date(); now.setHours(0, 0, 0, 0);
    return Math.round((now - d) / 86400000);
  }
  function freshBadge(s) {
    const n = daysAgo(s);
    if (n == null) return '<span class="fresh f0">最新</span>'; // 未标注具体日期（如"近期"）视为最新
    const txt = n <= 0 ? "今日" : n + "天前";
    const k = n <= 3 ? "f0" : n <= 7 ? "f1" : n <= 30 ? "f2" : "f3";
    return `<span class="fresh ${k}">${txt}</span>`;
  }
  // 按天数窗口筛选（无具体日期的"近期"视为新鲜，始终保留）
  function opWithin(days) {
    if (!days) return OPINIONS.slice();
    return OPINIONS.filter(p => {
      const n = daysAgo(p.date);
      return n == null ? true : (n >= 0 && n <= days);
    });
  }
  // 默认选最严且有内容的时效窗口（保证展示最新观点）
  function pickFreshWindow() {
    for (const w of [7, 15, 30, 0]) { if (opWithin(w).length > 0) return w; }
    return 0;
  }
  // 渲染观点列表（按日期从新到旧，默认收起前 8 条）
  function renderOpinions() {
    const box = document.getElementById("opList");
    if (!box) return;
    const list = opWithin(OP_FILTER).slice().sort((a, b) => {
      const da = parseDate(a.date), db = parseDate(b.date);
      const ka = da ? da.getTime() : Infinity; // "近期"置顶
      const kb = db ? db.getTime() : Infinity;
      return kb - ka;
    });
    const cnt = document.getElementById("opCount");
    if (cnt) cnt.textContent = `共 ${OPINIONS.length} 条 · 当前显示 ${list.length} 条（最新在前）`;
    const N = 8;
    const shown = OP_EXPANDED ? list : list.slice(0, N);
    const STANCE = { bullish: ["看多", "up"], bearish: ["看空", "down"], neutral: ["中性", "flat"] };
    const item = (p) => {
      const sc = STANCE[p.stance] || ["—", "flat"];
      const inst = p.institution || p.source || "";
      return `<li class="op-item">
        <div class="op-head"><span class="pill ${sc[1]}">${sc[0]}</span><span class="t">${p.title || "（无标题）"}</span></div>
        <div class="op-date"><b>${inst || "机构"}</b> · ${p.date || "—"} ${freshBadge(p.date)}</div>
        ${p.summary ? `<div class="muted" style="font-size:12px;margin-top:4px;line-height:1.5">${p.summary}</div>` : ""}
        ${p.url ? `<div style="margin-top:4px"><a href="${p.url}" target="_blank" rel="noopener" class="op-link">查看原文 ↗</a></div>` : ""}
      </li>`;
    };
    box.innerHTML = list.length
      ? `<ul class="list">${shown.map(item).join("")}</ul>`
        + (list.length > N ? `<button class="op-toggle" id="opToggle">${OP_EXPANDED ? "收起" : "展开全部 " + list.length + " 条机构观点"}</button>` : "")
      : '<div class="note">该时效窗口内暂无机构观点，可切换到「全部观点」查看。</div>';
    const tg = document.getElementById("opToggle");
    if (tg) tg.onclick = () => { OP_EXPANDED = !OP_EXPANDED; renderOpinions(); };
  }
  // 绑定观点卡片的时效筛选
  function bindOpinionControls() {
    const sel = document.getElementById("opFresh");
    if (!sel) return;
    const w = pickFreshWindow();
    OP_FILTER = w; OP_EXPANDED = false;
    sel.value = String(w);
    sel.onchange = () => { OP_FILTER = parseInt(sel.value, 10) || 0; OP_EXPANDED = false; renderOpinions(); };
    renderOpinions();
  }

  // -------------------- 图表初始化 --------------------
  function initCharts(s, sent) {
    const charts = [];
    const reg = (id, fn) => { const el = document.getElementById(id); if (el) charts.push(fn(echarts.init(el))); };
    if (s.kline && s.kline.rows) {
      const kc = Charts.kline($("#chK"), s.kline.rows, s.kline.ma5, s.kline.ma10, s.chanlun);
      charts.push(kc);
      Charts.capital($("#chCap"), s.kline.rows);
      // 缠论 K 线时间缩放预设（全部 / 1年 / 半年 / 3月 / 1月）
      const N = s.kline.rows.length;
      const zb = document.getElementById("chKzoom");
      if (zb) zb.querySelectorAll("button").forEach(b => {
        b.onclick = () => {
          const days = parseInt(b.dataset.days, 10);
          const start = days > 0 ? Math.max(0, 100 - days / N * 100) : 0;
          kc.dispatchAction({ type: "dataZoom", start: start, end: 100 });
          zb.querySelectorAll("button").forEach(x => x.classList.remove("active"));
          b.classList.add("active");
        };
      });
    }
    if (s.inventory && s.inventory.standard && s.inventory.standard.total != null) Charts.inventory($("#chInv"), s.inventory);
    if (s.basis && s.basis.main_close != null && s.basis.sub_close != null) Charts.basisSpread($("#chBasis"), s.basis);
    if (s.capital_flow && s.capital_flow.available) {
      // ⑦ 资金动向（AKShare）：分层净持仓组合图 / 总持仓历史 / 各层级净持仓历史
      if (s.capital_flow.tiers && s.capital_flow.tiers.length)
        Charts.capitalFlowTier($("#chCapFlowTier"), s.capital_flow.tiers);
      if (s.capital_flow.total_position && s.capital_flow.total_position.series)
        Charts.totalPositionCurve($("#chCapFlowTotal"), s.capital_flow.total_position.series);
      if (s.capital_flow.tier_net_history && s.capital_flow.tier_net_history.length >= 2)
        Charts.tierNetHistory($("#chCapFlowHist"), s.capital_flow.tier_net_history);
      // 前20会员近1月净持仓变化（复用 top20.history 累计曲线）
      if (s.top20 && s.top20.history && s.top20.history.length >= 2) {
        const h = s.top20.history.slice().sort((a, b) => (a.date < b.date ? -1 : 1));
        Charts.top20NetCurve($("#chCapFlowT20Hist"), h);
        const chg = h[h.length - 1].net - h[0].net;
        const v = document.getElementById("capFlowT20Verdict");
        if (v) v.innerHTML = `期末前20会员合计净持仓 <b>${fmt(h[h.length - 1].net)}</b> 手，近1月变化 <b class="${cls(chg)}">${chg > 0 ? "+" : ""}${fmt(chg)}</b> 手`;
      }
    } else if (s.top20 && s.top20.members && s.top20.members.length) {
      // 回退：原前20会员图表
      Charts.butterfly($("#chFly"), s.top20.members);
      Charts.netChgBars($("#chNetChg"), s.top20.members);
    }
    if (!(s.capital_flow && s.capital_flow.available) && s.top20 && s.top20.history && s.top20.history.length >= 2) {
      const h = s.top20.history.slice().sort((a, b) => (a.date < b.date ? -1 : 1));
      Charts.top20NetCurve($("#chT20Hist"), h);
      const chg = h[h.length - 1].net - h[0].net;
      const el = document.getElementById("t20HistVerdict");
      if (el) el.innerHTML = `期末主力净持仓 <b>${fmt(h[h.length - 1].net)}</b> 手，近1月变化 <b class="${cls(chg)}">${chg > 0 ? "+" : ""}${fmt(chg)}</b> 手`;
    }
    // 背离分析：COT 净持仓 z-score × 机构观点净情绪 z-score
    if (!(s.capital_flow && s.capital_flow.available) && s.top20 && s.top20.history && s.top20.history.length >= 2 && s.top20.opinion_series) {
      const h2 = s.top20.history.slice().sort((a, b) => (a.date < b.date ? -1 : 1));
      Charts.t20Divergence($("#chT20Div"), h2, s.top20.opinion_series);
      const last = h2[h2.length - 1];
      const op = (s.top20.opinion_series.find(o => o.date === last.date) || {});
      const a = last.z, b = op.z;
      const v = document.getElementById("t20DivVerdict");
      if (v && a != null && b != null) {
        if (a !== 0 && b !== 0 && Math.sign(a) !== Math.sign(b))
          v.innerHTML = `<b style="color:#e0a83a">背离：持仓 ${a > 0 ? "偏多" : "偏空"} / 观点 ${b > 0 ? "偏多" : "偏空"}</b>`;
        else
          v.innerHTML = `持仓与观点方向<b class="${a >= 0 ? "up" : "down"}">${a >= 0 ? "共振偏多" : "共振偏空"}</b>`;
      }
    }
    if (s.market_sentiment) {
      const ms = s.market_sentiment, sc = ms.score != null ? ms.score : 50;
      const color = ms.label === "偏多" ? "#e8483f" : ms.label === "偏空" ? "#1fab6b" : "#3d7eff";
      Charts.gauge($("#chGauge"), sc, ms.label || "中性", color);
    }
    bindOpinionControls();
    if (sent && sent.totals) {
      bindSentimentControls();
    }
    ensureResizeBound();
  }

  // 全局 resize：扫描所有 .chart 容器，兼容情绪榜下拉切换时重建的图表
  function ensureResizeBound() {
    if (resizeBound) return;
    window.addEventListener("resize", () => {
      document.querySelectorAll(".chart").forEach(el => {
        const c = echarts.getInstanceByDom(el);
        if (c && c.resize) c.resize();
      });
    });
    resizeBound = true;
  }

  window.addEventListener("DOMContentLoaded", init);
})();
