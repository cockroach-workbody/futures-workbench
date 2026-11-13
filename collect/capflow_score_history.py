#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第⑦部分「资金分析框架 · L4 四维共振读数」近一月逐日打分采集器

背景：L4 读数（0~100）依赖当日截面的四档 tiers / 前5席位 / 大单动向，
而三榜合一历史曲线 net_history 只保存了逐日「合计」值，无法直接回溯打分。
本脚本按「品种全部挂牌月份」逐日截面（allmonths_<CODE>.json，由
capflow_all_months.py 采集）复原每个交易日的完整打分输入，无需重抓榜单。

口径关键：分数本身不在 Python 里重写。collect/score_bridge.js 会在 VM 里
加载 app/static/app.js，直接调用页面使用的 capFlowAnalytics / capFlowNoise
（通过 window.__wbScoreCore 暴露），保证历史曲线与页面当日读数用同一份公式，
零口径漂移。价量仓中的「量 / 仓」、量能与噪音带均取「截止该交易日」的
全月份合成行情；价格仍为主力 / 主连收盘价（不同月份价格不可加总），
不使用未来数据。

产物：
  collect/raw_capflow/scoreinput_<CODE>.json  逐日打分输入缓存（幂等）
  collect/raw_capflow/capflow_<CODE>.json     新增 score_history 字段（含逐日 close / chg_pct 价格对照）
  build_all.py 原样透传 capital_flow，score_history 自动进入各品种快照。

调用：
  python collect/capflow_score_history.py --codes JD AP        # 试跑
  python collect/capflow_score_history.py --workers 4          # 全量
  python collect/capflow_score_history.py --force              # 忽略缓存重抓
"""
import argparse
import glob
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "collect"))
import akshare_capital_flow as acf  # noqa: E402  复用当日资金流的三榜解析口径

CF_DIR = os.path.join(BASE, "collect", "raw_capflow")
BRIDGE = os.path.join(BASE, "collect", "score_bridge.js")
ROWS_WINDOW = 130          # 打分最多用到近 61 根（噪音带），留足冗余
BATCH = 6                  # 每次 Node 桥接处理的品种数，控制 stdin 体积


def _norm(d):
    return str(d or "").replace("-", "")


def cf_path(code):
    return os.path.join(CF_DIR, f"capflow_{code}.json")


def cache_path(code):
    return os.path.join(CF_DIR, f"scoreinput_{code}.json")


def load_json(path, default=None):
    if not os.path.isfile(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def dump_json(path, obj, compact=True):
    with open(path, "w", encoding="utf-8") as f:
        if compact:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(obj, f, ensure_ascii=False, indent=2)


def resolve_snap_date():
    """优先环境变量，其次 web 刷新状态，最后取 data/ 下最新快照日期。"""
    d = os.environ.get("WB_SNAP_DATE")
    if d:
        return d
    st = load_json(os.path.join(BASE, "web", "refresh_status.json"), {}) or {}
    if st.get("snap_date"):
        return st["snap_date"]
    ds = sorted({os.path.basename(p)[:-5]
                 for p in glob.glob(os.path.join(BASE, "data", "*", "*", "*.json"))})
    return ds[-1] if ds else ""


def load_kline_rows(code, snap_date):
    """第⑦部分行情窗口。全月份口径下：价格取主力/主连（不同月份价格不可加总），
    量 / 仓取全部月份合计；严格按同一交易日 join，无同日数据即跳过，绝不跨日兜底。
    与 build_all.all_months_rows 生成的是同一份序列。"""
    hits = glob.glob(os.path.join(BASE, "data", "*", code, snap_date + ".json"))
    if not hits:
        return []
    d = load_json(hits[0], {}) or {}
    rows = ((d.get("kline") or {}).get("rows")) or []
    am = load_json(os.path.join(CF_DIR, f"allmonths_{code}.json"))
    series = {}
    if am and (am.get("totals") or {}).get("series"):
        series = {_norm(p.get("date")): p for p in am["totals"]["series"]}
    out = []
    for r in rows:
        if not isinstance(r, dict) or not r.get("date"):
            continue
        dt = _norm(r["date"])
        rec = dict(r, date=dt)
        s = series.get(dt)
        if s:
            rec["oi"] = s.get("hold")
            rec["oi_chg"] = s.get("chg")
            if s.get("volume") is not None:
                rec["volume"] = s["volume"]
        elif series:
            continue
        out.append(rec)
    return out


def price_by_date(rows):
    """合成日线 → {日期: {close, chg_pct}}，供 L4 曲线叠加价格对照。

    合成日线的「量 / 仓」为全月份合计，价格字段仍取主力 / 主连收盘价
    （不同月份价格不可加总）。严格按同一交易日对齐（与打分用的行情窗口同日），
    缺失就不写，绝不用前一交易日的价格顶替，避免跨日错配。
    """
    out = {}
    for r in rows:
        d = _norm(r.get("date"))
        if not d or r.get("close") in (None, ""):
            continue
        try:
            close = round(float(r["close"]), 4)
        except (TypeError, ValueError):
            continue
        chg = r.get("chg_pct")
        try:
            chg = round(float(chg), 4) if chg not in (None, "") else None
        except (TypeError, ValueError):
            chg = None
        out[d] = {"close": close, "chg_pct": chg}
    return out


def fetch_day_inputs(contract, date):
    """复原某交易日的完整打分输入；多单/空单榜缺一则视为失败。"""
    ldf = acf._fetch_hold_pos(contract, date, "多单持仓")
    sdf = acf._fetch_hold_pos(contract, date, "空单持仓")
    if ldf is None or sdf is None:
        return None
    vdf = acf._fetch_hold_pos(contract, date, "成交量")
    members = acf._build_member_table(ldf, sdf, vdf)
    tiers, iv, n = acf._tier_stats(members)
    large = acf._large_orders(members)
    return {
        "date": date, "contract": contract, "member_count": n,
        "tiers": tiers, "institutional_vs_retail": iv, "large_orders": large,
        # L4 打分不读 top_members，但保留以便后续口径扩展时无需重抓
        "top_members": [{"name": m["name"], "net": round(m["net"]),
                         "net_chg": round(m["net_chg"]), "long": round(m["long"]),
                         "short": round(m["short"]), "long_chg": round(m["long_chg"]),
                         "short_chg": round(m["short_chg"])} for m in members],
    }


def collect_inputs(code, cf, dates, force, delay):
    """逐日抓取并缓存打分输入，返回 (data 字典, 新增点数)。
    全月份口径下逐日截面已在 allmonths_<CODE>.json 落盘，直接复用、不再联网重抓。"""
    am = load_json(os.path.join(CF_DIR, f"allmonths_{code}.json"))
    if am and am.get("data"):
        data = {}
        for d in dates:
            sec = (am["data"] or {}).get(d)
            if not sec:
                continue
            data[d] = {k: sec.get(k) for k in
                       ("date", "contract", "member_count", "tiers",
                        "institutional_vs_retail", "large_orders", "top_members")}
        cache = {"code": code, "data": dict(sorted(data.items())),
                 "contract": am.get("main_contract"), "as_of": am.get("as_of"),
                 "scope": "all_months"}
        dump_json(cache_path(code), cache)
        return cache["data"], 0
    cache = load_json(cache_path(code), {"code": code, "data": {}}) or {"code": code, "data": {}}
    data = cache.setdefault("data", {})
    contract = cf.get("contract")
    added = 0
    for d in dates:
        if not force and d in data and data[d].get("tiers"):
            continue
        rec = fetch_day_inputs(contract, d)
        if rec:
            data[d] = rec
            added += 1
        if delay:
            import time
            time.sleep(delay)
    cache["contract"] = contract
    cache["as_of"] = cf.get("as_of")
    cache["data"] = dict(sorted(data.items()))
    dump_json(cache_path(code), cache)
    return cache["data"], added


def run_bridge(tasks):
    payload = json.dumps({"tasks": tasks}, ensure_ascii=False, separators=(",", ":"))
    p = subprocess.run(["node", BRIDGE], input=payload, cwd=BASE,
                       capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        raise RuntimeError(f"score_bridge 失败 rc={p.returncode}: {(p.stderr or '')[:400]}")
    return json.loads(p.stdout)["results"]


def build_tasks(code, day_inputs, rows, default_contract=""):
    """截止每个交易日的行情窗口 + 当日榜单截面。

    contract 必须传入：L4 移仓识别需要按合约代码推算交割月，
    页面快照的 capital_flow 自带该字段，历史回溯则取逐日缓存里的合约。
    """
    tasks = []
    for d, sec in day_inputs.items():
        upto = [r for r in rows if r["date"] <= d][-ROWS_WINDOW:]
        if len(upto) < 25:
            continue
        tasks.append({
            "code": code, "date": d,
            "s": {"kline": {"rows": upto}},
            "cf": {"tiers": sec.get("tiers") or [],
                   "institutional_vs_retail": sec.get("institutional_vs_retail") or {},
                   "large_orders": sec.get("large_orders") or {},
                   "top_members": sec.get("top_members") or [],
                   "contract": sec.get("contract") or default_contract},
        })
    return tasks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", nargs="*", default=None, help="只处理指定品种代码")
    ap.add_argument("--workers", type=int, default=4, help="并发抓取品种数")
    ap.add_argument("--batch", type=int, default=BATCH, help="每次桥接的品种数")
    ap.add_argument("--delay", type=float, default=0.03, help="单次请求后的间隔秒数")
    ap.add_argument("--force", action="store_true", help="忽略逐日缓存、全部重抓")
    ap.add_argument("--snap-date", default=None, help="快照日期（默认自动探测）")
    args = ap.parse_args()

    snap = args.snap_date or resolve_snap_date()
    if args.codes:
        codes = [c.upper() for c in args.codes]
    else:
        codes = []
        for fn in sorted(os.listdir(CF_DIR)):
            if not (fn.startswith("capflow_") and fn.endswith(".json")):
                continue
            cf = load_json(os.path.join(CF_DIR, fn))
            if cf and cf.get("available"):
                codes.append(fn[len("capflow_"):-len(".json")])
    print(f"[L4历史读数] 快照 {snap} | 目标品种 {len(codes)} 个 | "
          f"并发 {args.workers} | force={args.force}", flush=True)

    # 1) 逐日重抓三榜明细
    prepared = {}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {}
        for code in codes:
            cf = load_json(cf_path(code), {}) or {}
            nh = cf.get("net_history") or []
            dates = [_norm(p.get("date")) for p in nh if p.get("date")]
            if not cf.get("available") or not cf.get("contract") or len(dates) < 2:
                print(f"  {code:4} [SKIP] 无资金数据或缺 net_history", flush=True)
                continue
            rows = load_kline_rows(code, snap)
            if len(rows) < 25:
                print(f"  {code:4} [SKIP] 快照 kline 不足（{len(rows)} 根）", flush=True)
                continue
            futs[ex.submit(collect_inputs, code, cf, dates, args.force, args.delay)] = (code, cf, rows)
        for fu in as_completed(futs):
            code, cf, rows = futs[fu]
            try:
                data, added = fu.result()
            except Exception as e:
                print(f"  {code:4} [FAIL] 抓榜异常 {e}", flush=True)
                continue
            prepared[code] = {"cf": cf, "rows": rows, "inputs": data}
            print(f"  {code:4} 明细 {len(data)} 天（新增 {added}）", flush=True)

    # 2) 组装任务，按品种分批交给 Node 桥接算分
    all_codes = sorted(prepared)
    results = {}
    for i in range(0, len(all_codes), args.batch):
        batch = all_codes[i:i + args.batch]
        tasks = []
        for code in batch:
            tasks.extend(build_tasks(code, prepared[code]["inputs"], prepared[code]["rows"],
                                     (prepared[code]["cf"] or {}).get("contract") or ""))
        try:
            out = run_bridge(tasks)
        except Exception as e:
            print(f"  [FAIL] 桥接批次 {batch}: {e}", flush=True)
            continue
        for r in out:
            if r.get("error"):
                print(f"  [WARN] {r.get('code')} {r.get('date')} 打分异常: {r['error']}", flush=True)
                continue
            results.setdefault(r["code"], {})[r["date"]] = r
        print(f"  桥接 {i + 1}-{i + len(batch)}/{len(all_codes)}: {len(tasks)} 个交易日", flush=True)

    # 3) 写回 capflow_<CODE>.json
    note = (
        "近1月 L4「四维共振读数」逐日回溯：数据源为品种全部挂牌月份合约，"
        "每个交易日把全部有榜单月份的多单/空单/成交量三榜按会员跨月累加合并，"
        "复原当日四档 tiers / 前5席位 / 大单动向；并取截止该交易日的合成行情"
        "（价格为主力/主连收盘价，量仓为全月份合计）计算价量仓、量能与自适应噪音带；"
        "分数由页面同一份 capFlowAnalytics 公式计算（Node 桥接），与当日读数口径完全一致，不使用未来数据。"
        "权重：价·量·仓配合 35% / 前5席位净变化 25% / 分层一致性 20% / 大单动向 20%；"
        "50 中性，≥58 偏多、≤42 偏空。每个点同时附上同一交易日主连收盘价与当日涨跌幅，仅作价格对照（右轴、独立刻度），不参与打分。"
        "临近交割移仓窗口按「距交割月 ≤45 天 + 近5日总持仓连续下降 ≥3 天且降幅 ≥3% + 多空席位双边同步减仓」识别，"
        "落盘 rollover（距交割月天数 / 近5日持仓变化 / 连续下降天数 / 双边减仓）与 score_adj 校正参考读数；"
        "校正仅按 fade 向中性 50 收敛，不改原四维打分公式，历史读数口径保持稳定。"
        "分母已覆盖全部月份，读数不再受主力换月跳变影响；交易所仅对部分活跃月份公布会员排名，"
        "榜单覆盖月份数因品种而异；不同月份价格不可加总，价格对照曲线仍取主力/主连收盘价。"
        "读数仅供横向比较，非交易信号。")
    ok, bad = [], []
    for code in all_codes:
        rs = results.get(code) or {}
        hist = [rs[d] for d in sorted(rs)]
        if len(hist) < 2:
            bad.append((code, len(hist)))
            continue
        px = price_by_date(prepared[code]["rows"])
        for r in hist:
            r["contract"] = (prepared[code]["inputs"].get(r["date"]) or {}).get("contract")
            p = px.get(_norm(r.get("date"))) or {}
            r["close"] = p.get("close")
            r["chg_pct"] = p.get("chg_pct")
        cf = prepared[code]["cf"]
        cf["score_history"] = hist
        cf["score_history_note"] = note
        dump_json(cf_path(code), cf, compact=False)
        ok.append((code, len(hist), hist[-1]["score"], hist[-1]["label"]))

    print(f"\n[汇总] 写入 {len(ok)}/{len(all_codes)} 品种；失败 {bad}", flush=True)
    px_hit = px_all = roll_days = 0
    for code, n, _, _ in ok:
        hist = (load_json(cf_path(code), {}) or {}).get("score_history") or []
        px_all += len(hist)
        px_hit += sum(1 for r in hist if isinstance(r.get("close"), (int, float)))
        roll_days += sum(1 for r in hist if r.get("rollover"))
    print(f"[价格对照] {px_hit}/{px_all} 个交易日已按同日主连收盘价对齐", flush=True)
    print(f"[移仓识别] {roll_days}/{px_all} 个交易日落在临近交割自然减仓窗口", flush=True)
    for code, n, sc, label in ok[:8]:
        print(f"  {code:4} {n:2} 点  末日读数 {sc:3} {label}", flush=True)
    if len(ok) > 8:
        print(f"  ... 其余 {len(ok) - 8} 个品种略", flush=True)
    if bad:
        print(f"[提示] 未写入品种: {bad}", flush=True)


if __name__ == "__main__":
    main()
