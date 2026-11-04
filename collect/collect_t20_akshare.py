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
from t20_collect_lib import load_agg, save_agg, DATES

import akshare as ak

# AKShare 直连可覆盖的交易所（SHFE + CZCE）。DCE 数据域名在本环境不可达，改走 NeoData。
SHFE = ["RB","HC","RU","CU","AL","AU","NI","ZN","AG","PB","BU","SN"]
CZCE = ["FG","SA","SR","CF","RM","OI","AP","CJ","TA","MA","UR"]
ALL = SHFE + CZCE

def collect_date(date):
    """拉取某交易日全部品种的前20合计净持仓，返回 {code: rec}。"""
    df = ak.get_rank_sum_daily(start_day=date, end_day=date, vars_list=ALL)
    # 取品种汇总行：symbol == variety
    summ = df[df["symbol"] == df["variety"]]
    out = {}
    for _, r in summ.iterrows():
        code = str(r["variety"]).strip()
        lo = r.get("long_open_interest_top20")
        so = r.get("short_open_interest_top20")
        lc = r.get("long_open_interest_chg_top20")
        sc = r.get("short_open_interest_chg_top20")
        if lo is None or so is None:
            continue
        out[code] = {
            "date": date,
            "net": int(lo - so),
            "long_total": int(lo),
            "short_total": int(so),
            "long_chg": int(lc) if lc is not None else 0,
            "short_chg": int(sc) if sc is not None else 0,
        }
    return out

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
                p["variety"] = {"RB":"RB.SHF","HC":"HC.SHF","RU":"RU.SHF","CU":"CU.SHF","AL":"AL.SHF",
                    "AU":"AU.SHF","NI":"NI.SHF","ZN":"ZN.SHF","AG":"AG.SHF","PB":"PB.SHF","BU":"BU.SHF",
                    "SN":"SN.SHF","FG":"FG.ZCE","SA":"SA.ZCE","SR":"SR.ZCE","CF":"CF.ZCE","RM":"RM.ZCE",
                    "OI":"OI.ZCE","AP":"AP.ZCE","CJ":"CJ.ZCE","TA":"TA.ZCE","MA":"MA.ZCE","UR":"UR.ZCE",
                    "I":"I.DCE","J":"J.DCE","JM":"JM.DCE","M":"M.DCE","C":"C.DCE","A":"A.DCE","Y":"Y.DCE",
                    "P":"P.DCE","JD":"JD.DCE","LH":"LH.DCE","L":"L.DCE","V":"V.DCE","PP":"PP.DCE"}.get(code)
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
