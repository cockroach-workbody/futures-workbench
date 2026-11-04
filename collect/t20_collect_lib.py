#!/usr/bin/env python3
# 前20会员历史净持仓采集辅助库（被后台采集 Agent 调用）
# 读取/写入 collect/raw_hist/agg_<CODE>.json，幂等合并（已存在日期跳过）。
import json, os

BASE = os.path.dirname(os.path.abspath(__file__))
RAWHIST = os.path.join(BASE, "raw_hist")

def agg_path(code):
    return os.path.join(RAWHIST, f"agg_{code}.json")

def load_agg(code):
    fp = agg_path(code)
    if os.path.exists(fp):
        try:
            return json.load(open(fp, "r", encoding="utf-8"))
        except Exception:
            pass
    return {"code": code, "variety": None, "data": {}}

def save_agg(payload):
    os.makedirs(RAWHIST, exist_ok=True)
    json.dump(payload, open(agg_path(payload["code"]), "w", encoding="utf-8"),
              ensure_ascii=False, indent=0)

def upsert(code, variety, date, rec):
    """写入单日汇总；返回 True 表示本次新增（False 表示已存在跳过）。
    日期键统一规整为 8 位 YYYYMMDD，避免 '2026-11-03' 与 '20261103' 被当成两天。"""
    date = date.replace("-", "") if date else date
    p = load_agg(code)
    if p.get("variety") is None and variety:
        p["variety"] = variety
    data = p.setdefault("data", {})
    if date in data:
        return False
    rec = dict(rec)
    rec["date"] = date
    data[date] = rec
    save_agg(p)
    return True

def parse_rank_result(result, date):
    """把 futures_rank 返回的 result 行数组合并为单日汇总 dict。
    只汇总 持买 / 持卖（忽略 成交）。"""
    long_total = long_chg = short_total = short_chg = 0
    for r in result:
        ind = r.get("indicator_code")
        vol = r.get("indicator_volume") or 0
        chg = r.get("change_volume") or 0
        if ind == "持买":
            long_total += vol; long_chg += chg
        elif ind == "持卖":
            short_total += vol; short_chg += chg
    net = long_total - short_total
    return {"date": date, "net": net, "long_total": int(long_total),
            "short_total": int(short_total), "long_chg": int(long_chg),
            "short_chg": int(short_chg)}

# 标准 10 个采样交易日（近一月，剔除周末/国庆假日/非交易日 20260925）
DATES = ["20261030","20261028","20261026","20261022","20261020",
         "20261016","20261014","20261012","20261008","20260930"]
