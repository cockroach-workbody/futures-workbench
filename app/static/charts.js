/* 期货工作台 —— ECharts 图表封装（国内习惯：涨红跌绿，暗色主题） */
(function (global) {
  const AX = "#2a3543", TX = "#8b9bb0", TXT = "#e6edf3";
  const RED = "#e8483f", GREEN = "#1fab6b", BLUE = "#3d7eff", AMBER = "#e0a83a";
  const baseGrid = { left: 56, right: 16, top: 30, bottom: 40 };

  function base(dom) {
    const c = echarts.init(dom, null, { renderer: "canvas" });
    return c;
  }
  function axisCommon() {
    return {
      axisLine: { lineStyle: { color: AX } },
      axisLabel: { color: TX },
      splitLine: { lineStyle: { color: "rgba(42,53,67,.5)" } },
    };
  }

  // ① K 线 + 均线 + 缠论笔/中枢叠加
  function kline(dom, rows, ma5, ma10, chanlun) {
    const c = base(dom);
    const dates = rows.map(r => r.date);
    const ohlc = rows.map(r => [r.open, r.close, r.low, r.high]);
    const up = rows.map(r => r.close >= r.open ? 1 : -1);
    const N = rows.length;
    const start1y = Math.max(0, 100 - 252 / N * 100); // 默认最近 1 年

    // 缠论笔：每段用独立折线绘制（以 null 断开）
    let biSeries = null;
    if (chanlun && chanlun.bis && chanlun.bis.length) {
      const pts = [];
      chanlun.bis.forEach(b => {
        const s = rows[b.start_idx], e = rows[b.end_idx];
        if (s && e) {
          pts.push([s.date, +b.start]);
          pts.push([e.date, +b.end]);
          pts.push([null, null]);
        }
      });
      biSeries = { type: "line", name: "笔", data: pts, showSymbol: false,
        lineStyle: { width: 1.6, color: AMBER, type: "dashed" }, tooltip: { show: false },
        z: 5, connectNulls: false };
    }
    // 中枢：矩形区间（markArea），字段兼容多种引擎输出
    let centerArea = null;
    if (chanlun && chanlun.centers && chanlun.centers.length) {
      centerArea = chanlun.centers.map(ct => {
        const si = ct.start_idx != null ? ct.start_idx : ct.bi_span && ct.bi_span[0];
        const ei = ct.end_idx != null ? ct.end_idx : ct.bi_span && ct.bi_span[1];
        const s = rows[si], e = rows[ei];
        const hi = ct.zg != null ? ct.zg : (ct.high != null ? ct.high : ct.zd);
        const lo = ct.zd != null ? ct.zd : (ct.low != null ? ct.low : ct.zg);
        if (!s || !e) return null;
        return [{ xAxis: s.date, yAxis: lo }, { xAxis: e.date, yAxis: hi }];
      }).filter(Boolean);
    }

    const series = [
      {
        type: "candlestick", name: "K", data: ohlc,
        itemStyle: { color: RED, color0: GREEN, borderColor: RED, borderColor0: GREEN },
        markArea: centerArea ? {
          silent: true, itemStyle: { color: "rgba(224,168,58,.10)", borderColor: "rgba(224,168,58,.4)" },
          data: centerArea,
        } : undefined,
      },
      { type: "line", name: "MA5", data: ma5, smooth: true, showSymbol: false, lineStyle: { width: 1, color: AMBER } },
      { type: "line", name: "MA10", data: ma10, smooth: true, showSymbol: false, lineStyle: { width: 1, color: BLUE } },
    ];
    if (biSeries) series.push(biSeries);

    c.setOption({
      backgroundColor: "transparent",
      legend: { data: ["MA5", "MA10", "笔"].slice(0, biSeries ? 3 : 2), textStyle: { color: TX }, top: 2, right: 8 },
      tooltip: { trigger: "axis", axisPointer: { type: "cross" }, backgroundColor: "#1c2530", borderColor: AX, textStyle: { color: TXT } },
      grid: { ...baseGrid, right: 16, bottom: 48 },
      xAxis: { type: "category", data: dates, ...axisCommon(), boundaryGap: true },
      yAxis: { scale: true, ...axisCommon() },
      dataZoom: [
        { type: "inside", start: start1y, end: 100 },
        { type: "slider", start: start1y, end: 100, height: 14, bottom: 6, borderColor: AX,
          fillerColor: "rgba(61,126,255,.15)", handleStyle: { color: BLUE },
          moveHandleStyle: { color: BLUE }, textStyle: { color: TX },
          dataBackground: { lineStyle: { color: AX }, areaStyle: { color: "rgba(61,126,255,.10)" } },
          selectedDataBackground: { lineStyle: { color: BLUE }, areaStyle: { color: "rgba(61,126,255,.20)" } } },
      ],
      series: series,
    });
    return c;
  }

  // ⑥ 蝴蝶图：左多(负) 右空(正)
  function butterfly(dom, members) {
    const c = base(dom);
    const top = members.slice(0, 12);
    const names = top.map(m => m.name);
    const longs = top.map(m => -(m.long || 0));
    const shorts = top.map(m => (m.short || 0));
    c.setOption({
      backgroundColor: "transparent",
      tooltip: { trigger: "axis", backgroundColor: "#1c2530", borderColor: AX, textStyle: { color: TXT } },
      legend: { data: ["持买", "持卖"], textStyle: { color: TX }, top: 2 },
      grid: { left: 70, right: 16, top: 30, bottom: 30 },
      xAxis: { type: "value", ...axisCommon() },
      yAxis: { type: "category", data: names, ...axisCommon(), inverse: true },
      series: [
        { name: "持买", type: "bar", data: longs, itemStyle: { color: RED }, label: { show: true, position: "left", color: TX, formatter: p => Math.abs(p.value) } },
        { name: "持卖", type: "bar", data: shorts, itemStyle: { color: GREEN }, label: { show: true, position: "right", color: TX } },
      ],
    });
    return c;
  }

  // ⑥ 净持仓条形（按会员）
  function netBars(dom, members) {
    const c = base(dom);
    const top = members.slice().sort((a, b) => Math.abs(b.net) - Math.abs(a.net)).slice(0, 12).reverse();
    c.setOption({
      backgroundColor: "transparent",
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, backgroundColor: "#1c2530", borderColor: AX, textStyle: { color: TXT } },
      grid: { left: 70, right: 16, top: 16, bottom: 30 },
      xAxis: { type: "value", ...axisCommon() },
      yAxis: { type: "category", data: top.map(m => m.name), ...axisCommon() },
      series: [{
        type: "bar", data: top.map(m => ({ value: m.net, itemStyle: { color: m.net >= 0 ? RED : GREEN } })),
        label: { show: true, position: "right", color: TX },
      }],
    });
    return c;
  }

  // ② 库存（3 柱：标准仓单 / 社会库存 / 厂库）+ 较上期变化（醒目）
  function invFmt(n) {
    if (n == null) return "—";
    const a = Math.abs(n);
    if (a >= 100000000) return (n / 100000000).toFixed(n % 100000000 === 0 ? 0 : 2) + "亿";
    if (a >= 10000) return (n / 10000).toFixed(a % 10000 === 0 ? 0 : 1) + "万";
    return Number(n).toLocaleString("zh-CN", { maximumFractionDigits: 1 });
  }
  function inventory(dom, inv) {
    const c = base(dom);
    const segs = [
      { name: "标准仓单", sg: inv.standard, color: BLUE },
      { name: "社会库存", sg: inv.social, color: AMBER },
      { name: "厂库", sg: inv.mill, color: GREEN },
    ];
    // 统一折算到「吨」便于柱状对比（手/张为合约单位，无重量的按原值展示）
    const UNIT2T = { "吨": 1, "万吨": 10000, "千克": 0.001, "公斤": 0.001 };
    const cats = segs.map(s => s.name);
    const data = segs.map(s => {
      const v = s.sg && s.sg.total != null ? s.sg.total : null;
      const ch = s.sg && s.sg.change != null ? s.sg.change : null;
      const unit = (s.sg && s.sg.unit) || "";
      const chColor = ch == null ? TX : (ch > 0 ? RED : GREEN); // 增红减绿，醒目
      const showVal = v == null ? "待接入" : invFmt(v) + unit;
      const barVal = v == null ? 0 : v * (UNIT2T[unit] != null ? UNIT2T[unit] : 1);
      return {
        value: barVal,
        itemStyle: { color: s.color, opacity: v == null ? 0.3 : 1 },
        label: {
          show: true, position: "top",
          formatter: () => {
            if (v == null) return "待接入";
            if (ch == null) return showVal;
            const sign = ch > 0 ? "+" : "";
            return ["{v|" + showVal + "}", "{d|Δ " + sign + invFmt(ch) + "}"].join("\n");
          },
          rich: {
            v: { color: TX, fontSize: 12, fontWeight: "bold", lineHeight: 15 },
            d: { color: chColor, fontSize: 12, fontWeight: "bold", lineHeight: 15 },
          },
        },
      };
    });
    c.setOption({
      backgroundColor: "transparent",
      tooltip: {
        trigger: "axis", backgroundColor: "#1c2530", borderColor: AX, textStyle: { color: TXT },
        formatter: ps => {
          const i = ps[0].dataIndex, sg = segs[i].sg;
          if (!sg || sg.total == null) return segs[i].name + "<br/>待接入";
          const ch = sg.change;
          return segs[i].name + "<br/>总量 " + invFmt(sg.total) + (sg.unit || "") +
            "<br/>较上期 " + (ch == null ? "—" : (ch > 0 ? "+" : "") + invFmt(ch) + (sg.unit || "")) +
            (sg.date ? "<br/>日期 " + sg.date : "");
        },
      },
      grid: { left: 70, right: 16, top: 30, bottom: 26 },
      xAxis: { type: "category", data: cats, ...axisCommon() },
      yAxis: { type: "value", ...axisCommon() },
      series: [{ type: "bar", data: data, barWidth: "48%" }],
    });
    return c;
  }

  // ⑦ 前20会员：当日净持仓变化（净变化 = 多头增仓 − 空头增仓），红增绿减
  function netChgBars(dom, members) {
    const c = base(dom);
    const chg = (m) => (m.long_chg || 0) - (m.short_chg || 0);
    const top = members.slice()
      .map(m => ({ name: m.name, chg: chg(m) }))
      .sort((a, b) => Math.abs(b.chg) - Math.abs(a.chg)).slice(0, 12)
      .sort((a, b) => a.chg - b.chg); // 升序：净变化为负在下、为正在上
    c.setOption({
      backgroundColor: "transparent",
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, backgroundColor: "#1c2530", borderColor: AX, textStyle: { color: TXT },
        formatter: (p) => { const r = top[p[0].dataIndex];
          return `${r.name}<br/>当日净持仓变化 <b>${(r.chg > 0 ? "+" : "") + r.chg}</b> 手` +
            `<br/>${r.chg > 0 ? "净多增加（偏多）" : r.chg < 0 ? "净空增加（偏空）" : "无变化"}`; } },
      grid: { left: 70, right: 30, top: 26, bottom: 26 },
      xAxis: { type: "value", ...axisCommon(), name: "当日净持仓变化(手)", nameTextStyle: { color: TX } },
      yAxis: { type: "category", data: top.map(r => r.name), ...axisCommon(), axisLabel: { color: TX, fontSize: 11 } },
      series: [{
        type: "bar", data: top.map(r => ({ value: r.chg, itemStyle: { color: r.chg >= 0 ? RED : GREEN } })),
        barMaxWidth: 13,
        label: { show: true, position: "right", color: TX, formatter: (p) => (p.value > 0 ? "+" : "") + p.value },
      }],
    });
    return c;
  }

  // ⑨ 情绪仪表盘
  function gauge(dom, score, label, color) {
    const c = base(dom);
    c.setOption({
      backgroundColor: "transparent",
      series: [{
        type: "gauge", min: 0, max: 100, radius: "92%", center: ["50%", "58%"],
        progress: { show: true, width: 14 },
        axisLine: { lineStyle: { width: 14, color: [[0.4, GREEN], [0.6, AMBER], [1, RED]] } },
        axisLabel: { color: TX, distance: -38, fontSize: 10 },
        axisTick: { show: false }, splitLine: { length: 10, lineStyle: { color: AX } },
        pointer: { width: 5, itemStyle: { color: color || BLUE } },
        detail: { valueAnimation: true, fontSize: 26, color: TXT, offsetCenter: [0, "42%"], formatter: "{value}" },
        title: { offsetCenter: [0, "72%"], color: TX, fontSize: 13 },
        data: [{ value: score, name: label }],
      }],
    });
    return c;
  }

  // ⑤ 持仓/成交趋势
  function capital(dom, rows) {
    const c = base(dom);
    const dates = rows.map(r => r.date);
    c.setOption({
      backgroundColor: "transparent",
      tooltip: { trigger: "axis", backgroundColor: "#1c2530", borderColor: AX, textStyle: { color: TXT } },
      legend: { data: ["持仓量", "成交量"], textStyle: { color: TX }, top: 2 },
      grid: { ...baseGrid, right: 16, bottom: 48 },
      xAxis: { type: "category", data: dates, ...axisCommon() },
      yAxis: { type: "value", ...axisCommon() },
      dataZoom: [
        { type: "inside", start: 92, end: 100 },
        { type: "slider", start: 92, end: 100, height: 14, bottom: 6, borderColor: AX,
          fillerColor: "rgba(61,126,255,.15)", handleStyle: { color: BLUE },
          moveHandleStyle: { color: BLUE }, textStyle: { color: TX },
          dataBackground: { lineStyle: { color: AX }, areaStyle: { color: "rgba(61,126,255,.10)" } },
          selectedDataBackground: { lineStyle: { color: BLUE }, areaStyle: { color: "rgba(61,126,255,.20)" } } },
      ],
      series: [
        { name: "持仓量", type: "line", data: rows.map(r => r.oi), showSymbol: false, lineStyle: { color: BLUE }, areaStyle: { color: "rgba(61,126,255,.12)" } },
        { name: "成交量", type: "bar", data: rows.map(r => r.volume), itemStyle: { color: "rgba(224,168,58,.5)" } },
      ],
    });
    return c;
  }

  // ③ 期限结构价差
  function basisSpread(dom, basis) {
    const c = base(dom);
    c.setOption({
      backgroundColor: "transparent",
      tooltip: { trigger: "axis", backgroundColor: "#1c2530", borderColor: AX, textStyle: { color: TXT } },
      grid: { left: 60, right: 16, top: 20, bottom: 30 },
      xAxis: { type: "category", data: [basis.main_contract, basis.sub_contract], ...axisCommon() },
      yAxis: { type: "value", ...axisCommon() },
      series: [{
        type: "bar", data: [
          { value: basis.main_close, itemStyle: { color: RED } },
          { value: basis.sub_close, itemStyle: { color: BLUE } },
        ],
        label: { show: true, position: "top", color: TX },
      }],
    });
    return c;
  }

  // ⑫ 全市场机构情绪：整体多空饼图
  function sentimentPie(dom, totals) {
    const c = base(dom);
    c.setOption({
      backgroundColor: "transparent",
      tooltip: { trigger: "item", backgroundColor: "#1c2530", borderColor: AX, textStyle: { color: TXT }, formatter: "{b}: {c} 条 ({d}%)" },
      legend: { bottom: 0, textStyle: { color: TX }, itemWidth: 10, itemHeight: 10 },
      series: [{
        type: "pie", radius: ["42%", "68%"], center: ["50%", "44%"], avoidLabelOverlap: true,
        label: { color: TXT, formatter: "{b}\n{c}" },
        data: [
          { name: "看多", value: totals.bullish, itemStyle: { color: RED } },
          { name: "看空", value: totals.bearish, itemStyle: { color: GREEN } },
          { name: "中性", value: totals.neutral, itemStyle: { color: "#8b9bb0" } },
        ],
      }],
    });
    return c;
  }

  // ⑫ 全市场机构情绪：各品种净情绪比例（(看多−看空)/(看多+看空+中性)）横向条形榜
  // onPick(rankingItem) 可选：点击柱条回调，便于跳转到品种详情
  function sentimentNet(dom, ranking, onPick) {
    const c = base(dom);
    const sorted = ranking.slice().sort((a, b) => a.net - b.net); // 升序：净空在下、净多在顶
    const names = sorted.map(r => r.name + "(" + r.code + ")");
    const data = sorted.map(r => ({ value: r.net, itemStyle: { color: r.net >= 0 ? RED : GREEN } }));
    c.setOption({
      backgroundColor: "transparent",
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, backgroundColor: "#1c2530", borderColor: AX, textStyle: { color: TXT },
        formatter: (p) => { const r = sorted[p[0].dataIndex]; return `${r.name}(${r.code}) · ${r.category}<br/>看多 ${r.bullish} / 看空 ${r.bearish} / 中性 ${r.neutral}<br/>净情绪比例 <b>${(r.net > 0 ? "+" : "") + (r.net * 100).toFixed(0) + "%"}</b>`; } },
      grid: { left: 104, right: 36, top: 14, bottom: 22 },
      xAxis: { type: "value", ...axisCommon(), name: "净情绪比例", min: -1, max: 1, nameTextStyle: { color: TX },
        axisLabel: { color: TX, formatter: (v) => (v > 0 ? "+" : "") + (v * 100).toFixed(0) + "%" } },
      yAxis: { type: "category", data: names, ...axisCommon(), axisLabel: { color: TX, fontSize: 11 } },
      series: [{ type: "bar", data: data, barMaxWidth: 13, cursor: "pointer",
        label: { show: true, position: "right", color: TX, formatter: (p) => (p.value > 0 ? "+" : "") + (p.value * 100).toFixed(0) + "%" } }],
    });
    if (onPick) {
      c.on("click", (p) => { const r = sorted[p.dataIndex]; if (r) onPick(r); });
    }
    return c;
  }

  // ⑬ 单品种近 N 天净情绪比例曲线（7 日滚动：(看多−看空)/(看多+看空+中性)）
  // series: [{label, net, hasOp, bull, bear, neutral}]，net 为当日及前 6 天净情绪比例之和
  function sentimentCurve(dom, series) {
    const c = base(dom);
    const labels = series.map(s => s.label);
    const line = series.map(s => s.net);
    // 散点按「类目下标 + 值」成对，便于落在对应日期上（红=净多，绿=净空）
    const upPts = series.map((s, i) => s.hasOp && s.net >= 0 ? [i, s.net] : null).filter(Boolean);
    const dnPts = series.map((s, i) => s.hasOp && s.net < 0 ? [i, s.net] : null).filter(Boolean);
    c.setOption({
      backgroundColor: "transparent",
      tooltip: { trigger: "axis", backgroundColor: "#1c2530", borderColor: AX, textStyle: { color: TXT },
        formatter: (p) => {
          const s = series[p[0].dataIndex];
          if (!s) return "";
          return `${s.label}<br/>近7日净情绪比例 <b>${(s.net > 0 ? "+" : "") + (s.net * 100).toFixed(0) + "%"}</b>` +
            (s.hasOp ? `<br/>当日观点：看多 ${s.bull} / 看空 ${s.bear} / 中性 ${s.neutral}` : "<br/>（近7日窗口内无新观点）");
        } },
      grid: { left: 48, right: 18, top: 22, bottom: 46 },
      xAxis: { type: "category", data: labels, boundaryGap: false, ...axisCommon(),
        axisLabel: { color: TX, fontSize: 10, interval: Math.max(0, Math.floor(labels.length / 10)) } },
      yAxis: { type: "value", ...axisCommon(), name: "净情绪比例", min: -1, max: 1, nameTextStyle: { color: TX },
        axisLabel: { color: TX, formatter: (v) => (v > 0 ? "+" : "") + (v * 100).toFixed(0) + "%" } },
      series: [
        { type: "line", data: line, smooth: true, showSymbol: false, connectNulls: true,
          lineStyle: { width: 2, color: BLUE },
          areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1,
            [{ offset: 0, color: "rgba(61,126,255,.28)" }, { offset: 1, color: "rgba(61,126,255,0)" }]) },
          markLine: { silent: true, symbol: "none", lineStyle: { color: TX, type: "dashed" }, data: [{ yAxis: 0 }] } },
        { type: "scatter", data: upPts, symbolSize: 7, itemStyle: { color: RED }, tooltip: { show: false } },
        { type: "scatter", data: dnPts, symbolSize: 7, itemStyle: { color: GREEN }, tooltip: { show: false } },
      ],
    });
    return c;
  }

  // 前20会员近一月「主力合约净持仓」变化曲线
  function top20NetCurve(dom, history) {
    const c = base(dom);
    const pts = history.slice().sort((a, b) => (a.date < b.date ? -1 : 1));
    const labels = pts.map(p => p.date.length === 8 ? p.date.slice(4).replace(/(..)(..)/, "$1-$2") : p.date);
    const line = pts.map(p => p.net);
    const first = pts[0], last = pts[pts.length - 1];
    const chg = last.net - first.net;
    const up = last.net >= 0;
    const col = up ? RED : GREEN;
    const fmtN = (n) => (n == null ? "-" : Number(n).toLocaleString("en-US"));
    c.setOption({
      backgroundColor: "transparent",
      tooltip: { trigger: "axis", backgroundColor: "#1c2530", borderColor: AX, textStyle: { color: TXT },
        formatter: (p) => {
          const x = pts[p[0].dataIndex];
          return `${x.date}<br/>主力合约净持仓 <b>${fmtN(x.net)}</b> 手` +
            `<br/>持买 ${fmtN(x.long_total)} / 持卖 ${fmtN(x.short_total)}` +
            `<br/>当日增减：多 ${fmtN(x.long_chg)} / 空 ${fmtN(x.short_chg)}`;
        } },
      grid: { left: 60, right: 18, top: 18, bottom: 40 },
      xAxis: { type: "category", data: labels, boundaryGap: false, ...axisCommon(),
        axisLabel: { color: TX, fontSize: 10, interval: Math.max(0, Math.floor(labels.length / 8)) } },
      yAxis: { type: "value", ...axisCommon(), name: "合计净持仓", nameTextStyle: { color: TX },
        axisLabel: { color: TX, formatter: (v) => (v >= 0 ? "+" : "") + (Math.abs(v) >= 10000 ? (v / 10000).toFixed(1) + "万" : v) } },
      series: [{
        type: "line", data: line, smooth: true, showSymbol: true, symbolSize: 5, connectNulls: true,
        itemStyle: { color: col }, lineStyle: { width: 2, color: col },
        areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1,
          [{ offset: 0, color: up ? "rgba(232,72,63,.26)" : "rgba(31,171,107,.26)" },
           { offset: 1, color: "rgba(0,0,0,0)" }]) },
        markLine: { silent: true, symbol: "none", lineStyle: { color: TX, type: "dashed" }, data: [{ yAxis: 0 }] },
      }],
    });
    return c;
  }

  // 前20会员：COT 净持仓 + z-score 归一化轴 + 观点净情绪 叠加背离分析
  // history: [{date, net, z, contract?}]；opinion: [{date, net, bull, bear, z}]
  function t20Divergence(dom, history, opinion) {
    const c = base(dom);
    const hist = (history || []).slice().sort((a, b) => (a.date < b.date ? -1 : 1));
    if (!hist.length) return c;
    const opByDate = {};
    (opinion || []).forEach(o => { opByDate[o.date] = o; });
    const labels = hist.map(p => p.date.length === 8 ? p.date.slice(4).replace(/(..)(..)/, "$1-$2") : p.date);
    const netLine = hist.map(p => p.net);
    const zCot = hist.map(p => (p.z == null ? null : p.z));
    const zOp = hist.map(p => { const o = opByDate[p.date]; return (o && o.z != null) ? o.z : null; });
    const fmtN = (n) => (n == null ? "-" : Number(n).toLocaleString("en-US"));

    // 背离区间：COT z 与 观点 z 异号（均非零）
    const markAreas = [];
    let segStart = null;
    for (let i = 0; i < hist.length; i++) {
      const a = zCot[i], b = zOp[i];
      const opp = (a != null && b != null && a !== 0 && b !== 0 && Math.sign(a) !== Math.sign(b));
      if (opp && segStart === null) segStart = i;
      if ((!opp || i === hist.length - 1) && segStart !== null) {
        markAreas.push([{ xAxis: labels[segStart] }, { xAxis: labels[Math.min(i, hist.length - 1)] }]);
        segStart = null;
      }
    }

    c.setOption({
      backgroundColor: "transparent",
      tooltip: { trigger: "axis", backgroundColor: "#1c2530", borderColor: AX, textStyle: { color: TXT },
        formatter: (ps) => {
          const idx = ps[0].dataIndex; const x = hist[idx];
          let s = `${x.date}<br/>COT主力净持仓 <b>${fmtN(x.net)}</b> 手 (z=${x.z == null ? "-" : x.z})`;
          if (x.contract) s += `<br/>主力合约 ${x.contract}`;
          const o = opByDate[x.date];
          if (o) s += `<br/>观点净情绪比例 ${o.net > 0 ? "+" : ""}${(o.net * 100).toFixed(0)}% (看多${o.bull}/看空${o.bear}, z=${o.z == null ? "-" : o.z})`;
          const a = zCot[idx], b = zOp[idx];
          if (a != null && b != null && a !== 0 && b !== 0 && Math.sign(a) !== Math.sign(b))
            s += `<br/><b style="color:#e0a83a">⚠ 背离：持仓与观点方向不一致</b>`;
          return s;
        } },
      legend: { data: ["COT净持仓", "COT z", "观点净情绪 z"], textStyle: { color: TX }, top: 0, right: 6, itemWidth: 14, itemHeight: 8 },
      grid: { left: 64, right: 56, top: 34, bottom: 40 },
      xAxis: { type: "category", data: labels, boundaryGap: false, ...axisCommon(),
        axisLabel: { color: TX, fontSize: 10, interval: Math.max(0, Math.floor(labels.length / 8)) } },
      yAxis: [
        { type: "value", ...axisCommon(), name: "净持仓(手)", nameTextStyle: { color: TX }, scale: true,
          axisLabel: { color: TX, formatter: (v) => (v >= 0 ? "+" : "") + (Math.abs(v) >= 10000 ? (v / 10000).toFixed(1) + "万" : v) } },
        { type: "value", ...axisCommon(), name: "z-score", nameTextStyle: { color: TX },
          axisLabel: { color: TX, formatter: (v) => v.toFixed(1) }, splitLine: { show: false } },
      ],
      series: [
        { name: "COT净持仓", type: "line", yAxisIndex: 0, data: netLine, smooth: true,
          showSymbol: true, symbolSize: 4, connectNulls: true,
          itemStyle: { color: BLUE }, lineStyle: { width: 2, color: BLUE },
          areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1,
            [{ offset: 0, color: "rgba(61,126,255,.26)" }, { offset: 1, color: "rgba(0,0,0,0)" }]) },
          markArea: markAreas.length ? { silent: true, itemStyle: { color: "rgba(224,168,58,.10)" }, data: markAreas } : undefined },
        { name: "COT z", type: "line", yAxisIndex: 1, data: zCot, smooth: true,
          showSymbol: false, connectNulls: true, itemStyle: { color: RED }, lineStyle: { width: 2, color: RED, type: "solid" } },
        { name: "观点净情绪 z", type: "line", yAxisIndex: 1, data: zOp, smooth: true,
          showSymbol: false, connectNulls: true, itemStyle: { color: AMBER },
          lineStyle: { width: 2, color: AMBER, type: "dashed" } },
      ],
    });
    return c;
  }

  global.Charts = { kline, butterfly, netBars, netChgBars, inventory, gauge, capital, basisSpread, sentimentPie, sentimentNet, sentimentCurve, top20NetCurve, t20Divergence };
})(window);
