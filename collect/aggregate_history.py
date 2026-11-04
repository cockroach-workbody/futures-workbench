#!/usr/bin/env python3
# 汇总 collect/raw_hist/agg_<CODE>.json（每个品种一个文件，内含多日 long/short 汇总）
# 生成 _t20_history.json: {generated, by_code:{code:[{date,net,long_total,short_total,long_chg,short_chg}...按日期排序]}}
import json, os, glob

BASE = os.path.dirname(os.path.abspath(__file__))
RAWHIST = os.path.join(BASE, "raw_hist")
OUT = os.path.join(BASE, "_t20_history.json")

def main():
    os.makedirs(RAWHIST, exist_ok=True)
    merged = {}  # code -> {date: rec}
    files = sorted(glob.glob(os.path.join(RAWHIST, "agg_*.json")))
    print(f"Found {len(files)} agg files")
    for fp in files:
        try:
            with open(fp, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception as e:
            print("skip", fp, e)
            continue
        code = payload.get("code")
        data = payload.get("data", {})
        if not code or not data:
            continue
        for date, rec in data.items():
            if not isinstance(rec, dict) or "net" not in rec:
                continue
            merged.setdefault(code, {})[date] = rec
    out = {"generated": "2026-10-31", "by_code": {}}
    for code, m in merged.items():
        lst = [m[d] for d in sorted(m.keys())]
        out["by_code"][code] = lst
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    codes = sorted(out["by_code"].keys())
    print(f"Wrote {OUT}: {len(codes)} varieties")
    for c in codes:
        lst = out["by_code"][c]
        print(f"  {c}: {len(lst)} pts ({lst[0]['date']}..{lst[-1]['date']})")
    missing = [c for c in EXPECTED if c not in out["by_code"]]
    if missing:
        print("MISSING varieties:", missing)

EXPECTED = ["RB","HC","I","J","JM","FG","SA","M","C","SR","CF","A","Y","P","RM","OI","AP","JD","CJ","LH","TA","MA","RU","L","PP","BU","V","UR","CU","AL","AU","NI","ZN","AG","PB","SN"]

if __name__ == "__main__":
    main()
