#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第⑦部分「资金分析框架」——品种全部月份合约数据源采集器

背景：旧口径只取「单一主力合约」的三榜会员排名 + 主连日线 hold。
主连 hold 实际等于主力合约持仓，换月时会跳变，且主力临近交割的
自然减仓无法与「资金真正离场」区分。本采集器把数据源换成
**该品种全部挂牌月份合约**：

  - 月份枚举：从快照月起向前 2 个月、向后 15 个月，逐月探测日线
    （六大交易所统一 4 位 YYMM，郑商所亦用 FG2701 而非 FG701）
  - 日线：ak.futures_zh_daily_sina 一次返回单合约全历史
    → 逐日各月 hold / volume / close，合成「全月份合计」序列
  - 三榜：新浪 vFutures_Positions_cjcc.php 一次请求返回
    成交量榜 / 多单榜 / 空单榜三张表；按交易日、按持仓量靠前的
    月份逐个抓取，跨月按会员名累加合并
  - 价格：不同月份价格不可加总，故「价」仍用主力/主连收盘价；
    「量」「仓」用全月份合计。口径在产物 note 中固定说明。

产物（每品种一份，幂等、可断点续传）：
  collect/raw_capflow/allmonths_<CODE>.json
    {
      code, as_of, source, main_contract, note,
      months: [ 当日各月份合约的 hold / volume / close / 是否有榜单 ],
      daily:  [ 逐交易日全月份合计持仓、成交、主力价格、各月持仓 ],
      totals: { latest, chg_1d, chg_5d, chg_20d, series[] },
      data:   { "YYYYMMDD": 逐日全月份三榜合并截面（供 L4 打分 / 净持仓曲线） }
    }
  collect/raw_capflow/amdaily/<CODE>.json      月份日线缓存
  collect/raw_capflow/amrank/<CODE>/<DATE>.json 逐日逐月三榜缓存

调用：
  python collect/capflow_all_months.py --codes JD            # 单品种试跑
  python collect/capflow_all_months.py --workers 10          # 全量（首次回填约 5 分钟）
  python collect/capflow_all_months.py --days 22 --workers 10
  python collect/capflow_all_months.py --force-rank          # 忽略三榜缓存重抓
"""
import argparse
import datetime
import glob
import json
import os
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import StringIO

warnings.filterwarnings("ignore")

import pandas as pd
import requests
import akshare as ak

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "collect"))
import akshare_capital_flow as acf  # noqa: E402  复用分层 / 大单统计，避免口径漂移
from build_all import PRODUCTS, SNAP_DATE  # noqa: E402

CF_DIR = os.path.join(BASE, "collect", "raw_capflow")
DAILY_DIR = os.path.join(CF_DIR, "amdaily")
RANK_DIR = os.path.join(CF_DIR, "amrank")
for _d in (CF_DIR, DAILY_DIR, RANK_DIR):
    os.makedirs(_d, exist_ok=True)

SINA_URL = "https://vip.stock.finance.sina.com.cn/q/view/vFutures_Positions_cjcc.php"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
MONTHS_BACK = 2
MONTHS_FWD = 15
KEEP_DAYS = 130        # 覆盖 L1 噪音带 60 日 + 近1月 22 交易日
RANK_TOPN = 8          # 每日按持仓量取前 N 个月份尝试抓三榜
DEFAULT_DAYS = 22      # 与 net_history / score_history 天数一致

NOTE = (
    "数据源为该品种全部挂牌月份合约，而非单一主力。持仓量与成交量按全部月份合计，"
    "已消除主力换月造成的持仓跳变；会员席位按三榜（多单/空单/成交量）跨月同名累加合并后重新分档。"
    "不同月份合约价格不可加总，故价格仍取主力/主连收盘价，仅「量」「仓」为全月份合计。"
    "交易所仅对部分活跃合约公布会员持仓排名，故榜单截面覆盖的月份数少于全部月份，"
    "三榜合一占比为相对全品种总持仓的下限值。分子分母严格取同一交易日。"
)


def _norm(d):
    return str(d or "").replace("-", "")[:8]


def load_json(path, default=None):
    if not os.path.isfile(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def dump_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def out_path(code):
    return os.path.join(CF_DIR, f"allmonths_{code}.json")


def daily_path(code):
    return os.path.join(DAILY_DIR, f"{code}.json")


def rank_path(code, date):
    d = os.path.join(RANK_DIR, code)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{date}.json")


def candidate_symbols(code, as_of):
    """从快照当月向前 2 个月、向后 15 个月枚举 YYYYMM 合约代码。"""
    y0, m0 = int(as_of[:4]), int(as_of[5:7])
    out = []
    for i in range(-MONTHS_BACK, MONTHS_FWD + 1):
        y = y0 + (m0 - 1 + i) // 12
        m = (m0 - 1 + i) % 12 + 1
        out.append(f"{code.upper()}{y % 100:02d}{m:02d}")
    return out


def snap_kline(code, as_of):
    hits = glob.glob(os.path.join(BASE, "data", "*", code, as_of + ".json"))
    if not hits:
        return []
    d = load_json(hits[0], {}) or {}
    rows = ((d.get("kline") or {}).get("rows")) or []
    out = []
    for r in rows:
        if isinstance(r, dict) and r.get("date"):
            out.append(dict(r, date=_norm(r["date"])))
    return out


def trade_dates(code, as_of, days):
    rows = snap_kline(code, as_of)
    return [r["date"] for r in rows[-days:] if r.get("date")]


def main_contract_of(code, as_of):
    cf = load_json(os.path.join(CF_DIR, f"capflow_{code}.json"), {}) or {}
    mc = str(cf.get("contract") or "")
    return mc.upper()


# ---------------------------------------------------------------- 月份日线

def fetch_daily_cache(code, as_of, force=False):
    """逐月合约日线 → {SYM: {YYYYMMDD: {hold, volume, close, ...}}}"""
    p = daily_path(code)
    cache = load_json(p)
    if cache and cache.get("as_of") == as_of and cache.get("months") and not force:
        return cache["months"]
    as_of8 = _norm(as_of)
    months = {}
    for sym in candidate_symbols(code, as_of):
        try:
            df = ak.futures_zh_daily_sina(symbol=sym)
        except Exception:
            continue
        if df is None or df.empty or "hold" not in df.columns:
            continue
        try:
            df = df.copy()
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y%m%d")
            df = df[df["date"] <= as_of8].tail(KEEP_DAYS)
        except Exception:
            continue
        rec = {}
        for _, r in df.iterrows():
            try:
                rec[str(r["date"])] = {
                    "hold": int(pd.to_numeric(r.get("hold"), errors="coerce") or 0),
                    "volume": int(pd.to_numeric(r.get("volume"), errors="coerce") or 0),
                    "close": round(float(pd.to_numeric(r.get("close"), errors="coerce") or 0), 4),
                }
            except Exception:
                continue
        if rec:
            months[sym] = rec
    dump_json(p, {"code": code, "as_of": as_of, "months": months})
    return months


# ---------------------------------------------------------------- 三榜（单次请求拿三表）

def _rows_of(table, val_col):
    out = []
    if table is None or not hasattr(table, "iterrows"):
        return out
    for _, x in table.iterrows():
        name = str(x.get("会员简称", "") or "").strip()
        if not name or name in ("合计", "小计", "名次", "nan", "None"):
            continue
        try:
            val = float(pd.to_numeric(x.get(val_col), errors="coerce") or 0)
        except Exception:
            val = 0.0
        try:
            chg = float(pd.to_numeric(x.get("比上交易增减"), errors="coerce") or 0)
        except Exception:
            chg = 0.0
        out.append({"name": name, "val": val, "chg": chg})
    return out


def fetch_rank_one(sym, date):
    """单个合约单日，一次请求返回三张榜。空榜标记 empty 并缓存，避免反复请求。"""
    d8 = _norm(date)
    params = {"t_breed": sym.lower(), "t_date": f"{d8[:4]}-{d8[4:6]}-{d8[6:]}"}
    for attempt in range(2):
        try:
            r = requests.get(SINA_URL, params=params, headers=HEADERS, timeout=25)
            if r.status_code != 200:
                time.sleep(0.4)
                continue
            tables = pd.read_html(StringIO(r.text))
            if len(tables) < 5:
                return {"empty": True}
            return {
                "long": _rows_of(tables[3], "多单持仓"),
                "short": _rows_of(tables[4], "空单持仓"),
                "vol": _rows_of(tables[2], "成交量"),
            }
        except Exception:
            time.sleep(0.4)
    return None


def fetch_rank_day(code, date, syms, force=False, delay=0.02):
    """某品种某交易日：按候选月份逐个抓三榜，结果按日缓存、可断点续传。"""
    p = rank_path(code, date)
    cache = load_json(p, {}) or {}
    need = [s for s in syms if force or s not in cache]
    for s in need:
        rec = fetch_rank_one(s, date)
        if rec is not None:
            cache[s] = rec
            if delay:
                time.sleep(delay)
    if need:
        dump_json(p, cache)
    return cache


# ---------------------------------------------------------------- 跨月合并

def merge_members(day_ranks):
    """同一会员在不同月份的多/空/成交量累加，得到品种级会员截面。"""
    members = {}
    zero = {"long": 0.0, "long_chg": 0.0, "short": 0.0, "short_chg": 0.0,
            "volume": 0.0, "vol_chg": 0.0}
    for sym, tbl in (day_ranks or {}).items():
        if not isinstance(tbl, dict) or tbl.get("empty"):
            continue
        for key, val_f, chg_f in (("long", "long", "long_chg"),
                                  ("short", "short", "short_chg"),
                                  ("vol", "volume", "vol_chg")):
            for r in tbl.get(key) or []:
                name = r.get("name")
                if not name:
                    continue
                m = members.setdefault(name, dict(zero, name=name))
                m[val_f] += r.get("val") or 0
                m[chg_f] += r.get("chg") or 0
    for m in members.values():
        m["net"] = m["long"] - m["short"]
        m["net_chg"] = m["long_chg"] - m["short_chg"]
    return list(members.values())


def build_day_section(date, day_ranks, daily_rec):
    members = merge_members(day_ranks)
    if not members:
        return None
    tiers, iv, n = acf._tier_stats(members)
    large = acf._large_orders(members)
    long_total = round(sum(m["long"] for m in members))
    short_total = round(sum(m["short"] for m in members))
    with_rank = [s for s, t in (day_ranks or {}).items()
                 if isinstance(t, dict) and not t.get("empty") and (t.get("long") or t.get("short"))]
    return {
        "date": date,
        "contract": (daily_rec or {}).get("main_contract", ""),
        "member_count": n,
        "tiers": tiers,
        "institutional_vs_retail": iv,
        "large_orders": large,
        "top_members": [{"name": m["name"], "net": round(m["net"]),
                         "net_chg": round(m["net_chg"]), "long": round(m["long"]),
                         "short": round(m["short"]), "long_chg": round(m["long_chg"]),
                         "short_chg": round(m["short_chg"])} for m in members],
        "long_total": long_total,
        "short_total": short_total,
        "months_with_rank": sorted(with_rank),
    }


# ---------------------------------------------------------------- 单品种汇总

def daily_of(months_daily, kline_by_date, date, main_sym, main_contract):
    """合成某交易日的全月份行情：价格取主力/主连，量仓取全月份合计。"""
    per = {}
    for sym, rec in (months_daily or {}).items():
        v = rec.get(date)
        if v:
            per[sym] = v
    total_hold = sum(v["hold"] for v in per.values())
    total_volume = sum(v["volume"] for v in per.values())
    k = kline_by_date.get(date) or {}
    return {
        "date": date,
        "total_hold": total_hold,
        "total_volume": total_volume or (k.get("volume") or 0),
        "main_close": k.get("close"),
        "chg_pct": k.get("chg_pct"),
        "main_hold": (per.get(main_sym) or {}).get("hold"),
        "main_contract": main_contract,
        "months_hold": {s: v["hold"] for s, v in per.items()},
        "months_volume": {s: v["volume"] for s, v in per.items()},
    }


def assemble(code, as_of, days, months_daily, rank_by_date, main_contract):
    rows = snap_kline(code, as_of)
    kline_by_date = {r["date"]: r for r in rows}
    dates = [r["date"] for r in rows[-days:] if r.get("date")]
    main_sym = main_contract.upper() if main_contract else ""
    daily = [daily_of(months_daily, kline_by_date, d, main_sym, main_contract) for d in dates]
    # 移仓实测：主力减仓中，有多少被其他月份增仓承接（真实观测，不再按交割月推断）
    for i, d in enumerate(daily):
        mh = d.get("main_hold")
        d["other_hold"] = (d["total_hold"] - mh) if isinstance(mh, (int, float)) else None
        d["main_chg"] = None
        d["other_chg"] = None
        if i:
            p = daily[i - 1]
            pm = p.get("main_hold")
            if isinstance(mh, (int, float)) and isinstance(pm, (int, float)):
                d["main_chg"] = mh - pm
            if d["other_hold"] is not None and p.get("other_hold") is not None:
                d["other_chg"] = d["other_hold"] - p["other_hold"]
        mc, oc = d.get("main_chg"), d.get("other_chg")
        d["rollover_capture"] = (round(min(1.0, oc / (-mc)), 4)
                                 if isinstance(mc, (int, float)) and mc < 0
                                 and isinstance(oc, (int, float)) and oc > 0 else 0)

    # 全月份合计持仓 / 成交序列（用于 L1 价量仓 / 噪音带 / 移仓识别）
    agg = {}
    for sym, rec in (months_daily or {}).items():
        for d, v in rec.items():
            a = agg.setdefault(d, {"hold": 0, "volume": 0})
            a["hold"] += v["hold"]
            a["volume"] += v["volume"]
    all_dates = sorted(agg)[-KEEP_DAYS:]
    series = []
    prev = None
    for d in all_dates:
        h = agg[d]["hold"]
        series.append({"date": d, "hold": h, "volume": agg[d]["volume"],
                       "chg": 0 if prev is None else h - prev})
        prev = h
    tot = {
        "latest": series[-1]["hold"] if series else 0,
        "prev": series[-2]["hold"] if len(series) >= 2 else None,
        "chg_1d": (series[-1]["hold"] - series[-2]["hold"]) if len(series) >= 2 else None,
        "chg_5d": (series[-1]["hold"] - series[-6]["hold"]) if len(series) >= 6 else None,
        "chg_20d": (series[-1]["hold"] - series[-21]["hold"]) if len(series) >= 21 else None,
        "series": series,
        "as_of": series[-1]["date"] if series else None,
    }

    # 逐日全月份三榜截面
    data = {}
    for d in dates:
        sec = build_day_section(d, rank_by_date.get(d, {}), daily[-1] if daily else {})
        if sec:
            dd = next((x for x in daily if x["date"] == d), {})
            sec["total_oi"] = dd.get("total_hold", 0)
            sec["total_volume"] = dd.get("total_volume", 0)
            sec["main_close"] = dd.get("main_close")
            sec["chg_pct"] = dd.get("chg_pct")
            sec["months_hold"] = dd.get("months_hold", {})
            data[d] = sec

    last = daily[-1] if daily else {}
    months_view = []
    for sym, rec in sorted((months_daily or {}).items()):
        v = rec.get(_norm(as_of))
        if not v:
            continue
        has = sym.upper() in (data.get(dates[-1], {}).get("months_with_rank") or []) if dates else False
        months_view.append({
            "contract": sym, "hold": v["hold"], "volume": v["volume"], "close": v["close"],
            "is_main": sym.upper() == main_sym, "rank_available": has,
        })
    months_view.sort(key=lambda x: x["hold"], reverse=True)

    obj = {
        "code": code, "as_of": as_of, "source": "新浪财经（逐月合约日线 + 三榜会员排名）",
        "scope": "all_months", "main_contract": main_contract,
        "note": NOTE,
        "months": months_view,
        "daily": daily,
        "totals": tot,
        "data": dict(sorted(data.items())),
        "stats": {"month_count": len(months_view),
                  "rank_month_count": len(data.get(dates[-1], {}).get("months_with_rank") or []) if dates else 0,
                  "day_count": len(data)},
    }
    dump_json(out_path(code), obj)
    return obj


# ---------------------------------------------------------------- 主流程

def rank_symbols_for_date(months_daily, date, topn=RANK_TOPN):
    cand = []
    for sym, rec in (months_daily or {}).items():
        v = rec.get(date)
        if v and v["hold"] > 0:
            cand.append((v["hold"], sym))
    cand.sort(reverse=True)
    return [s for _, s in cand[:topn]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", nargs="*", default=None)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--delay", type=float, default=0.02)
    ap.add_argument("--force-daily", action="store_true")
    ap.add_argument("--force-rank", action="store_true")
    ap.add_argument("--snap-date", default=None)
    args = ap.parse_args()

    as_of = args.snap_date or os.environ.get("WB_SNAP_DATE") or SNAP_DATE
    registry = {p["code"]: p for p in PRODUCTS}
    codes = [c.upper() for c in args.codes] if args.codes else list(registry.keys())
    print(f"[全月份] as_of={as_of} 品种={len(codes)} 天数={args.days} 并发={args.workers}", flush=True)

    # 阶段 1：月份日线
    print("[1/3] 抓取各品种全部月份合约日线 ...", flush=True)
    months_all = {}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(fetch_daily_cache, c, as_of, args.force_daily): c for c in codes}
        for i, fu in enumerate(as_completed(futs), 1):
            c = futs[fu]
            try:
                months_all[c] = fu.result()
            except Exception as e:
                months_all[c] = {}
                print(f"  ! {c} 日线失败 {type(e).__name__}: {e}", flush=True)
            if i % 10 == 0 or i == len(codes):
                print(f"  日线 {i}/{len(codes)}", flush=True)

    # 阶段 2：逐日三榜（按 品种+交易日 为任务粒度，结果按日缓存）
    tasks = []
    for c in codes:
        dates = trade_dates(c, as_of, args.days)
        for d in dates:
            syms = rank_symbols_for_date(months_all.get(c, {}), d)
            if syms:
                tasks.append((c, d, syms))
    print(f"[2/3] 逐日三榜任务 {len(tasks)} 个（品种×交易日）...", flush=True)
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(fetch_rank_day, c, d, syms, args.force_rank, args.delay): (c, d)
                for c, d, syms in tasks}
        for fu in as_completed(futs):
            done += 1
            try:
                fu.result()
            except Exception as e:
                print(f"  ! {futs[fu]} 三榜失败 {type(e).__name__}: {e}", flush=True)
            if done % 100 == 0 or done == len(tasks):
                print(f"  三榜 {done}/{len(tasks)}", flush=True)

    # 阶段 3：合并输出
    print("[3/3] 跨月合并并写出产物 ...", flush=True)
    ok, bad = [], []
    for c in codes:
        dates = trade_dates(c, as_of, args.days)
        rank_by_date = {d: (load_json(rank_path(c, d), {}) or {}) for d in dates}
        mc = main_contract_of(c, as_of)
        try:
            obj = assemble(c, as_of, args.days, months_all.get(c, {}), rank_by_date, mc)
            st = obj["stats"]
            ok.append((c, st["month_count"], st["rank_month_count"], st["day_count"],
                       obj["totals"]["latest"]))
        except Exception as e:
            bad.append((c, f"{type(e).__name__}: {e}"))
    print(f"\n[汇总] 成功 {len(ok)}/{len(codes)}，失败 {len(bad)}", flush=True)
    for c, m, r, n, tot in ok[:12]:
        print(f"  {c:5s} 月份 {m:2d} / 有榜单 {r:2d} / 截面 {n:2d} 日 / 全月份合计持仓 {tot:,}", flush=True)
    if bad:
        for c, e in bad[:10]:
            print(f"  ! {c}: {e}", flush=True)
    # 全局统计
    total_days = sum(n for _, _, _, n, _ in ok)
    print(f"[校验] 覆盖交易日截面合计 {total_days} 个", flush=True)


if __name__ == "__main__":
    main()
