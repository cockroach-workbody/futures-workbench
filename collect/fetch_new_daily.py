#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量抓取新品种的新浪主连日 K 线，落盘 collect/<CODE>_daily.json
用法: python collect/fetch_new_daily.py [CODE ...]
"""
import json
import os
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(BASE, "collect")
URL = ("https://stock2.finance.sina.com.cn/futures/api/json.php/"
       "InnerFuturesNewService.getDailyKLine?symbol={}")

# (code, sina_symbol, 名称, 板块, variety, 交易所)
NEW = [
    # 黑色建材
    ("SF", "SF0", "硅铁",   "黑色建材", "SF.ZCE", "郑州商品交易所"),
    ("SM", "SM0", "锰硅",   "黑色建材", "SM.ZCE", "郑州商品交易所"),
    # 农产品
    ("B",  "B0",  "豆二",   "农产品",   "B.DCE",  "大连商品交易所"),
    ("CS", "CS0", "玉米淀粉", "农产品",  "CS.DCE", "大连商品交易所"),
    ("CY", "CY0", "棉纱",   "农产品",   "CY.ZCE", "郑州商品交易所"),
    ("PK", "PK0", "花生",   "农产品",   "PK.ZCE", "郑州商品交易所"),
    ("LG", "LG0", "原木",   "农产品",   "LG.DCE", "大连商品交易所"),
    # 化工品
    ("EG", "EG0", "乙二醇", "化工品",   "EG.DCE", "大连商品交易所"),
    ("EB", "EB0", "苯乙烯", "化工品",   "EB.DCE", "大连商品交易所"),
    ("PG", "PG0", "液化石油气", "化工品", "PG.DCE", "大连商品交易所"),
    ("BZ", "BZ0", "纯苯",   "化工品",   "BZ.DCE", "大连商品交易所"),
    ("SH", "SH0", "烧碱",   "化工品",   "SH.ZCE", "郑州商品交易所"),
    ("PX", "PX0", "对二甲苯", "化工品",  "PX.ZCE", "郑州商品交易所"),
    ("PR", "PR0", "瓶片",   "化工品",   "PR.ZCE", "郑州商品交易所"),
    ("SC", "SC0", "原油",   "化工品",   "SC.INE", "上海国际能源交易中心"),
    ("FU", "FU0", "燃油",   "化工品",   "FU.SHF", "上海期货交易所"),
    ("LU", "LU0", "低硫燃料油", "化工品", "LU.INE", "上海国际能源交易中心"),
    ("SP", "SP0", "纸浆",   "化工品",   "SP.SHF", "上海期货交易所"),
    ("NR", "NR0", "20号胶", "化工品",   "NR.INE", "上海国际能源交易中心"),
    # 有色金属
    ("AO", "AO0", "氧化铝", "有色金属", "AO.SHF", "上海期货交易所"),
    ("SS", "SS0", "不锈钢", "有色金属", "SS.SHF", "上海期货交易所"),
    ("BC", "BC0", "国际铜", "有色金属", "BC.INE", "上海国际能源交易中心"),
    # 广期板块
    ("SI", "SI0", "工业硅", "广期板块", "SI.GFE", "广州期货交易所"),
    ("LC", "LC0", "碳酸锂", "广期板块", "LC.GFE", "广州期货交易所"),
    ("PS", "PS0", "多晶硅", "广期板块", "PS.GFE", "广州期货交易所"),
    ("PT", "PT0", "铂",     "广期板块", "PT.GFE", "广州期货交易所"),
    ("PD", "PD0", "钯",     "广期板块", "PD.GFE", "广州期货交易所"),
]


def fetch(sym):
    req = urllib.request.Request(URL.format(sym), headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://finance.sina.com.cn/",
    })
    with urllib.request.urlopen(req, timeout=25) as r:
        txt = r.read().decode("utf-8", "ignore")
    i, j = txt.find("["), txt.rfind("]")
    if i < 0 or j <= i:
        return None
    return json.loads(txt[i:j + 1])


def main():
    targets = sys.argv[1:]
    ok, fail = [], []
    for code, sym, name, cat, var, exch in NEW:
        if targets and code not in targets:
            continue
        out_path = os.path.join(OUT_DIR, f"{code}_daily.json")
        if os.path.exists(out_path) and os.path.getsize(out_path) > 2000:
            ok.append((code, "cached"))
            continue
        try:
            data = fetch(sym)
        except Exception as e:
            fail.append((code, f"ERR {type(e).__name__}: {str(e)[:60]}"))
            continue
        if not data or len(data) < 30:
            fail.append((code, f"EMPTY n={len(data) if data else 0}"))
            continue
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        ok.append((code, f"{len(data)} bars {data[0]['d']}~{data[-1]['d']}"))
        time.sleep(0.6)
    print("=== OK ===")
    for c, s in ok:
        print(f"  {c:4s} {s}")
    print("=== FAIL ===")
    for c, s in fail:
        print(f"  {c:4s} {s}")
    print(f"\nok={len(ok)} fail={len(fail)}")


if __name__ == "__main__":
    main()
