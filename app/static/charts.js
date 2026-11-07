/* 期货工作台 —— ECharts 图表封装（国内习惯：涨红跌绿，暗色主题） */
(function (global) {
  const AX = "#2a3543", TX = "#8b9bb0", TXT = "#e6edf3";
  const RED = "#e8483f", GREEN = "#1fab6b", BLUE = "#3d7eff", AMBER = "#e0a83a";
  const baseGrid = { left: 56, right: 16, top: 30, bottom: 40 };
  // 四档分档按 |净持仓| 名次划分：只改显示名，数据字段仍是原始档名（兼容历史采样 key）
  const TIER_NAMES = { "超大户/机构": "前5席位", "大户": "第6–10名", "中户": "第11–15名", "散户/中小": "第16–20名" };
  const tierName = (t) => TIER_NAMES[t] || t;

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
  function fmtN(n) {
    if (n == null || isNaN(n)) return "-";
    return Number(n).toLocaleString("en-US");
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
    // 净情绪比例 = (看多 − 看空) / (看多 + 看空 + 中性)，区间 [-1,1]，与曲线/背离序列口径一致。
    // 注意：后端 ranking 的 net 字段是「看多−看空」的原始计数差，不可直接当比例 ×100，
    // 否则会出现 700% / -1700% 等超过 100% 的异常（如 RB -1700%、CU 700%）。
    const withRatio = ranking.map(r => {
      const tot = (r.bullish || 0) + (r.bearish || 0) + (r.neutral || 0);
      const ratio = tot ? (r.bullish - r.bearish) / tot : 0;
      return Object.assign({}, r, { _ratio: ratio });
    });
    const sorted = withRatio.slice().sort((a, b) => a._ratio - b._ratio); // 升序：净空在下、净多在顶
    const names = sorted.map(r => r.name + "(" + r.code + ")");
    const data = sorted.map(r => ({ value: r._ratio, itemStyle: { color: r._ratio >= 0 ? RED : GREEN } }));
    c.setOption({
      backgroundColor: "transparent",
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, backgroundColor: "#1c2530", borderColor: AX, textStyle: { color: TXT },
        formatter: (p) => { const r = sorted[p[0].dataIndex]; return `${r.name}(${r.code}) · ${r.category}<br/>看多 ${r.bullish} / 看空 ${r.bearish} / 中性 ${r.neutral}<br/>净情绪比例 <b>${(r._ratio > 0 ? "+" : "") + (r._ratio * 100).toFixed(0) + "%"}</b>`; } },
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

  // ⑦ 资金动向：按 |净持仓| 名次分档 净持仓(柱) + 当日净持仓变化(线，次轴) 组合图
  function capitalFlowTier(dom, tiers) {
    const c = base(dom);
    const names = tiers.map(t => tierName(t.tier));
    const net = tiers.map(t => t.net);
    const chg = tiers.map(t => t.net_chg);
    c.setOption({
      backgroundColor: "transparent",
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, backgroundColor: "#1c2530", borderColor: AX, textStyle: { color: TXT },
        formatter: (p) => {
          const i = p[0].dataIndex;
          return `${names[i]}<br/>净持仓 <b>${fmtN(net[i])}</b> 手` +
            `<br/>当日净持仓变化 <b style="color:${chg[i] >= 0 ? RED : GREEN}">${chg[i] > 0 ? "+" : ""}${fmtN(chg[i])}</b> 手` +
            `<br/>持买 ${fmtN(tiers[i].long)} / 持卖 ${fmtN(tiers[i].short)}`;
        } },
      legend: { data: ["净持仓", "当日净持仓变化"], textStyle: { color: TX }, top: 2 },
      grid: { left: 64, right: 56, top: 30, bottom: 26 },
      xAxis: { type: "category", data: names, ...axisCommon(), axisLabel: { color: TX, fontSize: 11 } },
      yAxis: [
        { type: "value", ...axisCommon(), name: "净持仓", nameTextStyle: { color: TX },
          axisLabel: { color: TX, formatter: (v) => (Math.abs(v) >= 10000 ? (v / 10000).toFixed(0) + "万" : v) } },
        { type: "value", ...axisCommon(), name: "净变化", position: "right", splitLine: { show: false }, nameTextStyle: { color: TX },
          axisLabel: { color: TX, formatter: (v) => (v > 0 ? "+" : "") + (Math.abs(v) >= 10000 ? (v / 10000).toFixed(0) + "万" : v) } },
      ],
      series: [
        { name: "净持仓", type: "bar", yAxisIndex: 0, data: net.map(v => ({ value: v, itemStyle: { color: v >= 0 ? RED : GREEN } })),
          barMaxWidth: 30, label: { show: true, position: "top", color: TX, formatter: (p) => (p.value > 0 ? "+" : "") + fmtN(p.value) } },
        { name: "当日净持仓变化", type: "line", yAxisIndex: 1, data: chg, symbolSize: 8,
          itemStyle: { color: AMBER }, lineStyle: { width: 2, color: AMBER },
          label: { show: true, position: "bottom", color: AMBER, formatter: (p) => (p.value > 0 ? "+" : "") + fmtN(p.value) } },
      ],
    });
    return c;
  }

  // ⑦ 资金动向：总持仓（主连 hold）历史回溯，揭示资金整体进出场节奏
  function totalPositionCurve(dom, series) {
    const c = base(dom);
    const dates = series.map(p => p.date);
    const hold = series.map(p => p.hold);
    const last = series[series.length - 1] || { hold: 0, chg: 0 };
    c.setOption({
      backgroundColor: "transparent",
      tooltip: { trigger: "axis", backgroundColor: "#1c2530", borderColor: AX, textStyle: { color: TXT },
        formatter: (p) => { const x = series[p[0].dataIndex]; return `${x.date}<br/>总持仓 <b>${fmtN(x.hold)}</b> 手<br/>较前值 ${x.chg > 0 ? "+" : ""}${fmtN(x.chg)} 手`; } },
      grid: { left: 62, right: 16, top: 18, bottom: 40 },
      xAxis: { type: "category", data: dates, boundaryGap: false, ...axisCommon(),
        axisLabel: { color: TX, fontSize: 10, interval: Math.max(0, Math.floor(dates.length / 8)) } },
      yAxis: { type: "value", ...axisCommon(), name: "总持仓", nameTextStyle: { color: TX },
        axisLabel: { color: TX, formatter: (v) => (Math.abs(v) >= 10000 ? (v / 10000).toFixed(0) + "万" : v) } },
      dataZoom: [
        { type: "inside", start: 80, end: 100 },
        { type: "slider", start: 80, end: 100, height: 14, bottom: 6, borderColor: AX,
          fillerColor: "rgba(61,126,255,.15)", handleStyle: { color: BLUE }, moveHandleStyle: { color: BLUE }, textStyle: { color: TX },
          dataBackground: { lineStyle: { color: AX }, areaStyle: { color: "rgba(61,126,255,.10)" } },
          selectedDataBackground: { lineStyle: { color: BLUE }, areaStyle: { color: "rgba(61,126,255,.20)" } } },
      ],
      series: [{
        type: "line", data: hold, smooth: true, showSymbol: true, symbolSize: 4, connectNulls: true,
        itemStyle: { color: BLUE }, lineStyle: { width: 2, color: BLUE },
        areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1,
          [{ offset: 0, color: "rgba(61,126,255,.26)" }, { offset: 1, color: "rgba(0,0,0,0)" }]) },
        markLine: { silent: true, symbol: "none", lineStyle: { color: TX, type: "dashed" }, data: [{ yAxis: last.hold }] },
      }],
    });
    return c;
  }

  // ⑦ 资金动向：历史回溯 —— 各名次分档净持仓变化曲线
  function tierNetHistory(dom, history) {
    const c = base(dom);
    const tiers = Object.keys(history[0] || {}).filter(k => k !== "date");
    const colors = [RED, AMBER, BLUE, GREEN, "#9b6dff"];
    const dates = history.map(h => h.date);
    const series = tiers.map((t, i) => ({
      name: tierName(t), type: "line", data: history.map(h => h[t]), smooth: true, showSymbol: false, connectNulls: true,
      itemStyle: { color: colors[i % colors.length] }, lineStyle: { width: 2, color: colors[i % colors.length] },
    }));
    if (series[0]) series[0].markLine = { silent: true, symbol: "none", lineStyle: { color: TX, type: "dashed" }, data: [{ yAxis: 0 }] };
    c.setOption({
      backgroundColor: "transparent",
      tooltip: { trigger: "axis", backgroundColor: "#1c2530", borderColor: AX, textStyle: { color: TXT },
        formatter: (ps) => { let s = ps[0].axisValue + "<br/>"; ps.forEach(p => { s += `${p.seriesName} <b>${fmtN(p.value)}</b> 手<br/>`; }); return s; } },
      legend: { data: tiers.map(tierName), textStyle: { color: TX }, top: 0, itemWidth: 12, itemHeight: 8 },
      grid: { left: 62, right: 16, top: 34, bottom: 36 },
      xAxis: { type: "category", data: dates, boundaryGap: false, ...axisCommon(),
        axisLabel: { color: TX, fontSize: 10, interval: Math.max(0, Math.floor(dates.length / 6)) } },
      yAxis: { type: "value", ...axisCommon(), name: "净持仓", nameTextStyle: { color: TX },
        axisLabel: { color: TX, formatter: (v) => (Math.abs(v) >= 10000 ? (v / 10000).toFixed(0) + "万" : v) } },
      series: series,
    });
    return c;
  }

  // ⑦ 专业框架 L1：价 · 量 · 仓 三联图
  // 上格：收盘价(左轴) × 总持仓(右轴)，琥珀点为「价涨仓减 / 价跌仓增」的价仓背离日；
  // 下格：成交量柱(涨红跌绿，显著放量描白边) + VMA5 / VMA20 均量线；
  // 两格共享十字准星与 dataZoom：价格方向是否由真实增量资金推动，须同时看「量」与「仓」。
  // opts: { pxEps, oiEps } —— 由 app.js 按品种波动率算出的噪音带（%）。
  // 日涨跌 / 日持仓变化率双双超过噪音带才标注「价仓背离日」，低波品种更敏感、高波品种更钝。
  function priceVolumeOi(dom, rows, opts) {
    if (!dom) return null;
    const pxEps = opts && typeof opts.pxEps === "number" ? opts.pxEps : 0.3;
    const oiEps = opts && typeof opts.oiEps === "number" ? opts.oiEps : 0;
    const c = base(dom);
    const num = (v) => (typeof v === "number" && isFinite(v) ? v : 0);
    const dstr = (d) => { const s = String(d).replace(/-/g, ""); return s.length === 8 ? `${s.slice(4, 6)}-${s.slice(6, 8)}` : String(d); };
    const full = rows.slice(-80);          // 多取 20 根，保证 VMA20 / 量比在首屏即有值
    const off = Math.max(0, full.length - 60);
    const a = full.slice(off);
    if (!a.length) return c;
    const avg = (arr) => { const v = arr.filter(x => x > 0); return v.length ? v.reduce((x, y) => x + y, 0) / v.length : 0; };
    const rollMa = (n) => full.map((_, i) => i < n - 1 ? null
      : Math.round(avg(full.slice(i - n + 1, i + 1).map(r => num(r.volume))))).slice(off);
    const ratioAll = full.map((r, i) => {
      if (i < 5) return 1;
      const base = avg(full.slice(i - 5, i).map(x => num(x.volume)));  // 前 5 日均量（不含当日）
      return base ? num(r.volume) / base : 1;
    });
    const vma5 = rollMa(5), vma20 = rollMa(20), ratios = ratioAll.slice(off);
    const dates = a.map(r => dstr(r.date));
    const closes = a.map(r => num(r.close));
    const ois = a.map(r => num(r.oi));
    const divPoints = a.map((r) => {
      const p = num(r.chg_pct), o = num(r.oi_chg);
      const prevOi = num(r.oi) - o;                    // 由「当日较前增减」还原前一日持仓
      const oPct = prevOi > 0 ? o / prevOi * 100 : 0; // 换成百分比才能与噪音带比较
      const diverge = (p > pxEps && oPct < -oiEps) || (p < -pxEps && oPct > oiEps);
      return diverge ? num(r.close) : null;
    });
    const volBars = a.map((r, i) => {
      const up = num(r.chg_pct) >= 0, heavy = ratios[i] >= 1.5;
      return { value: num(r.volume), itemStyle: {
        color: up ? (heavy ? "rgba(232,72,63,.95)" : "rgba(232,72,63,.55)")
                  : (heavy ? "rgba(31,171,107,.95)" : "rgba(31,171,107,.55)"),
        borderColor: heavy ? TXT : "transparent", borderWidth: heavy ? 1 : 0 } };
    });
    c.setOption({
      backgroundColor: "transparent",
      tooltip: { trigger: "axis", axisPointer: { type: "cross" }, backgroundColor: "#1c2530", borderColor: AX, textStyle: { color: TXT, fontSize: 11 },
        formatter: (ps) => {
          if (!ps || !ps.length) return "";
          const i = ps[0].dataIndex, r = a[i];
          if (!r) return "";
          const p = num(r.chg_pct), o = num(r.oi_chg), rt = ratios[i];
          let s = `${r.date}<br/>收盘 <b>${fmtN(r.close)}</b>　涨跌 <b style="color:${p >= 0 ? RED : GREEN}">${p > 0 ? "+" : ""}${p.toFixed(2)}%</b>`;
          s += `<br/>总持仓 <b>${fmtN(r.oi)}</b> 手　较前 <b style="color:${o >= 0 ? RED : GREEN}">${o > 0 ? "+" : ""}${fmtN(o)}</b>`;
          s += `<br/>成交量 <b>${fmtN(r.volume)}</b> 手　量比(前5日) <b style="color:${AMBER}">${rt.toFixed(2)}×</b>`;
          s += `<br/>VMA5 ${fmtN(vma5[i])}　VMA20 ${fmtN(vma20[i])}`;
          s += `<br/><span style="color:${TX}">噪音带 价 ±${pxEps.toFixed(2)}% / 仓 ±${oiEps.toFixed(2)}%（本品种波动率自适应）</span>`;
          s += `<br/><span style="color:${TX}">价${p >= 0 ? "涨" : "跌"} · ${rt >= 1.5 ? "放量" : rt <= 0.65 ? "缩量" : "常量"} · ${o >= 0 ? "增仓" : "减仓"}</span>`;
          if (rt >= 1.5) s += `<br/><span style="color:${AMBER}">◆ 显著放量：换手放大，须看仓是增是减</span>`;
          else if (rt <= 0.65) s += `<br/><span style="color:${TX}">◇ 明显缩量：参与度下降，信号强度打折</span>`;
          if (divPoints[i] != null) s += `<br/><span style="color:${AMBER}">◆ 价仓背离日：价格由平仓/回补推动</span>`;
          return s;
        } },
      axisPointer: { link: [{ xAxisIndex: "all" }], label: { backgroundColor: "#2a3543" } },
      legend: { type: "scroll", data: ["收盘价", "总持仓", "价仓背离", "成交量", "VMA5", "VMA20"],
        textStyle: { color: TX, fontSize: 10 }, pageTextStyle: { color: TX }, top: 0, itemWidth: 12, itemHeight: 8, itemGap: 10 },
      grid: [
        { left: 62, right: 66, top: 30, height: "52%" },
        { left: 62, right: 66, top: "70%", height: "20%" },
      ],
      xAxis: [
        { type: "category", gridIndex: 0, data: dates, boundaryGap: false, ...axisCommon(),
          axisLabel: { show: false }, axisTick: { show: false } },
        { type: "category", gridIndex: 1, data: dates, boundaryGap: true, ...axisCommon(),
          axisLabel: { color: TX, fontSize: 10, interval: Math.max(0, Math.floor(dates.length / 8)) } },
      ],
      yAxis: [
        { type: "value", gridIndex: 0, scale: true, ...axisCommon(), name: "价格", nameTextStyle: { color: TX },
          axisLabel: { color: TX } },
        { type: "value", gridIndex: 0, scale: true, ...axisCommon(), name: "持仓", position: "right", splitLine: { show: false }, nameTextStyle: { color: TX },
          axisLabel: { color: TX, formatter: (v) => (Math.abs(v) >= 10000 ? (v / 10000).toFixed(0) + "万" : v) } },
        { type: "value", gridIndex: 1, min: 0, ...axisCommon(), name: "量", position: "right", nameTextStyle: { color: TX, fontSize: 10 },
          splitLine: { show: false }, axisLabel: { color: TX, fontSize: 10,
            formatter: (v) => (Math.abs(v) >= 10000 ? (v / 10000).toFixed(0) + "万" : v) } },
      ],
      dataZoom: [{ type: "inside", xAxisIndex: [0, 1], start: 50, end: 100 }],
      series: [
        { name: "总持仓", type: "line", xAxisIndex: 0, yAxisIndex: 1, data: ois, smooth: true, showSymbol: false, connectNulls: true,
          itemStyle: { color: BLUE }, lineStyle: { width: 2, color: BLUE, opacity: 0.85 },
          areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1,
            [{ offset: 0, color: "rgba(61,126,255,.20)" }, { offset: 1, color: "rgba(61,126,255,0)" }]) } },
        { name: "收盘价", type: "line", xAxisIndex: 0, yAxisIndex: 0, data: closes, smooth: true, showSymbol: false, connectNulls: true,
          itemStyle: { color: AMBER }, lineStyle: { width: 2.2, color: AMBER } },
        { name: "价仓背离", type: "scatter", xAxisIndex: 0, yAxisIndex: 0, data: divPoints, symbolSize: 11,
          itemStyle: { color: "rgba(224,168,58,.85)", borderColor: "#fff", borderWidth: 1 },
          tooltip: { show: false } },
        { name: "成交量", type: "bar", xAxisIndex: 1, yAxisIndex: 2, data: volBars, barMaxWidth: 9,
          tooltip: { show: false } },
        { name: "VMA5", type: "line", xAxisIndex: 1, yAxisIndex: 2, data: vma5, smooth: true, showSymbol: false,
          connectNulls: true, itemStyle: { color: "#e6edf3" }, lineStyle: { width: 1.4, color: "#e6edf3", opacity: 0.9 },
          tooltip: { show: false } },
        { name: "VMA20", type: "line", xAxisIndex: 1, yAxisIndex: 2, data: vma20, smooth: true, showSymbol: false,
          connectNulls: true, itemStyle: { color: BLUE }, lineStyle: { width: 1.2, color: BLUE, type: "dashed", opacity: 0.8 },
          tooltip: { show: false } },
      ],
    });
    return c;
  }
  // 兼容旧调用名（语义已升级为价·量·仓三联）
  const priceOiDual = priceVolumeOi;

  // ⑦ 专业框架 L3：主力席位净变化横条 —— 主动进攻(实色) vs 回补/平仓(半透明)
  function memberMomentum(dom, members) {
    if (!dom) return null;
    const c = base(dom);
    const num = (v) => (typeof v === "number" && isFinite(v) ? v : 0);
    const a = members.slice().sort((x, y) => num(y.net_chg) - num(x.net_chg)).slice(0, 12);
    const names = a.map(m => m.name);
    const vals = a.map(m => num(m.net_chg));
    const colors = a.map(m => {
      const v = num(m.net_chg), L = num(m.long_chg), S = num(m.short_chg);
      if (v >= 0) return L > 0 ? RED : "rgba(232,72,63,.45)";      // 主动多增 / 空头回补
      return S > 0 ? GREEN : "rgba(31,171,107,.45)";                 // 主动空增 / 多头平仓
    });
    c.setOption({
      backgroundColor: "transparent",
      tooltip: { trigger: "item", backgroundColor: "#1c2530", borderColor: AX, textStyle: { color: TXT },
        formatter: (p) => {
          const m = a[p.dataIndex], L = num(m.long_chg), S = num(m.short_chg);
          const kind = num(m.net_chg) >= 0 ? (L > 0 ? "多头主动增仓" : "空头回补（被动偏多）")
            : (S > 0 ? "空头主动增仓" : "多头平仓（被动偏空）");
          return `<b>${m.name}</b><br/>当日净持仓变化 <b style="color:${num(m.net_chg) >= 0 ? RED : GREEN}">${num(m.net_chg) > 0 ? "+" : ""}${fmtN(m.net_chg)}</b> 手` +
            `<br/>多头增减 ${L > 0 ? "+" : ""}${fmtN(L)}　空头增减 ${S > 0 ? "+" : ""}${fmtN(S)}` +
            `<br/>净持仓 ${fmtN(m.net)} 手<br/><span style="color:${AMBER}">${kind}</span>`;
        } },
      grid: { left: 92, right: 70, top: 12, bottom: 26 },
      xAxis: { type: "value", ...axisCommon(), name: "净变化(手)", nameTextStyle: { color: TX },
        axisLabel: { color: TX, formatter: (v) => (v > 0 ? "+" : "") + (Math.abs(v) >= 10000 ? (v / 10000).toFixed(0) + "万" : v) } },
      yAxis: { type: "category", data: names, inverse: true, ...axisCommon(),
        axisLabel: { color: TX, fontSize: 11 } },
      series: [{
        type: "bar", data: vals.map((v, i) => ({ value: v, itemStyle: { color: colors[i], borderRadius: 3 } })),
        barMaxWidth: 16,
        label: { show: true, position: "right", color: TX, fontSize: 10,
          formatter: (p) => (p.value > 0 ? "+" : "") + fmtN(p.value) },
        markLine: { silent: true, symbol: "none", lineStyle: { color: TX, type: "dashed" }, data: [{ xAxis: 0 }] },
      }],
    });
    return c;
  }

  global.Charts = { kline, butterfly, netBars, netChgBars, inventory, gauge, capital, basisSpread, sentimentPie, sentimentNet, sentimentCurve, top20NetCurve, t20Divergence, capitalFlowTier, totalPositionCurve, tierNetHistory, priceVolumeOi, priceOiDual, memberMomentum };
})(window);
