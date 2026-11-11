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
  let scrollToCapital = false; // 从全市场背离清单点选品种后，定位到资金背离预警模块

  // ===== 席位口径（统一文案，勿在数据字段层面改动）=====
  // 交易所只公布期货公司会员席位，不披露终端客户属性；所谓「前5 / 其余席位」只是旧称。
  // 二分：前 5 席位 = 按 |净持仓| 排名前 5；其余席位 = 多单/空单/成交量三榜合并去重后第 6 名及以后。
  const PROXY_NOTE = "前5席位 = 按 |净持仓| 排名前 5 的会员席位；其余席位 = 多单 / 空单 / 成交量三榜合并去重后第 6 名及以后的全部席位。交易所仅公布期货公司席位、不披露终端客户属性，因此这是席位规模口径，不是真实的前5 / 其余席位账户分类。";
  const TIER_NAMES = { "超大户/机构": "前5席位", "大户": "第6–10名", "中户": "第11–15名", "散户/中小": "第16–20名" };
  const tierLabel = (t) => TIER_NAMES[t] || t;

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
  const MODE_LABEL = { data: "数据主跑", both: "全量刷新", opinion: "观点刷新", deploy: "仅部署", offline: "离线版" };
  const shortTime = (s) => (!s ? "—" : (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(s) ? s.slice(5, 16) : s));
  function refreshSuffix() {
    if (!REFRESH_INFO || REFRESH_INFO.available === false) return "";
    // 最近一次任务运行时间；旧状态文件里 generated_at 才是本次运行时间，
    // finished_at 曾被上一次数据主跑覆盖，不能当「刷新」显示。
    const runAt = REFRESH_INFO.run_finished_at || REFRESH_INFO.generated_at || REFRESH_INFO.finished_at;
    const tzTag = REFRESH_INFO.mode === "offline" ? "" : " 北京时间";
    const parts = [];
    parts.push("· 运行 " + shortTime(runAt) + tzTag);
    if (REFRESH_INFO.mode) parts.push(MODE_LABEL[REFRESH_INFO.mode] || REFRESH_INFO.mode);
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
    // ⑦ L4 四维证据：触屏点击固定口径说明（桌面 hover / 键盘 focus 由 CSS 负责）
    document.addEventListener("click", (ev) => {
      const el = ev.target && ev.target.closest ? ev.target.closest(".cf-ev, .cf-ev-h, .cf-vtip") : null;
      const wasOpen = el && el.classList.contains("open");
      document.querySelectorAll(".cf-ev.open, .cf-ev-h.open, .cf-vtip.open").forEach(x => x.classList.remove("open"));
      if (el && !wasOpen) el.classList.add("open");
      else if (el) el.blur();
    });
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
      cardCapital(s), cardCapitalAlert(s), cardTop20(s), cardChanlun(s),
      cardSentimentSummary(sent), cardOpinion(s),
    ].join("");
    initCharts(s, sent);
    loadMarketDivergence();   // 独立异步填充，不阻塞主仪表盘渲染
    if (scrollToCapital) {
      scrollToCapital = false;
      const el = document.getElementById("cardCapitalAlert");
      if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    } else if (scrollToOpinion) {
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
  // target: "opinion"（默认，定位机构观点）| "capital"（定位资金背离预警）
  function gotoProduct(code, target) {
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
    scrollToOpinion = target !== "capital";   // 跳转后直接定位到该品种的机构观点（含日期与时效）
    scrollToCapital = target === "capital";
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
  // 后端 ranking.net 是「看多−看空」计数差，离线 payload 又是 0~1 比例，两者口径不同；
  // 榜单统一由 bullish/bearish/neutral 现算净情绪比例，固定两位小数 + 百分号。
  function sentRatio(r) {
    const b = Number(r.bullish) || 0, be = Number(r.bearish) || 0, nt = Number(r.neutral) || 0;
    const tot = b + be + nt;
    return tot ? (b - be) / tot : 0;
  }
  function sentPct2(v) {
    return (v > 0 ? "+" : "") + (v * 100).toFixed(2) + "%";
  }
  function renderSentLists(catFilter) {
    const box = document.getElementById("sentLists");
    if (!box) return;
    const rl = filterRanking(catFilter).map(r => Object.assign({}, r, { _r: sentRatio(r) }));
    const bull = rl.filter(r => r._r > 0).sort((a, b) => b._r - a._r).slice(0, 12);
    const bear = rl.filter(r => r._r < 0).sort((a, b) => a._r - b._r).slice(0, 12);
    const li = (r) => `<li class="pick" data-code="${r.code}"><span class="pill ${r._r >= 0 ? "up" : "down"}">${sentPct2(r._r)}</span><div class="t">${r.name}(${r.code})</div><div class="m">看多${r.bullish}/看空${r.bearish}/中性${r.neutral} · ${r.category}</div></li>`;
    box.innerHTML =
      `<b class="muted" style="font-size:12px">偏多品种榜 <span style="font-weight:400">· 净情绪比例</span></b><ul class="list" style="margin-bottom:10px">${bull.map(li).join("") || '<li class="muted">无</li>'}</ul>` +
      `<b class="muted" style="font-size:12px">偏空品种榜 <span style="font-weight:400">· 净情绪比例</span></b><ul class="list">${bear.map(li).join("") || '<li class="muted">无</li>'}</ul>` +
      `<div class="note" style="margin-top:6px">净情绪比例 = (看多 − 看空) / (看多 + 看空 + 中性)，保留两位小数。</div>`;
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

  // ===== ⑥· 全市场资金背离预警（细分预警 + 全市场资金结构清单）=====
  // 上：当前品种前5 / 其余席位背离的细分预警（分歧加大标红、一般背离标琥珀）
  // 下：全市场清单（分歧 + 无分歧 + 无分层），异步加载，失败不影响其它模块
  function cardCapitalAlert(s) {
    const cf = s.capital_flow || {};
    const iv = cf.institutional_vs_retail || {};
    const inst = iv.institutional || {}, ret = iv.retail || {};
    const n = (v) => Number(v) || 0;
    const sgn = (v) => (v > 0 ? "+" : "") + fmt(v);
    const instNet = n(inst.net), retNet = n(ret.net);
    const instChg = n(inst.net_chg), retChg = n(ret.net_chg);
    const opp = (instNet > 0 && retNet < 0) || (instNet < 0 && retNet > 0);
    const gapChg = instChg - retChg;
    const widening = opp && ((instNet > 0 && gapChg > 0) || (instNet < 0 && gapChg < 0));
    const type = instNet > 0 ? "前5席位净多 / 其余席位净空" : "前5席位净空 / 其余席位净多";
    const nums = `前5席位净持仓 <b>${sgn(instNet)}</b> 手（当日 Δ ${sgn(instChg)}），其余席位 <b>${sgn(retNet)}</b> 手（当日 Δ ${sgn(retChg)}）`;
    let alertHtml;
    if (!cf.available) {
      alertHtml = `<div class="div-alert none">该品种暂无资金分层数据，无法判断前5 / 其余席位背离。</div>`;
    } else if (widening) {
      alertHtml = `<div class="alert-banner danger"><div class="ic">⚠</div><div><span class="ttl">当前品种 · ${type}（分歧加大）</span><br>${nums}；前5与其余席位价差当日继续扩大，属高确定性分歧——常见于底部蓄势 / 顶部派发的关键阶段，务必结合价格与总持仓放量确认拐点，切勿逆势盲动。</div></div>`;
    } else if (opp) {
      alertHtml = `<div class="alert-banner warn"><div class="ic">⚠</div><div><span class="ttl">当前品种 · ${type} 背离</span><br>${nums}；前5与其余席位方向相反，属典型分歧结构，但当日价差未继续扩大。需结合价格与总持仓放量确认方向，勿单独据此开仓。</div></div>`;
    } else {
      alertHtml = `<div class="div-alert none">当前品种前5与其余席位净持仓同向或未明显反向，未触发背离预警；是否为趋势行情，请看下方清单的「价 × 仓」确认。</div>`;
    }
    const body = `
      <div class="div-h"><span class="no">当前品种预警</span><em>前5 / 其余席位净持仓反向即触发，「分歧加大」标红</em></div>
      ${alertHtml}
      <div class="div-h"><span class="no">全市场资金结构清单</span><em>全部品种 · 点击任意行跳转该品种资金动向</em></div>
      <div class="div-guide"><b>无分歧 ≠ 预期一致 ≠ 趋势行情</b>：净持仓是当日榜单存量，不是当日资金流向；同向也可能是「结构不明」或「多头拥挤」。须用<b>价 × 仓</b>确认 —— 价涨仓增＝增量资金推动（趋势确认）；<span class="risky">价涨仓减＝空头回补 / 存量离场驱动（需警惕回落）</span>；价跌仓增＝新空进场（跌势延续）；价跌仓减＝多头离场（跌势尾声）。</div>
      <div id="divListBox"><div class="note">清单加载中…</div></div>
      <div class="cf-foot">口径：${PROXY_NOTE} 两组席位数随当日榜单变化（本品种 前5 ${n(inst.count) || 5} 席 / 其余 ${n(ret.count) || "—"} 席）；净占比低于 3% 判「方向不明」，不计入共识。价 / 仓为近 5 日主连序列，<b>噪音阈值按品种波动率自适应</b>（基准 ±0.8% / ±1.0%，按近 60 日稳健日波动率放缩 0.55~2.20 倍；农产品更严、有色/化工更宽，具体数值见清单上方噪音带与每行悬停；⑦ 单品种 L1 用更敏感的 ±0.35% / ±0.8%，但倍数相同）；5 日持仓变动超 15% 须先排除移仓换月。清单按「分歧加大 → 背离 → 需警惕回落 → 其它」排序，悬停任一行可看完整解读。</div>`;
    return card("全市场资金背离预警", "Divergence · 全市场", "span12", body, null, "cardCapitalAlert");
  }

  // 清单筛选：全部 / 仅分歧 / 无分歧 / 需警惕回落（跨分组）
  function applyDivFilter(f) {
    const box = $("#divListBox");
    if (!box) return;
    box.querySelectorAll(".div-fbtn").forEach(b => b.classList.toggle("on", b.dataset.f === f));
    box.querySelectorAll(".divrow").forEach(el => {
      const g = el.dataset.group, risk = el.dataset.risk;
      const show = f === "all" || (f === "div" && g === "div") ||
        (f === "ali" && g === "ali") || (f === "caution" && risk === "caution");
      el.style.display = show ? "" : "none";
    });
    box.querySelectorAll(".div-sec").forEach(sec => {
      const any = Array.from(sec.querySelectorAll(".divrow")).some(el => el.style.display !== "none");
      sec.style.display = any ? "" : "none";
    });
  }

  function renderDivergenceList(div) {
    if (!div) return `<div class="note">清单数据格式异常，刷新页面可重试；不影响上方各模块。</div>`;
    // 兼容旧接口：仅有 divergences 时退化为只显示分歧品种
    const all = Array.isArray(div.all) ? div.all
      : (Array.isArray(div.divergences) ? div.divergences : null);
    if (!all) return `<div class="note">清单数据格式异常，刷新页面可重试；不影响上方各模块。</div>`;
    const n = (v) => Number(v) || 0;
    const sgn = (v) => (v > 0 ? "+" : "") + fmt(v);
    const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g,
      c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
    const nb = div.noise;
    const noiseLine = nb ? `<div class="div-noise" title="${esc(nb.method)}｜基准（近5日）价 ±${nb.base_px}% / 仓 ±${nb.base_oi}%，按品种近 ${nb.window} 日稳健日波动率相对全市场基准（价 ${nb.ref_px}%/日、仓 ${nb.ref_oi}%/日）放缩。农产品等低波品种阈值收紧、有色/化工高波品种放宽，避免统一阈值把噪音当信号或把真信号磨平。"><b>⚙ 波动率自适应噪音带</b>：全市场 价 ±${n(nb.px_min).toFixed(2)}%~±${n(nb.px_max).toFixed(2)}% · 仓 ±${n(nb.oi_min).toFixed(2)}%~±${n(nb.oi_max).toFixed(2)}%<span class="muted">（低波 ${n(nb.regimes && nb.regimes["低波"])} / 中波 ${n(nb.regimes && nb.regimes["中波"])} / 高波 ${n(nb.regimes && nb.regimes["高波"])} 个；悬停每行看该品种阈值）</span></div>` : "";
    const noDiv = n(div.aligned_count) + n(div.neutral_count);
    const head = `<div class="div-summary">
      <span>全市场 <b>${n(div.total) || all.length}</b> 品种</span>
      <span>有资金数据 <b>${n(div.covered)}</b></span>
      <span class="danger">分歧 <b>${n(div.count)}</b>（加大 ${n(div.danger_count)}）</span>
      <span>无分歧 <b>${noDiv}</b> <span class="muted">${n(div.aligned_count)} 一致 / ${n(div.neutral_count)} 不明</span></span>
      <span class="risky">需警惕回落 <b>${n(div.caution_count)}</b></span>
      <span>无分层 <b>${n(div.nodata_count)}</b></span>
      <span class="muted">数据截至 ${fmtDate(div.as_of)}</span></div>
      ${noiseLine}
      <div class="div-filters">
        <button type="button" class="div-fbtn on" data-f="all">全部</button>
        <button type="button" class="div-fbtn" data-f="div">仅分歧</button>
        <button type="button" class="div-fbtn" data-f="ali">无分歧</button>
        <button type="button" class="div-fbtn" data-f="caution">⚠ 需警惕回落</button>
      </div>`;

    const rowHtml = (r) => {
      const stance = r.stance || (r.available === false ? "nodata" : "divergence");
      const lvl = r.level || "none";
      const cur = r.code === curProd ? " cur" : "";
      const grp = stance === "divergence" ? "div" : stance === "nodata" ? "nodata" : "ali";
      const risk = r.risk || "none";
      let dirChip;
      if (stance === "divergence") dirChip = `<span class="chip ${lvl === "danger" ? "danger" : "warn"}">${r.widening ? "分歧加大" : "背离"}</span>`;
      else if (stance === "nodata") dirChip = `<span class="chip none">无分层</span>`;
      else if (stance === "aligned_long") dirChip = `<span class="chip pos">一致净多</span>`;
      else if (stance === "aligned_short") dirChip = `<span class="chip neg">一致净空</span>`;
      else dirChip = `<span class="chip none">结构不明</span>`;
      const dirTone = (lvl === "danger" || lvl === "warn") ? lvl : "";
      const bandTip = r.band ? "\n" + r.band : "";
      const nbCell = r.px_eps != null
        ? `<span class="c-nb" title="${esc(r.band || "")}">噪 价±${n(r.px_eps).toFixed(2)}% / 仓±${n(r.oi_eps).toFixed(2)}% · ${esc(r.vol_regime || "")}</span>` : "";
      return `<div class="divrow ${lvl}${cur}" data-code="${esc(r.code)}" data-group="${grp}" data-risk="${esc(risk)}" title="${esc((r.note || "") + bandTip)}">
        <div class="c-prod"><b>${esc(r.name)}</b><span class="c-code">${esc(r.code)}</span><span class="c-mc">${esc(r.main_contract)}</span></div>
        <div class="c-dir ${dirTone}">${esc(r.direction)}${dirChip}</div>
        <div class="c-num"><span class="${cls(n(r.inst_net))}">前5 ${sgn(n(r.inst_net))}</span><span class="muted"> ${n(r.inst_ratio).toFixed(1)}%</span><br><span class="muted">Δ ${sgn(n(r.inst_chg))}</span></div>
        <div class="c-num"><span class="${cls(n(r.ret_net))}">其余 ${sgn(n(r.ret_net))}</span><span class="muted"> ${n(r.ret_ratio).toFixed(1)}%</span><br><span class="muted">Δ ${sgn(n(r.ret_chg))}</span></div>
        <div class="c-pq"><b>${esc(r.pq || "—")}</b><br><span class="muted">5日价 <span class="${cls(n(r.px_5d))}">${pct(n(r.px_5d))}</span> · 仓 <span class="${cls(n(r.oi_5d_pct))}">${pct(n(r.oi_5d_pct))}</span></span>${nbCell}</div>
        <div class="c-flag"><span class="chip f-${esc(risk)}">${esc(r.flag || "—")}</span></div>
      </div>`;
    };
    const groups = [
      { key: "div", title: "① 前5 / 其余席位分歧", hint: "净持仓反向，多空对峙", rows: all.filter(r => (r.stance || "divergence") === "divergence") },
      { key: "ali", title: "② 无分歧：方向一致或结构不明", hint: "一致 ≠ 趋势行情，看右侧价仓确认", rows: all.filter(r => { const st = r.stance || "divergence"; return st !== "divergence" && st !== "nodata"; }) },
      { key: "nodata", title: "③ 暂无会员持仓排名", hint: "仅有价量仓，无法判断前5 / 其余席位结构", rows: all.filter(r => r.stance === "nodata") },
    ];
    const body = groups.map(g => g.rows.length ? `<div class="div-sec" data-sec="${g.key}">
      <div class="div-sec-h"><b>${g.title}</b><span>${g.rows.length} 个</span><em>${g.hint}</em></div>
      ${g.rows.map(rowHtml).join("")}</div>` : "").join("");
    return head + (body ? `<div class="divlist">${body}</div>` : `<div class="note">当前无可用品种数据。</div>`);
  }

  // 清单独立异步填充：带 8s 超时，任何失败都只影响这一块，不阻塞整页
  async function loadMarketDivergence() {
    const box = $("#divListBox");
    if (!box) return;
    let div = null;
    try {
      const ctl = typeof AbortController !== "undefined" ? new AbortController() : null;
      const timer = ctl ? setTimeout(() => ctl.abort(), 8000) : null;
      let resp;
      try {
        resp = await fetch("/api/market_divergence", ctl ? { signal: ctl.signal } : undefined);
      } finally {
        if (timer) clearTimeout(timer);
      }
      div = await resp.json();
    } catch (e) {
      box.innerHTML = `<div class="note">清单暂不可用（接口未响应或超时），刷新页面可重试；不影响上方各模块。</div>`;
      return;
    }
    box.innerHTML = renderDivergenceList(div);
    box.querySelectorAll(".divrow").forEach(el => {
      el.onclick = () => gotoProduct(el.dataset.code, "capital");
    });
    box.querySelectorAll(".div-fbtn").forEach(b => {
      b.onclick = () => applyDivFilter(b.dataset.f);
    });
  }

  // ===== 波动率自适应噪音带（⑥ 全市场清单 / ⑦ L1 与后端 market_view.py 同一口径）=====
  // 农产品日内波动小、有色 / 化工大：统一的 ±0.35% / ±0.8% 会让低波品种把噪音当信号、
  // 高波品种把真信号磨平。取近 60 日主连「日收益 / 日持仓变化率」的稳健波动率
  // (MAD × 1.4826，对涨跌停与移仓跳空不敏感)，以相对全市场基准波动率的倍数放缩
  // 基准噪音带，倍数裁剪 0.55~2.20；不同观察周期再乘 √(周期/5)。
  const NOISE_REF = { px: 1.30, oi: 3.60, min: 0.55, max: 2.20, window: 60 };
  function capFlowNoise(rows) {
    const src = (rows || []).slice(-(NOISE_REF.window + 1));
    const num = (v) => (typeof v === "number" && isFinite(v) ? v : 0);
    const median = (arr) => {
      const a = arr.slice().sort((x, y) => x - y), m = a.length >> 1;
      return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2;
    };
    const changes = (key) => {
      const out = []; let prev = 0;
      src.forEach(r => {
        const v = num(r[key]);
        if (v > 0) { if (prev) out.push((v / prev - 1) * 100); prev = v; }
        else prev = 0;
      });
      return out;
    };
    const sigma = (arr, floor) => {
      if (arr.length < 8) return null;
      const med = median(arr);
      let s = 1.4826 * median(arr.map(v => Math.abs(v - med)));
      if (s < floor * 0.25) {                    // 序列过于平坦 → 用总体标准差兜底
        const mean = arr.reduce((x, y) => x + y, 0) / arr.length;
        s = Math.sqrt(arr.reduce((x, y) => x + (y - mean) * (y - mean), 0) / arr.length);
      }
      return Math.max(floor, s);
    };
    const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
    const sp = sigma(changes("close"), 0.05), so = sigma(changes("oi"), 0.30);
    const ok = sp != null && so != null;
    const rp = ok ? clamp(sp / NOISE_REF.px, NOISE_REF.min, NOISE_REF.max) : 1;
    const ro = ok ? clamp(so / NOISE_REF.oi, NOISE_REF.min, NOISE_REF.max) : 1;
    const eps = (h, basePx, baseOi) => {
      const k = Math.sqrt(Math.max(1, h) / 5);
      return { px: basePx * rp * k, oi: baseOi * ro * k };
    };
    return {
      ok, sigmaPx: sp || 0, sigmaOi: so || 0, ratioPx: rp, ratioOi: ro,
      regime: ok ? (rp < 0.8 ? "低波" : rp > 1.35 ? "高波" : "中波") : "样本不足",
      eps,
      z: (px5, oi5) => ({ px: ok ? px5 / (sp * Math.sqrt(5)) : 0,
                          oi: ok ? oi5 / (so * Math.sqrt(5)) : 0 }),
    };
  }

  // ===== ⑦ 专业资金分析框架 · 计算层 =====
  // 依据：净持仓分布 / 总持仓变动 / 大单动向 三维度，且「净持仓变化 > 绝对持仓量」。
  // 升级：L1 价量仓配合、L2 归一化结构矩阵（主动/被动拆解）、L3 席位动能、L4 多维共振读数。
  // 纯函数、只读，输入为当前快照，不依赖任何后端新增接口。
  function capFlowAnalytics(s, cf, noise) {
    const tiers = (cf.tiers || []).slice();
    const iv = cf.institutional_vs_retail || {};
    const inst = iv.institutional || {}, ret = iv.retail || {};
    const members = (cf.top_members || []).slice();
    const lo = cf.large_orders || {};
    const rows = (s && s.kline && s.kline.rows) || [];
    const num = (v) => (typeof v === "number" && isFinite(v) ? v : 0);
    const sgn = (v) => (v > 0 ? 1 : v < 0 ? -1 : 0);
    const avg = (arr) => { const v = arr.filter(x => typeof x === "number" && isFinite(x) && x > 0);
      return v.length ? v.reduce((x, y) => x + y, 0) / v.length : 0; };
    const volStateOf = (r) => r >= 1.5 ? "显著放量" : r >= 1.2 ? "温和放量"
      : r <= 0.6 ? "明显缩量" : r <= 0.8 ? "温和缩量" : "量能平稳";

    // ---- L1 量能画像：以「前 20 日均量」为基准，避免单日跳量误判 ----
    // 量比 = 当日成交量 / 前20日均量；量能趋势 = 近5日均量 / 前5日均量（资金关注度升降）。
    const volSeries = rows.slice(-41).map(r => num(r.volume));
    const volLast = volSeries.length ? volSeries[volSeries.length - 1] : 0;
    const volBase20 = avg(volSeries.slice(-21, -1));
    const volMa5 = avg(volSeries.slice(-5)), volMa20 = avg(volSeries.slice(-20));
    const volPrevMa5 = avg(volSeries.slice(-10, -5));
    const volRatio20 = volBase20 ? volLast / volBase20 : 1;
    const volTrend = volPrevMa5 ? volMa5 / volPrevMa5 : 1;
    const VP = {
      last: volLast, base20: volBase20, ma5: volMa5, ma20: volMa20, prevMa5: volPrevMa5,
      ratio: volRatio20, trend: volTrend, state: volStateOf(volRatio20),
      trendTag: volTrend >= 1.2 ? "近5日持续放量（关注度升温）"
        : volTrend <= 0.8 ? "近5日持续缩量（关注度降温）" : "近5日量能平稳",
      maTag: volPrevMa5 ? (volMa5 >= volPrevMa5 ? "VMA5 走高" : "VMA5 走低") : "—",
    };

    // ---- L1 价 · 量 · 仓：以区间首尾对齐，量能对比区间均量 ----
    function pvStat(n) {
      const a = rows.slice(-n);
      if (a.length < 2) return null;
      const f = a[0], l = a[a.length - 1];
      const dPrice = num(f.close) ? (num(l.close) - num(f.close)) / num(f.close) * 100 : 0;
      let dOi = num(l.oi) - num(f.oi);
      if (!dOi && a.some(r => r.oi_chg != null))
        dOi = a.slice(1).reduce((x, r) => x + num(r.oi_chg), 0);
      const dOiPct = num(f.oi) ? dOi / num(f.oi) * 100 : 0;
      const avgVol = avg(a.slice(0, -1).map(r => num(r.volume)));
      const volRatio = avgVol ? num(l.volume) / avgVol : 1;
      return { days: n - 1, from: f.date, to: l.date, close: num(l.close),
        dPrice, oi: num(l.oi), dOi, dOiPct,
        volume: num(l.volume), avgVol, volRatio, volState: volStateOf(volRatio) };
    }
    const pv1 = pvStat(2), pv5 = pvStat(6), pv20 = pvStat(21);
    const base = pv5 || pv1 || pv20;
    // 噪音阈值按本品种波动率自适应：基准 价 ±0.35% / 仓 ±0.8%（近 5 日口径）
    const NZ = noise || capFlowNoise(rows);
    const horizon = base ? base.days : 5;
    const NB = NZ.eps(horizon, 0.35, 0.8);
    const PT = NB.px, OT = NB.oi;
    const zNow = NZ.z(base ? base.dPrice : 0, base ? base.dOiPct : 0);
    let quad;
    if (!base) {
      quad = { key: "na", name: "数据不足", bias: 0, res: 0,
        desc: "缺少连续价格与持仓序列，无法判定价仓配合。" };
    } else {
      const p = base.dPrice, o = base.dOiPct;
      const up = p > PT, dn = p < -PT, oiUp = o > OT, oiDn = o < -OT;
      if (up && oiUp) quad = { key: "up_oiup", name: "价涨仓增 · 多头主动建仓", bias: 1, res: 1,
        desc: "增量资金入场做多推动价格，是最健康的上涨结构；若成交量同步放大，趋势延续概率较高。后续留意持仓见顶后的减仓拐点。" };
      else if (up && oiDn) quad = { key: "up_oidn", name: "价涨仓减 · 空头回补推动", bias: 0.45, res: -0.6,
        desc: "上涨主要来自空头平仓而非新多进场，属资金流出型上涨。空平一旦结束容易失去动能，追多需谨慎。" };
      else if (dn && oiUp) quad = { key: "dn_oiup", name: "价跌仓增 · 空头主动建仓", bias: -1, res: 1,
        desc: "增量资金入场做空压制价格，下跌有资金支撑，趋势偏空。关注持仓见顶回落后的空平反弹。" };
      else if (dn && oiDn) quad = { key: "dn_oidn", name: "价跌仓减 · 多头离场", bias: -0.45, res: -0.6,
        desc: "下跌来自多头平仓止损，属资金流出型下跌，抛压逐步释放，跌势可能接近尾声，不宜追空。" };
      else if (!up && !dn && oiUp) quad = { key: "flat_oiup", name: "价平仓增 · 分歧蓄势", bias: 0, res: 0.2,
        desc: "价格横盘而持仓明显增加，多空分歧加大、资金对峙，常是变盘前夜。宜等方向选择，不宜提前押注。" };
      else if (!up && !dn && oiDn) quad = { key: "flat_oidn", name: "价平仓减 · 资金离场", bias: 0, res: 0,
        desc: "价格横盘、持仓下降，资金观望离场，短期波动大概率收敛。" };
      else if (up) quad = { key: "up_flat", name: "价涨仓平 · 存量博弈", bias: 0.3, res: 0.2,
        desc: "价格上涨但持仓变化不大，由存量资金博弈推动，信号强度偏弱。" };
      else if (dn) quad = { key: "dn_flat", name: "价跌仓平 · 存量博弈", bias: -0.3, res: 0.2,
        desc: "价格下跌但持仓变化不大，缺乏增量空头，信号强度偏弱。" };
      else quad = { key: "flat", name: "价仓双平 · 观望", bias: 0, res: 0,
        desc: "价格与持仓均无明显变化，资金面中性。" };
      quad.band = NB;
      quad.horizon = horizon;
    }

    // ---- L1 量 × 价 × 仓：三要素配合结论 ----
    // 量能只做「确认 / 削弱」价仓信号，不单独定方向：同样的价涨仓增，放量与缩量含义完全不同。
    const inc = ["up_oiup", "dn_oiup", "flat_oiup"].indexOf(quad.key) >= 0;
    const dec = ["up_oidn", "dn_oidn", "flat_oidn"].indexOf(quad.key) >= 0;
    const heavy = VP.ratio >= 1.2, light = VP.ratio <= 0.8;
    let VV;
    if (heavy && inc) VV = { tag: "放量增仓 · 增量资金确认", confirm: 1, tone: "strong",
      desc: "成交放大且持仓同步增加，说明新资金真金白银进场建仓，价格方向有量能支撑，是三要素最健康的共振结构；后续盯持仓能否持续增加，持仓见顶往往就是动能拐点。" };
    else if (heavy && dec) VV = { tag: "放量减仓 · 集中平仓/回补", confirm: -0.4, tone: "warn",
      desc: "成交放大但持仓下降，换手来自集中平仓与回补。方向虽凌厉，却是存量资金离场驱动，常出现在行情加速段或尾声；不宜按趋势延续追单，留意量能衰竭后的反转。" };
    else if (heavy) VV = { tag: "放量但价仓未共振 · 高换手分歧", confirm: 0, tone: "warn",
      desc: "成交明显放大，价格与持仓却未同步给出方向，多为多空激烈换手、移仓换月或突发消息扰动。此时量能不等于方向，应等价格与持仓先选边。" };
    else if (light && inc) VV = { tag: "缩量增仓 · 轻仓试探", confirm: -0.45, tone: "warn",
      desc: "持仓增加但成交萎缩，增仓集中在少数席位、缺乏广泛参与，趋势尚待量能确认，谨防假突破与流动性不足下的滑点。" };
    else if (light && dec) VV = { tag: "缩量减仓 · 离场尾声", confirm: -0.3, tone: "warn",
      desc: "成交与持仓同步萎缩，资金温和撤离、观望情绪浓，原有动能在衰竭，波动大概率收敛，方向性判断应降权。" };
    else if (light) VV = { tag: "缩量整理 · 参与度低", confirm: -0.2, tone: "warn",
      desc: "量能低迷，价格与持仓信号强度打折；在放量确认前，不宜把小幅价仓变化当作趋势信号。" };
    else VV = { tag: "量能平稳 · 常规确认", confirm: 0.15, tone: "flat",
      desc: "成交量处于前20日均量附近，价仓信号按常规强度解读；若价格方向与持仓变化一致，可信度更高，反之仍属回补/平仓型行情。" };
    if (VV.tone === "strong") VV.tone = quad.bias > 0 ? "pos" : quad.bias < 0 ? "neg" : "neu";

    // 量价背离：价格创近 20 日新高/新低，但成交明显萎缩（排除当日极值）
    const win20 = rows.slice(-20), lastClose = rows.length ? num(rows[rows.length - 1].close) : 0;
    const priorCloses = win20.slice(0, -1).map(r => num(r.close)).filter(v => v > 0);
    let volPriceDiv = null;
    if (priorCloses.length >= 10 && VP.ratio <= 0.8) {
      if (lastClose > Math.max(...priorCloses)) volPriceDiv = "价格创近20日新高但成交量明显萎缩，上攻缺乏量能配合，警惕冲高回落。";
      else if (lastClose < Math.min(...priorCloses)) volPriceDiv = "价格创近20日新低但成交量明显萎缩，杀跌动能衰减，不宜追空。";
    }
    VP.divergence = volPriceDiv;

    // ---- 主动 / 被动拆解：同样的净变化，多增与空平含义完全不同 ----
    function actionOf(L, S) {
      const l = num(L), s = num(S);
      if (Math.abs(l) < 1 && Math.abs(s) < 1) return { tag: "基本持平", kind: "flat" };
      if (l > 0 && s <= 0) return { tag: "多头主动进攻", kind: "active", dir: 1 };
      if (s > 0 && l <= 0) return { tag: "空头主动进攻", kind: "active", dir: -1 };
      if (l > 0 && s > 0) return l >= s
        ? { tag: "双向增仓·多占优", kind: "dual", dir: 1 }
        : { tag: "双向增仓·空占优", kind: "dual", dir: -1 };
      if (l < 0 && s < 0) return Math.abs(s) >= Math.abs(l)
        ? { tag: "空头回补主导", kind: "passive", dir: 1 }
        : { tag: "多头平仓主导", kind: "passive", dir: -1 };
      return { tag: (l - s) > 0 ? "净多微增" : "净空微增", kind: "dual", dir: sgn(l - s) };
    }

    // ---- L2 资金结构矩阵：净占比 / 多空比 / 变化强度 / 结构权重（归一化后可跨品种比较）----
    const totalGross = tiers.reduce((x, t) => x + num(t.long) + num(t.short), 0) || 1;
    const stRows = tiers.map(t => {
      const gross = num(t.long) + num(t.short);
      return { tier: t.tier, count: t.count, long: t.long, short: t.short,
        net: t.net, net_chg: t.net_chg, long_chg: t.long_chg, short_chg: t.short_chg, gross,
        netRatio: gross ? num(t.net) / gross * 100 : 0,
        chgRatio: gross ? num(t.net_chg) / gross * 100 : 0,
        lsRatio: num(t.short) ? num(t.long) / num(t.short) : null,
        weight: gross / totalGross * 100,
        action: actionOf(t.long_chg, t.short_chg) };
    });
    const topGross = stRows.length ? stRows[0].gross : 0;
    const concentration = topGross / totalGross * 100;  // 头部层持仓占比（CR5 代理）

    // ---- L3 主力席位动能：按净变化排序，标注主动/被动 ----
    const memRows = members.map(m => ({ name: m.name, net: m.net, net_chg: m.net_chg,
      long: m.long, short: m.short, long_chg: m.long_chg, short_chg: m.short_chg,
      action: actionOf(m.long_chg, m.short_chg) }))
      .sort((a, b) => num(b.net_chg) - num(a.net_chg));
    const leaders = memRows.filter(m => num(m.net_chg) > 0).slice(0, 6);
    const laggards = memRows.filter(m => num(m.net_chg) < 0).slice(-6).reverse();

    // ---- 前5 / 其余席位：存量方向背离 + 当日变化方向背离 + 分歧是否扩大 ----
    const instChg = num(inst.net_chg), retChg = num(ret.net_chg);
    const chgDivergence = sgn(instChg) !== 0 && sgn(retChg) !== 0 && sgn(instChg) !== sgn(retChg);
    const posDivergence = sgn(num(inst.net)) !== 0 && sgn(num(ret.net)) !== 0 &&
      sgn(num(inst.net)) !== sgn(num(ret.net));
    const widening = chgDivergence && posDivergence && sgn(instChg) === sgn(num(inst.net));

    // ---- L4 四维共振读数（启发式，非预测）----
    const norm = (v, scale) => Math.max(-1, Math.min(1, v / (scale || 1)));
    const instScale = Math.max(1, (num(inst.long) + num(inst.short)) * 0.02);
    const chgList = stRows.map(r => num(r.net_chg));
    const nonzero = chgList.filter(v => v !== 0);
    const agree = nonzero.length
      ? Math.max(chgList.filter(v => v > 0).length, chgList.filter(v => v < 0).length) / nonzero.length : 0;
    const upSum = (lo.top_net_increase || []).reduce((x, m) => x + num(m.net_chg), 0);
    const dnSum = (lo.top_net_decrease || []).reduce((x, m) => x + num(m.net_chg), 0);
    const pvConfirm = 1 + 0.3 * VV.confirm;   // 量能对价仓信号的确认系数 0.7 ~ 1.3
    const pvBias = Math.max(-1, Math.min(1, quad.bias * pvConfirm));
    const parts = [
      { k: "价·量·仓配合", v: pvBias, w: 0.35,
        txt: `${quad.name}；${VV.tag}（量比 ${VP.ratio.toFixed(2)}×）` },
      { k: "前5席位净变化", v: norm(instChg, instScale), w: 0.25,
        txt: `前${inst.count || 5}席位当日净变化 ${instChg > 0 ? "+" : ""}${fmt(instChg)} 手` },
      { k: "分层一致性", v: sgn(chgList.reduce((x, v) => x + v, 0)) * (agree - 0.5) * 2, w: 0.20,
        txt: `${nonzero.length} 个资金层级中同向 ${Math.round(agree * nonzero.length)} 个` },
      { k: "大单动向", v: norm(upSum + dnSum, Math.max(1, (Math.abs(upSum) + Math.abs(dnSum)) * 0.55)), w: 0.20,
        txt: `头部净多增 ${fmt(upSum)} / 净空增 ${fmt(dnSum)} 手` },
    ];
    // ---- 四维证据的悬停口径：静态规则 + 本品种实况 ----
    const [pPv, pInst, pAgree, pLo] = parts;
    const posCnt = chgList.filter(v => v > 0).length;
    const negCnt = chgList.filter(v => v < 0).length;
    const flatCnt = chgList.length - nonzero.length;
    const contrib = (p) => `${p.v * p.w > 0 ? "+" : ""}${(p.v * p.w).toFixed(2)}`;
    const scoreRule = "读数 = 50 + 50 × Σ(各维归一化值 × 权重)，裁剪到 0~100；50 为中性，≥58 偏多、≤42 偏空。";
    pPv.tip = `【价·量·仓配合 · 权重 35%】\n近${horizon}日价格 × 总持仓构成四象限：价涨仓增／价跌仓减等按方向给 ±1 或 0，价仓背离时判为平仓驱动、方向偏弱。\n噪音阈值按品种波动率自适应：本品种 价 ±${NB.px.toFixed(2)}% / 仓 ±${NB.oi.toFixed(2)}%（${NZ.regime}，基准 ±0.35% / ±0.8% × ${NZ.ratioPx.toFixed(2)} / ${NZ.ratioOi.toFixed(2)} 倍），超过阈值才算有方向。\n成交量只作确认系数（0.7~1.3）调节强度，不单独产生方向：放量增仓增强，缩量或放量减仓削弱。\n本品种贡献 ${contrib(pPv)}。${scoreRule}`;
    pInst.tip = `【前5席位净变化 · 权重 25%】\n取前 ${inst.count || 5} 席位（二分口径的前5席位）当日净变化，除以该层双边持仓 ×2% 归一化到 ±1。\n净变化 = 多头增减 − 空头增减，空头回补也会显示为正，须回 L2 看「变化性质」区分新多进场与被动回补。\n本品种贡献 ${contrib(pInst)}。`;
    pAgree.tip = `【分层一致性 · 权重 20%】\n把四档「当日净变化」只取符号（+ 净多增 / − 净空增），数同向层数、取多数方；只数层数、不比金额，防止单层巨额调仓把「共识」伪装出来。方向取四层净变化合计的正负。\n强度按同向占比折算：4/4 → ±0.20 满额，3/4 → ±0.10，2/4 → 0（弃权，20% 权重不贡献方向）。\n本品种：净多增 ${posCnt} 层 / 净空增 ${negCnt} 层${flatCnt ? ` / 持平 ${flatCnt} 层` : ""}，此维贡献 ${contrib(pAgree)}。\n2/4 对半是常态，意味着各层分歧最大；此时勿把前5席位单笔大额变化当成全市场看多/看空，回到 L1 用价·量·仓确认。`;
    pLo.tip = `【大单动向 · 权重 20%】\n取「头部净多增 TOP」与「头部净空增 TOP」两组当日净变化合计，按两组绝对额之和的 55% 归一化到 ±1，衡量头部席位调仓合力。\n与前5席位净变化可能重叠，用于交叉验证而非独立信号。\n本品种贡献 ${contrib(pLo)}。`;
    const raw = parts.reduce((x, p) => x + p.v * p.w, 0);
    const score = Math.round(Math.max(0, Math.min(100, 50 + 50 * raw)));
    const sameSign = sgn(raw) === 0 ? 0 : parts.filter(p => sgn(p.v) === sgn(raw)).length;
    const label = score >= 70 ? "强偏多" : score >= 58 ? "偏多" : score > 42 ? "中性" : score > 30 ? "偏空" : "强偏空";
    const tone = score >= 58 ? "up" : score <= 42 ? "down" : "muted";
    const evidence = parts.map(p => `${p.k}：${p.txt}`);
    const risks = [];
    if (quad.res < -0.3) risks.push("价仓背离：价格方向与资金进出不一致，属回补/平仓驱动，趋势动能偏弱，不宜顺势追单。");
    if (VV.confirm <= -0.2) risks.push(`量能未确认价仓方向：${VV.tag}，L1 信号强度已按 ${pvConfirm.toFixed(2)} 系数下调，勿单独据此定方向。`);
    if (VP.divergence) risks.push(VP.divergence);
    if (VP.trend <= 0.8) risks.push("近5日均量较前5日回落超20%，资金关注度降温，突破信号需更严格确认。");
    if (VP.ratio > 1.8) risks.push(`当日成交量为前20日均量的 ${VP.ratio.toFixed(1)} 倍，极端放量需排除移仓换月与突发事件扰动。`);
    if (posDivergence) risks.push(widening
      ? "前5与其余席位持仓方向相反，且前5席位继续朝原方向加码，分歧正在扩大，波动风险上升。"
      : "前5与其余席位持仓方向相反，属典型分歧结构；需价格与总持仓放量共同确认，勿单独据此开仓。");
    if (chgDivergence) risks.push("当日前5与其余席位净持仓变化方向相反，短线博弈加剧。");
    if (concentration > 45) risks.push(`头部资金层持仓占比 ${concentration.toFixed(1)}%，集中度高，少数席位调仓即可放大波动。`);
    if (pv5 && pv5.volRatio > 1.8 && VP.ratio <= 1.8) risks.push(`最新成交量约为近 ${pv5.days} 日均量的 ${pv5.volRatio.toFixed(1)} 倍，放量日需防换月或突发事件扰动。`);
    if (!risks.length) risks.push("各维度未出现显著背离，但仍需结合基本面与价格结构确认；前5 / 其余席位为按净持仓规模推断的代理口径，交易所不披露终端客户属性。");

    return { pv: { pv1, pv5, pv20, base, quad, rows: rows.slice(-60) },
      volume: { profile: VP, verdict: VV },
      structure: { rows: stRows, concentration, totalGross },
      momentum: { all: memRows, leaders, laggards },
      divergence: { position: posDivergence, change: chgDivergence, widening, instChg, retChg },
      noise: { profile: NZ, band: NB, horizon, z: zNow },
      score: { value: score, label, tone, confirm: sameSign, total: parts.length, parts, evidence, risks, rule: scoreRule } };
  }

  // 三榜合一总持仓占比（席位口径，与 ⑦「前5席位 / 其余席位」同源同日）
  // 「三榜合一」指席位集合：多单榜 / 空单榜 / 成交量榜各 20 名按会员简称合并去重（通常 27~37 席）。
  // 分子并不是三榜持仓量相加：long 只来自多单榜、short 只来自空单榜；只在成交量榜出现的席位
  // long/short 记 0，所以并集对分子零增量 —— 分子恒等于「多单榜前20持买 + 空单榜前20持卖」。
  // 分母是主连总持仓；多空两侧分别相除取均值 = (持买+持卖)/(2×总持仓)。
  // 分子：多单 / 空单 / 成交量三榜按会员简称合并去重后的「全部席位」持买 / 持卖合计
  //       = capital_flow.institutional_vs_retail 两组 long / short 之和（两组覆盖全部 member_count 席位，
  //         不再是旧口径的「前20会员」榜单合计，后者来自另一路 NeoData 数据、与资金结构不同源）。
  // 分母：capital_flow.total_position.latest（主连单边总持仓）。
  // 多空两侧分别相除后取均值 = (持买+持卖)/(2×总持仓)，衡量三榜席位对全市场持仓的覆盖率 / 集中度。
  // 榜单为主力合约、总持仓为主连口径，合约换月时可能错位（>100%），故仅作集中度参考。
  function calcSeatShare(s, cf) {
    const tp = (cf && cf.total_position) || {};
    const n = (v) => (typeof v === "number" && isFinite(v) ? v : 0);
    const latest = n(tp.latest);
    if (latest <= 0) return { valid: false };
    const share = (L, S, count, basis) => {
      if (L <= 0 || S <= 0) return null;
      const lp = L / latest * 100, sp = S / latest * 100;
      return {
        valid: true, pct: (lp + sp) / 2, longPct: lp, shortPct: sp,
        longTotal: L, shortTotal: S, latest, count, basis,
        over: lp > 100 || sp > 100
      };
    };
    const iv = (cf && cf.institutional_vs_retail) || {};
    const inst = iv.institutional || {}, ret = iv.retail || {};
    const merged = share(n(inst.long) + n(ret.long), n(inst.short) + n(ret.short),
      n(inst.count) + n(ret.count), "三榜合一");
    if (merged) return merged;
    // 旧快照缺 long / short 字段时才回退 top20 榜单。注意：top20 来自一次性导入的
    // NeoData 静态快照（全市场 41 品种的 long_total 长期未变，as_of 早于当前交易日），并非当日榜单。
    // 旧分子对应的是当时的高持仓，与当日已缩小的分母相除会虚高，故标记 stale 并在界面显著标注。
    const t = (s && s.top20) || {};
    const fallback = share(n(t.long_total), n(t.short_total),
      (t.members && t.members.length) || 0, "前20榜单");
    if (fallback) fallback.stale = true;
    return fallback || { valid: false };
  }

  // ⑦ 速读卡片：五维精炼（钱在不在 / 谁在动 / 前5 vs 其余席位 / 大单 / 历史回溯）+ 一句话整体判断
  // 口径：净持仓＝当日榜单存量；Δ＝席位当日流量；前5 / 其余席位按席位净持仓规模划分。
  // seat：由 calcSeatShare 计算的「三榜合一总持仓占比」，作为纯入参传入，保持调用链单向无环。
  function capFlowRead(cf, noise, seat) {
    const tp = cf.total_position || {};
    const tiers = cf.tiers || [];
    const iv = cf.institutional_vs_retail || {};
    const inst = iv.institutional || {}, ret = iv.retail || {};
    const lo = cf.large_orders || {};
    const num = (v) => (typeof v === "number" && isFinite(v) ? v : 0);
    const sgn = (n) => (n > 0 ? "+" : "") + fmt(n);
    const cv = (n) => `<b class="${cls(n)}">${sgn(n)}</b>`;
    const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    const dir = (v, th) => (v > th ? 1 : v < -th ? -1 : 0);
    // 资金进出的噪音阈值与 L1 共用同一「持仓波动率倍数」，基准为近 5 日 ±0.3%
    const oiTh = (h) => noise ? noise.eps(h, 0, 0.3).oi : 0.3 * Math.sqrt(Math.max(1, h) / 5);

    // ① 钱在不在：总持仓变动（用相对变化，跨品种可比）
    const latest = num(tp.latest), d1 = num(tp.chg_1d), d5 = num(tp.chg_5d), d20 = num(tp.chg_20d);
    const p5 = latest ? d5 / latest * 100 : 0;
    const p20 = latest ? d20 / latest * 100 : 0;
    const s1 = dir(latest ? d1 / latest * 100 : 0, oiTh(1));
    const s5 = dir(p5, oiTh(5)), s20 = dir(p20, oiTh(20));
    const money = s5 > 0 ? "资金进场" : s5 < 0 ? "资金撤离" : "存量博弈";
    const moneyNote = s5 === 0 ? "5 日进出基本平衡"
      : `${money}：${s1 === s5 ? "1 日与 5 日同向" : `当日${s5 > 0 ? "回吐" : "回补"}`}`;

    // ② 谁在动：按席位 |净持仓| 名次分档的净持仓分布
    const nPos = tiers.filter(t => num(t.net) > 0).length;
    const nNeg = tiers.filter(t => num(t.net) < 0).length;
    const nUp = tiers.filter(t => num(t.net_chg) > 0).length;
    const lead = tiers.slice().sort((a, b) => Math.abs(num(b.net)) - Math.abs(num(a.net)))[0] || null;
    const mover = tiers.slice().sort((a, b) => Math.abs(num(b.net_chg)) - Math.abs(num(a.net_chg)))[0] || null;
    const mL = num(mover && mover.long_chg), mS = num(mover && mover.short_chg), mC = num(mover && mover.net_chg);
    let moverAct = "微调";
    if (mL > 0 && mS <= 0) moverAct = "新多进场";
    else if (mS > 0 && mL <= 0) moverAct = "新空进场";
    else if (mL < 0 && mS < 0) moverAct = mC > 0 ? "空头回补" : "多头平仓";
    else if (mL > 0 && mS > 0) moverAct = mC > 0 ? "双向增仓·多占优" : "双向增仓·空占优";
    const sameTier = !!(lead && mover && lead.tier === mover.tier);
    const tierNote = sameTier
      ? `${tierLabel(lead.tier)} ${lead.net > 0 ? "净多" : "净空"} ${sgn(num(lead.net))} 主导，当日${moverAct}；${nUp}/4 档净多增`
      : lead && mover
        ? `${tierLabel(lead.tier)} ${lead.net > 0 ? "净多" : "净空"} ${sgn(num(lead.net))} 主导；${tierLabel(mover.tier)} ${moverAct}，${nUp}/4 档净多增`
        : "暂无分层数据";

    // ③ 前5席位 vs 其余席位（席位口径）：重点看背离与价差方向
    const instNet = num(inst.net), instChg = num(inst.net_chg);
    const retNet = num(ret.net), retChg = num(ret.net_chg);
    const diverg = (instNet > 0 && retNet < 0) || (instNet < 0 && retNet > 0);
    const gapChg = instChg - retChg;
    const widening = diverg && ((instNet > 0 && gapChg > 0) || (instNet < 0 && gapChg < 0));
    const instAct = instChg > 0 ? (instNet > 0 ? "前5席位净多加码" : "前5席位空头回补")
      : instChg < 0 ? (instNet > 0 ? "前5席位净多减" : "前5席位净空加码") : "前5席位持平";
    const retAct = retChg > 0 ? (retNet > 0 ? "其余席位净多加码" : "其余席位空头回补")
      : retChg < 0 ? (retNet > 0 ? "其余席位净多减" : "其余席位净空加码") : "其余席位持平";
    const divWord = diverg ? (widening ? "前5 / 其余席位背离扩大" : "前5 / 其余席位背离收窄") : "前5与其余席位同向";
    const divNote = diverg
      ? `${instNet > 0 ? "前5席位净多 / 其余席位净空" : "前5席位净空 / 其余席位净多"}，当日价差${widening ? "继续扩大" : "收窄"}`
      : "方向未明显反向，未触发背离";
    // 背离长段落预警：讲清市场含义与操作提醒（与 ⑥ 独立预警卡并存）
    let alert = null;
    if (diverg) {
      const instBull = instNet > 0;
      alert = {
        type: instBull ? "前5席位净多 / 其余席位净空" : "前5席位净空 / 其余席位净多",
        level: widening ? "danger" : "warn",
        desc: instBull
          ? "前5席位偏多、其余席位偏空，典型分歧结构。常见于下跌末端或底部蓄势（前5席位低位承接），也需警惕前5席位被套后的摊平。务必结合价格与总持仓放量确认方向，勿单独据此开仓。"
          : "前5席位偏空、其余席位偏多，典型分歧结构。常见于上涨末端或顶部派发（其余席位追高、前5席位离场），警惕反转。务必结合价格与总持仓放量确认方向。"
      };
    }

    // ④ 大单动向：头部席位前 3 增减合计与领增/领减
    const inc = lo.top_net_increase || [], dec = lo.top_net_decrease || [];
    const sum3 = (arr) => arr.slice(0, 3).reduce((x, m) => x + num(m.net_chg), 0);
    const i3 = sum3(inc), d3 = sum3(dec), bigNet = i3 + d3;
    const balance = Math.abs(bigNet) < Math.max(Math.abs(i3), Math.abs(d3)) * 0.15 ? "增减对峙"
      : bigNet > 0 ? "净增多" : "净减多";
    const bigNote = (inc[0] && dec[0])
      ? `${esc(inc[0].name)} 领增 ${sgn(num(inc[0].net_chg))}、${esc(dec[0].name)} 领减 ${sgn(num(dec[0].net_chg))}`
      : `头部席位${balance}`;

    // ⑤ 历史回溯：只用连续的主连持仓序列做趋势确认
    // 分层历史采样是按「当前合约」回溯历史排名，换月前后席位规模不可比，故不展示数值对比。
    const sr = tp.series || [];
    const last20 = sr.slice(-20);
    const upDays = last20.filter(p => num(p.chg) > 0).length;
    let streak = 0;
    if (s5 !== 0) {
      const last5 = sr.slice(-5).map(p => num(p.chg));
      for (let i = last5.length - 1; i >= 0 && Math.sign(last5[i]) === s5; i--) streak++;
    }
    const trend = s20 > 0 && s5 < 0 ? "中期增仓趋势回落" : s20 > 0 && s5 > 0 ? "增仓趋势延续"
      : s20 < 0 && s5 < 0 ? "减仓趋势延续" : s20 < 0 && s5 > 0 ? "中期减仓后回补" : "中期方向不明";
    const histNote = `${trend}${streak >= 2 ? `，近 5 日连续 ${streak} 天${s5 > 0 ? "增" : "减"}仓` : ""}`;

    const bigPicture = s5 < 0 && s20 > 0 ? "短期资金流出、中期持仓仍高于 20 日前"
      : s5 < 0 && s20 < 0 ? "短中期同步减仓，趋势转弱"
        : s5 > 0 && s20 > 0 ? "短中期同步增仓，趋势延续"
          : s5 > 0 && s20 < 0 ? "短期资金回流、中期仍偏流出" : "资金面缺乏明确方向";
    const headline = `${bigPicture}；${instAct}、${retAct}，${divWord}，头部席位${balance}。`;
    const state = `${s5 > 0 ? "增仓" : s5 < 0 ? "减仓" : "观望"} · ${diverg ? "分歧" : "同向"}${widening ? "加大" : ""}`;
    const proxyTip = PROXY_NOTE;

    const rows = [
      { no: "①", label: "总持仓变动", sub: "钱在不在", tone: cls(s5),
        metric: `${fmt(latest)} 手｜1日 ${cv(d1)} · 5日 ${cv(d5)}（${pct(p5)}）` +
          (seat && seat.valid
            ? `<span class="seat-hl${seat.stale ? " stale" : ""}" title="${seat.basis === "三榜合一" ? "三榜合一总持仓占比" : "前20榜单总持仓占比"} ${seat.pct.toFixed(2)}%｜多 ${seat.longPct.toFixed(2)}% · 空 ${seat.shortPct.toFixed(2)}%｜${seat.basis} ${seat.count} 席｜分子＝多单榜前20持买 + 空单榜前20持卖，分母＝当日主连总持仓${seat.stale ? "｜⚠ 静态历史快照，非当日数据，不可与三榜口径比较" : `｜分子分母同为 ${fmtDate(cf.as_of)} 当日`}">${seat.basis === "三榜合一" ? "三榜合一占总持仓" : "前20榜单占总持仓"} <b>${seat.pct.toFixed(2)}%</b>${seat.stale ? " <b>⚠静态</b>" : ""}<span class="seat-hl-split">多 ${seat.longPct.toFixed(2)}% · 空 ${seat.shortPct.toFixed(2)}%</span></span>`
            : ""),
        note: moneyNote },
      { no: "②", label: "净持仓分布", sub: "谁在动 · 分档", tone: cls(mover ? mC : 0),
        metric: `净多 ${nPos} / 净空 ${nNeg} 档｜最大变化 ${mover ? esc(tierLabel(mover.tier)) : "—"} ${cv(mC)}`,
        note: tierNote },
      { no: "③", label: "前5席位 vs 其余席位", sub: "席位口径 · 看背离", info: esc(proxyTip), tone: diverg ? "warn" : cls(instChg),
        metric: `前5 ${cv(instNet)} / 其余 ${cv(retNet)}｜Δ ${cv(instChg)} / ${cv(retChg)}`,
        note: divNote,
        chip: diverg ? { text: widening ? "分歧加大" : "背离", kind: widening ? "danger" : "warn" } : null },
      { no: "④", label: "大单动向", sub: "头部席位痕迹", tone: cls(bigNet),
        metric: `前3增 ${cv(i3)} / 前3减 ${cv(d3)}｜净 ${cv(bigNet)}`,
        note: bigNote },
      { no: "⑤", label: "历史回溯", sub: "趋势确认", tone: cls(s20),
        metric: `20日 ${cv(d20)} / 5日 ${cv(d5)}｜近20日 ${upDays}/20 天增仓`,
        note: histNote },
    ];
    return { headline, state, tone: cls(s5), rows, alert };
  }

  // ⑦ 资金动向（AKShare·持仓结构·主力追踪）—— 由 capital_flow 数据驱动；缺失时回退原前20会员
  function renderCapFlow(s, cf) {
    const tp = cf.total_position || {};
    const seat = calcSeatShare(s, cf);   // 三榜合一总持仓占比（数据层，只读，与席位结构同源）
    const tiers = cf.tiers || [];
    const iv = cf.institutional_vs_retail || {};
    const lo = cf.large_orders || {};
    const inst = iv.institutional || {}, ret = iv.retail || {};
    const NOISE = capFlowNoise((s && s.kline && s.kline.rows) || []);
    const sign = (n) => (n > 0 ? "+" : "") + fmt(n);
    const kpi = (label, val, c, sub, hero) =>
      `<div class="netchg-card ${hero ? "hero" : ""}"><div class="k">${label}</div>
        <div class="v ${c || ""}">${val}</div><div class="s">${sub || ""}</div></div>`;

    const seatName = seat.basis === "三榜合一" ? "三榜合一总持仓占比" : "前20榜单总持仓占比";
    const seatTip = seat.basis === "三榜合一"
      ? `口径：分子 = 当日多单榜前 20 名持买合计 + 空单榜前 20 名持卖合计；分母 = 当日主连总持仓（单边），多空分别相除取均值，即 (持买+持卖)/(2×总持仓)。分子与分母同为 ${fmtDate(cf.as_of)} 当日数据。\n「三榜合一」是席位集合口径，不是持仓量相加：三张榜各 20 名按会员简称去重后共 ${seat.count} 席，用于 ⑦「前5席位 / 其余席位」分组；long 只来自多单榜、short 只来自空单榜，只在成交量榜出现的席位 long/short 记 0，并集对分子零增量。故该值是「榜单可观测持仓覆盖率」，不是席位持仓总额。\n旧版「前20榜单总持仓占比」取自一次性导入的静态历史快照，交易日早于当前、当时持仓更高；拿旧分子除当日分母会虚高。两者日期不同，数值不可直接比较；按各自当日分母计算，新口径并不低于旧口径。\n榜单为主力合约、总持仓为主连口径，换月时或有偏差。`
      : `口径：旧快照缺三榜合一席位数据，回退前20会员榜单持买/持卖合计 ÷ 当日主连总持仓（单边），多空两侧分别相除后取均值。\n⚠ 该榜单是一次性导入的静态历史快照，交易日早于当前、持仓水平不同；分子与分母不同日，结果仅供兜底展示，不可与三榜合一新口径或历史数值比较。`;
    const seatKpi = seat.valid ? `<div class="netchg-card hero seat-kpi${seat.stale ? " stale" : ""}">
        <div class="k">${seatName} <span class="cf-i" title="${seatTip}">ⓘ</span></div>
        <div class="v seat-pct">${seat.pct.toFixed(2)}%<span class="seat-split"><span>多 ${seat.longPct.toFixed(2)}%</span><span>空 ${seat.shortPct.toFixed(2)}%</span></span></div>
        <div class="s">${seat.stale ? "⚠ 静态历史快照 · 非当日数据 · 不可比" : `${seat.over ? "口径错位 · " : ""}当日 ${fmtDate(cf.as_of)} · ${seat.basis} ${seat.count} 席 · 分子分母同日`}</div>
      </div>` : "";
    const seatNote = seat.valid
      ? seat.basis === "三榜合一"
        ? `${seatName} =（当日多单榜前 20 持买 + 空单榜前 20 持卖）÷（2 × 当日主连总持仓），多空分别相除取均值；本品种三榜合并去重 ${seat.count} 席用于分组，只在成交量榜出现的席位不贡献分子，故「三榜合一」指席位集合而非持仓量相加。与「前5席位 / 其余席位」同源同日，衡量榜单可观测持仓对全市场的覆盖率；榜单为主力合约、总持仓为主连，换月时口径可能错位。旧版「前20榜单」为静态历史快照，分子分母不同日，不可与当日值直接比较。`
        : `⚠ ${seatName} 回退自静态历史快照，交易日早于当前、分子分母不同日，仅供兜底展示，不可与三榜合一当日口径或历史数值比较。`
      : "暂无榜单席位持仓数据，占比不展示。";

    // 现算席位口径摘要，不沿用旧快照里「机构 / 散户」的措辞
    const summaryText = cf.available
      ? `截至 ${fmtDate(cf.as_of)}，前5席位合计净持仓 ${sign(inst.net)} 手、当日净变化 ${sign(inst.net_chg)} 手；其余 ${ret.count || "—"} 个席位合计净持仓 ${sign(ret.net)} 手、当日净变化 ${sign(ret.net_chg)} 手。${inst.net_chg > 0 && ret.net_chg > 0 ? "两组同向净多增，方向相对一致。" : inst.net_chg < 0 && ret.net_chg < 0 ? "两组同向净空增，方向相对一致。" : "两组当日变化方向相反，分歧加大。"}`
      : (cf.note || "暂无会员持仓排名数据。");

    const kpis = `<div class="netchg">
      ${kpi("总持仓(主连)", (tp.latest != null ? fmt(tp.latest) : "—") + " 手", "", "近20日 " + (tp.chg_20d != null ? sign(tp.chg_20d) : "—"), true)}
      ${seatKpi}
      ${kpi("总持仓变化(1日)", (tp.chg_1d != null ? sign(tp.chg_1d) : "—") + " 手", cls(tp.chg_1d), tp.chg_1d > 0 ? "资金流入 ↑" : "资金流出 ↓")}
      ${kpi("总持仓变化(5日)", (tp.chg_5d != null ? sign(tp.chg_5d) : "—") + " 手", cls(tp.chg_5d), tp.chg_5d > 0 ? "5日净流入" : "5日净流出")}
      ${kpi("前5席位净持仓", sign(inst.net) + " 手", cls(inst.net), "按 |净持仓| 排名前 5", true)}
      ${kpi("前5席位净变化", sign(inst.net_chg) + " 手", cls(inst.net_chg), inst.net_chg > 0 ? "前5净多增仓" : "前5净空增仓")}
      ${kpi("其余席位净变化", sign(ret.net_chg) + " 手", cls(ret.net_chg), ret.net_chg > 0 ? "其余净多增仓" : "其余净空增仓")}
    </div>`;

    const tierRows = tiers.map(t => `<tr>
      <td>${tierLabel(t.tier)}</td>
      <td class="${cls(t.net)}">${sign(t.net)}</td>
      <td class="${cls(t.net_chg)}">${sign(t.net_chg)}</td>
      <td>${fmt(t.long)}</td><td>${fmt(t.short)}</td><td>${t.count}</td>
    </tr>`).join("");

    const listItems = (arr, key, valCls) => (arr && arr.length
      ? arr.map(m => `<div class="kv"><span class="k">${m.name}</span><span class="v ${valCls(m)}">${(key === "net_chg" ? sign(m.net_chg) : fmt(m[key]))}</span></div>`).join("")
      : '<div class="note">—</div>');

    const rd = capFlowRead(cf, NOISE, seat);
    const readRows = rd.rows.map(r => `<div class="cfread-row">
      <div class="n ${r.tone}">${r.no}</div>
      <div class="k"><div class="kh"><b>${r.label}</b>${r.info ? `<span class="cf-i" title="${r.info}">ⓘ</span>` : ""}</div><span>${r.sub}</span></div>
      <div class="m">${r.metric}</div>
      <div class="t">${r.note}${r.chip ? `<span class="cfread-chip ${r.chip.kind}">${r.chip.text}</span>` : ""}</div>
    </div>`).join("");
    const alertHtml = rd.alert
      ? `<div class="alert-banner ${rd.alert.level}"><div class="ic">⚠</div><div><span class="ttl">高亮预警 · ${rd.alert.type} 背离${rd.alert.level === "danger" ? "（分歧加大）" : ""}</span><br>${rd.alert.desc}</div></div>`
      : "";
    const capread = `<div class="capread">
      <div class="capread-h"><span class="lbl">速读卡片</span><span class="prod">${cf.main_contract || cf.contract || ""} · 资金五维速读</span><span class="cfread-state ${rd.tone}">${rd.state}</span></div>
      <div class="conclusion">${rd.headline}</div>
      ${alertHtml}
      <div class="cfread-rows">${readRows}</div>
      <div class="cfread-foot">注：${PROXY_NOTE} 四档分档仅取排名前 20 席位，③ 的「其余席位」含第 21 名及以后；净持仓＝当日榜单存量，Δ＝席位当日流量；榜单每日重排，勿倒推昨日值；历史分层采样换月前后不可比，仅作方向参考。</div>
    </div>`;

    // ===== 专业资金分析决策面板（L1→L4）=====
    const A = capFlowAnalytics(s, cf, NOISE);
    const q = A.pv.quad;
    const NZ = A.noise, NB = NZ.band;
    const barColor = A.score.tone === "up" ? "var(--red)" : A.score.tone === "down" ? "var(--green)" : "var(--amber)";
    const scoreHtml = `<div class="cf-score">
      <div class="cf-score-main">
        <div class="lbl">资金动向综合读数 · 四维共振</div>
        <div class="num ${A.score.tone}">${A.score.value}<small>/100</small></div>
        <div class="cf-tag ${A.score.tone === "up" ? "active" : A.score.tone === "down" ? "passive" : "flat"}">${A.score.label}</div>
      </div>
      <div class="cf-score-side">
        <div class="cf-bar"><i style="width:${A.score.value}%;background:${barColor}"></i><span class="mid"></span></div>
        <div class="cf-bar-lbl"><span>强偏空</span><span>中性</span><span>强偏多</span></div>
        <div class="cf-confirm">共振确认度 <b>${A.score.confirm}/${A.score.total}</b> 维同向${A.score.confirm >= 3 ? " · 信号一致" : " · 维度分歧，谨慎解读"}</div>
        <div class="cf-note">启发式读数，非行情预测。由「价·量·仓配合 35% / 前5席位净变化 25% / 分层一致性 20% / 大单动向 20%」加权合成；成交量作为价仓信号的确认系数（放量增仓增强、缩量或放量减仓削弱），不单独产生方向。仅供品种间横向比较，不构成交易信号。</div>
      </div>
    </div>`;

    // 悬停/聚焦口径说明：属性内换行写成 &#10;，由 CSS white-space:pre-line 还原
    const tipAttr = (t) => String(t || "").replace(/&/g, "&amp;").replace(/"/g, "&quot;")
      .replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\n/g, "&#10;");
    const VP = A.volume.profile, VV = A.volume.verdict;
    const volTip = `【量能卡片 · 四格数据口径】
每格三行 = 指标名 / 当前值 / 基准或状态说明。
① 当日成交量：最新交易日主连成交量（手）；小字「前20日均量」不含当日，是所有量比的共同基准。
② 量比(20日) = 当日成交量 ÷ 前20日均量。≥1.5× 显著放量、≥1.2× 温和放量、0.8~1.2× 之间（不含端点）量能平稳、≤0.8× 温和缩量、≤0.6× 明显缩量。卡片标题由这一格决定。
③ 量能趋势 = 近5日均量 ÷ 前5日均量（前5日 = 再往前 5 天），衡量资金关注度：≥1.2× 升温、≤0.8× 降温，其余平稳。
④ 均量结构 VMA5/20 = 近5日均量 ÷ 近20日均量；>1 说明近期量能高于中期水平，小字标注 VMA5 走高 / 走低及绝对值。小字「走高/走低」是拿近5日与前5日比，和 VMA5/20（近20日）参照不同，出现「>1 但走低」并不矛盾。
量能只做确认、不单独定方向：按 0.7~1.3 的系数增强或削弱价仓四象限信号。
「量能平稳」= 当日量比落在 0.8~1.2×，成交未异常放大或萎缩，价仓信号按常规强度解读；这不等于行情没方向，方向请回到上方四象限看价与仓。`;
    const pvRow = (st, label) => !st ? "" : `<tr>
      <td>${label}</td><td>${fmt(st.close, 2)}</td>
      <td class="${cls(st.dPrice)}">${pct(st.dPrice)}</td>
      <td>${fmt(st.oi)}</td>
      <td class="${cls(st.dOi)}">${sign(st.dOi)}</td>
      <td class="${cls(st.dOiPct)}">${pct(st.dOiPct)}</td>
      <td>${fmt(st.volume)}</td>
      <td class="${st.volRatio >= 1.2 ? "up" : st.volRatio <= 0.8 ? "down" : "muted"}">${st.volRatio.toFixed(2)}×</td>
      <td><span class="cf-tag ${st.volRatio >= 1.2 ? "active" : st.volRatio <= 0.8 ? "passive" : "dual"}">${st.volState}</span></td></tr>`;
    const volCard = `<div class="cf-vol ${VV.tone}">
      <div class="v-head"><span class="v-name">量 · ${VV.tag}</span>
        <span class="cf-vtip" tabindex="0" data-tip="${tipAttr(volTip)}">ⓘ</span>
        <span class="cf-tag ${VV.confirm > 0 ? "active" : VV.confirm < 0 ? "passive" : "dual"}">${VV.confirm > 0 ? "量能确认价仓信号" : VV.confirm < 0 ? "量能削弱价仓信号" : "量能中性"}</span></div>
      <div class="v-desc">${VV.desc}</div>
      <div class="v-grid">
        <div title="最新交易日主连成交量（手）；下方小字为前 20 个交易日（不含当日）均量，是量比的分母"><div class="k">当日成交量</div><div class="v">${fmt(VP.last)} <small>手</small></div><div class="s">前20日均量 ${fmt(VP.base20)}</div></div>
        <div title="当日成交量 ÷ 前20日均量；0.8~1.2× 判「量能平稳」，≥1.2× 放量、≤0.8× 缩量"><div class="k">量比 (20日)</div><div class="v ${VP.ratio >= 1.2 ? "up" : VP.ratio <= 0.8 ? "down" : ""}">${VP.ratio.toFixed(2)}×</div><div class="s">${VP.state}</div></div>
        <div title="近5日均量 ÷ 前5日均量（再往前 5 天）；≥1.2× 关注度升温、≤0.8× 降温"><div class="k">量能趋势</div><div class="v">${VP.trend.toFixed(2)}×</div><div class="s">${VP.trendTag}</div></div>
        <div title="VMA5 ÷ VMA20；>1 表示近期量能高于中期水平，小字标注 VMA5 绝对值与方向"><div class="k">均量结构 VMA5/20</div><div class="v">${VP.ma20 ? (VP.ma5 / VP.ma20).toFixed(2) : "—"}×</div><div class="s">${VP.maTag} · VMA5 ${fmt(VP.ma5)}</div></div>
      </div>
      <div class="cf-vol-note">每格三行 = <b>指标名 / 数值 / 基准或状态</b>。量比看当日（0.8~1.2× 即「量能平稳」）· 量能趋势看近5日 vs 前5日的关注度 · VMA5/20 看中期均量结构；量能只作 0.7~1.3 的确认系数，不单独定方向。ⓘ 可看完整口径。</div>
      ${VP.divergence ? `<div class="v-warn">⚠ ${VP.divergence}</div>` : ""}
    </div>`;
    const NP = NZ.profile;
    const noiseTip = `农产品波动小、有色/化工波动大，统一阈值会让低波品种把噪音当信号、高波品种把真信号磨平。\n算法：近 60 日主连「日收益率 / 日持仓变化率」的稳健波动率（MAD×1.4826，抗涨跌停与移仓跳空）÷ 全市场基准波动率（价 ${NOISE_REF.px.toFixed(2)}%/日、仓 ${NOISE_REF.oi.toFixed(2)}%/日）得到倍数，裁剪到 ${NOISE_REF.min}~${NOISE_REF.max} 倍；\n噪音带 = 基准（近5日 价 ±0.35% / 仓 ±0.8%）× 倍数 × √(观察周期/5)。\n只有变化超过噪音带才判定「价涨/价跌、仓增/仓减」，否则记为「平」。σ = 当前变化 ÷ 本品种同期波动标准差，绝对值越大信号越强。`;
    const noiseHtml = `<div class="cf-noise">
      <div class="cf-noise-h">波动率自适应噪音带<span class="cf-i" title="${noiseTip}">ⓘ</span>
        <span class="cf-nchip ${NP.regime === "低波" ? "low" : NP.regime === "高波" ? "high" : "mid"}">${NP.regime}品种</span>
        <em>农产品更严 · 有色/化工更宽</em></div>
      <div class="cf-noise-grid">
        <div><span class="k">60日稳健日波动</span><b>价 ${NP.sigmaPx.toFixed(2)}% / 仓 ${NP.sigmaOi.toFixed(2)}%</b></div>
        <div><span class="k">相对全市场基准</span><b>价 ×${NP.ratioPx.toFixed(2)} / 仓 ×${NP.ratioOi.toFixed(2)}</b></div>
        <div><span class="k">本品种噪音带 · 近${NZ.horizon}日</span><b>价 ±${NB.px.toFixed(2)}% / 仓 ±${NB.oi.toFixed(2)}%</b></div>
        <div><span class="k">当前变化 / σ</span><b>价 ${NZ.z.px >= 0 ? "+" : ""}${NZ.z.px.toFixed(2)}σ · 仓 ${NZ.z.oi >= 0 ? "+" : ""}${NZ.z.oi.toFixed(2)}σ</b></div>
      </div>
      <div class="cf-noise-note">基准 ±0.35% / ±0.8% 对应「中位波动」品种；本品种按上式自动放缩。超过噪音带才算有方向，否则判为「平」——这就是同一组涨跌幅在低波与高波品种上结论不同的原因。⑥ 全市场清单为避免 41 品种噪音，采用更严格基准 ±0.8% / ±1.0%，<b>波动率倍数与本卡完全相同</b>。</div>
    </div>`;
    const quadHtml = `<div class="cf-block">
      <div class="cf-sec"><span class="no">L1</span>价 · 量 · 仓配合<em>资金是否真正在推动价格</em></div>
      <div class="cf-quad ${q.bias > 0 ? "pos" : q.bias < 0 ? "neg" : "neu"}">
        <div class="q-name">${q.name}</div><div class="q-desc">${q.desc}</div>
      </div>
      ${noiseHtml}
      ${volCard}
      <div class="table-wrap"><table class="cf-pv-table"><thead><tr>
        <th>周期</th><th>收盘</th><th>价格变化</th><th>总持仓</th><th>持仓变化</th><th>持仓变化%</th><th>成交量(手)</th><th>量比</th><th>量能状态</th>
      </tr></thead><tbody>${pvRow(A.pv.pv1, "近1日")}${pvRow(A.pv.pv5, "近5日")}${pvRow(A.pv.pv20, "近20日")}</tbody></table></div>
      <div class="cf-hint">表格三行 = 近 <b>1 / 5 / 20 个交易日</b>，均以<b>区间首日</b>的收盘价与总持仓为基准算累计变化；「量比」列 = 区间末日成交量 ÷ 该区间之前的均量（1日=昨日量、5日=前5日均量、20日=前20日均量），故<b>近20日量比与卡片量比同值</b>，卡片口径固定为「当日量 ÷ 前20日均量」。价仓四象限以近 ${NZ.horizon} 日为主判据，噪音带已按本品种波动率调整为 价 ±${NB.px.toFixed(2)}% / 仓 ±${NB.oi.toFixed(2)}%（基准 ±0.35% / ±0.8%）；三联图的「价仓背离日」沿用同一噪音带逐日标注，悬停标题 ⓘ 可看完整算法。</div>
      <div class="cf-chart-h"><span>价 · 量 · 仓 三联图（近 60 交易日）</span><em>上格 收盘价×总持仓双轴 · 下格 成交量与 VMA5/VMA20</em></div>
      <div id="chCapFlowPriceOi" class="chart" style="height:300px;margin-top:4px"></div>
      <div class="cf-hint">成交量柱按当日涨跌红 / 绿着色，白边为显著放量（≥1.5×）；VMA5 高于 VMA20 表示近期资金关注度升温。三要素读法：<b>放量+增仓 = 增量资金进场</b>（趋势最可靠）、<b>放量+减仓 = 集中平仓/回补</b>（加速或尾声）、<b>缩量+增仓 = 轻仓试探</b>（待确认）、<b>缩量+减仓 = 离场尾声</b>（动能衰竭）。上下两格可同步缩放，悬停查看当日价·量·仓读数。</div>
    </div>`;

    const chgTip = (r) => `多头增减 ${sign(r.long_chg)} 手 / 空头增减 ${sign(r.short_chg)} 手｜当日净变化 = 多头增减 − 空头增减 = ${sign(r.net_chg)} 手（席位相对上一交易日的自身持仓变化）`;
    const matrixRows = A.structure.rows.map(r => `<tr>
      <td><b>${tierLabel(r.tier)}</b><span class="cf-sub">${r.count} 席 · 权重 ${r.weight.toFixed(0)}%</span></td>
      <td>${fmt(r.gross)}</td>
      <td class="${cls(r.net)}" title="净持仓 = 上榜多单量 ${fmt(r.long)} − 上榜空单量 ${fmt(r.short)}（当日存量）">${sign(r.net)}</td>
      <td class="${cls(r.netRatio)}">${r.netRatio.toFixed(1)}%</td>
      <td>${r.lsRatio ? r.lsRatio.toFixed(2) : "—"}</td>
      <td class="${cls(r.net_chg)}" title="${chgTip(r)}">${sign(r.net_chg)}</td>
      <td class="${cls(r.chgRatio)}" title="变化强度 = 当日净变化 ${sign(r.net_chg)} ÷ 双边持仓 ${fmt(r.gross)}（分母为毛持仓）">${r.chgRatio.toFixed(1)}%</td>
      <td><span class="cf-tag ${r.action.kind}" title="${chgTip(r)}">${r.action.tag}</span></td>
    </tr>`).join("");
    const legendRows = [
      { k: "active", t: "多头主动进攻", d: "多单增、空单不增 → 新多进场" },
      { k: "active", t: "空头主动进攻", d: "空单增、多单不增 → 新空进场" },
      { k: "passive", t: "空头回补主导", d: "多空双减、空减更多 → 净多增（被动）" },
      { k: "passive", t: "多头平仓主导", d: "多空双减、多减更多 → 净空增（被动）" },
      { k: "dual", t: "双向增仓·多/空占优", d: "多空同增，谁增得多谁占优 → 分歧对峙" },
      { k: "dual", t: "净多/净空微增", d: "一增一减但幅度很小 → 信号弱" },
      { k: "", t: "基本持平", d: "多空增减都近 0 → 无明显动作" },
    ];
    const legendHtml = `<div class="cf-legend">
      <div class="cf-legend-h">变化性质速读<em>净变化 = 多头增减 − 空头增减；<b class="r">红</b> 新资金进场 · <b class="g">绿</b> 存量离场 · <b class="a">琥珀</b> 双向/微弱</em></div>
      <div class="cf-legend-grid">${legendRows.map(x =>
        `<div class="cf-lrow"><span class="cf-tag ${x.k}">${x.t}</span><span>${x.d}</span></div>`).join("")}</div>
      <div class="cf-legend-note"><b>关键</b>：同为净多增加，「多头主动进攻」是新多进场，「空头回补主导」只是空头撤退——数值相同，趋势可靠性完全不同。</div>
    </div>`;
    const matrixHtml = `<div class="cf-block">
      <div class="cf-sec"><span class="no">L2</span>资金结构矩阵<em>归一化后可跨品种比较</em></div>
      <div class="table-wrap"><table><thead><tr>
        <th>席位分档</th><th>双边持仓</th>
        <th class="cf-th" title="当日存量：该层席位「上榜多单量 − 上榜空单量」。多空为两张独立榜单，未上榜一侧记 0，故为榜单口径近似">净持仓 ⓘ</th>
        <th>净占比</th><th>多空比</th>
        <th class="cf-th" title="当日流量：该层席位「多头增减 − 空头增减」之和。不是两日榜单净持仓之差，悬停数值可看多空拆解">当日净变化 ⓘ</th>
        <th>变化强度</th><th>变化性质</th>
      </tr></thead><tbody>${matrixRows}</tbody></table></div>
      ${legendHtml}
      <div class="cf-hint"><b>存量 ≠ 流量</b>：「净持仓」是当日榜单存量，「当日净变化」是席位自身增减。榜单每日重排分层、席位有进出，两者一般不满足「昨日 + 变化 = 今日」，<b>勿据此倒推昨日值</b>；悬停数值可看多空拆解。<br>净占比 = 净持仓 ÷ 双边持仓；变化强度 = 当日净变化 ÷ 双边持仓（<b>分母是毛持仓，不是净持仓</b>）。头部资金层持仓占比 <b>${A.structure.concentration.toFixed(1)}%</b>（集中度越高，少数席位调仓越易放大波动）。</div>
    </div>`;

    const momRow = (m) => `<div class="cf-mrow">
      <span class="nm">${m.name}</span>
      <span class="cf-tag ${m.action.kind}">${m.action.tag}</span>
      <span class="v ${cls(m.net_chg)}">${sign(m.net_chg)}</span></div>`;
    const momentumHtml = `<div class="cf-block">
      <div class="cf-sec"><span class="no">L3</span>主力席位动能<em>谁在主动进攻，谁在回补离场</em></div>
      <div class="flex">
        <div style="flex:1"><div class="cf-mini-h up">净多增加 TOP</div>${A.momentum.leaders.map(momRow).join("") || '<div class="note">—</div>'}</div>
        <div style="flex:1"><div class="cf-mini-h down">净空增加 TOP</div>${A.momentum.laggards.map(momRow).join("") || '<div class="note">—</div>'}</div>
      </div>
      <div id="chCapFlowMomentum" class="chart" style="height:230px;margin-top:8px"></div>
    </div>`;

    // 悬停/聚焦口径说明：属性内换行写成 &#10;，由 CSS white-space:pre-line 还原（tipAttr 已在上方定义）
    const evidenceLi = (p) => `<li class="cf-ev" tabindex="0" data-tip="${tipAttr(p.tip)}">${p.k}：${p.txt}<span class="cf-i">ⓘ</span></li>`;
    const scoreTip = `【四维共振读数】\n${A.score.rule}\n权重：价·量·仓配合 35% / 前5席位净变化 25% / 分层一致性 20% / 大单动向 20%。\n「共振确认度」= 与综合读数同向的维数；≥3 说明信号一致，≤2 说明维度分歧，需谨慎解读。\n悬停下方每条证据可看该维口径与本品种实况。启发式读数，仅供品种间横向比较，不构成交易信号。`;
    const agPart = A.score.parts.find(p => p.k === "分层一致性");
    const upN = A.structure.rows.filter(r => r.net_chg > 0).length;
    const dnN = A.structure.rows.filter(r => r.net_chg < 0).length;
    const agAct = agPart.v === 0
      ? "该维弃权（20% 权重不计方向），分歧时回 L1 确认"
      : `该维计入读数 ${agPart.v * agPart.w > 0 ? "+" : ""}${(agPart.v * agPart.w).toFixed(2)}`;
    const footHtml = `<div class="cf-foot">注：分层一致性只数同向层数、不比金额；本品种净多增 ${upN} 层 / 净空增 ${dnN} 层，${agAct}。其余口径悬停或点击各条查看。</div>`;
    const verdictHtml = `<div class="cf-block">
      <div class="cf-sec"><span class="no">L4</span>结论与验证清单<em>先证据，后结论</em></div>
      <div class="cf-evidence">
        <div><div class="h cf-ev-h" tabindex="0" data-tip="${tipAttr(scoreTip)}">四维证据 <span class="cf-i">ⓘ</span></div><ul>${A.score.parts.map(evidenceLi).join("")}</ul></div>
        <div><div class="h">风险与口径提示</div><ul>${A.score.risks.map(x => `<li>${x}</li>`).join("")}</ul></div>
      </div>
      ${footHtml}
    </div>`;

    const panelHtml = `<div class="cf-panel">
      <div class="cf-panel-h"><span class="ttl">专业资金分析框架</span><span class="sub">四维交叉验证 · 变化优先于绝对量 · 历史回溯</span></div>
      ${scoreHtml}${quadHtml}${matrixHtml}${momentumHtml}${verdictHtml}
    </div>`;

    const body = `${capread}
      ${panelHtml}
      ${kpis}
      <div class="verdict">${summaryText}</div>
      <div class="flex">
        <div style="flex:1.2">
          <div class="t20-hist-head"><span class="t">净持仓分布 · 按 |净持仓| 名次分档（前5 / 6–10 / 11–15 / 16–20 名）</span></div>
          <div id="chCapFlowTier" class="chart" style="height:230px"></div>
          <div class="table-wrap" style="margin-top:6px">
            <table><thead><tr><th>席位名次分档</th><th>净持仓</th><th>当日净变化</th><th>持买</th><th>持卖</th><th>席位</th></tr></thead>
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
          <div class="t20-hist-head"><span class="t">大单动向 · 头部席位操作痕迹</span></div>
          <div class="note">净持仓变化 = 多头增仓 − 空头增仓；正值=净多增加（偏多），负值=净空增加（偏空）。</div>
          <div class="flex">
            <div style="flex:1"><div class="k">净多增仓主力</div>${listItems(lo.top_net_increase, "net_chg", m => cls(m.net_chg))}</div>
            <div style="flex:1"><div class="k">净空增仓主力</div>${listItems(lo.top_net_decrease, "net_chg", m => cls(m.net_chg))}</div>
            <div style="flex:1"><div class="k">成交量领先席位</div>${listItems(lo.volume_leaders, "volume", () => "")}</div>
          </div>
        </div>
        <div style="flex:1.2">
          <div class="t20-hist-head"><span class="t">历史回溯 · 各名次分档净持仓变化</span></div>
          <div id="chCapFlowHist" class="chart" style="height:220px"></div>
        </div>
      </div>
      <div class="flex" style="margin-top:10px">
        <div style="flex:1">
          <div class="t20-hist-head"><span class="t">三榜合一近1月：合计净持仓 + 占总持仓占比（左轴手数 · 右轴占比）</span></div>
          <div id="chCapFlowT20Hist" class="chart" style="height:260px"></div>
          <div id="capFlowT20Verdict" class="verdict" style="margin-top:4px"></div>
          <div class="note" style="margin-top:4px">口径：席位集合 = 多单 / 空单 / 成交量三榜合并去重；合计净持仓 = 多单榜前20持买合计 − 空单榜前20持卖合计（只在成交量榜出现的席位不计持仓，对分子零增量）。左轴为合计净持仓（手）；右轴两条占比共用：① <b>三榜合一占总持仓</b> =（持买 + 持卖）/（2 × 当日主连总持仓），即速读卡「三榜合一总持仓占比」的逐日展开，衡量三榜席位对全市场持仓的覆盖率；② <b>净持仓占总持仓</b> = 合计净持仓 / 当日主连总持仓，正负表示三榜席位整体净多 / 净空，剔除合约规模与换月带来的量级变化，跨日可比。两条占比的分子分母严格同日（分母取主连日线当日 hold），换月错位时占比可能 &gt;100%，仅作参考。曲线覆盖全部当日有资金数据的品种（PD / PT 无资金数据；当日榜单源缺失的品种会显式回退旧口径）。</div>
        </div>
      </div>
      <div class="note" style="margin-top:8px">数据来源：${cf.source}。${PROXY_NOTE} 主力合约 ${cf.main_contract || cf.contract}；数据交易日 ${cf.as_of}。总持仓为主连口径，合约换月时或有跳变。${seatNote}</div>`;

    const tone = inst.net_chg > 0 ? "前5席位净多增仓" : (inst.net_chg < 0 ? "前5席位净空增仓" : "中性");
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
      // 专业框架 L1：价 · 量 · 仓 三联图（上格价×仓，下格成交量与均量线）
      // 背离日标记沿用 L1 四象限的同一条「波动率自适应噪音带」
      if (s.kline && s.kline.rows && s.kline.rows.length > 5) {
        const nb = capFlowNoise(s.kline.rows).eps(5, 0.35, 0.8);
        Charts.priceVolumeOi($("#chCapFlowPriceOi"), s.kline.rows,
          { pxEps: nb.px, oiEps: nb.oi });
      }
      // 专业框架 L3：主力席位净变化横条（主动进攻 / 回补离场分色）
      if (s.capital_flow.top_members && s.capital_flow.top_members.length)
        Charts.memberMomentum($("#chCapFlowMomentum"), s.capital_flow.top_members);
      // ⑦ 资金动向（AKShare）：分层净持仓组合图 / 总持仓历史 / 各层级净持仓历史
      if (s.capital_flow.tiers && s.capital_flow.tiers.length)
        Charts.capitalFlowTier($("#chCapFlowTier"), s.capital_flow.tiers);
      if (s.capital_flow.total_position && s.capital_flow.total_position.series)
        Charts.totalPositionCurve($("#chCapFlowTotal"), s.capital_flow.total_position.series);
      if (s.capital_flow.tier_net_history && s.capital_flow.tier_net_history.length >= 2)
        Charts.tierNetHistory($("#chCapFlowHist"), s.capital_flow.tier_net_history);
      // 三榜合一近1月合计净持仓：优先 capital_flow.net_history（全部有资金数据品种），
      // 旧 top20.history 仅在缺字段时回退（口径不同，结论里必须显式标注）
      const cfh = s.capital_flow.net_history;
      const hasNew = cfh && cfh.length >= 2;
      const oldHist = s.top20 && s.top20.history;
      const hasOld = oldHist && oldHist.length >= 2;
      if (hasNew || hasOld) {
        const h = (hasNew ? cfh : oldHist).slice().sort((a, b) => (a.date < b.date ? -1 : 1));
        Charts.capflowNetCurve($("#chCapFlowT20Hist"), h, { legacy: !hasNew });
        const last = h[h.length - 1], first = h[0];
        const chg = last.net - first.net;
        const seatTxt = last.seat_count ? `（三榜合并 ${last.seat_count} 席）` : "";
        const srcTxt = hasNew ? "三榜合一合计净持仓" : "前20会员合计净持仓（旧口径回退）";
        // 占比：期末值 + 近1月百分点变化（pp）。分母缺失的旧口径回退不显示。
        const pp = (a, b) => {
          if (typeof a !== "number" || typeof b !== "number") return "—";
          const d = Math.abs(a - b) < 0.005 ? 0 : a - b;
          return `<b class="${cls(d)}">${d > 0 ? "+" : ""}${d.toFixed(2)}pp</b>`;
        };
        const hasShare = hasNew && typeof last.seat_share === "number" && typeof last.net_share === "number";
        const shareTxt = hasShare
          ? ` ｜ 三榜合一占总持仓 <b>${last.seat_share.toFixed(2)}%</b>（近1月 ${pp(last.seat_share, first.seat_share)}）` +
            ` ｜ 净持仓占总持仓 <b class="${cls(last.net_share)}">${last.net_share > 0 ? "+" : ""}${last.net_share.toFixed(2)}%</b>（近1月 ${pp(last.net_share, first.net_share)}）`
          : "";
        const v = document.getElementById("capFlowT20Verdict");
        if (v) v.innerHTML = `期末${srcTxt} <b>${fmt(last.net)}</b> 手${seatTxt}，近1月变化 <b class="${cls(chg)}">${chg > 0 ? "+" : ""}${fmt(chg)}</b> 手` +
          shareTxt +
          (hasNew ? "" : ` <span class="note">（该品种缺三榜合一历史序列，暂用旧前20会员全合约曲线，无逐日总持仓分母，故不显示两条占比线）</span>`);
      } else {
        const v = document.getElementById("capFlowT20Verdict");
        if (v) v.innerHTML = `<span class="note">该品种暂无近1月三榜合一历史序列（PD / PT 无资金数据）。</span>`;
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
