#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
标准仓单 —— 每日滚动采集（覆盖式更新 collect/warehouse_<CODE>.json）

数据口径：各交易所「标准仓单」总量与较上期变化（单日快照）。
来源（绕开 NeoData 限流，最稳用 AKShare 直连交易所官网）：
  - SHFE 上期所：futures_shfe_warehouse_receipt -> 键为中文名（铜/铝/...），列 WRTWGHTS/WRTCHANGE
  - CZCE 郑商所：futures_warehouse_receipt_czce -> 键为代码（SR/CF/...），列 仓单数量/当日增减
  - GFEX 广期所：futures_gfex_warehouse_receipt -> 键为代码（LC/SI/...），列 今日仓单量/增减
  - DCE 大商所：瑞数反爬不可达，改走 NeoData futures_warehouse（仅当连接了 neodata 连接器）

输出：collect/warehouse_<CODE>.json  {code, date, standard:{total,change,unit,date}, mill:null, breakdown:[]}
      build_all.py 的 build_inventory 优先消费此文件（高于静态 neodata_<CODE>.json）。

幂等/稳健：失败品种跳过，不覆盖已有好数据（除非本次成功抓到）。
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import akshare as ak  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "collect")

# ---- 品种 -> 产品代码映射（与 products.json 一致）----
# SHFE 中文名 -> 代码
SHFE_CN2CODE = {
    "铜": "CU", "铝": "AL", "锌": "ZN", "铅": "PB", "镍": "NI", "锡": "SN",
    "黄金": "AU", "白银": "AG", "螺纹钢": "RB", "热轧卷板": "HC", "螺纹": "RB",
    "天然橡胶": "RU", "石油沥青": "BU", "沥青": "BU", "燃料油": "FU", "线材": "WR",
}
# CZCE / GFEX 直接用代码做键，下面列出本工作台涉及的品种集合
CZCE_CODES = {"FG", "SA", "SR", "CF", "RM", "OI", "AP", "CJ", "TA", "MA", "UR"}
GFEX_CODES = {"SI", "LC", "PS", "PT", "PD"}
# DCE 大商所（需 neodata）
DCE_VARIETY = {"I": "I.DCE", "J": "J.DCE", "JM": "JM.DCE", "M": "M.DCE", "C": "C.DCE",
               "A": "A.DCE", "Y": "Y.DCE", "P": "P.DCE", "JD": "JD.DCE", "LH": "LH.DCE",
               "L": "L.DCE", "V": "V.DCE", "PP": "PP.DCE"}
WH_FIELDS = "trading_day,variety_code,wr_stat_type_name,wrq,wrq_change,wr_unit_name"


def write_wh(code, date, total, change, unit):
    if total is None:
        return False
    payload = {
        "code": code, "date": date,
        "standard": {"total": round(float(total), 2), "change": round(float(change or 0), 2),
                     "unit": unit or "吨", "date": date},
        "mill": None, "breakdown": [],
    }
    with open(os.path.join(OUT, f"warehouse_{code}.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    return True


def collect_shfe(date):
    out = {}
    try:
        d = ak.futures_shfe_warehouse_receipt(date=date)
        for cn, df in d.items():
            code = SHFE_CN2CODE.get(cn.strip())
            if not code:
                continue
            total = float(df["WRTWGHTS"].sum())
            chg = float(df["WRTCHANGE"].sum())
            out[code] = (total, chg, "吨")
    except Exception as e:  # noqa
        print(f"  [SHFE] 抓取失败: {e}", flush=True)
    return out


def collect_czce(date):
    out = {}
    try:
        d = ak.futures_warehouse_receipt_czce(date=date)
        for code, df in d.items():
            code = code.strip()
            if code not in CZCE_CODES:
                continue
            # 排除 小计/合计/总计 行，避免重复求和
            sub = df[~df["仓库简称"].astype(str).str.contains("计", na=False)]
            total = float(sub["仓单数量"].sum())
            chg = float(sub["当日增减"].fillna(0).sum())
            out[code] = (total, chg, "张")
    except Exception as e:  # noqa
        print(f"  [CZCE] 抓取失败: {e}", flush=True)
    return out


def collect_gfex(date):
    out = {}
    try:
        d = ak.futures_gfex_warehouse_receipt(date=date)
        for code, df in d.items():
            code = code.strip()
            if code not in GFEX_CODES:
                continue
            total = float(df["今日仓单量"].sum())
            chg = float(df["增减"].sum())
            out[code] = (total, chg, "吨")
    except Exception as e:  # noqa
        print(f"  [GFEX] 抓取失败: {e}", flush=True)
    return out


def collect_dce(date):
    cfg_env = os.environ.get("CODEBUDDY_MCP_CONFIG")
    if not cfg_env:
        print("  [DCE] 未连接 neodata 连接器，跳过 DCE 仓单（保持上次数据）", flush=True)
        return {}
    try:
        import collect_raw_new as crn
        crn.init_session()
    except Exception as e:  # noqa
        print(f"  [DCE] neodata 初始化失败: {e}", flush=True)
        return {}
    out = {}
    for code, vc in DCE_VARIETY.items():
        ok, payload, err = crn.call_tool("futures_warehouse", {
            "variety_codes": vc, "view": "warehouse", "limit": 100,
            "output_fields": WH_FIELDS,
        })
        if not ok:
            print(f"  [DCE] {code} wh FAIL: {err}", flush=True)
            time.sleep(4)
            continue
        rows = payload.get("result") or []
        standard = None
        for r in rows:
            if (r.get("wr_stat_type_name") or "").find("品种") >= 0:
                standard = r
                break
        if standard:
            out[code] = (float(standard.get("wrq") or 0),
                         float(standard.get("wrq_change") or 0),
                         standard.get("wr_unit_name") or "吨")
        time.sleep(4)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="指定交易日 YYYYMMDD（默认最新已收盘交易日）")
    args = ap.parse_args()
    from wb_common import snap_date
    date = args.date or snap_date()
    print(f"== 仓单每日滚动 -> 交易日 {date} ==", flush=True)

    collected = {}
    for fn, label in ((collect_shfe, "SHFE"), (collect_czce, "CZCE"),
                      (collect_gfex, "GFEX")):
        r = fn(date)
        print(f"  {label}: {len(r)} 品种", flush=True)
        collected.update(r)
    dce = collect_dce(date)
    if dce:
        print(f"  DCE(neodata): {len(dce)} 品种", flush=True)
        collected.update(dce)

    written = 0
    for code, (total, chg, unit) in collected.items():
        if write_wh(code, date, total, chg, unit):
            written += 1
    print(f"DONE 仓单滚动. 成功写入 {written} 个品种（覆盖 collect/warehouse_*.json）", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
