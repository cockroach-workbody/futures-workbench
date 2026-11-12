#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三榜合一 · 近一月「合计净持仓」历史曲线采集器

背景：⑦ 资金动向原「前20会员近1月净持仓变化」曲线来自 raw_hist/agg_<CODE>.json，
口径是「前20会员 · 全合约汇总」，且只覆盖 36 个品种（SHFE 12 + CZCE 11 + DCE 13），
缺广期所 LC / PS / SI。与「三榜合一总持仓占比」所用的「当前主力合约 · 三榜合并席位」
口径不一致（多数品种数值差近一个数量级），无法与当日资金结构直接对照。

本脚本按三榜合一口径重算近一月序列，覆盖全部 available=True 的品种（当前 39 个）：
  - 交易日：取 capital_flow.total_position.series 中 <= as_of 的最近 N 个交易日
  - 每个交易日抓 多单持仓 / 空单持仓 / 成交量 三榜（AKShare 新浪镜像）
  - 合计净持仓 = 多单榜前20「持买合计」 − 空单榜前20「持卖合计」
      （即三榜按会员简称合并去重后全部席位的净持仓之和；只在成交量榜出现的席位
       long / short 记 0，对分子零增量 —— 与 calcSeatShare 的分子口径完全一致）
  - seat_count = 三榜合并去重后的席位总数，随曲线一起落盘供前端提示
  - 逐日两条占比（分子分母同日，分母取 total_position.series 当日主连总持仓 hold）：
      seat_share = (多单榜持买 + 空单榜持卖) / (2 × hold) × 100
          = 「三榜合一总持仓占比」的逐日展开，衡量三榜席位对全市场持仓的覆盖率；
      net_share  = 合计净持仓 / hold × 100
          = 三榜席位整体净多 / 净空相对全市场规模，剔除合约规模与换月导致的量级变化。

产物：
  collect/raw_capflow/hist_<CODE>.json   逐日缓存（幂等，已采集日跳过）
  collect/raw_capflow/capflow_<CODE>.json 新增 net_history 字段（按日期升序）
  build_all.py 原样透传 capital_flow，net_history 自动进入各品种快照。

调用：
  python collect/capflow_net_history.py                      # 全部有资金数据品种
  python collect/capflow_net_history.py --codes JD LC PS SI  # 指定品种（试跑）
  python collect/capflow_net_history.py --days 22 --workers 6
  python collect/capflow_net_history.py --force              # 忽略缓存重抓
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import akshare as ak

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CF_DIR = os.path.join(BASE, "collect", "raw_capflow")

BOARDS = {
    "long": ("多单持仓", "多单持仓"),
    "short": ("空单持仓", "空单持仓"),
    "volume": ("成交量", "成交量"),
}
SKIP_NAMES = {"合计", "小计", "总计", "名次", ""}
DEFAULT_DAYS = 22


def _norm(d):
    return (d or "").replace("-", "")


def cf_path(code):
    return os.path.join(CF_DIR, f"capflow_{code}.json")


def hist_path(code):
    return os.path.join(CF_DIR, f"hist_{code}.json")


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


def trade_dates(cf, days, as_of):
    """近一月交易日：来自主连日线 total_position.series，统一 8 位 YYYYMMDD。"""
    series = (cf.get("total_position") or {}).get("series") or []
    a = _norm(as_of)
    ds = sorted({_norm(p.get("date")) for p in series
                 if p.get("date") and _norm(p.get("date")) <= a})
    return ds[-days:] if days > 0 else ds


def oi_by_date(cf):
    """主连日线每日总持仓（分母）：{YYYYMMDD: hold}。与榜单交易日严格同日对齐。"""
    series = (cf.get("total_position") or {}).get("series") or []
    out = {}
    for p in series:
        d = _norm(p.get("date"))
        try:
            hold = float(p.get("hold") or 0)
        except (TypeError, ValueError):
            continue
        if d and hold > 0:
            out[d] = hold
    return out


def enrich(hist, cf):
    """为逐日曲线补 total_oi / seat_share / net_share；分母缺失则该点占比为 null。"""
    oi = oi_by_date(cf)
    out = []
    for rec in hist:
        r = dict(rec)
        hold = oi.get(_norm(r.get("date")))
        if hold and hold > 0 and r.get("long_total") is not None and r.get("short_total") is not None:
            r["total_oi"] = int(hold)
            # 与前端 calcSeatShare 一致：多空两侧分别相除再取均值
            r["seat_share"] = round((r["long_total"] + r["short_total"]) / (2 * hold) * 100, 4)
            r["net_share"] = round(r["net"] / hold * 100, 4)
        else:
            r["total_oi"] = None
            r["seat_share"] = None
            r["net_share"] = None
        out.append(r)
    return out


def fetch_board(contract, date, sym, val_col):
    """抓单张榜单，返回 (合计值, 增减合计, 会员名集合)；失败返回 (None, None, set())。"""
    try:
        df = ak.futures_hold_pos_sina(symbol=sym, contract=contract, date=date)
    except Exception:
        return None, None, set()
    if df is None or len(df) == 0:
        return None, None, set()
    total = chg = 0.0
    names = set()
    for _, r in df.iterrows():
        name = str(r.get("会员简称") or "").strip()
        if name in SKIP_NAMES:
            continue
        names.add(name)
        try:
            total += float(r.get(val_col) or 0)
        except Exception:
            pass
        try:
            chg += float(r.get("比上交易增减") or 0)
        except Exception:
            pass
    return total, chg, names


def fetch_day(contract, date):
    """某交易日三榜合一合计净持仓。多单/空单榜缺一则视为失败（成交量榜可选）。"""
    lt, lchg, n_long = fetch_board(contract, date, *BOARDS["long"])
    st, schg, n_short = fetch_board(contract, date, *BOARDS["short"])
    if lt is None or st is None:
        return None
    _, _, n_vol = fetch_board(contract, date, *BOARDS["volume"])
    net = int(round(lt - st))
    lchg_i = int(round(lchg or 0))
    schg_i = int(round(schg or 0))
    return {
        "date": date,
        "net": net,
        "long_total": int(round(lt)),
        "short_total": int(round(st)),
        "long_chg": lchg_i,
        "short_chg": schg_i,
        "net_chg": lchg_i - schg_i,
        "seat_count": len(n_long | n_short | n_vol),
        "contract": contract,
    }


def allmonths_path(code):
    return os.path.join(CF_DIR, f"allmonths_{code}.json")


def run_from_all_months(code, days, am):
    """数据源 = 品种全部月份合约：逐日全月份三榜合并截面已落盘，直接生成曲线，无需重抓。
    分母取全月份合计持仓，分子取全部有榜单月份的三榜持买/持卖，严格同日。"""
    cf = load_json(cf_path(code)) or {}
    data = am.get("data") or {}
    keys = sorted(data)
    dates = keys[-days:] if days > 0 else keys
    if len(dates) < 2:
        return {"code": code, "ok": False, "msg": f"only {len(dates)} all-months point(s)"}
    raw = []
    for d in dates:
        sec = data[d]
        L = float(sec.get("long_total") or 0)
        S = float(sec.get("short_total") or 0)
        lchg = sum(float(m.get("long_chg") or 0) for m in sec.get("top_members") or [])
        schg = sum(float(m.get("short_chg") or 0) for m in sec.get("top_members") or [])
        raw.append({
            "date": d, "net": int(round(L - S)),
            "long_total": int(round(L)), "short_total": int(round(S)),
            "long_chg": int(round(lchg)), "short_chg": int(round(schg)),
            "net_chg": int(round(lchg - schg)),
            "seat_count": sec.get("member_count"),
            "contract": sec.get("contract") or am.get("main_contract"),
            "months_with_rank": sec.get("months_with_rank") or [],
        })
    hist = enrich(raw, {"total_position": {"series": am.get("totals", {}).get("series") or []}})
    dump_json(hist_path(code), {
        "code": code, "data": {r["date"]: r for r in raw},
        "as_of": am.get("as_of"), "contract": am.get("main_contract"),
        "scope": "all_months",
    })
    cf["net_history"] = hist
    cf["net_history_note"] = (
        "数据源为品种全部挂牌月份合约：合计净持仓 = 全部有榜单月份的多单榜持买合计 − 空单榜持卖合计，"
        "会员席位按三榜跨月同名累加合并后去重。占总持仓占比 = (持买+持卖)/(2×当日全月份合计持仓)，"
        "净持仓占比 = 合计净持仓/当日全月份合计持仓；两条占比分子分母严格同日。"
        "分母已覆盖全部月份，故不再有主力换月造成的持仓跳变；交易所仅对部分活跃合约公布会员排名，"
        "占比为相对全品种总持仓的下限。价格不可加总，价格对照曲线仍取主力/主连收盘价。")
    dump_json(cf_path(code), cf, compact=False)

    last = hist[-1]
    sec = data[dates[-1]]
    member_net = round(sum(float(m.get("net") or 0) for m in sec.get("top_members") or []))
    return {"code": code, "ok": True, "added": 0, "points": len(hist),
            "first": hist[0]["date"], "last": last["date"],
            "last_net": last["net"], "cf_net": member_net,
            "diff": int(last["net"] - member_net), "seats": last.get("seat_count"),
            "oi_points": sum(1 for p in hist if p.get("total_oi")),
            "seat_share": last.get("seat_share"), "net_share": last.get("net_share")}


def run_one(code, days, force, delay):
    am = load_json(allmonths_path(code))
    if am and am.get("data") and (am.get("totals") or {}).get("series"):
        return run_from_all_months(code, days, am)
    cf = load_json(cf_path(code))
    if not cf or not cf.get("available"):
        return {"code": code, "ok": False, "msg": "no capital_flow data"}
    contract = cf.get("contract")
    as_of = cf.get("as_of") or ""
    if not contract:
        return {"code": code, "ok": False, "msg": "no contract"}
    dates = trade_dates(cf, days, as_of)
    if not dates:
        return {"code": code, "ok": False, "msg": "no trade dates"}

    cache = load_json(hist_path(code), {"code": code, "data": {}}) or {"code": code, "data": {}}
    data = cache.setdefault("data", {})
    added = 0
    for d in dates:
        if not force and d in data and data[d].get("net") is not None:
            continue
        rec = fetch_day(contract, d)
        if rec:
            data[d] = rec
            added += 1
        if delay:
            time.sleep(delay)

    cache["contract"] = contract
    cache["as_of"] = as_of
    cache["data"] = dict(sorted(data.items()))
    dump_json(hist_path(code), cache)

    # net_history 只取本次窗口内的交易日（按日期升序）；逐日补两条占比（纯计算，无需重抓榜单）
    window = set(dates)
    raw = [cache["data"][d] for d in sorted(cache["data"]) if d in window]
    if len(raw) < 2:
        return {"code": code, "ok": False, "msg": f"only {len(raw)} history point(s)",
                "added": added}
    hist = enrich(raw, cf)
    cf["net_history"] = hist
    cf["net_history_note"] = (
        "三榜合一合计净持仓 = 多单榜前20持买合计 − 空单榜前20持卖合计；"
        "席位集合为多单/空单/成交量三榜合并去重，只在成交量榜出现的席位不计持仓、对分子零增量。"
        "占总持仓占比 = (持买+持卖)/(2×当日主连总持仓)，衡量三榜席位覆盖率；"
        "净持仓占比 = 合计净持仓/当日主连总持仓，正负代表三榜席位整体净多/净空。"
        "两条占比的分子分母严格同日，按当前主力合约逐日回溯，换月前后规模或有跳变。")
    dump_json(cf_path(code), cf, compact=False)

    # 自检：最新交易日 net 应与当日 institutional_vs_retail 两组合计净持仓一致
    iv = cf.get("institutional_vs_retail") or {}
    ins, ret = iv.get("institutional") or {}, iv.get("retail") or {}
    cf_net = ((ins.get("long") or 0) + (ret.get("long") or 0)) - \
             ((ins.get("short") or 0) + (ret.get("short") or 0))
    last = hist[-1]
    return {"code": code, "ok": True, "added": added, "points": len(hist),
            "first": hist[0]["date"], "last": last["date"],
            "last_net": last["net"], "cf_net": int(cf_net),
            "diff": int(last["net"] - cf_net), "seats": last.get("seat_count"),
            "oi_points": sum(1 for p in hist if p.get("total_oi")),
            "seat_share": last.get("seat_share"), "net_share": last.get("net_share")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", nargs="*", default=None, help="只处理指定品种代码")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS, help="近 N 个交易日（默认 22，约一月）")
    ap.add_argument("--workers", type=int, default=6, help="并发品种数（默认 6）")
    ap.add_argument("--delay", type=float, default=0.03, help="单次请求后的间隔秒数")
    ap.add_argument("--force", action="store_true", help="忽略逐日缓存、全部重抓")
    args = ap.parse_args()

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
    print(f"[三榜合一历史] 目标品种 {len(codes)} 个，窗口 {args.days} 个交易日，"
          f"并发 {args.workers}，force={args.force}", flush=True)

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(run_one, c, args.days, args.force, args.delay): c for c in codes}
        for fu in as_completed(futs):
            try:
                r = fu.result()
            except Exception as e:  # noqa
                r = {"code": futs[fu], "ok": False, "msg": f"EXC {e}"}
            results.append(r)
            if r.get("ok"):
                ss, ns = r.get("seat_share"), r.get("net_share")
                print(f"  {r['code']:4} {r['points']:2} pts  {r['first']}..{r['last']}  "
                      f"net={r['last_net']:>10}  校验差={r['diff']:>7}  席位={r['seats']}  "
                      f"分母点={r.get('oi_points')}/{r['points']}  "
                      f"占比={ss if ss is not None else '-'}% / {ns if ns is not None else '-'}%  "
                      f"新增={r['added']}", flush=True)
            else:
                print(f"  {r['code']:4} [FAIL] {r.get('msg')}", flush=True)

    ok = [r for r in results if r.get("ok")]
    bad = [r for r in results if not r.get("ok")]
    exact = sum(1 for r in ok if r["diff"] == 0)
    print(f"\n[汇总] 成功 {len(ok)}/{len(results)} 品种；最新日与当日资金结构净持仓"
          f"完全对齐 {exact}/{len(ok)}；失败 {len(bad)}"
          + (f" -> {[r['code'] for r in bad]}" if bad else ""), flush=True)
    if len(ok) < len(codes):
        print("[提示] 失败品种会在 build_all 后由前端回退到旧 top20.history 曲线。", flush=True)


if __name__ == "__main__":
    main()
