#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用 AKShare 直接从三大交易所官网拉取「前20会员持仓排名」，绕过 NeoData 限流。
AKShare 的 get_rank_sum_daily 对每个交易日返回 按品种汇总的前20会员多空合计持仓，
其中 symbol==variety 的行为该品种(全合约)汇总行。
输出：collect/raw_hist/agg_<CODE>.json（与之前 NeoData 采集格式一致，幂等合并）。
"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from t20_collect_lib import load_agg, save_agg, DATES, parse_akshare_dominant

import akshare as ak

# AKShare 直连可覆盖的交易所（SHFE + CZCE）。DCE 数据域名在本环境不可达，改走 NeoData。
SHFE = ["RB","HC","RU","CU","AL","AU","NI","ZN","AG","PB","BU","SN"]
CZCE = ["FG","SA","SR","CF","RM","OI","AP","CJ","TA","MA","UR"]
ALL = SHFE + CZCE

# code -> variety（含交易所后缀），用于主力合约标签（如 RB2610.SHF）
VARIETY_MAP = {c: f"{c}.SHF" for c in SHFE}
VARIETY_MAP.update({c: f"{c}.ZCE" for c in CZCE})

def collect_date(date):
    """拉取某交易日全部品种的前20主力合约（持买合计最大）净持仓，返回 {code: rec}。"""
    df = ak.get_rank_sum_daily(start_day=date, end_day=date, vars_list=ALL)
    # 主力合约口径：逐品种在真实月份合约中选 持买合计(top20) 最大者
    return parse_akshare_dominant(df, date, VARIETY_MAP)

def main():
    total_new = 0
    for date in DATES:
        print(f"\n--- fetching {date} ---", flush=True)
        try:
            recs = collect_date(date)
        except Exception as e:
            print(f"  FAIL {date}: {e}", flush=True)
            time.sleep(3)
            continue
        # 合并到各品种 agg 文件（已存在日期跳过）
        for code, rec in recs.items():
            p = load_agg(code)
            if p.get("variety") is None:
                p["variety"] = VARIETY_MAP.get(code)
            data = p.setdefault("data", {})
            if date not in data:
                data[date] = rec
                total_new += 1
            save_agg(p)
        print(f"  got {len(recs)} varieties for {date}", flush=True)
        time.sleep(2)
    print(f"\nDONE. total new date-points added: {total_new}", flush=True)

if __name__ == "__main__":
    main()
