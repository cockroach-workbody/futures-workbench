# -*- coding: utf-8 -*-
"""
agg_t20.py
读取 collect/raw/<date>_<code>_<side>.json（side ∈ {long, short}，每文件为该
(date, code, side) 的前20会员行级数据），按 long/short 分别汇总
indicator_volume / change_volume，计算 net = long_total - short_total，
按 code 组织、按 date 排序，合并写入 collect/_t20_part_D.json。

规则：
- long_total/short_total = 该侧 indicator_volume 之和（覆盖该品种所有合约前20会员）
- long_chg/short_chg    = 该侧 change_volume 之和
- net = long_total - short_total
- 仅保留任务要求的 5 个目标交易日；某 (date, code) 任一方向缺失则跳过该品种当日。
- 与已有 _t20_part_D.json 合并：保留既有条目，按 (code, date) 刷新本次解析结果。
"""
import json, os

BASE = "E:/workbuddy/2026-10-28-11-22-26"
VAR_FILE = os.path.join(BASE, "collect", "_t20_variety.json")
RAW_DIR = os.path.join(BASE, "collect", "raw")
OUT = os.path.join(BASE, "collect", "_t20_part_D.json")

DATES = ["20260924", "20260925", "20260928", "20260929", "20260930"]


def load_variety_map():
    with open(VAR_FILE, encoding="utf-8") as f:
        arr = json.load(f)
    return {v["code"]: v["variety"] for v in arr}


def read_raw(date, code, side):
    p = os.path.join(RAW_DIR, f"{date}_{code}_{side}.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def tnum(x):
    try:
        f = float(x)
    except Exception:
        return 0
    if f == int(f):
        return int(f)
    return f


def main():
    vmap = load_variety_map()

    # 载入已有结果，便于增量合并
    by_code = {}
    if os.path.exists(OUT):
        try:
            old = json.load(open(OUT, encoding="utf-8"))
            by_code = old.get("by_code", {}) or {}
        except Exception:
            by_code = {}

    dates_covered = set()
    codes_with_data = set()

    for date in DATES:
        for code, variety in vmap.items():
            long_rows = read_raw(date, code, "long")
            short_rows = read_raw(date, code, "short")
            if not long_rows or not short_rows:
                # 任一方向缺失 -> 跳过该品种当日
                continue

            def total(rows, field):
                s = 0
                for r in rows:
                    if r.get("variety_code") and r["variety_code"] != variety:
                        continue
                    s += tnum(r.get(field))
                return s

            long_total = total(long_rows, "indicator_volume")
            long_chg = total(long_rows, "change_volume")
            short_total = total(short_rows, "indicator_volume")
            short_chg = total(short_rows, "change_volume")
            net = long_total - short_total

            entry = {
                "date": date,
                "net": net,
                "long_total": long_total,
                "short_total": short_total,
                "long_chg": long_chg,
                "short_chg": short_chg,
            }
            # 刷新（按 code+date）或追加
            lst = by_code.setdefault(code, [])
            existing = next((e for e in lst if e.get("date") == date), None)
            if existing:
                existing.update(entry)
            else:
                lst.append(entry)
            codes_with_data.add(code)
            dates_covered.add(date)

    for code in by_code:
        by_code[code].sort(key=lambda e: e["date"])
        # 仅保留目标日期内的条目
        by_code[code] = [e for e in by_code[code] if e["date"] in DATES]

    out = {"generated": "2026-10-31", "by_code": by_code}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("WROTE", OUT)
    print("dates_covered:", sorted(dates_covered))
    print("codes_with_data:", len(codes_with_data), sorted(codes_with_data))
    for d in DATES:
        n = sum(1 for c, lst in by_code.items() if any(e["date"] == d for e in lst))
        print(f"  {d}: {n} codes")


if __name__ == "__main__":
    main()
