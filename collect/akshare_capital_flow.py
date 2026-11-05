#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AKShare 期货资金流采集器（奇货可查式 / 交易所会员持仓排名同源）

隐藏技巧：用 AKShare 抓取「交易所每日会员持仓排名」（新浪镜像，与奇货可查同源），
挖掘期货市场资金动向，捕捉主力资金的真实意图：

  - futures_hold_pos_sina(symbol="多单持仓"|"空单持仓"|"成交量", contract, date)
        → 逐会员 多单 / 空单 / 成交量 及其「比上交易增减」
  - futures_zh_daily_sina(symbol="RB0")
        → 主连日线含 hold(总持仓) 历史，用于「总持仓变动」与历史回溯

计算产出（collect/raw_capflow/capflow_<CODE>.json），供 build_all 注入快照 capital_flow 键，
第7部分（资金动向）渲染：
  1) 净持仓分布：按账户资金规模分层的多空力量对比（机构/散户 二分 + 4 档资金规模）
  2) 总持仓变动：资金整体进出场节奏（主连 hold 历史回溯 + 当日/5日/20日变化）
  3) 大单动向：追踪主力机构的操作痕迹（净持仓变化最大的增/减仓席位 + 成交量领先席位）
  4) 净持仓变化而非绝对持仓量：全程以 Δ净持仓 为主信号
  5) 历史回溯数据：总持仓历史 + 各资金层级净持仓变化序列

运行：
  python collect/akshare_capital_flow.py            # 全品种
  python collect/akshare_capital_flow.py RB        # 单品种
  python collect/akshare_capital_flow.py --with-history   # 额外采样历史分层净持仓

注：机构/散户为按「净持仓规模」推断的代理口径（交易所排名不直接披露终端客户属性），
    前端会如实标注。
"""
import os
import sys
import json
import argparse
from datetime import datetime

import akshare as ak
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# 复用 build_all 的品种注册表与快照日期，避免重复维护
from build_all import PRODUCTS, SNAP_DATE, OUT_ROOT  # noqa

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "raw_capflow")
os.makedirs(OUT_DIR, exist_ok=True)

# 历史回溯采样：最近交易日 + 向前若干个交易日（用于分层净持仓变化序列）
HISTORY_OFFSETS = [5, 12, 21]

TIER_DEFS = [
    ("超大户/机构", 0, 5),     # 按 |净持仓| 排名第 1-5
    ("大户", 5, 10),
    ("中户", 10, 15),
    ("散户/中小", 15, 20),
]
INST_TOPN = 5  # 机构/主力 = 前 N 大净持仓规模席位


def _sina_contract(main_contract):
    """'RB2701.SHF' -> 'rb2701'；失败返回 None。"""
    if not main_contract or "." not in main_contract:
        return None
    return main_contract.split(".")[0].lower()


def _load_main_contract(code):
    """从已有快照读取 main_contract（采集器在每日 build_all 之后运行，快照已存在）。"""
    for cat in sorted({p["cat"] for p in PRODUCTS}):
        d = os.path.join(OUT_ROOT, cat, code)
        if not os.path.isdir(d):
            continue
        fs = sorted([f for f in os.listdir(d) if f.endswith(".json")])
        for fn in reversed(fs):
            try:
                snap = json.load(open(os.path.join(d, fn), encoding="utf-8"))
                mc = snap.get("main_contract")
                if mc:
                    return mc
            except Exception:
                continue
    return None


def _fetch_hold_pos(contract, date, sym):
    """抓取某一口径（多单持仓/空单持仓/成交量）的会员排名，失败返回 None。"""
    try:
        df = ak.futures_hold_pos_sina(symbol=sym, contract=contract, date=date)
        if df is None or len(df) == 0:
            return None
        return df
    except Exception:
        return None


def _build_member_table(long_df, short_df, vol_df):
    """合并多单/空单/成交量三套排名，逐会员计算 净持仓 与 净持仓变化。"""
    members = {}
    VAL_CHG = {"long": ("多单持仓", "long_chg"),
               "short": ("空单持仓", "short_chg"),
               "volume": ("成交量", "vol_chg")}

    def add(df, val_key):
        if df is None:
            return
        val_col, chg_key = VAL_CHG[val_key]
        for _, r in df.iterrows():
            name = str(r.get("会员简称", "") or "").strip()
            if not name or name in ("合计", "小计", "名次"):
                continue
            try:
                val = float(r.get(val_col) or 0)
            except Exception:
                val = 0.0
            try:
                chg = float(r.get("比上交易增减") or 0)
            except Exception:
                chg = 0.0
            m = members.setdefault(name, {
                "name": name, "long": 0.0, "long_chg": 0.0,
                "short": 0.0, "short_chg": 0.0,
                "volume": 0.0, "vol_chg": 0.0,
            })
            m[val_key] = val
            m[chg_key] = chg

    add(long_df, "long")
    add(short_df, "short")
    add(vol_df, "volume")

    for m in members.values():
        m["net"] = m["long"] - m["short"]
        m["net_chg"] = m["long_chg"] - m["short_chg"]
    return list(members.values())


def _tier_stats(members):
    """按 |净持仓| 降序分 4 档资金规模，返回每档聚合 + 机构/散户二分。"""
    ranked = sorted(members, key=lambda m: abs(m["net"]), reverse=True)
    n = len(ranked)
    tiers = []
    for label, lo, hi in TIER_DEFS:
        grp = ranked[lo:hi]
        if not grp:
            tiers.append({
                "tier": label, "count": 0, "net": 0.0, "net_chg": 0.0,
                "long": 0.0, "short": 0.0,
                "long_chg": 0.0, "short_chg": 0.0,
            })
            continue
        tiers.append({
            "tier": label,
            "count": len(grp),
            "net": round(sum(m["net"] for m in grp)),
            "net_chg": round(sum(m["net_chg"] for m in grp)),
            "long": round(sum(m["long"] for m in grp)),
            "short": round(sum(m["short"] for m in grp)),
            "long_chg": round(sum(m["long_chg"] for m in grp)),
            "short_chg": round(sum(m["short_chg"] for m in grp)),
        })
    # 机构/散户二分：机构 = 前 INST_TOPN 大净持仓规模席位
    inst = ranked[:INST_TOPN]
    retail = ranked[INST_TOPN:]
    inst_net = round(sum(m["net"] for m in inst))
    inst_net_chg = round(sum(m["net_chg"] for m in inst))
    retail_net = round(sum(m["net"] for m in retail))
    retail_net_chg = round(sum(m["net_chg"] for m in retail))
    inst_vs_retail = {
        "institutional": {
            "count": len(inst), "net": inst_net, "net_chg": inst_net_chg,
            "long": round(sum(m["long"] for m in inst)),
            "short": round(sum(m["short"] for m in inst)),
        },
        "retail": {
            "count": len(retail), "net": retail_net, "net_chg": retail_net_chg,
            "long": round(sum(m["long"] for m in retail)),
            "short": round(sum(m["short"] for m in retail)),
        },
    }
    return tiers, inst_vs_retail, n


def _large_orders(members):
    """大单动向：净持仓变化最大的增/减仓席位 + 成交量领先席位。"""
    by_net_chg = sorted(members, key=lambda m: m["net_chg"], reverse=True)
    by_vol = sorted(members, key=lambda m: m["volume"], reverse=True)
    top_increase = [{"name": m["name"], "net_chg": round(m["net_chg"]),
                     "net": round(m["net"])} for m in by_net_chg[:5] if m["net_chg"] > 0]
    top_decrease = [{"name": m["name"], "net_chg": round(m["net_chg"]),
                     "net": round(m["net"])} for m in by_net_chg[-5:] if m["net_chg"] < 0]
    top_decrease = list(reversed(top_decrease))
    vol_leaders = [{"name": m["name"], "volume": round(m["volume"]),
                    "vol_chg": round(m["vol_chg"]), "net": round(m["net"])}
                   for m in by_vol[:5]]
    return {
        "top_net_increase": top_increase,
        "top_net_decrease": top_decrease,
        "volume_leaders": vol_leaders,
    }


def _total_position_series(sina_symbol, snap_date):
    """主连日线 hold(总持仓) 历史 → 序列 + 当日/5日/20日变化。"""
    try:
        df = ak.futures_zh_daily_sina(symbol=sina_symbol)
    except Exception:
        return None
    if df is None or len(df) == 0 or "hold" not in df.columns:
        return None
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    df = df.sort_values("date").reset_index(drop=True)
    df["hold"] = pd.to_numeric(df["hold"], errors="coerce")
    df = df.dropna(subset=["hold"])
    last = df.tail(60).copy()
    last["chg"] = last["hold"].diff().round().fillna(0)
    series = [{"date": r["date"], "hold": int(r["hold"]), "chg": int(r["chg"])}
              for _, r in last.iterrows()]
    if len(df) == 0:
        return None
    latest_hold = int(df["hold"].iloc[-1])
    prev_hold = int(df["hold"].iloc[-2]) if len(df) >= 2 else latest_hold
    chg_1d = latest_hold - prev_hold
    chg_5d = latest_hold - int(df["hold"].iloc[-6]) if len(df) >= 6 else None
    chg_20d = latest_hold - int(df["hold"].iloc[-21]) if len(df) >= 21 else None
    return {
        "latest": latest_hold,
        "prev": prev_hold,
        "chg_1d": chg_1d,
        "chg_5d": chg_5d,
        "chg_20d": chg_20d,
        "series": series,
        "as_of": series[-1]["date"] if series else None,
    }


def _tier_net_at_date(contract, date):
    """抓取指定日期的三套排名，计算分层净持仓（用于历史回溯）。失败返回 None。"""
    long_df = _fetch_hold_pos(contract, date, "多单持仓")
    short_df = _fetch_hold_pos(contract, date, "空单持仓")
    vol_df = _fetch_hold_pos(contract, date, "成交量")
    if long_df is None and short_df is None:
        return None
    members = _build_member_table(long_df, short_df, vol_df)
    tiers, _, _ = _tier_stats(members)
    return {t["tier"]: t["net"] for t in tiers}


def _build_capflow(cfg, with_history=False):
    code = cfg["code"]
    sina = cfg.get("sina") or (code + "0")
    mc = _load_main_contract(code)
    contract = _sina_contract(mc)
    date = SNAP_DATE.replace("-", "")

    result = {
        "available": False,
        "source": "AKShare·期货交易所会员持仓排名（新浪镜像，与奇货可查同源）",
        "code": code,
        "contract": contract,
        "main_contract": mc,
        "as_of": SNAP_DATE,
        "note_proxy": "机构/散户为按净持仓规模推断的代理口径（交易所排名不直接披露终端客户属性）。",
    }
    if not contract:
        result["note"] = "未找到活跃合约（需先 build_all 生成快照 main_contract）。"
        return result

    # 1) 总持仓变动（主连 hold 历史）
    tp = _total_position_series(sina, SNAP_DATE)
    result["total_position"] = tp

    # 2) 当日会员持仓排名 → 净持仓分布 / 大单动向
    long_df = _fetch_hold_pos(contract, date, "多单持仓")
    short_df = _fetch_hold_pos(contract, date, "空单持仓")
    vol_df = _fetch_hold_pos(contract, date, "成交量")
    if long_df is None and short_df is None:
        result["note"] = f"持仓排名抓取失败（contract={contract}, date={date}）。"
        return result

    members = _build_member_table(long_df, short_df, vol_df)
    if not members:
        result["note"] = "会员持仓表为空。"
        return result

    tiers, inst_vs_retail, n = _tier_stats(members)
    large = _large_orders(members)

    # 头部席位明细（前 12，按 |净持仓|）
    top_members = sorted(members, key=lambda m: abs(m["net"]), reverse=True)[:12]
    member_rows = [{
        "name": m["name"],
        "long": round(m["long"]), "long_chg": round(m["long_chg"]),
        "short": round(m["short"]), "short_chg": round(m["short_chg"]),
        "net": round(m["net"]), "net_chg": round(m["net_chg"]),
    } for m in top_members]

    result.update({
        "available": True,
        "member_count": n,
        "tiers": tiers,
        "institutional_vs_retail": inst_vs_retail,
        "large_orders": large,
        "top_members": member_rows,
    })

    # 3) 历史回溯：分层净持仓变化序列（采样若干历史交易日）
    if with_history:
        hist = []
        base_dates = None
        if tp and tp.get("series"):
            base_dates = [p["date"] for p in tp["series"]]
        if base_dates and len(base_dates) > HISTORY_OFFSETS[-1]:
            sample_idx = [len(base_dates) - 1] + [len(base_dates) - 1 - o for o in HISTORY_OFFSETS]
            for idx in sample_idx:
                d = base_dates[idx].replace("-", "")
                snap_d = base_dates[idx]
                tmap = _tier_net_at_date(contract, d)
                if tmap:
                    row = {"date": snap_d}
                    row.update(tmap)
                    hist.append(row)
        if hist:
            result["tier_net_history"] = hist

    # 4) 文字结论
    inst = inst_vs_retail["institutional"]
    retail = inst_vs_retail["retail"]
    tone_inst = "净多" if inst["net"] > 0 else ("净空" if inst["net"] < 0 else "均衡")
    tone_ret = "净多" if retail["net"] > 0 else ("净空" if retail["net"] < 0 else "均衡")
    chg_inst = inst["net_chg"]
    chg_ret = retail["net_chg"]
    result["summary"] = (
        f"截至{SNAP_DATE}，前{INST_TOPN}大主力席位合计净持仓 {inst['net']:,} 手（{tone_inst}），"
        f"当日净持仓变化 {chg_inst:+,} 手；其余 {retail['count']} 个席位合计净持仓 {retail['net']:,} 手"
        f"（{tone_ret}），当日净持仓变化 {chg_ret:+,} 手。"
        f"净持仓变化 = 多头增仓 − 空头增仓；"
        + ("机构与散户同向增仓，资金共识偏强。" if chg_inst > 0 and chg_ret > 0
           else "机构与散户同向减仓，资金离场迹象。" if chg_inst < 0 and chg_ret < 0
           else "机构与散户方向相反，资金分歧加大。")
    )
    return result


def run_one(code, with_history=False):
    cfg = next((p for p in PRODUCTS if p["code"] == code), None)
    if not cfg:
        print(f"[skip] 未知品种 {code}")
        return
    try:
        res = _build_capflow(cfg, with_history=with_history)
    except Exception as e:  # noqa
        res = {"available": False, "code": code, "error": str(e)}
    out = os.path.join(OUT_DIR, f"capflow_{code}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    ok = res.get("available")
    print(f"[{'OK ' if ok else 'NO '}] {code} -> {out}  "
          f"contract={res.get('contract')} members={res.get('member_count')} "
          f"tiers={len(res.get('tiers', []))} hist={len(res.get('tier_net_history', []))}")


def main():
    ap = argparse.ArgumentParser(description="AKShare 期货资金流采集器")
    ap.add_argument("code", nargs="?", help="单品种代码，如 RB；缺省全品种")
    ap.add_argument("--with-history", action="store_true", help="额外采样历史分层净持仓")
    args = ap.parse_args()
    if args.code:
        run_one(args.code.upper(), with_history=args.with_history)
    else:
        for p in PRODUCTS:
            run_one(p["code"], with_history=args.with_history)


if __name__ == "__main__":
    main()
