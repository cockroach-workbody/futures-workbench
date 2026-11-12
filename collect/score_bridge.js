#!/usr/bin/env node
/* eslint-disable no-undef */
/*
 * 构建期评分桥：在 VM 里加载 app/static/app.js，直接调用页面使用的
 * capFlowAnalytics / capFlowNoise，按历史截面逐日重算 L4「四维共振读数」。
 *
 * 目的：不在 Python 里重写第二份打分公式，避免「近1月综合读数曲线」与
 * 页面当日读数出现口径漂移。app.js 通过 window.__wbScoreCore 暴露纯计算层。
 *
 * 用法： node collect/score_bridge.js < tasks.json > results.json
 * 输入： {"tasks":[{"code","date","s":{"kline":{"rows":[...]}},"cf":{...}}]}
 * 输出： {"results":[{"code","date","score","label","tone","confirm",
 *                     "parts":[{"k","v","w","contrib"}],"quad","band","volTag",
 *                     "rollover","score_adj"}]}
 */
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const appSrc = fs.readFileSync(
  path.join(__dirname, '..', 'app', 'static', 'app.js'), 'utf8');

function el(key) {
  return {
    key, innerHTML: '', textContent: '', value: '', disabled: false,
    dataset: {}, style: {}, onclick: null, onchange: null,
    classList: { add() {}, remove() {}, contains() { return false; } },
    appendChild() {}, addEventListener() {}, querySelector() { return null; },
    querySelectorAll() { return []; }, scrollIntoView() {}, blur() {}, focus() {},
    closest() { return null; }, getAttribute() { return null; }, setAttribute() {},
  };
}
const echarts = {
  init() {
    return {
      setOption() {}, resize() {}, dispose() {}, on() {}, off() {},
      getZr() { return { on() {} }; }, dispatchAction() {}, clear() {},
      showLoading() {}, hideLoading() {},
    };
  },
  getInstanceByDom() { return null; },
  graphic: { LinearGradient: function () { return {}; } },
};
const ctx = {
  console, setTimeout, clearTimeout, setInterval: () => 0, clearInterval: () => {},
  Promise, Math, Date, JSON, Number, String, Array, Object,
  encodeURIComponent, decodeURIComponent, isNaN, parseInt, parseFloat,
  alert: () => {}, echarts,
  addEventListener: () => {},
  document: {
    querySelector: (s) => el(s), getElementById: (id) => el('#' + id),
    createElement: (t) => el('create:' + t), addEventListener: () => {},
    querySelectorAll: () => [], body: el('body'),
  },
  fetch: async () => ({ ok: true, json: async () => ({}) }),
};
ctx.window = ctx;
ctx.globalThis = ctx;
ctx.self = ctx;
vm.createContext(ctx);
vm.runInContext(appSrc, ctx, { filename: 'app.js' });

const core = ctx.window.__wbScoreCore;
if (!core || typeof core.analytics !== 'function' || typeof core.noise !== 'function') {
  process.stderr.write('score_bridge: window.__wbScoreCore 未暴露，app.js 可能已改动\n');
  process.exit(2);
}

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const results = [];
for (const t of (input.tasks || [])) {
  try {
    const rows = ((t.s || {}).kline || {}).rows || [];
    const noise = core.noise(rows);
    const A = core.analytics(t.s, t.cf, noise);
    const sc = A.score;
    results.push({
      code: t.code, date: t.date, score: sc.value, label: sc.label,
      tone: sc.tone, confirm: sc.confirm,
      parts: sc.parts.map((p) => ({ k: p.k, v: p.v, w: p.w, contrib: p.v * p.w })),
      quad: A.pv.quad ? A.pv.quad.name : '',
      band: A.noise && A.noise.band ? { px: A.noise.band.px, oi: A.noise.band.oi } : null,
      volTag: A.volume && A.volume.verdict ? A.volume.verdict.tag : '',
      horizon: A.noise ? A.noise.horizon : null,
      // 临近交割自然减仓（移仓）识别：仅在确认窗口时落盘，供曲线标注与校正参考值
      rollover: sc.rollover && sc.rollover.fade > 0 ? {
        level: sc.rollover.level, fade: sc.rollover.fade, days: sc.rollover.days,
        oi5: Math.round((sc.rollover.oi5 || 0) * 100) / 100,
        decDays: sc.rollover.decDays, dual: sc.rollover.dual,
        contract: sc.rollover.contract || '', delivery: sc.rollover.delivery || '',
      } : null,
      score_adj: sc.adjusted,
    });
  } catch (e) {
    results.push({ code: t.code, date: t.date, error: String(e && e.message || e) });
  }
}
process.stdout.write(JSON.stringify({ results }));
