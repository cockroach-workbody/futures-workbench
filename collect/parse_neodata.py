# -*- coding: utf-8 -*-
"""
parse_neodata.py
把 collect/raw/<CODE>_rank.json (NeoData futures_rank 原始结果) 与
collect/raw/<CODE>_wh.json (futures_warehouse 原始结果) 解析成
collect/neodata_<CODE>.json，供 build_all.py 直接消费。

解析逻辑：
  - 从 rank 原始结果中按 contract_code 分组，取「持买合计最大」的合约为标杆/主力合约；
  - 取该合约的 持买 Top20 与 持卖 Top20，按会员号(member_code)合并为成员明细；
  - 计算前20多空合计、净持仓及较上期变化（long_chg/short_chg/net_chg）；
  - 从 warehouse 原始结果取「按品种统计」行的仓单总量与增减。
"""
import json
import os
import glob
import re

BASE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(BASE, "raw")


def load_raw(code, kind):
    p = os.path.join(RAW, f"{code}_{kind}.json")
    if not os.path.exists(p):
        return None
    return json.load(open(p, encoding="utf-8"))


def parse_rank(raw):
    """返回 (as_of, dominant_contract, members, summary, bias) 或 None。"""
    if not raw:
        return None
    # 按合约分组
    by_contract = {}
    for r in raw:
        cc = r.get("contract_code")
        by_contract.setdefault(cc, []).append(r)

    # 选主力：持买合计最大的「真实」合约（跳过品种汇总伪合约，如 AP.ZCE 无月份数字）
    best_cc, best_long_total = None, -1
    contract_long = {}
    for cc, rows in by_contract.items():
        stem = cc.split(".")[0]
        if not re.search(r"\d", stem):  # 品种汇总行（无合约月份）跳过
            continue
        lt = sum(float(x.get("indicator_volume") or 0)
                 for x in rows if x.get("indicator_code") == "持买")
        contract_long[cc] = lt
        if lt > best_long_total:
            best_long_total, best_cc = lt, cc
    if not best_cc:
        return None

    as_of = raw[0].get("end_date")
    dom_rows = by_contract[best_cc]

    long_map, short_map = {}, {}
    for x in dom_rows:
        ic = x.get("indicator_code")
        mc = x.get("member_code")
        name = x.get("member_abbr")
        vol = float(x.get("indicator_volume") or 0)
        chg = float(x.get("change_volume") or 0)
        if ic == "持买":
            long_map[mc] = {"name": name, "long": vol, "long_chg": chg}
        elif ic == "持卖":
            short_map[mc] = {"name": name, "short": vol, "short_chg": chg}

    members = {}
    for mc, v in long_map.items():
        members[mc] = {**v, "short": 0.0, "short_chg": 0.0}
    for mc, v in short_map.items():
        members.setdefault(mc, {"name": v["name"], "long": 0.0, "long_chg": 0.0})
        members[mc]["short"] = v["short"]
        members[mc]["short_chg"] = v["short_chg"]

    # 明细（按净持仓绝对值排序，取前20）
    detail = []
    for mc, v in members.items():
        v = dict(v)
        v["net"] = v["long"] - v["short"]
        v["net_chg"] = v["long_chg"] - v["short_chg"]
        detail.append(v)
    detail.sort(key=lambda d: abs(d["net"]), reverse=True)
    detail = detail[:20]

    long_total = sum(d["long"] for d in detail)
    short_total = sum(d["short"] for d in detail)
    net = long_total - short_total
    long_chg = sum(d["long_chg"] for d in detail)
    short_chg = sum(d["short_chg"] for d in detail)
    net_chg = long_chg - short_chg

    if net > 0:
        bias = "多头占优·" + ("净多增仓偏多" if net_chg > 0 else "净多减仓谨慎")
    else:
        bias = "空头占优·" + ("净空增仓偏空" if net_chg < 0 else "净空减仓")

    summary = {
        "long_total": round(long_total, 1),
        "short_total": round(short_total, 1),
        "net": round(net, 1),
        "long_chg": round(long_chg, 1),
        "short_chg": round(short_chg, 1),
        "net_chg": round(net_chg, 1),
    }
    return best_cc, as_of, detail, summary, bias


def parse_wh(raw):
    """返回 {standard, mill, breakdown}。
    - standard: 按品种统计（标准仓单总量与较上期变化）
    - mill:     按厂库统计（厂库合计，可能 None）
    - breakdown: 按仓库统计（标准仓单分仓库明细，用于参考）
    """
    if not raw:
        return None
    standard = None
    mill = None
    breakdown = []
    for r in raw:
        t = r.get("wr_stat_type_name")
        wrq = float(r.get("wrq") or 0)
        chg = float(r.get("wrq_change") or 0)
        unit = r.get("wr_unit_name") or ""
        day = r.get("trading_day")
        if t == "按品种统计":
            standard = {"total": wrq, "change": chg, "unit": unit, "date": day}
        elif t == "按厂库统计":
            if mill is None:
                mill = {"total": 0.0, "change": 0.0, "unit": unit, "date": day}
            mill["total"] += wrq
            mill["change"] += chg
        elif t == "按仓库统计":
            breakdown.append({"name": r.get("warehouse_code") or "",
                              "qty": wrq, "change": chg, "unit": unit})
    return {"standard": standard, "mill": mill, "breakdown": breakdown}


def main():
    codes = set()
    for f in glob.glob(os.path.join(RAW, "*_rank.json")):
        codes.add(os.path.basename(f).replace("_rank.json", ""))
    for f in glob.glob(os.path.join(RAW, "*_wh.json")):
        codes.add(os.path.basename(f).replace("_wh.json", ""))

    done = 0
    for code in sorted(codes):
        rank = load_raw(code, "rank")
        wh = load_raw(code, "wh")
        res = parse_rank(rank)
        wh_res = parse_wh(wh)
        out = {
            "code": code,
            "available": bool(res),
            "source": "NeoData",
            "as_of": res[1] if res else None,
            "dominant_contract": res[0] if res else None,
            "members": res[2] if res else [],
            "summary": res[3] if res else None,
            "bias": res[4] if res else None,
            "warehouse": wh_res,
        }
        with open(os.path.join(BASE, f"neodata_{code}.json"), "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        done += 1
        if wh_res and wh_res.get("standard"):
            s = wh_res["standard"]
            wc = f"标准仓单 {s['total']}{s['unit']}(Δ{s['change']})"
            mc = f"厂库 {wh_res['mill']['total']}{wh_res['mill']['unit']}(Δ{wh_res['mill']['change']})" if wh_res.get("mill") else "厂库无"
        else:
            wc, mc = "仓单无", ""
        print(f"  {code}: 主力 {res[0] if res else '-'} | 净持仓 {res[3]['net'] if res else '-'} "
              f"(Δ{res[3]['net_chg'] if res else '-'}) | {wc} | {mc}")
    print(f"[OK] 解析完成，共 {done} 个品种 -> collect/neodata_*.json")


if __name__ == "__main__":
    main()
